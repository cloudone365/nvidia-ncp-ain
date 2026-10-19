# Module 4.1 — Spectrum-X: AI-Optimized Ethernet

> **Exam domain:** NVIDIA Spectrum Networking (30%)  
> **Source course(s):** Spectrum-X (NVIDIA Academy)  
> **Source docs:** NVIDIA Spectrum-X Networking Platform documentation; Cumulus Linux User Guide — RoCE, Adaptive Routing, EVPN; SN5600/SN5400 datasheets; NVIDIA Air documentation  
> **Estimated study time:** 5–6 hours

## 1. Learning objectives

By the end of this module you will be able to…

- **Describe** what differentiates the Spectrum-X platform (Spectrum-4 switch + BlueField-3 SuperNIC + software stack) from "plain" data-center Ethernet. *(Spectrum Networking — platform architecture)*
- **Explain** the four classic failure modes of Ethernet under AI collective traffic (ECMP hash collisions, elephant flows, incast, tail latency) and name the Spectrum-X mechanism that addresses each. *(Spectrum Networking — AI traffic characteristics)*
- **Configure** lossless RoCE, ECN/PFC trust and **adaptive routing** on a Spectrum switch running Cumulus Linux using **NVUE** (`nv set` / `nv show` / `nv config apply`). *(Spectrum Networking — RoCE configuration)*
- **Verify** RoCE and adaptive-routing state with `nv show qos roce`, per-interface RoCE counters and `nv show router adaptive-routing`. *(Troubleshooting Tools — Ethernet verification)*
- **Configure** a multi-tenant BGP-EVPN/VXLAN fabric with per-tenant VRFs on Spectrum-X and **explain** how it delivers performance isolation. *(Spectrum Networking — EVPN multi-tenancy)*
- **Describe** the role of NVIDIA Air (digital twin) in validating a Spectrum-X design before deployment, and state what it cannot validate. *(Automation and Configuration — validation)*
- **Sketch** a **rail-optimized** Spectrum-X leaf/spine reference topology and compare 2:1 oversubscribed vs non-blocking designs. *(AI Data Center Design — reference architectures)*

## 2. Key technical concepts

### 2.1 What Spectrum-X is (and is not)

*Analogy:* a Formula 1 car is not just an engine. Spectrum-X is not just a switch — it is the engine (Spectrum-4 ASIC), the tyres that keep it on the track at speed (the BlueField-3 SuperNIC), and the telemetry pit wall watching every lap (NetQ/WJH). Take any one away and you are back to a fast road car.

Spectrum-X is NVIDIA's name for an *end-to-end* Ethernet platform tuned for AI training and inference clusters. It is not a single product. Three pieces must be present together:

| Layer | Component | Role |
|---|---|---|
| Switch | **Spectrum-4** ASIC in the **SN5600** (51.2 Tb/s, 64×800G OSFP) and **SN5400** (25.6 Tb/s, 400G) | Lossless RoCE, adaptive routing (packet spraying), telemetry-driven congestion signalling, deep shared buffer |
| Endpoint | **BlueField-3 SuperNIC** (400G) | Reorders sprayed packets in hardware (Direct Data Placement), runs the congestion-control algorithm, enforces per-tenant rate limits |
| Software | Cumulus Linux or NVIDIA SONiC on the switch; DOCA on the SuperNIC; **NetQ**/**WJH** telemetry; NVIDIA Air digital twin | Configuration, verification, observability |

The key insight for the exam: a Spectrum switch alone running standard RoCE is "good Ethernet". Spectrum-X exists because the switch and the NIC *cooperate* — the switch sprays and marks, the NIC reorders and throttles. Remove either half and you fall back to classic ECMP + DCQCN behaviour. See [`../05-bluefield-doca/01-bluefield-dpu-administration.md`](../05-bluefield-doca/01-bluefield-dpu-administration.md) for the SuperNIC side.

Why does NVIDIA position Spectrum-X against InfiniBand rather than replacing it? InfiniBand (Module 2.x) is still the reference fabric for the largest single-tenant training clusters. Spectrum-X targets *AI clouds*: environments that must run many tenants, reuse existing Ethernet operational tooling (BGP, EVPN, Ansible, syslog), and still achieve near-IB effective bandwidth. NVIDIA claims roughly 1.6× the effective bandwidth of traditional Ethernet for collective workloads; the exact number is marketing, but the *reason* for the gap is testable.

**Why the exam cares:** questions describe a mixed fleet (SN5600 + ConnectX-6 hosts, say) and ask why "Spectrum-X features" underperform. The answer is always that one half of the cooperating pair is missing.

#### Hardware quick reference

| Switch | ASIC | Capacity | Ports | Typical role |
|---|---|---|---|---|
| SN5600 | Spectrum-4 | 51.2 Tb/s | 64 × 800G OSFP (+1 mgmt SFP28); each 800G port breaks out to 2×400G or 4×200G | Spine and leaf in Spectrum-X SuperPOD; 128×400G leaf when broken out |
| SN5400 | Spectrum-4 | 25.6 Tb/s | 64 × 400G QSFP-DD (+ 2 × SFP28) | Leaf, storage, or smaller fabrics |
| SN4600/SN4700 | Spectrum-3 | 12.8 Tb/s | 64 × 200G / 32 × 400G | Prior generation; supports AR but not the full Spectrum-X feature set — verify against the Spectrum-X support matrix |

Port breakout is configured in NVUE exactly as on any Cumulus switch (Module 4.2): `nv set interface swp1 link breakout 2x` yields `swp1s0`/`swp1s1`. On the SN5600, a 400G breakout is how you connect 400G SuperNICs to an 800G leaf port.

### 2.2 The AI Ethernet problem — four failure modes

Classic data-center Ethernet was designed for many short, uncorrelated flows (web, storage, VM traffic). AI training traffic is the opposite: a small number of very large, highly synchronized flows generated by **NCCL** collectives (all-reduce, all-to-all, reduce-scatter). Four problems follow.

**ECMP hash collisions.** *Analogy:* four trucks arrive at a four-lane toll plaza, but every driver reads the same sign and picks the same lane. Three lanes sit empty while one backs up. A standard switch picks one equal-cost path per *flow* by hashing the 5-tuple. With a handful of flows per GPU pair and a handful of uplinks, it is statistically common for two or more elephant flows to hash to the same spine link while another sits idle. The fabric is non-blocking on paper and 50–60% utilized in practice.

**Elephant flows.** A single RDMA write of several gigabytes behaves as one flow. Flow-level load balancing cannot split it, so one link carries the burden for the life of the transfer. Resilient hashing and flowlet switching help marginally; they still bind a flow to one path at a time.

**Incast.** *Analogy:* every truck in the city arrives at one warehouse door in the same minute. In all-reduce, many senders target the same receiver in the same microsecond window. The receiver's leaf port is oversubscribed instantly. Without a lossless mechanism, packets drop; RoCE go-back-N retransmission then collapses throughput. With **PFC** alone, pause frames propagate upstream and create head-of-line blocking for unrelated tenants.

**Tail latency.** A collective finishes only when its *slowest* participant finishes. One congested path delays one GPU, which delays the whole step. In AI networks, the p99.9 latency matters more than the mean — a 1% slow-path event per iteration becomes a permanent tax on the job.

**Why the exam cares:** you will be asked to match a mechanism to a problem. AR fixes collisions and elephants. Congestion control plus lossless buffering fixes incast. Nothing "fixes" tail latency except removing the other three.

### 2.3 Adaptive routing (packet spraying + DDP)

*Analogy:* adaptive routing is a traffic cop at the toll plaza waving each truck — not each convoy — into whichever lane is emptiest right now. The SuperNIC is the receiving warehouse, which does not care in what order pallets arrive because each pallet is labelled with its exact shelf location.

**Adaptive routing** on Spectrum-4 forwards *per packet*, not per flow, across all equal-cost next hops, choosing the least-loaded egress based on real-time queue occupancy and the port's link-utilization threshold. This eliminates hash collisions and splits elephant flows across every uplink.

The obvious cost is reordering: packets of one RDMA flow arrive at the destination out of order. On a standard NIC, RoCE treats out-of-order arrival as loss and triggers go-back-N. The BlueField-3 SuperNIC solves this with **Direct Data Placement (DDP)**: because an RDMA packet carries the virtual address it belongs to, the SuperNIC writes each packet straight into the correct offset in GPU/host memory regardless of arrival order, then signals completion once the full message is present. No reorder buffer, no retransmit. This is why AR in Spectrum-X requires the SuperNIC — with a ConnectX-6 or third-party NIC you must either disable AR or accept the penalty.

Notes the exam likes:
- AR applies to RoCE traffic (identified by UDP port 4791 by default); non-RoCE traffic keeps flow-based ECMP.
- AR is not supported on SVIs, L3 subinterfaces, bonds or bond members — it is a physical L3 uplink feature.
- AR does not use resilient hashing; they are alternatives, not layers.

### 2.4 Congestion control (telemetry-based, switch-to-NIC)

*Analogy:* classic DCQCN is a driver who only slows down when they see brake lights ahead. Spectrum-X congestion control is a driver with a live traffic feed telling them exactly how full each stretch of road is.

Classic DCQCN relies on **ECN** marks in the IP header that the receiving NIC reflects back as CNPs. It reacts late and probes blindly. Spectrum-X adds in-band telemetry: the switch stamps congestion information (queue depth, link utilization) into RoCE packets as they traverse the fabric; the receiving SuperNIC returns that information to the sender, whose congestion-control engine (running on the SuperNIC's programmable datapath, not the host CPU) adjusts injection rate per flow within microseconds. Because the rate is set from *measured* fabric state rather than inferred from marks, flows converge faster and PFC is rarely triggered — PFC stays as a safety net, not the primary mechanism.

### 2.5 Lossless vs lossy — the decision

| Mode | Mechanism | When to use |
|---|---|---|
| `lossless` (default when you `nv set qos roce`) | **PFC** on switch-priority 3 (RoCE) + ECN marking; CNP on switch-priority 6 strict-priority | Classic RoCEv2 with any NIC; required when NICs do not run advanced congestion control |
| `lossy` | ECN only; no PFC; relies on NIC congestion control and, for Spectrum-X, telemetry-based CC + DDP tolerance | Spectrum-X with SuperNICs, where the goal is to avoid PFC head-of-line blocking entirely |
| `lossless-single-ipool` | Lossless with one shared ingress pool | Specific buffer tuning cases — check current docs |
| `lossy-multi-tc` | Lossy with multiple RoCE traffic classes | Multi-queue workloads — check current docs |

Exam framing: lossless means "never drop; pause instead"; lossy means "mark and let the endpoints slow down". Spectrum-X reference designs typically run *lossless* for safety with AR enabled, but NVIDIA's AI-cloud guidance increasingly favours lossy with SuperNIC congestion control. Know both; read the question for which NICs are present.

### 2.6 Performance isolation (multi-tenancy)

*Analogy:* a **VRF** is a separate building with its own address book. Tenants in different buildings can use the same room numbers without confusion, and a fire alarm in one building does not empty the other.

An AI cloud runs multiple jobs from multiple tenants on one fabric. One tenant's incast must not stretch another tenant's step time. Spectrum-X achieves isolation by combining:
- **Network segmentation** — BGP-EVPN with one VRF/L3VNI per tenant (§2.8).
- **Per-flow congestion control at the SuperNIC** — rate limits are applied to the misbehaving flow, not the shared port.
- **Shared-buffer architecture** — Spectrum-4's fully shared packet buffer lets any port absorb a burst instead of partitioning memory per port, so one tenant's burst does not starve another's lossless pool.
- **Traffic classes** — RoCE in its own lossless TC; CNP in a strict-priority TC; tenant management traffic in lossy TCs.

### 2.7 Telemetry: WJH and NetQ

**WJH** (What Just Happened) is a Spectrum ASIC feature that, on every drop or congestion event, exports the actual dropped packet header plus a *reason code* (e.g., "TTL expired", "ingress port ACL", "tail drop on TC3"). Combined with **NetQ** (Module 4.5), it turns "the job is slow" into "leaf03 swp12 TC3 dropped 1,204 packets from 10.1.3.7 because the PFC watchdog fired". Histogram telemetry on Cumulus (`nv set system telemetry ...`) samples queue occupancy and latency buckets continuously so you can see congestion forming before drops.

### 2.8 Multi-tenant BGP-EVPN with VRFs

*Analogy:* **EVPN** is a phone directory that every switch shares by BGP. Instead of flooding "who has MAC X?" into the fabric, each switch publishes its entries and reads everyone else's.

An AI cloud fabric is almost always a BGP-unnumbered IP underlay with an EVPN/VXLAN overlay. Each tenant gets a VRF (L3VNI) and optionally L2 VNIs. Spectrum-4 does VXLAN encapsulation/decapsulation, EVPN route handling and RoCE AR in hardware simultaneously; this is one of its selling points versus merchant silicon that disables features when VXLAN is active.

- **VXLAN** — MAC-in-UDP encapsulation (UDP 4789); the switch is the VTEP (NVE), identified by a loopback address.
- **EVPN** — BGP address-family (L2VPN EVPN) that distributes MAC/IP reachability. **Type-2** routes carry MAC (+IP) for hosts in an L2 VNI; **Type-5** routes carry IP prefixes between VTEPs for an L3 VNI (inter-subnet, per-VRF).
- **VRF per tenant** — each tenant's subnets live in a VRF mapped to one L3VNI; traffic between VTEPs within the tenant is routed symmetrically via the L3VNI.
- **Multihoming / ESI** — a host dual-attached to two leaves without MLAG; the leaves advertise an Ethernet Segment Identifier (EVPN Type-1/Type-4 routes) so remote VTEPs load-balance to both. NVIDIA's reference designs for GPU nodes usually single-attach each SuperNIC to one rail leaf (§2.10), so multihoming is more relevant for storage and management leaves.

For pure-L3 GPU fabrics many designs skip L2 VNIs entirely: each GPU-node interface lands on a /31 or /30 routed port in the tenant VRF and only Type-5 routes are exchanged. That removes BUM traffic and ARP suppression concerns and is the simplest fit for AR, which requires physical L3 interfaces on the uplinks anyway.

### 2.9 NVIDIA Air — the digital twin

**NVIDIA Air** (formerly Cumulus in the Cloud, sometimes referenced as DSX Air in Spectrum-X materials) is a hosted simulation platform that runs real Cumulus Linux, NVIDIA SONiC, NetQ and Ubuntu host images in a virtual topology you draw or import. For Spectrum-X it serves as a *digital twin*:

- Build the exact leaf/spine/host topology (including hostnames and interface names) before hardware arrives.
- Validate NVUE configurations, EVPN route exchange, BGP policy and Ansible playbooks (Module 6.1) — then push the identical YAML to production.
- Train operators and rehearse upgrades/failure scenarios (`nv action upgrade`, link flaps) safely.
- Limitations: no ASIC, so no real RoCE/AR/PFC data-plane behaviour, no WJH hardware drops, no performance numbers. Control-plane and configuration validation only. The exam tests this distinction.

Air exposes a REST API and SDK so a CI pipeline can spin up a twin, run the playbook, run `netq check bgp`/`netq check evpn`, and tear it down.

### 2.10 Reference topology: Spectrum-X SuperPOD

A GPU compute fabric in a DGX SuperPOD built on Spectrum-X is **rail-optimized**:

- Each DGX node has 8 GPUs, each paired with its own 400G SuperNIC (8 NICs/node).
- NIC *n* of every node in a scalable unit connects to leaf *n* ("rail *n*"). GPU 0 on every node shares leaf 0, GPU 1 shares leaf 1, etc.
- NCCL traffic between the same GPU index across nodes (the dominant pattern in ring/tree all-reduce) therefore stays on one leaf — one hop, no spine traversal, no AR needed. Cross-rail traffic goes leaf → spine → leaf and benefits from AR.
- A scalable unit of 32 DGX nodes × 8 NICs = 256 × 400G = 128 × 800G leaf downlinks (after breakout). With SN5600 leaves, one leaf per rail handles 32 nodes with 32 × 400G downlinks (16 × 800G ports broken out) and 16–32 × 800G uplinks.

| Design | Leaf downlink:uplink | Pros | Cons |
|---|---|---|---|
| Non-blocking (1:1) | 12.8T down / 12.8T up on SN5600 | Any-to-any full bandwidth; simplest to reason about for all-to-all workloads | Twice the spine ports/optics; higher cost |
| 2:1 oversubscribed | 2× more downlink than uplink | Cheaper; acceptable when rail-optimized traffic stays local | All-to-all (e.g., MoE expert parallel) suffers; AR becomes essential |

Spine count = uplinks per leaf for full ECMP spread; with AR the spines are used as a single aggregate pool so the fabric behaves close to non-blocking even at 2:1 for typical collectives. The management/storage fabric is a separate, conventional Ethernet network (often also Spectrum but not Spectrum-X RoCE AR). See [`../01-ai-datacenter-design/`](../01-ai-datacenter-design/) for the full SuperPOD RA.

## 3. Live-style walkthrough — one Spectrum-X leaf from factory default

Watch closely. We are going to take `leaf01`, an SN5600 fresh out of the box running Cumulus Linux 5.x, and bring it to a working Spectrum-X leaf: breakout for SuperNICs, BGP unnumbered underlay, EVPN with a tenant VRF, lossless RoCE, adaptive routing and telemetry. All commands are NVUE. Legacy NCLU (`net add ...`) is deprecated; do not use it on Spectrum-4 platforms.

```text
                  spine01    spine02    spine03    spine04
                     |          |          |          |
                 swp61      swp62      swp63      swp64        (800G uplinks)
                     \          |          |          /
                      +--------- leaf01 (SN5600) ----+
                     /                                \
                 swp1s0                             swp1s1       (400G breakout)
                   |                                   |
             GPU node, SuperNIC (tenant A)      GPU node, SuperNIC (tenant B)
```

### Step 1 — Identity and breakout

```bash
# Set the hostname. Everything in NetQ and syslog keys off this.
nv set system hostname leaf01

# Loopback: this becomes the BGP router-id AND the VTEP source address later.
nv set interface lo ip address 10.0.0.11/32

# Break the 800G OSFP port swp1 into two 400G lanes for two SuperNICs.
# Expect the ASIC port map to change; on many platforms this reloads switchd.
nv set interface swp1 link breakout 2x
# Result: swp1 disappears as a usable port; swp1s0 and swp1s1 appear.

# Review before touching the running system — pending vs applied.
nv config diff
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
# Apply with --assume-yes so no prompt interrupts. Breakout may take ~30 s.
nv config apply -y

# Checkpoint: do the sub-ports exist?
nv show interface swp1s0 link
```

Good looks like: `swp1s0` with `speed 400G`, `admin up` (or down until we enable it), `fec rs`. If you see only `swp1`, the breakout did not take — check `journalctl -u switchd`.

### Step 2 — BGP unnumbered underlay

```bash
# Bring the four 800G uplinks up.
nv set interface swp61-64 link state up

# Unnumbered: no IPv4 address. IPv4 forwarding is enabled so RFC 5549 routes resolve via IPv6 link-local next hops.
nv set interface swp61-64 ip ipv4 forward on

# One ASN per leaf. Spines share their own ASN.
nv set router bgp autonomous-system 65011
nv set router bgp router-id 10.0.0.11

# Peer-group "underlay": remote-as external = "any ASN that is not mine" — identical on every leaf.
nv set vrf default router bgp peer-group underlay remote-as external

# Bind each uplink to the peer-group. These are interface neighbors, hence unnumbered.
nv set vrf default router bgp neighbor swp61 peer-group underlay
nv set vrf default router bgp neighbor swp62 peer-group underlay
nv set vrf default router bgp neighbor swp63 peer-group underlay
nv set vrf default router bgp neighbor swp64 peer-group underlay

# Advertise the loopback so remote VTEPs can reach our VTEP source address.
nv set vrf default router bgp address-family ipv4-unicast network 10.0.0.11/32

# Turn on the EVPN address family globally and on the peer-group.
nv set vrf default router bgp address-family l2vpn-evpn enable on
nv set vrf default router bgp peer-group underlay address-family l2vpn-evpn enable on

nv config apply -y
```

Checkpoint — are the sessions up?

```bash
nv show vrf default router bgp neighbor
```

```text
Neighbor  Remote-AS  State        Uptime    MsgRcvd  MsgSent  PfxRcvd  PfxSent
--------  ---------  -----------  --------  -------  -------  -------  -------
swp61     65000      Established  00:01:12  14       13       4        1
swp62     65000      Established  00:01:10  14       13       4        1
swp63     65000      Established  00:01:11  13       13       4        1
swp64     65000      Established  00:01:09  13       13       4        1
```

Watch the `Remote-AS` column: you never typed 65000. `remote-as external` learned it from the OPEN message. If a session sits in `Active`, the spine side is usually missing the interface neighbor or IPv6 RA is disabled.

### Step 3 — RoCE lossless and adaptive routing

```bash
# ONE command installs the whole RoCE QoS profile:
#   DSCP 26 -> switch-priority 3 (RoCE), DSCP 48 -> switch-priority 6 (CNP),
#   PFC enabled on SP3, ECN min/max thresholds on the RoCE TC, shared-buffer lossless pool,
#   ETS bandwidth split, and an LLDP application TLV so DCBX-capable NICs learn the priority.
nv set qos roce mode lossless
# Alternative for a SuperNIC-only fabric going PFC-free: nv set qos roce mode lossy

# Adaptive routing: global enable first...
nv set router adaptive-routing enable on
# ...then per physical L3 uplink. NOT on bonds, SVIs or subinterfaces — apply fails.
nv set interface swp61-64 router adaptive-routing enable on
# In Cumulus 5.15+ the per-interface keyword is "state enabled" instead of "enable on";
# the NVUE reference lists both forms. Use the one your release accepts.
# Utilization above which AR treats a port as congested and prefers other members. Default 70%.
nv set interface swp61-64 router adaptive-routing link-utilization-threshold 70

nv config diff      # expect qos.roce, router.adaptive-routing and the four interfaces
nv config apply -y  # On Cumulus <=5.12 this RESTARTS switchd (data-plane outage); on 5.13+ it reloads. Plan a window on older releases.
```

Checkpoint — verify RoCE:

```bash
nv show qos roce
```

Expect `State: enabled`, `Mode: lossless`, the PFC section listing priority 3 with Rx/Tx enabled, the congestion-control section showing `ECN` on the RoCE TC with min/max thresholds and marking probability, `Trust: L3 (DSCP)`, and the DSCP→SP and SP→TC tables. Then per port:

```bash
nv show interface swp61 qos roce status        # PFC/ECN per TC on this port
nv show interface swp61 qos roce counters      # PFC pause rx/tx, ECN-marked packets, drops
nv show interface qos-roce-counters            # all ports at once
nv action clear interface swp61 qos roce counters   # zero them before a test
```

Reading the counters: a rising `pfc tx` on a leaf *downlink* means the host NIC is being paused — the host is the congestion point. A rising `pfc rx` on an *uplink* means the spine is pausing *you*; look upstream.

Checkpoint — verify AR:

```bash
nv show router adaptive-routing
nv show interface swp61 router adaptive-routing
```

Good: `enable on` globally, each uplink `enable on` with `link-utilization-threshold 70`.

If you later need to tune beneath the profile (set RoCE first, then override — the profile replaces existing QoS):

```bash
# Move RoCE to switch-priority 2 instead of 3 (must update mapping and egress queue too)
nv set qos pfc default-global switch-priority 2
nv set qos mapping default-global dscp 26 switch-priority 2
nv set qos egress-queue-mapping default-global switch-priority 2 traffic-class 3

# ECN thresholds are in bytes; defaults vary by platform — check the doc before quoting a number.
nv set qos congestion-control default-global traffic-class 3 min-threshold 150000
nv set qos congestion-control default-global traffic-class 3 max-threshold 1500000
nv set qos congestion-control default-global traffic-class 3 probability 100
# Longer cable -> more bytes in flight -> more PFC headroom.
nv set qos pfc default-global cable-length 100
# Guarantee the RoCE TC half the link under contention.
nv set qos egress-scheduler default-global traffic-class 3 bw-percent 50
```

Interface-level RoCE (apply only to some ports) exists in newer releases; verify in current docs for your Cumulus version.

### Step 4 — Overlay: VTEP, EVPN, tenant A

```bash
# The VTEP sources VXLAN from the loopback we advertised in Step 2.
nv set nve vxlan source address 10.0.0.11
# Answer ARP/ND locally from EVPN Type-2 routes instead of flooding.
nv set nve vxlan arp-nd-suppress on
nv set evpn enable on

# --- Tenant A: VRF + L3VNI + one L2 VNI ---
nv set vrf tenantA                                   # the "separate building"
nv set vrf tenantA evpn vni 100100                   # L3VNI: Type-5 routes for this tenant ride here
nv set bridge domain br_default vlan 100 vni 10100   # L2 VNI for VLAN 100 (Type-2 routes)
nv set interface vlan100 ip address 10.100.1.1/24    # SVI = tenant gateway on this leaf
nv set interface vlan100 ip vrf tenantA              # SVI lives in the tenant's building
nv set interface vlan100 ip vrr address 10.100.1.254/24     # anycast gateway (VRR) shared with the other leaves
nv set interface vlan100 ip vrr mac-address 00:00:5e:00:01:01
# Per-VRF BGP instance: redistribute connected so the SVI subnet becomes a Type-5 route.
nv set vrf tenantA router bgp autonomous-system 65011
nv set vrf tenantA router bgp router-id 10.0.0.11
nv set vrf tenantA router bgp address-family ipv4-unicast redistribute connected enable on
nv set vrf tenantA router bgp address-family l2vpn-evpn enable on

# --- Tenant B, same pattern ---
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

Checkpoint — verify the overlay:

```bash
nv show evpn                                            # VTEP, VNIs
nv show evpn vni
nv show vrf tenantA router bgp address-family l2vpn-evpn route   # type-2/type-5 routes
nv show vrf tenantA router rib ipv4
nv show nve vxlan
```

Illustrative `nv show evpn vni`:

```text
VNI     Type  VRF      VLAN  Flood-mode  Remote-VTEPs
------  ----  -------  ----  ----------  -------------------------
10100   L2    tenantA  100   head-end    10.0.0.12, 10.0.0.13
10200   L2    tenantB  200   head-end    10.0.0.12
100100  L3    tenantA  -     -           -
100200  L3    tenantB  -     -           -
```

Good: L2 VNIs list remote VTEPs; L3 VNIs are present with the right VRF. Under the hood this is FRR: `sudo vtysh -c "show bgp l2vpn evpn summary"` gives the same data.

### Step 5 — Telemetry

```bash
nv set system telemetry export otlp state enabled          # OpenTelemetry export (5.x) — verify
nv set system telemetry histogram ingress-buffer ...        # buffer histograms — see NVUE reference
nv set system telemetry histogram egress-buffer ...
nv config apply -y
nv show system telemetry
```

Histogram collection samples queue depth into buckets at microsecond granularity and is the data source for NetQ's congestion views. Exact object names differ between releases; the exam cares that *Spectrum provides hardware-timestamped histograms and WJH drop reasons*, not the precise NVUE path. Point the NetQ agent at your server (`sudo netq config add server <ip> port 31980 vrf mgmt`, Module 4.5) and WJH will flow.

## 4. Troubleshooting / common gotchas

### Gotcha 1 — AR enabled, NCCL got slower

**Symptom:** after `nv set router adaptive-routing enable on`, all-reduce bandwidth drops 30–50%; `ethtool -S` on hosts shows `out_of_sequence` climbing.

**Why it happens:** the hosts have ConnectX-6/7 or third-party NICs, not SuperNICs. AR sprays per packet; without DDP the NIC treats reordering as loss and triggers go-back-N.

**How to debug:**
```bash
nv show router adaptive-routing                   # confirm AR is on
ibv_devinfo | grep -E 'hca_id|board_id'           # on host: is it a BlueField-3 SuperNIC?
ethtool -S ens3f0np0 | grep out_of_sequence        # rising = reordering penalty
```

**Fix:** disable AR on the uplinks serving non-SuperNIC hosts (`nv unset interface swp61-64 router adaptive-routing`), or replace the NICs. AR is a *pair* feature.

### Gotcha 2 — `nv config apply` for AR fails on a bond

**Symptom:** `nv set interface bond1 router adaptive-routing enable on` → apply error.

**Why it happens:** AR is only supported on physical L3 interfaces — not bonds, bond members, SVIs or subinterfaces.

**How to debug:** `nv config diff` shows the offending path; the apply error names the interface type.

**Fix:** `nv unset interface bond1 router adaptive-routing`, then apply AR on the physical uplinks. For GPU fabrics use routed physical uplinks, no bonds.

### Gotcha 3 — Custom DSCP map vanished after enabling RoCE

**Symptom:** you had `nv set qos mapping default-global dscp 46 switch-priority 5` for voice/management; after `nv set qos roce mode lossless` it is gone.

**Why it happens:** `nv set qos roce` *replaces* the QoS profile.

**How to debug:** `nv show qos mapping default-global` — your DSCP 46 entry is missing; `nv config history` shows the RoCE revision.

**Fix:** set RoCE first, then re-apply overrides in the same or a later revision.

### Gotcha 4 — Apply caused a 20-second outage

**Symptom:** enabling AR on a leaf dropped every BGP session and host link for ~20 s.

**Why it happens:** on Cumulus ≤5.12 applying AR restarts `switchd`; 5.13+ only reloads it.

**How to debug:** `nv show system` for the version; `sudo journalctl -u switchd` shows the restart timestamp lining up with the blip.

**Fix:** upgrade to 5.13+ or schedule AR changes in a maintenance window, one leaf at a time.

## 5. Hands-on assignment — two-tenant Spectrum-X leaf/spine

**Scenario.** You are building the first scalable unit of an AI cloud. Two tenants (`tenantA`, `tenantB`) must share a 2-leaf / 2-spine Spectrum-X fabric with full performance isolation. Each leaf has two 400G SuperNIC downlinks (one per tenant) on a broken-out 800G port and two 800G uplinks. Use NVIDIA Air (or real gear) for the control plane.

**Requirements**
1. Hostnames `leaf01`, `leaf02`, `spine01`, `spine02`; loopbacks 10.0.0.11/.12 (leaves), 10.0.0.1/.2 (spines).
2. BGP unnumbered underlay: leaves ASN 65011/65012, spines ASN 65000, peer-group based, EVPN address family enabled everywhere.
3. On each leaf, `swp1` broken out 2x; `swp1s0` → tenantA (VLAN 100 / VNI 10100 / L3VNI 100100), `swp1s1` → tenantB (VLAN 200 / VNI 10200 / L3VNI 100200). Anycast gateway via VRR on both leaves.
4. Lossless RoCE profile and adaptive routing enabled on every leaf and spine fabric port, threshold 70.
5. Each tenant's prefix appears as an EVPN Type-5 route *only* in its own VRF on the remote leaf.
6. Telemetry proof: RoCE counters and AR state captured on each leaf; NetQ agent pointed at a server.

**Deliverables**
- Four NVUE YAML files (`nv config show -o yaml`), one per switch.
- A verification log containing `nv show vrf default router bgp neighbor`, `nv show evpn vni`, `nv show vrf tenantA router bgp address-family l2vpn-evpn route`, `nv show qos roce`, `nv show router adaptive-routing` from each leaf.
- One paragraph explaining what you could *not* verify in Air and why.

**Acceptance criteria**
- All 8 underlay sessions Established.
- `nv show evpn vni` on leaf01 lists 10.0.0.12 as a remote VTEP for VNI 10100 and 10200.
- leaf02's `tenantA` RIB contains 10.100.1.0/24 learned via EVPN; its `tenantB` RIB does *not*.
- `nv show qos roce` shows `Mode: lossless` on all four switches; `nv show router adaptive-routing` shows enabled with all fabric ports listed.
- Spines have no VRF/VNI configuration (pure underlay + EVPN route reflection is acceptable).

### Hint
- Build in this order: underlay → verify sessions → RoCE/AR → verify → overlay → verify. Do not skip a verification checkpoint.
- Spines only need the EVPN address family on the peer-group; they do not need `nv set evpn enable on` unless you want them as VTEPs.
- The redistribution that turns a tenant SVI subnet into a Type-5 route happens in the *per-VRF* BGP instance, not the default VRF.
- `remote-as external` on both sides means you never type the neighbour's ASN. If a session stays `Active`, check IPv6 link-local and the interface neighbor on the *other* end.
- Air has no ASIC: `nv show qos roce` will show the profile, but PFC counters will stay at zero. That is your paragraph.

## Appendix A — Quick command / config reference

| Task | NVUE |
|---|---|
| Enable lossless RoCE profile | `nv set qos roce mode lossless` |
| Enable lossy (ECN-only) RoCE | `nv set qos roce mode lossy` |
| Remove RoCE profile | `nv unset qos roce` |
| Show RoCE profile | `nv show qos roce` |
| Per-port RoCE status / counters | `nv show interface swpN qos roce status` / `... counters` |
| Clear RoCE counters | `nv action clear interface swpN qos roce counters` |
| AR global | `nv set router adaptive-routing enable on` |
| AR per interface | `nv set interface swpN router adaptive-routing enable on` (5.15+: `state enabled`) |
| AR congestion threshold | `nv set interface swpN router adaptive-routing link-utilization-threshold 70` |
| Show AR | `nv show router adaptive-routing` |
| PFC priority | `nv set qos pfc default-global switch-priority 3` |
| ECN thresholds | `nv set qos congestion-control default-global traffic-class 3 min-threshold <bytes>` |
| RoCE TC bandwidth | `nv set qos egress-scheduler default-global traffic-class 3 bw-percent 50` |
| VTEP | `nv set nve vxlan source address <lo>` |
| EVPN | `nv set evpn enable on` |
| Tenant VRF + L3VNI | `nv set vrf T` / `nv set vrf T evpn vni <vni>` |
| L2 VNI | `nv set bridge domain br_default vlan 100 vni 10100` |
| BGP unnumbered | `nv set vrf default router bgp neighbor swp61 remote-as external` |
| Breakout 800G→2×400G | `nv set interface swp1 link breakout 2x` |
| Telemetry | `nv set system telemetry export otlp state enabled` (verify) / `nv show system telemetry` |

## Appendix B — Exam traps & key takeaways

- Spectrum-X = Spectrum-4 switch (SN5600/SN5400) + BlueField-3 SuperNIC + Cumulus/SONiC + DOCA + NetQ, engineered as one system. A Spectrum switch alone is not Spectrum-X.
- AR with packet spraying on a non-SuperNIC endpoint causes out-of-order RoCE and go-back-N retransmits; DDP on the SuperNIC makes reordering harmless.
- AR spreads load across *paths*; incast is many sources into *one destination port* — only congestion control / lossless buffering addresses it.
- AR is unsupported on bonds, SVIs or subinterfaces. Physical L3 uplinks only.
- `nv set qos roce` *replaces* the QoS profile. Set RoCE first, then override.
- Lossless ≠ "no congestion". Lossless means PFC pause instead of drop; lossy + good CC is NVIDIA's direction with SuperNICs.
- Applying AR on Cumulus ≤5.12 restarts `switchd`; 5.13+ reloads. Know the boundary.
- NVIDIA Air validates control plane and config, not RoCE/AR performance — there is no ASIC in the twin.
- Type-2 = MAC/IP (L2 VNI); Type-5 = IP prefix (L3 VNI). A pure-L3 GPU fabric uses only Type-5.
- Rail-optimized means GPU *n* of every node on leaf *n*, not "one leaf per node"; AR makes 2:1 oversubscription workable.
- SN5600: 64×800G OSFP, 51.2T; SN5400: 64×400G + 2×SFP28, 25.6T.
- `nv show interface swpN qos roce counters`: PFC *tx* on a downlink = host congested; PFC *rx* on an uplink = spine pausing you.
- NCLU `net add` is not an acceptable exam answer — NVUE is the supported CLI on Spectrum-4.

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

Work through the four switches in order: spines first (they are simplest), then leaf01, then leaf02 as a near-copy. Every block is followed by the verification that proves the acceptance criterion.

### Spine01 (spine02 is identical except hostname, loopback 10.0.0.2 and router-id)

```bash
nv set system hostname spine01
nv set interface lo ip address 10.0.0.1/32
# Two downlinks per spine in this small lab: swp1 -> leaf01, swp2 -> leaf02
nv set interface swp1-2 link state up
nv set interface swp1-2 ip ipv4 forward on
nv set router bgp autonomous-system 65000          # both spines share ASN 65000
nv set router bgp router-id 10.0.0.1
nv set vrf default router bgp peer-group fabric remote-as external
nv set vrf default router bgp neighbor swp1 peer-group fabric
nv set vrf default router bgp neighbor swp2 peer-group fabric
nv set vrf default router bgp address-family ipv4-unicast network 10.0.0.1/32
# EVPN AF on the peer-group so Type-2/Type-5 routes are reflected between leaves
nv set vrf default router bgp address-family l2vpn-evpn enable on
nv set vrf default router bgp peer-group fabric address-family l2vpn-evpn enable on
# Requirement 4: RoCE + AR on the spine fabric ports too
nv set qos roce mode lossless
nv set router adaptive-routing enable on
nv set interface swp1-2 router adaptive-routing enable on
nv set interface swp1-2 router adaptive-routing link-utilization-threshold 70
nv config apply -y
nv config save
```

Note what is *absent*: no `nv set evpn enable on`, no VRF, no VNI. Spines are pure underlay plus EVPN route exchange. That satisfies the last acceptance criterion.

### Leaf01

```bash
nv set system hostname leaf01
nv set interface lo ip address 10.0.0.11/32
nv set interface swp1 link breakout 2x                     # Requirement 3
nv set interface swp61-62 link state up                    # two uplinks: swp61 -> spine01, swp62 -> spine02
nv set interface swp61-62 ip ipv4 forward on
nv set router bgp autonomous-system 65011
nv set router bgp router-id 10.0.0.11
nv set vrf default router bgp peer-group underlay remote-as external
nv set vrf default router bgp neighbor swp61 peer-group underlay
nv set vrf default router bgp neighbor swp62 peer-group underlay
nv set vrf default router bgp address-family ipv4-unicast network 10.0.0.11/32
nv set vrf default router bgp address-family l2vpn-evpn enable on
nv set vrf default router bgp peer-group underlay address-family l2vpn-evpn enable on
nv config apply -y
nv show vrf default router bgp neighbor                    # CHECKPOINT: both Established before continuing
```

```bash
# Requirement 4
nv set qos roce mode lossless
nv set router adaptive-routing enable on
nv set interface swp61-62 router adaptive-routing enable on
nv set interface swp61-62 router adaptive-routing link-utilization-threshold 70
nv config apply -y
nv show qos roce                                            # CHECKPOINT: Mode lossless
nv show router adaptive-routing                             # CHECKPOINT: enabled, swp61-62 listed
```

```bash
# Overlay
nv set nve vxlan source address 10.0.0.11
nv set nve vxlan arp-nd-suppress on
nv set evpn enable on

# Tenant A
nv set vrf tenantA
nv set vrf tenantA evpn vni 100100
nv set bridge domain br_default vlan 100 vni 10100
nv set interface vlan100 ip address 10.100.1.1/24
nv set interface vlan100 ip vrf tenantA
nv set interface vlan100 ip vrr address 10.100.1.254/24
nv set interface vlan100 ip vrr mac-address 00:00:5e:00:01:01
nv set vrf tenantA router bgp autonomous-system 65011
nv set vrf tenantA router bgp router-id 10.0.0.11
nv set vrf tenantA router bgp address-family ipv4-unicast redistribute connected enable on
nv set vrf tenantA router bgp address-family l2vpn-evpn enable on

# Tenant B
nv set vrf tenantB
nv set vrf tenantB evpn vni 100200
nv set bridge domain br_default vlan 200 vni 10200
nv set interface vlan200 ip address 10.200.1.1/24
nv set interface vlan200 ip vrf tenantB
nv set interface vlan200 ip vrr address 10.200.1.254/24
nv set interface vlan200 ip vrr mac-address 00:00:5e:00:01:02
nv set vrf tenantB router bgp autonomous-system 65011
nv set vrf tenantB router bgp router-id 10.0.0.11
nv set vrf tenantB router bgp address-family ipv4-unicast redistribute connected enable on
nv set vrf tenantB router bgp address-family l2vpn-evpn enable on

# Downlinks
nv set interface swp1s0 bridge domain br_default access 100
nv set interface swp1s1 bridge domain br_default access 200
nv config apply -y
nv config save
```

### Leaf02

Identical to leaf01 with these substitutions: hostname `leaf02`, loopback/router-id `10.0.0.12`, ASN `65012`, SVI addresses `10.100.1.2/24` and `10.200.1.2/24` (the VRR addresses `10.100.1.254` / `10.200.1.254` and VRR MACs stay *identical* — that is what makes the gateway anycast). The `nv set vrf tenantA router bgp autonomous-system` line uses `65012`.

### Verifying each acceptance criterion

**All 8 underlay sessions Established** — on each spine `nv show vrf default router bgp neighbor` shows two Established; on each leaf, two. 2+2+2+2 = 8.

**Remote VTEP visible** — on leaf01:
```bash
nv show evpn vni
```
Expect VNI 10100 and 10200 each listing `10.0.0.12` under Remote-VTEPs. If empty, check `nv show vrf default router bgp neighbor swp61` for `l2vpn-evpn` prefixes received > 0.

**Per-VRF isolation (Type-5)** — on leaf02:
```bash
nv show vrf tenantA router rib ipv4        # expect 10.100.1.0/24 (local) AND nothing from tenantB
nv show vrf tenantB router rib ipv4        # expect 10.200.1.0/24; 10.100.1.0/24 must be ABSENT
nv show vrf tenantA router bgp address-family l2vpn-evpn route
```
In the EVPN route table look for `[5]:[0]:[24]:[10.100.1.0]` with route-distinguisher from 10.0.0.11 and the L3VNI 100100 — that is the Type-5 route from leaf01. The equivalent 10.200.x route carries VNI 100200 and is imported only into tenantB because the route-target differs.

**RoCE + AR on all four** — `nv show qos roce` → `Mode: lossless`; `nv show router adaptive-routing` → enabled with the fabric ports. Record both from every switch into the verification log.

**Spines clean** — `nv show vrf` on a spine lists only `default` and `mgmt`; `nv show evpn` reports EVPN disabled/not configured.

**Telemetry proof** — on each leaf:
```bash
nv show interface swp61 qos roce counters       # in Air: all zeros, profile present
sudo netq config add server 10.10.0.100 port 31980 vrf mgmt
sudo netq config restart agent
```

**The "what Air cannot verify" paragraph** should state: Air has no Spectrum ASIC, so RoCE PFC/ECN counters stay at zero, adaptive routing is accepted in configuration but never sprays packets, there are no WJH drop records and no buffer histograms. Air proves the control plane (BGP, EVPN Type-2/Type-5 exchange, VRF isolation, configuration syntax) and the automation; proving lossless behaviour and AR gain requires SN5600 hardware with SuperNIC-equipped hosts and an `ib_write_bw`/NCCL test (Module 8.x).

### Exporting the deliverables

```bash
nv config show -o yaml > /home/cumulus/$(hostname).yaml     # one per switch
```
Collect the four files plus the show outputs above into your submission.
