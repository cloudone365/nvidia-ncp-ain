# NCP-AIN Exam Blueprint

Source: NVIDIA certification page for AI Networking Professional (checked October 2026). Verify against the live page before you register — NVIDIA revises blueprints without notice.

## Logistics

| Item | Detail |
|---|---|
| Name | NVIDIA-Certified Professional: AI Networking (NCP-AIN) |
| Level | Professional (intermediate) |
| Questions | 70–75 |
| Time | 120 minutes (~1.6 min/question) |
| Scoring | Pass/fail; no published passing score |
| Price | $400; $500 once lab exercises are added (announced for Nov 1, 2026) |
| Language | English |
| Validity | 2 years; recertify by retaking |
| Platform | Certiverse (online proctored) |
| Prerequisites | None formal. Recommended: 2–3 years hands-on DC experience, ideally with NVIDIA networking |

## Recommended background
- Ethernet, TCP/IP, routing/switching, VLANs, BGP, data-center (leaf/spine) architectures
- InfiniBand, RDMA, RoCE, Spectrum-X Ethernet
- GPU / storage / data-center networking operations
- Monitoring and troubleshooting performance, congestion, latency, connectivity
- Ansible, NVUE, Linux, Kubernetes, scripting

## Domains, weights, and objectives

### 1. NVIDIA Spectrum Networking — 30% (~21–23 questions)
- Configure Spectrum-X switches for RoCE
- Set up QoS, ECN, PFC, adaptive routing, and telemetry
- Configure multi-tenant BGP-EVPN
- Use DSX Air (NVIDIA Air), NetQ, and What Just Happened (WJH)
- Install DOCA and configure SuperNIC functions

Covered by: Modules 4.1–4.5, 5.1–5.2, 3.3, 8.4

### 2. NVIDIA InfiniBand Networking — 30% (~21–23 questions)
- Provision switches, including high availability
- Configure partition keys (PKeys) for multi-tenancy
- Set up QoS and adaptive routing
- Monitor with UFM

Covered by: Modules 2.1–2.3, 3.1–3.2, 8.1

### 3. Troubleshooting Tools — 20% (~14–15 questions)
- Use cl-resource-query, WJH, NetQ, and UFM
- Run ib_write_lat, ib_write_bw, ibping, ibstat, ibdiagnet, ibnodes, iblinkinfo to diagnose faults

Covered by: Modules 8.1–8.4, 4.3 (cl-resource-query), 2.2 (UFM)

### 4. Automation and Configuration — 10% (~7–8 questions)
- Manage Spectrum-X configurations with NVUE templates
- Write Ansible playbooks for VLANs and RoCE

Covered by: Module 6.1, 4.2

### 5. AI Data Center Design and Optimization — 5% (~3–4 questions)
- Describe AI factory architecture
- Rail-optimized topologies
- GPU-to-GPU communication

Covered by: Modules 1.1–1.3

### 6. Kubernetes Integration — 5% (~3–4 questions)
- Deploy the NVIDIA Network Operator for RDMA and InfiniBand
- Verify its functionality

Covered by: Module 7.1

## Where to spend your time
60% of the exam is Spectrum + InfiniBand configuration, and another 20% is troubleshooting those same fabrics. If you come from an Ethernet background, InfiniBand (SM, LIDs, PKeys, AR, UFM) is where you will lose the most points — front-load it. If you come from an HPC/IB background, Cumulus NVUE, EVPN, and RoCE QoS on Spectrum-X are your gap.

The three "small" domains (20% combined) are mostly recall: rail-optimized topology, NCCL basics, Network Operator components, NVUE/Ansible syntax. A few hours each is enough.

## Question style (from the public learning-mode practice set)
- Scenario-based multiple choice; usually one correct answer, occasionally "select two".
- Heavy on *which tool / which command / which parameter*: e.g. which utility shows per-link width and speed across the fabric (iblinkinfo), which NVUE command enables lossless RoCE, what ibstat "Initializing" with LID 0 means.
- Expect numbers: NDR = 400 Gb/s, Quantum-2 = 64 × 400G, SN5600 = 64 × 800G, default PKey 0x7fff, RoCE v2 UDP port 4791, default RoCE DSCP 26 / CNP DSCP 48, PFC priority 3.
- Expect "what does X do" for NCCL env vars, opensm params, mlxconfig params, WJH drop categories.
