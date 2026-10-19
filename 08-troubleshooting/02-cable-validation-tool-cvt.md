# Module 8.2 — Cable Validation Tool (CVT)

> **Exam domain:** Troubleshooting Tools (20%)  
> **Source course(s):** Cable Validation Tool (CVT) Fundamentals (NVIDIA Academy)  
> **Source docs:** NVIDIA UFM Cable Validation Tool User Manual (v1.4–v1.8); ibdiagnet User Manual ("Rail Optimized Topology Validation"); NVIDIA DGX SuperPOD Reference Architecture (cabling / rail design)  
> **Estimated study time:** 2 hours

> **Scope note.** CVT documentation is thinner and less public than the rest of the NVIDIA networking stack, and the tool has changed packaging between releases (standalone appliance → "UFM Cable Validation Tool" Docker-based collector with agents). This module is precise about *what* CVT validates and the *workflow* — which is what the exam tests — and explicitly hedges on exact binary names, flags, and file-format details. Where you see "verify in current CVT docs", do exactly that before relying on a command in production.

## Learning objectives
- Describe the purpose of CVT and the class of problems it catches that link-state tools (ibstat, iblinkinfo) do not.
- Identify the inputs CVT needs (planned topology file; discovered fabric) and the formats it accepts.
- Explain the plan → discover → compare → report → remediate workflow and where it sits in a SuperPOD bring-up schedule.
- Describe the CVT architecture (collector/bring-up server, agents, web UI, REST API) and the switch requirements for agents.
- Interpret CVT report categories: miswiring, link integrity, link state, underperforming links, provisioning issues.
- Relate CVT to **ibdiagnet** topology checks, **UFM**, and the rail-optimized cabling rules of a SuperPOD.
- Troubleshoot a swapped-rail or wrong-port finding end-to-end.

## 1. Why a cable validation tool exists

A DGX SuperPOD scalable unit has 32 nodes × 8 compute rails, plus storage and in-band management fabrics — thousands of cables, every one of which has an exact intended source and destination. `ibstat` and `iblinkinfo` can tell you every link is `Active / LinkUp / 4X NDR`, and the fabric can *still be wrong*: GPU3's NIC on node 17 plugged into the leaf that serves rail 2 instead of rail 3 will come up perfectly and silently cost performance on every collective, because rail-optimized traffic that should stay on one leaf now crosses a spine. Two uplinks swapped between spines produce an asymmetric topology that the routing engine will route around — until load reveals the hot spot.

Link-state tools answer "is this link up?". CVT answers "is this link **the one the design said should be here**, and is it healthy enough to trust?" That is a different question and it is the one that dominates bring-up.

What CVT checks:

1. **Connectivity correctness** — each discovered link (local node/port ↔ remote node/port) is compared with the planned cabling, cable by cable.
2. **Link integrity (layer 1)** — bit error rate, per-lane optical power, transceiver temperature, against thresholds.
3. **Link state stability** — down links, flapping links, ports in error-disable, negotiation failures.
4. **Speed/width** — a link planned as 4x NDR that trained at 2x or HDR is flagged as underperforming (this overlaps with `iblinkinfo`'s "Could be 4X", but CVT does it against the *plan*).

CVT supports InfiniBand (Quantum), NVOS switches, and Ethernet (Spectrum) fabrics, so the same workflow applies to a Spectrum-X compute fabric as to an IB one.

## 2. Inputs: the plan and the reality

### 2.1 The planned topology (the "golden" cabling plan)

CVT needs an authoritative description of how the fabric *should* be wired. The user manual accepts topology files in **P2P (point-to-point) format, `.topo` format, or DOT (Graphviz) format**. Conceptually all three enumerate links as:

```text
<node-A> <port-A>  <->  <node-B> <port-B>
```

Where the plan comes from in practice:

| Source | Notes |
|---|---|
| SuperPOD / BasePOD reference architecture cabling tables | NVIDIA publishes exact port maps for each SU; integrators convert them to P2P/topo |
| NVIDIA topology / cluster design tooling (e.g. the topology generators used with NVIDIA Air and SuperPOD deployment services) | Produce `.topo`/DOT that CVT can consume; exact export path depends on tool version — verify in current docs |
| Spreadsheet from the cabling contractor | Convert with a script; this is the common real-world case |
| `ibdiagnet` `net_dump` of a known-good reference fabric | Useful when replicating an SU: the first validated SU becomes the plan for the next ones |

The plan must use the same node naming the switches and hosts actually report (switch hostname / node description, host hostname + HCA). Naming mismatches are the number one source of false "unknown neighbor" findings — set switch hostnames before you validate.

### 2.2 The discovered fabric

CVT's current architecture collects reality through **agents** installed on the switches (and optionally servers). Agents monitor node and link state every ~10 seconds and push updates to the **collector** on change (plus a periodic full report). Agents require TCP **8251** open on the switches' management interface (verify in current docs). For devices that cannot run an agent, CVT supports *external/unmanaged* agents that the collector only monitors, and in InfiniBand environments the fabric view can be reconciled against SM / **ibdiagnet** discovery data.

Earlier/alternative workflows (and what you will use when you do not have the full CVT appliance): run `ibdiagnet` with `--get_cable_info` and `--extended_speeds all`, then compare `ibdiagnet2.net_dump` / `ibdiagnet2.db_csv` with the plan. Section 5 shows this.

## 3. Architecture

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
- **Agents** — lightweight processes on switches (and servers) that report local port state, neighbor identity (from LLDP on Ethernet, from the IB link-layer neighbor information on IB), and layer-1 metrics (BER, lane power, temperature — the same data `mlxlink -m -c` shows, Module 8.3). They start reporting after the collector sends `start_validation`.
- **Web UI** — the main report surface: per-link status, filterable by switch, rack, issue type; CSV export for the cabling crew.
- **REST API** — for integration into deployment automation (gate the next bring-up step on "zero miswires").

Exact install commands (container image name, `docker run` parameters, agent push mechanism, port) differ by release — **verify in the current CVT Installation Notes**. For the exam, know the roles, not the flags.

## 4. The workflow: plan → discover → compare → report → remediate

| Phase | What happens | Exit criterion |
|---|---|---|
| **1. Plan** | Load the cabling plan into the collector. Fix naming so plan node names match what devices report. | Plan parses; node count matches BoM |
| **2. Discover** | Agents (or ibdiagnet/SM) report every physical link and its layer-1 health. | Every planned device is reporting (no "no-report" / "unreachable-device") |
| **3. Compare** | Collector matches each discovered link to a planned link. | Comparison completes; mismatch list produced |
| **4. Report** | Categorized findings in UI/CSV: wrong-neighbor, wrong-port, extra cable, missing/no-transceiver, high BER, low power, flapping, speed/width below plan. | Cabling crew has a per-rack punch list |
| **5. Remediate** | Re-seat/move/replace cables; re-validate. Agents report state changes within seconds, so fixes are confirmed live. | Zero connectivity mismatches, zero integrity failures, documented exceptions |

Because the agents stream changes, the loop is tight: a technician moves a cable, the collector shows the link going from `wrong-port` to `OK` while they are still at the rack. That is CVT's practical advantage over "run ibdiagnet, email a diff, wait a day".

### Where it sits in bring-up

1. Racks powered, switches with management IP and hostnames, **no SM / no production config yet**.
2. CVT connectivity validation (this module) → fix all miswires. Doing this *before* SM/routing configuration avoids chasing routing anomalies that are really cabling errors.
3. Layer-1 soak: leave CVT monitoring for hours/overnight; fix anything with rising BER or flaps.
4. Enable SM / UFM (IB) or BGP/EVPN (Ethernet); ibdiagnet / NetQ validation.
5. perftest per link, nccl-tests per SU (Module 8.1).

## 5. Reading the report

CVT groups findings. Learn the categories; the exam asks "which category is this?" style questions.

| Category | Findings | Typical cause | Fix |
|---|---|---|---|
| **Miswiring** | *wrong-neighbor* (cable lands on a different switch than planned), *wrong-port* (right switch, wrong port), *unknown-neighbor* (far end not in plan), *extra cable* (a discovered link with no planned counterpart), *no transceiver* (planned port empty) | Human error at install; two cables swapped; plan/label mismatch | Move the cable; or correct the plan if the plan was wrong (document it) |
| **Link integrity** | BER above threshold, lane Rx/Tx power out of range, module temperature high | Dirty/scratched fiber, bent DAC, marginal transceiver, blocked airflow | Clean/reseat, replace cable or transceiver; check `mlxlink -m` |
| **Link state** | link down / no signal, flapping, ErrDisable-Rx, ErrDisable-Flap, anomalous port | Loose connector, failing transceiver, remote port disabled | Reseat; replace; clear err-disable |
| **Underperforming** | Negotiated speed/width below plan (2x not 4x; HDR not NDR) | Damaged lane in cable, wrong cable type, breakout mismatch | Replace cable; confirm cable PN matches design |
| **Provisioning / CVT** | AdminDown, negotiation fail, no-report (agent silent), unreachable-device | Port administratively down; firmware mismatch; agent not installed/port 8251 blocked | Enable port; fix mgmt reachability; install agent |

Reading a mismatch row (illustrative):

```text
Status        Planned                              Discovered
WRONG-PORT    leaf-r3-01:swp12 <-> dgx-17:mlx5_3   leaf-r3-01:swp13 <-> dgx-17:mlx5_3
WRONG-NEIGHBOR leaf-r2-01:swp17 <-> dgx-17:mlx5_2  leaf-r3-01:swp12 <-> dgx-17:mlx5_2
```

Those two rows together are a classic **swapped rail**: node 17's rail-2 and rail-3 cables were crossed. Each link is Active and 4x NDR — nothing else would have caught it.

## 6. Rail validation in a SuperPOD

A rail-optimized SuperPOD compute fabric places GPU *n*'s NIC on every node onto the same leaf (the rail-*n* leaf) so that the dominant NCCL traffic pattern (GPU *n* ↔ GPU *n* across nodes) is one leaf hop. The design rule CVT enforces:

```text
dgx-XX  mlx5_0 (GPU0 rail)  ->  rail-0 leaf
dgx-XX  mlx5_1 (GPU1 rail)  ->  rail-1 leaf
...
dgx-XX  mlx5_7 (GPU7 rail)  ->  rail-7 leaf
```

Plus: each leaf's uplinks spread evenly over all spines, and port numbering within a leaf follows the node index so that the plan is predictable. See `../01-ai-datacenter-design/` for the design rationale.

Validation without CVT: `ibdiagnet` has a dedicated **rail-optimized topology validation** stage (see the ibdiagnet User Manual, "Rail Optimized Topology Validation") that checks that each host's HCAs connect to the expected leaf group; enable it with the topology option and verify the flag name in your ibdiagnet version. Manually, `iblinkinfo -l` grouped by switch should show the *same* `mlx5_N` on every port of a given leaf:

```bash
$ sudo iblinkinfo -l --switches-only | grep "rail-3-leaf" | grep -o 'mlx5_[0-9]' | sort | uniq -c
     32 mlx5_3
```

Any count other than one `mlx5_` value on a rail leaf is a miswire.

## 7. Relationship to UFM, ibdiagnet, and NVIDIA Air

| Tool | Role in cabling validation |
|---|---|
| **CVT** | Plan-vs-actual comparison and layer-1 health during bring-up; live agents; web report. Branded as "UFM Cable Validation Tool" and distributed alongside UFM, but it is a separate collector/agent system |
| **UFM** | Production-time fabric manager (Module 2.x). UFM Enterprise's topology comparison and "fabric validation" tests overlap with CVT for IB fabrics; UFM also holds cable inventory (vendor/PN/SN) and raises high-BER events. CVT is the bring-up tool; UFM is the day-2 tool |
| **ibdiagnet** | Produces the discovered topology (`net_dump`, `db_csv`) and cable info; has its own topology-file comparison and rail validation. The scripted alternative when CVT is not deployed, and a good second opinion |
| **NetQ** | Ethernet equivalent for day-2: LLDP-based topology view, `netq check` validations, link-flap events (Module 8.4) |
| **NVIDIA Air** | Cloud simulation where you can build the *planned* Cumulus/Spectrum topology (and export its topology description) before hardware arrives; the same topology description is the natural seed for the cabling plan. Air does not simulate IB cabling faults, so CVT itself is a hardware-lab/production tool |

## 8. Worked example: swapped rails on one node

Symptom during acceptance: nccl-tests all_reduce across SU-1 reports busbw ~15% below the reference. Every link `Active`, `iblinkinfo` shows no "Could be".

1. CVT report filtered to node `dgx-17`: two **WRONG-NEIGHBOR** rows — `mlx5_2` on the rail-3 leaf, `mlx5_3` on the rail-2 leaf.
2. Confirm from the host: `ibtracert` from `dgx-17 mlx5_2` to `dgx-18 mlx5_2` shows a spine hop where every other node pair is one leaf hop.
3. Technician swaps the two cables at the node; CVT shows both rows flip to OK within seconds.
4. Re-run nccl-tests: busbw back to reference.

Without CVT, step 1 would have been "look at 256 links by hand".

## Configuration / command reference

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

# Discovery data without CVT (Ethernet / Cumulus): LLDP neighbors
nv show interface --view=lldp
netq show lldp                                   # fabric-wide from NetQ

# CVT collector: Docker container on bring-up server; agents on switches; TCP 8251 open
#   -> see "UFM Cable Validation Tool — Installation Notes" for the current image name,
#      run command, and agent deployment steps (verify in current CVT docs)
```

## Common pitfalls & exam traps
- "All links Active and 4x NDR" does **not** mean the fabric is cabled correctly. Link state tools cannot see a swapped rail; CVT (plan comparison) can.
- CVT compares against a *plan*. No plan file = no connectivity validation; it can still do layer-1 integrity monitoring.
- Node-name mismatches between plan and devices generate spurious unknown-neighbor findings. Hostnames before validation.
- *Wrong-port* = right switch, wrong port. *Wrong-neighbor* = wrong switch. *Extra cable* = present but not planned. Know the vocabulary.
- CVT is a **bring-up** tool with live agents; UFM is the **production** manager. The exam may describe a scenario and ask which to use — "before SM is enabled, racks just cabled" → CVT.
- Supports IB, NVOS, and Ethernet (Spectrum) fabrics — not IB only.
- Underperforming (2x/HDR on a planned 4x NDR link) is a *cable/lane* issue; the fix is physical, not an SM or routing change.
- Exact binary names, Docker image, and flags vary by CVT version — do not memorize a specific invocation for the exam; memorize roles and workflow.

## Key takeaways
- CVT validates physical cabling against the planned topology and layer-1 health, before and during bring-up.
- Inputs: topology plan (P2P / .topo / DOT) + discovered links from switch/server agents (or ibdiagnet data).
- Architecture: collector ("bring-up server", Docker) + agents on switches (TCP 8251) + web UI + REST API.
- Workflow: plan → discover → compare → report → remediate; agents make remediation feedback near-real-time.
- Finding categories: miswiring, link integrity, link state, underperforming, provisioning.
- Rail-optimized rule: `mlx5_N` of every node → rail-N leaf; a swapped rail is Active/4x and invisible to link-state tools.
- ibdiagnet (`net_dump`, `--get_cable_info`, rail validation stage) is the scripted fallback; UFM is day-2.
- Do CVT before enabling SM/routing so you do not debug routing symptoms caused by cables.

## Review questions

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

### Answer key
1. B — Link-state tools cannot detect a correctly-trained but mis-placed cable; CVT compares against the plan.
2. C — Right switch, wrong port = wrong-port.
3. D — The manual lists P2P, topo and DOT; YANG is a data-modeling language, not a CVT topology format.
4. B — "no-report" is a provisioning/CVT-category finding: the agent is not reporting to the collector.
5. C — Validate cabling before layering SM/routing so cabling faults are not mistaken for routing faults.
6. B — CVT for bring-up with live agents and plan comparison; UFM for ongoing fabric management.
7. B — Every port of a rail-N leaf should carry `mlx5_N`; the odd one is a swapped rail.

## References
- NVIDIA UFM Cable Validation Tool User Manual (v1.4.0 / v1.5.0 / v1.8.1): https://docs.nvidia.com/networking/display/cablevalidationtool150/Introduction (redirects to networking-docs.nvidia.com/cablevalidationtool/)
- ibdiagnet User Manual — "Rail Optimized Topology Validation": https://docs.nvidia.com/networking/display/ibdiagnetusermanualv221/rail+optimized+topology+validation
- NVIDIA DGX SuperPOD Reference Architecture (cabling and rail design): https://docs.nvidia.com/dgx-superpod/
- NVIDIA Academy: "Cable Validation Tool (CVT) Fundamentals"
- Related modules: `01-infiniband-diagnostics-and-perftest.md`, `03-mlxlink-and-mlxcables-debug-tools.md`, `../01-ai-datacenter-design/`, `../02-infiniband/`
