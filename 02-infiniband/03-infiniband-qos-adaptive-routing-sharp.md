# Module 2.3 — InfiniBand QoS, Adaptive Routing and SHARP

> **Exam domain:** NVIDIA InfiniBand Networking (30%)  
> **Source course(s):** InfiniBand Network Administration (QoS and routing units); NVIDIA SHARP overview training  
> **Source docs:** NVIDIA Adaptive Routing Configuration Guide (MLNXSM); InfiniBand QoS chapter of the OpenSM/MLNXSM documentation; NVIDIA SHARP (Scalable Hierarchical Aggregation and Reduction Protocol) Deployment Guide; NCCL environment variable reference; Quantum-2 product documentation  
> **Estimated study time:** 5–7 hours

## Learning objectives
- Describe Virtual Lanes, Service Levels, SL2VL mapping and VL arbitration and how they deliver QoS in InfiniBand.
- Configure QoS in opensm.conf and choose the SL used by an application (NCCL, perftest).
- Contrast IB credit-based flow control with RoCE's PFC, and explain IB congestion control (CC/PCC).
- Describe the problem **adaptive routing** solves, its modes and groups, and how packet reordering is handled.
- Enable adaptive routing in MLNXSM/UFM and understand its interaction with NCCL.
- Describe **SHARP** in-network aggregation: components (sharp_am, sharpd), aggregation trees, and how NCCL uses it.
- Verify SHARP with `sharp_hello` and nccl-tests.
- Describe SHIELD self-healing and fast link recovery.

## 1. InfiniBand QoS building blocks

Ethernet QoS is about *classification* (DSCP/PCP) → *queues* (traffic classes) → *scheduling* (strict/WRR). InfiniBand has the same three stages with its own names, and — because the **SM** programs every port — it is configured once centrally rather than per switch.

### 1.1 Virtual Lanes (VLs)
A **VL** is a separate buffer and credit pool on a physical link. Each port supports up to 15 data VLs (VL0–VL14) plus **VL15**, which is reserved for subnet management traffic (SMPs) and is never flow-controlled. Real hardware supports fewer: ConnectX/Quantum ports are typically configured for 8 data VLs (`max_op_vls`). Because flow control is per VL, traffic on VL1 can stall without blocking VL0 — this is how IB avoids head-of-line blocking between classes and why deadlock-free routing engines (torus-2QoS) use VLs for loop breaking.

### 1.2 Service Levels (SLs)
The **SL** is a 4-bit field (0–15) in the Local Route Header. It is the *end-to-end* class marker chosen by the application when it creates a connection (via the path record from the SA or an explicit QP attribute). SL is to IB what DSCP is to IP: it travels with the packet across the whole subnet.

### 1.3 SL2VL mapping
At each switch port (and HCA port), an **SL2VL table** maps the 16 SLs to the operational VLs on the *output* port. The SM programs these tables; the default is identity (SL n → VL n) truncated to the available VLs. Different ports can have different tables (e.g. a torus routing engine uses this to switch VLs mid-path), but in a fat-tree the mapping is uniform.

### 1.4 VL arbitration
Each output port has a **VL arbitration table** with two sub-tables, **high priority** and **low priority**, plus a **high-limit** value:

- Each entry is `VL:weight`. The arbiter scans the active table round-robin, letting a VL transmit up to `weight × 64 bytes` before moving on (weight 0 = skip; the exact byte granularity is an IB-spec detail — "verify in current docs" if asked precisely).
- The **high-priority table** is served first. `high_limit` bounds how many high-priority bytes (in units of 4 KiB; 255 = unlimited) can be sent before the arbiter must service the low-priority table once, which prevents starvation.
- Entries can repeat a VL to increase its share.

So: SL picks the class, SL2VL picks the queue, VLArb decides bandwidth share and priority. Note there is no "drop" anywhere — IB QoS shapes who goes first, not who gets discarded.

## 2. Expressing QoS in opensm.conf

OpenSM/MLNXSM has a simple global QoS block plus optional per-port-type overrides (`qos_ca_*`, `qos_sw0_*`, `qos_swe_*`, `qos_rtr_*` for HCA ports, switch port 0, switch external ports, routers). The policy that *assigns* SLs to flows lives in a separate `qos-policy.conf` file (or is left to applications).

```text
# ---- /etc/opensm/opensm.conf : QoS section ----
qos TRUE                         # master switch; FALSE leaves defaults and ignores the rest
qos_max_vls 8                    # operational data VLs to use (<= hardware max)
qos_high_limit 255               # high-priority byte limit before low table gets a turn (255 = unlimited)

# High-priority arbitration table: VL:weight pairs
qos_vlarb_high 0:0,1:0,2:0,3:0,4:0,5:0,6:0,7:255
# Low-priority table: share bandwidth among VL0..VL6
qos_vlarb_low  0:64,1:32,2:32,3:16,4:16,5:16,6:16,7:0

# SL -> VL map (16 entries, one per SL)
qos_sl2vl 0,1,2,3,4,5,6,7,0,1,2,3,4,5,6,7

# Optional: the policy file that maps QPs/ULPs/PKeys/ports to SLs
qos_policy_file /etc/opensm/qos-policy.conf
```

Reading the example: SL7→VL7 is placed only in the high table with a large weight, so anything sent on SL 7 (e.g. latency-sensitive control traffic or SHARP aggregation traffic) pre-empts everything else; SL0–SL6 share bandwidth on the low table with VL0 getting the largest weight. In GPU clusters a very common simpler design is two classes: default compute traffic on SL0 and storage on SL1 with a smaller weight, or NCCL on one SL and SHARP on another.

### 2.1 How applications select an SL
Because the SL rides with the connection, the host stack chooses it:

| Stack | How to set SL |
|---|---|
| **NCCL** | `NCCL_IB_SL=<0-15>` (default 0). All NCCL IB QPs use this SL; also `NCCL_IB_TC` for RoCE traffic class |
| **UCX** (OpenMPI, NVSHMEM over UCX) | `UCX_IB_SL=<n>` |
| **perftest** | `ib_write_bw -S <sl>` / `ib_write_lat -S <sl>` (`--sl`) |
| **RDMA CM** applications | SL taken from the SA path record; policy can be set in `qos-policy.conf` by PKey, ULP or service ID; or `rdma_set_option(..., RDMA_OPTION_ID_TOS)` maps to SL via `cma_roce_tos`-style mapping on RoCE |
| **IPoIB** | `sl=` in the partition definition (partitions.conf) |

A quick functional test of QoS: run two `ib_write_bw` streams on different SLs across the same oversubscribed link and compare throughput against the configured weights.

## 3. Flow control and congestion control in IB

### 3.1 Credit-based flow control (lossless by design)
On every link, for every data VL, the receiver advertises **credits** (free buffer in 64-byte units) to the transmitter in flow-control packets. The transmitter may only send what fits. There is no equivalent of "send and hope" followed by pause frames; buffer overflow simply cannot happen at the link level. Consequences:

- Loss in an IB fabric comes only from *bit errors* (bad cables; packets fail CRC and are dropped, visible as `PortRcvErrors`/`SymbolErrors`) or from *lifetime/HoQ time-outs* when a packet sits too long at the head of a queue (`PortXmitDiscards`).
- Compare RoCE ([Module 3.3](../03-rdma/03-roce-a-to-z.md)): Ethernet is lossy by default and needs **PFC** (802.1Qbb) configured on every hop and every host to approximate losslessness, plus careful buffer headroom math. IB gives you that for free and on a per-VL basis.
- The downside of pure back-pressure is **congestion spreading**: a slow receiver throttles the link into its leaf, which fills, throttles the spine, and so on ("tree saturation" / victim flows). Congestion control exists to counter this.

### 3.2 IB congestion control (CC) and PCC
The IB specification defines a **Congestion Control** mechanism: switches mark packets with a FECN bit when a port's queue crosses a threshold; the destination HCA returns BECN; the source HCA reduces its injection rate for that flow using a per-QP rate table (CCT). The SM distributes the CC parameters (`congestion_control` settings; MLNXSM has a `ccmgr`/congestion-control manager — parameter names "verify in current docs").

Quantum-2 and ConnectX-7 introduce **Programmable Congestion Control (PCC)** — the congestion algorithm runs on firmware-programmable engines in the HCA, so NVIDIA can ship improved algorithms (and customers can tune them) without new silicon. NVIDIA refers to the Quantum-2-era HCA algorithm as **ZTR** (Zero-Touch RoCE on the Ethernet side; on IB the same PCC infrastructure runs NVIDIA's IB congestion algorithm). For the exam: know that CC is end-to-end rate limiting triggered by switch marking, that it complements (does not replace) credit-based FC, and that PCC makes it programmable in the NIC firmware. **DCQCN** is the RoCE analogue and is covered in Module 3.3.

## 4. Adaptive Routing

### 4.1 The problem: static routing in a Clos
With `ftree`, every destination LID has exactly one output port at each switch (the LFT is a static table). Traffic from many sources to a spine is spread by *destination*, not by *flow*. Two heavy flows whose destinations happen to map to the same uplink will share it even though a sibling uplink is idle. In AI training with a handful of very large "elephant" flows per GPU (NCCL rings/trees), these collisions are frequent and cost 20–40 % of achievable all-reduce bandwidth on a statically routed fabric. The same phenomenon on Ethernet is ECMP hash collision, which Spectrum-X addresses with its own adaptive routing (Module 4.1).

### 4.2 What AR does
**Adaptive routing** lets the switch choose, per packet, among a *group* of equivalent output ports based on their current queue occupancy (local congestion). Components:

- **AR groups** — for each destination, the set of ports that are valid next hops (in a fat-tree: all uplinks leading toward a spine that can reach the destination). The SM computes these and programs an **AR LFT** (ARLFT) and **AR group table** into Quantum switches, in addition to the regular LFT.
- **AR modes** per destination/entry:
  - **Static** — behave like the normal LFT (no AR for this destination).
  - **Free** — pick the least-loaded port in the group for every packet; maximum balancing, maximum reordering.
  - **Bounded** — "sticky" selection: keep using the same port for a flow while it is not congested, move only when its queue crosses a threshold; a compromise that limits reordering. Also called *limited* in some docs.
  - (Quantum-2 docs also describe time-bound / "fast" variants; verify names in the AR guide for your MLNXSM version.)
- **Out-of-order handling** — packets of one RC connection may now arrive out of order. ConnectX-5 and later HCAs support **out-of-order RDMA** (OOO) for RC and UD: the receiver places RDMA WRITE/READ data directly by address and tolerates reordering within a window, so the transport does not retransmit. SENDs with ordering semantics are more constrained, which is why AR is typically enabled for RC and UD transports and the HCA advertises the capability (`enable_ar_by_device_cap`). The SM records which SLs are allowed to be reordered in the **OOO SL mask**.

### 4.3 Enabling AR in MLNXSM / UFM
Adaptive routing requires MLNXSM (NVIDIA's enhanced SM, included in MLNX_OFED/DOCA-OFED and UFM), Quantum-family switches and recent HCA firmware. The AR-capable routing engines are `ar_ftree`, `ar_updn`, `ar_dor` and `ar_torus`. A representative configuration (names from NVIDIA's AR guide; defaults and newer knobs — "verify in current docs"):

```text
# ---- opensm.conf : adaptive routing (MLNXSM) ----
routing_engine ar_ftree             # AR-capable fat-tree engine
adv_routing_engine auto             # pick advanced engine matching routing_engine
ar_mode 3                           # 0 = disabled, 1 = enabled, 2 = enabled + notifications,
                                    # 3 = let the routing engine decide (recommended)
ar_sl_mask 0xFFFF                   # bitmask of SLs allowed to use AR (0xFFFF = all)
ar_transport_mask 0x000A            # transports with AR: bit1 = RC, bit3 = UD  (0x000A = RC+UD)
enable_ar_by_device_cap TRUE        # only enable AR toward HCAs that support OOO
root_guid_file /etc/opensm/root_guid.cfg
```

In **UFM**, the same settings are under Settings → Subnet Manager → Adaptive Routing (and `routing_engine = ar_ftree` in `gv.cfg`); UFM writes them into its generated opensm.conf.

Verification from a host:

```bash
ibswitches -C mlx5_0                       # get switch LIDs
smparquery ARInfo -L <switch-lid> -C mlx5_0 # "AR Status: enabled", group cap, modes
ibdiagnet                                   # then:
grep -A1 OOOSLMask /var/tmp/ibdiagnet2/ibdiagnet2.db_csv   # per-port OOO SL mask (e.g. 0xfffa)
```

If `ar_sl_mask` excludes an SL (e.g. 0xFFFA excludes SL0 and SL2), traffic on those SLs is routed statically — a way to keep an ordering-sensitive ULP off AR while NCCL uses it.

### 4.4 AR and NCCL
NCCL benefits strongly from AR on IB. Relevant knobs:

- **`NCCL_IB_AR_THRESHOLD`** — message size (bytes, default 8192) above which NCCL uses RDMA operations compatible with adaptive routing (large messages are sent so the receiver can tolerate reordering; smaller ones stay on the ordered path). Lower it to push more traffic onto AR, raise it to reduce AR use. Only meaningful on IB with AR enabled.
- **`NCCL_IB_SL`** must be an SL present in `ar_sl_mask` for NCCL traffic to be adaptively routed.
- **`NCCL_IB_QPS_PER_CONNECTION`** / **`NCCL_IB_SPLIT_DATA_ON_QPS`** — spreading a connection over multiple QPs is an older way of fighting static hash collisions; with AR it is usually unnecessary and left at 1.

Symptom of AR being *off* on a large NDR fabric: nccl-tests all-reduce bus bandwidth plateaus well below line rate (e.g. 250–300 Gb/s per GPU instead of ~390 Gb/s on NDR) with high `PortXmitWait` on some spine uplinks and near-zero on others — the signature of hash collisions.

## 5. SHARP: in-network aggregation

### 5.1 What it is
A ring or tree all-reduce across N GPUs moves each byte of the result roughly 2×(N−1)/N times across the network and makes every GPU do reduction arithmetic. **SHARP** (Scalable Hierarchical Aggregation and Reduction Protocol) moves the reduction into the Quantum switch ASICs: each GPU sends its data *once* up an aggregation tree; switches sum (or min/max/etc.) the data on the way up; the root sends the result back down once. Benefits: roughly half the data crossing the fabric, lower and far more predictable latency, and GPU SMs freed from reduction work. It is the main reason InfiniBand still posts better all-reduce numbers than equivalently sized Ethernet fabrics for some collective patterns.

Generations: SHARPv1 (Switch-IB 2, small-message/latency focus), SHARPv2 (Quantum: large-message streaming aggregation, "SAT" — Streaming Aggregation Trees), SHARPv3 (Quantum-2: multiple tenants/jobs at once, larger payloads), SHARPv4 (Quantum-X800: 800G, further scaling).

### 5.2 Components
| Component | Runs on | Role |
|---|---|---|
| **sharp_am** (Aggregation Manager) | A management host — typically the **UFM** server or the SM host | Discovers SHARP-capable switches (via the SM), builds **aggregation trees** over the fabric topology, allocates per-job tree resources, manages quotas |
| **sharpd** (SHARP daemon) | Every compute host | Local agent; brokers job/communicator requests from the application library to sharp_am over the fabric; part of the `sharp` package in MLNX_OFED/DOCA |
| **libsharp / libsharp_coll** | Linked into the application via a plugin | The client library; NCCL uses it through the **NCCL SHARP (CollNet) plugin**, MPI through HCOLL/UCC |
| **Switch SHARP engine** | Quantum ASIC | Executes the reductions; one or more "aggregation nodes" per switch with finite buffer and group resources |
| SM | — | Must be MLNXSM/UFM with SHARP support enabled (`sharp_enabled`/UFM "SHARP" toggle); provides topology to sharp_am |

### 5.3 Enabling SHARP for NCCL
Prerequisites: Quantum or newer switches, SHARP-capable HCAs (ConnectX-6+), MLNXSM/UFM with SHARP enabled, `sharp` packages installed on hosts, `sharpd` running (`systemctl status sharpd`), and `sharp_am` running on UFM/SM host (`systemctl status sharp_am`; in UFM enable it under Settings → Subnet Manager → SHARP). Then, on the hosts:

```bash
# Install/verify the NCCL SHARP plugin (ships with HPC-X or as nccl-rdma-sharp-plugins)
ls /opt/mellanox/sharp/lib/ ; ls $(dirname $(ldconfig -p | grep libnccl-net | head -1 | awk '{print $NF}'))

# NCCL environment
export NCCL_COLLNET_ENABLE=1               # enable the CollNet (SHARP) algorithm
export NCCL_ALGO=CollnetDirect,CollnetChain,Ring,Tree   # optional; let NCCL choose
export NCCL_IB_HCA=mlx5                    # or list of devices
export NCCL_DEBUG=INFO                     # look for "SHARP" / "CollNet" lines
export LD_LIBRARY_PATH=/opt/mellanox/sharp/lib:$LD_LIBRARY_PATH   # plugin + libsharp
# Plugin-specific
export SHARP_COLL_ENABLE_SAT=1             # streaming aggregation for large messages
export SHARP_COLL_LOG_LEVEL=3
export NCCL_SHARP_GROUP_SIZE_THRESH=2      # min ranks/node for SHARP use (verify default)
```

NCCL will only use SHARP when the job spans multiple nodes and the CollNet plugin loads successfully; `NCCL_DEBUG=INFO` shows `NCCL INFO Connected ... via COLLNET` or `SHARP` in the log.

### 5.4 Verifying SHARP

```bash
# 1. Daemon-level sanity on two hosts
sharp_hello -d mlx5_0:1                     # host A; must reach sharp_am and create a trivial group
# 2. Aggregation manager state (on the AM host)
sharp_am -v ; systemctl status sharp_am
sharp_cmd  # / sharp_am CLI: show trees, jobs, quotas (command set per SHARP version)
# 3. End-to-end with nccl-tests
mpirun -np 16 -H nodeA:8,nodeB:8 -x NCCL_COLLNET_ENABLE=1 -x NCCL_DEBUG=INFO \
   ./build/all_reduce_perf -b 8 -e 8G -f 2 -g 1
```

Compare `busbw` with and without `NCCL_COLLNET_ENABLE`. On a healthy 2-node NDR setup SHARP visibly improves large-message all-reduce and especially small/medium-message latency; if there is no difference and the log shows no COLLNET, the plugin did not load or sharp_am rejected the job (check `/var/log/sharp_am.log` and `sharpd` logs).

SHARP uses its own SL and QoS class by convention (so aggregation traffic is not starved by bulk traffic); UFM/MLNXSM expose `sharp_sl`-style settings — verify names in the SHARP deployment guide.

## 6. SHIELD and fast link recovery

**SHIELD** (Self-Healing Interconnect Enhancement for inteLligent Datacenters) is a Quantum-family switch feature that lets switches react to a link failure *locally* instead of waiting for the SM:

- Normally a link failure → trap to SM → SM re-sweep and new LFTs → fabric repaired in hundreds of milliseconds to seconds (longer on huge fabrics). During that window flows routed over the dead link stall.
- With SHIELD, when a switch detects a port down it immediately re-routes affected destinations onto an alternative port from the AR group (or a pre-computed backup), informing neighbours as needed, in roughly a millisecond (NVIDIA quotes ~5,000× faster than SM-driven recovery). The SM still recomputes the global solution afterwards.
- SHIELD therefore depends on AR groups being available — enable AR first. In MLNXSM/UFM it is a toggle alongside AR (`shield_mode` or the "SHIELD" option in UFM; "verify in current docs").

**Fast link recovery / Fast Link-Up** on Quantum-2 reduces the time for a flapping link to return to Active. Together with UFM's auto-isolation of repeatedly flapping ports, these features turn a cable hiccup that used to kill a NCCL job into a brief slowdown.

## Configuration / command reference

| Purpose | Setting / command |
|---|---|
| Turn on QoS in SM | `qos TRUE`, `qos_max_vls`, `qos_high_limit`, `qos_vlarb_high`, `qos_vlarb_low`, `qos_sl2vl` in opensm.conf |
| QoS policy to SL | `qos_policy_file /etc/opensm/qos-policy.conf` |
| App SL | `NCCL_IB_SL`, `UCX_IB_SL`, `ib_write_bw -S <sl>`, partition `sl=` |
| Adaptive routing | `routing_engine ar_ftree`, `ar_mode 3`, `ar_sl_mask 0xFFFF`, `ar_transport_mask 0x000A`, `enable_ar_by_device_cap TRUE` |
| Verify AR | `smparquery ARInfo -L <lid>`, `ibdiagnet` → `OOOSLMask` |
| NCCL + AR | `NCCL_IB_AR_THRESHOLD` (default 8192) |
| SHARP services | `systemctl status sharp_am` (AM host), `systemctl status sharpd` (compute) |
| SHARP + NCCL | `NCCL_COLLNET_ENABLE=1`, SHARP plugin in `LD_LIBRARY_PATH`, `SHARP_COLL_ENABLE_SAT=1` |
| SHARP test | `sharp_hello -d mlx5_0:1`; nccl-tests `all_reduce_perf` with `NCCL_DEBUG=INFO` |
| SHIELD | Enable AR, then SHIELD toggle in UFM / MLNXSM (verify parameter) |
| Congestion symptom | `PortXmitWait` high on some uplinks (UFM Telemetry / `perfquery -x`) |

## Common pitfalls & exam traps
- **SL is end-to-end and chosen by the application; VL is per-link and chosen by the SL2VL table.** Candidates reverse these.
- **VL15 is management only** and never flow-controlled; data VLs are VL0–VL14 (hardware typically 8).
- **IB does not use PFC or ECN.** It is lossless through credit-based flow control; CC/PCC is an additional rate-control layer, not a loss-avoidance mechanism.
- **AR requires MLNXSM/UFM and Quantum switches**; stock OpenSM `ftree` is static. AR engines are `ar_ftree`, `ar_updn`, etc.
- **Out-of-order delivery** is the cost of AR; HCAs since ConnectX-5 handle it for RC/UD, and `ar_sl_mask` lets you exclude SLs that cannot tolerate it.
- **`NCCL_IB_AR_THRESHOLD`** is a *message size*, not an on/off switch; AR itself is enabled on the SM, not in NCCL.
- **SHARP needs sharp_am (central) *and* sharpd (every host) *and* the NCCL CollNet plugin with `NCCL_COLLNET_ENABLE=1`.** Missing any one silently falls back to Ring/Tree.
- SHARP reduces *data movement for collectives*; it does not speed up point-to-point RDMA or storage traffic.
- **SHIELD depends on AR** — it reroutes using AR group alternatives without waiting for the SM.
- `qos_high_limit 255` means unlimited high-priority bursts; low-priority starvation is possible if misconfigured.

## Key takeaways
- IB QoS: SL (end-to-end, 0–15) → SL2VL table → VL (per-link buffer/credit pool) → VL arbitration (high/low tables, weights, high_limit).
- opensm.conf: `qos TRUE`, `qos_max_vls`, `qos_vlarb_high/low` as `VL:weight`, `qos_sl2vl`.
- Apps select SL: `NCCL_IB_SL`, `UCX_IB_SL`, `ib_write_bw -S`.
- IB is lossless via credit-based flow control per VL; no PFC/ECN needed. CC/PCC adds end-to-end rate control; DCQCN is the RoCE equivalent.
- Adaptive routing fixes static-route collisions by choosing the least-loaded port in an AR group per packet; modes free / bounded / static.
- Enable AR in MLNXSM: `routing_engine ar_ftree`, `ar_mode 3`, `ar_sl_mask`, `ar_transport_mask`; verify with `smparquery ARInfo`.
- Out-of-order RDMA on ConnectX-5+ makes AR safe for RC/UD; `NCCL_IB_AR_THRESHOLD` controls which NCCL messages use AR-friendly ops.
- SHARP performs reductions inside Quantum switches along aggregation trees; components are sharp_am (on UFM/SM host), sharpd (hosts), libsharp + NCCL CollNet plugin.
- Enable with `NCCL_COLLNET_ENABLE=1`; verify with `sharp_hello` and nccl-tests.
- SHIELD reroutes around failed links in ~1 ms using AR alternatives; the SM fixes the global picture later.

## Review questions

1. Which InfiniBand field travels end-to-end with a packet and is chosen by the application to indicate its class of service?
   A. VL
   B. SL
   C. PKey
   D. LID

2. In opensm.conf, `qos_vlarb_high 7:255` with `qos_high_limit 255` and all other VLs in the low table will cause what behaviour?
   A. VL7 traffic is dropped when congested
   B. VL7 is served strictly before all other VLs, which may starve them
   C. VL7 is rate-limited to 255 Mb/s
   D. VL7 is mapped to VL15

3. Why does an InfiniBand fabric not require PFC to be lossless?
   A. IB switches have infinite buffers
   B. IB uses credit-based flow control per VL at the link layer
   C. IB uses ECN marking to prevent loss
   D. The SM drops excess traffic before it enters the fabric

4. A 2,048-GPU NDR fabric shows all-reduce bus bandwidth 30 % below expectation, with some spine uplinks saturated and others idle. Which single change most directly addresses this?
   A. Increase IPoIB MTU to 65520
   B. Enable adaptive routing (`routing_engine ar_ftree`, `ar_mode 3`) in MLNXSM/UFM
   C. Set `NCCL_IB_SL=7`
   D. Lower `qos_max_vls` to 2

5. What is the role of `ar_sl_mask`?
   A. Chooses which switch ports belong to an AR group
   B. Selects which service levels are permitted to use adaptive routing
   C. Sets the SL used by SHARP traffic
   D. Masks LIDs that must be routed statically

6. Which set of components must all be present for NCCL to use SHARP?
   A. sharp_am, sharpd, NCCL CollNet plugin, `NCCL_COLLNET_ENABLE=1`
   B. opensm, ibdiagnet, `NCCL_IB_AR_THRESHOLD=0`
   C. UFM Telemetry, PFC enabled on hosts, `NCCL_IB_TC=106`
   D. sharp_hello, perfquery, `NCCL_ALGO=Ring`

7. What does SHARP primarily reduce?
   A. The latency of point-to-point RDMA WRITE operations
   B. The number of LIDs the SM must assign
   C. The amount of data crossing the fabric and GPU work during collective reductions
   D. Cable BER through better FEC

8. How does SHIELD recover from a link failure faster than the SM alone?
   A. It pre-computes a full backup LFT for every possible failure
   B. The switch locally redirects affected traffic onto alternative AR-group ports before the SM resweeps
   C. It disables the SM sweep timer
   D. It retransmits lost packets from switch buffers

### Answer key
1. B — The SL is a 4-bit LRH field set by the sender; VLs are per-link queues chosen via SL2VL.
2. B — High-priority table with unlimited high_limit is strict priority; weights only matter among VLs within the same table.
3. B — Receivers advertise credits per VL; transmitters only send what fits, so link-level loss cannot occur.
4. B — The pattern is static-routing hash collisions; AR balances per packet across spine uplinks.
5. B — Bitmask of SLs allowed to be adaptively routed (and hence reordered).
6. A — AM for trees, sharpd on hosts, the CollNet plugin, and NCCL told to use CollNet.
7. C — In-network reduction halves traffic volume and offloads the arithmetic from GPUs.
8. B — Local rerouting via AR alternatives, with the SM fixing global routing afterwards.

## References
- NVIDIA Adaptive Routing Configuration Guide (MLNXSM / UFM) — https://docs.nvidia.com/networking/ (search "Adaptive Routing")
- HPC Advisory Council, *Adaptive Routing* (opensm.conf AR examples) — https://hpcadvisorycouncil.atlassian.net/wiki/spaces/HPCWORKS/pages/1639186443/Adaptive+Routing
- MLNXSM (InfiniBand Subnet Manager) Utility Release Notes — https://docs.nvidia.com/networking/display/mlnxsmrnv5150
- OpenSM `opensm(8)` man page, QoS section; MLNX_OFED User Manual, "Quality of Service" and "Congestion Control" chapters
- NVIDIA SHARP Deployment Guide / Release Notes — https://docs.nvidia.com/networking/ (search "SHARP")
- NCCL Environment Variables — https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html
- NVIDIA Quantum-2 InfiniBand platform (SHARPv3, SHIELD, PCC) — https://www.nvidia.com/en-us/networking/quantum2/
- Related modules: [2.1 IB Administration](01-infiniband-network-administration.md), [2.2 UFM](02-ufm-dc-management.md), [3.3 RoCE A to Z](../03-rdma/03-roce-a-to-z.md), Module 1.3 NCCL + GPUDirect (`../01-ai-datacenter-design/`)
