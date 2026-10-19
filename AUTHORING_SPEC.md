# Authoring spec (internal — used while building this course)

## Audience
Working network/data-center engineers (2–3 yrs experience, Cisco/Arista background typical) preparing for the NVIDIA-Certified Professional: AI Networking (NCP-AIN) exam. Assume they know Ethernet, VLANs, BGP, Linux basics. Do NOT assume they know InfiniBand, RDMA, Cumulus/NVUE, or NVIDIA tooling.

## Exam blueprint (official, nvidia.com, Oct 2026)
| Domain | Weight |
|---|---|
| NVIDIA Spectrum Networking | 30% |
| NVIDIA InfiniBand Networking | 30% |
| Troubleshooting Tools | 20% |
| Automation and Configuration | 10% |
| AI Data Center Design and Optimization | 5% |
| Kubernetes Integration | 5% |

70–75 questions, 2 hours, pass/fail, $400 (lab exercises added Nov 1, 2026; price → $500), valid 2 years.

## Repo layout
```
README.md
00-exam-guide/           exam-blueprint.md, study-plan.md, resources.md
01-ai-datacenter-design/ AI infra fundamentals, SuperPOD/BasePOD RA, NCCL + GPUDirect
02-infiniband/           IB administration, UFM, IB QoS/AR/SHARP
03-rdma/                 RDMA fundamentals, RDMA programming, RoCE A-to-Z
04-spectrum-ethernet/    Spectrum-X, Cumulus Essentials, Cumulus Admin, SONiC, NetQ
05-bluefield-doca/       BlueField DPU admin, DOCA SDK
06-automation/           NVUE, Ansible
07-kubernetes/           Network Operator
08-troubleshooting/      IB diag utilities + perftest, CVT, mlxlink/mlxcables, WJH + NetQ
09-labs/                 hands-on labs (NVIDIA Air / real gear)
10-practice-exam/        practice questions + answer key, flashcards
```

## Module template (every module file follows this exactly)
```markdown
# Module N.N — <Title>

> **Exam domain:** <domain> (<weight>)  
> **Source course(s):** <NVIDIA Academy course name(s)>  
> **Source docs:** <doc titles>  
> **Estimated study time:** <hours>

## Learning objectives
- (5–8 bullets, each testable, starting with a verb: Describe / Configure / Verify / Troubleshoot)

## 1. <Concept section>
...
## N. <Concept section>
...

## Configuration / command reference
(tables or fenced code blocks: real CLI syntax — nv set…, ibdiagnet …, mlxlink …, etc.)

## Common pitfalls & exam traps
- (what candidates get wrong; subtle distinctions the exam likes)

## Key takeaways
- (6–10 one-liners you could put on a flashcard)

## Review questions
1. (5–8 scenario-based multiple-choice questions, 4 options A–D)
...
### Answer key
1. B — one-sentence rationale
...

## References
- (official NVIDIA doc titles with URLs where known; Academy course names)
```

## Style rules
- Markdown only. GitHub-flavored. Use tables for comparisons, fenced code with language tags (`bash`, `yaml`, `text`).
- Depth: each module 1,500–3,500 words. Dense and technical, not marketing. Explain *why*, not just *what*.
- Every config concept gets a concrete example (NVUE `nv set …` for Cumulus; `ib*` / `mlx*` for IB tools; YAML for Ansible/K8s).
- Put the exact CLI/tool names the exam uses in **bold** on first use: cl-resource-query, WJH, NetQ, UFM, ib_write_lat, ib_write_bw, ibping, ibstat, ibdiagnet, ibnodes, iblinkinfo, mlxlink, mlxcables, mlxconfig, nv set/show/config apply, ECN, PFC, DSCP, adaptive routing, SHARP, PKey, SM, LID, GUID, GUID, DOCA, SuperNIC, Network Operator, rail-optimized.
- Accuracy over coverage. If unsure of a specific default value or exact flag, say "check the current doc" rather than inventing one. Prefer NVUE (`nv`) syntax over legacy NCLU (`net`) and say so.
- No copyrighted text reproduction. Original explanations only.
- Cross-link related modules with relative links (e.g. `../03-rdma/03-roce-a-to-z.md`).
