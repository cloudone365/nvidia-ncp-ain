# Module 8.1 — InfiniBand Diagnostics and RDMA Performance Testing

> **Exam domain:** Troubleshooting Tools (20%)  
> **Source course(s):** InfiniBand Essentials; InfiniBand Professional (Troubleshooting units)  
> **Source docs:** NVIDIA InfiniBand Fabric Utilities (infiniband-diags); ibdiagnet User Manual; perftest (github.com/linux-rdma/perftest); "Testing RDMA latency with ib_write_lat"; IPoIB chapter of the MLNX_OFED / DOCA-Host user manual; NCCL Troubleshooting guide  
> **Estimated study time:** 4 hours

## 1. Learning objectives

By the end of this module you will be able to:

- **Describe** what each InfiniBand port state and physical state means and infer the most likely cause from the combination (for example `Initializing` + `LinkUp` + no LID = no Subnet Manager). *(Troubleshooting Tools — interpret IB port status)*
- **Verify** fabric inventory and locate degraded links with **ibnodes**, ibswitches, ibhosts and **iblinkinfo**, including spotting the "Could be 4X" width downgrade. *(Troubleshooting Tools — fabric discovery utilities)*
- **Verify** end-to-end IB-layer reachability with **ibping**, ibtracert and ibroute, and explain why ICMP ping does not test the same thing. *(Troubleshooting Tools / InfiniBand Networking — reachability and routing)*
- **Troubleshoot** a link using perfquery port counters, distinguishing "cable/physical" counters from "congestion" counters. *(Troubleshooting Tools — counter interpretation)*
- **Configure and run** **ibdiagnet** with the flags the exam expects, locate its output files under `/var/tmp/ibdiagnet2/`, and interpret the error/warning summary. *(Troubleshooting Tools — ibdiagnet)*
- **Run** **ib_write_bw** / **ib_write_lat** / ib_read_bw / ib_send_bw in client–server mode (including GPUDirect with `--use_cuda`) and judge whether the numbers are healthy for the link rate. *(Troubleshooting Tools — perftest)*
- **Troubleshoot** an IPoIB interface that is up but cannot ping (PKey, SM, connected vs. datagram mode). *(InfiniBand Networking — IPoIB and partitions)*
- **Write** a bottom-up troubleshooting procedure and pick the right tool for a given symptom. *(Troubleshooting Tools — methodology)*

## 2. Key technical concepts

### 2.1 Bottom-up troubleshooting: check the plug before the app

**Analogy.** When the lamp does not come on you check the plug, then the bulb, then the switch, then the wiring, and only then do you call the electrician about the house. You do not start by rewiring the house. InfiniBand troubleshooting works the same way: every layer depends on the one below it, so you start at the cable and climb.

**Mechanism.** The layers, and the tool that answers each one:

```text
1. Physical/cable  -> ibstat Physical state; mlxlink -m / -c (8.3); perfquery SymbolErrors
2. Link state      -> ibstat State; iblinkinfo width/speed ("Could be 4X")
3. SM / LID        -> sminfo; Base lid != 0; SM lid identical everywhere
4. Partition/QoS   -> saquery -p; /sys/class/net/ib0/pkey; saquery PR for SL
5. Routing         -> ibtracert; ibroute; ibdiagnet -r (credit loops, AR)
6. RDMA perf       -> ib_write_bw / ib_write_lat per link; ucx_perftest
7. Application     -> nccl-tests; NCCL_DEBUG=INFO; GPU/NIC PCIe affinity
```

A fault at layer 2 (a link that trained at half width) *will* show up at layer 7 as a slow NCCL collective. If you start at layer 7 you spend a day tuning NCCL environment variables for a bad cable.

**Why the exam cares.** Nearly every troubleshooting question is "given this symptom, what do you check first?". The answer is always the lowest layer that could produce the symptom.

### 2.2 How the tools talk to the fabric: local reads vs. MADs

**Analogy.** Some tools read the gauge on your own dashboard; others radio the control tower and ask about everyone else's aircraft. The second kind only works if the radio (your port) is live and the tower (the SM) is on duty.

**Mechanism.** The InfiniBand diagnostics ship as part of MLNX_OFED / DOCA-Host (package `infiniband-diags`, plus `ibutils2` for ibdiagnet) and are also present on managed Quantum switches (MLNX-OS / NVOS) and on the UFM appliance. Two mechanisms sit underneath:

| Mechanism | Used by | What it needs |
|---|---|---|
| **Local verbs query** (reads the HCA driver) | ibstat, ibstatus, ibv_devinfo | Only a working driver; works even with no SM |
| **Management Datagrams (MADs)** over QP0/QP1 | ibnodes, iblinkinfo, ibping, ibtracert, ibroute, perfquery, ibportstate, saquery, sminfo, ibdiagnet | A port in `Active` state with a LID, i.e. a running **SM**. Most MAD tools need root for the `umad` device. |

If a MAD-based tool like `iblinkinfo` hangs or returns "MAD timeout", the problem is usually upstream of the tool — the local port is not Active, or the SM is not running — not that the fabric is broken.

Common options shared by MAD-based tools: `-C <ca>` selects the HCA (`-C mlx5_0`), `-P <port>` the port number, `-G` interprets an address as a **GUID** instead of a **LID**, `-t <ms>` sets the MAD timeout.

**Why the exam cares.** "iblinkinfo times out on a freshly cabled fabric" is a trick question: the answer is "no SM yet", not "bad cable".

### 2.3 Port state and physical state: two different questions

**Analogy.** *Physical state* is whether the phone line has dial tone. *State* is whether the exchange has given you a phone number. You can have perfect dial tone and still no number.

**Mechanism.** **ibstat** reads the local HCA and reports both:

| Field | Meaning | Healthy value |
|---|---|---|
| State | Logical (link-layer) port state managed by the SM | `Active` |
| Physical state | Electrical/optical state negotiated by the two ends | `LinkUp` |
| Rate | Negotiated data rate in Gb/s (4x NDR = 400, 4x HDR = 200, 4x EDR = 100) | Matches the slowest of HCA, cable, switch port |
| Base lid | The **LID** assigned by the SM | Non-zero |
| SM lid | LID of the master SM | Non-zero; identical on every host in the subnet |
| Port GUID | 64-bit unique port identifier (burned in) | Used by `-G` options and UFM |
| Link layer | `InfiniBand` or `Ethernet` (RoCE) | Depends on `LINK_TYPE_P1` in mlxconfig |

The combinations tell you the cause:

| State | Physical state | Meaning | Most likely cause |
|---|---|---|---|
| `Down` | `Polling` | No signal detected from peer | Cable unplugged, bad cable, remote port disabled, remote host powered off |
| `Down` | `Disabled` | Port administratively disabled | `ibportstate ... disable`, UFM isolation, or `mlxlink -a DN` |
| `Initializing` (Init) | `LinkUp` | Physical link is good, but no LID assigned | **No Subnet Manager** reachable (not running, wrong subnet, or SM port down). Base lid shows 0. |
| `Armed` | `LinkUp` | SM has configured the port but not yet moved it to Active | Transient during SM sweep; persistent Armed = SM problem |
| `Active` | `LinkUp` | Fully operational | — |

**ibstatus** is the terse variant (LIDs in hex: `0x25` = 37, whereas `ibstat` prints decimal 37 — the exam likes that mismatch). `ibv_devinfo` (`-v` for everything) comes from `libibverbs` and shows what an RDMA application sees: `active_mtu`, max QPs, and the GID table you need for RoCE perftest (`ibv_devinfo -d mlx5_0 -v | grep -i gid`).

**Why the exam cares.** `Initializing` + `LinkUp` + LID 0 → SM problem. `Down` + `Polling` → physical. This is the single most-tested distinction in the domain.

### 2.4 Inventory and link health: ibnodes, iblinkinfo

**Analogy.** Roll call, then a walk down every corridor checking each door.

**Mechanism.** **ibnodes** discovers the whole subnet via MADs and lists every node; ibswitches and ibhosts are the filtered views. `ibhosts | wc -l` versus the expected count answers "are all 32 nodes present?". The node description string (`"MF0;leaf-01:MQM9700/U1"`) comes from the switch hostname — set hostnames early or every later step is harder. `sminfo` reports the master SM (state 3 = `SMINFO_MASTER`); if it times out there is no SM, which explains every host sitting in `Initializing`. `saquery` queries the Subnet Administrator database: `saquery NR` for node records, `saquery PR <src-lid> <dst-lid>` for path records (SL, MTU, rate), `saquery -p` for the PKey table, `saquery MCMR` for multicast groups.

**iblinkinfo** walks every link and prints negotiated width, lane speed, state and far end. NDR per-lane speed shows as **106.25 Gbps**. A link that came up at **2X instead of 4X** is flagged `( Could be 4X )`; a speed downgrade shows `Could be 100 Gbps` or similar.

| Option | Purpose |
|---|---|
| `--switches-only` | Skip HCA-originated lines; halves the output on a big fabric |
| `-l` | One line per link, machine-friendly (easy to `grep "Could be"`) |
| `--down` / `-d` | Show only down links |
| `-S <guid>` / `-n <hops>` | Start from a given switch GUID / limit discovery depth |
| `--load-cache <file>` / `--diff` | Compare against a saved discovery to spot what changed |

**Why the exam cares.** `iblinkinfo -l | grep -i "could be"` is *the* fabric-scale way to find a degraded cable; the exam shows you an iblinkinfo line and asks what it means.

### 2.5 Reachability and path: ibping, ibtracert, ibroute

**Analogy.** ICMP ping asks "does your house answer the doorbell?". **ibping** asks "does the postal system have a route to your address?" — it tests the fabric, not the application stack. And unlike a doorbell, somebody has to be standing behind the door to answer: you must start a responder.

**Mechanism.** `ibping -S` on the target; `ibping -L <lid>` or `ibping -G <guid>` on the source. It proves both ports are Active, the SM assigned LIDs, and the switches hold forwarding entries between them. It does **not** prove RDMA data QPs work, PKeys match, or performance is acceptable. "ibping works but IPoIB ping fails" therefore points at IPoIB configuration (PKey, IP, mode).

`ibtracert <src-lid> <dst-lid>` prints the hop-by-hop path by reading each switch's linear forwarding table (LFT) — use it to confirm rail-0 to rail-0 traffic stays on the rail-0 leaf. `ibroute <switch-lid>` dumps a switch's LFT; `-M` the multicast table. With **adaptive routing**, the LFT is only the static fallback; AR groups are inspected via `ibdiagnet -r` or UFM.

**Why the exam cares.** Forgetting `ibping -S` and concluding "fabric broken" is a classic distractor.

### 2.6 Port counters: typos vs. traffic jams

**Analogy.** Some counters count *typos* on the wire (physical); others count *time stuck in traffic* (congestion). You fix typos by replacing the pen; you fix traffic by changing routes. Replacing a cable for a congestion counter fixes nothing.

**Mechanism.** perfquery reads the Port Counters MAD of any port: `perfquery [lid] [port]`; `-x` for 64-bit extended counters (32-bit data counters wrap in seconds at NDR); `-R` resets after reading; `-a` reads all ports of a switch.

| Counter | Category | Interpretation |
|---|---|---|
| SymbolErrorCounter | Physical | Bit errors on the wire. Rising steadily = marginal cable/transceiver/connector |
| LinkErrorRecoveryCounter | Physical | Link retrained successfully. A few at boot fine; incrementing during operation = flapping |
| LinkDownedCounter | Physical | Link went fully down |
| PortRcvErrors | Physical/protocol | Malformed/corrupt packets received (CRC). Remote transmitter or this receiver |
| PortRcvRemotePhysicalErrors | Physical (upstream) | Packets marked bad *by an earlier hop* — fault is before this link |
| LocalLinkIntegrityErrors / ExcessiveBufferOverrunErrors | Physical | Serious; usually with symbol errors |
| PortXmitDiscards | Congestion/forwarding | Dropped on transmit (lifetime limit, no route). Congestion or routing, not cables |
| PortXmitWait | Congestion | Ticks the port had data but no credits. The key counter for "fabric is up but slow" |
| VL15Dropped | Management | SM traffic dropped; usually only during storms |

ibqueryerrors sweeps the fabric and prints only ports over threshold (`-r` rates, `-k` reset after reading, `-s` suppress counters).

**Why the exam cares.** **SymbolError / LinkErrorRecovery / LinkDowned / PortRcvErrors → physical. PortXmitDiscards / PortXmitWait → congestion/routing.** A single error is not a failure; the *rate* is.

### 2.7 ibdiagnet: the full physical

**Analogy.** Everything above is a stethoscope; **ibdiagnet** is the annual full physical with blood work — slower, but it looks at everything and writes a report.

**Mechanism.** ibdiagnet (ibutils2) discovers topology, reads every port's counters and capabilities, validates the SM's routing tables for dead-ends and credit loops, pulls cable EEPROM data, and writes to `/var/tmp/ibdiagnet2/`.

| Flag | Purpose |
|---|---|
| `--pc` | Clear (reset) all port counters at the start |
| `--pm_pause_width=<sec>` | Wait after clearing so the second read measures a real interval |
| `-P all=1` | Report any counter that incremented by ≥1 (tune for production, e.g. `-P symbol_error_counter=100`) |
| `--extended_speeds all` | Read extended speed and lane info (NDR per-lane details) |
| `--get_cable_info` | Pull transceiver EEPROM: vendor, PN, SN, length, temperature → `ibdiagnet2.cables` |
| `-r` | Routing validation: LFT correctness, dead-ends, credit loops; reports AR configuration |
| `--sc` / `--skip` | Skip stages (e.g. `--skip dup_guids`) |
| `-o <dir>` | Alternate output directory |
| `--sharp` | Validate **SHARP** aggregation tree configuration |
| `-i <ca> -p <port>` | Which local HCA/port to discover from |

| File | Contents |
|---|---|
| `ibdiagnet2.log` | Stage-by-stage log and the summary table — read this first |
| `ibdiagnet2.db_csv` | Full machine-readable database; what UFM and scripts parse |
| `ibdiagnet2.pm` | Port counters for every port |
| `ibdiagnet2.fdbs` | Linear forwarding tables of every switch |
| `ibdiagnet2.net_dump` | Topology as node/port/neighbor listing (diff against a cabling plan — Module 8.2) |
| `ibdiagnet2.cables` | Cable EEPROM data when `--get_cable_info` is used |
| `ibdiagnet2.sm` | SM information |
| `ibdiagnet2.ar` / `.plft` | Adaptive routing groups and tables when `-r` is used |

"Routing" errors mentioning **credit loops** indicate a cyclic dependency in forwarding tables that can deadlock under load; they come from miscabling that violates the routing engine's topology assumptions. Fix cabling or pick the right engine in `opensm.conf` / UFM. On a multi-rail SuperPOD each rail may be a *separate subnet* and needs its own run with `-i <ca>`.

**Why the exam cares.** It asks for the default output path (`/var/tmp/ibdiagnet2/`), which file holds what, and what `--pc` does.

### 2.8 perftest: measuring the pipe

**Analogy.** A speed test for one pipe between two taps. One tap alone cannot fill an NDR pipe — open several (`-q`).

**Mechanism.** The perftest suite (`ib_write_bw`, **ib_write_lat**, `ib_read_bw`, `ib_read_lat`, `ib_send_bw`, `ib_send_lat`, `ib_atomic_*`) is client–server: start the server with no address, then point the client at the server's IP. TCP is only used to exchange QP information; the test traffic is RDMA.

| Option | Meaning |
|---|---|
| `-d mlx5_0` / `-i 1` | Device and port. Always specify on a multi-NIC node or you test the wrong rail |
| `-x <gid-index>` | GID index — required for **RoCE**; not needed on IB |
| `-R` | Use `rdma_cm` for connection setup (the normal way on RoCE) |
| `-a` | All message sizes 2 B → 8 MB; `-s <bytes>` for one size |
| `-q <n>` | QP count; a single QP will not saturate NDR, use `-q 4`–`-q 8` |
| `-D <sec>` | Duration instead of iteration count (`-n`) |
| `-F` | Do not fail if CPU frequency scaling is on |
| `--report_gbits` | Gb/s instead of MB/s |
| `-S <sl>` | Service level — test the SL your QoS maps RDMA to |
| `-m <mtu>` / `--tclass` | Path MTU / DSCP-bearing traffic class on RoCE |
| `--use_cuda=<gpu>` | Buffers in GPU memory → tests **GPUDirect RDMA**. Needs CUDA-enabled perftest and `nvidia-peermem` or DMA-BUF |
| `-b` | Bidirectional |
| `--run_infinitely` | Keep going until killed — watch for drops while reseating a cable |

Expected ballpark numbers (verify against your exact platform):

| Link | Signalling rate | Realistic `ib_write_bw` large-message | `ib_write_lat` small message |
|---|---|---|---|
| 4x NDR (400G) | 400 Gb/s | ~385–395 Gb/s | ~1.0–1.3 µs (half round-trip) |
| 4x HDR (200G) | 200 Gb/s | ~195 Gb/s | ~1.0–1.4 µs |
| 400G RoCE (ConnectX-7 / BlueField-3) | 400 Gb/s | ~380–390 Gb/s | ~1.5–2.5 µs |

~195 Gb/s on an NDR link = the link trained at 2X or HDR; go back to iblinkinfo. ~350 Gb/s with GPU buffers = check PCIe topology (`nvidia-smi topo -m`). A high latency max with a good median = host (CPU power states, IRQ affinity), not fabric. `ucx_perftest` tests the UCX layer under HPC-X MPI, the step between raw verbs and MPI.

**Why the exam cares.** Which option is RoCE-only (`-x`/`-R`), what `--use_cuda` tests, and what half-rate means.

### 2.9 nccl-tests and IPoIB

Once every link passes perftest, run `all_reduce_perf` from nccl-tests: `mpirun -np 16 -H dgx-01:8,dgx-02:8 ./all_reduce_perf -b 8 -e 8G -f 2 -g 1`. *busbw* at large sizes should approach the per-GPU NIC rate (50 GB/s for 400 Gb/s; a healthy multi-node all_reduce lands in the mid-40s GB/s — verify against reference numbers for your system). One degraded link drags the whole collective down to that link's speed. Useful environment: `NCCL_DEBUG=INFO`, `NCCL_IB_HCA`, `NCCL_IB_GID_INDEX`, `NCCL_IB_TC` / `NCCL_IB_SL`, `NCCL_NET_GDR_LEVEL`. "NCCL WARN NET/IB : Got completion with error 12" (transport retry exceeded) is almost always a fabric link problem or PFC/ECN misconfiguration on RoCE.

IPoIB (`ib0`) puts IP over IB using the broadcast multicast group of a **PKey**. `cat /sys/class/net/ib0/pkey` reads `0xffff` on the default partition (full-member form of `0x7fff`). A child interface (`echo 0x8001 > /sys/class/net/ib0/create_child`) must use a PKey the SM assigned to *both* ports (`saquery -p`, `smpquery pkeys <lid>`). `cat /sys/class/net/ib0/mode` shows `datagram` (UD, MTU up to 4092) vs `connected` (RC, MTU up to 65520; both ends must agree or large packets fail while small pings succeed). IPoIB throughput is CPU-bound and far below RDMA line rate by design — a 40 Gb/s `iperf` on an NDR link is normal.

## 3. Live-style walkthrough: "the training job hangs on node 7"

Scenario: a 32-node job stalls; the scheduler points at `dgx-07`. The SRE already restarted the job twice. You are going to work bottom-up and narrate every decision. All output is illustrative.

```bash
# ---- Step 1: local port state on the suspect node. Always first; it needs no SM and
#      no fabric, so if it is bad nothing else is worth running yet.
dgx-07$ ibstat
CA 'mlx5_0'
    CA type: MT4129
    Firmware version: 28.41.1000
    Node GUID: 0x946dae0300a1b2c0
    Port 1:
        State: Active                 # SM has configured this port: layer 3 of our ladder is fine
        Physical state: LinkUp        # cable, transceiver, port are electrically good: layer 1 fine
        Rate: 400                     # 4x NDR negotiated. Good so far — but Rate is this port only
        Base lid: 37                  # non-zero: SM assigned a LID
        SM lid: 1                     # master SM is LID 1; must match on every other node
        Port GUID: 0x946dae0300a1b2c0
        Link layer: InfiniBand        # not Ethernet: LINK_TYPE_P1=1 in mlxconfig
# Decision: layers 1–3 on mlx5_0 pass. Repeat for mlx5_1..mlx5_7 (8 rails on a DGX).
dgx-07$ for d in /sys/class/infiniband/mlx5_*; do echo -n "$(basename $d) "; cat $d/ports/1/state; done
mlx5_0 4: ACTIVE
mlx5_1 4: ACTIVE
...
mlx5_7 4: ACTIVE
# All eight Active. If one had shown "2: INIT" we would have jumped straight to sminfo.

# ---- Step 2: confirm the SM is alive and is the SAME master everyone else sees.
dgx-07$ sudo sminfo
sminfo: sm lid 1 sm guid 0xb8cef603002a2200, activity count 189231 priority 15 state 3 SMINFO_MASTER
# state 3 = MASTER, LID 1 matches ibstat's "SM lid". If this had timed out, every port
# would sit in Initializing and the fix is "start/repair the SM", not "touch cables".

# ---- Step 3: fabric-wide link health. We have no evidence yet of WHICH link is bad,
#      so ask the fabric about every link and grep for downgrades.
dgx-07$ sudo iblinkinfo -l --switches-only | grep -i "could be"
0xb8cef603002a1a00 "MF0;leaf-01:MQM9700/U1" 3 2[  ] ==( 2X 106.25 Gbps Active/ LinkUp)==> 38 1[  ] "dgx-07 mlx5_1" ( Could be 4X )
# There it is. LID 3 (leaf-01) port 2 to dgx-07 mlx5_1 trained at 2X: half the lanes.
# Per-lane speed is 106.25 Gbps (NDR), so speed is fine; WIDTH is the problem.
# ibstat on mlx5_1 said Active/LinkUp — state tools cannot see width problems; iblinkinfo can.
# Decision: this is a layer-1 fault presenting as "slow", and NCCL on a bus of 8 rails runs
# at the pace of the slowest rail. That is why the job "hangs" (really: crawls).

# ---- Step 4: counters on that specific switch port. Is the cable marginal (physical
#      counters rising) or did it just train badly once?
dgx-07$ sudo perfquery -x 3 2
# Port extended counters: Lid 3 port 2 (CapMask: 0x5A00)
PortXmitData:....................8812003346122
PortRcvData:.....................9122381910023
SymbolErrorCounter:..............1182      # physical: bit errors on the wire, non-trivial
LinkErrorRecoveryCounter:........3         # physical: retrained 3 times
LinkDownedCounter:...............1         # physical: went fully down once
PortRcvErrors:...................27        # physical: corrupt packets received
PortXmitDiscards:................0         # congestion: none
PortXmitWait:....................4421902   # congestion: some credit waits — expected on a 2X link
#                                            that the SM still routes 4X worth of traffic over
# Decision: SymbolError + LinkErrorRecovery + LinkDowned all non-zero = cable/transceiver/
# connector. XmitWait is a CONSEQUENCE of half width, not a separate congestion problem.
# Measure the rate to be sure it is live, not historical:
dgx-07$ sudo perfquery -R 3 2 >/dev/null; sleep 60; sudo perfquery -x 3 2 | grep -E 'Symbol|LinkError'
SymbolErrorCounter:..............94        # 94 symbol errors in 60 s: still happening now
LinkErrorRecoveryCounter:........0

# ---- Step 5: one full ibdiagnet pass for the record and to catch anything else.
#      NOTE: --pc clears counters. We already captured the evidence above, so that is OK.
dgx-07$ sudo ibdiagnet --pc --pm_pause_width=30 -P all=1 --extended_speeds all --get_cable_info -r
...
-I- Stage                     Warnings   Errors   Comment
-I- Discovery                        0        0
-I- Lids Check                       0        0
-I- Links Check                      1        0    # our 2X link
-I- Subnet Manager                   0        0
-I- Port Counters                    1        0    # symbol errors on LID 3 port 2 in the 30 s window
-I- Speed / Width checks             1        0
-I- Partition Keys                   0        0
-I- Routing                          0        0    # no credit loops: routing is not the problem
-I- Cables                           0        0
-W- link ... : Unexpected actual link width 2x (expected 4x) ...
-W- lid=0x0003 ... port=2 : SymbolErrorCounter=61 exceeds threshold
dgx-07$ grep -A3 "dgx-07 mlx5_1" /var/tmp/ibdiagnet2/ibdiagnet2.cables | head
# -> Vendor PN, SN, length, temperature of the suspect cable: this goes in the RMA ticket.
# Decision: exactly one problem in the fabric. Fix it and prove the fix.

# ---- Step 6: baseline the broken rail BEFORE touching it, so you can show the delta.
dgx-08$ ib_write_bw -d mlx5_1 -i 1 -F --report_gbits -a            # server on the peer
dgx-07$ ib_write_bw -d mlx5_1 -i 1 -F --report_gbits -a 10.0.1.8   # client, same rail
 #bytes     #iterations    BW peak[Gb/sec]    BW average[Gb/sec]   MsgRate[Mpps]
 1048576    5000           196.40             196.12               0.023
# 196 Gb/s on a 400G link: exactly half. Consistent with 2X. (If you saw this number first,
# with no other context, the right reflex is "go check iblinkinfo for width" — Q6 in the knowledge check.)

# ---- Step 7: remediate. Reseat and clean both ends of the cable (MPO ferrule on optics).
#      Retrain the port from the host rather than walking to the switch:
dgx-07$ sudo ibportstate 3 2 reset         # forces a retrain of LID 3 port 2; link drops briefly
dgx-07$ sleep 15; sudo iblinkinfo -l --switches-only | grep "dgx-07 mlx5_1"
... ==( 4X 106.25 Gbps Active/ LinkUp)==> 38 1[  ] "dgx-07 mlx5_1" ( )
# 4X and no "Could be". If it had come back 2X again, the next step is Module 8.3:
# mlxlink -m for per-lane Rx power, then replace the cable.

# ---- Step 8: prove it with the same test as Step 6.
dgx-07$ ib_write_bw -d mlx5_1 -i 1 -F --report_gbits -a -q 4 10.0.1.8
 1048576    5000           392.11             392.05               0.047
# ~392 Gb/s: healthy NDR. -q 4 because one QP can under-report on NDR; compare like with like.
dgx-07$ sudo perfquery -R 3 2 >/dev/null; sleep 300; sudo perfquery -x 3 2 | grep Symbol
SymbolErrorCounter:..............0         # five minutes clean. Close the ticket; schedule RMA if it recurs.
```

What "good" looks like: all ports `Active/LinkUp`, `sminfo` state 3, zero lines from `grep "could be"`, physical counters flat over a measured interval, ibdiagnet summary all zeros, `ib_write_bw` within ~3% of line rate.

## 4. Troubleshooting / common gotchas

### 4.1 `ibping` times out although the fabric is healthy

- **Symptom:** `ibping -L 52` prints only `ibwarn: ... ibping ... timeout`, yet `iblinkinfo` shows LID 52 Active.
- **Why it happens:** ibping has no default responder. Unlike ICMP, nothing answers vendor-specific MADs unless a server process is listening.
- **How to debug:** on the target run `sudo ibping -S`; retry the client. Confirm with `sudo smpquery nodeinfo 52` that the LID itself is reachable (SMP MADs need no responder).
- **Fix:** always pair `ibping -S` (target) with `ibping -L <lid>` / `-G <guid>` (source). Do not conclude "fabric broken" from a missing responder.

### 4.2 `ibdiagnet` destroyed the evidence, or refused to run

- **Symptom:** after a nightly `ibdiagnet --pc` the counters you wanted to examine are all zero; or ibdiagnet fails with `umad` / "Failed to open device" errors as a normal user.
- **Why it happens:** `--pc` clears every port counter at the start of the run. And MAD-based tools need root for `/dev/infiniband/umad*`.
- **How to debug:** `ls -l /dev/infiniband/umad*`; `id`; check your cron for `--pc`. Look for the previous run's `ibdiagnet2.pm` under `/var/tmp/ibdiagnet2/` (or the `-o` directory) — the pre-clear read is saved there.
- **Fix:** run with `sudo`. Omit `--pc` when you are preserving evidence; use `--pc --pm_pause_width=30` only for measured-interval runs, and archive `db_csv`/`pm` from every run.

### 4.3 Half-rate `ib_write_bw` blamed on the fabric when it is the test

- **Symptom:** `ib_write_bw` reports ~300 Gb/s on an NDR link; iblinkinfo shows 4X, counters clean.
- **Why it happens:** a single QP (`-q 1`) with default TX depth often cannot saturate 400G, CPU frequency scaling adds jitter, or on a multi-NIC node you tested the wrong device, or on RoCE you picked the wrong GID index.
- **How to debug:** rerun with `-q 4 -a --report_gbits`; confirm `-d`/`-i` match the rail under test (`ibdev2netdev`); check `lspci -vv -s <bdf> | grep LnkSta` for PCIe Gen5 x16; on RoCE verify `-x <gid>` against `show_gids` or use `-R`.
- **Fix:** standardize on `-d <dev> -i 1 -F -a -q 4 --report_gbits` and pin the CPU governor to performance for latency tests (`-F` only hides the warning).

### 4.4 IPoIB "up" but silent

- **Symptom:** `ib0` has an IP, `ibping` to the peer works, ICMP ping fails.
- **Why it happens:** PKey mismatch (no multicast group join, so no ARP), or connected/datagram mode mismatch with large MTU.
- **How to debug:** `cat /sys/class/net/ib0/pkey` on both hosts (expect `0xffff` on the default partition); `cat /sys/class/net/ib0/mode`; `ip link show ib0` for MTU; `saquery MCMR` for the broadcast group; `saquery -p` for what the SM actually assigned.
- **Fix:** align PKeys (provision the partition in `opensm.conf`/UFM on *both* ports), align mode and MTU, then re-test.

## 5. Hands-on assignment: a fabric health script with pass/fail thresholds

**Scenario.** Your team runs a 16-node HDR/NDR cluster without UFM. Every Monday an engineer pastes `iblinkinfo` and `perfquery` output into a ticket by hand. You will replace that with a script.

**Requirements.**
1. A bash script `ibhealth.sh` runnable as root on any node with `infiniband-diags` installed, taking `-C <ca>` (default `mlx5_0`) and `-n <expected-host-count>`.
2. Check A — local port: every local `mlx5_*` port must be `Active`/`LinkUp`; print each with PASS/FAIL.
3. Check B — SM: `sminfo` must return a MASTER within a 2-second timeout.
4. Check C — inventory: `ibhosts` count must equal `-n`.
5. Check D — degraded links: zero `Could be` lines from `iblinkinfo -l`; list offenders on FAIL.
6. Check E — physical counters: for every switch port in `ibqueryerrors` output, SymbolErrorCounter, LinkErrorRecoveryCounter, LinkDownedCounter and PortRcvErrors must be below configurable thresholds (defaults 100/5/1/10), *measured over a 30-second window after a reset* — not since boot.
7. Check F — congestion flag (warning, not failure): report any port whose PortXmitWait grew by more than a threshold in the window.
8. Exit 0 if all PASS, 1 if any FAIL; print a one-screen report.

**Deliverables.** `ibhealth.sh`; a sample report from a healthy run and from a run with a deliberately degraded link (you may simulate by editing captured output).

**Acceptance criteria.**
- AC1: all six checks print a PASS/FAIL (or WARN) line with the measured value and the threshold.
- AC2: a 2X link, a missing host, or a stopped SM each produce exit code 1 and name the cause.
- AC3: counters are measured as a *delta over the window*, never since boot.
- AC4: the script never leaves counters reset without saying so in the report.

### Hint
- Section 2.3's state table tells you what strings to expect from `ibstat` and from `/sys/class/infiniband/<dev>/ports/1/{state,phys_state}` — the sysfs files are easier to parse.
- `sminfo` hangs without an SM; wrap it in `timeout 2`.
- Section 2.4's option table: `iblinkinfo -l` is the machine-friendly form; count lines matching `Could be`.
- For a delta, Section 2.6: `perfquery -R` resets; `ibqueryerrors -k` resets after reading. Think about reading, waiting, reading again.
- Keep thresholds in variables at the top; the reviewer will change them.

## Appendix A — Quick command reference

```bash
# Local state
ibstat; ibstatus; ibv_devinfo -v
# Inventory and links
ibnodes; ibswitches; ibhosts; sminfo
iblinkinfo --switches-only; iblinkinfo -l | grep -i "could be"; iblinkinfo --down
# Reachability and routing
ibping -S                      # responder
ibping -L <lid> | ibping -G <guid>
ibtracert <src-lid> <dst-lid>; ibroute <switch-lid>
saquery NR | saquery -p | saquery PR <slid> <dlid> | saquery MCMR
# Counters
perfquery -x <lid> <port>; perfquery -R <lid> <port>; ibqueryerrors -r
# Port control (careful)
ibportstate <lid> <port> query|disable|enable|reset
ibportstate <lid> <port> width 1   # bitmask; 1=1X,2=4X,4=8X,8=12X (verify in current docs)
# Full diagnostic
ibdiagnet --pc --pm_pause_width=30 -P all=1 --extended_speeds all --get_cable_info -r
ls /var/tmp/ibdiagnet2/           # ibdiagnet2.log .db_csv .pm .fdbs .net_dump .cables
# Performance
ib_write_bw -d mlx5_0 -i 1 -F -a --report_gbits [-q 4] [-x <gid>] [-R] [--use_cuda=0] [server-ip]
ib_write_lat -d mlx5_0 -i 1 -F -s 8 -n 10000 [server-ip]
ib_read_bw / ib_send_bw / ib_send_lat   # same option set
ucx_perftest -t ucp_put_bw [server]
mpirun ... all_reduce_perf -b 8 -e 8G -f 2 -g 1
# IPoIB
cat /sys/class/net/ib0/{pkey,mode}; echo 0x8001 > /sys/class/net/ib0/create_child
```

### Symptom → tool decision table

| Symptom | First tool | Then |
|---|---|---|
| Port `Down`/`Polling` | ibstat, physical check | mlxlink -m (transceiver present/power), swap cable |
| Port `Initializing`, LID 0 | sminfo | Start SM (opensm / switch SM / UFM), check SM port |
| Some nodes missing from fabric | ibhosts / ibnodes | ibstat on the missing node, iblinkinfo --down |
| One link slow, others fine | iblinkinfo -l \| grep "Could be" | mlxlink per-lane, perfquery SymbolError, replace cable |
| Random slowness, no link errors | perfquery PortXmitWait/XmitDiscards | ibdiagnet -r (routing/credit loop), check AR, congestion control |
| ibping OK, IP ping fails | PKey / mode / MTU on ib0 | saquery MCMR |
| ib_write_bw half the expected rate | iblinkinfo width | -q more QPs, check PCIe gen/width (`lspci -vv`), CPU frequency |
| ib_write_bw OK, NCCL slow | nccl-tests + NCCL_DEBUG=INFO | NIC selection (NCCL_IB_HCA), GDR, `nvidia-smi topo -m` |
| Counters rising fabric-wide after change | ibdiagnet --pc, diff db_csv | ibqueryerrors -r |
| Need full picture before escalating | ibdiagnet (all flags) + UFM | Collect `/var/tmp/ibdiagnet2`, `ibstat`, `mlxlink` outputs |

## Appendix B — Exam traps & key takeaways

- `Initializing` + `LinkUp` is an **SM** problem, not a cable problem. `Down` + `Polling` is physical.
- `ibstat` first: State (SM-driven) and Physical state (layer 1) answer different questions. `sminfo` confirms a master SM; no SM = every port in Initializing with LID 0.
- `ibping` needs `ibping -S` on the target. It tests IB-layer reachability (LIDs and forwarding), not IP and not RDMA data-path performance.
- LIDs: `ibstat` prints decimal, `ibstatus`/`saquery` print hex. 0x25 and 37 are the same port.
- `iblinkinfo -l | grep "Could be"` finds degraded links; "Could be 4X" text, not a counter, is how you spot a bad cable at fabric scale. NDR per-lane = 106.25 Gbps.
- SymbolError/LinkErrorRecovery/LinkDowned/PortRcvErrors = physical. PortXmitWait/PortXmitDiscards = congestion/routing. Do not replace a cable for XmitWait.
- Use `perfquery -x` for 64-bit counters; 32-bit data counters wrap within seconds at NDR and look like they "reset".
- `ibdiagnet --pc` clears counters — do not use it when preserving evidence. Output lives in `/var/tmp/ibdiagnet2/`; read `ibdiagnet2.log` first; `.pm` counters, `.db_csv` database, `.fdbs` routing, `.net_dump` topology, `.cables` EEPROM.
- `-x <gid>` (or `-R`) is needed for perftest on RoCE; unnecessary on native IB. Wrong GID index = "Couldn't connect" or hang.
- A single QP rarely saturates NDR; add `-q 4` before blaming the fabric. `-F` only suppresses the CPU-frequency warning; pin the governor for latency tests.
- NDR ≈ 390+ Gb/s and ≈ 1–2 µs latency; half the rate means a 2X- or HDR-trained link.
- IPoIB throughput is CPU-bound and far below RDMA line rate by design. `/sys/class/net/ib0/pkey` reads `0xffff` on the default partition.
- On a multi-rail SuperPOD each rail may be a separate subnet; ibdiagnet needs a run per rail with `-i <ca>`.
- Bottom-up: cable → link → SM → partition → routing → RDMA perf → application.

## Appendix C — Knowledge check

1. `ibstat` on a new DGX shows `State: Initializing`, `Physical state: LinkUp`, `Base lid: 0`. What is the most likely cause?  
   A. Faulty cable  
   B. No Subnet Manager is running or reachable  
   C. The HCA is configured for Ethernet link type  
   D. PKey mismatch

2. Which `iblinkinfo` output indicates a degraded link that should be investigated with mlxlink?  
   A. `( 4X 106.25 Gbps Active/ LinkUp)`  
   B. `( Down/ Polling)`  
   C. `( 2X 106.25 Gbps Active/ LinkUp) ... ( Could be 4X )`  
   D. `enhanced port 0 lid 3 lmc 0`

3. An engineer runs `ibping -L 52` and gets only timeouts, but `iblinkinfo` shows LID 52 Active. What did they most likely forget?  
   A. To run `ibping -S` on the target host  
   B. To specify the GID index with `-x`  
   C. To reset counters with `perfquery -R`  
   D. To start OpenSM

4. `perfquery -x` on a leaf uplink shows PortXmitWait growing rapidly, while SymbolErrorCounter and LinkDownedCounter stay at zero. The best interpretation is:  
   A. The cable is marginal and should be replaced  
   B. The port is congested (credit starvation); investigate routing/AR and traffic pattern  
   C. The SM has failed over  
   D. The port's PKey table is empty

5. Where does `ibdiagnet` write its per-port counters and its human-readable summary by default?  
   A. `/var/log/ibdiagnet/` — `counters.txt` and `summary.txt`  
   B. `/var/tmp/ibdiagnet2/` — `ibdiagnet2.pm` and `ibdiagnet2.log`  
   C. `/etc/opensm/` — `opensm.pm` and `opensm.log`  
   D. `~/.ibdiagnet/` — `report.csv`

6. `ib_write_bw -a --report_gbits` between two ConnectX-7 NDR ports peaks at 196 Gb/s. Which check should come first?  
   A. Increase `-q` to 16 QPs  
   B. Check `iblinkinfo` for the negotiated width/speed of the two links  
   C. Enable `--use_cuda`  
   D. Change the SL with `-S`

7. On RoCE, which perftest option is required that is unnecessary on native InfiniBand?  
   A. `-F`  
   B. `--report_gbits`  
   C. `-x <gid-index>` (or `-R` for rdma_cm)  
   D. `-a`

8. `ib0` is UP with an IP, `ibping` to the peer works, but IP ping fails. Which is the most likely cause?  
   A. The SM is down  
   B. The link trained at 2X  
   C. The two hosts are in different PKeys (or one is on a child interface the SM has not provisioned)  
   D. The HCA firmware is outdated

### Answer key
1. B — Physical LinkUp with no LID means layer 1 is fine and the SM has not configured the port.
2. C — "Could be 4X" marks a width downgrade (half the lanes trained).
3. A — ibping has no default responder; the target must run `ibping -S`.
4. B — XmitWait counts credit-starved cycles; with zero physical errors it is congestion, not cable.
5. B — ibdiagnet writes to `/var/tmp/ibdiagnet2/`; `.pm` holds counters, `.log` the summary.
6. B — ~196 Gb/s on NDR is exactly a 2X- or HDR-trained link; verify the negotiated width before tuning the test.
7. C — RoCE needs the GID index (or rdma_cm) to select the RoCEv2 address; IB uses LIDs.
8. C — ibping proves IB reachability; IPoIB additionally requires matching PKey/multicast group membership.

## References
- NVIDIA InfiniBand Fabric Utilities (infiniband-diags) — MLNX_OFED / DOCA-Host User Manual, "InfiniBand Fabric Utilities" chapter: https://docs.nvidia.com/networking/
- ibdiagnet InfiniBand Fabric Diagnostic Tool User Manual: https://docs.nvidia.com/networking/display/ibdiagnetusermanualv221
- perftest (linux-rdma): https://github.com/linux-rdma/perftest
- NVIDIA Networking knowledge base: "Testing RDMA latency with ib_write_lat"; "Understanding mlx5 Linux counters"
- IPoIB chapter, MLNX_OFED User Manual
- NCCL Troubleshooting: https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/troubleshooting.html
- nccl-tests: https://github.com/NVIDIA/nccl-tests
- Related modules: `../02-infiniband/`, `../03-rdma/03-roce-a-to-z.md`, `02-cable-validation-tool-cvt.md`, `03-mlxlink-and-mlxcables-debug-tools.md`

---
## Solution guide — Hands-on assignment

The script below meets every requirement. It uses only `infiniband-diags` binaries and sysfs; every external call is wrapped so a hung MAD does not hang the report.

```bash
#!/usr/bin/env bash
# ibhealth.sh — InfiniBand fabric health report with pass/fail thresholds (Module 8.1 assignment)
# Usage: sudo ./ibhealth.sh [-C <ca>] [-n <expected-hosts>] [-w <window-seconds>]
set -u

# ---------- thresholds (AC1: every check prints value vs threshold; reviewers edit these) ----------
CA="mlx5_0"            # HCA used for MAD discovery (-C)
EXPECTED_HOSTS=0       # -n; 0 = skip inventory check
WINDOW=30              # seconds between counter reads (AC3: delta, never since-boot)
T_SYMBOL=100           # SymbolErrorCounter delta allowed in window
T_LINKREC=5            # LinkErrorRecoveryCounter delta allowed
T_LINKDOWN=1           # LinkDownedCounter delta allowed (>=1 is a FAIL)
T_RCVERR=10            # PortRcvErrors delta allowed
T_XMITWAIT=1000000     # PortXmitWait delta that triggers a WARN (congestion, not failure)

while getopts "C:n:w:" opt; do
  case $opt in
    C) CA=$OPTARG ;; n) EXPECTED_HOSTS=$OPTARG ;; w) WINDOW=$OPTARG ;;
    *) echo "usage: $0 [-C ca] [-n hosts] [-w seconds]"; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "FAIL: run as root (MAD tools need /dev/infiniband/umad*)"; exit 1; }

FAIL=0
pass() { printf "PASS  %-28s %s\n" "$1" "$2"; }
fail() { printf "FAIL  %-28s %s\n" "$1" "$2"; FAIL=1; }
warn() { printf "WARN  %-28s %s\n" "$1" "$2"; }

echo "== IB fabric health  $(hostname)  $(date -Is)  via $CA  window=${WINDOW}s =="

# ---------- Check A: local ports (sysfs is simpler to parse than ibstat text) ----------
for dev in /sys/class/infiniband/mlx5_*; do
  d=$(basename "$dev")
  for p in "$dev"/ports/*; do
    pn=$(basename "$p")
    state=$(cut -d: -f2 < "$p/state" | tr -d ' ')          # e.g. "ACTIVE", "INIT", "DOWN"
    phys=$(cut -d: -f2 < "$p/phys_state" | tr -d ' ')      # e.g. "LinkUp", "Polling", "Disabled"
    lid=$(cat "$p/lid")                                     # hex, 0x0 when no SM
    rate=$(cat "$p/rate")                                   # e.g. "400 Gb/sec (4X NDR)"
    if [[ $state == ACTIVE && $phys == LinkUp ]]; then
      pass "A local $d/$pn" "Active/LinkUp lid=$lid $rate"
    elif [[ $phys == LinkUp ]]; then
      fail "A local $d/$pn" "state=$state phys=$phys lid=$lid -> SM problem (Initializing with LinkUp)"
    else
      fail "A local $d/$pn" "state=$state phys=$phys -> physical/cable problem"
    fi
  done
done

# ---------- Check B: SM present (sminfo hangs without an SM: bound it) ----------
if sm=$(timeout 2 sminfo -C "$CA" 2>/dev/null) && grep -q SMINFO_MASTER <<<"$sm"; then
  pass "B subnet manager" "$(grep -o 'sm lid [0-9]*' <<<"$sm"), state MASTER"
else
  fail "B subnet manager" "sminfo timed out or no MASTER -> start/repair SM before anything else"
fi

# ---------- Check C: inventory ----------
if (( EXPECTED_HOSTS > 0 )); then
  hosts=$(timeout 30 ibhosts -C "$CA" 2>/dev/null | grep -c '^Ca')
  if (( hosts == EXPECTED_HOSTS )); then pass "C host inventory" "$hosts/$EXPECTED_HOSTS HCAs seen"
  else fail "C host inventory" "$hosts seen, $EXPECTED_HOSTS expected -> ibstat on missing nodes, iblinkinfo --down"; fi
fi

# ---------- Check D: degraded links ----------
links=$(timeout 60 iblinkinfo -C "$CA" -l --switches-only 2>/dev/null)
deg=$(grep -ic "could be" <<<"$links")
if (( deg == 0 )); then pass "D degraded links" "0 'Could be' lines (threshold 0)"
else
  fail "D degraded links" "$deg link(s) trained below capability (threshold 0):"
  grep -i "could be" <<<"$links" | sed 's/^/        /'
fi

# ---------- Check E/F: counter deltas over the window ----------
# Strategy: read all error counters fabric-wide with ibqueryerrors, reset with -k (AC4: say so),
# wait WINDOW seconds, read again. ibqueryerrors only prints ports over its own thresholds, so
# for the per-counter delta we use perfquery -x on every switch port it names plus local port.
echo "NOTE  counters on reported ports were RESET at $(date +%T) for a ${WINDOW}s measurement window"
declare -A before after
read_counters() {   # $1 = assoc array name; fills "lid:port:Counter" -> value
  local -n arr=$1
  while read -r lid port; do
    while IFS=: read -r name val; do
      name=$(tr -d ' .' <<<"$name"); val=$(tr -d ' .' <<<"$val")
      arr["$lid:$port:$name"]=$val
    done < <(timeout 5 perfquery -C "$CA" -x "$lid" "$port" 2>/dev/null | grep -E 'SymbolError|LinkErrorRecovery|LinkDowned|PortRcvErrors:|PortXmitWait')
  done < <(timeout 60 iblinkinfo -C "$CA" -l --switches-only 2>/dev/null | awk '{print $3, $4}' | tr -d '[]' | sort -u)
}
# Reset every switch port we are about to measure (perfquery -R), then take the first read.
while read -r lid port; do timeout 5 perfquery -C "$CA" -R "$lid" "$port" >/dev/null 2>&1; done \
  < <(timeout 60 iblinkinfo -C "$CA" -l --switches-only 2>/dev/null | awk '{print $3, $4}' | tr -d '[]' | sort -u)
read_counters before
sleep "$WINDOW"
read_counters after

ebad=0; fwarn=0
for key in "${!after[@]}"; do
  IFS=: read -r lid port name <<<"$key"
  delta=$(( ${after[$key]:-0} - ${before[$key]:-0} ))
  case $name in
    SymbolErrorCounter)        (( delta > T_SYMBOL ))   && { fail "E lid $lid port $port" "SymbolError +$delta in ${WINDOW}s (>$T_SYMBOL) -> physical"; ebad=1; } ;;
    LinkErrorRecoveryCounter)  (( delta > T_LINKREC ))  && { fail "E lid $lid port $port" "LinkErrorRecovery +$delta (>$T_LINKREC) -> flapping"; ebad=1; } ;;
    LinkDownedCounter)         (( delta >= T_LINKDOWN )) && { fail "E lid $lid port $port" "LinkDowned +$delta (>=$T_LINKDOWN) -> link dropped"; ebad=1; } ;;
    PortRcvErrors)             (( delta > T_RCVERR ))   && { fail "E lid $lid port $port" "PortRcvErrors +$delta (>$T_RCVERR) -> corrupt packets"; ebad=1; } ;;
    PortXmitWait)              (( delta > T_XMITWAIT )) && { warn "F lid $lid port $port" "PortXmitWait +$delta (>$T_XMITWAIT) -> congestion, not cable"; fwarn=1; } ;;
  esac
done
(( ebad )) || pass "E physical counters" "all switch ports within thresholds over ${WINDOW}s"
(( fwarn )) || pass "F congestion (XmitWait)" "no port exceeded +$T_XMITWAIT in ${WINDOW}s"

echo "== RESULT: $([[ $FAIL -eq 0 ]] && echo ALL PASS || echo FAILURES PRESENT) =="
exit $FAIL
```

**Sample healthy report (illustrative):**

```text
== IB fabric health  dgx-01  2026-10-19T09:40:12+00:00  via mlx5_0  window=30s ==
PASS  A local mlx5_0/1             Active/LinkUp lid=0x25 400 Gb/sec (4X NDR)
...
PASS  B subnet manager             sm lid 1, state MASTER
PASS  C host inventory             128/128 HCAs seen
PASS  D degraded links             0 'Could be' lines (threshold 0)
NOTE  counters on reported ports were RESET at 09:40:19 for a 30s measurement window
PASS  E physical counters          all switch ports within thresholds over 30s
PASS  F congestion (XmitWait)      no port exceeded +1000000 in 30s
== RESULT: ALL PASS ==
```

**Sample degraded report (illustrative), exit code 1:**

```text
FAIL  D degraded links             1 link(s) trained below capability (threshold 0):
        0xb8cef603002a1a00 "MF0;leaf-01:MQM9700/U1" 3 2[  ] ==( 2X 106.25 Gbps Active/ LinkUp)==> 38 1[  ] "dgx-07 mlx5_1" ( Could be 4X )
FAIL  E lid 3 port 2               LinkErrorRecovery +7 (>5) -> flapping
FAIL  E lid 3 port 2               SymbolError +212 in 30s (>100) -> physical
== RESULT: FAILURES PRESENT ==
```

(Note that with the default `T_SYMBOL=100`, the 94 symbol errors measured in Section 3 would PASS; lower the threshold or lengthen the window for an NDR production fabric. The point of AC1 is that value and threshold are both visible so the reviewer can make that call.)

**Verifying the acceptance criteria.**

- **AC1** — each `pass`/`fail`/`warn` call prints the measured value and the threshold in the same line (grep the output for `(>` or `threshold`).
- **AC2** — stop the SM (`systemctl stop opensm` on a test host, or disable the SM on the switch): Check B fails and exit is 1. Power off one node: Check C reports `127 seen, 128 expected`. A 2X link produces the Check D FAIL with the offending iblinkinfo line. `echo $?` after each run shows 1.
- **AC3** — counters are `after − before` across `sleep $WINDOW`; a port with 1,182 historical symbol errors but none in the window passes, which is correct.
- **AC4** — the `NOTE ... RESET` line is printed unconditionally before the reset loop runs.

Extension for production: write the report to `/var/log/ibhealth/<date>.txt`, keep the `before`/`after` arrays as CSV for trending, and run `ibdiagnet --pc --pm_pause_width=30 -P all=1 --extended_speeds all --get_cable_info -r -o /var/log/ibhealth/<date>/` once a week for the full record.
