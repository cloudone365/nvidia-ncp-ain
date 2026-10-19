# Lab 3 — RDMA perftest and NCCL as Fabric Tests

## Objective
Measure a link with **ib_write_bw**, **ib_write_lat**, `ib_read_bw` and `ib_send_bw` in client/server mode, read the output tables, understand which flags matter on **RoCE** vs InfiniBand, run a GPUDirect variant with `--use_cuda`, and use nccl-tests as the application-level check.

## Environment
Pick the best you have:

| Option | Hardware | What is real |
|---|---|---|
| **A. Two hosts with ConnectX (IB or RoCE), ideally with GPUs** | Production-like | Everything, including realistic Gb/s and µs |
| **B. Two Linux hosts/VMs with ordinary NICs + Soft-RoCE (rxe)** | Any x86 machines, even VMs on a laptop | Every *command* and *output format* is real; the kernel implements RoCEv2 in software, so bandwidth is tens of Gb/s at best and latency is tens of µs. No GPU path |

Soft-RoCE setup (both hosts, kernel 5.x+ with `rdma_rxe`):

```bash
sudo apt install -y rdma-core ibverbs-utils perftest infiniband-diags   # Debian/Ubuntu
sudo modprobe rdma_rxe
sudo rdma link add rxe0 type rxe netdev eth0        # use your netdev name
rdma link show
ibv_devices                                          # shows rxe0
ibv_devinfo -d rxe0 | grep -E 'state|link_layer|active_mtu'
```

Expected: `rxe0` with `state: PORT_ACTIVE`, `link_layer: Ethernet`. Hosts must reach each other over `eth0` (RoCEv2 is UDP/4791 — open it in any firewall).

## Prerequisites
- Module `08-troubleshooting/01-infiniband-diagnostics-and-perftest.md` and `../03-rdma/`.
- Two hosts `hostA` (server) and `hostB` (client), IPs reachable (`ping`).
- Device names from `ibv_devices` (`mlx5_0` or `rxe0`). For RoCE, the GID index: `show_gids` (MLNX_OFED) or `ibv_devinfo -v -d <dev> | grep -A1 GID` — pick the RoCE v2 entry for the IPv4 address you will use.

## Tasks

### Task 1 — Baseline bandwidth, all message sizes

```bash
# hostA
ib_write_bw -d mlx5_0 -i 1 -F --report_gbits -a
# hostB
ib_write_bw -d mlx5_0 -i 1 -F --report_gbits -a <hostA-ip>
```

On RoCE add `-x <gid-index>` on **both** sides, or use `-R` (rdma_cm) which resolves the GID from the IP for you. Soft-RoCE: `-d rxe0 -R`.

Expected shape (NDR hardware):

```text
 #bytes  #iterations  BW peak[Gb/sec]  BW average[Gb/sec]  MsgRate[Mpps]
 2       5000         0.11             0.11                6.80
 4096    5000         189.3            188.9               5.76
 65536   5000         391.8            391.7               0.747
 8388608 5000         392.0            392.0               0.006
```

Write down: the size at which you reach ~95% of line rate, and the small-message message rate. Soft-RoCE will plateau far lower; the point is the table.

### Task 2 — Latency

```bash
# hostA
ib_write_lat -d mlx5_0 -i 1 -F -s 8 -n 20000
# hostB
ib_write_lat -d mlx5_0 -i 1 -F -s 8 -n 20000 <hostA-ip>
```

Expected columns: `t_min[usec] t_max[usec] t_typical[usec] t_avg[usec] t_stdev[usec] 99% percentile 99.9% percentile`. On NDR hardware typical ≈ 1.0–1.3 µs. Repeat with `-s 4096`. Then set the CPU governor to performance (`sudo cpupower frequency-set -g performance`) and compare `t_max` and 99.9% — observe that the host, not the fabric, drives tail latency.

### Task 3 — Read and send, QPs and duration

```bash
ib_read_bw -d mlx5_0 -F --report_gbits -s 1048576 -D 10        # 10-second run
ib_send_bw -d mlx5_0 -F --report_gbits -s 65536 -q 4 -D 10     # 4 QPs
ib_write_bw -d mlx5_0 -F --report_gbits -s 65536 -q 1 -D 10    # 1 QP, compare
```

Expected: RDMA Read is slightly below Write at the same size (round-trip dependency, more outstanding requests needed); Send is close to Write at large sizes but costs receiver CPU for posting receives. Note how much `-q 4` helps a single-QP result that was below line rate.

### Task 4 — Bidirectional and service level / traffic class

```bash
ib_write_bw -d mlx5_0 -F --report_gbits -s 65536 -b -D 10                 # both directions
ib_write_bw -d mlx5_0 -F --report_gbits -s 65536 -S 3 -D 10               # IB: service level 3
ib_write_bw -d mlx5_0 -F --report_gbits -s 65536 -R --tclass 106 -D 10    # RoCE: DSCP 26 (TC 106 >> 2)
```

Bidirectional should approach 2× unidirectional. On a RoCE fabric with the Lab 1 lossless profile, `--tclass 106` puts the test in the PFC/ECN class — the same class NCCL uses with `NCCL_IB_TC=106`. If your switch has NetQ/WJH (Lab 5), run a congested test (several clients → one server) with and without `--tclass` and watch tail drops appear in the lossy class only.

### Task 5 — GPUDirect RDMA (hardware with GPUs only)

Needs a perftest build with CUDA (`ib_write_bw --help | grep cuda`) and the `nvidia-peermem` module (`lsmod | grep nvidia_peermem`) or DMA-BUF support.

```bash
nvidia-smi topo -m                 # find which GPU shares a PCIe switch/NUMA with mlx5_0
# hostA
ib_write_bw -d mlx5_0 -F --report_gbits -a --use_cuda=0
# hostB
ib_write_bw -d mlx5_0 -F --report_gbits -a --use_cuda=0 <hostA-ip>
```

Expected: large-message bandwidth within a few percent of the host-memory result when GPU and NIC are PCIe-local (`PIX`/`PXB` in the topo matrix); noticeably lower when the path crosses CPU sockets (`SYS`). Repeat with a non-local GPU to see the difference.

### Task 6 — nccl-tests across the two hosts (GPU hosts)

```bash
git clone https://github.com/NVIDIA/nccl-tests && cd nccl-tests && make MPI=1 MPI_HOME=/usr/mpi/gcc/openmpi-*  # path varies
mpirun -np 2 -H hostA:1,hostB:1 -x NCCL_DEBUG=INFO -x NCCL_IB_HCA=mlx5_0 \
       -x NCCL_IB_TC=106 -x NCCL_IB_GID_INDEX=3 \
       ./build/all_reduce_perf -b 8 -e 1G -f 2 -g 1
```

(Drop `NCCL_IB_TC`/`NCCL_IB_GID_INDEX` on InfiniBand.) In the INFO log, find: `NET/IB : Using [0]mlx5_0:1/RoCE` (or `/IB`), `GDRDMA` if GPUDirect is used, and the selected GID index. In the results table, the `busbw` column at 1 GB should approach the NIC rate in GB/s (≈45+ GB/s for one 400G NIC per GPU pair; far less with a single NIC shared by more GPUs — reason about why).

## Verification checklist
- [ ] `ib_write_bw -a` runs client/server and reaches a plateau; you know the message size where it does.
- [ ] `ib_write_lat` typical latency recorded; governor effect on 99.9% observed.
- [ ] You can explain Write vs Read vs Send differences from your numbers.
- [ ] `-x`/`-R` understood: RoCE needs a GID; IB does not.
- [ ] `-q` effect on a single-QP result documented.
- [ ] (GPU) `--use_cuda` result and `nvidia-smi topo -m` locality compared.
- [ ] (GPU) nccl-tests INFO output shows the expected NIC, transport, and GDR usage.

## Break/fix challenge
1. **Wrong GID index.** Partner runs the server with `-x 3` and the client with `-x 1`. Symptom: hang after "Waiting for client" / "Couldn't connect" or completion errors. Fix by matching the index from `show_gids`, or switch to `-R`.
2. **Device mismatch on a multi-rail node.** Server uses `-d mlx5_0`, client `-d mlx5_4` (a different rail/subnet). Symptom: connection succeeds via TCP but the RDMA test fails or runs over a spine path with lower performance. Diagnose with `ibv_devinfo` (link layer, LID/GID) and `ibtracert`/`netq trace`.
3. **Degraded link (hardware).** Half-seat an OSFP or use a known-marginal cable. Symptom: `ib_write_bw` ~50% of expected; `iblinkinfo` shows `2X`; `mlxlink -c` shows errors. Fix and re-measure.
4. **Lossy class on RoCE.** Run several clients to one server without `--tclass` and observe drops / retransmits (`ethtool -S <if> | grep -i -E 'out_of_sequence|retrans|discard'`), then with `--tclass 106` in the lossless class. Relate to Lab 1's `nv show qos roce`.
5. **MTU.** Set `-m 1024` on one side only (or mismatch netdev MTU for Soft-RoCE). Symptom: test negotiates the lower MTU and bandwidth falls at mid sizes; verify with the `Mtu` line in the perftest header.

## Cleanup
```bash
# Soft-RoCE
sudo rdma link delete rxe0
# GPU hosts
rm -rf nccl-tests   # optional
```
