# Module 1.1 — AI Infrastructure and Operations Fundamentals

> **Exam domain:** AI Data Center Design and Optimization (5%)  
> **Source course(s):** AI Infrastructure and Operations Fundamentals (NVIDIA Academy)  
> **Source docs:** NVIDIA DGX SuperPOD Reference Architecture (H100/H200/B200/GB200); NVIDIA Spectrum-X Networking Platform documentation; NVIDIA BlueField DPU documentation  
> **Estimated study time:** 2.5 hours

## Learning objectives
- Describe the "AI factory" model and explain how training and inference workloads place different demands on the network.
- Explain why AI back-end traffic (elephant flows, low entropy, synchronous collectives, tail-latency sensitivity) breaks the assumptions of classic enterprise/data-center Ethernet designs.
- Distinguish the four fabrics in an NVIDIA reference design — compute, storage, in-band management, out-of-band management — and state what traffic belongs on each.
- Differentiate scale-up (NVLink/NVSwitch) from scale-out (InfiniBand or Spectrum-X Ethernet) communication and identify where each applies.
- Contrast east-west and north-south traffic in an AI cluster and explain why AI designs are east-west dominated.
- Summarize the power, cooling and rack-density constraints that drive AI data-center physical design.
- Outline the operations lifecycle of an AI cluster (bring-up, validation, monitoring) and name the NVIDIA tools used at each stage.
- Describe where the BlueField DPU fits in an AI node and which functions it offloads.

## 1. The AI factory

NVIDIA uses the term **AI factory** for a data center whose output is not web pages or transactions but *tokens* and *trained model weights*. The analogy is deliberate: raw material (data) enters, energy is consumed, and a product (intelligence, delivered as inference) leaves. The practical consequence for a network engineer is that the whole facility is optimized around a single, tightly coupled workload class instead of thousands of independent tenants. Utilization of the GPUs is the primary KPI; any network behavior that leaves GPUs idle — a slow link, a lost packet, a congested spine — is a direct hit to the factory's throughput.

An AI factory is built from a small number of repeating blocks:

| Block | What it is | Example |
|---|---|---|
| Node | A server with (typically) 8 GPUs, NVLink-connected, with one compute NIC per GPU | DGX H100, DGX B200, HGX-based OEM server |
| Scalable Unit (SU) | A fixed group of nodes plus their leaf switches, designed once and repeated | 32 DGX nodes in the H100-era SuperPOD (see [Module 1.2](02-dgx-superpod-basepod-reference-architectures.md)) |
| Pod / SuperPOD | Multiple SUs under one spine (and optionally core) layer | 4 SUs ≈ 127 nodes, ~1,016 GPUs |
| Fabrics | Separate physical networks for compute, storage, in-band and OOB management | InfiniBand Quantum-2 or Spectrum-X for compute |

Because the blocks repeat, the exam tends to ask *formula* questions (how many leaf switches for N SUs) rather than one-off trivia. Learn the structure, not the numbers.

## 2. Training versus inference traffic

The two phases of the AI lifecycle generate very different traffic.

### 2.1 Training
Training a large model means splitting the model and/or the data across hundreds or thousands of GPUs and keeping them synchronized. Every iteration (a few hundred milliseconds to seconds) ends with a **collective operation** — most commonly an all-reduce of gradients — in which every GPU must exchange data with many others before the next iteration can begin. The characteristics:

- **Bulk-synchronous.** All participants wait at a barrier; the iteration finishes when the *slowest* transfer finishes.
- **Very large transfers.** Gradient buffers are hundreds of MB to tens of GB per GPU per step.
- **Predictable, repetitive patterns.** The same pairs of GPUs talk to each other in the same order every iteration.
- **Long-lived jobs.** Days to weeks, so a rare failure (link flap, silent packet loss) will eventually be hit.

### 2.2 Inference
Inference serves requests: a prompt comes in over the front-end (north-south) network, the model produces tokens, the response goes out. Large models are still sharded across several GPUs (tensor parallelism within a node, sometimes pipeline parallelism across nodes), so there is east-west traffic, but it is smaller per request and latency-sensitive rather than bandwidth-hungry. Inference clusters care about:

- Request/response latency and tail latency (p99) per token.
- Many concurrent small flows rather than a few giant ones.
- KV-cache transfer between prefill and decode GPUs in disaggregated serving — a newer pattern that re-introduces large east-west transfers.
- Front-end load balancing and north-south capacity.

| Property | Training | Inference |
|---|---|---|
| Dominant direction | East-west (GPU↔GPU) | North-south + intra-node east-west |
| Flow size | Elephant flows (GB) | Mice to medium |
| Synchronization | Bulk-synchronous collectives | Independent requests |
| Sensitivity | Tail latency of the slowest flow; any packet loss | Per-request latency, jitter |
| Typical fabric | InfiniBand NDR/XDR or Spectrum-X | Spectrum-X / standard Ethernet, NVLink within node |

## 3. Why AI networking differs from enterprise networking

Candidates coming from a Cisco/Arista enterprise background should pay particular attention here; this is the conceptual core of the whole exam.

### 3.1 Elephant flows and low entropy
Classic data-center Ethernet relies on **ECMP** hashing of five-tuples (src/dst IP, protocol, src/dst port). With thousands of short web flows, statistical multiplexing spreads load evenly across spine links. AI traffic is the opposite: a node might have only 8 RDMA queue pairs active, each moving gigabytes. There are too few flows for hashing to balance them, and the flows are too big for a collision to be harmless. Two elephant flows hashed onto the same uplink will each get half the bandwidth for the whole collective, and the entire job waits for them. This is the **low-entropy** problem. NVIDIA's answers are **adaptive routing** (per-packet or per-flowlet spraying with reordering handled in the NIC — standard in InfiniBand, added to Ethernet by Spectrum-X) and rail-optimized topologies that keep most traffic a single hop away from the spine entirely.

### 3.2 Synchronous collectives and tail latency
An all-reduce across 1,024 GPUs completes when the last byte arrives at the last GPU. If 1,023 flows finish in 10 ms and one takes 30 ms because of a congested link, the effective collective time is 30 ms and 1,023 GPUs idle for 20 ms. Average throughput is irrelevant; the metric that matters is the **tail**. This is why AI fabrics are built *non-blocking* (1:1 oversubscription) and why congestion control (**ECN**/**PFC**/DCQCN on Ethernet, credit-based flow control on InfiniBand) is not optional.

### 3.3 Loss intolerance
RDMA transports (InfiniBand natively, RoCEv2 on Ethernet) move data directly from one GPU's memory to another's with no CPU involvement. The transport was designed for a lossless link layer; a single dropped packet forces a go-back-N retransmission that can stall a queue pair for milliseconds. Enterprise Ethernet tolerates loss as a congestion signal (TCP). AI Ethernet must either avoid loss entirely (**PFC**) or signal congestion before loss occurs (**ECN** marking → DCQCN rate reduction at the NIC). See [`../03-rdma/03-roce-a-to-z.md`](../03-rdma/03-roce-a-to-z.md).

### 3.4 Scale of bandwidth
Each GPU in a DGX H100 has a dedicated 400 Gb/s port; a single node therefore injects 3.2 Tb/s into the compute fabric. One SU of 32 nodes is ~100 Tb/s of injection bandwidth that the fabric must carry without oversubscription. Enterprise designs rarely exceed 25–100 Gb/s per server with 3:1 or higher oversubscription.

### 3.5 Summary table

| Enterprise/cloud DC assumption | AI back-end reality |
|---|---|
| Many short flows, high entropy | Few elephant flows, low entropy |
| ECMP hashing balances load | Hash collisions stall whole job; need adaptive routing |
| Oversubscription 3:1 or more acceptable | Non-blocking 1:1 fat-tree |
| Loss is a congestion signal | Loss is catastrophic; lossless or near-lossless required |
| Average latency matters | Tail latency (slowest flow) matters |
| Server NIC 25–100G | 400G–800G per GPU, 8 per node |
| CPU handles the transport (TCP) | RDMA; NIC moves data GPU-to-GPU |

## 4. The four fabrics

NVIDIA reference architectures physically separate traffic into four networks. Each has a distinct purpose, technology, and failure domain.

| Fabric | Purpose | Typical technology (H100-era SuperPOD) | Carries |
|---|---|---|---|
| **Compute** (back-end) | GPU-to-GPU collectives | InfiniBand Quantum-2 NDR 400G, or Spectrum-X Ethernet 400G with BlueField-3 **SuperNIC** | NCCL / RDMA traffic only |
| **Storage** | High-speed access to training data and checkpoints | Ethernet (RoCE) or InfiniBand, via BlueField-3 ports | NFS, Lustre, GPFS/Spectrum Scale, object |
| **In-band management** | Cluster services, OS provisioning, Slurm/Kubernetes control, user SSH, home directories | Ethernet (e.g. SN4600C) | Everything that is not RDMA data or storage |
| **Out-of-band (OOB) management** | BMC/IPMI/Redfish, switch management ports, PDUs | 1G Ethernet (e.g. SN2201) | Lights-out management |

Why separate them? Three reasons the exam likes:

1. **Isolation of performance.** A checkpoint write to storage must not create congestion on the compute fabric during a collective.
2. **Isolation of failure.** You can lose or reconfigure the in-band network without the running job's compute traffic being affected.
3. **Different protocol requirements.** The compute fabric needs lossless RDMA tuned for low entropy; the storage fabric needs RoCE but with many clients and normal flow mixes; management needs ordinary routed IP.

A DGX H100 node's physical ports map as: 8 × ConnectX-7 (OSFP, 400G) for compute; 2 × BlueField-3 (dual-port) for storage and in-band; 1 × 1G BMC port for OOB. Later nodes (B200, GB200) follow the same pattern with ConnectX-8 and higher link rates — verify exact NIC counts and rates in the current RA.

## 5. Scale-up vs scale-out

### 5.1 Scale-up: NVLink and NVSwitch
Inside a node (and, with GB200 NVL72, inside a rack) GPUs are connected by **NVLink**, a proprietary point-to-point interconnect, switched by **NVSwitch** so every GPU has full-bandwidth access to every other. Fourth-generation NVLink on H100 provides 900 GB/s per GPU of bidirectional bandwidth — far above the 50 GB/s (400 Gb/s) of a ConnectX-7 port. The NVLink domain is a shared-memory-like domain: GPUs can load/store into each other's memory. Collectives inside the NVLink domain are extremely fast and never touch the network engineer's switches.

### 5.2 Scale-out: InfiniBand or Spectrum-X
Beyond the NVLink domain, GPUs communicate over the compute fabric using RDMA. Two NVIDIA options:

| | InfiniBand (Quantum-2 / Quantum-X800) | Spectrum-X Ethernet (Spectrum-4 / Spectrum-X800 + BlueField-3 SuperNIC) |
|---|---|---|
| Link rate | NDR 400G (Quantum-2), XDR 800G (Quantum-X800) | 400G (Spectrum-4), 800G (Spectrum-X800) |
| Lossless by design | Yes (credit-based) | Via PFC/ECN + SuperNIC congestion control |
| Adaptive routing | Native | Spectrum-X AR with SuperNIC reordering |
| In-network compute | **SHARP** | SHARP on Spectrum-X (verify current support) |
| Fabric management | Subnet Manager (**UFM** / opensm) | Standard Ethernet control plane (BGP) + NetQ |
| Best fit | Maximum performance, HPC heritage | Ethernet-standard operations, multi-tenant cloud |

The exam expects you to know that the NCCL library automatically selects NVLink for intra-node and the network (IB or RoCE) for inter-node transfers — see [Module 1.3](03-gpu-to-gpu-communication-nccl-gpudirect.md).

### 5.3 The hierarchy
```text
GPU ── NVLink/NVSwitch ── 8 GPUs in a node         (scale-up, ~900 GB/s per GPU)
   ── NVLink Switch rack (GB200 NVL72) ── 72 GPUs  (scale-up, rack level)
   ── ConnectX / SuperNIC ── leaf ── spine ── core (scale-out, 400–800 Gb/s per GPU)
```
Good parallelism strategies place the most communication-intensive dimension (tensor parallelism) inside the NVLink domain and the less intensive dimensions (data, pipeline parallelism) across the fabric.

## 6. East-west vs north-south

In enterprise networks the dominant traffic is **north-south**: clients outside the DC talking to servers inside. In an AI training cluster essentially all traffic is **east-west**: GPU to GPU, node to node, node to storage. North-south is limited to job submission, monitoring, dataset ingest, and (for inference) serving requests.

Design consequences:
- The compute fabric has *no* north-south path at all in most designs; it is an isolated back-end network, often without IP routing to the outside world.
- Border/edge capacity is sized for ingest and serving, not for the GPU traffic.
- Security boundaries move: the back-end fabric is trusted and flat; segmentation, if any, is done per tenant with **PKey** (InfiniBand) or VLAN/VXLAN (Ethernet).

## 7. Power and cooling basics

A DGX H100 draws up to about 10.2 kW; a DGX B200 about 14.3 kW (verify in the current spec sheet). Four nodes per rack is the common H100 density, giving ~40 kW per rack — already beyond what most enterprise colos (5–10 kW per rack) provide. GB200 NVL72 racks approach 120 kW and are liquid-cooled.

Key points for the network engineer:

- **Rack density drives topology.** Few nodes per rack means leaf switches may be in separate network racks and compute cables run 30–100 m — which forces optical (AOC or transceiver) rather than DAC cabling. See [Module 1.2](02-dgx-superpod-basepod-reference-architectures.md).
- **Power sequencing.** Switches and management infrastructure must be on separate PDUs/circuits from the nodes so the fabric stays up when nodes are power-cycled.
- **Cooling for optics.** 400G/800G transceivers run hot (12–17 W each for 800G twin-port OSFP); switch airflow direction (port-side intake vs exhaust) must match the hot/cold aisle.
- **Liquid cooling.** GB200 and some B200 deployments use direct-to-chip liquid cooling for GPUs; switches (Quantum-X800, Spectrum-X800) also come in liquid-cooled variants.
- **PUE** (power usage effectiveness) is the facility efficiency metric; AI facilities target ~1.1–1.3.

## 8. Operations lifecycle

### 8.1 Bring-up
1. Physical install: racks, PDUs, cabling per the RA cable schedule; label every cable by rail and port.
2. OOB network first — BMCs, switch management ports — so everything else can be reached.
3. Switch OS and firmware (Cumulus Linux / SONiC on Spectrum; MLNX-OS on Quantum) at the RA-specified versions.
4. Fabric manager: for InfiniBand, bring up the Subnet Manager (**UFM** appliance or opensm) so **LID**s are assigned and routing tables are computed; for Spectrum-X, configure BGP underlay, PFC/ECN, and adaptive routing.
5. Node provisioning via **Base Command Manager (BCM)**: PXE boot, DGX OS image, driver stack (CUDA, MOFED/DOCA-OFED, NCCL), Slurm or Kubernetes.
6. NIC firmware and configuration with **mlxconfig** (link type IB vs ETH, SR-IOV, etc.).

### 8.2 Validation
Validation proves the fabric before handing it to users. Standard sequence:

| Layer | Tool | What it proves |
|---|---|---|
| Physical | **mlxlink**, **mlxcables**, **iblinkinfo** | Every link up at the expected rate/width, no high BER, correct optics |
| Fabric topology | **ibnetdiscover**, **ibdiagnet**, NetQ topology | Cabling matches the intended rail-optimized design |
| Point-to-point RDMA | **ib_write_bw**, **ib_write_lat**, **ibping** | Each NIC pair achieves line rate and expected latency |
| Collectives | **nccl-tests** (`all_reduce_perf`) | Full-cluster bus bandwidth close to theoretical |
| Full stack | **Cluster Validation Tool (CVT)**, HPL/NCCL burn-in | Sustained performance under load, no stragglers |

### 8.3 Monitoring
Day-two operations require telemetry at every layer:
- **UFM** for InfiniBand: link errors, congestion counters, topology changes, SM state.
- **NetQ** and **WJH (What Just Happened)** for Spectrum Ethernet: per-packet drop reasons, buffer occupancy, ECN marks.
- **DCGM** (Data Center GPU Manager) for GPU health, and its NIC/NVLink counters.
- BCM dashboards and Prometheus/Grafana for node and job-level metrics.
- Regular `ibdiagnet` runs diffed against a baseline to catch degraded links before they cause stragglers.

The recurring operational theme: **a slow link is worse than a dead link.** A dead link is routed around; a link with a high bit-error rate retransmits silently and makes every collective wait for it. This is why BER and symbol-error monitoring is emphasized in the Troubleshooting domain.

## 9. Where DPUs fit

The **BlueField-3 DPU** is a ConnectX-7-class NIC with embedded Arm cores, its own memory, and a programmable datapath, running the **DOCA** software stack. In the DGX reference design, BlueField-3 cards serve the storage and in-band management networks, not the compute fabric (which uses plain ConnectX-7 for minimum latency). In Spectrum-X designs the BlueField-3 **SuperNIC** variant *is* the compute NIC: it is a BlueField-3 tuned for RoCE performance (adaptive-routing packet reordering, congestion control, lower power) rather than for general offload.

Functions a DPU offloads from the host CPU:
- Storage initiator (NVMe-oF, RoCE to parallel filesystems).
- Networking: OVS/VXLAN, firewalling, telemetry — without consuming host cores or exposing the host to the tenant network.
- Infrastructure isolation: the DPU runs its own OS, so cloud/multi-tenant operators can manage the node even if the host is compromised or owned by a tenant.
- Precision timing, encryption (IPsec/TLS), and in Spectrum-X, the RoCE-side half of the end-to-end congestion control and adaptive routing scheme.

Exam framing: *compute fabric = ConnectX-7 (IB) or BlueField-3 SuperNIC (Spectrum-X); storage/in-band = BlueField-3 DPU.* Details in [`../05-bluefield-doca/`](../05-bluefield-doca/).

## Configuration / command reference

```bash
# Node-side first look at the fabric NICs
ibstat                        # IB port state, rate, LID, GUID for each HCA
ibdev2netdev                  # map mlx5_X devices to netdev names and link state
mlxconfig -d /dev/mst/mt4129_pciconf0 q | grep LINK_TYPE   # IB (1) vs ETH (2) per port
nvidia-smi topo -m            # GPU <-> NIC affinity matrix (NVLink, PIX, NODE, SYS)

# Fabric topology and health (InfiniBand)
ibnetdiscover                 # dump discovered topology
iblinkinfo                    # every link with width/speed/state
ibdiagnet                     # full fabric diagnostics report in /var/tmp/ibdiagnet2

# Point-to-point RDMA test (server then client)
ib_write_bw -d mlx5_0 -a --report_gbits
ib_write_bw -d mlx5_0 -a --report_gbits <server-ip>

# Collective validation (one process per GPU, 2 nodes)
mpirun -np 16 -H nodeA:8,nodeB:8 -x NCCL_DEBUG=INFO ./build/all_reduce_perf -b 8 -e 8G -f 2 -g 1

# Spectrum (Cumulus, NVUE) quick checks
nv show interface --view=brief
nv show interface swp1 counters
nv show qos roce
```

## Common pitfalls & exam traps
- Assuming ECMP hashing solves load balancing: with low-entropy elephant flows it does not; the answer the exam wants is adaptive routing and/or rail-optimized design.
- Confusing the SuperNIC with the DPU. Both are BlueField-3 silicon; the SuperNIC is the compute-fabric NIC for Spectrum-X, the DPU is used for storage/in-band and offload.
- Treating oversubscription as acceptable on the compute fabric. The RA compute fabric is non-blocking; the *storage* fabric may be modestly oversubscribed (the H100 RA cites roughly 4:3 on the DGX side).
- Mixing up NVLink and InfiniBand bandwidth. NVLink (scale-up) per-GPU bandwidth is an order of magnitude above the per-GPU network port. The network is never faster than NVLink.
- Thinking tail latency is a nice-to-have. In bulk-synchronous training the slowest flow sets job speed.
- Believing that RoCE tolerates loss "a little". Any loss triggers retransmission that stalls the queue pair; the design goal is loss-free (PFC) or loss-avoiding (ECN).
- Expecting north-south capacity to matter for training. It matters for inference serving and ingest, not for the back-end fabric.
- Forgetting the OOB network comes up first; bring-up cannot proceed without BMC and switch console access.

## Key takeaways
- An AI factory's KPI is GPU utilization; any network stall is lost production.
- Training traffic = few elephant flows, bulk-synchronous collectives, tail-latency bound, loss-intolerant.
- Inference traffic = many smaller latency-sensitive flows, north-south heavy, with intra-node tensor parallelism.
- Four fabrics: compute (IB/Spectrum-X), storage (RoCE/IB via BlueField), in-band management, OOB management.
- Scale-up = NVLink/NVSwitch inside node/rack; scale-out = InfiniBand or Spectrum-X across nodes.
- Compute fabric is non-blocking 1:1; low entropy demands adaptive routing, not ECMP alone.
- DGX H100: 8 × ConnectX-7 400G compute, 2 × BlueField-3 for storage/in-band, 1 BMC for OOB.
- Bring-up order: OOB → switches/SM → BCM provisioning → NIC config → validation (mlxlink → ibdiagnet → perftest → nccl-tests → CVT).
- A slow (high-BER) link is worse than a dead one; monitor error counters, not just link state.
- BlueField-3 DPU offloads storage/network/security; BlueField-3 SuperNIC is the Spectrum-X compute NIC.

## Review questions

1. A 512-GPU training job shows average link utilization of only 60% on the spine, yet the all-reduce step time is 3× the expected value. Which explanation is most consistent with AI fabric behavior?  
   A. The job is CPU-bound, not network-bound.  
   B. Two or more elephant flows collided on one uplink; the collective waits for the slowest flow.  
   C. The spine switches need more buffer memory for TCP.  
   D. The job is using NVLink instead of InfiniBand.

2. Which traffic belongs on the in-band management network in an NVIDIA reference design?  
   A. NCCL all-reduce traffic  
   B. BMC/IPMI and switch console access  
   C. Slurm control, user SSH, and OS provisioning  
   D. Lustre/GPFS checkpoint writes

3. A customer insists that a 3:1 oversubscribed leaf-spine, as used in their web tier, is adequate for the GPU compute fabric. What is the correct response?  
   A. Agree; GPUs rarely saturate the network.  
   B. Explain that bulk-synchronous collectives make the fabric throughput equal to the slowest path, so the RA specifies a non-blocking fat-tree.  
   C. Suggest 2:1 as a compromise.  
   D. Recommend TCP tuning on the nodes instead.

4. Which component is used as the compute-fabric NIC in a Spectrum-X design?  
   A. ConnectX-7 configured for InfiniBand  
   B. BlueField-3 DPU in storage mode  
   C. BlueField-3 SuperNIC  
   D. Spectrum-4 switch ASIC

5. In which order should a new SuperPOD be brought up?  
   A. Nodes → compute switches → OOB → validation  
   B. OOB network → switch OS/SM → BCM node provisioning → validation  
   C. Compute switches → nodes → storage → OOB  
   D. BCM → nodes → OOB → switches

6. Why is a link with a high bit-error rate considered more dangerous than a failed link in a training cluster?  
   A. Failed links cannot be detected by UFM.  
   B. The SM re-routes around a failed link, but a degraded link silently retransmits and makes every collective wait on it.  
   C. High BER only affects storage traffic.  
   D. Degraded links consume more power.

7. Which statement about scale-up vs scale-out is correct?  
   A. InfiniBand provides higher per-GPU bandwidth than NVLink.  
   B. NVLink/NVSwitch connects GPUs within a node or NVL rack; InfiniBand or Spectrum-X connects GPUs across nodes.  
   C. NVSwitch is a Spectrum Ethernet switch variant.  
   D. Scale-out traffic never uses RDMA.

### Answer key
1. B — Low-entropy elephant flows can collide on one path; a synchronous collective completes only when the slowest flow does, so average utilization is misleading.
2. C — In-band carries cluster services and user access; A is compute, B is OOB, D is storage.
3. B — Synchronous collectives are bounded by the slowest path, which is why the compute fabric is designed non-blocking.
4. C — The BlueField-3 SuperNIC is the RoCE-optimized compute NIC for Spectrum-X; ConnectX-7 is used for InfiniBand compute.
5. B — OOB first for reachability, then switches and fabric manager, then node provisioning via BCM, then validation.
6. B — A dead link is routed around automatically; a degraded link is still used and retransmissions stall the collective.
7. B — NVLink is scale-up (intra-node/rack), IB or Spectrum-X is scale-out (inter-node); NVLink bandwidth exceeds the network port bandwidth.

## References
- NVIDIA Academy course: *AI Infrastructure and Operations Fundamentals*
- NVIDIA DGX SuperPOD Reference Architecture: Scalable Infrastructure with DGX H100 — https://docs.nvidia.com/dgx-superpod/reference-architecture-scalable-infrastructure-h100/latest/
- NVIDIA DGX SuperPOD Reference Architecture with DGX H200 / B200 / GB200 — https://docs.nvidia.com/dgx-superpod/
- NVIDIA Spectrum-X Networking Platform — https://www.nvidia.com/en-us/networking/spectrumx/
- NVIDIA BlueField-3 DPU and SuperNIC documentation — https://docs.nvidia.com/networking/
- NVIDIA DGX H100/B200 System User Guides (power and network port specifications) — https://docs.nvidia.com/dgx/
- Related modules: [1.2 DGX SuperPOD/BasePOD RA](02-dgx-superpod-basepod-reference-architectures.md), [1.3 NCCL and GPUDirect](03-gpu-to-gpu-communication-nccl-gpudirect.md), [RoCE A-to-Z](../03-rdma/03-roce-a-to-z.md)
