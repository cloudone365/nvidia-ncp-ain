# Module 3.2 — RDMA Programming Fundamentals

> **Exam domain:** NVIDIA InfiniBand Networking (30%) — concepts reused in Troubleshooting Tools (20%)  
> **Source course(s):** Fundamentals of RDMA Programming (NVIDIA Academy)  
> **Source docs:** RDMA Aware Networks Programming User Manual; `libibverbs` and `librdmacm` man pages (`ibv_create_qp(3)`, `ibv_modify_qp(3)`, `ibv_post_send(3)`, `rdma_cm(7)`); rdma-core `examples/` (rping, rc_pingpong)  
> **Estimated study time:** 5–6 hours

## Learning objectives
- Describe the sequence of libibverbs calls needed to move data between two hosts and what each call allocates.
- Describe the QP state machine and the attributes required for each transition.
- Explain work request flags (signaled, inline, fence, solicited), inline data and immediate data.
- Use librdmacm to establish connections and relate its event sequence to the raw verbs flow.
- Explain memory registration cost, pinning and why registration caches exist.
- Select the correct GID index for RoCE connections.
- Describe UD and multicast basics.
- Diagnose common verbs programming bugs from their completion error codes.
- Describe how DOCA, NVSHMEM, UCX and NCCL layer on top of verbs.

> This module is not about becoming an RDMA developer. The exam targets network engineers; what it tests is whether you can read an application's behaviour (and its errors) and map them to fabric causes. Code is shown to make the concepts concrete.

## 1. The minimal verbs program, step by step

Below is the skeleton of a two-node RDMA WRITE program (error handling removed). Both sides run the same code; they exchange connection data over a plain TCP socket (functions `exchange()` not shown).

### 1.1 Find and open a device

```c
#include <infiniband/verbs.h>

int num;
struct ibv_device **list = ibv_get_device_list(&num);      /* all RDMA devices (mlx5_0, mlx5_1 ...) */
struct ibv_device *dev = list[0];                          /* or match ibv_get_device_name(dev) */
struct ibv_context *ctx = ibv_open_device(dev);            /* device context: file descriptor + mapped UAR */
ibv_free_device_list(list);

struct ibv_port_attr pattr;
ibv_query_port(ctx, 1, &pattr);                            /* state, lid, active_mtu, link_layer */
```

Network-engineer reading: `ibv_open_device` is where `/dev/infiniband/uverbsN` is opened — the *only* kernel involvement on the data path later is none. `pattr.lid` is the LID the SM assigned; on RoCE it is 0 and `pattr.link_layer == IBV_LINK_LAYER_ETHERNET`.

### 1.2 Protection domain and memory registration

```c
struct ibv_pd *pd = ibv_alloc_pd(ctx);

size_t len = 1 << 20;                                      /* 1 MiB */
void *buf = aligned_alloc(4096, len);
struct ibv_mr *mr = ibv_reg_mr(pd, buf, len,
        IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_WRITE | IBV_ACCESS_REMOTE_READ);
/* mr->lkey : used in our own scatter/gather entries
   mr->rkey : handed to the peer so it can RDMA WRITE/READ this buffer      */
```

`ibv_reg_mr` pins the pages (they cannot be swapped or moved), translates virtual→physical for every page and programs the HCA's translation tables. The access flags are enforced by hardware: a remote WRITE to an MR without `REMOTE_WRITE` fails with a remote access error on the *initiator* side. For GPU memory, `cudaMalloc` pointers are registered the same way when GPUDirect RDMA (nvidia-peermem or DMA-BUF) is present.

### 1.3 Completion queue and queue pair

```c
struct ibv_cq *cq = ibv_create_cq(ctx, 256 /* min CQEs */, NULL, NULL, 0);

struct ibv_qp_init_attr qia = {
    .send_cq = cq, .recv_cq = cq,
    .cap = { .max_send_wr = 128, .max_recv_wr = 128,
             .max_send_sge = 1, .max_recv_sge = 1,
             .max_inline_data = 64 },                      /* bytes we may send inline */
    .qp_type = IBV_QPT_RC,
    .sq_sig_all = 0,                                       /* 0 = only WRs with IBV_SEND_SIGNALED complete */
};
struct ibv_qp *qp = ibv_create_qp(pd, &qia);               /* qp->qp_num is our QPN; state = RESET */
```

The QP is created in **RESET**. `sq_sig_all = 0` is the common choice and the source of a classic bug (§7).

### 1.4 Exchange connection parameters

```c
struct conn_info {
    uint16_t lid;  uint32_t qpn;  uint32_t psn;
    union ibv_gid gid;  uint64_t addr;  uint32_t rkey;
} local, remote;

local.lid = pattr.lid;  local.qpn = qp->qp_num;  local.psn = rand() & 0xffffff;
ibv_query_gid(ctx, 1, gid_index, &local.gid);              /* gid_index matters on RoCE (§6) */
local.addr = (uintptr_t)buf;  local.rkey = mr->rkey;
exchange(sock, &local, &remote);                           /* out-of-band, e.g. TCP */
```

This struct *is* the control plane. RDMA CM (§4) replaces the lid/qpn/psn/gid part; addr/rkey always remain the application's responsibility.

### 1.5 Move the QP to RTS

```c
/* RESET -> INIT */
struct ibv_qp_attr a = {0};
a.qp_state        = IBV_QPS_INIT;
a.pkey_index      = 0;                                     /* index into the port's PKey table (0 = default 0x7fff/0xffff) */
a.port_num        = 1;
a.qp_access_flags = IBV_ACCESS_REMOTE_WRITE | IBV_ACCESS_REMOTE_READ;
ibv_modify_qp(qp, &a, IBV_QP_STATE | IBV_QP_PKEY_INDEX | IBV_QP_PORT | IBV_QP_ACCESS_FLAGS);

/* receives may (and should) be posted here, before the peer can send */

/* INIT -> RTR : who we receive from */
memset(&a, 0, sizeof a);
a.qp_state           = IBV_QPS_RTR;
a.path_mtu           = IBV_MTU_4096;                       /* <= min(active_mtu) of both ports */
a.dest_qp_num        = remote.qpn;
a.rq_psn             = remote.psn;
a.max_dest_rd_atomic = 16;                                 /* inbound READ/atomic concurrency */
a.min_rnr_timer      = 12;                                 /* RNR NAK back-off, encoded */
a.ah_attr.dlid       = remote.lid;                         /* IB: destination LID */
a.ah_attr.sl         = 0;                                  /* service level (QoS, Module 2.3) */
a.ah_attr.port_num   = 1;
a.ah_attr.src_path_bits = 0;
/* RoCE (and IB with GRH): */
a.ah_attr.is_global          = 1;
a.ah_attr.grh.dgid           = remote.gid;
a.ah_attr.grh.sgid_index     = gid_index;                  /* which local GID (and thus RoCE version/IP) to use */
a.ah_attr.grh.hop_limit      = 64;
a.ah_attr.grh.traffic_class  = 0;                          /* RoCE: DSCP<<2 goes here (e.g. 106 for DSCP 26) */
ibv_modify_qp(qp, &a, IBV_QP_STATE | IBV_QP_AV | IBV_QP_PATH_MTU | IBV_QP_DEST_QPN |
                      IBV_QP_RQ_PSN | IBV_QP_MAX_DEST_RD_ATOMIC | IBV_QP_MIN_RNR_TIMER);

/* RTR -> RTS : how we send */
memset(&a, 0, sizeof a);
a.qp_state      = IBV_QPS_RTS;
a.sq_psn        = local.psn;
a.timeout       = 14;                                      /* ACK timeout, 4.096us * 2^timeout  (~67 ms) */
a.retry_cnt     = 7;                                       /* transport retries before IBV_WC_RETRY_EXC_ERR */
a.rnr_retry     = 7;                                       /* 7 = infinite RNR retries */
a.max_rd_atomic = 16;                                      /* outstanding outbound READ/atomic */
ibv_modify_qp(qp, &a, IBV_QP_STATE | IBV_QP_SQ_PSN | IBV_QP_TIMEOUT | IBV_QP_RETRY_CNT |
                      IBV_QP_RNR_RETRY | IBV_QP_MAX_QP_RD_ATOMIC);
```

Notice how much of the *network* is in these attributes: PKey index (partition), SL (QoS), path MTU, LID or GID (addressing), traffic class (RoCE DSCP), timeout/retry (how long a flow survives a link flap — NCCL exposes exactly these as `NCCL_IB_TIMEOUT` and `NCCL_IB_RETRY_CNT`).

### 1.6 Post work and poll completions

```c
/* Receive (two-sided only): post BEFORE the peer sends */
struct ibv_sge rsge = { .addr = (uintptr_t)buf, .length = len, .lkey = mr->lkey };
struct ibv_recv_wr rwr = { .wr_id = 1, .sg_list = &rsge, .num_sge = 1 }, *bad_r;
ibv_post_recv(qp, &rwr, &bad_r);

/* RDMA WRITE 64 KiB into the peer's buffer, signaled */
struct ibv_sge ssge = { .addr = (uintptr_t)buf, .length = 65536, .lkey = mr->lkey };
struct ibv_send_wr swr = {
    .wr_id      = 42,
    .sg_list    = &ssge, .num_sge = 1,
    .opcode     = IBV_WR_RDMA_WRITE,                       /* or IBV_WR_SEND, IBV_WR_RDMA_READ, IBV_WR_RDMA_WRITE_WITH_IMM ... */
    .send_flags = IBV_SEND_SIGNALED,
    .wr.rdma    = { .remote_addr = remote.addr, .rkey = remote.rkey },
}, *bad_s;
ibv_post_send(qp, &swr, &bad_s);

/* Poll */
struct ibv_wc wc;
while (ibv_poll_cq(cq, 1, &wc) == 0) ;                     /* busy poll; or ibv_req_notify_cq + ibv_get_cq_event */
if (wc.status != IBV_WC_SUCCESS)
    fprintf(stderr, "wr %lu failed: %s (vendor 0x%x)\n", wc.wr_id, ibv_wc_status_str(wc.status), wc.vendor_err);
```

`ibv_post_send` writes a WQE into the SQ ring and rings the HCA's doorbell with a single memory write — no syscall. Polling the CQ is likewise a memory read. For blocking behaviour, create the CQ with a **completion channel** (`ibv_create_comp_channel`), call `ibv_req_notify_cq`, and `ibv_get_cq_event` on its fd (which *is* a syscall — used when idle CPU matters).

Teardown is the reverse: `ibv_destroy_qp`, `ibv_destroy_cq`, `ibv_dereg_mr`, `ibv_dealloc_pd`, `ibv_close_device`.

## 2. The QP state machine

```text
          create                 modify(INIT)             modify(RTR)             modify(RTS)
  [RESET] ──────► (RESET) ─────────────► [INIT] ────────────────► [RTR] ─────────────────► [RTS]
                                           │                        │                        │  ▲
                                           │ post_recv allowed      │ receives processed     │  │ modify(SQD→RTS)
                                           ▼                        ▼                        ▼  │
                                    (sends rejected)          (sends rejected)          [SQD] ──┘  (drain, then re-configure path)
                                                                                           │
                            any state ── fatal error / modify(ERR) ───────────────► [ERR] ──┘   (all WRs flushed with IBV_WC_WR_FLUSH_ERR)
                                                                                      │
                                                            [SQE]  (UD/UC send error; RC goes straight to ERR)
```

| State | Can post recv? | Can post send? | Notes |
|---|---|---|---|
| **RESET** | No | No | Fresh QP; also the state to return to for reuse |
| **INIT** | **Yes** | No | PKey index, port, access flags set. Post receives here so they are ready before the peer reaches RTS |
| **RTR** — Ready to Receive | Yes | No | Destination QPN/PSN/path set; inbound packets accepted |
| **RTS** — Ready to Send | Yes | **Yes** | Full operation |
| **SQD** — Send Queue Drain | Yes | Posting allowed but not processed until back to RTS | Used for path migration / changing attributes safely |
| **SQE** — Send Queue Error | Yes | No | Only for UD/UC (unreliable types); RC skips to ERR |
| **ERR** | Flushes | Flushes | Every outstanding WR completes with `IBV_WC_WR_FLUSH_ERR`; application must reset or destroy the QP |

Exam angle: "the peer sends before this side is in RTR" → packets dropped/NAKed, so the standard handshake is *both sides reach RTR, then exchange a ready signal, then RTS*; RDMA CM sequences this for you. "A connection with retry_cnt exhausted" → `IBV_WC_RETRY_EXC_ERR` and QP in ERR; on the fabric that usually means a dead link, wrong LID/GID, or no route.

## 3. Work request flags, inline and immediate data

| Flag / feature | Meaning | Why you care |
|---|---|---|
| **`IBV_SEND_SIGNALED`** | Generate a CQE for this WR (when `sq_sig_all=0`) | Every N WRs must be signaled so the SQ can be reclaimed; see bug #1 |
| **`IBV_SEND_INLINE`** | Copy the payload into the WQE itself (≤ `max_inline_data`, typically ≤ 256–1024 bytes) instead of DMA-ing from the buffer | Lower latency for small messages; the buffer can be reused immediately after `post_send` returns and needs no lkey; this is why `ib_write_lat` small-message numbers are ~1 µs |
| **`IBV_SEND_FENCE`** | Wait for prior RDMA READs/atomics on this QP to complete before executing this WR | Ordering of a SEND that depends on READ results |
| **`IBV_SEND_SOLICITED`** | Set the solicited-event bit so a receiver waiting with `ibv_req_notify_cq(cq, 1)` is woken | Event-driven receivers |
| **Immediate data** (`IBV_WR_SEND_WITH_IMM`, `IBV_WR_RDMA_WRITE_WITH_IMM`, `.imm_data`) | 32 bits carried in the packet header and delivered in the receiver's CQE (`wc.imm_data`), consuming a receive WQE | Lets a one-sided WRITE also notify the receiver ("data landed, here is a tag") — NCCL and NVSHMEM-style signalling patterns |
| `wr_id` | Opaque 64-bit cookie returned in the CQE | Correlate completions with buffers |

Opcode cheat-sheet: `IBV_WR_SEND`, `IBV_WR_SEND_WITH_IMM`, `IBV_WR_RDMA_WRITE`, `IBV_WR_RDMA_WRITE_WITH_IMM`, `IBV_WR_RDMA_READ`, `IBV_WR_ATOMIC_CMP_AND_SWP`, `IBV_WR_ATOMIC_FETCH_AND_ADD`. Completion opcodes mirror them (`IBV_WC_RDMA_WRITE`, `IBV_WC_RECV`, `IBV_WC_RECV_RDMA_WITH_IMM`).

## 4. librdmacm: connection management done for you

**librdmacm** (RDMA CM) models connections on sockets and hides the LID/GID/QPN/PSN exchange and the state transitions. The key abstraction is the `rdma_cm_id` (like a socket), driven by events from an `rdma_event_channel`.

```c
#include <rdma/rdma_cma.h>

struct rdma_event_channel *ec = rdma_create_event_channel();
struct rdma_cm_id *id;
rdma_create_id(ec, &id, NULL, RDMA_PS_TCP);                /* RDMA_PS_TCP = reliable connected (RC); RDMA_PS_UDP = UD */

/* ---- client ---- */
rdma_resolve_addr(id, NULL, (struct sockaddr *)&server, 2000);   /* IP -> RDMA device, port, GID (picks GID index!) */
/* wait: RDMA_CM_EVENT_ADDR_RESOLVED  -> now id->verbs (context) and id->port_num are known */
pd = ibv_alloc_pd(id->verbs);  cq = ibv_create_cq(id->verbs, ...);  mr = ibv_reg_mr(pd, ...);
rdma_create_qp(id, pd, &qp_init_attr);                     /* QP created and moved to INIT for you */
rdma_resolve_route(id, 2000);                              /* IB: SA path record (SL, MTU, DLID); RoCE: ARP/ND + route */
/* wait: RDMA_CM_EVENT_ROUTE_RESOLVED */
struct rdma_conn_param cp = { .private_data = &my_rkey_and_addr, .private_data_len = 12,
                              .responder_resources = 16, .initiator_depth = 16, .retry_count = 7, .rnr_retry_count = 7 };
rdma_connect(id, &cp);                                     /* sends CM REQ; QP -> RTR -> RTS as the REP arrives */
/* wait: RDMA_CM_EVENT_ESTABLISHED   (or REJECTED / UNREACHABLE / CONNECT_ERROR) */

/* ---- server ---- */
rdma_bind_addr(listen_id, (struct sockaddr *)&local);  rdma_listen(listen_id, 10);
/* wait: RDMA_CM_EVENT_CONNECT_REQUEST  -> event->id is the new per-connection id, event->param.conn.private_data has the client's rkey */
rdma_create_qp(event->id, pd, &qp_init_attr);  post receives;  rdma_accept(event->id, &cp);
/* wait: RDMA_CM_EVENT_ESTABLISHED */

/* both: rdma_get_cm_event(ec, &event) ... rdma_ack_cm_event(event) ; on exit rdma_disconnect -> RDMA_CM_EVENT_DISCONNECTED */
```

Why network engineers should know this flow: the *error events* map to fabric problems. `ADDR_ERROR` = the IP does not map to an RDMA device (wrong interface, missing IPoIB address); `ROUTE_ERROR` on IB = SA path query failed (no SM, PKey mismatch); `UNREACHABLE`/`CONNECT_ERROR` on RoCE = GID/MAC resolution or a firewalled UDP 4791; `REJECTED` = the peer is listening on a different port space or refused (e.g. QP attribute mismatch).

### 4.1 rping
**rping** (`rdma-core` examples) is the smallest useful RDMA CM program: server `rping -s -a <ip> -v`, client `rping -c -a <server-ip> -v -C 10`. It performs connect → RDMA WRITE ping → RDMA READ pong, printing each payload. It is the RDMA equivalent of `ping` and the first thing to run when "RDMA doesn't work" — it validates device mapping, GID selection, PKey/route resolution and that READ/WRITE actually pass, independent of NCCL. `udaddy` (UD), `ucmatose` (RC), `rdma_server`/`rdma_client` (the simplified synchronous `rdma_cm` API) are its siblings.

## 5. Memory registration cost and pinning

Registration does three expensive things: it faults in and **pins** every page (the kernel marks them unmovable, and they count against `RLIMIT_MEMLOCK`), builds the HCA's address translation (MTT) entries, and writes them to the device. Order of magnitude: tens of microseconds for small buffers, milliseconds for gigabytes. Deregistration is similarly costly. Consequences:

- `ulimit -l unlimited` (memlock) is mandatory on RDMA hosts; a container with a low memlock limit fails with `ibv_reg_mr: Cannot allocate memory`. Kubernetes pods need the `IPC_LOCK` capability or the limit raised (Module 7).
- Libraries keep **registration caches** (UCX `rcache`, NCCL's registered buffer pools) and use large, long-lived buffers. Churn in registration shows up as bandwidth collapse and high CPU in `ibv_reg_mr`.
- **On-Demand Paging (ODP)** (`IBV_ACCESS_ON_DEMAND`) and **implicit ODP** let the HCA page-fault instead of pinning, trading latency spikes for flexibility.
- **GPUDirect RDMA**: registering `cudaMalloc` memory requires the `nvidia-peermem` kernel module (or DMA-BUF on newer kernels/drivers); `NCCL_NET_GDR_LEVEL` controls when NCCL uses it based on PCIe topology. Without it, NCCL bounces through host memory — visible as halved bandwidth and CPU load.
- **Relaxed ordering** (`IBV_ACCESS_RELAXED_ORDERING`) improves PCIe efficiency to GPUs behind switches; firmware `mlxconfig` knobs (`PCI_WR_ORDERING`) interact with it.

## 6. GID index selection for RoCE

On RoCE there are no LIDs; every connection is addressed by **GID**, and a port's GID *table* contains several entries created automatically by the kernel from the netdev's addresses:

```text
$ show_gids
DEV     PORT  INDEX  GID                                      IPv4           VER  DEV
mlx5_2  1     0      fe80:0000:0000:0000:b83f:d2ff:fe00:1122                 v1   ens1f0
mlx5_2  1     1      fe80:0000:0000:0000:b83f:d2ff:fe00:1122                 v2   ens1f0
mlx5_2  1     2      0000:0000:0000:0000:0000:ffff:0a0a:0114  10.10.1.20     v1   ens1f0
mlx5_2  1     3      0000:0000:0000:0000:0000:ffff:0a0a:0114  10.10.1.20     v2   ens1f0
mlx5_2  1     4      ...  (one pair per VLAN sub-interface or extra IP)
```

Each IP (and link-local) appears twice: once as **RoCE v1** and once as **RoCE v2**. The `sgid_index` you place in the address vector (or `-x` in perftest, `NCCL_IB_GID_INDEX` in NCCL) decides *both* the source IP and the RoCE version of the packets you emit. Rules:

- Use a **v2** entry with the IPv4/IPv6 address on the interface that routes to the peer (index 3 above). Index 0/1 (link-local) only works on the same L2 segment.
- Entries on VLAN sub-interfaces get their own indices; the VLAN tag is inserted by the HCA.
- RDMA CM selects the index automatically from the IP you connect to — one more reason to prefer it. NCCL ≥ 2.12 also auto-detects when `NCCL_IB_GID_INDEX` is unset (check release notes; older versions default to 0, which breaks routed RoCE).
- `cma_roce_mode -d mlx5_2 -p 1` shows/sets the default RoCE version RDMA CM uses (details in [Module 3.3](03-roce-a-to-z.md)).

## 7. UD and multicast basics

UD QPs have no connection: every `ibv_post_send` carries an **Address Handle** (`ibv_create_ah` from an `ibv_ah_attr` with DLID/GID/SL) plus the destination QPN and **Q_Key** (a 32-bit shared secret that must match on both sides). Messages are limited to one path MTU; each receive buffer must leave **40 bytes** at the front for the GRH, which the HCA deposits even on IB when present. For **multicast**: the application joins a multicast group (MGID) via the SA (`rdma_join_multicast` or `ibv_attach_mcast`), the SM programs the switches' multicast forwarding tables, and a SEND to the MLID/MGID is replicated by the fabric. IPoIB uses exactly this for broadcast/ARP, which is why a missing `ipoib` flag in partitions.conf (no multicast group) breaks IP over a PKey.

## 8. Common bugs and their symptoms

| Bug | Symptom (completion status / behaviour) | Explanation |
|---|---|---|
| Posting many WRs with `sq_sig_all=0` and **no `IBV_SEND_SIGNALED`** | `ibv_post_send` eventually returns `ENOMEM`; throughput stalls | The SQ cannot be reclaimed without completions; signal at least every `max_send_wr/2` WRs |
| **No receive buffers posted** when peer SENDs | Peer sees `IBV_WC_RNR_RETRY_EXC_ERR` (after retries) or stalls with `rnr_retry=7`; `rdma statistic` shows `rnr_nak_retry_err` / `rx_rnr` counters rising | Receiver Not Ready NAK; post receives in INIT, replenish as you consume |
| **Wrong rkey or address**, or MR lacks `REMOTE_WRITE/READ` | Initiator: `IBV_WC_REM_ACCESS_ERR`; QP → ERR; responder may log `remote access error` and `local_protection` counters | HCA checks rkey + address range + permission on every RDMA op |
| Wrong **lkey** / buffer freed | `IBV_WC_LOC_PROT_ERR` | Local SGE does not match a valid MR in this PD |
| **MTU mismatch**: `path_mtu` larger than what the path supports | Connection hangs; `IBV_WC_RETRY_EXC_ERR`; on RoCE, switch drops oversized frames with no NAK | Query `active_mtu` on both sides, use the minimum; on RoCE raise Ethernet MTU to ≥ 4200 for 4096 path MTU |
| Wrong **GID index** on RoCE (v1 vs v2, link-local vs IP) | Connect time-out / `IBV_WC_RETRY_EXC_ERR`; `tcpdump` shows no UDP 4791 or wrong source IP | GID index sets source IP and RoCE version |
| **PKey mismatch** / wrong `pkey_index` | IB: packets silently dropped; `PortRcvConstraintErrors` increments on the switch/HCA; SA path lookup fails via RDMA CM | Partition enforcement is per packet |
| `retry_cnt`/`timeout` too low for a flapping link | `IBV_WC_RETRY_EXC_ERR` during brief outages | NCCL defaults `NCCL_IB_TIMEOUT=18`, `NCCL_IB_RETRY_CNT=7` to survive SM re-sweeps |
| Low **memlock** limit | `ibv_reg_mr` fails with `ENOMEM` | Pinning limit; set `ulimit -l unlimited` / `IPC_LOCK` |
| Peer not yet in RTR when first SEND arrives | First message lost / NAK; works on retry | Synchronise "both in RTR" before RTS, or use RDMA CM |
| Using the same CQ from many threads without locking | Lost or duplicated completions | Verbs objects are not thread-safe except where documented |

The status strings come from `ibv_wc_status_str()`; `wc.vendor_err` plus `dmesg`/`mlx5_core` syndromes give the hardware detail. From the *network* side, correlate with `rdma statistic show`, `ethtool -S` (RoCE), and port counters/`PortXmitDiscards` (IB).

## 9. How DOCA, NVSHMEM, UCX and NCCL build on verbs

| Layer | Relationship to verbs | What it adds |
|---|---|---|
| **UCX** | Thin transport abstraction over `libibverbs`/`mlx5dv`, shared memory, CUDA IPC, TCP | Picks RC/DC/UD per message size and scale, registration cache, rendezvous vs eager protocols, GPU memory awareness. Used by Open MPI/HPC-X, NVSHMEM (optionally), and as the base of UCC collectives |
| **NCCL** | Its `net_ib` plugin calls verbs directly (RC QPs, RDMA WRITE + flags/immediate, registered GPU buffers via GPUDirect RDMA); can be replaced by external *net plugins* (`libnccl-net.so`) such as the SHARP/CollNet plugin or a UCX plugin | Topology-aware collective algorithms (ring, tree, CollNet, NVLS), channel/QP management, `NCCL_IB_*` knobs that map 1:1 to the QP attributes in §1.5 |
| **NVSHMEM** | One-sided put/get from GPU kernels → RDMA WRITE/READ and atomics through either its own IBRC/IBGDA transport (GPU-initiated verbs via `mlx5dv`, the HCA doorbell rung from the GPU) or UCX | Symmetric heap, GPU-side synchronisation, lowest latency for fine-grained communication |
| **DOCA** (BlueField) | DOCA RDMA and DOCA Comch wrap verbs/`mlx5dv` with a device-model API usable on host or DPU; DOCA also exposes flow steering, DPA (data-path accelerator) programming | Programmable offloads on the DPU; Module 5 |
| **DPDK / mlx5dv direct verbs** | Bypass even libibverbs' generic layer to post WQEs directly | Used by high-performance middleware; not exam-critical beyond knowing it exists |

The thread running through all of them: *one-sided RDMA WRITE with registered, pinned GPU memory* is the primitive that lets a GPU in rack 12 land data in a GPU in rack 40 without any CPU on either end — and every one of the fabric properties in this course (losslessness, MTU, QoS, adaptive routing, PFC/ECN) is in service of that primitive.

## Configuration / command reference

| Task | Verbs / tool |
|---|---|
| Enumerate and open | `ibv_get_device_list` → `ibv_open_device` → `ibv_query_port` / `ibv_query_gid` |
| Resources | `ibv_alloc_pd`, `ibv_reg_mr(pd, buf, len, access)`, `ibv_create_cq`, `ibv_create_qp` |
| State transitions | `ibv_modify_qp` with masks `IBV_QP_STATE | IBV_QP_PKEY_INDEX | IBV_QP_PORT | IBV_QP_ACCESS_FLAGS` (INIT); `… | IBV_QP_AV | IBV_QP_PATH_MTU | IBV_QP_DEST_QPN | IBV_QP_RQ_PSN | IBV_QP_MAX_DEST_RD_ATOMIC | IBV_QP_MIN_RNR_TIMER` (RTR); `… | IBV_QP_SQ_PSN | IBV_QP_TIMEOUT | IBV_QP_RETRY_CNT | IBV_QP_RNR_RETRY | IBV_QP_MAX_QP_RD_ATOMIC` (RTS) |
| Data path | `ibv_post_recv`, `ibv_post_send`, `ibv_poll_cq`, `ibv_req_notify_cq` + `ibv_get_cq_event` |
| RDMA CM | `rdma_create_event_channel`, `rdma_create_id`, `rdma_resolve_addr`, `rdma_resolve_route`, `rdma_create_qp`, `rdma_connect` / `rdma_listen` + `rdma_accept`, `rdma_get_cm_event`, `rdma_ack_cm_event`, `rdma_disconnect` |
| Quick connectivity test | `rping -s -a <ip> -v` / `rping -c -a <ip> -v -C 10` |
| GID table | `show_gids`, `ibv_devinfo -v` (GID[n]), `cma_roce_mode -d <dev> -p <port>` |
| Live QP state | `rdma resource show qp` (state, pid), `rdma statistic show` |
| Memlock | `ulimit -l unlimited`; `/etc/security/limits.conf` `* - memlock unlimited` |
| Error decode | `ibv_wc_status_str()`, `dmesg | grep mlx5` |

## Common pitfalls & exam traps
- QP creation order: context → PD → MR/CQ → QP; the QP starts in **RESET**, receives may be posted from **INIT**, sends only in **RTS**.
- INIT needs PKey index/port/access flags; RTR needs the *peer's* QPN, PSN and address (LID or GID) plus path MTU; RTS needs *our* PSN plus timeout/retry counts.
- `IBV_SEND_SIGNALED` controls completions when `sq_sig_all=0`; forgetting it fills the SQ.
- `IBV_SEND_INLINE` copies data into the WQE (no lkey needed, buffer reusable immediately) and is limited by `max_inline_data`.
- Immediate data is 32 bits, consumes a receive WQE even for RDMA WRITE with immediate.
- RDMA CM exchanges QPN/PSN/GID and transitions the QP; it does **not** exchange rkeys/addresses (use `private_data`).
- On RoCE the **GID index** determines source IP and RoCE version; always use a v2 IP-based entry for routed fabrics.
- `IBV_WC_REM_ACCESS_ERR` = rkey/permission problem; `IBV_WC_RETRY_EXC_ERR` = peer unreachable/ dead link/MTU; `IBV_WC_RNR_RETRY_EXC_ERR` = no receive buffers; `IBV_WC_WR_FLUSH_ERR` = QP went to error, all WRs flushed.
- UD receive buffers need 40 bytes for the GRH; UD messages are ≤ one MTU; Q_Key must match.
- Memory registration pins pages and needs memlock; libraries cache registrations for a reason.

## Key takeaways
- Minimal flow: `ibv_get_device_list` → `ibv_open_device` → `ibv_alloc_pd` → `ibv_reg_mr` → `ibv_create_cq` → `ibv_create_qp` → `ibv_modify_qp` (INIT→RTR→RTS) → `ibv_post_recv`/`ibv_post_send` → `ibv_poll_cq`.
- QP states: RESET → INIT → RTR → RTS (+ SQD, SQE, ERR). Receives in INIT, sends in RTS.
- QP attributes encode the network: pkey_index, SL, path MTU, DLID/GID+sgid_index, traffic_class, timeout, retry_cnt, rnr_retry.
- Flags: SIGNALED (CQE), INLINE (payload in WQE), FENCE, SOLICITED; immediate data = 32 bits in header, delivered via a receive CQE.
- librdmacm: resolve_addr → resolve_route → connect/accept, events via rdma_get_cm_event; rping is the RDMA "ping".
- Registration pins memory; memlock unlimited; registration caches; GPUDirect RDMA needs nvidia-peermem/DMA-BUF.
- RoCE GID index = source IP + RoCE version; pick the v2 IP entry.
- UD: address handles, Q_Key, 1-MTU messages, 40-byte GRH space; multicast via SA join and SM-programmed MFTs.
- Error map: REM_ACCESS_ERR (rkey), RETRY_EXC_ERR (path/MTU/link), RNR_RETRY_EXC_ERR (no RECV), WR_FLUSH_ERR (QP in ERR).
- UCX, NCCL, NVSHMEM and DOCA all sit on verbs/mlx5dv and one-sided RDMA WRITE into pinned GPU memory.

## Review questions

1. In which QP state should receive work requests first be posted so they are available before the peer can send?
   A. RESET
   B. INIT
   C. RTS
   D. SQD

2. A developer sets `sq_sig_all = 0` and posts thousands of RDMA WRITEs without `IBV_SEND_SIGNALED`. What happens?
   A. Latency improves indefinitely
   B. `ibv_post_send` eventually fails because the send queue cannot be reclaimed
   C. The QP moves to RTR
   D. The HCA automatically signals every 16th WR

3. A client's RDMA WRITE completes with `IBV_WC_REM_ACCESS_ERR`. Which is the most likely cause?
   A. The remote host has no receive buffers posted
   B. The rkey or address is wrong, or the remote MR lacks `IBV_ACCESS_REMOTE_WRITE`
   C. The path MTU is too large
   D. The GID index is wrong

4. Which attributes are mandatory in the INIT→RTR transition for an RC QP?
   A. Local PSN, timeout, retry count
   B. Destination QPN, remote PSN, path MTU, address vector
   C. PKey index, port, access flags
   D. Q_Key and multicast GID

5. What does `IBV_SEND_INLINE` change?
   A. The payload is copied into the WQE, so no lkey/DMA from the buffer is needed and the buffer may be reused at once
   B. The message bypasses the CQ
   C. The payload is sent uncompressed
   D. The message is sent on VL15

6. On a routed RoCE v2 fabric, perftest fails to connect when started with `-x 0`. `show_gids` shows index 0 as a link-local v1 GID and index 3 as the IPv4 v2 GID. What should be done?
   A. Use `-x 3`
   B. Raise `retry_cnt`
   C. Switch to RoCE v1 on the switch
   D. Disable PFC

7. Which librdmacm call sequence does a client use to connect?
   A. `rdma_bind_addr` → `rdma_listen` → `rdma_accept`
   B. `rdma_resolve_addr` → `rdma_resolve_route` → `rdma_connect`
   C. `ibv_modify_qp` three times
   D. `rdma_join_multicast` → `rdma_connect`

8. A UD application's receive buffers are exactly the message size and the first 40 bytes of every message appear corrupted. Why?
   A. The Q_Key is wrong
   B. The HCA writes the 40-byte GRH at the start of each UD receive buffer
   C. The MTU is too small
   D. The SL is not in the AR mask

### Answer key
1. B — Receives can be posted from INIT; doing so before the peer reaches RTS avoids RNR NAKs.
2. B — Without completions the HCA cannot tell the SQ entries are free; signal periodically.
3. B — Remote access errors are rkey/address/permission failures detected by the responder HCA.
4. B — RTR configures who we receive from: dest QPN, rq_psn, path MTU, AV (plus max_dest_rd_atomic and min_rnr_timer).
5. A — Inline data is embedded in the WQE up to `max_inline_data`; it lowers small-message latency.
6. A — The GID index selects the source IP and RoCE version; link-local v1 cannot cross routers.
7. B — Address, then route, then connect; events report each stage.
8. B — UD receives always reserve 40 bytes for the (possibly absent) GRH.

## References
- NVIDIA Academy: *Fundamentals of RDMA Programming*
- RDMA Aware Networks Programming User Manual — https://docs.nvidia.com/networking/display/rdmaawareprogrammingv17
- rdma-core man pages: `ibv_modify_qp(3)`, `ibv_post_send(3)`, `ibv_reg_mr(3)`, `rdma_cm(7)`, `rping(1)` — https://github.com/linux-rdma/rdma-core
- rdma-core `libibverbs/examples/rc_pingpong.c`, `ud_pingpong.c`; `librdmacm/examples/rping.c`
- NCCL environment variables (`NCCL_IB_GID_INDEX`, `NCCL_IB_TIMEOUT`, `NCCL_IB_RETRY_CNT`, `NCCL_NET_GDR_LEVEL`) — https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html
- NVIDIA GPUDirect RDMA documentation — https://docs.nvidia.com/cuda/gpudirect-rdma/
- UCX documentation — https://openucx.readthedocs.io/ ; NVSHMEM documentation — https://docs.nvidia.com/nvshmem/
- Related modules: [3.1 RDMA Fundamentals](01-rdma-fundamentals.md), [3.3 RoCE A to Z](03-roce-a-to-z.md), [2.3 IB QoS/AR/SHARP](../02-infiniband/03-infiniband-qos-adaptive-routing-sharp.md), Module 5 DOCA (`../05-bluefield-doca/`)
