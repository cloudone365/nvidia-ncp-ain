# Module 8.2 — Cable Validation Tool (CVT)

> **Exam domain:** Troubleshooting Tools (20%)  
> **Source course(s):** Cable Validation Tool (CVT) Fundamentals (NVIDIA Academy)  
> **Source docs:** NVIDIA UFM Cable Validation Tool User Manual (v1.4–v1.8); ibdiagnet User Manual ("Rail Optimized Topology Validation"); NVIDIA DGX SuperPOD Reference Architecture (cabling / rail design)  
> **Estimated study time:** 2.5 hours

> **How to study this module:** read §1–§2, work through the §3 walkthrough on real gear or NVIDIA Air, then attempt the §5 hands-on assignment using only the Hint section. Open the Solution guide (last section) only after you have a working attempt. Finish with the Appendix C knowledge check closed-book.

> **Scope note.** CVT documentation is thinner and less public than the rest of the NVIDIA networking stack, and the tool has changed packaging between releases (standalone appliance → "UFM Cable Validation Tool" Docker-based collector with agents). This module is precise about *what* CVT validates and the *workflow* — which is what the exam tests — and explicitly hedges on exact binary names, flags, and file-format details. Where you see "verify in current CVT docs", do exactly that before relying on a command in production.

## 1. Learning objectives

By the end of this module you will be able to:

- **Describe** the purpose of **CVT** and the class of problems it catches that link-state tools (ibstat, iblinkinfo) do not. *(Troubleshooting Tools — cabling validation)*
- **Describe** the inputs CVT needs (planned topology file; discovered fabric) and the formats it accepts (P2P, `.topo`, DOT). *(Troubleshooting Tools — CVT inputs)*
- **Explain** the plan → discover → compare → report → remediate workflow and where it sits in a SuperPOD bring-up schedule. *(AI Data Center Design — bring-up sequence)*
- **Describe** the CVT architecture (collector/bring-up server, agents, web UI, REST API) and the switch requirements for agents. *(Troubleshooting Tools — CVT architecture)*
- **Interpret** CVT report categories: miswiring, link integrity, link state, underperforming links, provisioning issues. *(Troubleshooting Tools — reading CVT output)*
- **Verify** a **rail-optimized** cabling design by hand with **ibdiagnet** and **iblinkinfo** when CVT is not deployed. *(InfiniBand Networking / AI Data Center Design — rail-optimized topology)*
- **Troubleshoot** a swapped-rail or wrong-port finding end to end and relate CVT to **UFM** and **NetQ**. *(Troubleshooting Tools — tool selection)*

## 2. Key technical concepts

### 2.1 Why "all links up" is not "cabled correctly"

**Analogy.** An electrician wires a new building. Every light switch works — but the switch in room 3 controls the lights in room 4. Flipping switches (link-state checks) will never find that. Only comparing the *wiring diagram* to what the electrician *actually did* will. CVT is that comparison.

**Mechanism.** A DGX SuperPOD scalable unit has 32 nodes × 8 compute rails, plus storage and in-band management fabrics — thousands of cables, each with an exact intended source and destination. `ibstat` and `iblinkinfo` can report every link `Active / LinkUp / 4X NDR` and the fabric can *still be wrong*: GPU3's NIC on node 17 plugged into the leaf that serves rail 2 instead of rail 3 comes up perfectly and silently costs performance on every collective, because **rail-optimized** traffic that should stay on one leaf now crosses a spine. Two uplinks swapped between spines produce an asymmetric topology the routing engine routes around — until load reveals the hot spot.

Link-state tools answer "is this link up?". CVT answers "is this link **the one the design said should be here**, and is it healthy enough to trust?"

What CVT checks:

1. **Connectivity correctness** — each discovered link (local node/port ↔ remote node/port) is compared with the planned cabling, cable by cable.
2. **Link integrity (layer 1)** — bit error rate, per-lane optical power, transceiver temperature, against thresholds.
3. **Link state stability** — down links, flapping links, ports in error-disable, negotiation failures.
4. **Speed/width** — a link planned as 4x NDR that trained at 2x or HDR is flagged as underperforming (overlapping `iblinkinfo`'s "Could be 4X", but against the *plan*).

CVT supports InfiniBand (Quantum), NVOS switches, and Ethernet (Spectrum) fabrics, so the same workflow applies to a Spectrum-X compute fabric as to an IB one.

**Why the exam cares.** The classic question: "all 256 links Active/4X NDR, nccl-tests 15% below reference — which tool?" Answer: CVT.

### 2.2 The two inputs: the plan and the reality

**Analogy.** You cannot grade an exam without the answer key. The planned topology is the answer key; the discovered fabric is the student's paper.

**Mechanism — the plan.** CVT needs an authoritative description of how the fabric *should* be wired. The user manual accepts topology files in **P2P (point-to-point) format, `.topo` format, or DOT (Graphviz) format**. Conceptually all three enumerate links as:

```text
<node-A> <port-A>  <->  <node-B> <port-B>
```

| Source | Notes |
|---|---|
| SuperPOD / BasePOD reference architecture cabling tables | NVIDIA publishes exact port maps for each SU; integrators convert them to P2P/topo |
| NVIDIA topology / cluster design tooling (e.g. the topology generators used with NVIDIA Air and SuperPOD deployment services) | Produce `.topo`/DOT that CVT can consume; exact export path depends on tool version — verify in current docs |
| Spreadsheet from the cabling contractor | Convert with a script; this is the common real-world case |
| `ibdiagnet` `net_dump` of a known-good reference fabric | When replicating an SU, the first validated SU becomes the plan for the next ones |

The plan must use the same node naming the switches and hosts actually report (switch hostname / node description, host hostname + HCA). Naming mismatches are the number one source of false "unknown neighbor" findings — set switch hostnames before you validate.

**Mechanism — the reality.** CVT's current architecture collects reality through **agents** installed on the switches (and optionally servers). Agents monitor node and link state every ~10 seconds and push updates to the **collector** on change (plus a periodic full report). Agents require TCP **8251** open on the switches' management interface (verify in current docs). For devices that cannot run an agent, CVT supports *external/unmanaged* agents the collector only monitors, and in InfiniBand environments the fabric view can be reconciled against SM / **ibdiagnet** discovery data.

Without the appliance: run `ibdiagnet` with `--get_cable_info` and `--extended_speeds all`, then compare `ibdiagnet2.net_dump` / `ibdiagnet2.db_csv` with the plan (Section 3 does exactly this).

**Why the exam cares.** It asks which formats are valid (P2P/topo/DOT — not YANG, not YAML) and what "no plan loaded" means (no connectivity validation; integrity monitoring still works).

### 2.3 Architecture: collector, agents, UI, API

**Analogy.** A site foreman (collector) with a radio to every floor (agents), a whiteboard everyone reads (web UI), and a phone line to head office (REST API).

```text
             +------------------------------------------+
             |  CVT Collector ("bring-up server")       |
             |  Docker container                        |
             |  - reads topology file (P2P/topo/DOT)    |
             |  - deploys & monitors agents             |
             |  - compares plan vs discovered           |
             |  - web UI (filter/sort/CSV) + REST API   |
             +------+------------------+----------------+
                    | mgmt network      | :8251
     +--------------+------+   +--------+-------------+
     | Agent on leaf-01    |   | Agent on spine-01    |    ... agents on every switch
     | link state / BER /  |   |                      |        (and optionally hosts)
     | neighbor every 10 s |   |                      |
     +---------------------+   +----------------------+
```

- **Collector / bring-up server** — the only component you interact with. Runs anywhere with reachability to the switch management network. Holds the plan, receives agent reports, runs the comparison, serves the web UI and REST API.
- **Agents** — lightweight processes on switches (and servers) that report local port state, neighbor identity (LLDP on Ethernet; IB link-layer neighbor information on IB), and layer-1 metrics (BER, lane power, temperature — the same data `mlxlink -m -c` shows, Module 8.3). They start reporting after the collector sends `start_validation`.
- **Web UI** — per-link status, filterable by switch, rack, issue type; CSV export for the cabling crew.
- **REST API** — gate the next bring-up step on "zero miswires" from deployment automation.

Exact install commands (container image, `docker run` parameters, agent push mechanism, port) differ by release — **verify in the current CVT Installation Notes**. For the exam, know the roles, not the flags.

### 2.4 The workflow and where it sits in bring-up

| Phase | What happens | Exit criterion |
|---|---|---|
| **1. Plan** | Load the cabling plan into the collector. Fix naming so plan node names match what devices report. | Plan parses; node count matches BoM |
| **2. Discover** | Agents (or ibdiagnet/SM) report every physical link and its layer-1 health. | Every planned device is reporting (no "no-report" / "unreachable-device") |
| **3. Compare** | Collector matches each discovered link to a planned link. | Comparison completes; mismatch list produced |
| **4. Report** | Categorized findings in UI/CSV: wrong-neighbor, wrong-port, extra cable, missing/no-transceiver, high BER, low power, flapping, speed/width below plan. | Cabling crew has a per-rack punch list |
| **5. Remediate** | Re-seat/move/replace cables; re-validate. Agents report changes within seconds. | Zero connectivity mismatches, zero integrity failures, documented exceptions |

Because agents stream changes, a technician moves a cable and watches the row flip from `wrong-port` to `OK` while still at the rack — CVT's practical advantage over "run ibdiagnet, email a diff, wait a day".

Bring-up order:

1. Racks powered, switches with management IP and hostnames, **no SM / no production config yet**.
2. CVT connectivity validation → fix all miswires. Doing this *before* SM/routing configuration avoids chasing routing anomalies that are really cabling errors.
3. Layer-1 soak: leave CVT monitoring for hours/overnight; fix anything with rising BER or flaps.
4. Enable SM / UFM (IB) or BGP/EVPN (Ethernet); ibdiagnet / NetQ validation.
5. perftest per link, nccl-tests per SU (Module 8.1).

**Why the exam cares.** "When should the first full CVT run happen?" — after cabling and hostnames, before SM/routing.

### 2.5 Report categories: learn the vocabulary

| Category | Findings | Typical cause | Fix |
|---|---|---|---|
| **Miswiring** | *wrong-neighbor* (lands on a different switch than planned), *wrong-port* (right switch, wrong port), *unknown-neighbor* (far end not in plan), *extra cable* (discovered, no planned counterpart), *no transceiver* (planned port empty) | Human error at install; two cables swapped; plan/label mismatch | Move the cable; or correct the plan if the plan was wrong (document it) |
| **Link integrity** | BER above threshold, lane Rx/Tx power out of range, module temperature high | Dirty/scratched fiber, bent DAC, marginal transceiver, blocked airflow | Clean/reseat, replace cable or transceiver; check `mlxlink -m` |
| **Link state** | link down / no signal, flapping, ErrDisable-Rx, ErrDisable-Flap, anomalous port | Loose connector, failing transceiver, remote port disabled | Reseat; replace; clear err-disable |
| **Underperforming** | Negotiated speed/width below plan (2x not 4x; HDR not NDR) | Damaged lane in cable, wrong cable type, breakout mismatch | Replace cable; confirm cable PN matches design |
| **Provisioning / CVT** | AdminDown, negotiation fail, no-report (agent silent), unreachable-device | Port administratively down; firmware mismatch; agent not installed/port 8251 blocked | Enable port; fix mgmt reachability; install agent |

A mismatch pair (illustrative):

```text
Status         Planned                              Discovered
WRONG-PORT     leaf-r3-01:swp12 <-> dgx-17:mlx5_3   leaf-r3-01:swp13 <-> dgx-17:mlx5_3
WRONG-NEIGHBOR leaf-r2-01:swp17 <-> dgx-17:mlx5_2   leaf-r3-01:swp12 <-> dgx-17:mlx5_2
```

Together these are a classic **swapped rail**: node 17's rail-2 and rail-3 cables were crossed. Each link is Active and 4x NDR — nothing else would have caught it.

### 2.6 Rail validation in a SuperPOD

**Analogy.** Eight parallel express trains, one per GPU index. Every "GPU 3" passenger boards train 3. Put one passenger on train 2 and they need a transfer at the hub (spine) — slower for them and congesting the hub for everyone.

**Mechanism.** A rail-optimized compute fabric places GPU *n*'s NIC on every node onto the same leaf (the rail-*n* leaf) so the dominant NCCL pattern (GPU *n* ↔ GPU *n* across nodes) is one leaf hop:

```text
dgx-XX  mlx5_0 (GPU0 rail)  ->  rail-0 leaf
dgx-XX  mlx5_1 (GPU1 rail)  ->  rail-1 leaf
...
dgx-XX  mlx5_7 (GPU7 rail)  ->  rail-7 leaf
```

Plus: each leaf's uplinks spread evenly over all spines, and port numbering within a leaf follows the node index so the plan is predictable. See `../01-ai-datacenter-design/`.

Without CVT: `ibdiagnet` has a dedicated **rail-optimized topology validation** stage (ibdiagnet User Manual, "Rail Optimized Topology Validation") — enable it with the topology option and verify the flag name in your version. Manually, every port of a rail-N leaf should carry the same `mlx5_N`:

```bash
sudo iblinkinfo -l --switches-only | grep "rail-3-leaf" | grep -o 'mlx5_[0-9]' | sort | uniq -c
     32 mlx5_3
```

Any second `mlx5_` value on a rail leaf is a miswire.

### 2.7 CVT vs. UFM vs. ibdiagnet vs. NetQ vs. Air

| Tool | Role in cabling validation |
|---|---|
| **CVT** | Plan-vs-actual comparison and layer-1 health during bring-up; live agents; web report. Branded "UFM Cable Validation Tool" and distributed alongside UFM, but a separate collector/agent system |
| **UFM** | Production-time fabric manager (Module 2.x). Topology comparison and fabric validation overlap CVT for IB; holds cable inventory (vendor/PN/SN) and raises high-BER events. CVT is the bring-up tool; UFM is the day-2 tool |
| **ibdiagnet** | Produces the discovered topology (`net_dump`, `db_csv`) and cable info; has its own topology-file comparison and rail validation. The scripted alternative and a second opinion |
| **NetQ** | Ethernet equivalent for day-2: LLDP-based topology, `netq check` validations, link-flap events (Module 8.4) |
| **NVIDIA Air** | Cloud simulation to build the *planned* Cumulus/Spectrum topology before hardware arrives; its topology description seeds the cabling plan. Air does not simulate IB cabling faults, so CVT itself is a hardware tool |

## 3. Live-style walkthrough: plan → discover → compare → re-cable → re-run

Scenario: SU-1 of a SuperPOD has just been cabled. Switch hostnames are set, management IPs are up, no SM is enabled. You are the bring-up engineer. Because CVT's exact CLI varies by release, the walkthrough shows the collector steps as *what you do and what you see*, and shows the ibdiagnet-based fallback with real commands so you can reproduce it anywhere.

```bash
# ---- Step 1: PLAN. Produce the golden P2P file from the RA cabling table. One line per cable.
#      Naming rule: use the exact hostname each device reports, or every link becomes "unknown-neighbor".
bringup$ head -4 su1-plan.p2p
dgx-01 mlx5_0  leaf-r0-01 1
dgx-01 mlx5_1  leaf-r1-01 1
dgx-01 mlx5_2  leaf-r2-01 1
dgx-01 mlx5_3  leaf-r3-01 1
bringup$ wc -l su1-plan.p2p
320 su1-plan.p2p          # 32 nodes x 8 rails = 256 host links + 64 leaf->spine uplinks. Matches the BoM: plan is complete.

# ---- Step 2: confirm the devices call themselves what the plan calls them. Fix BEFORE loading.
leaf-r3-01$ nv show system hostname          # Cumulus / NVOS (NVUE)
leaf-r3-01
# (MLNX-OS IB switch: 'show hosts' / 'hostname leaf-r3-01' in config mode.)
# If a switch still says "switch-5a3f", set it now: nv set system hostname leaf-r3-01 ; nv config apply

# ---- Step 3: DISCOVER. On the CVT collector: load su1-plan.p2p, confirm 40 devices (32 hosts + 8 leaves
#      + spines) are listed, deploy agents, start validation. Watch the device list: every device must
#      go to "reporting". One that stays "no-report" is a PROVISIONING finding, not a cabling one:
#      check TCP 8251 from collector to switch mgmt IP before you blame a cable.
bringup$ nc -zv 10.0.100.23 8251           # leaf-r3-01 mgmt; "succeeded" = agent port reachable (verify port in docs)

# Fallback / second opinion without the appliance: discover with ibdiagnet from any node.
# No SM yet? ibdiagnet needs an Active port. For pure bring-up either run a temporary opensm on the
# bring-up host, or do the CVT step first (agents do not need an SM). Here a temporary SM is running.
dgx-01$ sudo ibdiagnet --extended_speeds all --get_cable_info -o /var/tmp/ibdiagnet2
dgx-01$ grep -c "" /var/tmp/ibdiagnet2/ibdiagnet2.net_dump
# net_dump lists each node and, per port, its neighbor. Convert it to the same P2P shape as the plan:
dgx-01$ awk '/^"?dgx-|^"?leaf-|^"?spine-/{node=$1} /: *[0-9]+ *: .*->/{...}' ibdiagnet2.net_dump  # (your parser; see Solution guide)
dgx-01$ sort -u discovered.p2p | wc -l
320                      # same count as the plan: no missing or extra cables. Good first sign.

# ---- Step 4: COMPARE. CVT does this live in the UI. The fallback is a sorted diff:
dgx-01$ diff <(sort su1-plan.p2p) <(sort discovered.p2p)
< dgx-17 mlx5_2  leaf-r2-01 17          # planned, not found
< dgx-17 mlx5_3  leaf-r3-01 17          # planned, not found
> dgx-17 mlx5_2  leaf-r3-01 17          # found, not planned  -> CVT would say WRONG-NEIGHBOR
> dgx-17 mlx5_3  leaf-r2-01 17          # found, not planned  -> WRONG-NEIGHBOR
# Two planned links missing, two unplanned present, same node, same port numbers, rails 2 and 3 crossed.
# Decision: a swapped rail on dgx-17. Every one of these four links is Active / 4X NDR —
# iblinkinfo would show nothing. Confirm with the rail rule:
dgx-01$ sudo iblinkinfo -l --switches-only | grep "leaf-r3-01" | grep -o 'mlx5_[0-9]' | sort | uniq -c
     31 mlx5_3
      1 mlx5_2             # the intruder
dgx-01$ sudo iblinkinfo -l --switches-only | grep "leaf-r3-01" | grep mlx5_2
... ==( 4X 106.25 Gbps Active/ LinkUp)==> 73 1[  ] "dgx-17 mlx5_2" ( )     # perfectly healthy, perfectly wrong

# ---- Step 5: READ THE REPORT the way the cabling crew will. In the CVT UI, filter on dgx-17:
#      Status          Planned                              Discovered
#      WRONG-NEIGHBOR  leaf-r2-01:17 <-> dgx-17:mlx5_2      leaf-r3-01:17 <-> dgx-17:mlx5_2
#      WRONG-NEIGHBOR  leaf-r3-01:17 <-> dgx-17:mlx5_3      leaf-r2-01:17 <-> dgx-17:mlx5_3
#      Export CSV -> the punch list says: "dgx-17, swap the cables in mlx5_2 and mlx5_3". One line, one fix.
#      Also scan the other tabs before you send anyone to the rack:
#        Link integrity : 0 findings (BER / power / temp all inside thresholds)
#        Link state     : 0 flapping, 0 down
#        Underperforming: 0 (no 2x, no HDR on NDR-planned links)
#        Provisioning   : 0 no-report
#      Decision: exactly one miswire in 320 cables. Fix it; keep CVT running for the overnight soak.

# ---- Step 6: RE-CABLE. Technician swaps the two OSFP cables at dgx-17 (the node end is easier than
#      the leaf end: both offending cables terminate on the same server). Because agents push on change,
#      both rows flip to OK in the UI within ~10 s, while they are still standing there.

# ---- Step 7: RE-RUN the comparison. Trust but verify with the fallback too:
dgx-01$ sudo ibdiagnet --extended_speeds all --get_cable_info -o /var/tmp/ibdiagnet2 >/dev/null
dgx-01$ diff <(sort su1-plan.p2p) <(sort discovered.p2p) && echo "PLAN == ACTUAL"
PLAN == ACTUAL
dgx-01$ sudo iblinkinfo -l --switches-only | grep "leaf-r3-01" | grep -o 'mlx5_[0-9]' | sort | uniq -c
     32 mlx5_3
# And the proof that matters to the customer: rail-2 to rail-2 between any two nodes is ONE leaf hop.
dgx-01$ sudo ibtracert 73 81              # dgx-17 mlx5_2 (LID 73) -> dgx-18 mlx5_2 (LID 81)
From ca {...} portnum 1 lid 73-73 "dgx-17 mlx5_2"
[1] -> switch port {...}[17] lid 12-12 "MF0;leaf-r2-01:MQM9700/U1"
[18] -> ca port {...}[1] lid 81-81 "dgx-18 mlx5_2"
To ca {...} portnum 1 lid 81-81 "dgx-18 mlx5_2"
# Before the fix this trace went leaf-r3-01 -> spine -> leaf-r2-01: three hops instead of one.
# Decision: SU-1 cabling validated. Snapshot discovered.p2p as the reference plan for SU-2.
```

What "good" looks like: plan and discovered line counts equal; `diff` empty; every rail leaf shows exactly one `mlx5_N` value at count = node count; CVT tabs for integrity/state/underperforming/provisioning all empty after the soak.

## 4. Troubleshooting / common gotchas

### 4.1 Hundreds of "unknown-neighbor" findings on a fabric that is actually fine

- **Symptom:** the CVT comparison flags most links as unknown-neighbor or wrong-neighbor; the cabling crew swears it is right.
- **Why it happens:** the plan names devices differently from what they report (`leaf-r3-01` in the plan, `MF0;leaf03:MQM9700/U1` or a default `switch-5a3f` on the device). CVT matches by name.
- **How to debug:** compare `cut -f3 su1-plan.p2p | sort -u` with `ibswitches` node descriptions (or `nv show system hostname` on each switch). Look for one systematic pattern (prefix, case, zero-padding) rather than 300 individual errors.
- **Fix:** set hostnames on the devices *before* validating (`nv set system hostname …; nv config apply`), or normalize names in the plan. Re-run; the findings collapse to the real ones.

### 4.2 Treating "no-report" as a cabling problem

- **Symptom:** leaf-05 shows every link missing; the crew re-seats 32 cables for nothing.
- **Why it happens:** the agent on leaf-05 is not running or cannot reach the collector (TCP 8251 blocked, wrong management VRF, agent not deployed). That is a *provisioning* category finding, not miswiring.
- **How to debug:** on the collector, check the device list status for leaf-05; `nc -zv <leaf-05-mgmt-ip> 8251` from the collector; on the switch, check the agent process/service status per the CVT release notes.
- **Fix:** restore management reachability or redeploy the agent; links reappear without touching a cable.

### 4.3 Validating with no plan, or with a plan from the wrong SU

- **Symptom:** CVT shows green link-integrity data but no connectivity findings at all — or flags every single link as extra cable.
- **Why it happens:** no topology file loaded (CVT then only does layer-1 monitoring), or the SU-1 plan was loaded for SU-2 whose node names are `dgx-33`…`dgx-64`.
- **How to debug:** confirm the plan is loaded and its node list intersects the discovered device list; `wc -l` the plan against the BoM.
- **Fix:** load the correct plan; for a replica SU, clone the validated SU's discovered topology and rename nodes.

### 4.4 Running the cabling validation after the SM and routing are up

- **Symptom:** a week into bring-up, routing looks "asymmetric", `ibdiagnet -r` warns about unexpected topology, perftest between some pairs takes an extra hop.
- **Why it happens:** CVT was skipped or run late; cabling faults are now being debugged as routing faults.
- **How to debug:** run the Section 3 diff now; count `mlx5_N` per rail leaf.
- **Fix:** fix cables, then re-run the SM sweep / ibdiagnet -r. Put CVT before SM enablement in the runbook.

## 5. Hands-on assignment: a CVT-style cabling plan and diff procedure for one rail-optimized SU

**Scenario.** Your integrator will cable a 32-node, 8-rail SU next month: 8 rail leaves (`leaf-r0-01`…`leaf-r7-01`, 64 ports each), 4 spines (`spine-01`…`spine-04`), nodes `dgx-01`…`dgx-32` with `mlx5_0`…`mlx5_7`. You must hand them a plan and a procedure to validate against it with or without CVT.

**Requirements.**
1. Generate a P2P plan file `su-plan.p2p` with one line per cable: `<node> <port> <node> <port>`.
2. Rail rule: `dgx-NN mlx5_R` connects to `leaf-rR-01` port `NN` (node index = leaf port).
3. Uplinks: each leaf uses ports 33–64 toward the spines, spread evenly (8 ports per spine), with a deterministic spine port assignment.
4. A script `cvt-diff.sh` that takes the plan and an `ibdiagnet2.net_dump` (or an already-normalized discovered P2P file) and prints findings classified as **wrong-neighbor**, **wrong-port**, **extra cable**, **missing**, mirroring CVT's vocabulary.
5. A rail check that prints, per rail leaf, the count of each `mlx5_N` seen and flags leaves with more than one value.
6. A one-page runbook: when in bring-up to run it, who fixes what, exit criteria.

**Deliverables.** `gen-plan.sh` (or Python), `su-plan.p2p`, `cvt-diff.sh`, `runbook.md`, and a demonstration run against a synthetic discovered file with two cables deliberately swapped.

**Acceptance criteria.**
- AC1: `su-plan.p2p` has exactly 256 host links + 256 uplinks = 512 lines, no duplicates, no leaf port used twice.
- AC2: the diff classifies a swapped-rail pair as two wrong-neighbor findings and a same-leaf port swap as two wrong-port findings.
- AC3: the rail check reports `32 mlx5_R` for every clean leaf and flags any leaf with a second value.
- AC4: the runbook places validation after hostnames/management and before SM/routing, and defines exit as zero connectivity findings.

### Hint
- Section 2.6 states the rail rule in one line; two nested loops produce 256 host links.
- For uplinks, leaf port `32 + s*8 + k` → `spine-s` is deterministic; the spine port can be `leafIndex*8 + k`. Check nobody shares a spine port.
- Section 2.5's table defines the vocabulary: wrong-port = same switch, different port; wrong-neighbor = different switch. Compare keyed on the *host-side* endpoint to tell them apart.
- `diff <(sort plan) <(sort discovered)` from Section 3 is the raw material; classify its `<`/`>` lines by pairing on `dgx-NN mlx5_R`.
- Section 2.4's table gives you the runbook phases and exit criteria almost verbatim.

## Appendix A — Quick command / config reference

CVT itself is driven through its web UI and REST API; exact CLI differs by release. The commands below are the surrounding workflow that is stable.

```bash
# Set switch hostnames first (so plan names match discovery)
nv set system hostname leaf-r3-01 ; nv config apply       # Cumulus / NVOS (NVUE)
# (MLNX-OS IB: 'hostname leaf-r3-01' in config mode)

# Discovery data without CVT (InfiniBand)
sudo ibdiagnet --extended_speeds all --get_cable_info -o /var/tmp/ibdiagnet2
less /var/tmp/ibdiagnet2/ibdiagnet2.net_dump         # node/port/neighbor listing
less /var/tmp/ibdiagnet2/ibdiagnet2.cables           # vendor, PN, SN, length, temp per link
# Rail check by hand
sudo iblinkinfo -l --switches-only | grep "<rail-leaf-name>" | grep -o 'mlx5_[0-9]' | sort | uniq -c
# Quick plan-vs-actual diff (your own script around net_dump), e.g.
diff <(sort plan.p2p) <(sort discovered.p2p)
# Path proof after a fix
sudo ibtracert <src-lid> <dst-lid>

# Discovery data without CVT (Ethernet / Cumulus): LLDP neighbors
nv show interface --view=lldp
netq show lldp                                   # fabric-wide from NetQ

# CVT collector: Docker container on bring-up server; agents on switches; TCP 8251 open
#   -> see "UFM Cable Validation Tool — Installation Notes" for the current image name,
#      run command, and agent deployment steps (verify in current CVT docs)
```

| Symptom | Tool | Category |
|---|---|---|
| All links up, collective slow, no "Could be" | CVT plan compare; rail count | Miswiring |
| One leaf shows no links at all | Agent / TCP 8251 / mgmt reachability | Provisioning |
| Link planned 4x NDR trained 2x or HDR | CVT underperforming; `iblinkinfo`; `mlxlink -m` | Underperforming |
| BER / Rx power / temperature out of range | CVT integrity; `mlxlink -m -c` | Link integrity |
| Flapping, err-disabled | CVT state; `perfquery` LinkErrorRecovery | Link state |

## Appendix B — Exam traps & key takeaways

- "All links Active and 4x NDR" does **not** mean the fabric is cabled correctly. Link-state tools cannot see a swapped rail; CVT (plan comparison) can.
- CVT compares against a *plan*. No plan file = no connectivity validation; it can still do layer-1 integrity monitoring.
- Plan formats: P2P, `.topo`, DOT. YANG/YAML are distractors.
- Node-name mismatches between plan and devices generate spurious unknown-neighbor findings. Hostnames before validation.
- *Wrong-port* = right switch, wrong port. *Wrong-neighbor* = wrong switch. *Extra cable* = present but not planned. *No-report* = agent silent (provisioning). Know the vocabulary.
- Architecture: collector ("bring-up server", Docker) + agents on switches (TCP 8251, ~10 s updates) + web UI + REST API.
- CVT is a **bring-up** tool with live agents; UFM is the **production** manager. "Before SM is enabled, racks just cabled" → CVT.
- Supports IB, NVOS, and Ethernet (Spectrum) fabrics — not IB only.
- Underperforming (2x/HDR on a planned 4x NDR link) is a *cable/lane* issue; the fix is physical, not an SM or routing change.
- Rail-optimized rule: `mlx5_N` of every node → rail-N leaf; `iblinkinfo | grep leaf | grep -o 'mlx5_[0-9]' | sort | uniq -c` must return one value.
- ibdiagnet (`net_dump`, `--get_cable_info`, rail validation stage) is the scripted fallback; UFM is day-2; NetQ/LLDP is the Ethernet day-2 equivalent.
- Do CVT before enabling SM/routing so you do not debug routing symptoms caused by cables.
- Exact binary names, Docker image, and flags vary by CVT version — memorize roles and workflow, not an invocation.

## Appendix C — Knowledge check

1. All 256 compute links in an SU show `Active / LinkUp / 4X NDR` in iblinkinfo, yet nccl-tests is 15% below reference. Which tool is designed to find the likely cause?  
   A. perfquery  
   B. Cable Validation Tool (plan-vs-actual comparison)  
   C. ibping  
   D. sminfo

2. In a CVT report, a link whose far end is the correct leaf switch but a different port than planned is categorized as:  
   A. Wrong-neighbor  
   B. Extra cable  
   C. Wrong-port  
   D. Unknown-neighbor

3. Which of the following is NOT an input format the CVT user manual lists for the planned topology?  
   A. P2P  
   B. .topo  
   C. DOT  
   D. YANG

4. The CVT collector shows "no-report" for leaf-05. The most likely cause is:  
   A. A cable on leaf-05 is miswired  
   B. The agent on leaf-05 is not running or its management port (TCP 8251) is blocked  
   C. The SM has not assigned LIDs  
   D. leaf-05's uplinks trained at 2x

5. When in a SuperPOD bring-up should the first full CVT connectivity validation run?  
   A. After nccl-tests passes  
   B. After the SM/UFM is enabled and routing is verified  
   C. After cabling and switch hostname/management setup, before SM/routing configuration  
   D. Only when UFM raises a high-BER alarm

6. Which statement about CVT and UFM is correct?  
   A. CVT replaces UFM as the production fabric manager  
   B. CVT is a bring-up validation tool with live agents; UFM is the day-2 fabric manager, and the two are complementary  
   C. UFM cannot show cable inventory, so CVT is required in production  
   D. CVT only works on Ethernet fabrics

7. A rail-3 leaf in a rail-optimized fabric shows 31 ports connected to `mlx5_3` and one to `mlx5_2`. This is:  
   A. Expected — rail leaves always carry one management NIC  
   B. A miswire (swapped rail) on one node  
   C. A routing problem that the SM will fix  
   D. A PKey mismatch

8. A CVT comparison flags nearly every link as unknown-neighbor immediately after loading the plan. The first thing to check is:  
   A. Transceiver firmware versions  
   B. Whether device hostnames match the node names used in the plan  
   C. Whether the SM is running  
   D. PFC configuration on the leaves

### Answer key
1. B — Link-state tools cannot detect a correctly-trained but mis-placed cable; CVT compares against the plan.
2. C — Right switch, wrong port = wrong-port.
3. D — The manual lists P2P, topo and DOT; YANG is a data-modeling language, not a CVT topology format.
4. B — "no-report" is a provisioning/CVT-category finding: the agent is not reporting to the collector.
5. C — Validate cabling before layering SM/routing so cabling faults are not mistaken for routing faults.
6. B — CVT for bring-up with live agents and plan comparison; UFM for ongoing fabric management.
7. B — Every port of a rail-N leaf should carry `mlx5_N`; the odd one is a swapped rail.
8. B — CVT matches by name; a systematic naming mismatch produces mass unknown-neighbor findings.

## References
- NVIDIA UFM Cable Validation Tool User Manual (v1.4.0 / v1.5.0 / v1.8.1): https://docs.nvidia.com/networking/display/cablevalidationtool150/Introduction (redirects to networking-docs.nvidia.com/cablevalidationtool/)
- ibdiagnet User Manual — "Rail Optimized Topology Validation": https://docs.nvidia.com/networking/display/ibdiagnetusermanualv221/rail+optimized+topology+validation
- NVIDIA DGX SuperPOD Reference Architecture (cabling and rail design): https://docs.nvidia.com/dgx-superpod/
- NVIDIA Academy: "Cable Validation Tool (CVT) Fundamentals"
- Related modules: `01-infiniband-diagnostics-and-perftest.md`, `03-mlxlink-and-mlxcables-debug-tools.md`, `../01-ai-datacenter-design/`, `../02-infiniband/`

---
## Solution guide — Hands-on assignment

### Plan generator

```bash
#!/usr/bin/env bash
# gen-plan.sh — P2P cabling plan for one rail-optimized SU (32 nodes x 8 rails, 8 leaves, 4 spines)
# Output format (one cable per line): <nodeA> <portA> <nodeB> <portB>
set -eu
NODES=32; RAILS=8; SPINES=4
UPLINKS_PER_LEAF=32            # leaf ports 33..64 -> spines
PER_SPINE=$(( UPLINKS_PER_LEAF / SPINES ))   # 8 ports from each leaf to each spine

# Host links: rail rule from Section 2.6 — dgx-NN mlx5_R -> leaf-rR-01 port NN
for n in $(seq 1 $NODES); do
  for r in $(seq 0 $((RAILS-1))); do
    printf "dgx-%02d mlx5_%d leaf-r%d-01 %d\n" "$n" "$r" "$r" "$n"
  done
done
# Uplinks: leaf-rR-01 port 32 + s*8 + k  ->  spine-(s+1) port R*8 + k + 1
# Each spine therefore receives 8 ports from each of 8 leaves = 64 ports, none reused.
for r in $(seq 0 $((RAILS-1))); do
  for s in $(seq 0 $((SPINES-1))); do
    for k in $(seq 1 $PER_SPINE); do
      lp=$(( 32 + s*PER_SPINE + k ))
      sp=$(( r*PER_SPINE + k ))
      printf "leaf-r%d-01 %d spine-%02d %d\n" "$r" "$lp" "$((s+1))" "$sp"
    done
  done
done
```

```bash
$ ./gen-plan.sh > su-plan.p2p
$ wc -l su-plan.p2p                          # AC1: 256 + 256
512 su-plan.p2p
$ sort su-plan.p2p | uniq -d | wc -l         # AC1: no duplicate lines
0
$ awk '{print $3, $4}' su-plan.p2p | sort | uniq -d | wc -l    # AC1: no leaf/spine port used twice
0
$ awk '$1 ~ /^dgx/ {print $1, $2}' su-plan.p2p | sort | uniq -d | wc -l   # no HCA used twice
0
```

### Diff and classification script

```bash
#!/usr/bin/env bash
# cvt-diff.sh — classify plan-vs-discovered differences in CVT vocabulary
# Usage: cvt-diff.sh su-plan.p2p discovered.p2p
# discovered.p2p must be in the same 4-field format. For an ibdiagnet net_dump, normalize first
# (see normalize-netdump.awk below). Comparison is keyed on the HOST-side endpoint (dgx-NN mlx5_R)
# for host links and on the leaf-side endpoint for uplinks, so we can tell wrong-port from wrong-neighbor.
set -eu
PLAN=$1; DISC=$2
norm() {  # put the endpoint that identifies the cable first; canonical order: dgx < leaf < spine
  awk '{ if ($1 ~ /^dgx/ || ($1 ~ /^leaf/ && $3 ~ /^spine/)) print $1, $2, $3, $4; else print $3, $4, $1, $2 }' "$1" | sort -u
}
norm "$PLAN" > /tmp/plan.n; norm "$DISC" > /tmp/disc.n

# join on the identifying endpoint (fields 1+2)
join -j1 <(awk '{print $1"/"$2, $3, $4}' /tmp/plan.n | sort) <(awk '{print $1"/"$2, $3, $4}' /tmp/disc.n | sort) \
| awk '{
    key=$1; psw=$2; ppt=$3; dsw=$4; dpt=$5
    if (psw==dsw && ppt==dpt) ok++
    else if (psw==dsw)   printf "WRONG-PORT      %-18s planned %s:%s  discovered %s:%s\n", key, psw, ppt, dsw, dpt
    else                 printf "WRONG-NEIGHBOR  %-18s planned %s:%s  discovered %s:%s\n", key, psw, ppt, dsw, dpt
  } END { printf "OK              %d links match plan\n", ok }'
# planned endpoints with no discovered link at all
comm -23 <(awk '{print $1"/"$2}' /tmp/plan.n | sort) <(awk '{print $1"/"$2}' /tmp/disc.n | sort) \
| sed 's/^/MISSING         /'
# discovered endpoints that are not in the plan
comm -13 <(awk '{print $1"/"$2}' /tmp/plan.n | sort) <(awk '{print $1"/"$2}' /tmp/disc.n | sort) \
| sed 's/^/EXTRA-CABLE     /'

# Rail check (AC3): every port of leaf-rR-01 that faces a host must carry mlx5_R
echo "--- rail check ---"
awk '$3 ~ /^leaf-r/ { leaf=$3; sub(/^leaf-r/,"",leaf); sub(/-01$/,"",leaf); print $3, $2 }' /tmp/disc.n \
| sort | uniq -c | awk '{ cnt[$2]++; line[$2]=line[$2] sprintf(" %d x %s", $1, $3) }
    END { for (l in cnt) printf "%s %s%s\n", (cnt[l]>1 ? "RAIL-MISWIRE" : "RAIL-OK     "), l, line[l] }' | sort -k2
```

Normalizer for `ibdiagnet2.net_dump` (the exact layout varies slightly by ibdiagnet version; adjust the field positions after inspecting your file — the structure is always "node header, then one line per port with the neighbor"):

```awk
# normalize-netdump.awk — emit "<node> <port> <peer-node> <peer-port>" from ibdiagnet2.net_dump
# net_dump lines look (illustratively) like:
#   "dgx-17 mlx5_2", ... <node header>
#     1  : ... -> "MF0;leaf-r3-01:MQM9700/U1" : 17
/^"/ { node=$0; gsub(/^"|".*$/,"",node); hca=""; if (node ~ /mlx5_/) { split(node, a, " "); node=a[1]; hca=a[2] } }
/->/ { port=$1; peer=$0; sub(/.*-> *"/,"",peer); ppt=peer; sub(/".*/,"",peer); sub(/.*: */,"",ppt)
       gsub(/^MF0;|:MQM[0-9]*\/U1$/,"",peer)              # strip switch description decoration
       if (hca!="") print node, hca, peer, ppt; else print node, port, peer, ppt }
```

### Demonstration run (AC2)

```bash
# Build a synthetic "discovered" file: copy the plan, swap rails 2 and 3 on dgx-17, and swap ports 5/6 on leaf-r0-01
$ cp su-plan.p2p discovered.p2p
$ sed -i -e 's/^dgx-17 mlx5_2 leaf-r2-01 17$/dgx-17 mlx5_2 leaf-r3-01 17/' \
         -e 's/^dgx-17 mlx5_3 leaf-r3-01 17$/dgx-17 mlx5_3 leaf-r2-01 17/' \
         -e 's/^dgx-05 mlx5_0 leaf-r0-01 5$/dgx-05 mlx5_0 leaf-r0-01 6/' \
         -e 's/^dgx-06 mlx5_0 leaf-r0-01 6$/dgx-06 mlx5_0 leaf-r0-01 5/' discovered.p2p
$ ./cvt-diff.sh su-plan.p2p discovered.p2p
WRONG-PORT      dgx-05/mlx5_0      planned leaf-r0-01:5  discovered leaf-r0-01:6
WRONG-PORT      dgx-06/mlx5_0      planned leaf-r0-01:6  discovered leaf-r0-01:5
WRONG-NEIGHBOR  dgx-17/mlx5_2      planned leaf-r2-01:17  discovered leaf-r3-01:17
WRONG-NEIGHBOR  dgx-17/mlx5_3      planned leaf-r3-01:17  discovered leaf-r2-01:17
OK              508 links match plan
--- rail check ---
RAIL-OK      leaf-r0-01 32 x mlx5_0
RAIL-OK      leaf-r1-01 32 x mlx5_1
RAIL-MISWIRE leaf-r2-01 31 x mlx5_2 1 x mlx5_3
RAIL-MISWIRE leaf-r3-01 31 x mlx5_3 1 x mlx5_2
RAIL-OK      leaf-r4-01 32 x mlx5_4
...
```

AC2 is met: the swapped rail yields two WRONG-NEIGHBOR rows (different leaf), the same-leaf swap yields two WRONG-PORT rows. AC3 is met: clean leaves show `32 x mlx5_R`; the two affected leaves are flagged.

### Runbook (AC4)

```markdown
# SU cabling validation runbook
Prerequisites: racks powered; every switch has mgmt IP and its PLANNED hostname (nv set system hostname …);
               no SM enabled; su-plan.p2p generated from the RA and checked (512 lines, no duplicates).
1. Load su-plan.p2p into the CVT collector (or stage it on the bring-up host for the ibdiagnet fallback).
2. Deploy agents; confirm every device is "reporting". Any "no-report" -> fix TCP 8251 / mgmt reachability FIRST.
3. Start validation. Export the connectivity findings CSV, sorted by rack.
   Owner: cabling crew. Rule: WRONG-PORT/WRONG-NEIGHBOR -> move the cable; EXTRA/MISSING -> check against BoM;
   if the PLAN is wrong, change the plan and record why.
4. Watch findings flip to OK live; re-export until the connectivity list is EMPTY.
5. Fallback / second opinion: temporary opensm on bring-up host -> ibdiagnet --extended_speeds all --get_cable_info
   -> normalize net_dump -> ./cvt-diff.sh su-plan.p2p discovered.p2p -> zero WRONG/MISSING/EXTRA, all RAIL-OK.
6. Layer-1 soak overnight with agents running; fix any integrity (BER/power/temp), state (flap) or
   underperforming (2x/HDR) finding. Owner: cabling crew + network engineer (mlxlink -m -c, Module 8.3).
Exit criteria: zero connectivity findings; zero integrity/state/underperforming findings after soak;
               documented exceptions signed off. ONLY THEN enable SM/UFM (IB) or BGP/EVPN (Ethernet).
Archive: su-plan.p2p, final discovered.p2p, CSV exports -> this becomes the plan for the next SU.
```

Validation runs after hostnames/management and before SM/routing, and exit is defined as zero connectivity findings — AC4 met.
