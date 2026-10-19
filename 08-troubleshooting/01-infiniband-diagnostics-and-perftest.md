# Module 8.1 — InfiniBand Diagnostics and RDMA Performance Testing

> **Exam domain:** Troubleshooting Tools (20%)  
> **Source course(s):** InfiniBand Essentials; InfiniBand Professional (Troubleshooting units)  
> **Source docs:** NVIDIA InfiniBand Fabric Utilities (infiniband-diags); ibdiagnet User Manual; perftest (github.com/linux-rdma/perftest); "Testing RDMA latency with ib_write_lat"; IPoIB chapter of the MLNX_OFED / DOCA-Host user manual; NCCL Troubleshooting guide  
> **Estimated study time:** 4 hours

## Learning objectives
- Describe what each InfiniBand port state and physical state means and infer the most likely cause from the combination (for example `Initializing` + `LinkUp` + no LID = no Subnet Manager).
- Inventory a fabric and locate degraded links with **ibnodes**, ibswitches, ibhosts and **iblinkinfo**.
- Verify end-to-end IB-layer reachability with **ibping**, ibtracert and ibroute, and explain why ICMP ping does not test the same thing.
- Read port error counters with perfquery and distinguish "cable/physical" counters from "congestion" counters.
- Run **ibdiagnet** with the flags the exam expects, locate its output files, and interpret the error/warning summary.
- Run **ib_write_bw** / **ib_write_lat** / ib_read_bw / ib_send_bw in client–server mode (including GPUDirect with `--use_cuda`) and judge whether the numbers are healthy for the link rate.
- Troubleshoot an IPoIB interface that is up but cannot ping (PKey, SM, connected vs. datagram mode).
- Apply a bottom-up troubleshooting methodology and pick the right tool for a given symptom.

## 1. Where these tools live and how they talk to the fabric

The InfiniBand diagnostics ship as part of MLNX_OFED / DOCA-Host (package `infiniband-diags`, plus `ibutils2` for ibdiagnet) and are also present on managed Quantum switches (MLNX-OS / NVOS) and on the UFM appliance. There are two mechanisms underneath:

| Mechanism | Used by | What it needs |
|---|---|---|
| **Local verbs query** (reads the HCA driver) | ibstat, ibstatus, ibv_devinfo | Only a working driver; works even with no SM |
| **Management Datagrams (MADs)** over QP0/QP1 | ibnodes, iblinkinfo, ibping, ibtracert, ibroute, perfquery, ibportstate, saquery, sminfo, ibdiagnet | A port in `Active` state with a LID, i.e. a running **SM**. Most MAD tools need root for the `umad` device. |

That split is the first thing to internalize: if a tool like `iblinkinfo` hangs or returns "MAD timeout", the problem is usually upstream of the tool — the local port is not Active, or the SM is not running, not that the fabric is broken.

Common options shared by MAD-based tools: `-C <ca>` selects the HCA (`-C mlx5_0`), `-P <port>` the port number, `-G` interprets an address as a **GUID** instead of a **LID**, `-t <ms>` sets MAD timeout.

## 2. Local port state: ibstat, ibstatus, ibv_devinfo

### 2.1 ibstat

**ibstat** is the first command in every IB troubleshooting session. It reads the local HCA and reports every port.

```bash
$ ibstat
CA 'mlx5_0'
    CA type: MT4129
    Number of ports: 1
    Firmware version: 28.41.1000
    Hardware version: 0
    Node GUID: 0x946dae0300a1b2c0
    System image GUID: 0x946dae0300a1b2c0
    Port 1:
        State: Active
        Physical state: LinkUp
        Rate: 400
        Base lid: 37
        LMC: 0
        SM lid: 1
        Capability mask: 0xa651e848
        Port GUID: 0x946dae0300a1b2c0
        Link layer: InfiniBand
```

Field by field:

| Field | Meaning | Healthy value |
|---|---|---|
| State | Logical (link-layer) port state managed by the SM | `Active` |
| Physical state | Electrical/optical state negotiated by the two ends | `LinkUp` |
| Rate | Negotiated data rate in Gb/s (4x NDR = 400, 4x HDR = 200, 4x EDR = 100) | Matches the slowest of HCA, cable, switch port |
| Base lid | The **LID** assigned by the SM | Non-zero |
| SM lid | LID of the master SM | Non-zero; identical on every host in the subnet |
| Port GUID | 64-bit unique port identifier (burned in) | Used by `-G` options and UFM |
| Link layer | `InfiniBand` or `Ethernet` (RoCE) | Depends on `LINK_TYPE_P1` in mlxconfig |

The state machine matters because the exam asks you to infer cause from state:

| State | Physical state | Meaning | Most likely cause |
|---|---|---|---|
| `Down` | `Polling` | No signal detected from peer | Cable unplugged, bad cable, remote port disabled, remote host powered off |
| `Down` | `Disabled` | Port administratively disabled | `ibportstate ... disable`, UFM isolation, or `mlxlink -a DN` |
| `Initializing` (Init) | `LinkUp` | Physical link is good, but no LID assigned | **No Subnet Manager** reachable (not running, wrong subnet, or SM port down). Base lid shows 0. |
| `Armed` | `LinkUp` | SM has configured the port but not yet moved it to Active | Transient during SM sweep; persistent Armed = SM problem |
| `Active` | `LinkUp` | Fully operational | — |

Rule of thumb: **Physical state** is about layer 1 (cable, transceiver, port). **State** is about the SM. A port stuck in `Initializing` with `LinkUp` is almost never a cable problem.

### 2.2 ibstatus and ibv_devinfo

**ibstatus** is a terse variant that is handy in scripts and shows the rate as text:

```bash
$ ibstatus mlx5_0
Infiniband device 'mlx5_0' port 1 status:
    default gid:     fe80:0000:0000:0000:946d:ae03:00a1:b2c0
    base lid:        0x25
    sm lid:          0x1
    state:           4: ACTIVE
    phys state:      5: LinkUp
    rate:            400 Gb/sec (4X NDR)
    link_layer:      InfiniBand
```

Note that LIDs are shown in hex here (0x25 = 37) and in decimal in `ibstat`. The exam likes that mismatch.

`ibv_devinfo` (`-v` for everything) comes from `libibverbs` rather than `infiniband-diags` and shows what an RDMA application sees: max QPs, MTU, GID table entries, and the device's verbs capabilities. Use `ibv_devinfo -d mlx5_0 -v | grep -i gid` to find the GID index you will need for RoCE perftest (`-x`).

```bash
$ ibv_devinfo -d mlx5_0
hca_id: mlx5_0
    transport:            InfiniBand (0)
    fw_ver:               28.41.1000
    node_guid:            946d:ae03:00a1:b2c0
    max_mr_size:          0xffffffffffffffff
    port:   1
        state:            PORT_ACTIVE (4)
        max_mtu:          4096 (5)
        active_mtu:       4096 (5)
        sm_lid:           1
        port_lid:         37
        link_layer:       InfiniBand
```

`active_mtu` is worth checking: an IB fabric with a 2048 MTU somewhere will negotiate paths down, and IPoIB MTU depends on it.

## 3. Fabric inventory: ibnodes, ibswitches, ibhosts, sminfo, saquery

**ibnodes** discovers the whole subnet via MADs and lists every node (switches and channel adapters). ibswitches and ibhosts are the filtered views.

```bash
$ ibswitches
Switch : 0xb8cef603002a1a00 ports 65 "MF0;leaf-01:MQM9700/U1" enhanced port 0 lid 3 lmc 0
Switch : 0xb8cef603002a1b40 ports 65 "MF0;leaf-02:MQM9700/U1" enhanced port 0 lid 4 lmc 0
Switch : 0xb8cef603002a2200 ports 65 "MF0;spine-01:MQM9700/U1" enhanced port 0 lid 1 lmc 0

$ ibhosts
Ca  : 0x946dae0300a1b2c0 ports 1 "dgx-01 mlx5_0"
Ca  : 0x946dae0300a1b2d4 ports 1 "dgx-01 mlx5_1"
...
```

Use this to answer "are all 32 nodes present?" — count the output (`ibhosts | wc -l`) and compare to the expected inventory. The node description string (`"MF0;leaf-01:MQM9700/U1"`) comes from the switch's configured hostname; unlabelled switches show a default and make every later step harder, so set hostnames early.

`sminfo` tells you who the master SM is and its priority/state:

```bash
$ sminfo
sminfo: sm lid 1 sm guid 0xb8cef603002a2200, activity count 189231 priority 15 state 3 SMINFO_MASTER
```

State 3 = MASTER. If `sminfo` times out, there is no SM, which explains every host sitting in `Initializing`.

`saquery` queries the Subnet Administrator database directly: `saquery NR` lists node records, `saquery PR <src-lid> <dst-lid>` returns path records (SL, MTU, rate) — useful to confirm that the SM actually computed a path with the SL your QoS policy expects. `saquery -p` shows the PKey table the SM holds, which is the authoritative source when debugging partition membership.

## 4. Per-link health: iblinkinfo

**iblinkinfo** walks the fabric and prints every link with its negotiated width and speed, and what the far end is. It is the fastest way to find a degraded link.

```bash
$ iblinkinfo --switches-only
Switch: 0xb8cef603002a1a00 MF0;leaf-01:MQM9700/U1:
      3    1[  ]  ==( 4X  106.25 Gbps Active/  LinkUp)==>      37    1[  ] "dgx-01 mlx5_0" ( )
      3    2[  ]  ==( 2X  106.25 Gbps Active/  LinkUp)==>      38    1[  ] "dgx-01 mlx5_1" ( Could be 4X )
      3    3[  ]  ==(                Down/ Polling)==>             [  ] "" ( )
      3   33[  ]  ==( 4X  106.25 Gbps Active/  LinkUp)==>       1   17[  ] "MF0;spine-01:MQM9700/U1" ( )
```

Reading the line: local LID 3, port 1, width 4X, lane speed 106.25 Gbps (NDR per lane), state Active/LinkUp, connected to LID 37 port 1 on `dgx-01 mlx5_0`. The second line is the exam's favourite: a link that came up at **2X instead of 4X**. iblinkinfo flags it in parentheses with `Could be 4X` (and similarly `Could be 100 Gbps` for speed downgrades). Half the lanes failed to train; the usual causes are a damaged or marginal cable, a dirty connector, or an OSFP breakout plugged into the wrong port. Correlate with `mlxlink` (Module 8.3) for the per-lane view.

Useful options:

| Option | Purpose |
|---|---|
| `--switches-only` | Skip HCA-originated lines; halves the output on a big fabric |
| `-l` | One line per link, machine-friendly (easy to `grep "Could be"`) |
| `--down` / `-d` | Show only down links |
| `-S <guid>` / `-n <hops>` | Start from a given switch GUID / limit discovery depth |
| `--load-cache <file>` / `--diff` | Compare against a saved discovery to spot what changed |

The one-liner every IB admin knows: `iblinkinfo -l | grep -i "could be"` lists every degraded link in the fabric.

## 5. Reachability and routing: ibping, ibtracert, ibroute

### 5.1 ibping

**ibping** tests IB-layer reachability using vendor-specific MADs, not IP. Unlike ICMP, the far end does not answer by default: you must start a responder.

```bash
# On the target host
$ sudo ibping -S
# On the source host, by LID or by port GUID
$ sudo ibping -L 37
Pong from dgx-01.(none) (Lid 37): time 0.041 ms
Pong from dgx-01.(none) (Lid 37): time 0.038 ms
$ sudo ibping -G 0x946dae0300a1b2c0 -c 5
```

What ibping proves: both ports are Active, the SM has assigned LIDs, and the switches have forwarding table entries between the two LIDs. What it does **not** prove: that RDMA data QPs work, that PKeys match for your application partition, or that performance is acceptable. "ibping works but IPoIB ping fails" therefore points straight at IPoIB configuration (PKey, IP, mode) rather than the fabric. Exam trap: candidates forget `-S`; without a server you get `ibwarn: ... ibping ... timeout` and wrongly conclude the fabric is broken.

### 5.2 ibtracert and ibroute

`ibtracert <src-lid> <dst-lid>` prints the hop-by-hop path the SM programmed, by reading each switch's linear forwarding table (LFT):

```bash
$ sudo ibtracert 37 52
From ca {0x946dae0300a1b2c0} portnum 1 lid 37-37 "dgx-01 mlx5_0"
[1] -> switch port {0xb8cef603002a1a00}[1] lid 3-3 "MF0;leaf-01:MQM9700/U1"
[33] -> switch port {0xb8cef603002a2200}[17] lid 1-1 "MF0;spine-01:MQM9700/U1"
[21] -> switch port {0xb8cef603002a1b40}[34] lid 4-4 "MF0;leaf-02:MQM9700/U1"
[5] -> ca port {0x946dae0300a1c110}[1] lid 52-52 "dgx-05 mlx5_0"
To ca {0x946dae0300a1c110} portnum 1 lid 52-52 "dgx-05 mlx5_0"
```

Use it to confirm that traffic between two rail-0 NICs really stays on the rail-0 leaf (rail-optimized design) rather than taking an extra spine hop, and to find which switch to inspect when only one pair of nodes is slow. `-m <mlid>` traces a multicast tree instead.

`ibroute <switch-lid>` dumps a switch's LFT — which output port serves each destination LID. `ibroute -M` dumps the multicast forwarding table. With **adaptive routing** enabled, the LFT is only the static fallback; AR groups are inspected via `ibdiagnet -r` or UFM, not `ibroute`.

## 6. Port counters: perfquery and ibqueryerrors

perfquery reads the Port Counters performance MAD of any port in the fabric: `perfquery [lid] [port]`. With no arguments it reads the local port.

```bash
$ sudo perfquery -x 3 2
# Port extended counters: Lid 3 port 2 (CapMask: 0x5A00)
PortSelect:......................2
CounterSelect:...................0x0000
PortXmitData:....................8812003346122
PortRcvData:.....................9122381910023
PortXmitPkts:....................33120239812
PortRcvPkts:.....................34092312891
SymbolErrorCounter:..............1182
LinkErrorRecoveryCounter:........3
LinkDownedCounter:...............1
PortRcvErrors:...................27
PortRcvRemotePhysicalErrors:.....0
PortRcvSwitchRelayErrors:........0
PortXmitDiscards:................0
PortXmitConstraintErrors:........0
PortRcvConstraintErrors:.........0
LocalLinkIntegrityErrors:........0
ExcessiveBufferOverrunErrors:....0
VL15Dropped:.....................0
PortXmitWait:....................4421902
```

`-x` requests the 64-bit extended counters (the 32-bit data counters wrap in seconds at NDR). `-R` resets the counters after reading, which is how you measure a rate: reset, wait 60 seconds, read again. `-a` reads all ports of a switch.

Interpretation, grouped by what they imply:

| Counter | Category | Interpretation |
|---|---|---|
| SymbolErrorCounter | Physical | Bit errors on the wire, pre-FEC-correction visible errors. Rising steadily = marginal cable/transceiver/connector |
| LinkErrorRecoveryCounter | Physical | Link retrained successfully. A few at boot are fine; incrementing during operation = flapping link |
| LinkDownedCounter | Physical | Link went fully down. Correlate with cable events |
| PortRcvErrors | Physical/protocol | Malformed or corrupt packets received (CRC). Cable-side issue on the *remote* transmitter or this receiver |
| PortRcvRemotePhysicalErrors | Physical (upstream) | Packets marked bad *by an earlier hop*. The fault is somewhere before this link, not on it |
| LocalLinkIntegrityErrors / ExcessiveBufferOverrunErrors | Physical | Serious; usually goes with symbol errors |
| PortXmitDiscards | Congestion/forwarding | Packets dropped on transmit (switch lifetime limit exceeded, no route). Points at congestion or routing, not cables |
| PortXmitWait | Congestion | Ticks the port had data but no credits. High relative to XmitData = head-of-line blocking / congested path. The key counter for "fabric is up but slow" |
| VL15Dropped | Management | SM traffic dropped; usually only during storms |

Exam distinction to memorize: **SymbolError / LinkErrorRecovery / LinkDowned / PortRcvErrors → cable and physical layer. PortXmitDiscards / PortXmitWait → congestion and routing.** A single error is not a failure; what matters is the *rate*.

ibqueryerrors sweeps the whole fabric and prints only ports whose counters exceed thresholds (`-s` to suppress specific counters, `-r` to also show per-port rates, `--data` for data counters, `-k` to reset after reading). It is the quick "show me every unhealthy port" command when you do not want the full ibdiagnet run.

## 7. Port control: ibportstate

ibportstate queries or changes a *remote* port via MADs — the way to bounce a switch port from a host without logging into the switch:

```bash
$ sudo ibportstate 3 2 query          # show state, width, speed of LID 3 port 2
$ sudo ibportstate 3 2 disable        # admin-down it (Physical state -> Disabled)
$ sudo ibportstate 3 2 enable
$ sudo ibportstate 3 2 reset          # force a retrain without disabling
$ sudo ibportstate 3 2 width 1        # bitmask; 1=1X,2=4X,4=8X,8=12X (verify in current docs)
$ sudo ibportstate 3 2 speed 1        # SDR mask; use espeed/fdr10 for extended speeds
```

Disabling a switch port that carries your own path cuts you off — the classic self-inflicted outage. Prefer UFM or the switch CLI for anything near the management path, and remember that `Disabled` physical state in `ibstat` output means someone did this on purpose.

## 8. Full fabric diagnostic: ibdiagnet

**ibdiagnet** (ibutils2) is the comprehensive sweep: it discovers topology, reads every port's counters and capabilities, checks the SM's routing tables for correctness and credit loops, pulls cable EEPROM data, and writes a set of files to `/var/tmp/ibdiagnet2/`.

```bash
# Baseline run, reset counters first and pause 30s so counters reflect a measured window
$ sudo ibdiagnet --pc --pm_pause_width=30 -P all=1 --extended_speeds all --get_cable_info -r
```

| Flag | Purpose |
|---|---|
| `--pc` | Clear (reset) all port counters at the start |
| `--pm_pause_width=<sec>` | Wait this long after clearing so the second read measures a real interval |
| `-P all=1` | Report any counter that incremented by ≥1 as a warning (threshold policy; tune for production, e.g. `-P symbol_error_counter=100`) |
| `--extended_speeds all` | Read extended speed and lane info (needed to see NDR per-lane details) |
| `--get_cable_info` | Pull transceiver/cable EEPROM: vendor, PN, SN, length, temperature. Written to `ibdiagnet2.cables` |
| `-r` | Routing validation: checks LFTs for correctness, dead-ends and credit loops; reports AR configuration |
| `--sc` / `--skip` | Skip stages (e.g. `--skip dup_guids`) |
| `-o <dir>` | Alternate output directory |
| `--sharp` | Validate **SHARP** aggregation tree configuration |
| `-i <ca> -p <port>` | Which local HCA/port to use as the discovery point |

On a managed switch or UFM appliance the same tool is available (`ibdiagnet` from the switch CLI; UFM runs it on a schedule and shows results under Fabric Health).

Output files (in `/var/tmp/ibdiagnet2/`):

| File | Contents |
|---|---|
| `ibdiagnet2.log` | Human-readable stage-by-stage log and the summary table — read this first |
| `ibdiagnet2.db_csv` | Full machine-readable database: nodes, ports, counters, cable info; what UFM and scripts parse |
| `ibdiagnet2.pm` | Port counters for every port |
| `ibdiagnet2.fdbs` | Linear forwarding tables (routing) of every switch |
| `ibdiagnet2.net_dump` | Topology in a node/port/neighbor listing (useful diff against a cabling plan — see Module 8.2) |
| `ibdiagnet2.cables` | Cable EEPROM data when `--get_cable_info` is used |
| `ibdiagnet2.sm` | SM information |
| `ibdiagnet2.ar` / `.plft` | Adaptive routing groups and tables when `-r` is used |

The summary at the end of the log is what you act on:

```text
-I- Stage                     Warnings   Errors   Comment
-I- Discovery                        0        0
-I- Lids Check                       0        0
-I- Links Check                      1        0
-I- Subnet Manager                   0        0
-I- Port Counters                    2        0
-I- Nodes Information                0        0
-I- Speed / Width checks             1        0
-I- Partition Keys                   0        0
-I- Alias GUIDs                      0        0
-I- Temperature Sensing              0        0
-I- Routing                          0        0
-I- Cables                           0        0

-W- link ... : Unexpected actual link width 2x (expected 4x) ...
-W- lid=0x0003 ... port=2 : SymbolErrorCounter=1182 exceeds threshold
```

Workflow: run once at bring-up with `--pc` to establish a clean baseline, keep the `db_csv`, then re-run after any change and diff. "Routing" errors mentioning **credit loops** indicate a cyclic dependency in the forwarding tables that can deadlock the fabric under load; they come from miscabling that does not match the routing engine's topology assumptions (for example, a fat-tree engine on a fabric with an unexpected cross-link). Fix the cabling or choose the right routing engine in `opensm.conf` / UFM.

## 9. RDMA performance: perftest

The perftest suite (`ib_write_bw`, **ib_write_lat**, `ib_read_bw`, `ib_read_lat`, `ib_send_bw`, `ib_send_lat`, plus `ib_atomic_*`) is the standard micro-benchmark for a single link or path. It is client–server: start the server with no address, then point the client at the server's IP (the TCP/IP connection is only used to exchange QP information; the actual test traffic is RDMA).

```bash
# Server (dgx-05)
$ ib_write_bw -d mlx5_0 -i 1 -F --report_gbits -a
# Client (dgx-01)
$ ib_write_bw -d mlx5_0 -i 1 -F --report_gbits -a 10.0.0.5
```

Key options:

| Option | Meaning |
|---|---|
| `-d mlx5_0` / `-i 1` | Device and port. Always specify on a multi-NIC node, or you may test the wrong rail |
| `-x <gid-index>` | GID index — required for **RoCE** (pick the RoCEv2 GID for the IP you use, from `show_gids`/`ibv_devinfo -v`). Not needed on IB |
| `-R` | Use `rdma_cm` for connection setup (resolves GID/route automatically; the normal way on RoCE) |
| `-a` | Run all message sizes 2 B → 8 MB; `-s <bytes>` for one size |
| `-q <n>` | Number of QPs; a single QP will not saturate an NDR link, use `-q 4`–`-q 8` |
| `-D <sec>` | Run for a duration instead of iteration count (`-n`) |
| `-F` | Do not fail if CPU frequency scaling is on (otherwise it refuses to run on many servers) |
| `--report_gbits` | Gb/s instead of MB/s |
| `-S <sl>` | Service level — test the specific SL / traffic class your QoS policy maps RDMA to |
| `-m <mtu>` | Path MTU; `--tclass` sets DSCP-bearing traffic class on RoCE |
| `--use_cuda=<gpu>` | Allocate buffers in GPU memory → tests **GPUDirect RDMA** across the NIC/GPU PCIe path. Requires the perftest build with CUDA support and `nvidia-peermem` or DMA-BUF |
| `-b` | Bidirectional |
| `--run_infinitely` | Keep going until killed — useful while reseating a cable to watch for drops |

Sample output (illustrative) from `ib_write_bw -a --report_gbits` on 4x NDR:

```text
---------------------------------------------------------------------------------------
                    RDMA_Write BW Test
 Dual-port       : OFF          Device         : mlx5_0
 Number of qps   : 1            Transport type : IB
 Connection type : RC           Using SRQ      : OFF
 TX depth        : 128
 Mtu             : 4096[B]
 Link type       : IB
 Max inline data : 0[B]
 rdma_cm QPs     : OFF
---------------------------------------------------------------------------------------
 #bytes     #iterations    BW peak[Gb/sec]    BW average[Gb/sec]   MsgRate[Mpps]
 2          5000             0.11               0.11                 6.812
 64         5000             3.41               3.38                 6.605
 4096       5000           189.32             188.90                 5.764
 65536      5000           391.84             391.67                 0.747
 1048576    5000           392.11             392.05                 0.047
 8388608    5000           392.02             392.01                 0.006
---------------------------------------------------------------------------------------
```

Expected ballpark numbers (verify against your exact platform):

| Link | Signalling rate | Realistic `ib_write_bw` large-message | `ib_write_lat` small message |
|---|---|---|---|
| 4x NDR (400G) | 400 Gb/s | ~385–395 Gb/s | ~1.0–1.3 µs (half round-trip) |
| 4x HDR (200G) | 200 Gb/s | ~195 Gb/s | ~1.0–1.4 µs |
| 400G RoCE (ConnectX-7 / BlueField-3) | 400 Gb/s | ~380–390 Gb/s | ~1.5–2.5 µs |

If you see ~195 Gb/s on an NDR link, the link trained at 2X or HDR — go back to `iblinkinfo`. If you see 350 Gb/s and a GPU is the buffer, check the PCIe topology (`nvidia-smi topo -m`): the NIC and GPU should share a PCIe switch for full GPUDirect performance. Latency tests report a table of minimum, typical (median) and maximum, plus 99% and 99.9% percentiles; a high max with a good median usually means the host (CPU power states, IRQ affinity), not the fabric.

`ucx_perftest` (`ucx_perftest -t ucp_put_bw` server; `ucx_perftest <host> -t ucp_put_bw` client) tests the UCX layer that HPC-X MPI uses, and is the step between raw verbs and MPI when debugging an MPI performance complaint.

## 10. nccl-tests as a fabric test

Once every link passes perftest, run `all_reduce_perf` from nccl-tests across the nodes: `mpirun -np 16 -H dgx-01:8,dgx-02:8 ./all_reduce_perf -b 8 -e 8G -f 2 -g 1`. The *busbw* column at large sizes should approach the per-GPU NIC rate: bus bandwidth is reported in GB/s, and with one 400 Gb/s (50 GB/s) NIC per GPU a healthy multi-node all_reduce lands in the mid-40s GB/s (verify against the reference numbers published for your system). A single degraded link drags the *whole* collective down to that link's speed, which is why fabric-wide `iblinkinfo` comes first.

Useful environment: `NCCL_DEBUG=INFO` (prints which NICs/GIDs were selected and whether GDR was used), `NCCL_IB_HCA=mlx5_0,mlx5_1,...` (restrict to the rail NICs — exclude storage NICs), `NCCL_IB_GID_INDEX` (RoCE GID), `NCCL_IB_TC` / `NCCL_IB_SL` (QoS class), `NCCL_NET_GDR_LEVEL`. "NCCL WARN NET/IB : Got completion with error 12" (transport retry exceeded) during a run is almost always a fabric link problem or PFC/ECN misconfiguration on RoCE, not NCCL. See `../01-ai-datacenter-design/` for NCCL fundamentals.

## 11. IPoIB troubleshooting

IPoIB (`ib0`) puts IP on top of IB using the broadcast multicast group of a **PKey**. Common failure: `ib0` is UP with an address, but `ping` to a peer fails while `ibping` works.

Check in this order:

1. **Port Active?** `ibstat` — IPoIB is dead without a LID.
2. **Same PKey?** `cat /sys/class/net/ib0/pkey` on both hosts (default `0x7fff` = full-member default partition). A child interface (`ib0.8001`) created with `echo 0x8001 > /sys/class/net/ib0/create_child`) must use a PKey the SM has actually assigned to *both* ports (`saquery -p`, or `smpquery pkeys <lid>`). Mismatched PKey = no multicast group join = no ARP.
3. **Mode:** `cat /sys/class/net/ib0/mode` — `datagram` (UD, MTU up to 4092 B, works everywhere) vs `connected` (RC, MTU up to 65520 B, higher throughput, both ends must agree or large packets fail while small pings succeed).
4. **MTU:** `ip link show ib0`; a 65520 connected-mode MTU talking to a 2044 datagram peer fails on large frames (PMTU does not help on the same L2).
5. **Multicast group join:** `saquery MCMR` shows the IPoIB broadcast groups the SM created; if the SM's `opensm.conf` partitions file omits `ipoib` for that PKey, no group exists.
6. **Firewall / routing** as on any Linux box.

Remember IPoIB performance is not RDMA performance — a 40 Gb/s `iperf` over IPoIB on an NDR link is normal, not a fault.

## 12. Troubleshooting methodology

Work bottom-up; each layer depends on the one below.

```text
1. Physical/cable  -> ibstat Physical state; mlxlink -m / -c (8.3); perfquery SymbolErrors
2. Link state      -> ibstat State; iblinkinfo width/speed ("Could be 4X")
3. SM / LID        -> sminfo; Base lid != 0; SM lid identical everywhere
4. Partition/QoS   -> saquery -p; /sys/class/net/ib0/pkey; saquery PR for SL
5. Routing         -> ibtracert; ibroute; ibdiagnet -r (credit loops, AR)
6. RDMA perf       -> ib_write_bw / ib_write_lat per link; ucx_perftest
7. Application     -> nccl-tests; NCCL_DEBUG=INFO; GPU/NIC PCIe affinity
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

## Configuration / command reference

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

## Common pitfalls & exam traps
- `Initializing` + `LinkUp` is an **SM** problem, not a cable problem. `Down` + `Polling` is physical.
- `ibping` needs `ibping -S` on the target. It tests IB-layer reachability (LIDs and forwarding), not IP and not RDMA data-path performance.
- LIDs: `ibstat` prints decimal, `ibstatus`/`saquery` print hex. 0x25 and 37 are the same port.
- `iblinkinfo` prints "Could be 4X" for a width downgrade — that text, not a counter, is how you spot a degraded cable at fabric scale.
- SymbolError/LinkErrorRecovery/LinkDowned/PortRcvErrors = physical. PortXmitWait/PortXmitDiscards = congestion/routing. Do not replace a cable for XmitWait.
- Use `perfquery -x` for 64-bit counters; the 32-bit data counters wrap within seconds at NDR and look like they "reset".
- `ibdiagnet --pc` clears counters — do not run it with `--pc` when you are trying to *preserve* evidence of a past event.
- `-x <gid>` (or `-R`) is needed for perftest on RoCE; on native IB it is unnecessary. Mismatched GID index gives "Couldn't connect" or hangs.
- A single QP rarely saturates NDR; a "slow" `ib_write_bw` at `-q 1` may be a test artifact. Add `-q 4` before blaming the fabric.
- `-F` only suppresses the CPU-frequency warning; it does not fix the inaccurate *latency* that frequency scaling causes. Pin governor to performance for latency tests.
- IPoIB throughput is CPU-bound and far below RDMA line rate by design. Do not "fix" it.
- Prefer `ibdiagnet` from a host with a rail that reaches the whole subnet; on a multi-rail SuperPOD each rail may be a *separate subnet* and needs a separate run with `-i <ca>`.

## Key takeaways
- `ibstat` first: State (SM-driven) and Physical state (layer 1) answer different questions.
- `sminfo` confirms a master SM exists; no SM means every port sits in Initializing with LID 0.
- `ibhosts`/`ibswitches` give the inventory; `iblinkinfo -l | grep "Could be"` finds degraded links.
- `ibping -S` on the server, `ibping -L <lid>` on the client; it is a MAD-based test, not ICMP.
- `ibtracert` shows the programmed path; `ibroute` dumps a switch LFT.
- `perfquery -x` for 64-bit counters, `-R` to reset; physical counters vs. congestion counters.
- `ibdiagnet --pc --pm_pause_width=30 -P all=1 --extended_speeds all --get_cable_info -r` writes to `/var/tmp/ibdiagnet2/`; read `ibdiagnet2.log` summary first.
- perftest is client/server; specify `-d`/`-i`, use `-a`, `-q`, `--report_gbits`, `-x`/`-R` for RoCE, `--use_cuda` for GPUDirect.
- NDR ≈ 390+ Gb/s and ≈ 1–2 µs latency; half the rate means a 2X or HDR-trained link.
- Bottom-up: cable → link → SM → partition → routing → RDMA perf → application.

## Review questions

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
6. B — ~196 Gb/s on NDR is exactly a 2X or HDR-trained link; verify the negotiated width before tuning the test.
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
