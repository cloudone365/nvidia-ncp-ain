# Module 3.1 — RDMA Fundamentals

> **Exam domain:** NVIDIA InfiniBand Networking (30%) — RDMA concepts also underpin Spectrum-X/RoCE (30%) and Troubleshooting Tools (20%)  
> **Source course(s):** RDMA (NVIDIA Academy)  
> **Source docs:** RDMA Aware Networks Programming User Manual; rdma-core documentation (`libibverbs`, `librdmacm` man pages); perftest README; MLNX_OFED/DOCA-OFED User Manual  
> **Estimated study time:** 4–5 hours

## Learning objectives
- Describe kernel bypass, zero copy and CPU offload and why they matter for AI workloads.
- Identify the objects of the verbs model (HCA, PD, MR, QP, CQ, SRQ, WR/WQE, CQE) and their relationships.
- Distinguish the QP transport types (RC, UC, UD, XRC, DC) and the RDMA operations (SEND/RECV, READ, WRITE, atomics).
- Explain how two endpoints exchange connection parameters (QPN, LID/GID, rkey) with RDMA CM or out-of-band.
- Compare InfiniBand, RoCE v1, RoCE v2 and iWARP transports.
- Describe path MTU negotiation and its relationship to link MTU.
- Use rdma-core tools (`ibv_devinfo`, `rdma link`, `rdma dev`, `show_gids`) to inspect RDMA devices.
- Run **ib_write_bw** / **ib_write_lat** and related perftest tools with the correct flags.

## 1. What RDMA changes

In a conventional TCP/IP stack, moving data from one application to another involves the sender's kernel copying from user buffer to socket buffer, building headers, the NIC DMA-ing the frame, and the mirror image on the receiver — with interrupts and context switches at each step. At 10 Gb/s this is tolerable; at 400 Gb/s per GPU, with eight GPUs per server, the host CPU cannot keep up and the copies alone exceed memory bandwidth budgets.

**RDMA** (Remote Direct Memory Access) removes the host from the data path:

| Property | Meaning | Effect |
|---|---|---|
| **Kernel bypass** | The application talks to the NIC directly through memory-mapped queues (user-space verbs library); the kernel is involved only in setup (resource allocation, memory registration, connection) | No syscalls or interrupts per message; single-digit microsecond latency (sub-2 µs on IB) |
| **Zero copy** | The NIC reads from and writes into the application's (or GPU's) buffers directly | No intermediate socket buffers; payload touched once by hardware |
| **CPU offload / transport offload** | Segmentation, reliability (ACK/retransmit), ordering and congestion handling are implemented in the NIC | CPU utilisation near zero at line rate, leaving cores for the application |
| **One-sided operations** | A remote NIC can read/write a peer's memory without the peer's CPU knowing in real time | Enables NCCL/NVSHMEM put/get semantics and GPUDirect RDMA (GPU memory ↔ remote GPU memory with no host involvement) |

The combination is what makes NCCL achieve ~390 Gb/s per NDR HCA with negligible CPU, and why "RDMA fabric" (IB or RoCE) is non-negotiable for scale-out AI training.

## 2. The verbs object model

The programming interface for RDMA on Linux is **verbs** (`libibverbs`), originally defined by the InfiniBand specification and shared by IB, RoCE and iWARP. Every resource below is created by the application and lives in the NIC and the driver.

```text
  Application process
  ┌───────────────────────────────────────────────────────────────────┐
  │  Context (ibv_context) ─ opened on one HCA device (e.g. mlx5_0)   │
  │   └── Protection Domain (PD)                                      │
  │        ├── Memory Region (MR)   buffer, lkey, rkey                │
  │        ├── Queue Pair (QP)      Send Queue + Receive Queue        │
  │        │     └── posts Work Requests (WR) → WQEs in the queues    │
  │        ├── Shared Receive Queue (SRQ)  optional, shared RQ        │
  │        └── Address Handle (AH)  for UD destinations               │
  │   └── Completion Queue (CQ)     CQEs, polled or event-driven      │
  │        └── Completion Channel   fd for blocking wait              │
  └───────────────────────────────────────────────────────────────────┘
```

| Object | Purpose | Key facts |
|---|---|---|
| **HCA / device** | The RDMA NIC port(s); `mlx5_0`, `mlx5_1`… | One device per PCIe function; each has ports with LID/GID tables |
| **Protection Domain (PD)** | A security boundary; MRs and QPs in different PDs cannot be used together | Every QP, MR, AH belongs to exactly one PD |
| **Memory Region (MR)** | A pinned, registered virtual address range the NIC may DMA to/from | Registration returns **lkey** (used in local WRs) and **rkey** (handed to peers for RDMA READ/WRITE). Access flags: LOCAL_WRITE, REMOTE_READ, REMOTE_WRITE, REMOTE_ATOMIC |
| **Queue Pair (QP)** | Send Queue (SQ) + Receive Queue (RQ); the "connection" endpoint | Identified by **QPN**; has a transport type and a state machine; ordering is per QP |
| **Completion Queue (CQ)** | Where the NIC reports finished work (success or error) as CQEs | SQ and RQ may share a CQ or use separate ones |
| **Shared Receive Queue (SRQ)** | One RQ shared by many QPs to save receive buffers | Used by UCX/MPI with many connections; XRC depends on it |
| **Work Request (WR) / WQE** | A descriptor: operation, scatter/gather list (addr, length, lkey), flags, for RDMA ops the remote addr + rkey | Posted with `ibv_post_send` / `ibv_post_recv`; becomes a WQE in NIC-visible memory |
| **Completion (CQE / `ibv_wc`)** | Status, opcode, byte count, `wr_id`, immediate data | Status `IBV_WC_SUCCESS` or an error (e.g. `IBV_WC_REM_ACCESS_ERR`, `IBV_WC_RETRY_EXC_ERR`) |

The ordering of creation (context → PD → MR/CQ → QP → connect) is detailed in [Module 3.2](02-rdma-programming-fundamentals.md).

## 3. QP transport types

| Type | Connected? | Reliable? | Operations | Notes / who uses it |
|---|---|---|---|---|
| **RC** — Reliable Connected | Yes (1:1) | Yes: ACK/NAK, in-order, retransmit | SEND/RECV, RDMA WRITE/READ, atomics | The workhorse: NCCL, UCX, NVMe-oF, storage. One QP per peer → N² QPs at scale |
| **UC** — Unreliable Connected | Yes | No | SEND/RECV, RDMA WRITE | Rare; streaming where loss is acceptable |
| **UD** — Unreliable Datagram | No (any-to-any via Address Handles) | No | SEND/RECV only, one MTU max per message | IPoIB datagram mode, multicast, connection-manager control traffic, SA queries |
| **XRC** — eXtended Reliable Connected | Yes, shared | Yes | RC ops | Lets one QP target multiple SRQs on a remote node, cutting QP count for multi-process-per-node MPI jobs |
| **DC** — Dynamically Connected (NVIDIA extension) | Connect-on-demand | Yes | RC ops | Transient connections created per message burst; UCX uses it for huge rank counts; `ibv_devinfo -v` and `mlx5dv` expose it |
| **RD** — Reliable Datagram | spec-defined, not implemented | — | — | Know it exists only on paper |

Exam angle: RC is the only *widely used* type supporting one-sided RDMA READ and atomics; UD is the only one supporting multicast; XRC/DC are the scaling answers to "RC needs a QP per peer".

## 4. RDMA operations

| Operation | Sidedness | Who supplies the buffer? | Completion on remote side? |
|---|---|---|---|
| **SEND / RECV** | Two-sided ("channel semantics") | Receiver must have pre-posted a RECV WR with a buffer ≥ message size | Yes — a receive completion with length |
| **RDMA WRITE** | One-sided ("memory semantics") | Sender names remote `addr + rkey`; receiver CPU not involved | No (unless **WRITE with Immediate**, which consumes a RECV and generates a CQE carrying 32-bit immediate data) |
| **RDMA READ** | One-sided | Initiator pulls from remote `addr + rkey` into local buffer | No |
| **Atomics** (Fetch-and-Add, Compare-and-Swap, 64-bit) | One-sided | Remote `addr + rkey` | No; result returned to initiator |
| **SEND with Immediate** | Two-sided | As SEND; 32-bit immediate delivered in the CQE | Yes |

Two-sided requires the **receive-before-send** discipline: if a SEND arrives and no RECV WQE is available, the responder returns an **RNR NAK** (Receiver Not Ready); after `rnr_retry` attempts the QP goes to error. One-sided operations need the initiator to *already know* the remote virtual address and rkey, which is why control-plane exchange exists (§5). Under the hood, NCCL mostly uses RDMA WRITE (with immediate or a flag write) because the responder GPU never needs to react.

## 5. Connection establishment

A QP cannot be used until both sides know each other's parameters:

- the peer's **QPN**,
- the peer's **LID** (IB) or **GID** (RoCE/IB global), plus port, path MTU, SL, and for RoCE the GID index and MAC resolved through the IP stack,
- a **Packet Sequence Number (PSN)** starting value,
- for one-sided operations, the peer's buffer **address and rkey**.

### 5.1 Raw verbs with out-of-band exchange
The traditional (perftest, many tutorials) way: open a TCP socket, exchange a small struct containing `{lid, qpn, psn, gid, addr, rkey}`, then each side drives the QP through INIT→RTR→RTS with the peer's values. Simple, fully under your control, but you own the socket, the error handling and the transport-specific details (e.g. GID index for RoCE).

### 5.2 RDMA Connection Manager (librdmacm)
**RDMA CM** gives a sockets-like API (`rdma_resolve_addr` → `rdma_resolve_route` → `rdma_connect` / `rdma_accept`) that:

- uses IP addresses to identify peers (IPoIB addresses on IB, real IPs on RoCE),
- resolves the IB path record via the SA or the Ethernet route via the kernel, picking the correct **GID index** automatically,
- exchanges QPN/PSN via **MAD-based CM** (IB) or the same protocol encapsulated on RoCE, and transitions the QP for you,
- still requires *you* to exchange buffer addresses/rkeys (often in `private_data` of the connect request).

Nearly all production stacks (NVMe-oF, UCX, NCCL's default IB transport) use RDMA CM or an equivalent, because hand-rolled exchanges break on RoCE when GID selection is wrong. Note that **UD multicast** and **UD unicast** also work through RDMA CM (`rdma_join_multicast`) and that for UD you talk to many peers via **Address Handles** (AH), each wrapping a destination LID/GID + SL + port.

## 6. Transports compared

| Property | InfiniBand | RoCE v1 | RoCE v2 | iWARP |
|---|---|---|---|---|
| Layer 2 | IB link layer, LIDs | Ethernet; EtherType 0x8915 | Ethernet | Ethernet |
| Layer 3/4 | IB network/transport (GRH optional) | None — **not routable**, VLAN-bounded | **UDP/IP, dst port 4791**, routable, ECMP-hashable by src port | TCP/IP |
| Lossless requirement | Built in (credit FC) | Needs **PFC** (or lossy with limits) | Needs PFC and/or ECN+DCQCN; "lossy RoCE" possible with CX-5+ and good CC | TCP handles loss |
| Congestion control | IB CC / PCC | None standard | **DCQCN** via ECN+CNP | TCP |
| Addressing exposed to verbs | LID + optional GID | GID (MAC-derived, link-local) | GID (IPv4-mapped / IPv6 address) | IP |
| Fabric manager | SM required | Switches + DCB settings | Switches + DCB/QoS settings (NVUE RoCE profile) | None |
| Typical NVIDIA use | Quantum fabrics for SuperPOD | legacy only | **Spectrum-X**, cloud AI fabrics, storage | not supported on ConnectX |
| Latency (approx.) | lowest | low | low, slightly above IB | higher |

Practical consequences: the *verbs API is identical* across IB and RoCE — the differences are in addressing (GID vs LID), in what must be configured on the network (SM vs PFC/ECN), and in debugging tools (`sminfo`/`ibdiagnet` vs `mlnx_qos`/`ethtool -S`). Module 3.3 covers RoCE in depth.

## 7. MTU and path MTU

Two MTUs exist:

- **Link MTU** — the physical maximum IB packet payload on a port (256, 512, 1024, 2048, **4096**), set by the SM (`partition mtu=`) within switch/HCA capability. On RoCE the "link MTU" is the Ethernet MTU of the interface (e.g. 9000 or 9216).
- **Path MTU / active MTU** — negotiated per QP as the *minimum* along the path, from the IB-encoded set {256, 512, 1024, 2048, 4096}. `ibv_devinfo` shows `max_mtu` and `active_mtu`. On RoCE v2 the maximum RDMA MTU is **4096** even with a 9000-byte Ethernet MTU, because RoCE payload + headers (Ethernet 14 + IP 20 + UDP 8 + BTH 12 + ICRC 4 + payload) must fit in an Ethernet frame and the IB encoding only has power-of-two sizes; a 1500-byte Ethernet MTU limits RoCE to a 1024-byte path MTU, which costs significant throughput.

Messages larger than the path MTU are segmented by the HCA into packets; the application never sees segmentation, but small MTUs raise per-packet overhead and reduce bandwidth, so 4096 is the standard target everywhere.

## 8. rdma-core packages and inspection tools

`rdma-core` is the upstream user-space package set (libibverbs, librdmacm, ibverbs-providers incl. `mlx5`, rdma tooling). NVIDIA ships a tested version inside MLNX_OFED/DOCA-OFED along with extras (`mlnx-tools`, `infiniband-diags`, `perftest`, `mft`).

```bash
# Devices and ports
ibv_devices                        # device names + node GUIDs
ibv_devinfo -d mlx5_0              # fw, GUIDs, port state, link_layer, active_mtu, GID[0]
ibv_devinfo -v | grep -iE "max_qp|max_mr|max_cq|atomic|pkey_tbl_len"

rdma dev                           # kernel RDMA device list (node GUID, type)
rdma link                          # per-port state, physical_state, netdev binding (RoCE)
rdma link show mlx5_2/1            # one port
rdma resource show                 # QP/CQ/MR/PD counts per device (live)
rdma resource show qp              # list of QPs with state and PID
rdma statistic show                # hw counters (RoCE: out_of_sequence, cnp, etc.)
rdma system show                   # netns mode (shared/exclusive)

show_gids                          # NVIDIA script: table of DEV PORT INDEX GID IPv4 VER(v1/v2) NETDEV
ibdev2netdev -v                    # map mlx5_x to ethX/ibX and show link state
```

Interpretation hints: for IB `ibv_devinfo` shows `link_layer: InfiniBand`, a non-zero `port_lid` and `sm_lid`; for RoCE it shows `link_layer: Ethernet`, LID 0, and the GID table holds MAC-derived (v1) and IP-derived (v2) entries — pick the IPv4 **v2** entry's index for `-x`.

## 9. Why RDMA matters for AI: the software stack

```text
   PyTorch / JAX / Megatron
            │
   NCCL  (collectives: all-reduce, all-gather, reduce-scatter, all-to-all)
   NVSHMEM (one-sided GPU put/get, symmetric heap)
   UCX / UCC (MPI point-to-point and collectives, used by Open MPI, HPC-X)
            │
   libibverbs + librdmacm  (+ mlx5dv direct verbs, GPUDirect RDMA via nvidia-peermem/DMA-BUF)
            │
   ConnectX / BlueField HCA  ──  InfiniBand (Quantum)  or  RoCE v2 (Spectrum-X)
```

- **NCCL** implements its IB transport directly on verbs: per peer it creates RC QPs (`NCCL_IB_QPS_PER_CONNECTION`), registers GPU memory with **GPUDirect RDMA** so the HCA DMAs straight into HBM, and uses RDMA WRITE + flags to move chunks. Env knobs you will meet in this course: `NCCL_IB_HCA`, `NCCL_IB_GID_INDEX`, `NCCL_IB_TC`, `NCCL_IB_SL`, `NCCL_IB_TIMEOUT`, `NCCL_IB_RETRY_CNT`, `NCCL_IB_AR_THRESHOLD`, `NCCL_NET_GDR_LEVEL`.
- **NVSHMEM** exposes a PGAS model where a GPU kernel can `put`/`get` into a remote GPU's memory; it maps directly to one-sided RDMA WRITE/READ (and to NVLink inside the node).
- **UCX** is the general-purpose communication layer under MPI and some frameworks; it auto-selects RC/DC/UD transports and handles memory registration caches.
- **DOCA** (BlueField) and **DPDK** sit beside verbs for data-plane applications; DOCA RDMA wraps verbs with a DPU-friendly API ([Module 5](../05-bluefield-doca/)).

The importance of **memory registration** here: pinning large GPU buffers is expensive (hundreds of microseconds per registration), so every library caches registrations — registration churn shows up as throughput collapse and is a classic performance pitfall.

## 10. perftest: the RDMA benchmark suite

**perftest** is the standard tool for validating an RDMA link before blaming NCCL. The programs are named `ib_<op>_<metric>`:

| Program | Measures |
|---|---|
| **ib_write_bw** / **ib_write_lat** | RDMA WRITE bandwidth / latency |
| **ib_read_bw** / **ib_read_lat** | RDMA READ |
| **ib_send_bw** / **ib_send_lat** | SEND/RECV (two-sided) |
| **ib_atomic_bw** / **ib_atomic_lat** | Atomics |
| **raw_ethernet_bw** | Raw Ethernet packet injection (Spectrum testing) |

Server and client run the same binary; the client gets the server's IP (any IP — TCP is only used to exchange the connection parameters).

```bash
# Server
ib_write_bw -d mlx5_0 -i 1 -x 3 -F --report_gbits -a -q 4
# Client
ib_write_bw -d mlx5_0 -i 1 -x 3 -F --report_gbits -a -q 4 10.10.1.20

# Latency, single 8-byte message, 100k iterations
ib_write_lat -d mlx5_0 -s 8 -n 100000 -F  10.10.1.20

# Use RDMA CM instead of out-of-band socket exchange (resolves GID automatically)
ib_write_bw -d mlx5_0 -R -F --report_gbits 10.10.1.20
```

| Flag | Meaning |
|---|---|
| `-d <dev>` | RDMA device (`mlx5_0`) |
| `-i <port>` | HCA port number (1-based) |
| `-x <gid-index>` | GID index — **required for RoCE**; pick the RoCE v2 IPv4 entry from `show_gids`. Not needed on IB |
| `-q <n>` | Number of QPs (helps reach line rate on 400G) |
| `-s <bytes>` | Message size (default 65536 for bw, 2 for lat) |
| `-a` | Run all message sizes 2 B → 8 MB |
| `-F` | Do not fail on CPU frequency scaling warnings |
| `-R` | Use RDMA CM for connection setup |
| `-S <sl>` | Service level (IB QoS) |
| `--tclass <n>` | Traffic class / DSCP×4 for RoCE QoS |
| `-n <iters>` | Iterations |
| `-D <sec>` | Run for a duration instead of iterations |
| `-b` | Bidirectional |
| `-m <mtu>` | Force path MTU |
| `--report_gbits` | Report in Gb/s instead of MB/s |
| `--use_cuda=<gpu>` | Use GPU memory (GPUDirect RDMA test; perftest built with CUDA) |
| `--tx-depth`/`--rx-depth` | Queue depths |

Expected results (healthy): NDR 400G ~ 390 Gb/s with `-q 4 -s 1M`; HDR 200G ~ 195 Gb/s; RDMA WRITE latency ~1.0–1.5 µs on IB for 8 bytes, a bit higher on RoCE. Anything far off → check `active_mtu`, link width/speed (`ibstat`), PCIe width (`lspci -vv`, `mlxlink`), CPU frequency (`-F` only hides the warning), or on RoCE the GID index and PFC/ECN configuration.

## Configuration / command reference

| Task | Command |
|---|---|
| List devices | `ibv_devices`, `rdma dev` |
| Device/port details | `ibv_devinfo [-d dev] [-v]`, `rdma link` |
| GID table (RoCE) | `show_gids`, `cat /sys/class/infiniband/mlx5_0/ports/1/gids/*` |
| Device ↔ netdev | `ibdev2netdev -v`, `rdma link` |
| Live resources | `rdma resource show [qp|cq|mr|pd]` |
| HW counters | `rdma statistic show`, `cat /sys/class/infiniband/mlx5_0/ports/1/hw_counters/*` |
| Bandwidth test | `ib_write_bw -d mlx5_0 -i 1 [-x N] -q 4 -a -F --report_gbits [server-ip]` |
| Latency test | `ib_write_lat -d mlx5_0 -s 8 -n 100000 -F [server-ip]` |
| Two-sided test | `ib_send_bw ...` |
| Via RDMA CM | add `-R` |
| GPU memory test | `ib_write_bw --use_cuda=0 ...` |

## Common pitfalls & exam traps
- **lkey vs rkey**: lkey is used in *local* scatter/gather entries; rkey is given to the *remote* side for READ/WRITE/atomics. Using the wrong one yields a local or remote protection error.
- **RECV must be posted before the SEND arrives**, or the responder sends RNR NAK; one-sided WRITE/READ do not consume receive WQEs (except WRITE *with immediate*).
- **RDMA READ and atomics are RC-only** (and XRC/DC); UD supports only SEND/RECV with ≤ 1 MTU messages and is the only multicast-capable type.
- **RoCE v1 is not routable** (no IP header); RoCE v2 uses UDP 4791.
- **`-x <gid index>` is required for RoCE perftest** and must point at a RoCE v2 entry with an IPv4/IPv6 address; on IB it is omitted.
- **Path MTU is the min along the path from the set {256…4096}**; a 1500-byte Ethernet MTU limits RoCE to 1024.
- **RDMA CM still does not exchange your buffer addresses/rkeys**; it only sets up the QP.
- **Memory registration is expensive**; libraries cache it. Pinned memory also counts against `ulimit -l` (memlock) — set it to unlimited on RDMA hosts.
- Kernel bypass does not mean "no kernel": resource creation and connection setup go through the kernel driver; only the fast path bypasses it.

## Key takeaways
- RDMA = kernel bypass + zero copy + transport offload + one-sided operations; essential for 400G-per-GPU scale-out.
- Verbs objects: Context → PD → MR (lkey/rkey), CQ, QP (SQ+RQ), optional SRQ; WRs become WQEs, results are CQEs.
- QP types: RC (reliable, 1:1, all ops), UC, UD (datagram, multicast, SEND only), XRC and DC (scale RC).
- Two-sided SEND/RECV vs one-sided WRITE/READ/atomics; RNR NAK when no receive buffer.
- Connection needs peer QPN + LID/GID + PSN (+ addr/rkey for one-sided); RDMA CM automates the QP part using IP addresses.
- IB: lossless, SM-managed, LIDs. RoCE v2: UDP 4791, routable, needs PFC/ECN. RoCE v1: L2 only. iWARP: TCP, not on ConnectX.
- Path MTU = min of link MTUs along the path, ≤ 4096; standard target 4096.
- Tools: `ibv_devinfo`, `ibv_devices`, `rdma dev/link/resource/statistic`, `show_gids`, `ibdev2netdev`.
- NCCL/NVSHMEM/UCX all build on verbs + GPUDirect RDMA.
- perftest: `ib_write_bw -d -i -x -q -s -a -F -R --report_gbits`; `-x` only on RoCE.

## Review questions

1. Which key does an initiator place in an RDMA WRITE work request to address the remote buffer?
   A. lkey of its local MR
   B. rkey of the remote MR
   C. PKey of the partition
   D. QPN of the remote QP

2. A SEND arrives at a QP that has no posted receive work requests. What happens?
   A. The data is buffered in the HCA until a RECV is posted
   B. The responder returns an RNR NAK and the requester retries up to rnr_retry times
   C. The packet is silently dropped with no effect
   D. The QP transitions to RTR

3. Which QP transport type supports multicast?
   A. RC
   B. UC
   C. UD
   D. XRC

4. Which RoCE version encapsulates the IB transport in UDP/IP and can cross routers?
   A. RoCE v1
   B. RoCE v2
   C. iWARP
   D. Both v1 and v2

5. `ibv_devinfo` on a RoCE host shows `active_mtu: 1024 (3)` and `max_mtu: 4096 (5)`. What is the most likely cause?
   A. The HCA firmware is outdated
   B. The Ethernet interface MTU is 1500
   C. The SM set the partition MTU to 1024
   D. perftest was run with `-a`

6. An engineer runs `ib_write_bw -d mlx5_2 -F 10.0.3.5` against a RoCE server and the connection fails before any data moves. Which flag was most likely missing?
   A. `-q 4`
   B. `--report_gbits`
   C. `-x <gid index>`
   D. `-s 65536`

7. Which statement about RDMA CM is correct?
   A. It replaces the need to register memory
   B. It establishes the QP connection using IP addresses but does not exchange application buffer rkeys
   C. It works only on InfiniBand
   D. It is required for RDMA READ operations

8. What makes RDMA WRITE "one-sided"?
   A. It uses only one QP in total
   B. The remote CPU is not involved and no remote receive WQE is consumed
   C. Only one byte can be written at a time
   D. It is only available on RoCE

### Answer key
1. B — Remote access needs the remote address plus its rkey; lkey is for the local SGE.
2. B — Receiver Not Ready NAK; repeated RNRs eventually move the QP to error.
3. C — Multicast is a UD-only feature (IPoIB broadcast, for example).
4. B — v2 adds IP/UDP (port 4791); v1 is a plain Ethernet frame.
5. B — RoCE path MTU is bounded by the Ethernet MTU; 1500 bytes fits only a 1024-byte IB MTU.
6. C — RoCE needs a GID index; without it perftest defaults to index 0 (often a link-local/v1 GID) and fails.
7. B — RDMA CM resolves address/route and transitions the QP; rkeys are still the application's job (often via private_data).
8. B — The HCA writes directly into registered remote memory; the remote application is not notified unless immediate data is used.

## References
- NVIDIA Academy: *RDMA*
- RDMA Aware Networks Programming User Manual (NVIDIA/Mellanox) — https://docs.nvidia.com/networking/display/rdmaawareprogrammingv17
- rdma-core project and man pages (`ibv_devinfo(1)`, `rdma(8)`, `rdma_cm(7)`) — https://github.com/linux-rdma/rdma-core
- perftest README — https://github.com/linux-rdma/perftest
- NCCL environment variables — https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html
- MLNX_OFED / DOCA-OFED User Manual, "RDMA" and "InfiniBand Fabric Utilities" chapters — https://docs.nvidia.com/networking/
- Related modules: [3.2 RDMA Programming](02-rdma-programming-fundamentals.md), [3.3 RoCE A to Z](03-roce-a-to-z.md), [2.1 IB Administration](../02-infiniband/01-infiniband-network-administration.md)
