# Module 3.3 — RDMA over Converged Ethernet (RoCE) from A to Z

> **Exam domain:** NVIDIA InfiniBand Networking (30%) / NVIDIA Spectrum Networking (30%) — RoCE sits on the boundary and is heavily tested from both sides  
> **Source course(s):** RDMA over Converged Ethernet (RoCE) from A to Z (NVIDIA Academy)  
> **Source docs:** Cumulus Linux User Guide — "RDMA over Converged Ethernet (RoCE)" (QoS chapter); MLNX_OFED/DOCA-OFED User Manual — RoCE, QoS and "RoCE Lossless/Lossy" sections; NVIDIA RoCE Configuration community guides (`mlnx_qos`, DCQCN); Spectrum-X Reference Architecture  
> **Estimated study time:** 6–8 hours

## Learning objectives
- Distinguish RoCE v1 from RoCE v2 and lossless from lossy RoCE deployments.
- Explain how **PFC** (802.1Qbb) achieves losslessness, what headroom is, and how PFC storms arise and are contained.
- Explain **ECN**-based congestion control with **DCQCN**: marking thresholds, CNP packets and RP/NP/CP roles.
- Configure host-side RoCE QoS with `mlnx_qos`, `cma_roce_mode`, `cma_roce_tos`, ECN sysfs and `mlnx_tune`.
- Configure switch-side RoCE on Cumulus Linux with NVUE (`nv set qos roce …`) and describe what the profile auto-configures.
- Verify RoCE operation with `ethtool -S`, `nv show interface … counters`, `mlnx_qos -i`, `rdma statistic` and perftest.
- Troubleshoot out-of-order delivery, retransmits and priority-3 discards.
- Choose correct MTU values for switch, host and RoCE path.

## 1. What RoCE is and why it is hard

**RoCE** carries the InfiniBand transport (BTH and payload, the exact packets from Modules 3.1/3.2) over Ethernet. The verbs API, QPs, MRs, SEND/WRITE/READ — all identical. What changes is the network underneath: Ethernet was built to drop packets under congestion and let TCP sort it out. The IB transport, by contrast, assumes a lossless link: a single lost packet triggers a go-back-N retransmission of everything after it (ConnectX HCAs implement selective repeat for some cases, but the default remains expensive), and bandwidth collapses by an order of magnitude at 0.1 % loss. So "configuring RoCE" really means "making Ethernet behave like InfiniBand" using DCB tools: **PFC**, **ECN**, **DSCP**/**PCP** classification, traffic classes, buffer management.

### 1.1 RoCE v1 vs RoCE v2

| | RoCE v1 | RoCE v2 |
|---|---|---|
| Encapsulation | Ethernet header (EtherType **0x8915**) → IB GRH → BTH → payload → ICRC | Ethernet → IPv4/IPv6 → **UDP (dst port 4791)** → BTH → payload → ICRC |
| Addressing | GID derived from MAC (link-local); **L2 only, not routable** | GID = IP address; **routable**, works across L3 spine-leaf fabrics |
| ECMP / load balancing | By MAC only | UDP **source port** varies per QP/flow → hashable by ECMP on every spine |
| Congestion signalling | No standard ECN carrier (no IP header) | **ECN bits in the IP header** → DCQCN works |
| Status | Legacy; disabled on modern fabrics | The default on ConnectX-4 and later, and the only option for Spectrum-X |

Everything that follows assumes RoCE v2. Note a packet-capture tell: RoCE v2 shows up in Wireshark as `RRoCE`/`InfiniBand` over UDP 4791; RoCE v1 as EtherType 0x8915.

### 1.2 Lossless vs lossy RoCE

| Model | Mechanism | Pros | Cons |
|---|---|---|---|
| **Lossless RoCE** | PFC on the RoCE priority end-to-end (host NIC ↔ switches) **plus** ECN/DCQCN to keep PFC rare | Zero drops; the IB transport's go-back-N never fires | PFC configuration on every hop, headroom math, risk of PFC storms/deadlocks, head-of-line blocking within the priority |
| **Lossy RoCE** | No PFC; rely on ECN/DCQCN (+ the NIC's improved loss recovery: selective repeat, adaptive retransmission on ConnectX-5+) and deep buffers to keep loss tiny | Simpler switch config; no PFC storm risk; multi-vendor friendly | Small loss still costs; requires modern HCAs and well-tuned ECN; not NVIDIA's recommendation for training fabrics |
| **Spectrum-X** | ECN/DCQCN-class congestion control plus adaptive routing with reordering handled in the BlueField-3 SuperNIC; PFC configured as backstop in lossless profile | Highest effective bandwidth on Ethernet | Requires Spectrum-4 + BlueField-3 end to end (Module 4.1) |

NVIDIA's guidance for AI training fabrics is lossless (PFC + ECN) with ECN doing almost all of the work and PFC acting as a safety net. Cumulus encodes this as the `nv set qos roce mode lossless` profile (§5).

## 2. PFC — Priority-based Flow Control (802.1Qbb)

Classic 802.3x PAUSE stops *the whole link*. **PFC** adds a priority field: a receiver whose buffer for priority *p* fills past a threshold (**XOFF**) sends a PFC frame telling its link partner "stop sending priority *p* for *t* quanta" and later releases it (**XON**) when the buffer drains. Only the RoCE priority (conventionally **priority 3**) is made lossless; TCP management traffic on priority 0 is still allowed to drop.

### 2.1 Headroom
Between the moment the receiver decides to send XOFF and the moment the last in-flight byte arrives, data keeps coming: the sender's reaction time, the serialization of a maximum-size frame already started, and **2× cable propagation delay** (XOFF travels one way, the final bytes travel the other). The receiver must reserve this **headroom** buffer *per lossless priority per port*, over and above the XOFF threshold, or it drops despite PFC. Headroom grows with link speed and cable length; this is why NVUE's RoCE profile asks for (or assumes) a cable length (`nv show qos roce` prints `cable-length`) and why 100 m fibre runs between leaf and spine need more headroom than 3 m DAC to servers. Spectrum ASICs compute headroom automatically from the configured cable length.

### 2.2 PFC storms, deadlock and the watchdog
If a receiver gets stuck (a hung NIC, a misbehaving driver, a failed buffer) it may hold XOFF asserted indefinitely. Its upstream switch port stops transmitting priority 3, its buffer fills, *it* sends PFC upstream — the pause propagates hop by hop across the fabric: a **PFC storm**, which can freeze every RoCE flow that shares any path with the victim. A related failure is **PFC deadlock**: a cyclic buffer dependency (possible when routing creates loops in a Clos during failures) in which every port waits on another. Counter-measures:

- **PFC watchdog** on the switch: if a port has been paused (receiving PFC XOFF) for longer than a threshold (hundreds of milliseconds), the switch declares a storm, temporarily *drops* traffic on that priority to break the chain, logs an event, and restores after a recovery interval. Cumulus: `nv set qos pfc-watchdog ...` / `nv set interface swpN qos pfc-watchdog enable` (exact syntax by release — verify in current docs); NVUE shows `pfc-watchdog` state in `nv show qos roce`.
- Keep PFC rare by making ECN react first (§3), use adaptive routing / deadlock-free routing designs, and alert on `rx_prio3_pause`/`tx_prio3_pause` counters that never return to zero.

## 3. ECN and DCQCN — congestion control for RoCE

PFC stops loss but is a blunt instrument that spreads congestion. **ECN** (Explicit Congestion Notification) gives the sender an early, per-flow signal so it slows down *before* buffers fill and PFC fires. The algorithm standardised across NVIDIA HCAs and switches is **DCQCN** (Data Center Quantized Congestion Notification).

### 3.1 Roles

| Role | Where | What it does |
|---|---|---|
| **CP — Congestion Point** | Switch egress queue | Monitors queue depth; marks the IP header ECN field to `11` (CE, Congestion Experienced) on packets with probability rising from 0 at `min-threshold` to `probability` (e.g. 100 %) at `max-threshold` (WRED-style), for ECN-capable packets (ECT bits set by the NIC) |
| **NP — Notification Point** | Receiving HCA | On receiving CE-marked RoCE packets, sends a **CNP** (Congestion Notification Packet — a small RoCE packet with BTH opcode CNP) back to the sender for that QP, rate-limited (e.g. at most one per N µs per flow) |
| **RP — Reaction Point** | Sending HCA | On each CNP, multiplicatively cuts the QP's rate (`alpha`-based reduction); in the absence of CNPs, increases rate additively then hyper-additively (fast recovery), à la DCTCP/QCN. Per-QP, in firmware |

### 3.2 Why CNPs need their own priority
CNPs must get back to the sender *fast* and *never* be stuck behind the very congestion they report. So CNPs are marked with a separate, high-priority DSCP and queued in a strict-priority traffic class — conventionally **DSCP 48 → priority/TC 6**. The data path is conventionally **DSCP 26 → priority/TC 3**. These are *common defaults* used by NVIDIA (and the NVUE RoCE profile); other vendors and some deployments use DSCP 24 for data, so always verify both ends match.

### 3.3 Threshold intuition
- `min-threshold` low enough that marking starts well before the PFC XOFF point; `max-threshold` below XOFF so ECN always acts first.
- Too aggressive marking → under-utilisation (senders back off needlessly); too lax → PFC storms do the work instead. Spectrum defaults (NVUE lossless: roughly 150 KB min / 1.5 MB max on TC3 with 100 % probability — the exact numbers vary per ASIC and release) are tuned for 25–400G AI/storage traffic; change only with measurement.
- DCQCN parameters on the NIC (`rate_reduce_monitor_period`, `dce_tcp_g`, `initial_alpha_value`, `rpg_*`) are exposed under `/sys/class/net/<if>/ecn/roce_rp/` and `roce_np/`; defaults are fine for most deployments.

## 4. Host-side configuration (ConnectX / BlueField with MLNX_OFED or DOCA-OFED)

### 4.1 Trust, PFC and traffic classes with mlnx_qos
**mlnx_qos** configures the NIC's DCB state: whether to classify on **DSCP** or **PCP**, which priorities are PFC-enabled, the priority→traffic-class map and the ETS scheduler.

```bash
# Trust DSCP (L3 marking) rather than PCP (VLAN tag) - mandatory for routed RoCE v2
mlnx_qos -i ens1f0np0 --trust dscp

# Enable PFC on priority 3 only (8 comma-separated fields, priorities 0..7)
mlnx_qos -i ens1f0np0 --pfc 0,0,0,1,0,0,0,0

# Optional: priority -> traffic class mapping and ETS (strict for TC6/CNP, DWRR others)
mlnx_qos -i ens1f0np0 --prio_tc 0,0,0,3,0,0,6,0
mlnx_qos -i ens1f0np0 --tsa vendor,vendor,vendor,ets,vendor,vendor,strict,vendor --tcbw 0,0,0,100,0,0,0,0   # verify syntax per release

# Map DSCP -> priority (default already puts DSCP 24-31 -> prio 3, 48-55 -> prio 6)
mlnx_qos -i ens1f0np0 --dscp2prio set,26,3
mlnx_qos -i ens1f0np0 --dscp2prio set,48,6

# Show the whole QoS state of the port
mlnx_qos -i ens1f0np0
```

Expected `mlnx_qos -i` output highlights: `Priority trust state: dscp`, `PFC configuration: priority 0..7 enabled 0 0 0 1 0 0 0 0`, the `dscp2prio` table, and `tc: 3 ratelimit: unlimited, tsa: ets, bw: ...` lines. Settings are not persistent across reboots; put them in a systemd unit, `/etc/network/interfaces` post-up, or the DOCA/OFED `roce_config` hook. Note that interface names are the netdev (not `mlx5_0`); use `ibdev2netdev` to map.

### 4.2 RoCE version and ToS for RDMA CM
Applications that use RDMA CM (NCCL does not — it sets `traffic_class` itself; NVMe-oF, UCX-over-CM, rping do) take the RoCE version and the ToS/DSCP from per-device defaults:

```bash
cma_roce_mode -d mlx5_2 -p 1              # show: "RoCE v2"
cma_roce_mode -d mlx5_2 -p 1 -m 2         # force RoCE v2 (1 = v1)
cma_roce_tos  -d mlx5_2 -p 1              # show ToS used for CM connections
cma_roce_tos  -d mlx5_2 -p 1 -t 106       # ToS 106 = (DSCP 26 << 2) | ECT(0) = 104 + 2
# (Both write under /sys/kernel/config/rdma_cm/mlx5_2/ports/1/default_roce_mode|default_roce_tos)

# Raw-verbs applications pick version via the GID index and set traffic_class themselves:
show_gids                                  # choose the v2 IPv4 GID index (Module 3.2 §6)
# Legacy (ConnectX-3) only: set_roce_mode / ibv_exp_* - obsolete on mlx5 devices
```

NCCL equivalents: `NCCL_IB_GID_INDEX=<v2 index>` (or auto in recent versions), `NCCL_IB_TC=106` (ToS = DSCP 26 × 4 + ECN ECT bit), `NCCL_IB_SL` is ignored on RoCE but `NCCL_IB_PKEY` is not applicable. perftest: `-x <gid>` and `--tclass=106`.

### 4.3 ECN on the host
ECN marking *response* must be enabled on the NIC per priority, for both roles:

```bash
IF=ens1f0np0
cat /sys/class/net/$IF/ecn/roce_np/enable/3          # NP: react to CE by sending CNPs, priority 3
cat /sys/class/net/$IF/ecn/roce_rp/enable/3          # RP: react to CNPs by reducing rate, priority 3
echo 1 > /sys/class/net/$IF/ecn/roce_np/enable/3
echo 1 > /sys/class/net/$IF/ecn/roce_rp/enable/3
cat /sys/class/net/$IF/ecn/roce_np/cnp_dscp           # DSCP used for CNPs (default 48)
cat /sys/class/net/$IF/ecn/roce_np/cnp_802p_prio      # PCP used for CNPs (default 6)
ls /sys/class/net/$IF/ecn/roce_rp/                    # dcqcn knobs: rpg_time_reset, rpg_byte_reset, clamp_tgt_rate, dce_tcp_g, ...
```

On current MLNX_OFED/DOCA releases ECN is enabled by default on all priorities for mlx5; verify rather than assume. Older docs reference `mlnx_qos --dcbx` and `mlx5_core` module parameters; prefer the sysfs interface.

### 4.4 mlnx_tune and performance settings
**mlnx_tune** applies a tuning profile (`HIGH_THROUGHPUT`, `LOW_LATENCY_VMA`, …) adjusting CPU governor, IRQ affinity, ring sizes and relevant sysctls; `mlnx_tune -r` reports the current state and warns on PCIe width/speed, NUMA mismatch, etc. For AI hosts also check: `mlxconfig` `ADVANCED_PCI_SETTINGS`/`MAX_ACC_OUT_READ` (RDMA READ depth on PCIe Gen5), CPU C-states, IOMMU passthrough, and that the RoCE netdev MTU is 9000 (§8).

## 5. Switch-side configuration: Cumulus Linux with NVUE

On Spectrum switches running Cumulus Linux, NVIDIA collapsed the entire DCB recipe into one profile. Use **NVUE** (`nv`) syntax; legacy NCLU (`net`) is deprecated and cannot configure the RoCE profile at all.

### 5.1 Lossless RoCE profile

```bash
nv set qos roce                       # same as: nv set qos roce mode lossless
nv config apply
nv config save
```

What `mode lossless` auto-configures (verified against Cumulus Linux 5.x documentation; details per release):

| Item | Value |
|---|---|
| Port trust | `l3` (DSCP) — PCP also honoured for tagged frames depending on release (`trust-mode` shown in `nv show qos roce`) |
| DSCP → switch priority | DSCP **26 → SP3** (RoCE data), DSCP **48 → SP6** (CNP); others → SP0 |
| Switch priority → traffic class | SP3 → **TC3**, SP6 → **TC6**, all others → TC0 |
| ETS scheduler | TC6 **strict** priority (CNPs first); TC3 and TC0 DWRR **50 % / 50 %** |
| **PFC** | Enabled on **switch priority 3**, rx and tx, with headroom computed from `cable-length` |
| **ECN** | Enabled on **TC3**, absolute thresholds (approx. 150 KB min / 1.5 MB max, 100 % probability — check `nv show qos roce` on your ASIC) |
| Buffers | Separate lossy and `roce-lossless` ingress/egress pools; SP3 placed in the lossless pool |
| LLDP DCBX App TLV | Advertises UDP/4791 at priority 3 so DCBX-capable NICs can learn the RoCE priority |

### 5.2 Lossy profile and variants

```bash
nv set qos roce mode lossy            # ECN on TC3, DSCP/TC mapping, CNP TC6 - but NO PFC, SP3 stays in the lossy pool
nv set qos roce mode lossless-single-ipool   # lossless, but one shared ingress pool (release-dependent)
nv set qos roce mode lossy-multi-tc          # lossy with packet trimming across multiple TCs (Spectrum-4, newer releases)
nv unset qos roce                     # remove the profile entirely
nv config apply
```

Caveat from the docs: if the switch is already in `lossy`, running plain `nv set qos roce` does *not* switch it back — you must specify `mode lossless` explicitly. The RoCE profile is per switch, not per port; port-level overrides are done with standard `nv set qos ...` objects (e.g. different `cable-length` on uplinks: `nv set interface swp49-56 qos pfc ... cable-length 100m`-style settings; verify exact syntax).

### 5.3 Verifying on the switch

```bash
nv show qos roce                      # mode, pfc (priority, rx/tx, cable-length), congestion-control (enabled-tc, min/max, probability),
                                      # trust-mode, lldp-app-tlv, DSCP/PCP->SP and SP->TC tables, pool config, pfc-watchdog state
nv show interface swp1 qos roce status           # per-port view incl. CNP/RoCE mappings and pool membership
nv show interface swp1 qos roce counters         # rx-stats: non-roce, pfc pause frames, roce; tx-stats: cnp, ecn-marked, pfc, roce
nv show interface qos-roce-counters              # all ports at once
nv show interface swp1 counters                  # generic rx/tx/drops/errors
nv show interface swp1 qos pfc                   # pfc per priority
nv show interface swp1 qos congestion-control    # ECN per TC
nv action clear interface swp1 qos roce counters # reset
```

Legacy/underlying Linux equivalents still work for troubleshooting: `ethtool -S swp1 | grep -iE "pause|ecn|prio3|discard"`, `cat /cumulus/switchd/run/...` (switchd state), and **WJH** (What Just Happened) for per-drop reasons (Module 8).

## 6. Spectrum-X preview

Spectrum-X (Spectrum-4 switches + BlueField-3 SuperNICs, Module 4.1) uses the same building blocks but moves beyond DCQCN-on-ECMP: the switch performs per-packet **adaptive routing** across spine uplinks (like IB AR) and the SuperNIC reorders packets using a "Direct Data Placement" mechanism; congestion control is telemetry-assisted (switch-generated congestion telemetry to the SuperNIC rather than only ECN marks), and PFC remains configured on priority 3 as a backstop. NVUE on Spectrum-X exposes this as the RoCE profile plus `nv set router adaptive-routing` / `nv set qos roce` extensions — covered in [Module 4.1](../04-spectrum-ethernet/01-spectrum-x.md). Everything in this module (DSCP 26/48, TC3/TC6, PFC prio 3, ECN) is the foundation Spectrum-X builds on.

## 7. Verification and troubleshooting

### 7.1 Host counters
```bash
IF=ens1f0np0
ethtool -S $IF | grep -E "prio3_pause|prio3_discards|pause_duration|ecn|cnp|out_of_sequence|retrans"
#  rx_prio3_pause / tx_prio3_pause          PFC frames received / sent on priority 3
#  rx_prio3_pause_duration / tx_...         time paused (us) - should be small; growing continuously = congestion or storm
#  rx_prio3_discards                        packets DROPPED on the lossless priority -> PFC not effective (mismatch, headroom, trust wrong)
#  rx_prio3_bytes/packets                   confirms RoCE traffic is actually landing in priority 3

rdma statistic show link mlx5_2/1
#  np_cnp_sent             NP sent CNPs (this host saw ECN-marked packets)
#  rp_cnp_handled          RP received CNPs and reduced rate (remote marked our traffic)
#  np_ecn_marked_roce_packets   received CE-marked packets
#  out_of_sequence         received OOO -> retransmits on the sender; indicates loss or reordering (ECMP/AR)
#  packet_seq_err, local_ack_timeout_err, rnr_nak_retry_err, implied_nak_seq_err, duplicate_request
#  rx_icrc_encapsulated    corrupted RoCE packets (bad cable / bad switch port)

# Same data via sysfs
ls /sys/class/infiniband/mlx5_2/ports/1/hw_counters/
cat /sys/class/infiniband/mlx5_2/ports/1/hw_counters/out_of_sequence
```

### 7.2 Reading the symptoms

| Observation | Likely cause | Fix |
|---|---|---|
| `rx_prio3_discards` increasing on host or `rx ... discards` on the switch TC3 | Traffic classified into prio 3 but PFC not negotiated/enabled on that hop, or headroom too small, or trust mode mismatch (host DSCP vs switch PCP) | Confirm `mlnx_qos -i` and `nv show qos roce` agree: trust dscp, PFC prio 3 both ends; check cable-length; check DSCP 26 actually set (`--tclass`/`NCCL_IB_TC`) |
| RoCE traffic counted in `rx_prio0_*` | DSCP not set by application, or VLAN/PCP trust with untagged traffic | Set `NCCL_IB_TC=106` / `cma_roce_tos 106`, trust dscp |
| `out_of_sequence` and `packet_seq_err` rising, `rp_cnp_handled` low | Packet *loss* (not just reordering) → retransmits | Look for discards upstream; verify PFC; check `rx_icrc_encapsulated` for bad cables |
| `out_of_sequence` rising but no discards anywhere | Reordering from ECMP per-packet spraying or adaptive routing on a NIC that cannot reorder | Use flow-based hashing on non-Spectrum-X; with Spectrum-X ensure BlueField-3 and the AR feature set are enabled |
| `tx_prio3_pause_duration` large and constant; many ports paused | PFC storm | Check pfc-watchdog events, find the stuck receiver (`rx_prio3_pause` on the switch port facing it), check its NIC/driver |
| `np_cnp_sent` huge, throughput poor | Chronic congestion, ECN thresholds too low, or incast pattern | Validate oversubscription and routing; tune thresholds with measurement |
| Bandwidth fine single-flow, collapses at scale | ECMP hash collisions (same as IB static routing) | More QPs per connection (`NCCL_IB_QPS_PER_CONNECTION`), Spectrum-X AR |
| `ib_write_bw` fails to connect | Wrong `-x` GID index, UDP 4791 blocked, RoCE v1 vs v2 mismatch | `show_gids`, pick v2 IPv4 index; `cma_roce_mode` |
| `active_mtu 1024` | Ethernet MTU 1500 | Set host MTU 9000 (§8) |

### 7.3 perftest over RoCE

```bash
# Server
ib_write_bw -d mlx5_2 -i 1 -x 3 --tclass=106 -q 4 -F --report_gbits -a
# Client
ib_write_bw -d mlx5_2 -i 1 -x 3 --tclass=106 -q 4 -F --report_gbits -a 10.10.1.20
# Watch while it runs:
watch -n1 'ethtool -S ens1f0np0 | grep -E "prio3_(pause|discards)"; rdma statistic show link mlx5_2/1 | tr " " "\n" | grep -E "cnp|out_of_seq"'
```

A clean 400G RoCE link reaches ~390 Gb/s with `-q 4`; a run that *passes* but shows CNPs and pause on an uncongested point-to-point test indicates misconfiguration (e.g. mismatched MTU causing fragmentation drops, or trust mismatch).

## 8. MTU: switch, host and RoCE

| Layer | Recommended | Why |
|---|---|---|
| Switch port MTU (Cumulus) | **9216** (`nv set interface swp1-64 link mtu 9216`) | Default on Cumulus; must be ≥ host MTU + any encapsulation (VXLAN adds 50) |
| Host netdev MTU | **9000** (`ip link set ens1f0np0 mtu 9000`) | Jumbo for TCP/storage; leaves room on the switch |
| RoCE path MTU (`active_mtu`) | **4096** | IB MTU values are powers of two up to 4096; 4096 + RoCE/UDP/IP/Ethernet headers (~4150 bytes) fits in 9000 but **not** in 1500 (which caps RoCE at 1024) |

So "RoCE uses 4K MTU even on 9000-byte Ethernet" is expected, not a problem. The exam trap is the reverse: an Ethernet MTU of 1500 anywhere on the path (a mis-set host, a VLAN sub-interface, a VXLAN tunnel) forces `active_mtu` down or, worse, causes silent drops when the two ends negotiated 4096 but a middle hop cannot carry it (IB transport has no path-MTU discovery). Always set MTU consistently, then confirm with `ibv_devinfo | grep mtu` on both hosts and `nv show interface swp1 link mtu` on each switch.

## Configuration / command reference

| Task | Host (mlx5) | Switch (Cumulus NVUE) |
|---|---|---|
| Lossless RoCE baseline | `mlnx_qos -i $IF --trust dscp --pfc 0,0,0,1,0,0,0,0` | `nv set qos roce mode lossless; nv config apply` |
| Lossy | `mlnx_qos -i $IF --trust dscp --pfc 0,0,0,0,0,0,0,0` (ECN only) | `nv set qos roce mode lossy` |
| RoCE version (CM apps) | `cma_roce_mode -d mlx5_2 -p 1 -m 2` | n/a |
| DSCP for data | `cma_roce_tos -d mlx5_2 -p 1 -t 106`; `NCCL_IB_TC=106`; perftest `--tclass=106` | DSCP 26 → SP3 (profile default) |
| CNP DSCP | `/sys/class/net/$IF/ecn/roce_np/cnp_dscp` (48) | DSCP 48 → SP6 strict (profile default) |
| ECN on/off | `echo 1 > /sys/class/net/$IF/ecn/roce_{np,rp}/enable/3` | part of profile; `nv show qos roce` → congestion-control |
| MTU | `ip link set $IF mtu 9000` | `nv set interface swp1-64 link mtu 9216` |
| Show QoS state | `mlnx_qos -i $IF` | `nv show qos roce`, `nv show interface swp1 qos roce status` |
| Counters | `ethtool -S $IF`, `rdma statistic show link mlx5_2/1` | `nv show interface swp1 qos roce counters`, `nv show interface swp1 counters` |
| GID / version | `show_gids`, `ibv_devinfo` | n/a |
| Tuning | `mlnx_tune -p HIGH_THROUGHPUT`, `mlnx_tune -r` | buffers via `nv set qos traffic-pool …` |
| Test | `ib_write_bw -d mlx5_2 -x <gid> --tclass=106 -q 4 -F --report_gbits <ip>` | watch `qos roce counters` |

## Common pitfalls & exam traps
- **RoCE v1 is L2-only (EtherType 0x8915); RoCE v2 is UDP/4791 over IP and routable** and the only one that supports ECN.
- **PFC prevents loss; ECN/DCQCN prevents congestion.** You want ECN to fire *first* and PFC to be a rarely-used backstop; thresholds are set accordingly.
- **CNP = Congestion Notification Packet**, generated by the *receiver* (NP) and acted on by the *sender* (RP); the switch (CP) only marks ECN.
- **Common defaults: data DSCP 26 → priority/TC 3; CNP DSCP 48 → priority/TC 6 (strict).** They are conventions, not laws — mismatched DSCP between host and switch is the #1 RoCE misconfiguration.
- **Trust DSCP, not PCP, for routed RoCE v2**; PCP is lost at L3 hops and untagged frames have none.
- `rx_prio3_discards` on a "lossless" priority means PFC is not actually working on that hop.
- **Headroom** depends on cable length and speed; PFC without correct headroom still drops.
- **PFC storms** propagate hop-by-hop; the **PFC watchdog** breaks them by temporarily dropping.
- **NVUE RoCE profile is switch-wide**; `nv set qos roce` alone will not move a switch out of `lossy` — specify `mode lossless`. NCLU cannot configure it.
- **MTU trio: switch 9216, host 9000, RoCE path 4096.** 1500 anywhere caps RoCE at 1024 or causes silent drops.
- perftest on RoCE needs `-x <v2 GID index>`; wrong index = connect failure, not a slow run.
- `out_of_sequence` can be loss *or* reordering; correlate with discard counters before blaming the fabric design.

## Key takeaways
- RoCE = IB transport over Ethernet; v2 = UDP 4791, routable, ECN-capable; v1 = legacy L2.
- Lossless RoCE = PFC on priority 3 + ECN/DCQCN; lossy RoCE = ECN only; Spectrum-X adds adaptive routing and SuperNIC reordering.
- PFC (802.1Qbb) pauses one priority; headroom = in-flight data after XOFF; watchdog breaks PFC storms.
- DCQCN: switch (CP) marks ECN between min/max thresholds; receiver (NP) sends CNPs (DSCP 48, TC6 strict); sender (RP) cuts rate per QP.
- Host: `mlnx_qos -i IF --trust dscp --pfc 0,0,0,1,0,0,0,0`; `cma_roce_mode`/`cma_roce_tos` for CM apps; ECN via `/sys/class/net/IF/ecn/roce_{np,rp}/enable/3`; `mlnx_tune` for host tuning.
- Switch: `nv set qos roce mode lossless` → trust DSCP, DSCP26→TC3, DSCP48→TC6 strict, PFC prio 3, ECN TC3, lossless pool, LLDP App TLV; `nv show qos roce` to verify; `mode lossy` drops PFC.
- Verify: `ethtool -S` (`*_prio3_pause*`, `rx_prio3_discards`), `rdma statistic show` (`np_cnp_sent`, `rp_cnp_handled`, `out_of_sequence`), `nv show interface swpN qos roce counters`.
- perftest over RoCE: `-x <gid index>` and `--tclass=106`.
- MTU: switch 9216 / host 9000 / RoCE 4096.

## Review questions

1. Which statement about RoCE v2 is correct?
   A. It uses EtherType 0x8915 and cannot cross routers
   B. It encapsulates the IB transport in UDP destination port 4791 over IP and is routable
   C. It requires an InfiniBand Subnet Manager
   D. It is only supported on Spectrum-X

2. In DCQCN, which element generates CNP packets?
   A. The switch when its queue exceeds the ECN max threshold
   B. The sending NIC (reaction point)
   C. The receiving NIC (notification point) upon receiving ECN-marked packets
   D. The Subnet Manager

3. Why are CNPs mapped to a separate strict-priority traffic class (commonly DSCP 48 → TC6)?
   A. To allow them to be dropped first under congestion
   B. So they reach the sender quickly and are not queued behind the congested data traffic
   C. Because PFC cannot pause TC6
   D. To bypass ECMP hashing

4. A host shows `rx_prio3_discards` incrementing while `rx_prio3_pause` stays at zero on the connected switch port. What is the most likely issue?
   A. ECN thresholds are too low
   B. PFC is not enabled (or trust/priority mapping differs) on this hop, so lossless priority 3 traffic is being dropped
   C. The RoCE MTU is 4096
   D. CNPs are not being generated

5. What does `nv set qos roce mode lossless` configure that `nv set qos roce mode lossy` does not?
   A. ECN on TC3
   B. DSCP 26 → priority 3 mapping
   C. PFC on switch priority 3 and placement in the lossless buffer pool
   D. CNP mapping to TC6

6. On a 9000-byte-MTU host connected to 9216-byte-MTU switch ports, `ibv_devinfo` reports `active_mtu: 4096`. What should the engineer do?
   A. Nothing; 4096 is the maximum RoCE/IB path MTU and is expected
   B. Raise the host MTU to 9216
   C. Lower the switch MTU to 9000
   D. Enable connected mode

7. A PFC storm is freezing RoCE traffic across several leaves. Which switch feature is designed to break the storm?
   A. ECN marking
   B. PFC watchdog, which temporarily drops traffic on the stuck priority after a pause timeout
   C. Adaptive routing
   D. LLDP App TLV

8. Which host command sets the NIC to classify on DSCP and enable PFC only for priority 3?
   A. `mlnx_qos -i eth0 --trust pcp --pfc 1,1,1,1,1,1,1,1`
   B. `mlnx_qos -i eth0 --trust dscp --pfc 0,0,0,1,0,0,0,0`
   C. `cma_roce_mode -d mlx5_0 -p 1 -m 2`
   D. `ethtool -A eth0 rx on tx on`

### Answer key
1. B — v2 adds IP/UDP (4791) and therefore routing and ECN; v1 is the L2-only EtherType variant.
2. C — The NP (receiver) sends CNPs; the CP (switch) marks ECN; the RP (sender) reduces rate.
3. B — Feedback latency determines how fast senders back off; CNPs must not sit behind the congestion they report.
4. B — Discards on the lossless priority mean PFC did not protect it: not enabled, headroom wrong, or priority/trust mismatch.
5. C — Both modes configure trust, DSCP/TC mapping, ECN and CNP handling; only lossless adds PFC and the lossless pool.
6. A — RoCE path MTU is capped at 4096 by the IB MTU encoding regardless of jumbo Ethernet MTU.
7. B — The watchdog detects a port paused beyond a threshold and drops/restores to break the chain.
8. B — Trust DSCP for routed RoCE v2 and PFC only on priority 3; `ethtool -A` is global 802.3x pause, not PFC.

## References
- NVIDIA Academy: *RDMA over Converged Ethernet (RoCE) from A to Z*
- Cumulus Linux User Guide — RDMA over Converged Ethernet (RoCE) — https://docs.nvidia.com/networking-ethernet-software/cumulus-linux-517/Layer-1-and-Switch-Ports/Quality-of-Service/RDMA-over-Converged-Ethernet-RoCE/
- Cumulus Linux User Guide — Quality of Service (PFC, ECN, buffers, pfc-watchdog) — https://docs.nvidia.com/networking-ethernet-software/cumulus-linux-517/Layer-1-and-Switch-Ports/Quality-of-Service/
- MLNX_OFED / DOCA-OFED User Manual — "RoCE", "Quality of Service (QoS)", "Explicit Congestion Notification (ECN)" — https://docs.nvidia.com/networking/
- NVIDIA community guides: *Lossless RoCE Configuration for Linux Drivers in DSCP-Based QoS Mode*; *RoCE Configuration for Cumulus* — https://enterprise-support.nvidia.com/s/ (search titles)
- Zhu et al., *Congestion Control for Large-Scale RDMA Deployments* (DCQCN), SIGCOMM 2015 — background on DCQCN
- NVIDIA Spectrum-X Reference Architecture — https://docs.nvidia.com/networking-ethernet-software/ (Spectrum-X section)
- NCCL environment variables (`NCCL_IB_TC`, `NCCL_IB_GID_INDEX`) — https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html
- Related modules: [3.1 RDMA Fundamentals](01-rdma-fundamentals.md), [3.2 RDMA Programming](02-rdma-programming-fundamentals.md), [2.3 IB QoS/AR/SHARP](../02-infiniband/03-infiniband-qos-adaptive-routing-sharp.md), Module 4.1 Spectrum-X (`../04-spectrum-ethernet/01-spectrum-x.md`), Module 8 WJH/NetQ (`../08-troubleshooting/`)
