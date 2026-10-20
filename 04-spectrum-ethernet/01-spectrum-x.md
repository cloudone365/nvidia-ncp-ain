# Module 4.1 — Spectrum-X: AI-Optimized Ethernet

> **Exam domain:** NVIDIA Spectrum Networking (30%)  
> **Source course(s):** Spectrum-X (NVIDIA Academy)  
> **Source docs:** NVIDIA Spectrum-X Networking Platform documentation; Cumulus Linux User Guide — RoCE, Adaptive Routing, EVPN; SN5600/SN5400 datasheets; NVIDIA Air documentation  
> **Estimated study time:** 5–6 hours

## 1. Learning objectives

By the end of this module you will be able to…

- **Describe** what differentiates Spectrum-X (Spectrum-4 switch + BlueField-3 SuperNIC + software stack) from "plain" data-center Ethernet. *(Spectrum Networking — platform architecture)*
- **Explain** the four failure modes of Ethernet under AI collective traffic (ECMP hash collisions, elephant flows, incast, tail latency) and the Spectrum-X mechanism for each. *(Spectrum Networking — AI traffic characteristics)*
- **Configure** lossless RoCE, ECN/PFC trust and **adaptive routing** on a Spectrum switch running Cumulus Linux using **NVUE** (`nv set` / `nv show` / `nv config apply`). *(Spectrum Networking — RoCE configuration)*
- **Verify** RoCE and AR state with `nv show qos roce`, per-interface RoCE counters and `nv show router adaptive-routing`. *(Troubleshooting Tools — Ethernet verification)*
- **Configure** a multi-tenant BGP-EVPN/VXLAN fabric with per-tenant VRFs and **explain** how it delivers performance isolation. *(Spectrum Networking — EVPN multi-tenancy)*
- **Describe** NVIDIA Air's role as a digital twin and state what it cannot validate. *(Automation and Configuration — validation)*
- **Sketch** a **rail-optimized** Spectrum-X leaf/spine topology and compare 2:1 oversubscribed vs non-blocking designs. *(AI Data Center Design — reference architectures)*

## 2. Key technical concepts

### 2.1 What Spectrum-X is (and is not)

*Analogy:* a Formula 1 car is not just an engine. Spectrum-X is the engine (Spectrum-4 ASIC), the tyres that keep it on the track (BlueField-3 SuperNIC) and the pit wall watching every lap (NetQ/WJH). Take any one away and you have a fast road car.

Spectrum-X is NVIDIA's name for an *end-to-end* Ethernet platform tuned for AI training and inference clusters — not a single product. Three pieces must be present together:

| Layer | Component | Role |
|---|---|---|
| Switch | **Spectrum-4** ASIC in the **SN5600** (51.2 Tb/s, 64×800G OSFP) and **SN5400** (25.6 Tb/s, 400G) | Lossless RoCE, adaptive routing (packet spraying), telemetry-driven congestion signalling, deep shared buffer |
| Endpoint | **BlueField-3 SuperNIC** (400G) | Reorders sprayed packets in hardware (Direct Data Placement), runs congestion control, enforces per-tenant rate limits |
| Software | Cumulus Linux or NVIDIA SONiC; DOCA on the SuperNIC; **NetQ**/**WJH** telemetry; NVIDIA Air digital twin | Configuration, verification, observability |

The key insight for the exam: a Spectrum switch alone running standard RoCE is "good Ethernet". Spectrum-X exists because the switch and the NIC *cooperate* — the switch sprays and marks, the NIC reorders and throttles. Remove either half and you fall back to classic ECMP + DCQCN behaviour. See [`../05-bluefield-doca/01-bluefield-dpu-administration.md`](../05-bluefield-doca/01-bluefield-dpu-administration.md) for the SuperNIC side.

InfiniBand (Module 2.x) remains the reference fabric for the largest single-tenant clusters. Spectrum-X targets *AI clouds*: many tenants, existing Ethernet tooling (BGP, EVPN, Ansible, syslog), near-IB effective bandwidth. NVIDIA claims ~1.6× traditional Ethernet for collectives; the number is marketing, the *reason* is testable.

**Why the exam cares:** a mixed fleet (SN5600 + ConnectX-6 hosts) underperforming on "Spectrum-X features" always means one half of the cooperating pair is missing.

#### Hardware quick reference

| Switch | ASIC | Capacity | Ports | Typical role |
|---|---|---|---|---|
| SN5600 | Spectrum-4 | 51.2 Tb/s | 64 × 800G OSFP (+1 mgmt SFP28); each 800G port breaks out to 2×400G or 4×200G | Spine and leaf in Spectrum-X SuperPOD; 128×400G leaf when broken out |
| SN5400 | Spectrum-4 | 25.6 Tb/s | 64 × 400G QSFP-DD (+ 2 × SFP28) | Leaf, storage, or smaller fabrics |
| SN4600/SN4700 | Spectrum-3 | 12.8 Tb/s | 64 × 200G / 32 × 400G | Prior generation; supports AR but not the full Spectrum-X feature set — verify against the support matrix |

Breakout is standard NVUE (Module 4.2): `nv set interface swp1 link breakout 2x` yields `swp1s0`/`swp1s1` — how 400G SuperNICs attach to an 800G SN5600 leaf port.

### 2.2 The AI Ethernet problem — four failure modes

Classic data-center Ethernet was designed for many short, uncorrelated flows. AI training traffic is the opposite: a few very large, highly synchronized flows from **NCCL** collectives (all-reduce, all-to-all, reduce-scatter). Four problems follow.

**ECMP hash collisions.** *Analogy:* four trucks reach a four-lane toll plaza, every driver reads the same sign and picks the same lane; three lanes sit empty while one backs up. A standard switch picks one equal-cost path per *flow* by hashing the 5-tuple. With few flows per GPU pair and few uplinks, two elephant flows routinely hash to the same spine link while another sits idle: non-blocking on paper, 50–60% utilized in practice.

**Elephant flows.** A multi-gigabyte RDMA write is one flow. Flow-level load balancing cannot split it; resilient hashing and flowlet switching help marginally but still bind a flow to one path at a time.

**Incast.** *Analogy:* every truck in the city arrives at one warehouse door in the same minute. In all-reduce, many senders hit the same receiver in the same microsecond window; its leaf port is oversubscribed instantly. Without a lossless mechanism, packets drop and RoCE go-back-N collapses throughput. With **PFC** alone, pause frames propagate upstream and head-of-line block unrelated tenants.

**Tail latency.** A collective finishes when its *slowest* participant finishes; p99.9 matters more than the mean — a 1% slow-path event per iteration is a permanent tax on the job.

**Why the exam cares:** match mechanism to problem. AR fixes collisions and elephants. Congestion control plus lossless buffering fixes incast. Nothing "fixes" tail latency except removing the other three.

### 2.3 Adaptive routing (packet spraying + DDP)

*Analogy:* adaptive routing is a traffic cop waving each truck — not each convoy — into whichever lane is emptiest right now. The SuperNIC is the receiving warehouse, which does not care in what order pallets arrive because each is labelled with its exact shelf location.

**Adaptive routing** on Spectrum-4 forwards *per packet*, not per flow, across all equal-cost next hops, choosing the least-loaded egress from real-time queue occupancy and the port's link-utilization threshold. This eliminates hash collisions and splits elephant flows across every uplink.

The cost is reordering. On a standard NIC, RoCE treats out-of-order arrival as loss and triggers go-back-N. The BlueField-3 SuperNIC solves this with **Direct Data Placement (DDP)**: an RDMA packet carries the virtual address it belongs to, so the SuperNIC writes each packet straight into the correct offset in GPU/host memory regardless of arrival order and signals completion once the full message is present. No reorder buffer, no retransmit. This is why AR in Spectrum-X requires the SuperNIC — with a ConnectX-6 or third-party NIC you must disable AR or accept the penalty.

Notes the exam likes: AR applies to RoCE traffic (UDP port 4791 by default) — non-RoCE traffic keeps flow-based ECMP; AR is not supported on SVIs, L3 subinterfaces, bonds or bond members — it is a physical L3 uplink feature; AR and resilient hashing are alternatives, not layers.

### 2.4 Congestion control (telemetry-based, switch-to-NIC)

*Analogy:* DCQCN is a driver who slows down on seeing brake lights. Spectrum-X CC is a driver with a live traffic feed showing how full each stretch of road is.

Classic DCQCN relies on **ECN** marks that the receiving NIC reflects back as CNPs; it reacts late and probes blindly. Spectrum-X adds in-band telemetry: the switch stamps congestion information (queue depth, link utilization) into RoCE packets in flight; the receiving SuperNIC returns it to the sender, whose congestion-control engine (on the SuperNIC's programmable datapath, not the host CPU) adjusts injection rate per flow within microseconds. Rate set from *measured* fabric state converges faster than rate inferred from marks, so PFC is rarely triggered — it stays as a safety net, not the primary mechanism.

### 2.5 Lossless vs lossy — the decision

| Mode | Mechanism | When to use |
|---|---|---|
| `lossless` (default with `nv set qos roce`) | **PFC** on switch-priority 3 (RoCE) + ECN marking; CNP on switch-priority 6 strict-priority | Classic RoCEv2 with any NIC; required when NICs lack advanced congestion control |
| `lossy` | ECN only; no PFC; relies on NIC CC and, for Spectrum-X, telemetry-based CC + DDP tolerance | Spectrum-X with SuperNICs, to avoid PFC head-of-line blocking entirely |
| `lossless-single-ipool` | Lossless with one shared ingress pool | Specific buffer tuning cases — check current docs |
| `lossy-multi-tc` | Lossy with multiple RoCE traffic classes | Multi-queue workloads — check current docs |

Exam framing: lossless = "never drop; pause instead"; lossy = "mark and let the endpoints slow down". Spectrum-X reference designs typically run *lossless* with AR, but NVIDIA's AI-cloud guidance increasingly favours lossy with SuperNIC CC. Read the question for which NICs are present.

### 2.6 Performance isolation (multi-tenancy)

*Analogy:* a **VRF** is a separate building with its own address book. Tenants in different buildings can reuse room numbers, and a fire alarm in one building does not empty the other.

One tenant's incast must not stretch another's step time. Spectrum-X isolates by combining: **network segmentation** (BGP-EVPN, one VRF/L3VNI per tenant, §2.8); **per-flow congestion control at the SuperNIC** (rate limits hit the misbehaving flow, not the shared port); **shared-buffer architecture** (Spectrum-4's fully shared packet buffer lets any port absorb a burst, so one tenant's burst does not starve another's lossless pool); **traffic classes** (RoCE in its own lossless TC, CNP strict-priority, management in lossy TCs).

### 2.7 Telemetry: WJH and NetQ

**WJH** (What Just Happened) is a Spectrum ASIC feature that exports every dropped packet's header plus a *reason code* ("TTL expired", "ingress port ACL", "tail drop on TC3"). With **NetQ** (Module 4.5) it turns "the job is slow" into "leaf03 swp12 TC3 dropped 1,204 packets from 10.1.3.7 because the PFC watchdog fired". Histogram telemetry (`nv set system telemetry ...`) samples queue occupancy and latency buckets continuously so you see congestion forming before drops.

### 2.8 Multi-tenant BGP-EVPN with VRFs

*Analogy:* **EVPN** is a phone directory every switch shares by BGP. Instead of flooding "who has MAC X?", each switch publishes its entries and reads everyone else's.

An AI cloud fabric is almost always a BGP-unnumbered IP underlay with an EVPN/VXLAN overlay; each tenant gets a VRF (L3VNI) and optionally L2 VNIs. Spectrum-4 does VXLAN encap/decap, EVPN and RoCE AR in hardware simultaneously — unlike merchant silicon that disables features when VXLAN is active.

- **VXLAN** — MAC-in-UDP (UDP 4789); the switch is the VTEP (NVE), identified by a loopback.
- **EVPN** — BGP L2VPN EVPN address-family distributing MAC/IP reachability. **Type-2** = MAC (+IP) for hosts in an L2 VNI; **Type-5** = IP prefixes between VTEPs for an L3 VNI (inter-subnet, per-VRF).
- **VRF per tenant** — subnets live in a VRF mapped to one L3VNI; inter-VTEP traffic within the tenant routes symmetrically via the L3VNI.
- **Multihoming / ESI** — dual-attachment without MLAG; leaves advertise an Ethernet Segment Identifier (Type-1/Type-4) so remote VTEPs load-balance to both. GPU SuperNICs single-attach to one rail leaf (§2.10), so this matters mostly for storage and management leaves.

Pure-L3 GPU fabrics often skip L2 VNIs: each GPU-node interface lands on a /31 or /30 routed port in the tenant VRF and only Type-5 routes are exchanged — no BUM, no ARP suppression, and a natural fit for AR, which needs physical L3 uplinks anyway.

### 2.9 NVIDIA Air — the digital twin

**NVIDIA Air** (formerly Cumulus in the Cloud; "DSX Air" in some Spectrum-X materials) is a hosted simulation platform running real Cumulus Linux, NVIDIA SONiC, NetQ and Ubuntu host images in a topology you draw or import. As a *digital twin* it lets you build the exact leaf/spine/host topology before hardware arrives, validate NVUE configuration, EVPN route exchange, BGP policy and Ansible playbooks (Module 6.1), push the identical YAML to production, and rehearse upgrades and failures safely. Limitation: no ASIC — no real RoCE/AR/PFC data-plane behaviour, no WJH hardware drops, no performance numbers. Control-plane and configuration validation only; the exam tests this. Air exposes a REST API and SDK so CI can spin up a twin, run the playbook and `netq check bgp`/`netq check evpn`, and tear it down.

### 2.10 Reference topology: Spectrum-X SuperPOD

A Spectrum-X DGX SuperPOD compute fabric is **rail-optimized**: each DGX node has 8 GPUs, each with its own 400G SuperNIC; NIC *n* of every node connects to leaf *n* ("rail *n*"). Same-GPU-index NCCL traffic (the dominant ring/tree all-reduce pattern) stays on one leaf — one hop, no spine, no AR needed. Cross-rail traffic goes leaf → spine → leaf and benefits from AR. A scalable unit of 32 nodes × 8 NICs = 256 × 400G = 128 × 800G leaf downlinks after breakout; one SN5600 per rail handles 32 nodes with 32 × 400G downlinks (16 × 800G broken out) and 16–32 × 800G uplinks.

| Design | Leaf downlink:uplink | Pros | Cons |
|---|---|---|---|
| Non-blocking (1:1) | 12.8T down / 12.8T up on SN5600 | Any-to-any full bandwidth; simplest for all-to-all workloads | Twice the spine ports/optics; higher cost |
| 2:1 oversubscribed | 2× more downlink than uplink | Cheaper; fine when rail-optimized traffic stays local | All-to-all (e.g., MoE expert parallel) suffers; AR becomes essential |

Spine count = uplinks per leaf for full ECMP spread; with AR the spines act as one aggregate pool so the fabric behaves close to non-blocking even at 2:1 for typical collectives. Management/storage is a separate conventional Ethernet network (often Spectrum, not Spectrum-X RoCE AR). Full SuperPOD RA: [`../01-ai-datacenter-design/`](../01-ai-datacenter-design/).

## 3. Live-style walkthrough — one Spectrum-X leaf from factory default

We take `leaf01`, an SN5600 fresh out of the box on Cumulus Linux 5.x, to a working Spectrum-X leaf: breakout, BGP unnumbered underlay, EVPN tenant VRFs, lossless RoCE, adaptive routing, telemetry. All commands are NVUE; legacy NCLU (`net add ...`) is deprecated on Spectrum-4.

```text
   spine01   spine02   spine03   spine04
     |         |         |         |
   swp61     swp62     swp63     swp64      (800G uplinks)
      \        |         |        /
       +------ leaf01 (SN5600) --+
      /                           \
   swp1s0                       swp1s1      (400G breakout)
   SuperNIC, tenant A           SuperNIC, tenant B
```

### Step 1 — Identity and breakout

```bash
nv set system hostname leaf01                 # NetQ and syslog key off this
nv set interface lo ip address 10.0.0.11/32   # becomes BGP router-id AND VTEP source later
nv set interface swp1 link breakout 2x        # 800G OSFP -> two 400G lanes for two SuperNICs; swp1s0/swp1s1 appear, ASIC port map changes (switchd reload on many platforms)
nv config diff                                # review pending vs applied before touching the running system
```

Expected `nv config diff` output (abbreviated):

```text
- set:
    interface:
      lo:
        ip:
          address:
            10.0.0.11/32: {}
      swp1:
        link:
          breakout:
            2x: {}
    system:
      hostname: leaf01
```

```bash
nv config apply -y                 # --assume-yes: no prompt. Breakout may take ~30 s.
nv show interface swp1s0 link      # CHECKPOINT: speed 400G, fec rs. Only swp1 present? breakout failed — journalctl -u switchd
```

### Step 2 — BGP unnumbered underlay

```bash
nv set interface swp61-64 link state up               # four 800G uplinks
nv set interface swp61-64 ip ipv4 forward on          # unnumbered: no IPv4 address; RFC 5549 routes resolve via IPv6 link-local next hops
nv set router bgp autonomous-system 65011             # one ASN per leaf; spines share their own
nv set router bgp router-id 10.0.0.11
nv set vrf default router bgp peer-group underlay remote-as external   # "any ASN that is not mine" — identical on every leaf
nv set vrf default router bgp neighbor swp61 peer-group underlay       # interface neighbors = unnumbered
nv set vrf default router bgp neighbor swp62 peer-group underlay
nv set vrf default router bgp neighbor swp63 peer-group underlay
nv set vrf default router bgp neighbor swp64 peer-group underlay
nv set vrf default router bgp address-family ipv4-unicast network 10.0.0.11/32   # advertise the loopback: remote VTEPs must reach it
nv set vrf default router bgp address-family l2vpn-evpn enable on                # EVPN AF globally...
nv set vrf default router bgp peer-group underlay address-family l2vpn-evpn enable on   # ...and on the peer-group
nv config apply -y
nv show vrf default router bgp neighbor               # CHECKPOINT
```

```text
Neighbor  Remote-AS  State        Uptime    MsgRcvd  MsgSent  PfxRcvd  PfxSent
--------  ---------  -----------  --------  -------  -------  -------  -------
swp61     65000      Established  00:01:12  14       13       4        1
swp62     65000      Established  00:01:10  14       13       4        1
swp63     65000      Established  00:01:11  13       13       4        1
swp64     65000      Established  00:01:09  13       13       4        1
```

Watch the `Remote-AS` column: you never typed 65000; `remote-as external` learned it from the OPEN message. A session stuck in `Active` usually means the spine side lacks the interface neighbor or IPv6 RA is disabled.

### Step 3 — RoCE lossless and adaptive routing

```bash
# ONE command installs the whole RoCE QoS profile: DSCP 26 -> switch-priority 3 (RoCE), DSCP 48 -> SP6 (CNP),
# PFC on SP3, ECN min/max on the RoCE TC, shared-buffer lossless pool, ETS split, and an LLDP application TLV
# so DCBX-capable NICs learn the priority.
nv set qos roce mode lossless                  # SuperNIC-only, PFC-free fabric: mode lossy
nv set router adaptive-routing enable on       # AR global enable first...
nv set interface swp61-64 router adaptive-routing enable on    # ...then per physical L3 uplink. NOT bonds/SVIs/subinterfaces.
# 5.15+: per-interface keyword is "state enabled" instead of "enable on"; the NVUE reference lists both forms.
nv set interface swp61-64 router adaptive-routing link-utilization-threshold 70   # above this % a port counts as congested; default 70
nv config diff      # expect qos.roce, router.adaptive-routing and the four interfaces
nv config apply -y  # Cumulus <=5.12: RESTARTS switchd (data-plane outage). 5.13+: reload only. Plan a window on older releases.
```

Checkpoint — verify RoCE and AR:

```bash
nv show qos roce                               # State enabled, Mode lossless, PFC on priority 3 Rx/Tx, ECN min/max + probability on the RoCE TC, Trust L3 (DSCP), DSCP->SP and SP->TC tables
nv show interface swp61 qos roce status        # PFC/ECN per TC on this port
nv show interface swp61 qos roce counters      # PFC pause rx/tx, ECN-marked packets, drops
nv show interface qos-roce-counters            # all ports at once
nv action clear interface swp61 qos roce counters   # zero them before a test
nv show router adaptive-routing                # enable on
nv show interface swp61 router adaptive-routing     # enable on, link-utilization-threshold 70
```

Rising `pfc tx` on a leaf *downlink*: the host NIC is being paused — the host is the congestion point. Rising `pfc rx` on an *uplink*: the spine is pausing *you*; look upstream.

To tune beneath the profile (set RoCE first, then override — the profile replaces existing QoS):

```bash
# Move RoCE to switch-priority 2 instead of 3 (must update mapping and egress queue too)
nv set qos pfc default-global switch-priority 2
nv set qos mapping default-global dscp 26 switch-priority 2
nv set qos egress-queue-mapping default-global switch-priority 2 traffic-class 3
# ECN thresholds in bytes; defaults vary by platform — check the doc before quoting a number.
nv set qos congestion-control default-global traffic-class 3 min-threshold 150000
nv set qos congestion-control default-global traffic-class 3 max-threshold 1500000
nv set qos congestion-control default-global traffic-class 3 probability 100
nv set qos pfc default-global cable-length 100                       # longer cable -> more PFC headroom
nv set qos egress-scheduler default-global traffic-class 3 bw-percent 50   # RoCE TC gets half the link under contention
```

Interface-level RoCE (apply only to some ports) exists in newer releases; verify in current docs.

### Step 4 — Overlay: VTEP, EVPN, tenants

```bash
nv set nve vxlan source address 10.0.0.11            # VTEP sources VXLAN from the loopback advertised in Step 2
nv set nve vxlan arp-nd-suppress on                  # answer ARP/ND locally from Type-2 routes instead of flooding
nv set evpn enable on

# --- Tenant A: VRF + L3VNI + one L2 VNI ---
nv set vrf tenantA                                   # the "separate building"
nv set vrf tenantA evpn vni 100100                   # L3VNI: Type-5 routes for this tenant ride here
nv set bridge domain br_default vlan 100 vni 10100   # L2 VNI for VLAN 100 (Type-2 routes)
nv set interface vlan100 ip address 10.100.1.1/24    # SVI = tenant gateway on this leaf
nv set interface vlan100 ip vrf tenantA              # SVI lives in the tenant's building
nv set interface vlan100 ip vrr address 10.100.1.254/24     # anycast gateway (VRR) shared with the other leaves
nv set interface vlan100 ip vrr mac-address 00:00:5e:00:01:01
nv set vrf tenantA router bgp autonomous-system 65011       # per-VRF BGP instance...
nv set vrf tenantA router bgp router-id 10.0.0.11
nv set vrf tenantA router bgp address-family ipv4-unicast redistribute connected enable on   # ...SVI subnet becomes a Type-5 route
nv set vrf tenantA router bgp address-family l2vpn-evpn enable on

# --- Tenant B: identical pattern with vrf tenantB, L3VNI 100200, VLAN 200 / VNI 10200, SVI 10.200.1.1/24 ---
nv set vrf tenantB
nv set vrf tenantB evpn vni 100200
nv set bridge domain br_default vlan 200 vni 10200
nv set interface vlan200 ip address 10.200.1.1/24
nv set interface vlan200 ip vrf tenantB
nv set vrf tenantB router bgp autonomous-system 65011
nv set vrf tenantB router bgp address-family ipv4-unicast redistribute connected enable on
nv set vrf tenantB router bgp address-family l2vpn-evpn enable on

# --- GPU-node downlinks: one SuperNIC per tenant on the broken-out port ---
nv set interface swp1s0 bridge domain br_default access 100
nv set interface swp1s1 bridge domain br_default access 200

nv config apply -y
nv config save          # apply is NOT persistent; save writes startup.yaml
```

```bash
nv show evpn; nv show evpn vni; nv show nve vxlan                # CHECKPOINT: VTEP, VNIs
nv show vrf tenantA router bgp address-family l2vpn-evpn route   # type-2/type-5 routes
nv show vrf tenantA router rib ipv4
```

```text
VNI     Type  VRF      VLAN  Flood-mode  Remote-VTEPs
------  ----  -------  ----  ----------  -------------------------
10100   L2    tenantA  100   head-end    10.0.0.12, 10.0.0.13
10200   L2    tenantB  200   head-end    10.0.0.12
100100  L3    tenantA  -     -           -
100200  L3    tenantB  -     -           -
```

Good: L2 VNIs list remote VTEPs; L3 VNIs present with the right VRF. Underneath it is FRR: `sudo vtysh -c "show bgp l2vpn evpn summary"`.

### Step 5 — Telemetry

```bash
nv set system telemetry export otlp state enabled          # OpenTelemetry export (5.x) — verify
nv set system telemetry histogram ingress-buffer ...        # buffer histograms — see NVUE reference
nv set system telemetry histogram egress-buffer ...
nv config apply -y
nv show system telemetry
sudo netq config add server 10.10.0.100 port 31980 vrf mgmt   # NetQ agent (Module 4.5) — WJH flows once this is set
```

Histograms sample queue depth into buckets at microsecond granularity and feed NetQ's congestion views. Object names differ between releases; the exam cares that *Spectrum provides hardware-timestamped histograms and WJH drop reasons*, not the NVUE path.

## 4. Troubleshooting / common gotchas

### Gotcha 1 — AR enabled, NCCL got slower

**Symptom:** after enabling AR, all-reduce bandwidth drops 30–50%; host `ethtool -S` shows `out_of_sequence` climbing.

**Why it happens:** the hosts have ConnectX-6/7 or third-party NICs, not SuperNICs. Without DDP the NIC treats reordering as loss and triggers go-back-N.

**How to debug:**
```bash
nv show router adaptive-routing                   # confirm AR is on
ibv_devinfo | grep -E 'hca_id|board_id'           # on host: is it a BlueField-3 SuperNIC?
ethtool -S ens3f0np0 | grep out_of_sequence        # rising = reordering penalty
```

**Fix:** `nv unset interface swp61-64 router adaptive-routing` on uplinks serving non-SuperNIC hosts, or replace the NICs. AR is a *pair* feature.

### Gotcha 2 — `nv config apply` for AR fails on a bond

**Symptom:** `nv set interface bond1 router adaptive-routing enable on` → apply error.

**Why it happens:** AR is only supported on physical L3 interfaces — not bonds, bond members, SVIs or subinterfaces.

**How to debug:** `nv config diff` shows the offending path; the apply error names the interface type.

**Fix:** `nv unset interface bond1 router adaptive-routing`; apply AR on the physical uplinks. GPU fabrics use routed physical uplinks, no bonds.

### Gotcha 3 — Custom DSCP map vanished after enabling RoCE

**Symptom:** `nv set qos mapping default-global dscp 46 switch-priority 5` was in place; after `nv set qos roce mode lossless` it is gone.

**Why it happens:** `nv set qos roce` *replaces* the QoS profile.

**How to debug:** `nv show qos mapping default-global` — DSCP 46 missing; `nv config history` shows the RoCE revision.

**Fix:** set RoCE first, then re-apply overrides in the same or a later revision.

## 5. Hands-on assignment — two-tenant Spectrum-X leaf/spine

**Scenario.** Two tenants (`tenantA`, `tenantB`) share a 2-leaf / 2-spine Spectrum-X fabric with full performance isolation. Each leaf has two 400G SuperNIC downlinks (one per tenant) on a broken-out 800G port and two 800G uplinks. Use NVIDIA Air (or real gear).

**Requirements**
1. Hostnames `leaf01`, `leaf02`, `spine01`, `spine02`; loopbacks 10.0.0.11/.12 (leaves), 10.0.0.1/.2 (spines).
2. BGP unnumbered underlay: leaves ASN 65011/65012, spines ASN 65000, peer-groups, EVPN AF everywhere.
3. On each leaf, `swp1` broken out 2x; `swp1s0` → tenantA (VLAN 100 / VNI 10100 / L3VNI 100100), `swp1s1` → tenantB (VLAN 200 / VNI 10200 / L3VNI 100200). VRR anycast gateway on both leaves.
4. Lossless RoCE and AR on every leaf and spine fabric port, threshold 70.
5. Each tenant's prefix appears as a Type-5 route *only* in its own VRF on the remote leaf.
6. Telemetry proof: RoCE counters and AR state captured per leaf; NetQ agent pointed at a server.

**Deliverables**
- Four NVUE YAML files (`nv config show -o yaml`), one per switch.
- A verification log: `nv show vrf default router bgp neighbor`, `nv show evpn vni`, `nv show vrf tenantA router bgp address-family l2vpn-evpn route`, `nv show qos roce`, `nv show router adaptive-routing` from each leaf.
- One paragraph on what you could *not* verify in Air and why.

**Acceptance criteria**
- All 8 underlay sessions Established.
- `nv show evpn vni` on leaf01 lists 10.0.0.12 as a remote VTEP for VNI 10100 and 10200.
- leaf02's `tenantA` RIB contains 10.100.1.0/24 via EVPN; its `tenantB` RIB does *not*.
- `Mode: lossless` and AR enabled with all fabric ports on all four switches.
- Spines carry no VRF/VNI configuration.

### Hint
- Order: underlay → verify → RoCE/AR → verify → overlay → verify. Do not skip a checkpoint.
- Spines need only the EVPN AF on the peer-group; `nv set evpn enable on` makes a VTEP, which they are not.
- The redistribution that turns a tenant SVI subnet into a Type-5 route lives in the *per-VRF* BGP instance.
- A session stuck in `Active`: check IPv6 link-local and the interface neighbor on the *other* end.
- Air has no ASIC: the profile shows, PFC counters stay at zero. That is your paragraph.

## Appendix A — Quick command / config reference

| Task | NVUE |
|---|---|
| RoCE profile lossless / lossy / remove / show | `nv set qos roce mode lossless` / `... lossy` / `nv unset qos roce` / `nv show qos roce` |
| Per-port RoCE status / counters / clear | `nv show interface swpN qos roce status` / `... counters` / `nv action clear interface swpN qos roce counters` |
| AR global / per interface / threshold / show | `nv set router adaptive-routing enable on` / `nv set interface swpN router adaptive-routing enable on` (5.15+: `state enabled`) / `... link-utilization-threshold 70` / `nv show router adaptive-routing` |
| PFC priority | `nv set qos pfc default-global switch-priority 3` |
| ECN thresholds | `nv set qos congestion-control default-global traffic-class 3 min-threshold <bytes>` |
| RoCE TC bandwidth | `nv set qos egress-scheduler default-global traffic-class 3 bw-percent 50` |
| VTEP / EVPN | `nv set nve vxlan source address <lo>` / `nv set evpn enable on` |
| Tenant VRF + L3VNI / L2 VNI | `nv set vrf T` + `nv set vrf T evpn vni <vni>` / `nv set bridge domain br_default vlan 100 vni 10100` |
| BGP unnumbered | `nv set vrf default router bgp neighbor swp61 remote-as external` |
| Breakout 800G→2×400G | `nv set interface swp1 link breakout 2x` |
| Telemetry | `nv set system telemetry export otlp state enabled` (verify) / `nv show system telemetry` |

## Appendix B — Exam traps & key takeaways

- Spectrum-X = Spectrum-4 switch (SN5600: 64×800G OSFP, 51.2T; SN5400: 64×400G + 2×SFP28, 25.6T) + BlueField-3 SuperNIC + Cumulus/SONiC + DOCA + NetQ. A Spectrum switch alone is not Spectrum-X.
- AR with packet spraying on a non-SuperNIC endpoint causes out-of-order RoCE and go-back-N retransmits; DDP on the SuperNIC makes reordering harmless.
- AR spreads load across *paths*; incast is many sources into *one destination port* — only congestion control / lossless buffering addresses it.
- AR is unsupported on bonds, SVIs or subinterfaces. Physical L3 uplinks only. Applying it on Cumulus ≤5.12 restarts `switchd`; 5.13+ reloads.
- `nv set qos roce` *replaces* the QoS profile. Set RoCE first, then override.
- Lossless ≠ "no congestion". Lossless means PFC pause instead of drop; lossy + good CC is NVIDIA's direction with SuperNICs.
- NVIDIA Air validates control plane and config, not RoCE/AR performance — there is no ASIC in the twin.
- Type-2 = MAC/IP (L2 VNI); Type-5 = IP prefix (L3 VNI). A pure-L3 GPU fabric uses only Type-5.
- Rail-optimized means GPU *n* of every node on leaf *n*, not "one leaf per node"; AR makes 2:1 oversubscription workable.
- RoCE counters: PFC *tx* on a downlink = host congested; PFC *rx* on an uplink = spine pausing you. NCLU `net add` is never the answer — NVUE is.

## Appendix C — Knowledge check

1. A customer deploys SN5600 leaves with adaptive routing enabled, but the GPU servers use ConnectX-6 Dx NICs. NCCL all-reduce performance is worse than with AR disabled. Why?
   A. ConnectX-6 Dx does not support RoCEv2.
   B. Packet spraying delivers RoCE packets out of order and the NIC lacks Direct Data Placement, causing go-back-N retransmits.
   C. AR disables ECN marking.
   D. The SN5600 cannot run AR and EVPN simultaneously.

2. Which NVUE command installs PFC on switch-priority 3, ECN thresholds, DSCP trust and the RoCE traffic-class mapping in one step?
   A. `nv set qos pfc enable on`
   B. `nv set qos roce mode lossless`
   C. `nv set qos trust dscp`
   D. `net add roce lossless`

3. You need routed, isolated connectivity for 12 tenants across a Spectrum-X EVPN fabric, with no L2 stretching. Which EVPN route type carries the tenant prefixes between VTEPs?
   A. Type-1
   B. Type-2
   C. Type-3
   D. Type-5

4. An engineer runs `nv set interface bond1 router adaptive-routing enable on` and the apply fails. What is the most likely reason?
   A. AR requires `nv set qos roce` first.
   B. AR is not supported on bonds; it must be applied to physical L3 interfaces.
   C. The bond has no IP address.
   D. The command requires `--assume-yes`.

5. Which statement about NVIDIA Air as a Spectrum-X digital twin is correct?
   A. It reproduces WJH hardware drop events for RoCE traffic.
   B. It can benchmark NCCL all-reduce bandwidth before hardware arrives.
   C. It validates NVUE configuration, BGP/EVPN control plane and Ansible playbooks, but not ASIC data-plane behaviour.
   D. It requires physical SN5600 switches connected via tunnels.

6. In a rail-optimized SuperPOD, GPU 3 on node 17 sends to GPU 3 on node 22. Which path does the traffic take?
   A. leaf → spine → leaf, always
   B. Stays on leaf 3, one hop, because both NICs connect to the same rail leaf
   C. Through the management switch
   D. Through leaf 17 and leaf 22

7. `nv show interface swp61 qos roce counters` shows a rapidly increasing PFC *rx* pause count on a leaf's uplink. What does this indicate?
   A. The directly attached host is congested.
   B. The spine is pausing this leaf; congestion is upstream.
   C. ECN is misconfigured on the host.
   D. The port is in lossy mode.

8. On Cumulus Linux 5.11, which side effect must be planned for when applying adaptive routing?
   A. BGP sessions reset
   B. `switchd` restarts, interrupting forwarding
   C. The RoCE profile is removed
   D. The management VRF is deleted

### Answer key
1. B — AR sprays per packet; without a SuperNIC's DDP the receiving NIC treats reordering as loss.
2. B — `nv set qos roce mode lossless` installs the complete lossless RoCE QoS profile.
3. D — Type-5 EVPN routes advertise IP prefixes per L3VNI/VRF.
4. B — Adaptive routing is unsupported on bonds, bond members, SVIs and subinterfaces.
5. C — Air has no ASIC; it is a control-plane/configuration twin.
6. B — Rail-optimized design puts the same GPU index of every node on the same leaf.
7. B — PFC frames *received* on an uplink come from the spine pausing the leaf.
8. B — Cumulus ≤5.12 restarts `switchd` when AR is applied; 5.13+ only reloads it.

## References
- NVIDIA Spectrum-X Networking Platform — https://www.nvidia.com/en-us/networking/spectrumx/
- Cumulus Linux User Guide: RDMA over Converged Ethernet (RoCE) — https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/Layer-1-and-Switch-Ports/Quality-of-Service/RDMA-over-Converged-Ethernet-RoCE/
- NVUE Command Reference: Adaptive Routing — https://docs.nvidia.com/networking-ethernet-software/nvue-reference/Set-and-Unset-Commands/Adaptive-Routing
- Cumulus Linux User Guide: Ethernet Virtual Private Network (EVPN) — https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/Network-Virtualization/Ethernet-Virtual-Private-Network-EVPN/
- NVIDIA SN5600 / SN5400 datasheets — https://www.nvidia.com/en-us/networking/ethernet-switching/spectrum-sn5000/
- NVIDIA Air — https://air.nvidia.com/ and https://docs.nvidia.com/networking-ethernet-software/nvidia-air/
- NVIDIA DGX SuperPOD Reference Architecture (Spectrum-X) — https://docs.nvidia.com/dgx-superpod/
- NVIDIA Academy course: Spectrum-X
- Related modules: [`../03-rdma/03-roce-a-to-z.md`](../03-rdma/03-roce-a-to-z.md), [`02-cumulus-linux-essentials.md`](02-cumulus-linux-essentials.md), [`05-netq-deployment-and-installation.md`](05-netq-deployment-and-installation.md), [`../05-bluefield-doca/01-bluefield-dpu-administration.md`](../05-bluefield-doca/01-bluefield-dpu-administration.md)

---
## Solution guide — Hands-on assignment

Spines first (simplest), then leaf01, then leaf02 as a near-copy; then verify each acceptance criterion.

### Spine01 (spine02: hostname, loopback 10.0.0.2, router-id)

```bash
nv set system hostname spine01
nv set interface lo ip address 10.0.0.1/32
nv set interface swp1-2 link state up                # swp1 -> leaf01, swp2 -> leaf02
nv set interface swp1-2 ip ipv4 forward on
nv set router bgp autonomous-system 65000            # both spines share ASN 65000
nv set router bgp router-id 10.0.0.1
nv set vrf default router bgp peer-group fabric remote-as external
nv set vrf default router bgp neighbor swp1 peer-group fabric
nv set vrf default router bgp neighbor swp2 peer-group fabric
nv set vrf default router bgp address-family ipv4-unicast network 10.0.0.1/32
nv set vrf default router bgp address-family l2vpn-evpn enable on                      # EVPN AF so Type-2/5 are reflected between leaves
nv set vrf default router bgp peer-group fabric address-family l2vpn-evpn enable on
nv set qos roce mode lossless                        # Requirement 4 on spines too
nv set router adaptive-routing enable on
nv set interface swp1-2 router adaptive-routing enable on
nv set interface swp1-2 router adaptive-routing link-utilization-threshold 70
nv config apply -y
nv config save
# Absent on purpose: nv set evpn enable on, VRFs, VNIs — spines are underlay + EVPN route exchange only (last acceptance criterion)
```

### Leaf01

Run §3 Steps 1–4 with `swp61-62` in place of `swp61-64` (two neighbor lines, two AR lines), applying and hitting each checkpoint. Then give tenantB the same anycast gateway tenantA has:

```bash
nv set interface vlan200 ip vrr address 10.200.1.254/24
nv set interface vlan200 ip vrr mac-address 00:00:5e:00:01:02
nv set vrf tenantB router bgp router-id 10.0.0.11
nv config apply -y
nv config save
```

### Leaf02

Identical to leaf01 with: hostname `leaf02`, loopback/router-id `10.0.0.12`, ASN `65012` (default and per-VRF BGP), SVI addresses `10.100.1.2/24` and `10.200.1.2/24`. VRR addresses and MACs stay *identical* — that is what makes the gateway anycast.

### Verifying each acceptance criterion

**All 8 underlay sessions Established** — `nv show vrf default router bgp neighbor` on each of the four switches shows two Established; 2+2+2+2 = 8.

**Remote VTEP visible** — on leaf01 `nv show evpn vni`: VNI 10100 and 10200 each list `10.0.0.12` under Remote-VTEPs. If empty, check `nv show vrf default router bgp neighbor swp61` for `l2vpn-evpn` prefixes received > 0.

**Per-VRF isolation (Type-5)** — on leaf02:
```bash
nv show vrf tenantA router rib ipv4        # expect 10.100.1.0/24; nothing from tenantB
nv show vrf tenantB router rib ipv4        # expect 10.200.1.0/24; 10.100.1.0/24 must be ABSENT
nv show vrf tenantA router bgp address-family l2vpn-evpn route   # [5]:[0]:[24]:[10.100.1.0], RD from 10.0.0.11, L3VNI 100100
```
The 10.200.x Type-5 carries VNI 100200 and imports only into tenantB because the route-target differs.

**RoCE + AR on all four** — `nv show qos roce` → `Mode: lossless`; `nv show router adaptive-routing` → enabled with the fabric ports. Record both from every switch.

**Spines clean** — `nv show vrf` on a spine lists only `default` and `mgmt`; `nv show evpn` reports not configured.

**Telemetry proof** — on each leaf `nv show interface swp61 qos roce counters` (in Air: zeros, profile present), then `sudo netq config add server 10.10.0.100 port 31980 vrf mgmt && sudo netq config restart agent`.

**The "what Air cannot verify" paragraph:** Air has no Spectrum ASIC, so PFC/ECN counters stay at zero, AR is accepted but never sprays, and there are no WJH drops or buffer histograms. Air proves the control plane (BGP, EVPN Type-2/5 exchange, VRF isolation, syntax) and the automation; lossless behaviour and AR gain need SN5600 hardware, SuperNIC hosts and an `ib_write_bw`/NCCL test (Module 8.x).

**Deliverables:** `nv config show -o yaml > /home/cumulus/$(hostname).yaml` on each switch, plus the show outputs above.
