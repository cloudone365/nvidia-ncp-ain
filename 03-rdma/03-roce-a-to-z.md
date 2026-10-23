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

**What it is.** **RoCE** carries the InfiniBand transport (BTH and payload, the exact packets from Modules 3.1/3.2) over Ethernet. The verbs API, QPs, MRs, SEND/WRITE/READ — all identical. Figure 3.3-1 shows a RoCE v2 packet and where the fields this module configures live.

![RoCE v2 packet laid out left to right: Ethernet 14 bytes, IPv4 20 bytes with the ToS byte, UDP 8 bytes with per-QP source port and destination port 4791, BTH 12 bytes, RETH 16 bytes, payload up to 4096 bytes, ICRC and FCS; the ToS byte is expanded into six DSCP bits and two ECN bits with the 106 and 0x6b worked examples](diagrams/3.3-01-rocev2-packet.svg)
*Figure 3.3-1 — RoCE v2 on the wire: the switch sees Ethernet/IP/UDP (orange), the HCA owns the IB transport (blue); DSCP and ECN share the IPv4 ToS byte.*

**RoCE v1 vs RoCE v2:**

| | RoCE v1 | RoCE v2 |
|---|---|---|
| Encapsulation | Ethernet header (EtherType **0x8915**) → IB GRH → BTH → payload → ICRC | Ethernet → IPv4/IPv6 → **UDP (dst port 4791)** → BTH → payload → ICRC |
| Addressing | GID derived from MAC (link-local); **L2 only, not routable** | GID = IP address; **routable**, works across L3 spine-leaf fabrics |
| ECMP / load balancing | By MAC only | UDP **source port** varies per QP/flow → hashable by ECMP on every spine |
| Congestion signalling | No standard ECN carrier (no IP header) | **ECN bits in the IP header** → DCQCN works |
| Status | Legacy; disabled on modern fabrics | The default on ConnectX-4 and later, and the only option for Spectrum-X |

Everything that follows assumes RoCE v2. Packet-capture tell: RoCE v2 shows up in Wireshark as `RRoCE`/`InfiniBand` over UDP 4791; RoCE v1 as EtherType 0x8915.

**Why it exists — and why it is hard.** Data centers already run Ethernet; RoCE brings RDMA to it without a second fabric. What changes is the network underneath: Ethernet was built to drop packets under congestion and let TCP sort it out. The IB transport, by contrast, assumes a lossless link: a single lost packet triggers a go-back-N retransmission of everything after it (ConnectX HCAs implement selective repeat for some cases, but the default remains expensive), and bandwidth collapses by an order of magnitude at 0.1 % loss. The arithmetic: a 400 Gb/s flow with a 4 MB window in flight that loses one packet resends up to the whole window after a NAK or timeout; at 0.1 % loss with 4 KB packets that is a loss every ~1,000 packets — inside every window — so the link spends most of its time re-sending. So "configuring RoCE" really means "making Ethernet behave like InfiniBand" using DCB tools: **PFC**, **ECN**, **DSCP**/**PCP** classification, traffic classes, buffer management.

**How it works — the fields you configure.**

- **IPv4 ToS byte (8 bits)** = **DSCP** (high 6 bits, the class) + **ECN** (low 2 bits: `00` Not-ECT, `10` ECT(0), `01` ECT(1), `11` CE). The NIC writes it from the QP's `traffic_class` (`NCCL_IB_TC`, perftest `--tclass`, `cma_roce_tos`). **ToS 106 = (DSCP 26 << 2) | ECT(0) = 104 + 2 = 0x6a**: data in class 26, ECN-capable. A switch that marks congestion rewrites the ECN bits to `11`, giving 0x6b. CNPs use DSCP 48 → ToS 0xc0 (not ECT).
- **UDP**: destination port **4791** identifies RoCE v2; the source port is chosen per QP (a hash), which is the only per-flow entropy ECMP sees.
- **802.1Q PCP (3 bits)** exists only on tagged frames and is rewritten or lost at every L3 hop — which is why routed RoCE trusts DSCP.
- **Overhead**: 14 + 20 + 8 + 12 (+16 RETH) + 4 ICRC + 4 FCS = 78 bytes on a 4096-byte payload (~98 % efficiency); the 4096-byte cap comes from the IB MTU encoding (§2.7).

**Lossless vs lossy RoCE.** Figure 3.3-2 frames the design decision.

![Decision flow: a RoCE v2 fabric for NCCL training or loss-sensitive storage goes lossless with PFC on priority 3 plus ECN and a strict CNP class; mixed or tolerant workloads may run lossy with ECN only; a Spectrum-4 plus BlueField-3 end-to-end fabric goes Spectrum-X; each branch lists what it commits you to](diagrams/3.3-06-lossy-vs-lossless.svg)
*Figure 3.3-2 — Lossy or lossless? The decision, and what each choice commits you to operationally.*

| Model | Mechanism | Pros | Cons |
|---|---|---|---|
| **Lossless RoCE** | PFC on the RoCE priority end-to-end (host NIC ↔ switches) **plus** ECN/DCQCN to keep PFC rare | Zero drops; the IB transport's go-back-N never fires | PFC configuration on every hop, headroom math, risk of PFC storms/deadlocks, head-of-line blocking within the priority |
| **Lossy RoCE** | No PFC; rely on ECN/DCQCN (+ the NIC's improved loss recovery: selective repeat, adaptive retransmission on ConnectX-5+) and deep buffers to keep loss tiny | Simpler switch config; no PFC storm risk; multi-vendor friendly | Small loss still costs; requires modern HCAs and well-tuned ECN; not NVIDIA's recommendation for training fabrics |
| **Spectrum-X** | ECN/DCQCN-class congestion control plus adaptive routing with reordering handled in the BlueField-3 SuperNIC; PFC configured as backstop in lossless profile | Highest effective bandwidth on Ethernet | Requires Spectrum-4 + BlueField-3 end to end (Module 4.1) |

NVIDIA's guidance for AI training fabrics is lossless (PFC + ECN) with ECN doing almost all of the work and PFC acting as a safety net. Cumulus encodes this as the `nv set qos roce mode lossless` profile.

**Why the exam cares:** v1 vs v2 (EtherType 0x8915 vs UDP 4791, routable, ECN-capable), ToS 106, and "lossless = PFC + ECN, ECN does the work".

### 2.2 PFC — Priority-based Flow Control (802.1Qbb)

**Analogy:** classic 802.3x PAUSE is a red light for the whole road. **PFC** is a red light *per lane*: the receiver can stop lane 3 (RoCE) while lane 0 (management TCP) keeps flowing. Headroom is the stopping distance — the cars already past the light when it turned red still need somewhere to go.

**What it is.** A receiver whose buffer for priority *p* fills past a threshold (**XOFF**) sends a PFC frame telling its link partner "stop sending priority *p* for *t* quanta" and later releases it (**XON**) when the buffer drains. Only the RoCE priority (conventionally **priority 3**) is made lossless; TCP management traffic on priority 0 is still allowed to drop. Figure 3.3-3 shows one hop in detail and how a stuck receiver turns into a storm.

![PFC mechanism: hostA transmits priority 3 to leaf01 swp1; when the priority-3 ingress buffer crosses XOFF the switch sends a PFC frame pausing priority 3 for 0xFFFF quanta; the buffer bar shows normal fill, headroom and XON; below, a hung host holds XOFF and pause frames propagate hop by hop against the data until the PFC watchdog breaks the chain](diagrams/3.3-02-pfc-mechanism.svg)
*Figure 3.3-3 — PFC: ① the pause frame, ② XOFF threshold, ③ headroom; ④ storm propagation against the data flow; ⑤ the watchdog.*

**Why it exists.** Ethernet switches drop when a buffer is full; RoCE cannot afford drops (§2.1). PFC turns "full buffer → drop" into "nearly-full buffer → tell the neighbour to stop", per priority, so lossless and lossy traffic can share a link. It is the Ethernet approximation of IB's credits — but *reactive*: IB never sends without a grant, PFC sends until told to stop, so it must reserve buffer for what is already in flight.

**How it works — the frame (step ①).** A PFC frame is a MAC Control frame: destination MAC `01:80:C2:00:00:01`, EtherType **0x8808**, opcode **0x0101**, a 16-bit priority-enable vector (one bit per priority 0–7) and eight 16-bit timers, one per priority, in **quanta of 512 bit-times**. At 400 Gb/s a quantum is 1.28 ns, so the maximum 0xFFFF is about 84 µs; the receiver keeps refreshing XOFF while it is still congested and sends a timer of 0 (XON) to release early. PFC frames are link-local: they are consumed by the neighbour and never forwarded.

**Direction matters.** Pause frames are sent by the receiver whose *ingress* buffer cannot drain, toward the transmitter. So on a switch port, PFC counted **rx** means the device on the other end is pausing the switch (it is congested); PFC counted **tx** means the switch is pausing that device because traffic is backing up inside the switch. On a host NIC, `rx_prio3_pause` means the switch paused the host; `tx_prio3_pause` means the host paused the switch.

**How it works — thresholds and headroom (steps ② and ③).** The receiver's per-port, per-priority ingress accounting has an **XOFF** threshold (send pause), an **XON** threshold below it (release — the gap is hysteresis, so the link does not flap between pause and resume), and **headroom** above XOFF. Between the moment the receiver decides to send XOFF and the moment the last in-flight byte arrives, data keeps coming: the sender's reaction time, the serialization of a maximum-size frame already started, and **2× cable propagation delay** (XOFF travels one way, the final bytes travel the other). The receiver must reserve this **headroom** buffer *per lossless priority per port*, over and above the XOFF threshold, or it drops despite PFC.

Headroom budget for one 400 Gb/s port and priority:

| Component | Formula | 100 m fibre | 3 m DAC |
|---|---|---|---|
| Cable round trip | 2 × length × ~5 ns/m × 400 Gb/s | 2 × 500 ns → **50 KB** | 2 × 15 ns → ~1.5 KB |
| Frame the peer had already started | 1 × max frame | 9.2 KB | 9.2 KB |
| Frame we were sending when we decided | 1 × max frame | 9.2 KB | 9.2 KB |
| Peer reaction + MAC/PHY pipeline | spec-bounded response time (verify for your ASIC) | several KB | several KB |
| **Total** | | **~70–100 KB** | **~25–30 KB** |

Headroom grows with link speed and cable length; this is why NVUE's RoCE profile asks for (or assumes) a cable length (`nv show qos roce` prints `cable-length`) and why 100 m fibre runs between leaf and spine need more headroom than 3 m DAC to servers. Spectrum ASICs compute headroom automatically from the configured cable length. Multiply by every lossless port: 64 ports × 100 KB is 6.4 MB of shared buffer set aside for something that should rarely be used — the reason to keep lossless priorities to one.

**PFC storms, deadlock and the watchdog (steps ④–⑤).** If a receiver gets stuck (a hung NIC, a misbehaving driver, a failed buffer) it may hold XOFF asserted indefinitely. Its upstream switch port stops transmitting priority 3, its buffer fills, *it* sends PFC upstream — the pause propagates hop by hop across the fabric: a **PFC storm**, which can freeze every RoCE flow that shares any path with the victim. A related failure is **PFC deadlock**: a cyclic buffer dependency (possible when routing creates loops in a Clos during failures) in which every port waits on another. Counter-measures:

- **PFC watchdog** on the switch: if a port has been paused (receiving PFC XOFF) for longer than a threshold (hundreds of milliseconds), the switch declares a storm, temporarily *drops* traffic on that priority to break the chain, logs an event, and restores after a recovery interval. Cumulus: `nv set qos pfc-watchdog ...` / `nv set interface swpN qos pfc-watchdog enable` (exact syntax by release — verify in current docs); NVUE shows `pfc-watchdog` state in `nv show qos roce`.
- Keep PFC rare by making ECN react first, use adaptive routing / deadlock-free routing designs, and alert on `rx_prio3_pause`/`tx_prio3_pause` counters that never return to zero.

**How to observe it.** Host: `ethtool -S <if> | grep prio3` — `rx_prio3_pause` / `rx_prio3_pause_duration` growing = the switch is pausing this host (it is a sender caught in back-pressure); `tx_prio3_pause` growing = this host is pausing the switch (the host is the congestion point); `rx_prio3_discards` > 0 = PFC did not protect this hop (missing PFC, wrong trust, or too little headroom). Switch: `nv show interface swpN qos roce counters` (pfc rx/tx per port); in a storm, follow the ports that are *receiving* PFC toward the device that originates it.

**Why the exam cares:** PFC is per priority (802.1Qbb, not 802.3x), headroom scales with cable length and speed, storms propagate against the data, the watchdog breaks them by dropping — and rx vs tx pause tells you who is congested.

### 2.3 ECN and DCQCN — congestion control for RoCE

**Analogy:** PFC is the red light — blunt, late, and it backs traffic up behind it. **ECN** is the variable speed-limit sign on the motorway a few kilometres before the jam: the switch flips the sign (marks CE), the car at the destination radios the driver's dispatcher (CNP), and the dispatcher (the sending NIC) slows that one driver down. **DCQCN** is the rulebook for how much to slow and how fast to speed back up.

**What it is.** **ECN** (Explicit Congestion Notification) gives the sender an early, per-flow signal so it slows down *before* buffers fill and PFC fires. The algorithm standardised across NVIDIA HCAs and switches is **DCQCN** (Data Center Quantized Congestion Notification). It has three roles:

| Role | Where | What it does |
|---|---|---|
| **CP — Congestion Point** | Switch egress queue | Monitors queue depth; marks the IP header ECN field to `11` (CE, Congestion Experienced) on packets with probability rising from 0 at `min-threshold` to `probability` (e.g. 100 %) at `max-threshold` (WRED-style), for ECN-capable packets (ECT bits set by the NIC) |
| **NP — Notification Point** | Receiving HCA | On receiving CE-marked RoCE packets, sends a **CNP** (Congestion Notification Packet — a small RoCE packet with BTH opcode CNP) back to the sender for that QP, rate-limited (e.g. at most one per N µs per flow) |
| **RP — Reaction Point** | Sending HCA | On each CNP, multiplicatively cuts the QP's rate (`alpha`-based reduction); in the absence of CNPs, increases rate additively then hyper-additively (fast recovery), à la DCTCP/QCN. Per-QP, in firmware |

**Why it exists.** PFC stops loss but spreads congestion: a pause stops *every* flow on that priority on that link, including flows that are not going to the hot spot (head-of-line blocking), and it propagates upstream. What you actually want is for only the flows *causing* the hot spot to slow down, and early — while the queue is still shallow. ECN marks exactly those packets; DCQCN turns the marks into per-QP rate control in the NIC, at microsecond timescales that software TCP could never reach.

**How it works.** Figure 3.3-4 is one full DCQCN cycle; the numbers match.

![DCQCN sequence across the reaction point sending NIC, the congestion point switch egress queue on TC3 and the notification point receiving NIC: ECN-capable data is marked CE between Kmin and Kmax, the receiver sends a CNP on DSCP 48 to TC6, the sender cuts its rate by alpha over two, then recovers through fast recovery, additive and hyper increase](diagrams/3.3-03-dcqcn.svg)
*Figure 3.3-4 — DCQCN: the switch marks (CP), the receiver notifies (NP), the sender slows (RP), then recovers.*

1. **Send ECN-capable.** The RP's QP transmits at line rate with ToS 0x6a (DSCP 26, ECT(0)).
2. **Mark (CP).** At the switch egress queue for TC3, when the queue depth is between **Kmin** (`min-threshold`) and **Kmax** (`max-threshold`) each ECT packet is marked CE with a probability rising linearly from 0 to **Pmax** (`probability`); above Kmax every ECT packet is marked. Marking rewrites two bits — the packet is *not* dropped, and the ICRC is computed over invariant fields so it stays valid.
3. **Notify (NP).** The receiving HCA counts the marked packet (`np_ecn_marked_roce_packets`) and, at most once per QP per CNP interval (`min_time_between_cnps`, a few µs — verify the default), sends a **CNP**: a RoCE v2 packet with BTH opcode **0x81** addressed to the sender's QP, on **DSCP 48** (`cnp_dscp`) / PCP 6 (`cnp_802p_prio`).
4. **CNP travels back** on the strict-priority TC6 so it never waits behind the congested TC3 data (`np_cnp_sent` on the NP).
5. **Cut (RP).** On a CNP (`rp_cnp_handled`) the sender's QP remembers its current rate as the target **R_T**, updates its congestion estimate **α ← (1 − g)·α + g**, and cuts **R_C ← R_C × (1 − α/2)**. Repeated CNPs drive α toward 1 (cuts approach 50 %); isolated ones barely move it. Without CNPs, α decays (α ← (1 − g)·α each α-update period).
6. **Recover.** Rate increase is driven by a timer (`rpg_time_reset`) and a byte counter (`rpg_byte_reset`). For the first **F** stages (F = 5 in the DCQCN paper) the RP does **fast recovery**: R_C ← (R_T + R_C)/2, closing half the gap each stage. Then **additive increase**: R_T += **R_AI** (`rpg_ai_rate`) before averaging; and once both timer and byte counter have passed F stages, **hyper increase**: R_T += i·**R_HAI** (`rpg_hai_rate`). `clamp_tgt_rate` controls whether R_T is reset on each CNP. The DCQCN paper's values (g = 1/256, F = 5, α-timer 55 µs) are a reference; the NIC's actual defaults are in sysfs.

**Why CNPs need their own priority.** CNPs must get back to the sender *fast* and *never* be stuck behind the very congestion they report. So CNPs are marked with a separate, high-priority DSCP and queued in a strict-priority traffic class — conventionally **DSCP 48 → priority/TC 6**. The data path is conventionally **DSCP 26 → priority/TC 3**. These are *common defaults* used by NVIDIA (and the NVUE RoCE profile); other vendors and some deployments use DSCP 24 for data, so always verify both ends match.

**Threshold intuition.** `min-threshold` low enough that marking starts well before the PFC XOFF point; `max-threshold` below XOFF so ECN always acts first. Too aggressive marking → under-utilisation (senders back off needlessly); too lax → PFC storms do the work instead. Spectrum defaults (NVUE lossless: roughly 150 KB min / 1.5 MB max on TC3 with 100 % probability — the exact numbers vary per ASIC and release) are tuned for 25–400G AI/storage traffic; change only with measurement. DCQCN parameters on the NIC (`rate_reduce_monitor_period`, `dce_tcp_g`, `initial_alpha_value`, `rpg_*`) are exposed under `/sys/class/net/<if>/ecn/roce_rp/` and `roce_np/`; defaults are fine for most deployments.

**Failure modes and how to observe them.** `ecn-marked` > 0 on the switch but `np_cnp_sent` = 0 on the receiver → NP disabled for that priority (`roce_np/enable/3` = 0) or traffic not actually ECT. `np_cnp_sent` > 0 but `rp_cnp_handled` = 0 on senders → CNPs lost or queued behind data (CNP DSCP not mapped to the strict TC). PFC frames growing steadily under load while ECN marks are low → thresholds inverted relative to XOFF, or ECN disabled on TC3.

**Why the exam cares:** who marks (CP = switch), who sends CNPs (NP = receiver), who slows down (RP = sender); and the DSCP 26/TC3 data, DSCP 48/TC6 CNP convention.

### 2.4 Host-side configuration

**What it is.** Four host knobs: the NIC's DCB state (**mlnx_qos**), the default RoCE version and ToS for RDMA CM applications (**cma_roce_mode**, **cma_roce_tos**), the ECN roles in sysfs, and general tuning (**mlnx_tune**).

**Why it exists.** Lossless RoCE is end to end: the NIC is the first and last hop. It must put the traffic in the right class (DSCP marking and trust), pause and honour pause on the same priority as the switch, and run the NP/RP halves of DCQCN. A switch configured perfectly next to a host that trusts PCP or has PFC on a different priority gives you drops on the "lossless" class.

**How it works.**

- **mlnx_qos** configures the NIC's DCB state: whether to classify on **DSCP** or **PCP**, which priorities are PFC-enabled, the priority→traffic-class map and the ETS scheduler. Trust DSCP (not PCP) for routed RoCE v2 — PCP lives in the VLAN tag, which is lost at L3 hops and absent on untagged frames. The NIC's default dscp2prio maps DSCP 24–31 → priority 3 and 48–55 → priority 6, i.e. DSCP ≫ 3. Settings are not persistent across reboots; put them in a systemd unit, `/etc/network/interfaces` post-up, or the DOCA/OFED `roce_config` hook. Interface names are the netdev (not `mlx5_0`); use `ibdev2netdev` to map.
- **RoCE version and ToS for RDMA CM.** Applications that use RDMA CM (NCCL does not — it sets `traffic_class` itself; NVMe-oF, UCX-over-CM, rping do) take the RoCE version and the ToS/DSCP from per-device defaults set with `cma_roce_mode` and `cma_roce_tos` (both write under `/sys/kernel/config/rdma_cm/<dev>/ports/<p>/default_roce_mode|default_roce_tos`). **ToS 106 = (DSCP 26 << 2) | ECT(0) = 104 + 2**: the DSCP shifted left two bits, plus the ECN-capable-transport bit so switches may mark the packet. NCCL equivalents: `NCCL_IB_GID_INDEX=<v2 index>` (or auto in recent versions), `NCCL_IB_TC=106`; perftest: `-x <gid>` and `--tclass=106`. `NCCL_IB_SL` is ignored on RoCE. Raw-verbs applications pick the version via the GID index and set `traffic_class` themselves (Module 3.2). Legacy ConnectX-3 only: `set_roce_mode` / `ibv_exp_*` — obsolete on mlx5 devices.
- **ECN on the host.** ECN marking *response* must be enabled on the NIC per priority for both roles under `/sys/class/net/<if>/ecn/roce_np/enable/<prio>` and `roce_rp/enable/<prio>`; `roce_np/cnp_dscp` (default 48) and `roce_np/cnp_802p_prio` (default 6) set how CNPs are marked. On current MLNX_OFED/DOCA releases ECN is enabled by default on all priorities for mlx5; verify rather than assume. Older docs reference `mlnx_qos --dcbx` and `mlx5_core` module parameters; prefer the sysfs interface.
- **mlnx_tune** applies a tuning profile (`HIGH_THROUGHPUT`, `LOW_LATENCY_VMA`, …) adjusting CPU governor, IRQ affinity, ring sizes and relevant sysctls; `mlnx_tune -r` reports the current state and warns on PCIe width/speed, NUMA mismatch, etc. For AI hosts also check **mlxconfig** `ADVANCED_PCI_SETTINGS`/`MAX_ACC_OUT_READ` (RDMA READ depth on PCIe Gen5), CPU C-states, IOMMU passthrough, and that the RoCE netdev MTU is 9000.

The host column of Figure 3.3-6 (§2.5) lists these settings in the order they must match the switch.

### 2.5 Switch-side configuration: Cumulus Linux with NVUE

**What it is.** On Spectrum switches running Cumulus Linux, NVIDIA collapsed the entire DCB recipe into one profile. Use **NVUE** (`nv`) syntax; legacy NCLU (`net`) is deprecated and cannot configure the RoCE profile at all. Figure 3.3-5 is the classification chain the profile programs.

![Classification chain on a Spectrum switch as a table and a packet path: RoCE data DSCP 26 to switch priority 3 to TC3 in the lossless pool with PFC and ECN under DWRR, CNP DSCP 48 to SP6 to TC6 strict, everything else to SP0 and TC0 lossy, with the InfiniBand analogue of each column; packet path steps trust DSCP, dscp2prio, prio_tc, buffer pool with PFC and ECN, and ETS](diagrams/3.3-05-dscp-tc-mapping.svg)
*Figure 3.3-5 — DSCP → switch priority → traffic class → buffer pool → scheduler, as `nv set qos roce mode lossless` programs it, with the IB analogue of each stage.*

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

**Why it exists.** A lossless class needs a dozen settings to agree on every port of every switch: trust mode, two maps, scheduler weights, PFC enable and headroom, ECN thresholds, buffer pool membership. Each one wrong on one hop produces silent drops on the "lossless" class. One profile that sets them consistently — and that `nv show qos roce` can read back — removes most of that risk.

**How it works.** Follow a RoCE packet through Figure 3.3-5: ① ingress trusts DSCP; ② DSCP 26 maps to switch priority 3 (dscp2prio); ③ SP3 maps to TC3 (prio→TC); ④ the packet is admitted to the **lossless** ingress pool, where SP3's per-port accounting drives PFC XOFF/XON and headroom, and at egress TC3's queue depth drives ECN marking; ⑤ ETS serves TC6 (CNPs) strictly first, then TC3 and TC0 by DWRR 50/50. Because the decision at each stage is a table lookup, a packet with the wrong DSCP silently takes the TC0 path: lossy pool, no PFC, no ECN.

Variants: `mode lossy` (ECN on TC3, DSCP/TC mapping, CNP TC6 — but **no PFC**, SP3 stays in the lossy pool), `mode lossless-single-ipool` (one shared ingress pool; release-dependent), `mode lossy-multi-tc` (lossy with packet trimming across multiple TCs; Spectrum-4, newer releases), `nv unset qos roce` to remove the profile. Caveat from the docs: if the switch is already in `lossy`, running plain `nv set qos roce` does *not* switch it back — you must specify `mode lossless` explicitly. The RoCE profile is per switch, not per port; port-level overrides are done with standard `nv set qos ...` objects (e.g. different `cable-length` on uplinks; verify exact syntax).

Figure 3.3-6 puts the host and switch halves side by side; reading across a row, every box must agree.

![End-to-end lossless RoCE configuration map: hostA, leaf01, spine01, leaf02 and hostB columns with matching rows for MTU, trust DSCP, PFC on priority 3, DSCP 26 and 48 mapping, ECN on TC3 and CNP DSCP 48, ETS with TC6 strict, and ToS 106 or pfc-watchdog, over 3 m DAC and 100 m links](diagrams/3.3-04-lossless-config-map.svg)
*Figure 3.3-6 — End-to-end lossless RoCE: every hop must agree on the same classes; one mismatch means silent drops.*

### 2.6 Spectrum-X preview

**What it is.** Spectrum-X (Spectrum-4 switches + BlueField-3 SuperNICs, Module 4.1) uses the same building blocks but moves beyond DCQCN-on-ECMP: the switch performs per-packet **adaptive routing** across spine uplinks (like IB AR) and the SuperNIC reorders packets using a "Direct Data Placement" mechanism; congestion control is telemetry-assisted (switch-generated congestion telemetry to the SuperNIC rather than only ECN marks), and PFC remains configured on priority 3 as a backstop.

**Why it exists.** Flow-hash ECMP has the same collision problem as static IB routing (Module 2.3): a few elephant RoCE flows land on the same uplink while others idle, and DCQCN then dutifully slows the collided flows instead of moving them. Per-packet spraying fixes the collision but reorders packets, which ordinary RoCE NICs treat as loss — hence the need for the SuperNIC.

**How it works.** NVUE on Spectrum-X exposes this as the RoCE profile plus `nv set router adaptive-routing` / `nv set qos roce` extensions — covered in [Module 4.1](../04-spectrum-ethernet/01-spectrum-x.md). Everything in this module (DSCP 26/48, TC3/TC6, PFC prio 3, ECN) is the foundation Spectrum-X builds on.

### 2.7 MTU: switch, host and RoCE

**What it is.** Three MTUs that must be set in the right relationship:

| Layer | Recommended | Why |
|---|---|---|
| Switch port MTU (Cumulus) | **9216** (`nv set interface swp1-64 link mtu 9216`) | Default on Cumulus; must be ≥ host MTU + any encapsulation (VXLAN adds 50) |
| Host netdev MTU | **9000** (`ip link set ens1f0np0 mtu 9000`) | Jumbo for TCP/storage; leaves room on the switch |
| RoCE path MTU (`active_mtu`) | **4096** | IB MTU values are powers of two up to 4096; 4096 + RoCE/UDP/IP/Ethernet headers (~4150 bytes) fits in 9000 but **not** in 1500 (which caps RoCE at 1024) |

**Why it exists.** The RoCE path MTU is an IB transport parameter (encoded 256…4096) fixed per QP at connection time, and the transport has no path-MTU discovery or fragmentation. The Ethernet MTUs must be large enough to carry it on every hop, with the switch larger than the host so tagging or tunnelling never pushes a frame over the limit.

**How it works.** The HCA derives `active_mtu` from the netdev MTU: the largest IB MTU whose payload plus RoCE headers fits (for IPv4: 4096 + 20 + 8 + 12 + 16 + 4 ≈ 4156 bytes ≤ 9000 → 4096; with 1500 → 1024). Both ends then use the smaller of their values. So "RoCE uses 4K MTU even on 9000-byte Ethernet" is expected, not a problem. The exam trap is the reverse: an Ethernet MTU of 1500 anywhere on the path (a mis-set host, a VLAN sub-interface, a VXLAN tunnel) forces `active_mtu` down or, worse, causes silent drops when the two ends negotiated 4096 but a middle hop cannot carry it (IB transport has no path-MTU discovery). Always set MTU consistently, then confirm with `ibv_devinfo | grep mtu` on both hosts and `nv show interface swp1 link mtu` on each switch.

**Why the exam cares:** the MTU trio — switch 9216, host 9000, RoCE path 4096 — and "1500 anywhere caps RoCE at 1024".

## 3. Live-style walkthrough: lossless RoCE end to end, then prove it under load

Each step fills in one column of Figure 3.3-6: Steps 1–3 are the host column, Steps 4–5 the switch columns, Step 6 proves the DCQCN cycle of Figure 3.3-4 under load. Lab: `hostA` (10.10.1.20, `mlx5_2` ↔ `ens1f0np0`) on `leaf01 swp1`, `hostB` (10.10.1.21) on `leaf02 swp1`, Spectrum-3 leaves running Cumulus 5.x, a routed spine between them. Nothing QoS-related is configured yet.

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
# Watch leaf02 swp1 (the port facing hostB) and hostB — this is Figure 3.3-4 live: ② ecn-marked, ③-④ CNPs, ⑤ rp_cnp_handled:
cumulus@leaf02:~$ nv show interface swp1 qos roce counters
ecn-marked                               1849203          # <-- CP: the egress queue to hostB crossed min-threshold; packets marked CE
cnp                    912                                # CNPs flowing back through this port
pfc pause frames       0                 0                # none on the RECEIVER-facing port: the backlog is this port's EGRESS queue,
                                                           # so any pause is sent by the ingress side, back toward the senders (below)
hostB$ rdma statistic show link mlx5_2/1 | tr ' ' '\n' | grep -E "cnp|ecn"
np_ecn_marked_roce_packets 1849203        # NP: hostB received the CE-marked packets...
np_cnp_sent 912                           # ...and sent CNPs (rate-limited, so far fewer than marks)
hostA$ rdma statistic show link mlx5_2/1 | tr ' ' '\n' | grep rp_cnp
rp_cnp_handled 455                        # RP: hostA (one of the two senders) received its share and cut rate
cumulus@leaf01:~$ nv show interface swp1 qos roce counters | grep pfc      # leaf01 swp1 faces SENDER hostA
pfc pause frames       0                 37               # tx: leaf01 paused hostA — a sender caught in back-pressure, not a fault.
                                                           # A FEW frames: ECN did the heavy lifting, PFC was the backstop. Healthy.
hostA$ ethtool -S ens1f0np0 | grep prio3_pause
     rx_prio3_pause: 37                   # rx on the host = the switch paused the host (matches leaf01 tx above)
     tx_prio3_pause: 0                    # tx on the host would mean hostA paused the switch (hostA congested) — not the case
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

### 4.2 Everything freezes for seconds at a time; `rx_prio3_pause_duration` huge on many hosts

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
- E. PFC pause frames sent (tx) where back-pressure starts — `leaf02`'s spine-facing uplinks and `leaf01`'s sender-facing ports — are ≤ a few hundred and stop increasing within seconds of the test ending; the receiver-facing port `leaf02 swp1` shows no PFC in either direction (rx there would mean the receiver itself is the congestion point); the watchdog never fires (`/var/log/switchd.log`).

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
pfc pause frames      0 / 0                                    # receiver-facing port: no PFC here (the queue is egress) — see E
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
cumulus@leaf02:~$ nv show interface swp49-56 qos roce counters | grep pfc       # uplinks: tx = leaf02 pausing the spines; 118 frames total in 30 s
cumulus@leaf02:~$ sleep 5; nv show interface swp49-56 qos roce counters | grep pfc   # still 118: stopped increasing when the test ended
cumulus@leaf01:~$ nv show interface swp1-3 qos roce counters | grep pfc          # sender-facing ports: tx = leaf01 pausing hostA/C/D (back-pressure reached the senders)
grep -ic watchdog /var/log/switchd.log                                            # 0 — watchdog never fired
hostA$ ethtool -S ens1f0np0 | grep rx_prio3_pause_duration                        # rx on a sender = the switch paused it; small (few ms total), flat after the test
hostB$ ethtool -S ens1f0np0 | grep tx_prio3_pause                                 # 0 — the receiver never paused the switch; it was not the congestion point
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
| PFC pause frames sent (leaf02 uplinks + leaf01 sender ports, tx) | 0 (no PFC) | 118, non-growing |
| Watchdog events | n/a | 0 |

**Interpretation.** On the lossy fabric the traffic was unmarked, so neither ECN nor PFC applied: the switch simply dropped at the incast point, the IB transport went back-N on every loss (`packet_seq_err`), and ~10 % of the link was spent on retransmission. After conversion, data is marked DSCP 26, classified into priority 3 on every hop, ECN marking starts at 150 KB of queue and CNPs on DSCP 48/TC6 return immediately on a strict-priority class; the senders throttle to a fair share and the link saturates at ~390 Gb/s with zero drops. PFC on priority 3 engaged only 118 times in 30 s, as a backstop during the initial burst, and never propagated — the watchdog stayed silent. All five acceptance criteria are met and the configuration survives a reboot on both hosts (systemd) and switches (`nv config save`).
