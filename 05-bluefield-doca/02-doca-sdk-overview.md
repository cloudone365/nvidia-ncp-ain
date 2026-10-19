# Module 5.2 — DOCA SDK Overview and SuperNIC Configuration

> **Exam domain:** NVIDIA Spectrum Networking (30%) — "install DOCA and configure SuperNIC functions"; also Automation and Configuration (10%)  
> **Source course(s):** BlueField DPU Administration (NVIDIA Academy, DOCA sections)  
> **Source docs:** NVIDIA DOCA SDK Documentation; DOCA Installation Guide for Linux; DOCA Profiles; DOCA Release Notes (compatibility matrix); DOCA HBN Service Guide; DOCA Telemetry Service Guide; Spectrum-X RoCE configuration guides  
> **Estimated study time:** 4 hours

## Learning objectives
- Describe what **DOCA** is and how it replaced MLNX_OFED as the host driver stack (DOCA-OFED).
- Explain DOCA installation profiles and install the correct profile on a host or a BlueField Arm.
- Summarise the main DOCA libraries (Flow, RDMA, DMA, Comm Channel, DPA, Telemetry, App Shield, Firefly) and what each offloads.
- Describe DOCA services on BlueField — DOCA Telemetry Service and Host-Based Networking (HBN) — and the HBN use case.
- Explain DOCA version compatibility with firmware and the BlueField BSP.
- Configure and verify **SuperNIC** functions for Spectrum-X (RoCE adaptive routing / DDP, congestion control) across `mlxconfig`, DOCA and the switch.
- Verify a DOCA installation with `ofed_info -s`, `dpkg -l | grep doca` and `mlxfwmanager --query`.

## 1. What DOCA is

**DOCA** (Data Center Infrastructure-on-a-Chip Architecture) is NVIDIA's software framework for BlueField DPUs/SuperNICs and ConnectX NICs. It is best understood as three layers that ship together under one version number:

| Layer | Contents | Who uses it |
|---|---|---|
| **Drivers / runtime** | The `mlx5` kernel drivers, RDMA core (`rdma-core`, `libibverbs`), MFT tools (`mlxconfig`, `mlxlink`, `mlxfwmanager`), `rshim`, OVS with offload, firmware | Every host or DPU with an NVIDIA NIC — this is the piece that *replaced MLNX_OFED* |
| **Libraries (SDK)** | C/C++ APIs that expose hardware accelerators: DOCA Flow, DOCA RDMA, DOCA DMA, DOCA DPA, DOCA Comm Channel, DOCA Telemetry, DOCA App Shield, DOCA Firefly, DOCA Compress, DOCA Crypto/IPsec, DOCA RegEx (BF-2) | Developers building offloaded applications |
| **Services** | Ready-to-run containers for the BlueField Arm: HBN, Telemetry Service (DTS), Firefly (PTP), Flow Inspector, BlueMan (UI), UROM, SNAP (storage emulation, separate license) | Operators deploying DPU functions without coding |

### 1.1 DOCA-OFED replaces MLNX_OFED
Through 2023, NVIDIA host networking drivers were distributed as **MLNX_OFED** (`mlnxofedinstall`). Starting with DOCA 2.x (MLNX_OFED's last release was 23.10), the same drivers are delivered as the **DOCA-OFED** profile inside the DOCA repository. For an administrator this means:
- You add the DOCA apt/yum repo and install a *profile* package instead of running `mlnxofedinstall`.
- `ofed_info -s` still works and prints something like `OFED-internal-24.x` / `MLNX_OFED_LINUX-24.xx` even on a DOCA install, because the underlying packages keep their names.
- Inbox (distro) drivers remain an option for basic connectivity but lack the newest features and firmware tooling; NVIDIA reference designs assume DOCA-OFED.

Exam phrasing: "MLNX_OFED is end-of-life; install DOCA (the `doca-ofed` or `doca-all` profile) on hosts."

## 2. DOCA profiles and installation

A **profile** is a meta-package that pulls in a curated set of DOCA packages. Names below reflect DOCA 2.x–3.x documentation; the set has shifted between releases, so verify against the "DOCA Profiles" page for your version.

| Profile | Contents | Install on |
|---|---|---|
| `doca-all` | Everything: drivers, SDK libraries, tools, samples, services runtime | Developer hosts; BlueField Arm (already in the BFB) |
| `doca-networking` | Drivers + networking-oriented libraries (Flow, DPA, Comm Channel, RDMA) and tools, without storage/crypto extras | Hosts running DOCA-offloaded networking apps |
| `doca-ofed` | Drivers, RDMA core, MFT, OVS — the MLNX_OFED equivalent, no SDK libraries | Production GPU/compute hosts that just need RoCE/IB + tools |
| `doca-roce` | Lean driver profile aimed at RoCE-only hosts (introduced in later releases — verify existence/name) | Minimal GPU hosts |
| `doca-devel` / `doca-sdk` / `doca-runtime` style sub-profiles | Finer-grained SDK vs runtime splits in newer releases | Verify in current docs |

### 2.1 Installing on a host (Ubuntu example)
```bash
# 1. Add the repo (use the exact URL from the DOCA download page for your distro/version)
wget https://www.mellanox.com/downloads/DOCA/DOCA_v2.x/host/doca-host_2.x.x-xxxxxx-ubuntu2204_amd64.deb
sudo dpkg -i doca-host_2.x.x-*.deb            # installs the repo definition and GPG key
sudo apt update

# 2. Install a profile
sudo apt install -y doca-ofed                 # OFED-equivalent for a GPU compute node
# or
sudo apt install -y doca-all                  # full SDK

# 3. Load drivers / reboot
sudo /etc/init.d/openibd restart              # or reboot
```
RHEL/Rocky use `dnf install doca-host-*.rpm` then `dnf install doca-ofed`. The `doca-host` package is the repository bootstrap; the profile is the real install. For offline hosts, NVIDIA provides a full tarball with all packages.

### 2.2 On the BlueField Arm
The BFB image already contains DOCA (runtime + services + SDK depending on BFB flavour). Updating DOCA on the Arm is normally done by installing a new BFB (Module 5.1 §5) rather than `apt upgrade`, to keep BSP, firmware and DOCA aligned. Container services are pulled from NGC (`nvcr.io/nvidia/doca/...`).

### 2.3 Verify
```bash
ofed_info -s                        # driver stack version string
dpkg -l | grep -i doca              # installed DOCA packages and version (rpm -qa | grep doca on RHEL)
dpkg -l | grep -E 'mlnx|rdma-core|mft'
modinfo mlx5_core | grep ^version
ibstat; ibv_devinfo                 # RDMA devices up, link_layer Ethernet
mlxfwmanager --query                # firmware present vs available — must match DOCA matrix
mst status -v
```
A healthy install shows `mlx5_core` from the DOCA package (not inbox), RDMA devices in `ibv_devinfo`, and firmware within the release's supported range.

## 3. DOCA libraries overview

| Library | What it offloads / provides | Typical use |
|---|---|---|
| **DOCA Flow** | Hardware flow steering pipelines (match → action → forward), counters, meters, mirroring | Build a line-rate firewall, load balancer or tenant classifier on the DPU; underlies HBN/OVS offload |
| **DOCA RDMA** | Simplified RDMA verbs (QP management, send/write/read) for host and DPA | Custom RDMA data movers, storage protocols |
| **DOCA DMA** | DMA engine access to copy between host memory and DPU memory without CPU | Zero-copy data staging on the Arm |
| **DOCA Comm Channel** | Secure control channel between a host application and a DPU application over PCIe | Management/control plane for offloaded services |
| **DOCA DPA** | Programming the Data Path Accelerator cores: event-driven kernels in the packet path | Custom congestion control, packet-pacing, collective acceleration — the Spectrum-X CC algorithm runs here |
| **DOCA Telemetry** | API to publish counters/events into the DOCA Telemetry Service | Export application metrics alongside NIC metrics |
| **DOCA App Shield** | Host memory introspection from the DPU (read process lists, detect rootkits) without host agents | Zero-trust host monitoring |
| **DOCA Firefly** | PTP/IEEE-1588 time synchronisation service using the NIC's hardware clock | Precision time for distributed/HFT/5G; packaged as a service too |
| DOCA Crypto / IPsec / TLS | Inline encryption offload | Encrypted east-west without host CPU cost |
| DOCA Compress | Deflate/LZ4 engines | Storage/analytics |
| DOCA UROM / GPUNetIO | Unified Resource and Offload Manager; GPU-initiated networking (GPUDirect Async) | HPC/AI collective and GPU-driven packet processing |

You are not expected to program these for the exam; you are expected to match a library to its purpose and recognise that DOCA Flow + DPA are the pieces that make DPU/SuperNIC datapath features programmable.

## 4. DOCA services on BlueField

### 4.1 DOCA Telemetry Service (DTS)
**DTS** runs as a container on the Arm and collects:
- NIC/port counters, RoCE/PFC/ECN statistics, DPA metrics, thermal/power, and application metrics via the DOCA Telemetry API,
- exporting them via Prometheus endpoint, Fluent Bit, OpenTelemetry, or to NetQ.
Configuration lives in `/opt/mellanox/doca/services/telemetry/config/dts_config.ini` (path indicative; verify). In a Spectrum-X deployment DTS on the SuperNIC side plus NetQ/WJH on the switch side give end-to-end congestion visibility.

### 4.2 Host-Based Networking (HBN)
**HBN** packages the Cumulus Linux networking stack — FRR routing, NVUE, VXLAN/EVPN, ECMP — into a DOCA container on the BlueField Arm. The DPU becomes a *top-of-rack switch inside the server*:

- The host sees a normal NIC. The DPU runs BGP (often unnumbered) to the real leaf switches and terminates VXLAN/EVPN; tenant separation (VRF/VNI) happens on the DPU.
- Configuration uses **NVUE** — the same `nv set interface ...`, `nv set router bgp ...`, `nv set evpn ...` syntax as a Spectrum switch (Modules 4.1–4.2) — so one skill set and one automation toolchain (Module 6.1) covers switch and DPU.
- Hardware acceleration: routing/VXLAN decisions made by FRR are offloaded to the ConnectX eswitch via DOCA Flow/OVS, so forwarding runs at line rate.

Why it matters for AI clouds: HBN pushes the L3 boundary to the server, enabling per-tenant EVPN overlays that start at the DPU, host-level route advertisement (each GPU node advertises its /32 — simplifies multi-rail routing), and removes MLAG/L2 from the leaf layer. Deployment is a `docker`/containerd pod on the Arm pulled from NGC with a YAML spec under `/etc/kubelet.d/` (Kubelet-managed), plus an HBN-specific `bf.cfg` that creates the required SFs and bridges. Verify current steps in the DOCA HBN Service Guide.

### 4.3 Other services
Firefly (PTP), Flow Inspector (export sampled packets to telemetry), BlueMan (web dashboard for a DPU), UROM, and SNAP (NVMe/virtio-blk emulation) complete the catalogue. DPF (Module 5.1 §9) deploys these as Kubernetes workloads across many DPUs.

## 5. Versioning and compatibility

DOCA releases are numbered `major.minor.patch` (2.5, 2.7, 2.9, 3.0, …) roughly quarterly. Each release has a **compatibility matrix** listing:

| Item | Constraint |
|---|---|
| BlueField BSP | DOCA x.y ships *with* BSP a.b inside the BFB; the host DOCA version should match the BFB's DOCA version (same major.minor) |
| NIC firmware | Minimum firmware per device (BlueField-3 32.xx, ConnectX-7 28.xx); `mlxfwmanager` in the DOCA package carries the matching firmware |
| Host OS | Supported kernels/distros per release (Ubuntu 22.04/24.04, RHEL 8.x/9.x, etc.) |
| CUDA / GPU driver | For GPUDirect RDMA and GPUNetIO, the DOCA release notes name tested CUDA versions |
| Container services | HBN/DTS container tags are tied to the DOCA version on the Arm |

Rules: upgrade the BFB (which upgrades BSP + DOCA + firmware on the card) and the host DOCA profile together; keep all SuperNICs in a cluster on the same firmware; never run a host DOCA major version ahead of the card's BSP without checking the matrix. `ofed_info -s`, `dpkg -l | grep doca`, `mlxfwmanager --query` and `cat /etc/mlnx-release` (on the Arm) are the four commands to collect when checking alignment.

## 6. Relationship to the exam objective

The NCP-AIN blueprint lists "install DOCA and configure SuperNIC functions" under Spectrum Networking. In practice that decomposes into:
1. Install the right DOCA profile on each GPU host (`doca-ofed` or `doca-all`).
2. Ensure firmware/BSP match the DOCA version.
3. Set the SuperNIC's firmware parameters for Ethernet, RoCE and the Spectrum-X features.
4. Configure the host RoCE QoS so DSCP/PFC/ECN line up with the switch profile.
5. Configure the matching switch side (Module 4.1).
6. Verify end to end.

Section 7 walks through steps 3–6.

## 7. Configuring SuperNIC functions for Spectrum-X

### 7.1 Where each setting lives
| Function | Switch side (Cumulus/NVUE) | NIC side |
|---|---|---|
| Lossless/lossy RoCE QoS (DSCP 26 → prio 3, PFC, ECN) | `nv set qos roce mode lossless` | `mlnx_qos -i <if> --trust dscp --pfc 0,0,0,1,0,0,0,0`; ECN enabled per priority in sysfs; DSCP for RoCE traffic class via `cma_roce_tos`/`echo 106 > /sys/class/infiniband/<dev>/tc/1/traffic_class` (106 = DSCP 26 << 2) |
| CNP priority/DSCP | Part of the RoCE profile (prio 6, DSCP 48) | `mlxconfig -s CNP_DSCP_P1=48 CNP_802P_PRIO_P1=6` |
| **Adaptive routing** (packet spraying) | `nv set router adaptive-routing enable on` + per-interface | SuperNIC must tolerate out-of-order RoCE: DDP/out-of-order RDMA support is a SuperNIC firmware capability, enabled through firmware/DOCA settings in the Spectrum-X deployment guide — exact parameter names are release-specific; verify in the current Spectrum-X RoCE configuration guide |
| Congestion control | Switch telemetry marking (part of RoCE profile) | Spectrum-X congestion-control algorithm on the DPA, selected via firmware/DOCA (`ROCE_CC_*` mlxconfig parameters and the Spectrum-X CC package); default DCQCN otherwise |
| Performance isolation | Per-tenant VRF/VNI, TC separation | Per-QP/flow rate limiting by the CC engine |
| Telemetry | NetQ/WJH, histograms | DTS exporting RoCE counters |

### 7.2 Host-side RoCE QoS (common to any NIC)
```bash
# Identify devices
ibdev2netdev -v                           # mlx5_0 ↔ ens3f0np0 etc.

# Trust DSCP and enable PFC on priority 3 (lossless profile)
mlnx_qos -i ens3f0np0 --trust dscp
mlnx_qos -i ens3f0np0 --pfc 0,0,0,1,0,0,0,0
mlnx_qos -i ens3f0np0                     # show current mapping / PFC / ETS

# RoCE traffic marking: DSCP 26 (TOS 106) for RDMA CM connections
echo 106 > /sys/class/infiniband/mlx5_0/tc/1/traffic_class
cma_roce_tos -d mlx5_0 -t 106
cma_roce_mode -d mlx5_0 -p 1 -m 2         # RoCEv2

# ECN per priority (DCQCN) — enable on prio 3 for both RP and NP
echo 1 > /sys/class/net/ens3f0np0/ecn/roce_rp/enable/3
echo 1 > /sys/class/net/ens3f0np0/ecn/roce_np/enable/3
# CNP DSCP/priority (persistent via firmware)
mlxconfig -d /dev/mst/mt41692_pciconf0 s CNP_DSCP_P1=48 CNP_802P_PRIO_P1=6
```
For a *lossy* Spectrum-X deployment, skip `--pfc` on the host and rely on ECN + the Spectrum-X CC algorithm; the switch is set to `nv set qos roce mode lossy`. Host and switch profiles must agree — `netq show roce-config` (switch) plus `mlnx_qos -i` (host) is the comparison the exam expects.

### 7.3 SuperNIC-specific firmware settings
```bash
mlxconfig -d /dev/mst/mt41692_pciconf0 q | grep -E 'LINK_TYPE|INTERNAL_CPU|ROCE|CNP|ADVANCED_PCI|MAX_ACC_OUT_READ'
mlxconfig -d /dev/mst/mt41692_pciconf0 s LINK_TYPE_P1=2                        # Ethernet
mlxconfig -d /dev/mst/mt41692_pciconf0 s INTERNAL_CPU_OFFLOAD_ENGINE=1          # NIC mode (SuperNIC personality)
mlxconfig -d /dev/mst/mt41692_pciconf0 s ADVANCED_PCI_SETTINGS=1 MAX_ACC_OUT_READ=44   # PCIe/RDMA tuning per DGX guides (values vary)
# Spectrum-X AR/CC enablement parameters: see the Spectrum-X "RoCE adaptive routing and congestion control" host configuration guide
# for the exact mlxconfig keys (e.g. ROCE_ADAPTIVE_ROUTING_EN / ROCE_CC_... family) — names change between firmware trains; verify.
mlxfwreset -d /dev/mst/mt41692_pciconf0 reset        # or power cycle
```
NVIDIA's Spectrum-X deployment guides (and the DGX SuperPOD with Spectrum-X RA) supply a tested set of these parameters as a script; the exam wants you to know *that* AR/CC on the NIC is enabled through firmware (`mlxconfig`) and DOCA components, that it must match the switch's AR/RoCE profile, and that mismatched settings (AR on the switch, no out-of-order support on the NIC) hurt performance (Module 4.1 §3.1).

### 7.4 NCCL alignment
NCCL reads the NIC configuration and a few environment variables:
```bash
export NCCL_IB_HCA=mlx5_0,mlx5_1,mlx5_2,mlx5_3,mlx5_4,mlx5_5,mlx5_6,mlx5_7
export NCCL_IB_TC=106              # TOS → DSCP 26, matches switch RoCE class
export NCCL_IB_GID_INDEX=3         # RoCEv2 GID (verify with show_gids)
export NCCL_SOCKET_IFNAME=mgmt0
export NCCL_IB_QPS_PER_CONNECTION=4   # more QPs per connection to spread load; Spectrum-X guides give the value
```
`NCCL_IB_TC` must match the DSCP the switch trusts for RoCE; a wrong value puts GPU traffic in the lossy best-effort queue and is a classic "RoCE configured but slow" cause. `show_gids` (from DOCA tools) lists GID indices and their RoCE version/IP.

### 7.5 Verify end to end
```bash
# Host
ofed_info -s; dpkg -l | grep doca; mlxfwmanager --query
ibstat; ibv_devinfo -d mlx5_0 | grep -E 'state|link_layer|fw_ver'
mlnx_qos -i ens3f0np0
cat /sys/class/net/ens3f0np0/ecn/roce_np/enable/3
mlxlink -d /dev/mst/mt41692_pciconf0 -p 1              # link up at 400G, FEC OK
ethtool -S ens3f0np0 | grep -E 'pause|ecn|cnp|out_of_sequence|rx_prio3'
ib_write_bw -d mlx5_0 -F --report_gbits <peer>          # RDMA bandwidth test (Module 8.x)

# Switch
nv show qos roce
nv show interface swp1 qos roce counters
nv show router adaptive-routing
netq show roce-config; netq check roce
```
`rx_prio3_pause`/`tx_prio3_pause` rising on the host indicates PFC exchange; `np_cnp_sent`/`rp_cnp_handled` indicate ECN-driven congestion control; `out_of_sequence` growing with AR enabled and *no* throughput loss is expected on a SuperNIC with DDP, and a warning sign on a non-SuperNIC.

## Configuration / command reference

```bash
# Install DOCA on host
dpkg -i doca-host_<ver>_<distro>.deb && apt update
apt install -y doca-ofed        # or doca-all / doca-networking / doca-roce (verify names)
/etc/init.d/openibd restart

# Verify install
ofed_info -s; dpkg -l | grep doca; modinfo mlx5_core | grep version
mlxfwmanager --query; mst status -v; ibstat; ibv_devinfo

# Host RoCE QoS
mlnx_qos -i <if> --trust dscp --pfc 0,0,0,1,0,0,0,0
echo 106 > /sys/class/infiniband/<dev>/tc/1/traffic_class; cma_roce_tos -d <dev> -t 106
echo 1 > /sys/class/net/<if>/ecn/roce_rp/enable/3; echo 1 > /sys/class/net/<if>/ecn/roce_np/enable/3

# SuperNIC firmware
mlxconfig -d <dev> s LINK_TYPE_P1=2 INTERNAL_CPU_OFFLOAD_ENGINE=1 CNP_DSCP_P1=48 CNP_802P_PRIO_P1=6
mlxconfig -d <dev> q | grep -E 'ROCE|CNP'; mlxfwreset -d <dev> reset

# NCCL
NCCL_IB_HCA=... NCCL_IB_TC=106 NCCL_IB_GID_INDEX=3

# Arm services
docker ps | grep -E 'hbn|telemetry'; cat /etc/mlnx-release
```

## Common pitfalls & exam traps
- MLNX_OFED is retired; the driver stack is now the `doca-ofed` profile of DOCA. `ofed_info -s` still works.
- `doca-all` ≠ `doca-ofed`: the former includes the SDK and samples; production GPU hosts typically need only `doca-ofed` (or the RoCE-lean profile).
- The `doca-host` .deb/.rpm only installs the *repository*; you still need `apt install <profile>`.
- Host DOCA, BFB (BSP + DOCA), and firmware are versioned together — check the matrix before upgrading one.
- HBN runs NVUE/FRR on the DPU — the NVUE commands are the same as on a Spectrum switch.
- `NCCL_IB_TC=106` is TOS, which equals DSCP 26 (DSCP × 4). Confusing DSCP with TOS silently drops GPU traffic into the wrong queue.
- Enabling adaptive routing on the switch without SuperNIC out-of-order support degrades RoCE — the NIC side of AR is a firmware/DOCA capability, not a host sysctl.
- `mlxconfig` changes need `mlxfwreset` or a power cycle to take effect.
- Host PFC must match the switch profile: lossless profile = PFC on priority 3 both ends; lossy profile = no PFC either end, ECN on.
- DTS exports NIC telemetry from the Arm; NetQ/WJH export from the switch — both are needed for end-to-end RoCE visibility.

## Key takeaways
- DOCA = drivers/runtime + SDK libraries + Arm services, under one version; DOCA-OFED profile replaced MLNX_OFED.
- Profiles: `doca-all` (everything), `doca-networking`, `doca-ofed` (driver stack), lean RoCE profile — verify names per release.
- Install: add repo via `doca-host` package → `apt install doca-ofed` → restart `openibd`/reboot → verify with `ofed_info -s`, `dpkg -l | grep doca`, `mlxfwmanager --query`.
- Libraries: Flow (steering), RDMA, DMA, Comm Channel (host↔DPU), DPA (datapath cores — runs CC), Telemetry, App Shield (host introspection), Firefly (PTP).
- Services: DTS (telemetry export) and HBN (Cumulus/NVUE routing on the DPU).
- Compatibility: host DOCA ↔ BFB/BSP ↔ firmware must align; upgrade together.
- SuperNIC functions are configured on three planes: `mlxconfig` firmware params, DOCA/host QoS (`mlnx_qos`, ECN sysfs, CNP DSCP), and the switch (`nv set qos roce`, `nv set router adaptive-routing`).
- `NCCL_IB_TC=106` ↔ DSCP 26 ↔ switch-priority 3 — the end-to-end RoCE class chain.
- Verify with `mlnx_qos -i`, `ethtool -S` pause/CNP/out_of_sequence counters, `ib_write_bw`, `nv show qos roce`, `netq check roce`.

## Review questions

1. A new GPU host needs NVIDIA RoCE drivers, `mlxconfig`, and `mlnx_qos`, but no development libraries. Which DOCA profile is most appropriate?
   A. `doca-all`
   B. `doca-ofed`
   C. `doca-devel`
   D. `doca-telemetry`

2. After `dpkg -i doca-host_*.deb` and `apt update`, `ibstat` still reports no devices. What was missed?
   A. `mlxfwmanager --online -u`
   B. Installing a DOCA profile (e.g. `apt install doca-ofed`) and restarting the driver stack
   C. Enabling NIC mode
   D. Setting `NCCL_IB_TC`

3. Which DOCA component hosts NVIDIA's Spectrum-X congestion-control algorithm on the SuperNIC?
   A. DOCA Flow on the Arm cores
   B. DOCA App Shield
   C. The DPA (Data Path Accelerator) programmed via DOCA DPA
   D. DOCA Firefly

4. Which DOCA service runs the Cumulus Linux routing stack (FRR + NVUE, EVPN/VXLAN) on a BlueField DPU?
   A. DTS
   B. HBN
   C. SNAP
   D. BlueMan

5. The switch RoCE profile trusts DSCP 26 for lossless traffic. Which NCCL setting places GPU traffic in that class?
   A. `NCCL_IB_TC=26`
   B. `NCCL_IB_TC=106`
   C. `NCCL_IB_GID_INDEX=26`
   D. `NCCL_IB_SL=3`

6. Which three items must be version-aligned when upgrading a Spectrum-X host?
   A. Host DOCA, BlueField BFB/BSP, NIC firmware
   B. Cumulus Linux, NetQ, Ansible
   C. CUDA, Docker, Kubernetes
   D. BIOS, RAID firmware, iDRAC

7. Which command string correctly verifies the installed driver stack version on a DOCA host?
   A. `nv show system`
   B. `ofed_info -s`
   C. `show version`
   D. `cat /dev/rshim0/misc`

8. Adaptive routing is enabled on the SN5600 fabric. Hosts with SuperNICs perform well; hosts with ConnectX-6 NICs show `out_of_sequence` counters climbing and poor bandwidth. Why?
   A. ConnectX-6 cannot run RoCEv2
   B. The SuperNIC tolerates out-of-order delivery via Direct Data Placement; the ConnectX-6 treats reordering as loss
   C. The ConnectX-6 hosts lack `doca-all`
   D. The switch profile is lossy

### Answer key
1. B — `doca-ofed` is the MLNX_OFED-equivalent driver/tools profile.
2. B — `doca-host` only adds the repository; a profile must be installed.
3. C — Congestion-control code runs on the DPA cores.
4. B — HBN containerises Cumulus networking on the DPU.
5. B — TOS 106 = DSCP 26 (×4).
6. A — DOCA release notes tie host DOCA, BSP/BFB and firmware together.
7. B — `ofed_info -s` prints the driver stack version even under DOCA.
8. B — AR requires out-of-order tolerant RoCE, a SuperNIC capability.

## References
- NVIDIA DOCA SDK Documentation — https://docs.nvidia.com/doca/sdk/
- DOCA Installation Guide for Linux — https://docs.nvidia.com/doca/sdk/doca-installation-guide-for-linux/
- DOCA Profiles — https://docs.nvidia.com/doca/sdk/doca-profiles/
- DOCA Release Notes (compatibility matrix) — https://docs.nvidia.com/doca/sdk/doca-release-notes/
- DOCA HBN Service Guide; DOCA Telemetry Service Guide — https://docs.nvidia.com/doca/sdk/ (Services section)
- NVIDIA Spectrum-X RoCE / adaptive routing host configuration guides and DGX SuperPOD with Spectrum-X RA — https://docs.nvidia.com/dgx-superpod/
- NCCL Environment Variables — https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html
- NVIDIA Academy course: BlueField DPU Administration
- Related: [`01-bluefield-dpu-administration.md`](01-bluefield-dpu-administration.md), [`../04-spectrum-ethernet/01-spectrum-x.md`](../04-spectrum-ethernet/01-spectrum-x.md), [`../03-rdma/03-roce-a-to-z.md`](../03-rdma/03-roce-a-to-z.md), [`../01-ai-datacenter-design/`](../01-ai-datacenter-design/)
