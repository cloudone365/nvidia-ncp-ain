# Module 1.2 — DGX SuperPOD and BasePOD Reference Architectures

> **Exam domain:** AI Data Center Design and Optimization (5%)  
> **Source course(s):** AI Infrastructure and Operations Fundamentals; InfiniBand Professional (topology sections)  
> **Source docs:** NVIDIA DGX SuperPOD Reference Architecture — Scalable Infrastructure with DGX H100 / H200 / B200 / GB200; NVIDIA DGX BasePOD Reference Architecture; NVIDIA Base Command Manager documentation  
> **Estimated study time:** 3 hours

## Learning objectives
- Describe the network layout of a DGX H100/H200/B200 node: which NIC serves which fabric and how many ports face each network.
- Distinguish a DGX BasePOD from a DGX SuperPOD in scale, fabric choice, and management.
- Explain the Scalable Unit (SU) concept and derive leaf, spine, and cable counts for a given number of SUs.
- Draw and explain a **rail-optimized** topology and state why it gives one-hop GPU-to-GPU paths on the same rail.
- Calculate oversubscription for a leaf-spine (Clos/fat-tree) fabric and recognize a non-blocking design.
- Compare the InfiniBand Quantum-2 and Spectrum-X compute-fabric options and the storage/management fabric choices.
- Identify cable and transceiver types (OSFP, twin-port, DAC, AOC) used in the RA and the distance constraints that drive them.
- Describe the role of **Base Command Manager (BCM)** in a SuperPOD.

## 1. The DGX node as a network endpoint

Every reference architecture starts from the node. Learn the H100 layout cold; later generations follow the same pattern.

### 1.1 DGX H100 / H200
| Component | Count | Fabric | Port type |
|---|---|---|---|
| H100/H200 GPU | 8 | NVLink/NVSwitch (internal) | — |
| **ConnectX-7** HCA, 400G NDR (compute) | 8 (one per GPU) | Compute fabric | 4 × OSFP twin-port cages on the rear (each cage carries 2 × 400G ports) |
| **BlueField-3** DPU, dual-port 400G | 2 | Storage + in-band management | 2 × 400G per card (typically one port each for storage, in-band — verify in RA) |
| BMC | 1 | OOB management | 1G RJ45 |
| Optional 1G/10G host NICs | 1–2 | In-band (alternative) | RJ45 |

The key design intent: **one compute NIC per GPU, physically adjacent on the PCIe topology** so GPUDirect RDMA traffic never crosses a CPU socket or PCIe switch unnecessarily. `nvidia-smi topo -m` shows this as `PIX` or `PXB` affinity between GPU *n* and `mlx5_n`.

### 1.2 DGX B200 and GB200
- **DGX B200**: same 8-GPU air-cooled form factor; 8 × ConnectX-7 400G compute NICs and 2 × BlueField-3 for storage/in-band in the first B200 SuperPOD RA (verify — later revisions move to ConnectX-8).
- **DGX GB200 NVL72**: a liquid-cooled rack of 18 compute trays (each 2 Grace CPUs + 4 Blackwell GPUs), all 72 GPUs in one NVLink domain via NVLink Switch trays. Each compute tray has 4 × **ConnectX-7** or **ConnectX-8** compute ports (one per GPU) and BlueField-3 for storage/in-band. The *rack* becomes the scale-up domain, and the compute fabric (Quantum-X800 XDR 800G or Spectrum-X800) connects racks. Verify exact per-tray NIC model and rate in the current GB200 RA.

Whatever the generation, the invariant for the network engineer is: **number of compute fabric ports = number of GPUs**, and the fabric is designed around that count.

## 2. BasePOD vs SuperPOD

| | DGX BasePOD | DGX SuperPOD |
|---|---|---|
| Positioning | Enterprise entry/mid-scale AI infrastructure | Turnkey, validated supercomputer-class AI factory |
| Scale | Typically 2–16 DGX nodes (verify current RA ceiling) | Built in Scalable Units; H100 RA scales 1–4 SUs (up to 127 nodes / 1,016 GPUs); larger with core layer |
| Compute fabric | InfiniBand (Quantum-2) *or* Ethernet (Spectrum) — customer choice | InfiniBand Quantum-2 NDR (classic) or Spectrum-X (Ethernet variant) |
| Storage | Partner storage over Ethernet or IB | Certified high-performance parallel storage; dedicated storage fabric |
| Management | BCM | BCM; UFM for IB fabrics |
| Delivery | Reference design, partner-built | NVIDIA-validated design with strict component list and cabling plan |

Think of BasePOD as the design you adapt and SuperPOD as the design you replicate. Many SuperPOD exam questions are really SU-arithmetic questions.

## 3. The Scalable Unit (SU)

A **Scalable Unit** is the smallest repeatable block: a fixed number of nodes, their leaf switches, and the cabling between them. In the H100 SuperPOD RA, **an SU is 32 DGX H100 nodes (256 GPUs)**. The RA notes that in practice one node slot per SU is given up to connect the UFM appliances, so the tables show 31 nodes per SU (248 GPUs) and 127 nodes for 4 SUs. If an exam question says "32-node SU", that is the design figure; "31 nodes" is the as-built figure after UFM.

### 3.1 Why build by SU?
- The leaf layer is fully populated and symmetric, so adaptive routing and the SM's routing algorithms produce balanced paths.
- Cable schedules, rack elevations, and switch configurations are templated and reused.
- Validation (CVT, nccl-tests) is done per SU, then across SUs.
- NVIDIA guidance: build the fabric for a complete SU even if you deploy fewer nodes initially; adding nodes later then needs no re-cabling of the spine.

### 3.2 H100 SuperPOD compute fabric scaling table (from the RA)

| SUs | Nodes (as built) | GPUs | Leaf switches | Spine switches | Node→leaf cables (incl. UFM) | Leaf→spine cables |
|---|---|---|---|---|---|---|
| 1 | 31 | 248 | 8 | 4 | 252 | 256 |
| 2 | 63 | 504 | 16 | 8 | 508 | 512 |
| 3 | 95 | 760 | 24 | 16 | 764 | 768 |
| 4 | 127 | 1,016 | 32 | 16 | 1,020 | 1,024 |

Observe the formulas behind the table rather than memorizing it:

- **Leaf switches per SU = 8** (one per GPU rail).
- **Node→leaf cables per SU = nodes × 8** (plus 4 for UFM in the as-built design: 31 × 8 + 4 = 252).
- **Leaf→spine cables per SU = leaf count × uplinks per leaf = 8 × 32 = 256**.
- Spine count grows to keep the fabric non-blocking: each spine must have enough ports to take one uplink from every leaf. With 32 leaves at 4 SUs and 64 ports per spine, 16 spines × 64 ports = 1,024 = the 1,024 leaf→spine cables.

## 4. The switches

### 4.1 Quantum-2 QM9700 / QM9790
The H100-era compute fabric uses the **NVIDIA Quantum-2 QM9700** (managed) or QM9790 (unmanaged) switch:
- 64 × NDR 400G ports delivered through **32 OSFP cages**, each cage carrying a **twin-port** transceiver (2 × 400G).
- 51.2 Tb/s aggregate (verify), SHARPv3 in-network computing, adaptive routing.
- Also configurable as 128 × 200G (NDR200) by splitting each 400G port — common for connecting ConnectX-7 at 200G in BasePOD or storage designs.

In an SU, each leaf QM9700 uses **32 ports down** (one per node on its rail) and **32 ports up** to spines — a 1:1 split, hence non-blocking.

### 4.2 Spectrum-X alternative
A Spectrum-X SuperPOD replaces Quantum-2 with **Spectrum-4 SN5600** (64 × 800G OSFP, or 128 × 400G) leaf/spine switches running Cumulus Linux or SONiC, and ConnectX-7 with **BlueField-3 SuperNICs** in the nodes. The topology math is identical; the difference is the control plane (BGP EVPN/VXLAN or plain L3 underlay), congestion control (RoCE with **PFC**/**ECN** + SuperNIC-side DCQCN) and adaptive routing done by the Spectrum-4 ASIC with reordering in the SuperNIC. See [`../04-spectrum-ethernet/01-spectrum-x.md`](../04-spectrum-ethernet/01-spectrum-x.md).

### 4.3 Storage, in-band, OOB
| Fabric | H100 RA switch | Notes |
|---|---|---|
| Storage | **QM9700 (MQM9700-NS2F)** InfiniBand in the classic H100 RA; Spectrum SN4600/SN5600 Ethernet (RoCE) in Ethernet-storage variants | Per-node storage I/O target > 40 GB/s; storage side 1:1, DGX side approximately 4:3 oversubscribed per the RA |
| In-band management | **Spectrum SN4600C** (64 × 100G) | Cluster services, home directories, Slurm/K8s, BCM |
| OOB management | **Spectrum SN2201** (48 × 1G RJ45 + 4 × 100G) | BMCs, switch mgmt ports, PDUs, storage controllers |

## 5. Rail-optimized topology

This is the single most-asked design concept in the domain. Read it twice.

### 5.1 The idea
In a conventional leaf-spine, every NIC in a *rack* connects to the rack's leaf (top-of-rack). In a **rail-optimized** design, every NIC of the *same index* across all nodes in the SU connects to the *same* leaf. GPU 0 of every node → leaf 0; GPU 1 of every node → leaf 1; … GPU 7 → leaf 7. Each such set is a **rail**.

```text
                         Spine 0      Spine 1      Spine 2      Spine 3
                        ┌──────┐     ┌──────┐     ┌──────┐     ┌──────┐
                        └──┬┬┬─┘     └──┬┬┬─┘     └──┬┬┬─┘     └──┬┬┬─┘
      (every leaf has 32 uplinks spread evenly across the spines)
   ┌───────────┬───────────┬───────────┬── ... ──┬───────────┐
┌──┴───┐   ┌───┴──┐   ┌────┴─┐   ┌────┴─┐       ┌────┴─┐
│Leaf 0│   │Leaf 1│   │Leaf 2│   │Leaf 3│  ...  │Leaf 7│      <- one leaf per RAIL
│rail 0│   │rail 1│   │rail 2│   │rail 3│       │rail 7│
└┬┬┬┬┬─┘   └┬┬┬┬┬─┘   └┬┬┬┬┬─┘   └┬┬┬┬┬─┘       └┬┬┬┬┬─┘
 │││││      │││││      │││││      │││││          │││││
 32 cables  32 cables  32 cables  32 cables      32 cables     <- one from each node
 │││││      │││││      │││││      │││││          │││││
 ┌─────────────────────────────────────────────────────────────┐
 │ Node 00:  GPU0-NIC0  GPU1-NIC1  GPU2-NIC2  GPU3-NIC3 ... GPU7-NIC7 │
 │ Node 01:  GPU0-NIC0  GPU1-NIC1  GPU2-NIC2  GPU3-NIC3 ... GPU7-NIC7 │
 │   ...                                                          │
 │ Node 31:  GPU0-NIC0  GPU1-NIC1  GPU2-NIC2  GPU3-NIC3 ... GPU7-NIC7 │
 └─────────────────────────────────────────────────────────────┘
       │          │          │          │                 │
     rail 0     rail 1     rail 2     rail 3    ...     rail 7
   (all NIC0s  (all NIC1s
    → Leaf 0)   → Leaf 1)
```

### 5.2 Why it works
- **Same-rail traffic is one hop.** GPU 3 on node 5 talking to GPU 3 on node 20 enters Leaf 3 and exits Leaf 3 — never touches a spine. With 32 nodes per SU, each GPU can reach the same-rank GPU on 31 other nodes in one switch hop.
- **NCCL exploits it.** NCCL's ring and tree algorithms, combined with **PXN** (PCI × NVLink), arrange that inter-node traffic is sent from GPU *n* to GPU *n* on the peer node — same rail. Traffic that must change rail is first moved over NVLink inside the node (to the GPU that owns the correct rail), then sent. Spine traffic is thereby minimized. See [Module 1.3](03-gpu-to-gpu-communication-nccl-gpudirect.md).
- **Spine bandwidth is still full.** The design remains a non-blocking fat-tree, so cross-rail traffic (which does traverse spines) is not penalized; rail optimization is an optimization *on top of* non-blocking, not a substitute for it.
- **Failure domains line up with rails.** Losing Leaf 2 removes rail 2 from the whole SU, which NCCL can route around via NVLink to other rails, rather than removing all 8 NICs of four racks of nodes.

### 5.3 Physical consequence
Because every node has a cable to each of eight leaves, nodes and leaves cannot be in the same rack in a neat ToR fashion; leaf switches sit in a central network rack per SU and the node→leaf cables are 10–50 m. That is why the RA specifies **active optical cables or transceivers + fiber**, not DAC, for compute links.

## 6. Fat-tree / Clos math

A **fat-tree** (folded Clos) is non-blocking when, at every switch, the bandwidth facing down equals the bandwidth facing up.

```text
Oversubscription ratio = (total downlink bandwidth) / (total uplink bandwidth)
                       = (downlink ports × rate) / (uplink ports × rate)
```

| Design | Down | Up | Ratio |
|---|---|---|---|
| SuperPOD compute leaf (QM9700) | 32 × 400G | 32 × 400G | 1:1 (non-blocking) |
| Hypothetical cost-reduced leaf | 48 × 400G | 16 × 400G | 3:1 |
| H100 RA storage, DGX side | per RA | per RA | ≈ 4:3 |

Two-tier (leaf-spine) limit with *P*-port switches: maximum non-blocking endpoints = P²/2. For QM9700 (P = 64): 64²/2 = 2,048 endpoints, i.e. 256 nodes × 8 GPUs = 8 SUs, with 32 spines... but the H100 RA stops at 4 SUs / 16 spines at two tiers and adds a **core** layer (three-tier fat-tree) for larger builds. Verify the exact tier breakpoints in the RA for the generation you are studying; the exam is more likely to test the *principle* (adding a tier when spine ports run out) than the exact number.

Three-tier rule of thumb: leaf↔spine inside a pod/SU-group stays as designed; each spine then splits its uplinks to core switches, and the number of core switches = (spine uplinks per spine × number of spines) / ports per core.

## 7. Compute fabric choice: InfiniBand vs Spectrum-X

| Criterion | Quantum-2 InfiniBand | Spectrum-X Ethernet |
|---|---|---|
| Loss model | Lossless by credit-based flow control at L2 | Lossless via PFC + ECN/DCQCN, tuned end-to-end with the SuperNIC |
| Load balancing | Adaptive routing in switch; SM-computed routes | Spectrum-4 adaptive routing with SuperNIC reordering (RoCE AR) |
| In-network compute | **SHARP** (reductions in switch) | SHARP support on Spectrum-X announced; verify current status |
| Fabric control | Subnet Manager (**UFM** appliance in SuperPOD) assigns **LID**s, computes routing | Distributed: BGP underlay, EVPN optional; NetQ for visibility |
| Multi-tenancy | **PKey** partitions | VLAN/VXLAN, VRFs |
| Tooling | ibdiagnet, UFM, mlxlink | NVUE, NetQ, WJH, mlxlink |
| When chosen | Highest performance, HPC-style single-tenant | Operators standardized on Ethernet, cloud/multi-tenant, convergence with existing NOS skills |

Both fabrics use the same topology (rail-optimized, non-blocking fat-tree) and the same port count per node; the choice does not change the cable math.

## 8. Storage fabric

Training nodes read datasets and write checkpoints. Checkpoints of large models are hundreds of GB and are written by every node near-simultaneously, so storage I/O is bursty and high-bandwidth. The RA sets a per-node target of **> 40 GB/s** for the H100 SuperPOD.

- Protocol: RDMA — **RoCEv2** on Ethernet or native InfiniBand — to parallel or scale-out filesystems: **Lustre**, **IBM Storage Scale (GPFS)**, **WEKA**, **VAST**, **DDN**, NFS-over-RDMA for home directories.
- Node side: ports on the two **BlueField-3** cards. GPUDirect Storage (GDS) lets the filesystem client DMA directly into GPU memory.
- Fabric: a separate leaf-spine (not rail-optimized; conventional). Storage-side 1:1, compute-side modest oversubscription acceptable because storage I/O is not bulk-synchronous in the same way as collectives.
- Why a separate fabric: a checkpoint storm must not share buffers with an all-reduce.

## 9. Management networks

- **In-band**: Ethernet on the BlueField-3 (or host 1/10/25G NIC in BasePOD). Carries BCM provisioning (PXE, image deploy), Slurm/Kubernetes control, monitoring, user SSH/Jupyter, container registry pulls. Routed to the corporate network.
- **OOB**: 1G Ethernet from every BMC, switch management port, PDU, storage controller to SN2201 switches. Isolated; used by BCM for power control (IPMI/Redfish) and console.
- **UFM appliances** attach to the compute IB fabric (hence the "31 of 32" node slot) and to in-band Ethernet for their web UI/API.

## 10. Cabling

| Item | Detail |
|---|---|
| **OSFP** | The form factor for 400G NDR / 800G ports. Switch side uses finned (top) OSFP; NIC side uses flat (riding heatsink) OSFP. They are not interchangeable — verify part numbers. |
| **Twin-port transceiver** | A single OSFP module carrying 2 × 400G NDR lanes. On the QM9700 one cage = 2 ports. A twin-port switch transceiver connects to *two* ConnectX-7 single-port transceivers via a 2-way breakout fiber (MPO-12/APC). |
| **DAC** (direct attach copper) | Passive copper, up to ~2–3 m at NDR; used only for very short in-rack links (e.g. leaf↔spine in adjacent racks is usually too far). Lowest power and latency. |
| **ACC** (active copper) | Active copper, extends copper to ~3–5 m. |
| **AOC** (active optical cable) | Transceivers permanently attached to fiber; typical for node→leaf at 10–30 m in BasePOD. |
| **Transceiver + MMF/SMF fiber** | Separate transceivers and structured fiber; the SuperPOD default because it allows patching and long runs (up to 50 m MMF, 500 m+ SMF). |
| Cable count formula | node→leaf = GPUs (8 × nodes); leaf→spine = leaves × 32; storage and in-band per node = BlueField-3 ports used. |

Cabling validation with **mlxcables** (read transceiver EEPROM, type, length) and **mlxlink** (link state, BER, eye margins) is covered in the Troubleshooting modules.

## 11. Base Command Manager

**NVIDIA Base Command Manager (BCM)** is the cluster management software for BasePOD and SuperPOD (derived from Bright Cluster Manager). Its role:

- Bare-metal provisioning: DHCP/PXE, node images with DGX OS, drivers, MOFED/DOCA-OFED, CUDA, NCCL.
- Node categories and software images so all 127 nodes are identical.
- Integration with Slurm and/or Kubernetes (deploys the control plane and GPU/Network Operators).
- Monitoring and health checks; power control via the OOB network.
- `cmsh` CLI and Base View GUI; head node is highly available (two head nodes).

BCM is *not* the InfiniBand fabric manager — that is **UFM** (or opensm). The exam likes to separate these roles.

## Configuration / command reference

```bash
# Verify the node's compute NICs and their GPU affinity before cabling to rails
ibdev2netdev -v                      # mlx5_X <-> netdev, PCI address, link state
nvidia-smi topo -m                   # GPU0 <-> mlx5_0 should show PIX/PXB

# Confirm a leaf really is one rail: every neighbor port on the leaf should be the
# same HCA index (mlx5_N) on different nodes
iblinkinfo -S <leaf-switch-GUID>     # list links on one switch with remote node names

# Dump topology and compare against the RA cable plan
ibnetdiscover > topo.txt
ibdiagnet --topo_file ra_topology.topo   # check cabling against a reference topology (verify flag)

# Check cable/transceiver inventory
mlxcables                            # per-cable vendor, PN, length, type
mlxlink -d mlx5_0 -m -e              # module info + eye/BER on a given HCA port

# Spectrum-X leaf: ports and breakout (NVUE)
nv set interface swp1-32 link speed 400G
nv set interface swp1 link breakout 2x400G   # verify syntax for 800G OSFP breakout on SN5600
nv config apply

# BCM: list nodes and categories
cmsh -c "device list"
cmsh -c "category list"
```

Design formulas (memorize these, not the table):

```text
leaves_per_SU         = GPUs_per_node                     (8)
node_to_leaf_cables   = nodes × GPUs_per_node             (per SU: 32 × 8 = 256)
leaf_to_spine_cables  = leaves × uplinks_per_leaf         (per SU: 8 × 32 = 256)
spines_needed         = (total_leaves × uplinks_per_leaf) / ports_per_spine
oversubscription      = downlink_bw / uplink_bw           (1:1 = non-blocking)
max 2-tier endpoints  = P² / 2   for P-port switches
```

## Common pitfalls & exam traps
- Confusing *rail-optimized* with *rack-based ToR*. In rail-optimized designs a leaf serves one NIC index across all nodes, not all NICs of one rack.
- Saying rail optimization makes the fabric cheaper by reducing spines. It does not; the fabric stays non-blocking. It reduces spine *traffic* (and latency), not spine *count*.
- 32 vs 31 nodes per SU: 32 is the design, 31 is as-built after reserving a slot for UFM connectivity in the H100 RA.
- Twin-port OSFP: one switch cage = two 400G ports. A 64-port QM9700 has 32 cages. Questions about "ports" and "cages" are not the same question.
- Assuming compute and storage share the BlueField-3. Compute uses ConnectX-7; BlueField-3 is storage/in-band.
- BCM vs UFM: BCM manages nodes and software; UFM manages the IB fabric (SM, telemetry).
- Non-blocking vs oversubscribed storage: the RA tolerates ≈4:3 on the storage fabric DGX side but never on the compute fabric.
- DAC for compute links: at 400G NDR, DAC reach is only a few meters; rail-optimized cabling distances require optics.
- Forgetting the GB200 shift: the NVLink domain becomes the rack (72 GPUs), so the scale-out fabric connects racks, with one compute port per GPU still.

## Key takeaways
- DGX H100: 8 GPUs, 8 × ConnectX-7 400G compute (4 twin-port OSFP cages), 2 × BlueField-3 for storage/in-band, 1 BMC.
- SU (H100 RA) = 32 nodes design / 31 as built, 8 leaf switches, 256 node→leaf and 256 leaf→spine cables.
- Spines: 4 (1 SU), 8 (2 SUs), 16 (3–4 SUs) with QM9700 64-port switches.
- Rail-optimized = GPU *n* of every node → leaf *n*; same-rail inter-node traffic is one hop.
- Compute fabric is a non-blocking fat-tree: 32 down / 32 up per leaf.
- Oversubscription = downlink bandwidth / uplink bandwidth; 1:1 is non-blocking.
- Spectrum-X uses the same topology with SN5600 + BlueField-3 SuperNIC, RoCE, PFC/ECN, AR.
- Storage fabric is separate (RoCE or IB via BlueField-3), > 40 GB/s per node target, may be ≈4:3 on the node side.
- In-band = SN4600C class Ethernet; OOB = SN2201 1G for BMC/switch mgmt/PDU.
- BCM provisions and manages nodes; UFM manages the IB fabric.

## Review questions

1. A customer is building a 2-SU DGX H100 SuperPOD. How many compute leaf switches and how many leaf→spine cables are required?  
   A. 8 leaves, 256 cables  
   B. 16 leaves, 512 cables  
   C. 16 leaves, 256 cables  
   D. 32 leaves, 1,024 cables

2. In a rail-optimized SU, GPU 5 on node 12 sends data to GPU 5 on node 27. What path does the traffic take?  
   A. Leaf 12 → spine → leaf 27  
   B. Leaf 5 only (one hop)  
   C. NVLink to GPU 0, then leaf 0 → spine → leaf 0  
   D. Any leaf via adaptive routing

3. A leaf switch has 48 × 400G ports connected to servers and 16 × 400G ports to spines. What is its oversubscription ratio?  
   A. 1:1  
   B. 2:1  
   C. 3:1  
   D. 4:1

4. Which node component carries storage traffic in the DGX H100 SuperPOD RA?  
   A. ConnectX-7 compute HCAs  
   B. BlueField-3 DPU ports  
   C. The BMC port  
   D. NVSwitch

5. Why does the H100 RA list 31 nodes per SU in its tables when the SU is defined as 32 nodes?  
   A. One node is a hot spare.  
   B. One node slot's leaf ports are used to connect the UFM appliances.  
   C. The 32nd node is the BCM head node.  
   D. Leaf switches only have 31 downlink ports.

6. What does a twin-port OSFP transceiver on a QM9700 provide?  
   A. One 800G port  
   B. Two independent 400G NDR ports from a single cage  
   C. Redundant 400G links to the same destination  
   D. A 400G port with a backup lane

7. Which statement correctly separates BCM and UFM?  
   A. BCM manages the IB subnet; UFM provisions nodes.  
   B. BCM provisions and manages nodes and software; UFM runs the Subnet Manager and fabric telemetry.  
   C. They are the same product under different names.  
   D. UFM is only used in Spectrum-X designs.

8. A design team proposes a rail-optimized leaf layer but a 2:1 oversubscribed spine to save cost, arguing that rail optimization keeps most traffic off the spines. What is the best technical response?  
   A. Accept; NCCL guarantees no cross-rail traffic.  
   B. Reject; rail optimization reduces spine traffic for same-rail patterns, but cross-rail, all-to-all, and multi-SU traffic still require a non-blocking spine for predictable collective performance.  
   C. Accept only if SHARP is enabled.  
   D. Reject; rail-optimized designs do not use spines.

### Answer key
1. B — 8 leaves per SU × 2 = 16 leaves; each leaf has 32 uplinks → 512 cables.
2. B — Same NIC index on every node lands on the same leaf; same-rail traffic never leaves the leaf.
3. C — 48 × 400 / 16 × 400 = 3:1.
4. B — BlueField-3 cards serve storage and in-band; ConnectX-7 is compute only.
5. B — The RA footnote states one DGX slot is removed to accommodate UFM connectivity.
6. B — Twin-port OSFP carries two 400G NDR ports; 32 cages give 64 ports.
7. B — BCM is node/cluster management; UFM is the InfiniBand fabric manager.
8. B — Rail optimization is an optimization on top of a non-blocking fabric; cross-rail and all-to-all traffic still crosses spines.

## References
- NVIDIA DGX SuperPOD Reference Architecture: Scalable Infrastructure with DGX H100 — Network Fabrics — https://docs.nvidia.com/dgx-superpod/reference-architecture-scalable-infrastructure-h100/latest/network-fabrics.html
- NVIDIA DGX SuperPOD RA — Key Components — https://docs.nvidia.com/dgx-superpod/reference-architecture-scalable-infrastructure-h100/latest/dgx-superpod-components.html
- NVIDIA DGX SuperPOD RA with DGX H200 / DGX B200 / DGX GB200 — https://docs.nvidia.com/dgx-superpod/
- NVIDIA DGX BasePOD Reference Architecture — https://docs.nvidia.com/dgx-basepod/reference-architecture-infrastructure-foundation-enterprise-ai/latest/
- NVIDIA Quantum-2 QM9700/QM9790 Switch User Manual — https://docs.nvidia.com/networking/
- NVIDIA Spectrum SN5600 / SN4600C / SN2201 product briefs — https://www.nvidia.com/en-us/networking/ethernet-switching/
- NVIDIA Base Command Manager documentation — https://docs.nvidia.com/base-command-manager/
- NVIDIA LinkX cables and transceivers (OSFP, twin-port) — https://docs.nvidia.com/networking/display/linkx
- Related modules: [1.1 AI Infrastructure Fundamentals](01-ai-infrastructure-and-operations-fundamentals.md), [1.3 NCCL and GPUDirect](03-gpu-to-gpu-communication-nccl-gpudirect.md), [InfiniBand administration](../02-infiniband/), [Spectrum-X](../04-spectrum-ethernet/)
