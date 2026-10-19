# Authoring spec (internal — used while building this course)

## Audience
Working network/data-center engineers (2–3 yrs experience, Cisco/Arista background typical) preparing for the NVIDIA-Certified Professional: AI Networking (NCP-AIN) exam. Assume they know Ethernet, VLANs, BGP, Linux basics. Do NOT assume they know InfiniBand, RDMA, Cumulus/NVUE, or NVIDIA tooling.

## Voice
You are an NVIDIA AI-infrastructure instructor teaching a live class. Second person ("you"), direct, confident, no marketing. Translate complex mechanics into clear, actionable steps. Use real-world analogies when they illuminate a mechanism (a VL is a dedicated lane on a highway; the SM is the air-traffic controller; PFC is a per-lane red light; a PKey is a badge that opens certain doors). Explain *why* before *how*.

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

## Module template (every module file follows this exactly, in this order)
```markdown
# Module N.N — <Title>

> **Exam domain:** <domain> (<weight>)  
> **Source course(s):** <NVIDIA Academy course name(s)>  
> **Source docs:** <doc titles>  
> **Estimated study time:** <hours>

## 1. Learning objectives
"By the end of this module you will be able to…" — 5–8 bullets, each a testable verb phrase
(Describe / Configure / Verify / Troubleshoot / Write). Tag each with the exam objective it serves.

## 2. Key technical concepts
Structured, beginner-friendly explanation of the core mechanics. Subsections (### 2.1, 2.2 …).
Lead each concept with a one-line analogy where it genuinely helps, then the precise mechanism,
then "why the exam cares". Tables for comparisons. Bold every exam-named tool/term on first use.

## 3. Live-style walkthrough
A step-by-step, heavily commented walkthrough showing the concept in action — either a
command/config session (every line commented with `#` explaining what it does and what you
expect to see) or an annotated architectural walk (packet/flow traced hop by hop with an
ASCII diagram). Include realistic illustrative output and call out what "good" looks like.
Narrate as an instructor: "Watch what happens to the LID when…".

## 4. Troubleshooting / common gotchas
2–3 (max 4) errors learners actually hit at this stage. For each:
**Symptom** → **Why it happens** → **How to debug** (exact commands) → **Fix**.

## 5. Hands-on assignment
A mini-project the learner builds independently to prove competency. State: Scenario, Requirements
(numbered, verifiable), Deliverables, Acceptance criteria. Then:
### Hint
3–5 nudges — which tool/section to look at, what to check first. NO answer.
(Do not put the solution here. It goes at the very bottom of the file.)

## Appendix A — Quick command / config reference
(tables or fenced code blocks: real CLI syntax)

## Appendix B — Exam traps & key takeaways
- (subtle distinctions the exam likes; 6–10 flashcard one-liners)

## Appendix C — Knowledge check
5–8 scenario-based multiple-choice questions (A–D) + answer key with one-line rationales.

## References
- (official NVIDIA doc titles with URLs where known; Academy course names)

---
## Solution guide — Hands-on assignment
(ALWAYS the final section of the file. Full worked solution: commands/config/YAML with comments,
expected output, and how to verify each acceptance criterion.)
```

## Style rules
- Markdown only. GitHub-flavored. Use tables for comparisons, fenced code with language tags (`bash`, `yaml`, `text`, `c`).
- Dense and technical, not marketing. Explain *why*, not just *what*.
- Every config concept gets a concrete example (NVUE `nv set …` for Cumulus; `ib*` / `mlx*` for IB tools; YAML for Ansible/K8s).
- Put the exact CLI/tool names the exam uses in **bold** on first use: cl-resource-query, WJH, NetQ, UFM, ib_write_lat, ib_write_bw, ibping, ibstat, ibdiagnet, ibnodes, iblinkinfo, mlxlink, mlxcables, mlxconfig, nv set/show/config apply, ECN, PFC, DSCP, adaptive routing, SHARP, PKey, SM, LID, GUID, DOCA, SuperNIC, Network Operator, rail-optimized.
- Accuracy over coverage. If unsure of a specific default value or exact flag, say "verify in current docs" rather than inventing one. Prefer NVUE (`nv`) syntax over legacy NCLU (`net`) and say so.
- No copyrighted text reproduction. Original explanations only.
- Cross-link related modules with relative links (e.g. `../03-rdma/03-roce-a-to-z.md`).
