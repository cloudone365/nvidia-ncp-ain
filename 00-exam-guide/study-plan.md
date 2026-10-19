# Study Plan

Two tracks. Both assume ~8–10 hours/week. Each week ends with the module review questions and a lab where one exists.

## Track A — 8 weeks (recommended)

| Week | Focus | Modules | Lab | Academy course(s) to complete |
|---|---|---|---|---|
| 1 | AI DC foundations | 1.1, 1.2, 1.3 | — | AI Infrastructure and Operations Fundamentals |
| 2 | RDMA foundations | 3.1, 3.2 | Lab 03 (perftest) | RDMA; Fundamentals of RDMA Programming |
| 3 | InfiniBand admin | 2.1 | Lab 02 (if hardware) | InfiniBand Essentials; InfiniBand Network Administration |
| 4 | IB QoS/AR/SHARP + UFM | 2.3, 2.2 | — | DC Management Made Easy with NVIDIA UFM |
| 5 | Cumulus + RoCE | 4.2, 4.3, 3.3 | Lab 01 (Air RoCE fabric) | Cumulus Linux Essentials; Cumulus Linux Administration; RoCE A to Z |
| 6 | Spectrum-X, SONiC, NetQ, BlueField/DOCA | 4.1, 4.4, 4.5, 5.1, 5.2 | Lab 05 (NetQ/WJH) | Spectrum-X; SONiC Essentials; NetQ Deployment; BlueField DPU Administration |
| 7 | Automation + Kubernetes | 6.1, 7.1 | Lab 04 (Ansible), Lab 06 (Network Operator) | — |
| 8 | Troubleshooting + exam prep | 8.1, 8.2, 8.3, 8.4; practice exam; flashcards | Break/fix challenges in all labs | CVT Fundamentals; MLXlink and MLX Cable Debug Tools |

Weekly rhythm that works:
- Day 1–2: read modules, take notes in your own words
- Day 3–4: Academy course videos + quizzes (they are free; the exam page links them)
- Day 5: lab
- Day 6: module review questions closed-book; re-read anything you missed
- Day 7: flashcards from earlier weeks (spaced repetition)

## Track B — 4 weeks (accelerated, for people already running IB or Cumulus in production)

| Week | Focus | Modules |
|---|---|---|
| 1 | Your weak fabric (IB *or* Spectrum) — all modules of that section | 2.x + 3.x **or** 4.x + 5.x + 3.3 |
| 2 | The other fabric at skim depth + RDMA/RoCE | remaining 2.x/4.x, 3.1, 3.3 |
| 3 | Troubleshooting + automation + K8s | 8.1–8.4, 6.1, 7.1 |
| 4 | Design/NCCL skim, practice exam twice, flashcards | 1.1–1.3, 10 |

## Final-week checklist
- [ ] Practice exam score ≥ 85% on second attempt, closed book
- [ ] Can write from memory: NVUE lossless RoCE + adaptive routing config for a leaf; opensm partitions.conf entry; NicClusterPolicy skeleton; Ansible play that applies NVUE YAML
- [ ] Can explain ibstat output states; what each ib* utility is for; what mlxlink BER fields mean
- [ ] Can draw a rail-optimized SU and explain why GPU-N-to-GPU-N is one hop
- [ ] Re-read every module's "Appendix B — Exam traps & key takeaways" and complete every hands-on assignment before opening its solution guide
- [ ] Register on Certiverse, test webcam/room, read the proctoring rules

## Exam-day tactics
- 120 min / 75 q ≈ 95 s per question. Flag and move on after 2 minutes.
- Eliminate: if two options are tools from the *wrong* fabric (e.g. `netq check bgp` for an IB question), cut them first.
- "Select two" questions: the two answers are usually complementary (e.g. a host-side and a switch-side step).
- Watch for legacy traps: NCLU `net add` is not NVUE; MLNX_OFED is now DOCA-OFED; `opensm` vs UFM-embedded SM.
