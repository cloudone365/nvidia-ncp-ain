# Module 1.3 — GPU-to-GPU Communication: NCCL and GPUDirect

> **Exam domain:** AI Data Center Design and Optimization (5%) — also underpins Troubleshooting Tools (20%) and InfiniBand Networking (30%)  
> **Source course(s):** AI Infrastructure and Operations Fundamentals; InfiniBand Professional (SHARP section)  
> **Source docs:** NVIDIA NCCL User Guide (Environment Variables, Troubleshooting); NCCL Tests README; GPUDirect RDMA documentation; GPUDirect Storage documentation; NVIDIA SHARP documentation  
> **Estimated study time:** 3 hours

## Learning objectives
- Describe the main collective operations (all-reduce, all-gather, reduce-scatter, broadcast, all-to-all) and map each to the parallelism strategy that generates it.
- Explain the difference between NCCL ring and tree algorithms and when each is selected.
- Describe how NCCL chooses a transport (NVLink → PCIe/SHM → network) and which environment variables influence NIC selection and GPUDirect RDMA.
- Run **nccl-tests** (`all_reduce_perf`) and interpret bus bandwidth versus algorithm bandwidth.
- Explain GPUDirect RDMA (nvidia-peermem / DMA-BUF) and GPUDirect Storage and verify they are active.
- Describe **SHARP** in-network reductions and enable them for NCCL.
- Explain **PXN** and how NCCL exploits a **rail-optimized** topology.
- Troubleshoot NCCL hangs and poor performance using `NCCL_DEBUG`, topology dumps, and HCA-selection checks.

## 1. Collective operations

Distributed training is built on a handful of collective communication primitives. The **NVIDIA Collective Communications Library (NCCL, pronounced "nickel")** implements them for GPUs over NVLink, PCIe, InfiniBand and RoCE.

| Collective | Semantics (N ranks, each with buffer of size S) | Data moved per rank (ring) | Typical use |
|---|---|---|---|
| **AllReduce** | Every rank ends with the element-wise sum (or max/min/avg) of all ranks' buffers | 2 × S × (N−1)/N | Gradient averaging in data parallelism; activation reductions in tensor parallelism |
| **ReduceScatter** | Reduce across ranks, each rank keeps 1/N of the result | S × (N−1)/N | First half of AllReduce; ZeRO/FSDP gradient sharding |
| **AllGather** | Every rank collects every other rank's buffer (result N × S) | S × (N−1)/N | Second half of AllReduce; FSDP parameter gathering; tensor-parallel outputs |
| **Broadcast** | One root sends its buffer to all | S | Initial weight distribution, checkpoint load |
| **Reduce** | All ranks reduce into one root | S | Loss aggregation, metrics |
| **AllToAll** | Every rank sends a distinct slice to every other rank | S × (N−1)/N but N−1 distinct destinations | Expert parallelism (MoE token routing), sequence parallelism |
| Send/Recv (point-to-point) | Explicit pair transfer | S | Pipeline parallelism activations/gradients between stages |

Note that AllReduce = ReduceScatter + AllGather; NCCL's ring AllReduce literally runs these two phases back-to-back, which is why its data volume is 2 × S × (N−1)/N.

### 1.1 Which parallelism uses which collective

| Parallelism | What is split | Dominant collective | Where it should run |
|---|---|---|---|
| **Data parallel (DP / DDP)** | The batch | AllReduce of gradients every step (or ReduceScatter + AllGather with ZeRO/FSDP) | Across nodes — fabric |
| **Tensor parallel (TP)** | Individual layers' matrices | AllReduce / AllGather / ReduceScatter *inside every layer*, several times per forward and backward pass | NVLink domain only (intra-node, or NVL72 rack) |
| **Pipeline parallel (PP)** | Layers into stages | Point-to-point Send/Recv of activations between stages | Across nodes, light bandwidth |
| **Expert parallel (EP, MoE)** | Experts across GPUs | AllToAll to route tokens to experts and back | Across nodes — stresses the fabric with many-to-many traffic, highest entropy |
| **Sequence/context parallel** | Long sequences | AllToAll or ring Send/Recv | Mixed |

The rule the exam wants: *the most communication-intensive dimension (TP) stays inside NVLink; DP and PP cross the scale-out fabric; EP's AllToAll is the hardest pattern for the fabric because it is not rail-friendly.*

## 2. Ring vs tree algorithms

### 2.1 Ring
Ranks form a logical ring; each rank sends to its right neighbor and receives from its left. For AllReduce, the buffer is split into N chunks; in N−1 steps of ReduceScatter each rank accumulates one fully reduced chunk, then N−1 steps of AllGather distribute them. Properties:
- Bandwidth-optimal: every link is used in both directions throughout.
- Latency grows linearly with N (2 × (N−1) steps) — bad for small messages at large scale.
- NCCL builds multiple rings (one per NIC/NVLink lane) to use all available links in parallel.

### 2.2 Tree
NCCL (since 2.4) also builds **double binary trees**: two complementary trees so that every rank is an interior node in one and a leaf in the other, balancing load. Reduction goes up the tree, broadcast comes down. Properties:
- Latency grows as log₂(N) — much better at large N for small/medium messages.
- Slightly lower peak bandwidth than ring on some topologies.
- NCCL auto-selects: tree for latency-bound (small) messages and very large rank counts, ring for bandwidth-bound (large) messages, using an internal cost model tuned per topology. `NCCL_ALGO=Ring|Tree|CollnetDirect|CollnetChain|NVLS` forces a choice for testing; leave unset in production.

### 2.3 NVLS and CollNet
- **NVLS** (NVLink SHARP): on Hopper/Blackwell with NVSwitch, the NVSwitch performs in-switch reductions for intra-node collectives.
- **CollNet / SHARP**: in-network reductions on the InfiniBand fabric (Section 6).

## 3. NCCL transport selection

At `ncclCommInitRank` NCCL discovers the hardware topology (`/sys` PCIe tree, NVLink, NUMA, NICs) and builds a graph; it then picks, for every pair of ranks, the fastest available transport:

1. **P2P over NVLink** — same node, GPUs connected by NVLink/NVSwitch. Highest bandwidth.
2. **P2P over PCIe** — same node, no NVLink path (or NVLink disabled). Falls back to PCIe peer-to-peer if the IOMMU/ACS allow it.
3. **SHM (shared memory)** — same node, when P2P is not possible (e.g. across sockets with ACS enabled); data is staged through host memory.
4. **NET** — different nodes, via a network plugin:
   - The built-in **IB plugin** (`NCCL_NET=IB`) uses ibverbs and handles both InfiniBand and RoCE.
   - The **Socket** plugin (`NCCL_NET=Socket`) uses TCP; dramatically slower, used only when no RDMA device is usable — seeing `NET/Socket` in `NCCL_DEBUG=INFO` output on an RDMA cluster is a red flag.
   - External plugins (e.g. `libnccl-net.so` for SHARP/CollNet, AWS EFA, etc.).

Over the NET transport, NCCL further decides whether to use **GPUDirect RDMA** (NIC DMAs straight to/from GPU memory) or to bounce through host memory, controlled by `NCCL_NET_GDR_LEVEL` and the PCIe distance between GPU and NIC.

### 3.1 Key environment variables

| Variable | Purpose | Notes |
|---|---|---|
| `NCCL_DEBUG=INFO` | Verbose log: transports chosen, rings/trees built, NICs used, GDR status, errors | `WARN` is the default; `TRACE` is extremely verbose. First thing to set when debugging. |
| `NCCL_DEBUG_SUBSYS=INIT,NET,GRAPH,TUNING` | Filter INFO output by subsystem | `ALL` or a comma list; prefix with `^` to exclude |
| `NCCL_DEBUG_FILE=/tmp/nccl.%h.%p.log` | Write debug output per host/PID instead of stderr | Essential on large jobs |
| `NCCL_TOPO_DUMP_FILE=/tmp/topo.xml` | Dump the topology graph NCCL discovered | Compare across nodes to find a node with a missing NIC or wrong PCIe affinity |
| `NCCL_TOPO_FILE=` | Load a hand-written topology (for VMs, or to override) | Rarely needed on bare-metal DGX |
| `NCCL_SOCKET_IFNAME=eth0` / `^docker,lo` | Interface(s) for the bootstrap/out-of-band TCP channel and for Socket transport | Must be an interface reachable between all nodes; wrong value is a classic cause of init hangs |
| `NCCL_IB_HCA=mlx5_0,mlx5_1` / `=^mlx5_8` / `=mlx5_0:1` | Restrict which HCAs (and ports) NCCL uses for the IB transport | Prefix `^` excludes; `=mlx5_` prefix match vs exact match — check the NCCL doc for the `=`-prefix exact-match syntax. Use to keep storage BlueField-3 ports out of NCCL. |
| `NCCL_IB_GID_INDEX=3` | GID index for RoCE (selects IPv4/IPv6 and RoCE v1/v2 GID) | Typically 3 for RoCEv2 IPv4 on MOFED; verify with `show_gids` |
| `NCCL_IB_TC=106` | Traffic class (DSCP ×4) for RoCE packets | Must match the DSCP the switches mark/queue for lossless RoCE (e.g. DSCP 26 → TC 104; verify your fabric's QoS plan) |
| `NCCL_IB_SL=0` | InfiniBand service level | Match the SM's QoS/SL config if used |
| `NCCL_IB_TIMEOUT=18`, `NCCL_IB_RETRY_CNT=7` | QP timeout exponent and retry count | Raise timeout for large fabrics with adaptive routing or when seeing "transport retry counter exceeded" |
| `NCCL_IB_QPS_PER_CONNECTION=1` | QPs per connection; >1 increases entropy for ECMP-based Ethernet fabrics | Spectrum-X with AR usually does not need it; verify guidance |
| `NCCL_IB_SPLIT_DATA_ON_QPS` | Split data across multiple QPs | Pairs with the above |
| `NCCL_IB_DISABLE=1` | Force Socket transport | Diagnostic only |
| `NCCL_NET_GDR_LEVEL=PIX|PXB|PHB|SYS` (or `LOC`) | Maximum GPU↔NIC PCIe distance at which GPUDirect RDMA is used | Default is `PXB` in current releases (verify); on DGX each GPU/NIC pair is PIX/PXB so GDR is on by default |
| `NCCL_NET_GDR_READ=1` | Also use GDR for sends (reads from GPU memory) | Default on for recent GPUs |
| `NCCL_CROSS_NIC=0|1|2` | Whether rings may use different NICs on sender and receiver (i.e. cross rails) | 0 = keep same NIC (rail-aligned) when possible; 1 = allow; 2 = auto (default) |
| `NCCL_PXN_DISABLE=1` | Disable PXN | Diagnostic only |
| `NCCL_COLLNET_ENABLE=1` | Enable the CollNet (SHARP) plugin | Requires the SHARP net plugin and AM — Section 6 |
| `NCCL_ALGO`, `NCCL_PROTO=LL,LL128,Simple` | Force algorithm / protocol | Testing only |
| `NCCL_MIN_NCHANNELS`, `NCCL_MAX_NCHANNELS` | Channels (parallel rings) | Tuning |
| `NCCL_NET_PLUGIN=` | Pick a specific `libnccl-net-<name>.so` | e.g. `sharp` |

## 4. nccl-tests

**nccl-tests** (github.com/NVIDIA/nccl-tests) is the standard benchmark and acceptance test. Build with `make MPI=1 MPI_HOME=/path/to/mpi CUDA_HOME=/usr/local/cuda` to get the MPI launcher.

```bash
# Single node, 8 GPUs, AllReduce from 8 B to 8 GB, doubling
./build/all_reduce_perf -b 8 -e 8G -f 2 -g 8

# Two nodes, one process per GPU, with MPI
mpirun -np 16 -H dgx01:8,dgx02:8 \
  -x NCCL_DEBUG=INFO -x NCCL_IB_HCA=mlx5_0,mlx5_1,mlx5_2,mlx5_3,mlx5_4,mlx5_5,mlx5_6,mlx5_7 \
  -x NCCL_SOCKET_IFNAME=enp225s0np0 \
  ./build/all_reduce_perf -b 8 -e 8G -f 2 -g 1

# Under Slurm
srun -N 16 --ntasks-per-node=8 --gpus-per-node=8 ./build/all_reduce_perf -b 1G -e 8G -f 2 -g 1
```

Common flags: `-b` begin size, `-e` end size, `-f` size multiplication factor, `-g` GPUs per process, `-n` iterations, `-w` warm-up iterations, `-c 1` check correctness, `-o sum`, `-d float`.

### 4.1 Reading the output

```text
#       size   count    type  redop  root   time  algbw  busbw #wrong   time  algbw  busbw #wrong
#        (B)  (elements)                   (us)  (GB/s) (GB/s)         (us)  (GB/s) (GB/s)
  8589934592  2147483648  float   sum    -1  46213  185.9  371.8      0  46180  186.0  372.0      0
# Avg bus bandwidth    : 350.12
```

- **algbw (algorithm bandwidth)** = data size / time. What the application perceives: "an 8 GB all-reduce took 46 ms, so 186 GB/s."
- **busbw (bus bandwidth)** = algbw × correction factor that reflects the data each rank's links actually carried. For AllReduce the factor is 2 × (N−1)/N; for AllGather/ReduceScatter it is (N−1)/N; for Broadcast/Reduce it is 1. busbw is therefore comparable to hardware link bandwidth regardless of rank count and is the number you compare against NVLink or NIC line rate.

Rules of thumb for acceptance (verify against your RA's validated figures):
- Intra-node H100 (NVLink 4 + NVSwitch): AllReduce busbw ~ 350–480 GB/s at large sizes.
- Inter-node over 8 × NDR 400G: each node injects 8 × 50 GB/s = 400 GB/s; expect busbw in the high 300s GB/s for large messages across a full SU, with a modest drop with more nodes.
- A single node far below its peers → check `nvidia-smi topo -m`, `ibstat` rates, and the NCCL log for `NET/Socket` or `GDR disabled`.

## 5. GPUDirect

### 5.1 GPUDirect RDMA
Without GPUDirect RDMA, a NIC can only DMA to host memory; sending GPU data requires a `cudaMemcpy` to a host bounce buffer, then the NIC reads it — doubling PCIe traffic and adding CPU work and latency. **GPUDirect RDMA** lets the NIC (ConnectX, BlueField) read and write GPU memory directly over PCIe. Requirements:

- GPU and NIC under the same PCIe root complex or close enough (`NCCL_NET_GDR_LEVEL`). DGX places each NIC next to its GPU for this reason.
- A kernel mechanism to pin GPU memory and expose it to the NIC driver:
  - **nvidia-peermem** kernel module (shipped with the NVIDIA driver; successor to `nv_peer_mem`). `lsmod | grep nvidia_peermem` must show it loaded on MOFED/DOCA-OFED systems.
  - **DMA-BUF**: the upstream mechanism (Linux 5.12+, open-kernel NVIDIA driver, recent rdma-core). NCCL uses it automatically when available; `NCCL_DEBUG=INFO` prints `GDRDMA ... dmabuf` or `peermem`.
- `NCCL_NET_GDR_LEVEL` permitting the GPU↔NIC distance; `NCCL_NET_GDR_READ` for the send side.
- With PCIe ACS enabled on switches/root ports, P2P can be forced through the root complex and GDR may fail; DGX OS disables ACS, and the Troubleshooting modules show `setpci` checks.

Verification:
```bash
lsmod | grep -E 'nvidia_peermem|ib_core'
NCCL_DEBUG=INFO ./all_reduce_perf ... 2>&1 | grep -iE 'GDRDMA|GDR'
# expected: "... via NET/IB/0/GDRDMA" on each inter-node channel
ib_write_bw -d mlx5_0 --use_cuda=0 -a   # perftest built with CUDA: GPU-memory RDMA test (verify flag)
```

### 5.2 GPUDirect Storage (GDS)
**GPUDirect Storage** extends the same idea to storage: the NVMe or NIC (for NFS-over-RDMA, Lustre, GPFS, WEKA) DMAs file data straight into GPU memory via the `cuFile` API and the `nvidia-fs` kernel module. Benefits: lower CPU use and higher throughput for dataset loading and checkpoints. Check with `gdscheck -p` (part of the CUDA toolkit's GDS package). GDS traffic rides the *storage* fabric on the BlueField-3 ports, not the compute fabric.

### 5.3 GPUDirect P2P
Intra-node GPU-to-GPU memory access over NVLink or PCIe; the mechanism behind NCCL's P2P transport. Not to be confused with GPUDirect RDMA (NIC↔GPU).

## 6. SHARP: in-network reductions

**SHARP (Scalable Hierarchical Aggregation and Reduction Protocol)** moves the reduction step of AllReduce from the GPUs into the InfiniBand switch ASICs (Quantum/Quantum-2/Quantum-X800). Instead of each rank sending data around a ring, every rank sends its buffer once to its leaf; the leaf reduces, forwards partial results up a reduction tree to the spine (and root), and the result is multicast back down. Benefits: data crossing each link drops from ~2× to ~1× the buffer size, and latency is near-constant with rank count.

Components:
- **SHARP Aggregation Manager (AM)** — runs with the Subnet Manager (on the UFM appliance or the opensm host, `sharp_am` service) and allocates aggregation trees and switch resources.
- **SHARP daemon (`sharpd`)** on each compute node — brokers job requests to the AM.
- **libsharp / libsharp_coll** — user-space library.
- **NCCL SHARP (CollNet) plugin** — `nccl-rdma-sharp-plugins` provides `libnccl-net.so`; NCCL loads it and exposes the CollNet algorithm.

Enable in NCCL:
```bash
export NCCL_COLLNET_ENABLE=1         # turn on CollNet (SHARP) algorithm
export NCCL_ALGO=CollnetDirect       # optional, force for testing; default lets the tuner choose
export NCCL_SHARP_GROUP_SIZE_THRESH=2  # verify name/default; minimum ranks per node group to use SHARP
export SHARP_COLL_ENABLE_SAT=1       # streaming aggregation (large messages) on Quantum-2 — verify
export SHARP_COLL_LOG_LEVEL=3
```
Check with `NCCL_DEBUG=INFO` for lines mentioning `SHARP` / `CollNet` and with `sharp_hello` / `sharp_benchmark.sh` from the SHARP package. SHARP needs the AM reachable, the switches licensed/enabled for SHARP, and the job's partition (**PKey**) allowed in the AM configuration. Only one or a few SHARP jobs can hold switch aggregation resources concurrently per tree; verify limits per switch generation. Module [`../02-infiniband/03-infiniband-qos-adaptive-routing-sharp.md`](../02-infiniband/03-infiniband-qos-adaptive-routing-sharp.md) covers the fabric side.

## 7. PXN and rail-optimized topologies

### 7.1 The problem
On a rail-optimized fabric ([Module 1.2](02-dgx-superpod-basepod-reference-architectures.md)), GPU *n* of every node sits on leaf *n*. If NCCL's ring on node A exits via NIC 0 but the next hop ring position on node B is GPU 3 (NIC 3), the packet must cross a spine (leaf 0 → spine → leaf 3). Do that for every ring and the spine load returns.

### 7.2 PXN
**PXN (PCI × NVLink)**, introduced in NCCL 2.12, lets a GPU send data to a *different* GPU's NIC in the same node over NVLink first, then out that NIC. So GPU 3 on node A, needing to reach GPU 3 on node B, always sends through NIC 3 → leaf 3 → NIC 3 on node B — one hop — even if the ring logically runs through other GPUs. NCCL aggregates messages for the same destination rail on the NVLink side as well, reducing the number of network messages. Effects:
- Inter-node traffic stays on its rail; spine utilization drops.
- Fewer, larger network sends; better small-message performance.
- Requires NVLink between the GPUs in the node (true on DGX/HGX) and the NIC-per-GPU layout.

`NCCL_CROSS_NIC=0` further tells NCCL to prefer rings whose endpoints use the same NIC index on both ends. On a rail-optimized fabric, `NCCL_DEBUG=INFO` with `NCCL_DEBUG_SUBSYS=GRAPH` shows rings with matching NIC indices and `PXN` in the channel description.

### 7.3 Why this matters for the network engineer
If PXN is off (older NCCL, NVLink down, `NCCL_PXN_DISABLE=1`) or rails are mis-cabled (NIC 2 of one node landing on leaf 5), you will see unexpected spine traffic and lower busbw even though every link is up. A topology check that cabling matches the rail plan (`iblinkinfo` per leaf, or NetQ topology) is a prerequisite before blaming NCCL.

## 8. Troubleshooting NCCL

### 8.1 Hangs at initialization
Symptoms: job sits at `ncclCommInitRank` and eventually times out (PyTorch: `NCCL timeout` / watchdog after 10–30 min by default).

| Likely cause | Check |
|---|---|
| Bootstrap TCP cannot connect | `NCCL_SOCKET_IFNAME` names an interface that exists and is routable on *all* nodes; firewalls; `NCCL_DEBUG=INFO` shows the bootstrap IP it chose |
| Different ranks pick different HCAs or one node has fewer visible HCAs | `ibstat` / `ibdev2netdev` on every node; `NCCL_IB_HCA` identical; port `Active` with the expected rate |
| Wrong GID index on RoCE | `show_gids`, `NCCL_IB_GID_INDEX` |
| Mismatched **PKey** / partition membership | `ibstat` shows port state but `ibping` to peer LID fails; check SM partition config |
| One rank crashed/not launched | rank count in the launcher vs. `-np`; look for a rank with no log |
| ACS / IOMMU interfering with P2P | log shows `SHM` instead of `P2P` for intra-node |

### 8.2 Hangs or errors mid-run
- `NET/IB : Got completion with error 12, opcode ..., ... transport retry counter exceeded` — a link or path is dropping packets; check `mlxlink`, `ibdiagnet` error counters, PFC/ECN config on RoCE, increase `NCCL_IB_TIMEOUT` only as a mitigation.
- Watchdog timeout on a single collective — usually one straggler node; use per-host `NCCL_DEBUG_FILE` and DCGM to find the slow GPU or the port with symbol errors.
- `Cuda failure 'out of memory'` during init — too many channels/QPs; reduce `NCCL_MAX_NCHANNELS` or `NCCL_IB_QPS_PER_CONNECTION`.

### 8.3 Poor bandwidth
1. Confirm transport: `grep -E 'via NET|via P2P|via SHM' nccl.log`. Want `NET/IB/.../GDRDMA` inter-node and `P2P/NVL` or `P2P/CUMEM` intra-node.
2. Confirm all 8 HCAs are used (`NCCL INFO NET/IB : Using [0]mlx5_0:1/IB [1]mlx5_1:1/IB ...`). If BlueField storage ports appear, exclude them with `NCCL_IB_HCA`.
3. Confirm link rates: `ibstat | grep -E 'Rate|State'` — a port at 200 or 100 Gb/s on one node will cap the whole collective.
4. Dump topology on a good and a bad node (`NCCL_TOPO_DUMP_FILE`) and diff.
5. Run `ib_write_bw` between the specific NIC pairs to separate fabric problems from NCCL problems.
6. On RoCE: check PFC pause and ECN counters on the switch (`nv show interface swpX counters`, WJH drops) — see [`../04-spectrum-ethernet/`](../04-spectrum-ethernet/).

## Configuration / command reference

```bash
# Minimal diagnostic environment for a 2-node run
export NCCL_DEBUG=INFO
export NCCL_DEBUG_SUBSYS=INIT,NET,GRAPH
export NCCL_DEBUG_FILE=/var/log/nccl/%h.%p.log
export NCCL_TOPO_DUMP_FILE=/var/log/nccl/topo.%h.xml   # verify %h support; else use a per-node path
export NCCL_SOCKET_IFNAME=enp225s0np0        # in-band interface
export NCCL_IB_HCA=^mlx5_8,mlx5_9            # exclude BlueField-3 storage ports (example indices)
export NCCL_NET_GDR_LEVEL=PXB
export NCCL_IB_GID_INDEX=3                   # RoCE only
export NCCL_IB_TC=106                        # RoCE only; match fabric DSCP plan
export NCCL_COLLNET_ENABLE=1                 # IB with SHARP only

# Host-side checks before running NCCL
nvidia-smi topo -m                    # GPU<->NIC affinity; want PIX/PXB per pair
ibdev2netdev                          # HCA <-> netdev, Up/Down
ibstat | grep -E 'CA |State|Rate'     # all ports Active, same Rate
lsmod | grep nvidia_peermem           # GPUDirect RDMA module (or dmabuf support in log)
show_gids                             # RoCE GID table (MOFED utility)
cat /sys/class/infiniband/mlx5_0/ports/1/pkeys/0   # PKey membership

# Point-to-point baseline on the same rail (node A then node B)
ib_write_bw -d mlx5_3 -a --report_gbits
ib_write_bw -d mlx5_3 -a --report_gbits dgx01-ib3

# Collective test
mpirun -np 16 -H dgx01:8,dgx02:8 --bind-to none -x NCCL_DEBUG -x NCCL_IB_HCA \
  ./build/all_reduce_perf -b 1G -e 8G -f 2 -g 1 -n 20 -w 5
```

## Common pitfalls & exam traps
- busbw vs algbw: busbw is the link-comparable figure; algbw is what the application sees. For AllReduce, busbw = algbw × 2(N−1)/N.
- `NCCL_SOCKET_IFNAME` is for the bootstrap/TCP path and `NCCL_IB_HCA` for the RDMA path; setting only one does not fix the other.
- `NET/Socket` in the log on an RDMA cluster means NCCL did not find usable IB devices — a driver, permissions (`/dev/infiniband` in containers), or `NCCL_IB_HCA` problem.
- GPUDirect RDMA needs nvidia-peermem (or DMA-BUF) *and* acceptable PCIe distance; a missing module silently falls back to host bounce buffers and roughly halves inter-node bandwidth.
- Tensor parallelism across nodes over the fabric is a design error; keep TP in the NVLink domain.
- SHARP helps AllReduce (and Reduce/Broadcast-style) collectives on InfiniBand; it does nothing for AllToAll, and needs the AM, `sharpd`, and the CollNet plugin enabled.
- PXN relies on NVLink; a node with NVLink errors loses rail alignment and shows up as extra spine load.
- Raising `NCCL_IB_TIMEOUT` hides a lossy link; it is a mitigation, not a fix.
- Ring is bandwidth-optimal, tree is latency-optimal; NCCL chooses automatically. Forcing `NCCL_ALGO` in production is usually wrong.
- Rank count math: a 2-node, 8-GPU-per-node job is `-np 16 -g 1`, not `-np 2 -g 8`, when launched with MPI one process per GPU.

## Key takeaways
- AllReduce = ReduceScatter + AllGather; DP uses AllReduce, TP uses AllReduce/AllGather inside layers, PP uses Send/Recv, EP uses AllToAll.
- Ring: bandwidth-optimal, latency O(N). Tree: latency O(log N). NCCL auto-selects.
- Transport order: NVLink P2P → PCIe P2P → SHM → NET (IB/RoCE via IB plugin, Socket as fallback).
- `NCCL_DEBUG=INFO`, `NCCL_DEBUG_FILE`, `NCCL_TOPO_DUMP_FILE` are the first three debugging tools.
- `NCCL_IB_HCA` selects HCAs; `NCCL_SOCKET_IFNAME` selects the bootstrap interface; `NCCL_IB_GID_INDEX`/`NCCL_IB_TC` matter for RoCE.
- `NCCL_NET_GDR_LEVEL` sets how far a NIC may be from a GPU for GPUDirect RDMA; needs nvidia-peermem or DMA-BUF.
- busbw = algbw × 2(N−1)/N for AllReduce; compare busbw to link rate.
- SHARP offloads reductions to Quantum switches; enable with `NCCL_COLLNET_ENABLE=1` plus the SHARP plugin, AM and sharpd.
- PXN sends via the NIC on the destination's rail using NVLink first, keeping inter-node traffic one hop on a rail-optimized fabric.
- Straggler hunting: one slow link/port/GPU bounds the whole collective; diff topology dumps and ibstat across nodes.

## Review questions

1. A 16-node data-parallel job runs `all_reduce_perf` and reports algbw of 190 GB/s and busbw of 378 GB/s at 8 GB. Which value should be compared with the nodes' 8 × 400G NIC capacity?  
   A. algbw, because it is what the application sees  
   B. busbw, because it is normalized to the data each link actually carries  
   C. Neither; only latency matters  
   D. The sum of both

2. `NCCL_DEBUG=INFO` shows `NET/Socket` for inter-node channels on a DGX cluster with InfiniBand. What is the most likely cause?  
   A. SHARP is disabled  
   B. NCCL cannot see or open the IB devices (driver, container `/dev/infiniband`, or `NCCL_IB_HCA` exclusion)  
   C. NVLink is down  
   D. The job uses tree instead of ring

3. Which parallelism strategy generates the AllToAll pattern that is hardest for a rail-optimized fabric?  
   A. Data parallelism  
   B. Tensor parallelism  
   C. Pipeline parallelism  
   D. Expert parallelism (MoE)

4. Which combination is required for GPUDirect RDMA to be used by NCCL?  
   A. nvidia-peermem or DMA-BUF support, and GPU–NIC PCIe distance within `NCCL_NET_GDR_LEVEL`  
   B. SHARP and UFM  
   C. `NCCL_SOCKET_IFNAME` set to the IB interface  
   D. Tree algorithm forced with `NCCL_ALGO=Tree`

5. What does PXN do?  
   A. Replaces InfiniBand with NVLink between nodes  
   B. Lets a GPU send through another GPU's NIC in the same node via NVLink so inter-node traffic stays on the destination's rail  
   C. Disables cross-rail communication entirely  
   D. Performs reductions in the switch

6. A job intermittently fails with "transport retry counter exceeded" on one node. What is the best first action?  
   A. Increase `NCCL_IB_TIMEOUT` and `NCCL_IB_RETRY_CNT` permanently  
   B. Check that node's links with `mlxlink`/`ibdiagnet` error counters for a degraded path  
   C. Switch NCCL to Socket transport  
   D. Reduce the batch size

7. Which components must be running for NCCL to use SHARP on a Quantum-2 fabric?  
   A. Only `NCCL_COLLNET_ENABLE=1`  
   B. SHARP Aggregation Manager with the SM, `sharpd` on nodes, the NCCL SHARP (CollNet) plugin, and `NCCL_COLLNET_ENABLE=1`  
   C. NetQ and WJH  
   D. BCM and Slurm

8. Two nodes differ: `ibstat` on node 7 shows `mlx5_4` at 200 Gb/s while all other ports are 400 Gb/s. What is the expected effect on an all-reduce across the SU?  
   A. None; NCCL avoids that port automatically  
   B. Only node 7 slows down  
   C. The whole collective slows to the rate of the slowest participant path  
   D. SHARP compensates

### Answer key
1. B — busbw applies the 2(N−1)/N factor so it reflects per-link traffic and is comparable with hardware bandwidth.
2. B — Socket transport is the fallback when the IB plugin finds no usable RDMA device.
3. D — MoE expert parallelism routes tokens many-to-many with AllToAll, which is not rail-friendly.
4. A — GDR needs the kernel peer-memory path and a close enough GPU/NIC; the other options are unrelated.
5. B — PXN uses NVLink to reach the NIC on the correct rail, keeping inter-node traffic one hop.
6. B — Retry exhaustion indicates loss or a bad link; find the degraded path rather than hiding it with longer timeouts.
7. B — SHARP requires AM, sharpd, the CollNet plugin, and the NCCL enable flag.
8. C — Collectives are bulk-synchronous; the slowest link bounds the step time for all ranks.

## References
- NCCL User Guide — Environment Variables — https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html
- NCCL User Guide — Troubleshooting — https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/troubleshooting.html
- NCCL Tests README (bus bandwidth definition) — https://github.com/NVIDIA/nccl-tests
- NVIDIA GPUDirect RDMA documentation — https://docs.nvidia.com/cuda/gpudirect-rdma/
- NVIDIA GPUDirect Storage documentation — https://docs.nvidia.com/gpudirect-storage/
- NVIDIA SHARP documentation — https://docs.nvidia.com/networking/display/sharpv300
- NVIDIA Developer Blog: "Doubling all2all Performance with NVIDIA Collective Communication Library 2.12" (PXN) — https://developer.nvidia.com/blog/
- nccl-rdma-sharp-plugins — https://github.com/Mellanox/nccl-rdma-sharp-plugins
- Related modules: [1.2 SuperPOD RA (rail-optimized)](02-dgx-superpod-basepod-reference-architectures.md), [IB QoS/AR/SHARP](../02-infiniband/03-infiniband-qos-adaptive-routing-sharp.md), [RDMA fundamentals](../03-rdma/), [IB diag and perftest](../08-troubleshooting/)
