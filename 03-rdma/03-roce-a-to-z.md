# Module 3.3 — RDMA over Converged Ethernet (RoCE) from A to Z

> **Exam domain:** NVIDIA InfiniBand Networking (30%) / NVIDIA Spectrum Networking (30%) — RoCE sits on the boundary and is heavily tested from both sides  
> **Source course(s):** RDMA over Converged Ethernet (RoCE) from A to Z (NVIDIA Academy)  
> **Source docs:** Cumulus Linux User Guide — "RDMA over Converged Ethernet (RoCE)" (QoS chapter); MLNX_OFED/DOCA-OFED User Manual — RoCE, QoS and "RoCE Lossless/Lossy" sections; NVIDIA RoCE Configuration community guides (`mlnx_qos`, DCQCN); Spectrum-X Reference Architecture  
> **Estimated study time:** 6–8 hours

> **How to study this module:** read §1–§2, work through the §3 walkthrough on real gear or NVIDIA Air, then attempt the §5 hands-on assignment using only the Hint section. Open the Solution guide (last section) only after you have a working attempt. Finish with the Appendix C knowledge check closed-book.

## 1. Learning objectives

By the end of this module you will be able to…

- **Distinguish** RoCE v1 from RoCE v2 and lossless from lossy RoCE deployments. *(Spectrum Networking: RoCE)*
- **Explain** how **PFC** (802.1Qbb) achieves losslessness, what headroom is, and how PFC storms arise and are contained. *(Spectrum Networking: QoS)*
- **Explain** **ECN**-based congestion control with **DCQCN**: marking thresholds, CNP packets and RP/NP/CP roles. *(Spectrum Networking: congestion control)*
- **Configure** host-side RoCE QoS with `mlnx_qos`, `cma_roce_mode`, `cma_roce_tos`, ECN sysfs and `mlnx_tune`. *(Automation and Configuration; Troubleshooting Tools)*
- **Configure** switch-side RoCE on Cumulus Linux with NVUE (`nv set qos roce …`) and **describe** what the profile auto-configures. *(Spectrum Networking: Cumulus)*
- **Verify** RoCE operation with `ethtool -S`, `nv show interface … counters`, `mlnx_qos -i`, `rdma statistic` and perftest. *(Troubleshooting Tools)*
- **Troubleshoot** out-of-order delivery, retransmits, priority-3 discards and PFC storms. *(Troubleshooting Tools)*
- **Choose** correct MTU values for switch, host and RoCE path. *(Spectrum Networking)*

## 2. Key technical concepts

### 2.1 What RoCE is and why it is hard

**Analogy:** InfiniBand is a railway: trains only leave when the next segment is confirmed clear (credits), so collisions and drops are impossible. **RoCE** is running the same trains on a road network built for cars that are expected to bump into each other occasionally. Making that safe means installing traffic lights at every junction (PFC), speed-limit signs the drivers obey (ECN/DCQCN), and lane markings everyone agrees on (DSCP/TC).

**RoCE** carries the InfiniBand transport (BTH and payload, the exact packets from Modules 3.1/3.2) over Ethernet. The verbs API, QPs, MRs, SEND/WRITE/READ — all identical. What changes is the network underneath: Ethernet was built to drop packets under congestion and let TCP sort it out. The IB transport, by contrast, assumes a lossless link: a single lost packet triggers a go-back-N retransmission of everything after it (ConnectX HCAs implement selective repeat for some cases, but the default remains expensive), and bandwidth collapses by an order of magnitude at 0.1 % loss. So "configuring RoCE" really means "making Ethernet behave like InfiniBand" using DCB tools: **PFC**, **ECN**, **DSCP**/**PCP** classification, traffic classes, buffer management.

**RoCE v1 vs RoCE v2:**

| | RoCE v1 | RoCE v2 |
|---|---|---|
| Encapsulation | Ethernet header (EtherType **0x8915**) → IB GRH → BTH → payload → ICRC | Ethernet → IPv4/IPv6 → **UDP (dst port 4791)** → BTH → payload → ICRC |
| Addressing | GID derived from MAC (link-local); **L2 only, not routable** | GID = IP address; **routable**, works across L3 spine-leaf fabrics |
| ECMP / load balancing | By MAC only | UDP **source port** varies per QP/flow → hashable by ECMP on every spine |
| Congestion signalling | No standard ECN carrier (no IP header) | **ECN bits in the IP header** → DCQCN works |
| Status | Legacy; disabled on modern fabrics | The default on ConnectX-4 and later, and the only option for Spectrum-X |

Everything that follows assumes RoCE v2. Packet-capture tell: RoCE v2 shows up in Wireshark as `RRoCE`/`InfiniBand` over UDP 4791; RoCE v1 as EtherType 0x8915.

**Lossless vs lossy RoCE:**

| Model | Mechanism | Pros | Cons |
|---|---|---|---|
| **Lossless RoCE** | PFC on the RoCE priority end-to-end (host NIC ↔ switches) **plus** ECN/DCQCN to keep PFC rare | Zero drops; the IB transport's go-back-N never fires | PFC configuration on every hop, headroom math, risk of PFC storms/deadlocks, head-of-line blocking within the priority |
| **Lossy RoCE** | No PFC; rely on ECN/DCQCN (+ the NIC's improved loss recovery: selective repeat, adaptive retransmission on ConnectX-5+) and deep buffers to keep loss tiny | Simpler switch config; no PFC storm risk; multi-vendor friendly | Small loss still costs; requires modern HCAs and well-tuned ECN; not NVIDIA's recommendation for training fabrics |
| **Spectrum-X** | ECN/DCQCN-class congestion control plus adaptive routing with reordering handled in the BlueField-3 SuperNIC; PFC configured as backstop in lossless profile | Highest effective bandwidth on Ethernet | Requires Spectrum-4 + BlueField-3 end to end (Module 4.1) |

NVIDIA's guidance for AI training fabrics is lossless (PFC + ECN) with ECN doing almost all of the work and PFC acting as a safety net. Cumulus encodes this as the `nv set qos roce mode lossless` profile.

### 2.2 PFC — Priority-based Flow Control (802.1Qbb)

**Analogy:** classic 802.3x PAUSE is a red light for the whole road. **PFC** is a red light *per lane*: the receiver can stop lane 3 (RoCE) while lane 0 (management TCP) keeps flowing. Headroom is the stopping distance — the cars already past the light when it turned red still need somewhere to go.

A receiver whose buffer for priority *p* fills past a threshold (**XOFF**) sends a PFC frame telling its link partner "stop sending priority *p* for *t* quanta" and later releases it (**XON**) when the buffer drains. Only the RoCE priority (conventionally **priority 3**) is made lossless; TCP management traffic on priority 0 is still allowed to drop.

**Headroom.** Between the moment the receiver decides to send XOFF and the moment the last in-flight byte arrives, data keeps coming: the sender's reaction time, the serialization of a maximum-size frame already started, and **2× cable propagation delay** (XOFF travels one way, the final bytes travel the other). The receiver must reserve this **headroom** buffer *per lossless priority per port*, over and above the XOFF threshold, or it drops despite PFC. Headroom grows with link speed and cable length; this is why NVUE's RoCE profile asks for (or assumes) a cable length (`nv show qos roce` prints `cable-length`) and why 100 m fibre runs between leaf and spine need more headroom than 3 m DAC to servers. Spectrum ASICs compute headroom automatically from the configured cable length.

**PFC storms, deadlock and the watchdog.** If a receiver gets stuck (a hung NIC, a misbehaving driver, a failed buffer) it may hold XOFF asserted indefinitely. Its upstream switch port stops transmitting priority 3, its buffer fills, *it* sends PFC upstream — the pause propagates hop by hop across the fabric: a **PFC storm**, which can freeze every RoCE flow that shares any path with the victim. A related failure is **PFC deadlock**: a cyclic buffer dependency (possible when routing creates loops in a Clos during failures) in which every port waits on another. Counter-measures:

- **PFC watchdog** on the switch: if a port has been paused (receiving PFC XOFF) for longer than a threshold (hundreds of milliseconds), the switch declares a storm, temporarily *drops* traffic on that priority to break the chain, logs an event, and restores after a recovery interval. Cumulus: `nv set qos pfc-watchdog ...` / `nv set interface swpN qos pfc-watchdog enable` (exact syntax by release — verify in current docs); NVUE shows `pfc-watchdog` state in `nv show qos roce`.
- Keep PFC rare by making ECN react first, use adaptive routing / deadlock-free routing designs, and alert on `rx_prio3_pause`/`tx_prio3_pause` counters that never return to zero.

### 2.3 ECN and DCQCN — congestion control for RoCE

**Analogy:** PFC is the red light — blunt, late, and it backs traffic up behind it. **ECN** is the variable speed-limit sign on the motorway a few kilometres before the jam: the switch flips the sign (marks CE), the car at the destination radios the driver's dispatcher (CNP), and the dispatcher (the sending NIC) slows that one driver down. **DCQCN** is the rulebook for how much to slow and how fast to speed back up.

PFC stops loss but spreads congestion. **ECN** (Explicit Congestion Notification) gives the sender an early, per-flow signal so it slows down *before* buffers fill and PFC fires. The algorithm standardised across NVIDIA HCAs and switches is **DCQCN** (Data Center Quantized Congestion Notification).

| Role | Where | What it does |
|---|---|---|
| **CP — Congestion Point** | Switch egress queue | Monitors queue depth; marks the IP header ECN field to `11` (CE, Congestion Experienced) on packets with probability rising from 0 at `min-threshold` to `probability` (e.g. 100 %) at `max-threshold` (WRED-style), for ECN-capable packets (ECT bits set by the NIC) |
| **NP — Notification Point** | Receiving HCA | On receiving CE-marked RoCE packets, sends a **CNP** (Congestion Notification Packet — a small RoCE packet with BTH opcode CNP) back to the sender for that QP, rate-limited (e.g. at most one per N µs per flow) |
| **RP — Reaction Point** | Sending HCA | On each CNP, multiplicatively cuts the QP's rate (`alpha`-based reduction); in the absence of CNPs, increases rate additively then hyper-additively (fast recovery), à la DCTCP/QCN. Per-QP, in firmware |

**Why CNPs need their own priority.** CNPs must get back to the sender *fast* and *never* be stuck behind the very congestion they report. So CNPs are marked with a separate, high-priority DSCP and queued in a strict-priority traffic class — conventionally **DSCP 48 → priority/TC 6**. The data path is conventionally **DSCP 26 → priority/TC 3**. These are *common defaults* used by NVIDIA (and the NVUE RoCE profile); other vendors and some deployments use DSCP 24 for data, so always verify both ends match.

**Threshold intuition.** `min-threshold` low enough that marking starts well before the PFC XOFF point; `max-threshold` below XOFF so ECN always acts first. Too aggressive marking → under-utilisation (senders back off needlessly); too lax → PFC storms do the work instead. Spectrum defaults (NVUE lossless: roughly 150 KB min / 1.5 MB max on TC3 with 100 % probability — the exact numbers vary per ASIC and release) are tuned for 25–400G AI/storage traffic; change only with measurement. DCQCN parameters on the NIC (`rate_reduce_monitor_period`, `dce_tcp_g`, `initial_alpha_value`, `rpg_*`) are exposed under `/sys/class/net/<if>/ecn/roce_rp/` and `roce_np/`; defaults are fine for most deployments.

**Why the exam cares:** who marks (CP = switch), who sends CNPs (NP = receiver), who slows down (RP = sender); and the DSCP 26/TC3 data, DSCP 48/TC6 CNP convention.

### 2.4 Host-side configuration

**mlnx_qos** configures the NIC's DCB state: whether to classify on **DSCP** or **PCP**, which priorities are PFC-enabled, the priority→traffic-class map and the ETS scheduler. Trust DSCP (not PCP) for routed RoCE v2 — PCP lives in the VLAN tag, which is lost at L3 hops and absent on untagged frames. Settings are not persistent across reboots; put them in a systemd unit, `/etc/network/interfaces` post-up, or the DOCA/OFED `roce_config` hook. Interface names are the netdev (not `mlx5_0`); use `ibdev2netdev` to map.

**RoCE version and ToS for RDMA CM.** Applications that use RDMA CM (NCCL does not — it sets `traffic_class` itself; NVMe-oF, UCX-over-CM, rping do) take the RoCE version and the ToS/DSCP from per-device defaults set with `cma_roce_mode` and `cma_roce_tos` (both write under `/sys/kernel/config/rdma_cm/<dev>/ports/<p>/default_roce_mode|default_roce_tos`). **ToS 106 = (DSCP 26 << 2) | ECT(0) = 104 + 2**: the DSCP shifted left two bits, plus the ECN-capable-transport bit so switches may mark the packet. NCCL equivalents: `NCCL_IB_GID_INDEX=<v2 index>` (or auto in recent versions), `NCCL_IB_TC=106`; perftest: `-x <gid>` and `--tclass=106`. `NCCL_IB_SL` is ignored on RoCE. Raw-verbs applications pick the version via the GID index and set `traffic_class` themselves (Module 3.2). Legacy ConnectX-3 only: `set_roce_mode` / `ibv_exp_*` — obsolete on mlx5 devices.

**ECN on the host.** ECN marking *response* must be enabled on the NIC per priority for both roles under `/sys/class/net/<if>/ecn/roce_np/enable/<prio>` and `roce_rp/enable/<prio>`; `roce_np/cnp_dscp` (default 48) and `roce_np/cnp_802p_prio` (default 6) set how CNPs are marked. On current MLNX_OFED/DOCA releases ECN is enabled by default on all priorities for mlx5; verify rather than assume. Older docs reference `mlnx_qos --dcbx` and `mlx5_core` module parameters; prefer the sysfs interface.

**mlnx_tune** applies a tuning profile (`HIGH_THROUGHPUT`, `LOW_LATENCY_VMA`, …) adjusting CPU governor, IRQ affinity, ring sizes and relevant sysctls; `mlnx_tune -r` reports the current state and warns on PCIe width/speed, NUMA mismatch, etc. For AI hosts also check **mlxconfig** `ADVANCED_PCI_SETTINGS`/`MAX_ACC_OUT_READ` (RDMA READ depth on PCIe Gen5), CPU C-states, IOMMU passthrough, and that the RoCE netdev MTU is 9000.

### 2.5 Switch-side configuration: Cumulus Linux with NVUE

On Spectrum switches running Cumulus Linux, NVIDIA collapsed the entire DCB recipe into one profile. Use **NVUE** (`nv`) syntax; legacy NCLU (`net`) is deprecated and cannot configure the RoCE profile at all.

What `nv set qos roce mode lossless` auto-configures (verified against Cumulus Linux 5.x documentation; details per release):

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

Variants: `mode lossy` (ECN on TC3, DSCP/TC mapping, CNP TC6 — but **no PFC**, SP3 stays in the lossy pool), `mode lossless-single-ipool` (one shared ingress pool; release-dependent), `mode lossy-multi-tc` (lossy with packet trimming across multiple TCs; Spectrum-4, newer releases), `nv unset qos roce` to remove the profile. Caveat from the docs: if the switch is already in `lossy`, running plain `nv set qos roce` does *not* switch it back — you must specify `mode lossless` explicitly. The RoCE profile is per switch, not per port; port-level overrides are done with standard `nv set qos ...` objects (e.g. different `cable-length` on uplinks; verify exact syntax).

### 2.6 Spectrum-X preview

Spectrum-X (Spectrum-4 switches + BlueField-3 SuperNICs, Module 4.1) uses the same building blocks but moves beyond DCQCN-on-ECMP: the switch performs per-packet **adaptive routing** across spine uplinks (like IB AR) and the SuperNIC reorders packets using a "Direct Data Placement" mechanism; congestion control is telemetry-assisted (switch-generated congestion telemetry to the SuperNIC rather than only ECN marks), and PFC remains configured on priority 3 as a backstop. NVUE on Spectrum-X exposes this as the RoCE profile plus `nv set router adaptive-routing` / `nv set qos roce` extensions — covered in [Module 4.1](../04-spectrum-ethernet/01-spectrum-x.md). Everything in this module (DSCP 26/48, TC3/TC6, PFC prio 3, ECN) is the foundation Spectrum-X builds on.

### 2.7 MTU: switch, host and RoCE

| Layer | Recommended | Why |
|---|---|---|
| Switch port MTU (Cumulus) | **9216** (`nv set interface swp1-64 link mtu 9216`) | Default on Cumulus; must be ≥ host MTU + any encapsulation (VXLAN adds 50) |
| Host netdev MTU | **9000** (`ip link set ens1f0np0 mtu 9000`) | Jumbo for TCP/storage; leaves room on the switch |
| RoCE path MTU (`active_mtu`) | **4096** | IB MTU values are powers of two up to 4096; 4096 + RoCE/UDP/IP/Ethernet headers (~4150 bytes) fits in 9000 but **not** in 1500 (which caps RoCE at 1024) |

So "RoCE uses 4K MTU even on 9000-byte Ethernet" is expected, not a problem. The exam trap is the reverse: an Ethernet MTU of 1500 anywhere on the path (a mis-set host, a VLAN sub-interface, a VXLAN tunnel) forces `active_mtu` down or, worse, causes silent drops when the two ends negotiated 4096 but a middle hop cannot carry it (IB transport has no path-MTU discovery). Always set MTU consistently, then confirm with `ibv_devinfo | grep mtu` on both hosts and `nv show interface swp1 link mtu` on each switch.

## 3. Live-style walkthrough: lossless RoCE end to end, then prove it under load

Lab: `hostA` (10.10.1.20, `mlx5_2` ↔ `ens1f0np0`) on `leaf01 swp1`, `hostB` (10.10.1.21) on `leaf02 swp1`, Spectrum-3 leaves running Cumulus 5.x, a routed spine between them. Nothing QoS-related is configured yet.

### Step 1 — Host: map the device, fix MTU

```bash
hostA$ ibdev2netdev -v                                   # which netdev owns the RDMA device? mlnx_qos needs the netdev name
0000:4b:00.0 mlx5_2 (MT4129 - ...) fw 28.41.1000 port 1 (ACTIVE) ==> ens1f0np0 (Up)
hostA$ ip link set ens1f0np0 mtu 9000                    # 9000 on hosts; leaves ~216 bytes for the switch's 9216
hostA$ ibv_devinfo -d mlx5_2 | grep -E "active_mtu|link_layer"
                active_mtu:             4096 (5)         # <-- was 1024 (3) at MTU 1500. 4096 is the RoCE maximum; this is correct
                link_layer:             Ethernet
```

### Step 2 — Host: trust DSCP, PFC on priority 3, verify with mlnx_qos -i

```bash
hostA$ mlnx_qos -i ens1f0np0 --trust dscp                # classify on the L3 DSCP field, not the VLAN PCP — mandatory for routed RoCE v2
hostA$ mlnx_qos -i ens1f0np0 --pfc 0,0,0,1,0,0,0,0       # 8 fields = priorities 0..7; enable PFC ONLY on priority 3
hostA$ mlnx_qos -i ens1f0np0 --prio_tc 0,0,0,3,0,0,6,0   # optional: priority->TC map; prio 3 -> TC3, prio 6 -> TC6 (CNPs)
hostA$ mlnx_qos -i ens1f0np0 --tsa vendor,vendor,vendor,ets,vendor,vendor,strict,vendor --tcbw 0,0,0,100,0,0,0,0   # TC6 strict, TC3 ETS (verify syntax per release)
hostA$ mlnx_qos -i ens1f0np0 --dscp2prio set,26,3        # DSCP 26 -> priority 3 (default already maps DSCP 24-31 -> prio 3)
hostA$ mlnx_qos -i ens1f0np0 --dscp2prio set,48,6        # DSCP 48 -> priority 6 (default already maps 48-55 -> prio 6)

hostA$ mlnx_qos -i ens1f0np0                             # read back the whole DCB state
DCBX mode: OS controlled
Priority trust state: dscp                               # <-- trust dscp
dscp2prio mapping:
        prio:0 dscp:07,06,05,04,03,02,01,00,
        prio:3 dscp:31,30,29,28,27,26,25,24,              # <-- 26 lands in prio 3
        prio:6 dscp:55,54,53,52,51,50,49,48,              # <-- 48 lands in prio 6
Receive buffer size (bytes): ...
PFC configuration:
        priority    0   1   2   3   4   5   6   7
        enabled     0   0   0   1   0   0   0   0        # <-- PFC only on 3
tc: 3 ratelimit: unlimited, tsa: ets, bw: 100%
         priority:  3
tc: 6 ratelimit: unlimited, tsa: strict
         priority:  6
# Repeat on hostB. These settings do NOT survive a reboot — wrap them in a systemd oneshot unit.
```

### Step 3 — Host: RoCE version, ToS for CM apps, ECN roles, GID index

```bash
hostA$ cma_roce_mode -d mlx5_2 -p 1                      # default RoCE version RDMA CM will use
RoCE v2
hostA$ cma_roce_mode -d mlx5_2 -p 1 -m 2                 # force v2 (1 = v1) if it said v1
hostA$ cma_roce_tos  -d mlx5_2 -p 1 -t 106               # ToS 106 = (DSCP 26 << 2) | ECT(0) = 104 + 2 -> CM apps (rping, NVMe-oF) ride priority 3 and are ECN-capable
hostA$ cma_roce_tos  -d mlx5_2 -p 1
106

IF=ens1f0np0
hostA$ cat /sys/class/net/$IF/ecn/roce_np/enable/3       # NP role: on seeing CE marks for prio 3, send CNPs
1
hostA$ cat /sys/class/net/$IF/ecn/roce_rp/enable/3       # RP role: on receiving CNPs for prio 3, reduce rate
1
hostA$ echo 1 > /sys/class/net/$IF/ecn/roce_np/enable/3  # (if either printed 0)
hostA$ echo 1 > /sys/class/net/$IF/ecn/roce_rp/enable/3
hostA$ cat /sys/class/net/$IF/ecn/roce_np/cnp_dscp       # DSCP used for the CNPs we SEND
48
hostA$ cat /sys/class/net/$IF/ecn/roce_np/cnp_802p_prio  # PCP used for CNPs on tagged links
6
hostA$ ls /sys/class/net/$IF/ecn/roce_rp/                # the DCQCN knobs: rpg_time_reset, rpg_byte_reset, clamp_tgt_rate, dce_tcp_g, ... leave at defaults

hostA$ show_gids | grep mlx5_2
mlx5_2  1  0  fe80:0000:0000:0000:b83f:d2ff:fe00:1122               v1  ens1f0np0
mlx5_2  1  1  fe80:0000:0000:0000:b83f:d2ff:fe00:1122               v2  ens1f0np0
mlx5_2  1  2  0000:0000:0000:0000:0000:ffff:0a0a:0114  10.10.1.20   v1  ens1f0np0
mlx5_2  1  3  0000:0000:0000:0000:0000:ffff:0a0a:0114  10.10.1.20   v2  ens1f0np0   # <-- index 3: IPv4 + v2. Use this for -x / NCCL_IB_GID_INDEX
hostA$ mlnx_tune -r | grep -iE "pcie|numa|governor"      # sanity: Gen5 x16, HCA and CPU on the same NUMA node, performance governor
```

### Step 4 — Switch: apply the lossless RoCE profile with NVUE

```bash
cumulus@leaf01:~$ nv set interface swp1-32 link mtu 9216  # host-facing and uplink ports: 9216 > host 9000
cumulus@leaf01:~$ nv set qos roce mode lossless           # THE one-liner: trust DSCP, 26->SP3->TC3, 48->SP6->TC6 strict, PFC on SP3, ECN on TC3, lossless pool, LLDP App TLV
cumulus@leaf01:~$ nv config apply                         # push to switchd
cumulus@leaf01:~$ nv config save                          # persist across reboot
# Repeat on leaf02 and on every spine the RoCE traffic crosses — PFC/ECN must be configured on EVERY hop.
```

### Step 5 — Switch: read back what the profile did

```bash
cumulus@leaf01:~$ nv show qos roce
                    operational  applied
------------------  -----------  --------
enable              on           on
mode                lossless     lossless
cable-length        100          100       # metres assumed for headroom — lower it for 3 m DAC to save buffer, raise for long fibre
congestion-control
  congestion-mode   ECN                    # ECN marking...
  enabled-tc        0,3                    # ...on TC3 (and TC0 depending on release)
  max-threshold     1.50 MB                # marking probability reaches 100% here — well below the PFC XOFF point
  min-threshold     150.00 KB              # marking starts here
  probability       100
pfc
  pfc-priority      3                      # <-- PFC only on SP3
  rx-enabled        enabled
  tx-enabled        enabled
trust
  trust-mode        pcp,dscp               # DSCP honoured (and PCP for tagged frames)
mode  lossless
  switch-priority   3                      # SP3 in the lossless pool
  tc                3
...
RoCE PCP/DSCP->SP mapping configurations
  dscp  26  switch-priority 3             # data
  dscp  48  switch-priority 6             # CNP
RoCE SP->TC mapping and ETS configurations
  switch-priority 3  traffic-class 3  scheduler-weight DWRR-50%
  switch-priority 6  traffic-class 6  scheduler-weight strict     # <-- CNPs never wait behind data
cumulus@leaf01:~$ nv show interface swp1 qos roce status  # per-port view: pool membership, PFC state, TC mapping on THIS port
cumulus@leaf01:~$ nv show interface swp1 qos pfc          # pfc per priority on the port
cumulus@leaf01:~$ nv show interface swp1 qos congestion-control   # ECN per TC on the port
```

### Step 6 — perftest with -x and --tclass, while watching PFC/ECN counters

```bash
# Clear counters first so what we see is from this test
cumulus@leaf01:~$ nv action clear interface swp1 qos roce counters
hostA$ # (host counters are cumulative; note the starting values)

# Server on hostB, client on hostA. -x 3 = v2 IPv4 GID; --tclass=106 = DSCP 26 + ECT so the data rides priority 3 and can be ECN-marked
hostB$ ib_write_bw -d mlx5_2 -i 1 -x 3 --tclass=106 -q 4 -F --report_gbits -D 30
hostA$ ib_write_bw -d mlx5_2 -i 1 -x 3 --tclass=106 -q 4 -F --report_gbits -D 30 10.10.1.21
 #bytes  #iterations   BW peak[Gb/sec]   BW average[Gb/sec]   MsgRate[Mpps]
 65536   ...            391.6              390.9                0.745        # <-- ~390 of 400 on a clean, uncongested path

# In a second terminal on hostA, while it runs:
hostA$ watch -n1 'ethtool -S ens1f0np0 | grep -E "prio3_(pause|discards|bytes)"; rdma statistic show link mlx5_2/1 | tr " " "\n" | grep -E "cnp|out_of_seq|ecn"'
     rx_prio3_bytes: 1203391827393        # <-- climbing: the RoCE traffic really is classified into priority 3 (DSCP 26 honoured)
     rx_prio3_pause: 0                    # PFC frames received on prio 3: zero on a point-to-point test is CORRECT
     tx_prio3_pause: 0
     rx_prio3_pause_duration: 0
     rx_prio3_discards: 0                 # <-- MUST be zero on a lossless priority. Non-zero = PFC not actually protecting this hop
np_cnp_sent 0                             # we saw no CE marks (no congestion)
rp_cnp_handled 0                          # nobody told us to slow down
np_ecn_marked_roce_packets 0
out_of_sequence 0                         # no loss, no reordering

cumulus@leaf01:~$ nv show interface swp1 qos roce counters
                      rx                tx
---------------------  ----------------  ----------------
roce (bytes/packets)   1203391827393/... 48920311/...      # data in on swp1, ACKs out
non-roce               ...
pfc pause frames       0                 0                 # none sent, none received
cnp                                      0
ecn-marked                               0
# "Good": roce counters moving, everything else zero. Now make congestion on purpose: run a SECOND client from hostC to hostB
# so two 400G senders share hostB's single 400G link (2:1 incast).
hostC$ ib_write_bw -d mlx5_2 -i 1 -x 3 --tclass=106 -q 4 -F --report_gbits -D 30 10.10.1.21
# Watch leaf02 swp1 (the port facing hostB) and hostB:
cumulus@leaf02:~$ nv show interface swp1 qos roce counters
ecn-marked                               1849203          # <-- CP: the egress queue to hostB crossed min-threshold; packets marked CE
cnp                    912                                # CNPs flowing back through this port
pfc pause frames       0                 37               # a FEW PFC frames: ECN did the heavy lifting, PFC was the backstop. Healthy.
hostB$ rdma statistic show link mlx5_2/1 | tr ' ' '\n' | grep -E "cnp|ecn"
np_ecn_marked_roce_packets 1849203        # NP: hostB received the CE-marked packets...
np_cnp_sent 912                           # ...and sent CNPs (rate-limited, so far fewer than marks)
hostA$ rdma statistic show link mlx5_2/1 | tr ' ' '\n' | grep rp_cnp
rp_cnp_handled 455                        # RP: hostA (one of the two senders) received its share and cut rate
# Both clients now report ~195 Gb/s each: DCQCN split the link fairly. rx_prio3_discards on hostB stays 0: lossless held.
```

## 4. Troubleshooting / common gotchas

### 4.1 `rx_prio3_discards` climbing on the host while the switch shows no PFC frames

**Symptom:** the host NIC counts discards on priority 3; `nv show interface swp1 qos roce counters` shows `pfc pause frames 0/0`; perftest "works" but `out_of_sequence` and `packet_seq_err` rise and bandwidth sags.

**Why it happens:** the switch is not actually protecting priority 3 on this hop: the RoCE profile was not applied on *this* switch (or is in `lossy`), trust is PCP on one side and DSCP on the other, or the application is not marking DSCP 26 so its traffic is in priority 0 on the switch but priority 3 on the host — a classification mismatch.

**How to debug:**

```bash
cumulus@leaf01:~$ nv show qos roce | grep -E "mode|pfc-priority|trust"      # lossless? pfc-priority 3? trust includes dscp?
hostA$ mlnx_qos -i ens1f0np0 | grep -A2 "trust\|PFC"                         # dscp and 0 0 0 1 0 0 0 0?
hostA$ ethtool -S ens1f0np0 | grep -E "rx_prio[0-9]_bytes"                  # is RoCE in prio3 or leaking into prio0?
hostA$ sudo tcpdump -ni ens1f0np0 -c 5 -vv udp port 4791 | grep -o "tos 0x[0-9a-f]*"   # tos 0x6a = 106 = DSCP 26 + ECT. 0x0 = unmarked
```

**Fix:** `nv set qos roce mode lossless` + `nv config apply` on every hop; `mlnx_qos --trust dscp --pfc 0,0,0,1,0,0,0,0` on the hosts; mark the traffic (`--tclass=106`, `NCCL_IB_TC=106`, `cma_roce_tos -t 106`).

### 4.2 Everything freezes for seconds at a time; `tx_prio3_pause_duration` huge on many ports

**Symptom:** several hosts stall simultaneously; `ethtool -S` shows `rx_prio3_pause_duration` growing continuously on many switch-facing ports; NCCL jobs time out.

**Why it happens:** a **PFC storm**. One receiver (hung NIC, driver fault, a misbehaving VF) holds XOFF asserted; its leaf port fills and pauses the spine, the spine pauses every leaf, and the pause spreads to unrelated hosts that merely share a path.

**How to debug:**

```bash
cumulus@spine01:~$ nv show interface qos-roce-counters | sort -k4 -n | tail  # which ports are RECEIVING the most PFC? follow them downstream
cumulus@leaf02:~$ nv show interface swp7 qos roce counters                   # the leaf port facing the stuck host shows rx pfc pause climbing, tx roce ~0
cumulus@leaf02:~$ nv show qos roce | grep -i watchdog                        # is the pfc-watchdog enabled? did it fire (check /var/log/switchd.log)?
hostX$ dmesg | grep -i mlx5 | tail ; ethtool -S ens1f0np0 | grep tx_prio3_pause   # the culprit host sends pause but receives nothing
```

**Fix:** disable the culprit port (`nv set interface swp7 link state down`) to break the chain; enable the PFC watchdog on all RoCE ports (`nv set interface swp1-32 qos pfc-watchdog enable` — verify syntax); reset/replace the stuck NIC; alert on `*_prio3_pause_duration` that never returns to zero.

### 4.3 `out_of_sequence` rising, bandwidth fine single-flow but collapsing at scale, no discards anywhere

**Symptom:** host counters show `out_of_sequence` increments but `rx_prio3_discards` is zero on every hop and the switch shows no drops; multi-flow NCCL bandwidth is well below line rate.

**Why it happens:** *reordering*, not loss. Either per-packet spraying/ECMP changes are reordering packets of one QP on a NIC that cannot handle it, or (more often) several QPs hash onto the same spine uplink (ECMP collision — the Ethernet version of IB static-routing collisions) while others sit idle, producing retransmits and stalls.

**How to debug:**

```bash
hostA$ rdma statistic show link mlx5_2/1 | tr ' ' '\n' | grep -E "out_of_sequence|packet_seq_err|rx_icrc"   # OOO without seq_err/ICRC = reordering, not loss
cumulus@spine01:~$ nv show interface --view=counters | grep -E "swp(49|50|51|52)"   # uplink utilisation imbalance?
cumulus@leaf01:~$ nv show router ecmp                                               # hash fields: UDP source port must be included for RoCE v2 spreading
```

**Fix:** keep flow-based ECMP hashing (include L4 ports) on non-Spectrum-X fabrics and spread load with `NCCL_IB_QPS_PER_CONNECTION` (more QPs → more source ports → better hash spread); on Spectrum-X enable adaptive routing with BlueField-3 reordering. Never "fix" OOO by disabling ECN.

## 5. Hands-on assignment: convert a lossy RoCE fabric to lossless and prove it

**Scenario.** A customer's 32-node RoCE cluster was deployed "lossy": `nv set qos roce mode lossy` on the Spectrum switches, no PFC on the hosts, applications unmarked. NCCL jobs show retransmits and 30 % lower bus bandwidth than the vendor sizing. Convert the fabric to lossless RoCE with DCQCN, end to end, and prove under incast load that there is no loss and no PFC storm.

**Requirements:**

1. Hosts: trust DSCP, PFC on priority 3 only, ECN NP/RP enabled on priority 3, CNP DSCP 48, data marked DSCP 26 for both CM applications (`cma_roce_tos`) and NCCL (`NCCL_IB_TC`); configuration persistent across reboot (systemd unit).
2. Switches: every leaf and spine on `nv set qos roce mode lossless` with a cable-length appropriate to each port class, MTU 9216; PFC watchdog enabled on all RoCE ports.
3. MTU trio verified: switch 9216, host 9000, `active_mtu` 4096 on every host.
4. A reproducible incast test (3 senders → 1 receiver with `ib_write_bw`) that generates ECN marks and CNPs.
5. Under that test: zero `rx_prio3_discards` on every host and zero TC3 drops on every switch port, CNPs observed, and PFC frames either zero or a small, non-growing count (no storm).
6. A before/after table (lossy vs lossless) of: aggregate throughput, `out_of_sequence`, `np_cnp_sent`, `rp_cnp_handled`, `rx_prio3_discards`, PFC frame counts.

**Deliverables:** the host systemd unit, the NVUE command set (or `nv config show` diff), the test script, and the before/after table with a one-paragraph interpretation.

**Acceptance criteria:**

- A. `mlnx_qos -i` on every host shows `trust dscp`, PFC `0 0 0 1 0 0 0 0`; `nv show qos roce` on every switch shows `mode lossless`, `pfc-priority 3`, ECN on TC3.
- B. `tcpdump` of the test traffic shows `tos 0x6a` (DSCP 26, ECT) on data packets and CNPs with DSCP 48.
- C. During the incast test `np_cnp_sent` on the receiver and `rp_cnp_handled` on each sender increase; `nv show interface <rx-port> qos roce counters` shows `ecn-marked > 0`.
- D. `rx_prio3_discards` is 0 on all four hosts and `nv show interface <port> counters` shows no TC3 drops on the receiver's leaf port, for the entire test.
- E. PFC pause frame counters on the receiver's leaf port are ≤ a few hundred and stop increasing within seconds of the test ending; the watchdog never fires (`/var/log/switchd.log`).

### Hint

- The conversion is exactly §3 Steps 1–5 applied to all hosts and all switches; the hard part is "every hop" and "persistent". Which §2.5 caveat about `nv set qos roce` bites when a switch is already in `lossy`?
- Headroom: how does cable length differ between the 3 m host DACs and the 50–100 m leaf-spine fibres, and which NVUE object carries it?
- For the "before" column, run the identical incast test on the lossy fabric first — do not skip it, the comparison is the deliverable.
- `watch -n1` on `ethtool -S | grep prio3` and `rdma statistic show` while the test runs; capture the final values with a script, not by eye.
- For criterion B, `tcpdump -vv udp port 4791` prints `tos`; CNPs are RoCE packets too — filter on the BTH opcode or just on DSCP 48 (`ip[1] & 0xfc == 0xc0`).

## Appendix A — Quick command / config reference

| Task | Host (mlx5) | Switch (Cumulus NVUE) |
|---|---|---|
| Lossless RoCE baseline | `mlnx_qos -i $IF --trust dscp --pfc 0,0,0,1,0,0,0,0` | `nv set qos roce mode lossless; nv config apply; nv config save` |
| Lossy | `mlnx_qos -i $IF --trust dscp --pfc 0,0,0,0,0,0,0,0` (ECN only) | `nv set qos roce mode lossy` |
| Remove profile | — | `nv unset qos roce` |
| Prio → TC / ETS | `mlnx_qos -i $IF --prio_tc 0,0,0,3,0,0,6,0 --tsa ... --tcbw ...` (verify syntax) | part of profile |
| RoCE version (CM apps) | `cma_roce_mode -d mlx5_2 -p 1 [-m 2]` | n/a |
| DSCP for data | `cma_roce_tos -d mlx5_2 -p 1 -t 106`; `NCCL_IB_TC=106`; perftest `--tclass=106` | DSCP 26 → SP3 (profile default) |
| DSCP → prio on NIC | `mlnx_qos -i $IF --dscp2prio set,26,3` / `set,48,6` | `nv show qos roce` mapping table |
| CNP DSCP / PCP | `/sys/class/net/$IF/ecn/roce_np/cnp_dscp` (48), `cnp_802p_prio` (6) | DSCP 48 → SP6 strict (profile default) |
| ECN on/off | `echo 1 > /sys/class/net/$IF/ecn/roce_{np,rp}/enable/3` | part of profile; `nv show qos roce` → congestion-control |
| PFC watchdog | n/a | `nv set interface swpN qos pfc-watchdog enable` (verify syntax) |
| MTU | `ip link set $IF mtu 9000` | `nv set interface swp1-64 link mtu 9216` |
| Show QoS state | `mlnx_qos -i $IF` | `nv show qos roce`, `nv show interface swp1 qos roce status`, `nv show interface swp1 qos pfc`, `nv show interface swp1 qos congestion-control` |
| Counters | `ethtool -S $IF \| grep -E "prio3\|ecn\|cnp"`, `rdma statistic show link mlx5_2/1`, `/sys/class/infiniband/mlx5_2/ports/1/hw_counters/` | `nv show interface swp1 qos roce counters`, `nv show interface qos-roce-counters`, `nv show interface swp1 counters`, `nv action clear interface swp1 qos roce counters` |
| GID / version | `show_gids`, `ibv_devinfo` | n/a |
| Tuning | `mlnx_tune -p HIGH_THROUGHPUT`, `mlnx_tune -r` | buffers via `nv set qos traffic-pool …` |
| Test | `ib_write_bw -d mlx5_2 -i 1 -x <gid> --tclass=106 -q 4 -F --report_gbits <ip>` | watch `qos roce counters` |
| Drop reasons | — | **WJH** (Module 8); `ethtool -S swpN` on the switch |

Host counter meanings: `rx/tx_prio3_pause` (PFC frames), `*_prio3_pause_duration` (µs paused; growing = congestion/storm), `rx_prio3_discards` (drops on the lossless priority = PFC ineffective), `rx_prio3_bytes` (RoCE really in prio 3); `np_cnp_sent`, `rp_cnp_handled`, `np_ecn_marked_roce_packets`, `out_of_sequence`, `packet_seq_err`, `local_ack_timeout_err`, `rnr_nak_retry_err`, `implied_nak_seq_err`, `duplicate_request`, `rx_icrc_encapsulated` (corrupted RoCE packets = bad cable/port).

## Appendix B — Exam traps & key takeaways

- **RoCE v1 is L2-only (EtherType 0x8915); RoCE v2 is UDP/4791 over IP and routable** and the only one that supports ECN.
- **PFC prevents loss; ECN/DCQCN prevents congestion.** You want ECN to fire *first* and PFC to be a rarely-used backstop; thresholds are set accordingly.
- **CNP = Congestion Notification Packet**, generated by the *receiver* (NP) and acted on by the *sender* (RP); the switch (CP) only marks ECN.
- **Common defaults: data DSCP 26 → priority/TC 3; CNP DSCP 48 → priority/TC 6 (strict).** Conventions, not laws — mismatched DSCP between host and switch is the #1 RoCE misconfiguration.
- **ToS 106 = DSCP 26 << 2 + ECT(0)** — `NCCL_IB_TC=106`, `cma_roce_tos -t 106`, perftest `--tclass=106`.
- **Trust DSCP, not PCP, for routed RoCE v2**; PCP is lost at L3 hops and untagged frames have none.
- `rx_prio3_discards` on a "lossless" priority means PFC is not actually working on that hop.
- **Headroom** depends on cable length and speed; PFC without correct headroom still drops.
- **PFC storms** propagate hop-by-hop; the **PFC watchdog** breaks them by temporarily dropping.
- **NVUE RoCE profile is switch-wide**; `nv set qos roce` alone will not move a switch out of `lossy` — specify `mode lossless`. NCLU cannot configure it. `mode lossy` = everything except PFC and the lossless pool.
- **MTU trio: switch 9216, host 9000, RoCE path 4096.** 1500 anywhere caps RoCE at 1024 or causes silent drops.
- perftest on RoCE needs `-x <v2 GID index>`; wrong index = connect failure, not a slow run.
- `out_of_sequence` can be loss *or* reordering; correlate with discard counters before blaming the fabric design.
- Spectrum-X builds on the same foundation (DSCP 26/48, TC3/TC6, PFC prio 3, ECN) and adds adaptive routing + SuperNIC reordering.

## Appendix C — Knowledge check

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

---
## Solution guide — Hands-on assignment

Lab for the solution: senders `hostA`, `hostC`, `hostD` on `leaf01`; receiver `hostB` on `leaf02 swp1`; two spines. All hosts `mlx5_2` ↔ `ens1f0np0`.

### Step 0 — "Before" measurement on the lossy fabric (requirement 6)

```bash
# Capture baseline counters, run the incast, capture again. Same script is reused for the "after" run.
cat > incast.sh <<'EOF'
#!/usr/bin/env bash
# incast.sh <receiver-ip> <sender...>  — 3:1 incast with ib_write_bw, 30 s, DSCP 26, v2 GID index 3
set -euo pipefail
RX=$1; shift
snap() { ssh "$1" "ethtool -S ens1f0np0 | grep -E 'prio3_(pause|discards)|prio3_pause_duration' | tr -d ' '; rdma statistic show link mlx5_2/1 | tr ' ' '\n' | grep -E 'cnp|out_of_seq|packet_seq' | paste - - " ; }
for h in "$RX" "$@"; do echo "== before $h"; snap "$h"; done > counters.before
for i in 0 1 2; do ssh "$RX" "ib_write_bw -d mlx5_2 -i 1 -x 3 --tclass=106 -q 4 -F --report_gbits -D 30 -p $((18600+i)) > /tmp/rx_$i.log 2>&1" & done
sleep 1
i=0; for s in "$@"; do ssh "$s" "ib_write_bw -d mlx5_2 -i 1 -x 3 --tclass=106 -q 4 -F --report_gbits -D 30 -p $((18600+i)) $RX | tail -2 | head -1" & i=$((i+1)); done
wait
for h in "$RX" "$@"; do echo "== after $h"; snap "$h"; done > counters.after
EOF
chmod +x incast.sh
./incast.sh 10.10.1.21 hostA hostC hostD | tee before_throughput.txt
# Lossy result (typical): per-sender avg ~110-125 Gb/s (aggregate ~350 Gb/s, not 390), out_of_sequence and packet_seq_err
# climbing on senders, rx_prio3_discards 0 (nothing is IN prio 3 — traffic is unmarked/untrusted), switch TC0 drops present.
```

### Step 1 — Hosts (requirement 1): persistent configuration

```bash
# /etc/systemd/system/roce-qos@.service   — instantiate per netdev: systemctl enable --now roce-qos@ens1f0np0
sudo tee /etc/systemd/system/roce-qos@.service <<'EOF'
[Unit]
Description=Lossless RoCE QoS for %i
After=network-online.target openibd.service
Wants=network-online.target

[Service]
Type=oneshot
# %i is the netdev; derive the RDMA device from it
ExecStart=/bin/bash -c '\
  IF=%i; DEV=$(ibdev2netdev | awk -v i=$IF "\\$NF==\"(Up)\" && \\$(NF-1)==i {print \\$2}"); \
  ip link set $IF mtu 9000; \
  mlnx_qos -i $IF --trust dscp; \
  mlnx_qos -i $IF --pfc 0,0,0,1,0,0,0,0; \
  mlnx_qos -i $IF --dscp2prio set,26,3; \
  mlnx_qos -i $IF --dscp2prio set,48,6; \
  echo 1 > /sys/class/net/$IF/ecn/roce_np/enable/3; \
  echo 1 > /sys/class/net/$IF/ecn/roce_rp/enable/3; \
  echo 48 > /sys/class/net/$IF/ecn/roce_np/cnp_dscp; \
  cma_roce_mode -d $DEV -p 1 -m 2; \
  cma_roce_tos  -d $DEV -p 1 -t 106'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now roce-qos@ens1f0np0
# NCCL side (cluster-wide environment / Slurm prolog):
echo 'NCCL_IB_TC=106'          | sudo tee -a /etc/environment      # DSCP 26 + ECT for NCCL's own QPs
echo 'NCCL_IB_GID_INDEX=3'     | sudo tee -a /etc/environment      # v2 IPv4 GID (or leave unset on NCCL >= 2.12 and verify auto-detect)
# Verify (criterion A, host half):
mlnx_qos -i ens1f0np0 | grep -E "trust state|enabled"
Priority trust state: dscp
        enabled     0   0   0   1   0   0   0   0
cat /sys/class/net/ens1f0np0/ecn/roce_{np,rp}/enable/3        # 1 and 1
cma_roce_tos -d mlx5_2 -p 1                                    # 106
sudo reboot && mlnx_qos -i ens1f0np0 | grep enabled            # survives reboot
```

### Step 2 — Switches (requirement 2): every leaf and spine

```bash
# On each leaf (host ports swp1-32 = 3 m DAC, uplinks swp49-56 = 100 m fibre) and each spine:
nv set interface swp1-56 link mtu 9216
nv set qos roce mode lossless                 # EXPLICIT mode: the switch is currently in lossy, and plain 'nv set qos roce' would NOT flip it
nv set qos roce cable-length 100              # headroom for the longest link class; alternatively per-port overrides (verify syntax):
# nv set interface swp1-32  qos pfc cable-length 3
# nv set interface swp49-56 qos pfc cable-length 100
nv set interface swp1-56 qos pfc-watchdog enable      # (verify exact object per release) — breaks a storm instead of propagating it
nv config apply ; nv config save
# Verify (criterion A, switch half):
nv show qos roce | grep -E "^mode|pfc-priority|enabled-tc|min-threshold|max-threshold|cable-length"
mode                lossless
cable-length        100
  enabled-tc        0,3
  min-threshold     150.00 KB
  max-threshold     1.50 MB
  pfc-priority      3
nv config diff  # against the saved lossy config: shows the profile objects, PFC, pools, watchdog added
```

### Step 3 — MTU trio (requirement 3)

```bash
for h in hostA hostB hostC hostD; do ssh $h "ip -br link show ens1f0np0 | awk '{print \$1, \$NF}'; ibv_devinfo -d mlx5_2 | grep active_mtu"; done
# ens1f0np0 ... mtu 9000  /  active_mtu: 4096 (5)  on all four
for s in leaf01 leaf02 spine01 spine02; do ssh cumulus@$s "nv show interface swp1 link mtu"; done    # 9216
```

### Step 4 — Incast test on the lossless fabric (requirements 4–6)

```bash
cumulus@leaf02:~$ nv action clear interface swp1 qos roce counters        # receiver-facing port
cumulus@leaf02:~$ sudo tail -f /var/log/switchd.log | grep -i -E "pfc|watchdog" &   # criterion E: must stay silent
hostB$ sudo tcpdump -ni ens1f0np0 -c 2000 -w incast.pcap udp port 4791 &  # criterion B evidence
./incast.sh 10.10.1.21 hostA hostC hostD | tee after_throughput.txt
# Per-sender: ~128-131 Gb/s each => aggregate ~390 Gb/s at the receiver (DCQCN split 3 ways, link saturated, no loss)
```

**Criterion B — marking:**

```bash
hostB$ tcpdump -nr incast.pcap -vv | grep -oE "tos 0x[0-9a-f]+" | sort | uniq -c
   1964 tos 0x6a            # 0x6a = 106 = DSCP 26 << 2 | ECT(0)   -> data correctly marked, ECN-capable
     31 tos 0x6b            # 0x6b = DSCP 26 | CE(11) -> CE-MARKED data packets: the switch flipped the ECN bits
      5 tos 0xc0            # 0xc0 = DSCP 48 << 2 -> CNPs (hostB -> senders; captured because tcpdump sees tx too)
```

**Criterion C — DCQCN roles observed:**

```bash
diff <(grep -A3 "before hostB" counters.before) <(grep -A3 "after hostB" counters.after)
< np_cnp_sent 0                  > np_cnp_sent 2210            # NP: receiver sent CNPs
< np_ecn_marked_roce_packets 0   > np_ecn_marked_roce_packets 4418127
diff <(grep -A3 "before hostA" counters.before) <(grep -A3 "after hostA" counters.after)
< rp_cnp_handled 0               > rp_cnp_handled 744          # RP: each sender handled ~1/3 of the CNPs and cut rate
cumulus@leaf02:~$ nv show interface swp1 qos roce counters | grep -E "ecn|cnp|pfc"
ecn-marked            4418127                                  # CP: switch marked at the receiver's egress queue
cnp                   2210
pfc pause frames      0 / 118                                  # see E
```

**Criterion D — no loss anywhere:**

```bash
for h in hostA hostB hostC hostD; do ssh $h "ethtool -S ens1f0np0 | grep rx_prio3_discards"; done
     rx_prio3_discards: 0      # x4
cumulus@leaf02:~$ nv show interface swp1 counters | grep -iE "drop|discard"      # 0 on TC3; (WJH in Module 8 would list any drop reason)
grep -E "out_of_sequence|packet_seq_err" counters.after | grep -E "host[ACD]"      # unchanged from counters.before => no retransmits
```

**Criterion E — PFC backstop, no storm:**

```bash
cumulus@leaf02:~$ nv show interface swp1 qos roce counters | grep pfc            # tx 118 pause frames total during 30 s of 3:1 incast
cumulus@leaf02:~$ sleep 5; nv show interface swp1 qos roce counters | grep pfc   # still 118: stopped increasing when the test ended
grep -ic watchdog /var/log/switchd.log                                            # 0 — watchdog never fired
hostB$ ethtool -S ens1f0np0 | grep tx_prio3_pause_duration                        # small (few ms total), flat after the test
# Interpretation: ECN marking began at 150 KB of queue; CNPs throttled the senders within ~tens of microseconds; the queue only
# briefly reached the PFC XOFF point during the first burst (118 frames), then DCQCN kept it below. That is the designed behaviour:
# PFC as a safety net, ECN doing the work.
```

### Before / after table (requirement 6)

| Metric (30 s, 3→1 incast) | Lossy (before) | Lossless + DCQCN (after) |
|---|---|---|
| Aggregate throughput at receiver | ~350 Gb/s (3 × ~117) | ~390 Gb/s (3 × ~130) |
| `out_of_sequence` (sum, senders) | +38,412 | +0 |
| `packet_seq_err` (sum, senders) | +9,870 | +0 |
| `np_cnp_sent` (receiver) | 0 (traffic unmarked → no ECN) | 2,210 |
| `rp_cnp_handled` (per sender) | 0 | ~740 |
| `rx_prio3_discards` (all hosts) | 0 (traffic not in prio 3) | 0 |
| Switch drops on receiver port | TC0 drops present | 0 |
| PFC pause frames (receiver leaf port) | 0 (no PFC) | 118, non-growing |
| Watchdog events | n/a | 0 |

**Interpretation.** On the lossy fabric the traffic was unmarked, so neither ECN nor PFC applied: the switch simply dropped at the incast point, the IB transport went back-N on every loss (`packet_seq_err`), and ~10 % of the link was spent on retransmission. After conversion, data is marked DSCP 26, classified into priority 3 on every hop, ECN marking starts at 150 KB of queue and CNPs on DSCP 48/TC6 return immediately on a strict-priority class; the senders throttle to a fair share and the link saturates at ~390 Gb/s with zero drops. PFC on priority 3 engaged only 118 times in 30 s, as a backstop during the initial burst, and never propagated — the watchdog stayed silent. All five acceptance criteria are met and the configuration survives a reboot on both hosts (systemd) and switches (`nv config save`).
