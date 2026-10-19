# NVIDIA-Certified Professional: AI Networking (NCP-AIN) — Training Course

A self-paced, open training course for the **NCP-AIN** certification exam. It is organized around the official exam domains and maps every module back to the NVIDIA Academy courses and NVIDIA documentation the exam recommends.

> Not affiliated with or endorsed by NVIDIA. All explanations are original; always confirm version-specific details against the current NVIDIA documentation linked in each module.

## Exam at a glance (official, Oct 2026)

| Item | Detail |
|---|---|
| Questions | 70–75 |
| Duration | 2 hours |
| Format | Multiple choice, pass/fail (hands-on lab exercises being added Nov 1, 2026) |
| Price | $400 (rising to $500 with labs) |
| Validity | 2 years |
| Delivery | Online proctored via Certiverse |

| Exam domain | Weight | Course section |
|---|---|---|
| NVIDIA Spectrum Networking | **30%** | [04 — Spectrum Ethernet](04-spectrum-ethernet/) + [05 — BlueField/DOCA](05-bluefield-doca/) |
| NVIDIA InfiniBand Networking | **30%** | [02 — InfiniBand](02-infiniband/) + [03 — RDMA](03-rdma/) |
| Troubleshooting Tools | **20%** | [08 — Troubleshooting](08-troubleshooting/) |
| Automation and Configuration | 10% | [06 — Automation](06-automation/) |
| AI Data Center Design and Optimization | 5% | [01 — AI Data Center Design](01-ai-datacenter-design/) |
| Kubernetes Integration | 5% | [07 — Kubernetes](07-kubernetes/) |

Full breakdown: [00-exam-guide/exam-blueprint.md](00-exam-guide/exam-blueprint.md)

## How every module is built

Each module is written as an instructor-led lesson and follows the same five-part structure:

| # | Section | What you get |
|---|---|---|
| 1 | **Learning objectives** | "By the end of this module you will be able to…" — testable, mapped to exam objectives |
| 2 | **Key technical concepts** | Beginner-friendly explanation of the mechanics, analogy first, precise mechanism second, "why the exam cares" third |
| 3 | **Live-style walkthrough** | A step-by-step, heavily commented command/config session or architectural trace showing the concept in action |
| 4 | **Troubleshooting / common gotchas** | 2–3 errors learners actually hit here: Symptom → Why → How to debug → Fix |
| 5 | **Hands-on assignment** | A mini-project you build independently, with a **Hint** section — and the **Solution guide held until the very bottom of the file** |

After those, each module carries appendices you'll want on exam day: a quick command/config reference, exam traps & key takeaways, a knowledge check (scenario MCQs + answer key), and references.

## How to use this course

1. Read the [exam blueprint](00-exam-guide/exam-blueprint.md) and pick a [study plan](00-exam-guide/study-plan.md) (8-week default, 4-week accelerated).
2. Work the modules in order. Do the hands-on assignment **before** reading the solution guide — the assignment is where competency is actually built.
3. Do the [labs](09-labs/) as you reach them. Most Ethernet labs run free in NVIDIA Air; InfiniBand labs need real Quantum gear or a UFM demo.
4. Finish with the [practice exam](10-practice-exam/practice-exam.md) and [flashcards](10-practice-exam/flashcards.md).
5. Before exam day, re-read every module's **Appendix B — Exam traps & key takeaways**.

## Course map

### 00 — Exam guide
- [Exam blueprint](00-exam-guide/exam-blueprint.md)
- [Study plan](00-exam-guide/study-plan.md)
- [Resources: NVIDIA Academy courses & docs](00-exam-guide/resources.md)

### 01 — AI Data Center Design and Optimization (5%)
| Module | Source |
|---|---|
| [1.1 AI Infrastructure and Operations Fundamentals](01-ai-datacenter-design/01-ai-infrastructure-and-operations-fundamentals.md) | Academy: AI Infrastructure and Operations Fundamentals |
| [1.2 DGX SuperPOD & BasePOD Reference Architectures](01-ai-datacenter-design/02-dgx-superpod-basepod-reference-architectures.md) | Docs: DGX SuperPOD / BasePOD RA |
| [1.3 GPU-to-GPU Communication: NCCL & GPUDirect RDMA](01-ai-datacenter-design/03-gpu-to-gpu-communication-nccl-gpudirect.md) | Docs: NCCL, GPUDirect RDMA |

### 02 — InfiniBand Networking (30%)
| Module | Source |
|---|---|
| [2.1 InfiniBand Network Administration](02-infiniband/01-infiniband-network-administration.md) | Academy: InfiniBand Essentials, InfiniBand Network Administration |
| [2.2 Data Center Management with UFM](02-infiniband/02-ufm-dc-management.md) | Academy: DC Management Made Easy with NVIDIA UFM |
| [2.3 InfiniBand QoS, Adaptive Routing & SHARP](02-infiniband/03-infiniband-qos-adaptive-routing-sharp.md) | Docs: AR config guide, IB QoS, SHARP with NCCL |

### 03 — RDMA (supports both 30% domains)
| Module | Source |
|---|---|
| [3.1 RDMA Fundamentals](03-rdma/01-rdma-fundamentals.md) | Academy: RDMA |
| [3.2 Fundamentals of RDMA Programming](03-rdma/02-rdma-programming-fundamentals.md) | Academy: Fundamentals of RDMA Programming |
| [3.3 RoCE from A to Z](03-rdma/03-roce-a-to-z.md) | Academy: RDMA over Converged Ethernet (RoCE) from A to Z |

### 04 — Spectrum Networking (30%)
| Module | Source |
|---|---|
| [4.1 Spectrum-X](04-spectrum-ethernet/01-spectrum-x.md) | Academy: Spectrum-X |
| [4.2 Cumulus Linux Essentials](04-spectrum-ethernet/02-cumulus-linux-essentials.md) | Academy: Cumulus Linux Essentials |
| [4.3 Cumulus Linux Administration](04-spectrum-ethernet/03-cumulus-linux-administration.md) | Academy: Cumulus Linux Administration |
| [4.4 SONiC Essentials](04-spectrum-ethernet/04-sonic-essentials.md) | Academy: SONiC Essentials by NVIDIA |
| [4.5 NetQ Deployment and Installation](04-spectrum-ethernet/05-netq-deployment-and-installation.md) | Academy: NetQ Deployment and Installation |

### 05 — BlueField DPU & DOCA (part of Spectrum domain)
| Module | Source |
|---|---|
| [5.1 BlueField DPU Administration](05-bluefield-doca/01-bluefield-dpu-administration.md) | Academy: BlueField DPU Administration |
| [5.2 DOCA SDK Overview](05-bluefield-doca/02-doca-sdk-overview.md) | Docs: DOCA SDK & BlueField |

### 06 — Automation and Configuration (10%)
| Module | Source |
|---|---|
| [6.1 NVUE & Ansible Automation](06-automation/01-nvue-and-ansible-automation.md) | Docs: NVUE, Ansible, Data Center Network Automation guides |

### 07 — Kubernetes Integration (5%)
| Module | Source |
|---|---|
| [7.1 NVIDIA Network Operator for RDMA & InfiniBand](07-kubernetes/01-network-operator-rdma-infiniband.md) | Docs: Network Operator deployment guides |

### 08 — Troubleshooting Tools (20%)
| Module | Source |
|---|---|
| [8.1 InfiniBand Diagnostics & perftest](08-troubleshooting/01-infiniband-diagnostics-and-perftest.md) | Docs: IB fabric utilities, perftest |
| [8.2 Cable Validation Tool (CVT)](08-troubleshooting/02-cable-validation-tool-cvt.md) | Academy: CVT Fundamentals |
| [8.3 mlxlink & mlxcables Debug Tools](08-troubleshooting/03-mlxlink-and-mlxcables-debug-tools.md) | Academy: MLXlink and MLX Cable Debug Tools |
| [8.4 WJH, NetQ & Cumulus Troubleshooting](08-troubleshooting/04-wjh-netq-and-cumulus-troubleshooting.md) | Docs: WJH, NetQ, UFM health |

### 09 — Labs
See [09-labs/README.md](09-labs/README.md) — six hands-on labs (NVIDIA Air, Soft-RoCE, Kubernetes).

### 10 — Practice exam
- [Practice exam (75 questions + answer key)](10-practice-exam/practice-exam.md)
- [Flashcards](10-practice-exam/flashcards.md)

## Contributing
Corrections welcome — especially where NVIDIA has changed a command, default, or product name. Open an issue or PR with a link to the current doc.

## License
Content is released under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). NVIDIA, Spectrum, Cumulus, BlueField, DOCA, NetQ, UFM, and related names are trademarks of NVIDIA Corporation.
