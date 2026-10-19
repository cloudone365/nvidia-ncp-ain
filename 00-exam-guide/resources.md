# Resources — NVIDIA Academy Courses and Documentation

Every module links back to these. Course and doc URLs move; when a link is dead, search the title on https://academy.nvidia.com or https://docs.nvidia.com/networking/.

## NVIDIA Academy (self-paced, free registration)

Catalog: https://academy.nvidia.com/en/catalog/

| Course | Used in module | Domain |
|---|---|---|
| AI Infrastructure and Operations Fundamentals | 1.1 | AI DC Design |
| InfiniBand Essentials | 2.1 | InfiniBand |
| InfiniBand Network Administration | 2.1 | InfiniBand |
| DC Management Made Easy with NVIDIA UFM | 2.2 | InfiniBand |
| Spectrum-X | 4.1 | Spectrum |
| RDMA | 3.1 | Both |
| Fundamentals of RDMA Programming | 3.2 | Both |
| RDMA over Converged Ethernet (RoCE) from A to Z | 3.3 | Spectrum |
| Cumulus Linux Essentials | 4.2 | Spectrum / Automation |
| Cumulus Linux Administration | 4.3 | Spectrum / Troubleshooting |
| SONiC Essentials by NVIDIA | 4.4 | Spectrum |
| NetQ Deployment and Installation | 4.5 | Spectrum / Troubleshooting |
| BlueField DPU Administration | 5.1 | Spectrum |
| Cable Validation Tool (CVT) Fundamentals | 8.2 | Troubleshooting |
| MLXlink and MLX Cable Debug Tools | 8.3 | Troubleshooting |

The exam page explicitly names **InfiniBand Essentials**, **InfiniBand Network Administration**, and **Cumulus Linux Essentials** as recommended training.

## NVIDIA documentation (by domain)

### AI Data Center Design
- DGX SuperPOD reference architectures — https://docs.nvidia.com/dgx-superpod/
- DGX BasePOD reference architectures — https://docs.nvidia.com/dgx-basepod/
- NCCL documentation — https://docs.nvidia.com/deeplearning/nccl/
- GPUDirect RDMA — https://docs.nvidia.com/cuda/gpudirect-rdma/
- NVIDIA Spectrum-X networking platform whitepaper — search "Spectrum-X whitepaper" on nvidia.com
- BlueField-3 Administrator Quick Start Guide — https://docs.nvidia.com/networking/ (BlueField section)
- SONiC wiki — https://github.com/sonic-net/SONiC/wiki

### Spectrum Networking
- Cumulus Linux user guide — https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/
  - RoCE configuration, QoS, EVPN basic & multihoming, VRF, What Just Happened, NVUE CLI reference
- Cumulus NetQ user guide — https://docs.nvidia.com/networking-ethernet-software/cumulus-netq/
- NVIDIA Air (DSX Air) — https://air.nvidia.com and https://docs.nvidia.com/networking-ethernet-software/nvidia-air/
- DOCA installation guide & profiles — https://docs.nvidia.com/doca/sdk/
- ConnectX-8 SuperNIC user manual; BlueField-3 SuperNIC — https://docs.nvidia.com/networking/
- Spectrum-X / SN5600 datasheets — https://www.nvidia.com/en-us/networking/spectrumx/

### InfiniBand Networking
- Introduction to InfiniBand (whitepaper) — https://network.nvidia.com/pdf/whitepapers/IB_Intro_WP_190.pdf
- MLNX-OS user manual (Subnet Manager, QoS, AR) — https://docs.nvidia.com/networking/ (MLNX-OS section)
- Adaptive Routing configuration guide — docs.nvidia.com/networking, search "Adaptive Routing"
- UFM Enterprise user manual — https://docs.nvidia.com/networking/display/ufmenterpriseumv6170 (v6.17; check for newer)
- InfiniBand fabric utilities (MLNX_OFED / DOCA-OFED user manual) — https://docs.nvidia.com/networking/display/mlnxofedv23
- SHARP with NCCL — https://docs.nvidia.com/networking/display/sharpv300
- mlxconfig (MFT user manual) — https://docs.nvidia.com/networking/display/mftv

### Kubernetes Integration
- NVIDIA Network Operator — https://docs.nvidia.com/networking/display/cokan10
- Network Operator on OpenShift; Network Operator on DGX BasePOD (RHEL) — same portal
- Red Hat OpenShift RDMA documentation — https://docs.openshift.com

### Troubleshooting Tools
- perftest — https://github.com/linux-rdma/perftest
- ib_write_lat / ib_write_bw knowledge-base articles — https://enterprise-support.nvidia.com/s/
- UFM health & system dump — UFM Enterprise user manual, "Fabric Health" chapter
- NetQ switch monitoring — NetQ user guide
- NCCL troubleshooting — https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/troubleshooting.html
- IPoIB — DOCA-OFED user manual, "IP over InfiniBand" chapter
- MFT (mlxlink, mlxcables, mlxconfig, mlxfwmanager) — https://docs.nvidia.com/networking/display/mftv

### Automation and Configuration
- NVUE CLI and command reference — Cumulus Linux user guide → "NVUE CLI"
- NVUE cheat sheet — Cumulus Linux user guide
- NVUE with Ansible — https://galaxy.ansible.com/ui/repo/published/nvidia/nvue/
- Data Center Network Automation guides (Ansible, NVUE) — https://docs.nvidia.com/networking-ethernet-software/
- Cumulus Linux upgrade guide — Cumulus Linux user guide → "Upgrading Cumulus Linux"
- NVIDIA developer blog on network automation — https://developer.nvidia.com/blog

## Other useful material
- NVIDIA Air sample topologies (free, instant Cumulus leaf-spine) — https://air.nvidia.com
- Linux RDMA mailing list and rdma-core — https://github.com/linux-rdma/rdma-core
- OpenFabrics Alliance tutorials — https://www.openfabrics.org
- NVIDIA enterprise support knowledge base (RoCE tuning, PFC/ECN recipes) — https://enterprise-support.nvidia.com/s/
