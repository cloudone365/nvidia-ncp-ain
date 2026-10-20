# Module 4.5 — NetQ Deployment and Installation

> **Exam domain:** NVIDIA Spectrum Networking (30%) — also supports Troubleshooting Tools (20%)  
> **Source course(s):** NetQ Deployment and Installation (NVIDIA Academy)  
> **Source docs:** NVIDIA NetQ User Guide (4.x) — Installation Management, Lifecycle Management, Validation Checks, Monitor Operations, Events and Notifications; NetQ CLI Reference  
> **Estimated study time:** 4–5 hours

> **How to study this module:** read §1–§2, work through the §3 walkthrough on real gear or NVIDIA Air, then attempt the §5 hands-on assignment using only the Hint section. Open the Solution guide (last section) only after you have a working attempt. Finish with the Appendix C knowledge check closed-book.

## 1. Learning objectives

By the end of this module you will be able to…

- **Describe** the NetQ architecture: server (on-premises VM or cloud-hosted), agents, OPTA, and data flow. *(Troubleshooting Tools — telemetry platforms)*
- **Size** and **install** an on-premises NetQ server VM with `netq install`/`netq bootstrap`. *(Spectrum Networking — NetQ deployment)*
- **Install** and **configure** the NetQ agent on Cumulus Linux, SONiC and Linux hosts with `netq config add server`. *(Spectrum Networking — NetQ deployment)*
- **Perform** validation checks (`netq check bgp|evpn|mlag|interfaces|roce`) and **interpret** results. *(Troubleshooting Tools — fabric validation)*
- **Use** NetQ for RoCE telemetry (`netq show roce-counters`, `netq show roce-config`) and **WJH** drop analysis (`netq show wjh-drop`). *(Troubleshooting Tools — RoCE/WJH)*
- **Run** traces (`netq trace`), **inspect** topology and events, and **configure** threshold-based notifications to Slack/PagerDuty/syslog. *(Troubleshooting Tools — events)*
- **Describe** Lifecycle Management (LCM) for switch image and NetQ agent upgrades. *(Automation and Configuration)*

## 2. Key technical concepts

### 2.1 What NetQ is

*Analogy:* `nv show` is a security guard looking at one camera. NetQ is the control room with every camera recorded, so you can ask "show me every door that was open at 3 a.m." — across the whole building, and in the past.

**NetQ** is NVIDIA's network operations and telemetry platform for Cumulus Linux, NVIDIA SONiC, and Linux hosts. It continuously collects state (not just counters) from every device — interfaces, LLDP, BGP, EVPN, MLAG, routes, MAC/ARP tables, sensors, RoCE/PFC/ECN statistics, WJH drops — stores it as a time series, and lets you validate the *whole fabric* with a single command or UI card. It is the operational layer in the Spectrum-X stack (Module 4.1) and the primary troubleshooting tool for Ethernet in the exam's Troubleshooting domain.

Mental model for Cisco/Arista engineers: NetQ ≈ CloudVision/DNA Center assurance, but agent-based, CLI-first and fabric-wide validation oriented.

### 2.2 Components and deployment models

| Component | Role |
|---|---|
| **NetQ server** | Kubernetes-based appliance (single VM or 3-node HA cluster) running Kafka, Cassandra and the NetQ microservices; hosts the UI (HTTPS 443) and the API/CLI endpoint |
| **NetQ Cloud** | Same software hosted by NVIDIA; on-prem only runs the OPTA |
| **OPTA** (On-Premises Telemetry Aggregator) | A VM that receives agent data and forwards it to NetQ Cloud; used only in the cloud-hosted model |
| **NetQ agent** (`netq-agent`) | Runs on each switch/host; collects state via Linux/NVUE/FRR hooks and streams to the server on TCP **31980** (31443 for cloud/OPTA with TLS) |
| **NetQ CLI** (`netq` command) | Installed on the server and optionally on switches/hosts; queries the server's API |
| **NetQ UI** | Web workbench with cards, validation, topology, events |

Deployment models:
1. **On-premises, standalone** — one VM holds everything. Most common in labs and small/medium fabrics.
2. **On-premises, cluster** — three VMs (one master, two workers) for HA and larger scale.
3. **Cloud-hosted** — NVIDIA runs the backend; you deploy an OPTA VM (standalone or cluster) that agents connect to.

**Why the exam cares:** port numbers (31980 vs 31443 vs 443) and "which model has an OPTA" are reliable questions.

### 2.3 Sizing

Published requirements change by release; verify in the current install guide. Representative on-prem standalone VM: 16 vCPU, 64 GB RAM, 500 GB SSD (cluster nodes similar each). The OPTA is lighter (8 vCPU, 64 GB, 256 GB — again verify). NetQ scales to several hundred to low thousands of devices per server depending on telemetry rate; a Spectrum-X SuperPOD with a few hundred switches and GPU hosts with agents is squarely within one on-prem cluster.

Hypervisors: VMware ESXi (OVA) and KVM (qcow2). The VM needs a routable management-network address reachable from every switch's `eth0` (mgmt VRF) and outbound HTTPS if cloud or for LCM image downloads.

### 2.4 Agents: Fresh and Rotten

*Analogy:* an agent is a reporter phoning in from each switch. "Fresh" means they called recently; "Rotten" means the line went dead — and anything NetQ tells you about that switch is stale news.

Cumulus ships with the `netq-agent` and `netq-apps` (CLI) packages installed but pointed at nothing. `sudo netq config add server <ip> port 31980 vrf mgmt` points them at your server. Host agents (Ubuntu/RHEL GPU servers) report interfaces, LLDP (so NetQ builds host-to-leaf topology), routes/neighbours, and on NVIDIA NICs the RoCE counters. Hosts have no mgmt VRF, so omit `vrf`. On DGX/GPU nodes this is how NetQ correlates a slow NCCL job with a specific leaf port's PFC storm.

"Fresh" means recent heartbeat; "Rotten" means the agent has stopped reporting — the first thing to check before trusting any `netq check`.

### 2.5 Validation with `netq check`

A **validation** compares the collected state of *every* device against protocol expectations and against each other. Checks run on demand or on a schedule and feed the UI "Validation Summary" card. Output lists total nodes, checked, failed, warnings and the specific session/link that failed with a reason.

Typical exam usage: "after an EVPN change, which single command confirms every VTEP and VNI across the fabric agrees?" → `netq check evpn`. `netq check interfaces` catching an MTU mismatch between two LLDP neighbours is another favourite — a problem no single-switch `nv show` reveals. Because NetQ is time-series, `netq check bgp around 1h` validates the *past*.

### 2.6 RoCE telemetry and WJH

`netq show roce-config` reveals a leaf accidentally left in lossy mode; `netq check roce` automates that comparison across the fabric. `netq show roce-counters <host> <port>` gives per-port PFC pause rx/tx, ECN-marked packets, RoCE packets and drops, with `ingress|egress|pool` views.

**WJH** (What Just Happened) is the Spectrum ASIC's drop-reason telemetry (Module 4.1 and Module 8.x). The NetQ agent collects WJH records when enabled (`netq config add agent wjh`) and streams them so you can query fleet-wide. Drop categories: **L1** (link down/flaps, FEC errors), **L2** (MLAG/VLAN mismatch, STP, unknown VLAN), **Router** (blackhole, TTL, uRPF), **Tunnel** (VXLAN decap failures), **ACL**, **Buffer** (tail drop, WRED, PFC-WD), plus congestion/latency events. Each record includes the switch, port, drop reason, 5-tuple of the sample packet and a count. WJH answers *why* a packet died without mirroring or guesswork — it is a Spectrum differentiator the exam highlights.

### 2.7 Traces, topology and snapshots

`netq trace` computes the L2/L3 path through the fabric *from collected state* — it does not inject packets. It shows every ECMP path, hops, and flags issues (MTU mismatch, missing route, down link) along the way, and can trace a path as it was an hour ago. The UI topology view is built from LLDP data reported by agents (including hosts); `netq show lldp` is the CLI equivalent. A **snapshot** captures fabric state at a point in time; comparing two lists every difference. LCM takes them automatically when asked.

### 2.8 Lifecycle Management (LCM)

LCM lets NetQ upgrade Cumulus Linux images and NetQ agents, and (in newer releases) push NVUE configuration, across many switches from one place. Key behaviours: LCM backs up the configuration before upgrade and can restore on failure; it can take a NetQ snapshot before and after to prove nothing changed in BGP/EVPN/interfaces state; it upgrades sequentially or in configured groups to protect MLAG pairs. For NVUE configuration management, LCM can store NVUE YAML "configuration profiles" and apply them — a GUI alternative to Ansible (Module 6.1).

### 2.9 Notifications and thresholds

*Analogy:* a **channel** is a phone number (Slack, PagerDuty, syslog, email); a **rule** is a keyword you listen for; a **filter** wires a rule to a number. **TCAs** are separate smoke detectors — they trigger on a numeric threshold and ring a channel directly.

**Threshold-crossing events (TCA)** fire when a metric exceeds a value you set. TCA event IDs exist for interface rates/errors, sensors, ASIC resource utilization (hosts, routes, ACLs, MAC — ties to `cl-resource-query`), RoCE counters, WJH drop rates, and digital-optics (DOM) values. Exact IDs are listed in the Threshold-Crossing Events reference; the exam asks what categories exist and that the scope syntax is `hostname,interface` with wildcards.

### 2.10 UI workbench

The UI organises everything into *cards* on a *workbench*: Validation Summary, Network Health, Events, Inventory, Device/Interface cards, RoCE cards, WJH cards, Topology. Each card has a small/medium/large/full view; clicking into a validation card shows failed checks with drill-down to the device. You build custom workbenches per team (e.g., an "AI fabric" workbench showing RoCE validation + WJH buffer drops + PFC rate cards). The UI also exposes LCM, snapshot compare, and notification configuration graphically. The CLI is exam-primary.

## 3. Live-style walkthrough — install NetQ, add agents, validate, read RoCE

### Step 1 — Deploy and install the on-prem server

1. Download the NetQ platform image (`.ova`/`.qcow2`) and the installation bundle (`NetQ-<ver>.tgz`) from the NVIDIA Enterprise Support portal.
2. Deploy the VM, log in as `cumulus` (default password `cumulus`, forced change), set hostname/IP/DNS/NTP on `eth0` — 10.10.0.100 in our lab.
3. Copy the bundle to `/mnt/installables/` on the VM.
4. Run the installer:

```bash
# Older releases split bootstrap from install:
netq bootstrap master interface eth0 tarball /mnt/installables/netq-bootstrap-4.x.tgz
# Single-command install (4.x) — standalone, everything on this VM, agents arrive on eth0:
netq install standalone full interface eth0 bundle /mnt/installables/NetQ-4.x.tgz
# Cluster alternative:
# netq install cluster full interface eth0 bundle /mnt/installables/NetQ-4.x.tgz \
#    workers <worker1-ip> <worker2-ip> cluster-vip <vip>
# OPTA for cloud-hosted:
# netq install opta standalone full interface eth0 bundle /mnt/installables/NetQ-4.x-opta.tgz \
#    config-key <key-from-cloud-portal>
```

The installer bootstraps Kubernetes, loads container images and starts the services; it takes 30–60 minutes. Check:

```bash
netq show status [verbose]           # every pod/service "Running" / "Ready"
kubectl get pods -n default          # underlying k8s — all 1/1 or n/n Running, no CrashLoopBackOff
netq show version
netq show opta-health                # OPTA deployments only
```

Good looks like a status table with zero entries not in `Running`. Post-install: log in to `https://10.10.0.100/` as `admin`/`admin` (change it), add a premises name, and optionally install the CLI elsewhere with `netq config add cli server 10.10.0.100`.

Upgrading later keeps data:
```bash
netq upgrade bundle /mnt/installables/NetQ-4.y.tgz
netq show status
```

### Step 2 — Point switch agents at the server

On each Cumulus switch (ZTP normally does this — Module 4.3):

```bash
sudo netq config add server 10.10.0.100 port 31980 vrf mgmt     # on-prem server, reached via eth0 in mgmt VRF
# Cloud/OPTA would be: sudo netq config add server <opta-ip> port 31443 vrf mgmt
sudo netq config restart agent                                   # re-read config and reconnect
sudo netq config show                                            # confirm server, port, vrf
sudo netq config add agent cpu-limit 40                          # optional: cap agent CPU at 40%
sudo netq config add agent wjh                                   # enable WJH drop collection
sudo netq config add agent wjh-drop-filter drop-type l2 drop-reasons "Source MAC equals destination MAC"   # suppress noise
sudo netq config restart agent
```

To upgrade the agent to the server's version, add the NetQ apt repo and install (repository path and release codename vary; check the agent install page):
```bash
sudo bash -c 'echo "deb https://apps3.cumulusnetworks.com/repos/deb $(lsb_release -cs) netq-4.x" > /etc/apt/sources.list.d/netq.list'
sudo apt update && sudo apt install netq-agent netq-apps
```

SONiC: NVIDIA SONiC images include the agent as a container or Debian package depending on release; `sudo netq config add server <ip> port 31980` and `sudo netq config restart agent` behave the same. Linux hosts: install `netq-agent` from the NetQ repo and run the same command *without* `vrf`.

Now from the server:

```bash
netq show agents
```
```text
Matching agents records:
Hostname   Status   NTP Sync  Version     Sys Uptime  Agent Uptime  Reinitialize Time  Last Changed
---------- -------- --------- ----------- ----------- ------------- ------------------ -------------------
leaf01     Fresh    yes       4.x.y-cl5u  2d:4h       0:02:11       0:02:11            Mon Oct 19 09:31:02
leaf02     Fresh    yes       4.x.y-cl5u  2d:4h       0:01:58       0:01:58            Mon Oct 19 09:31:15
spine01    Rotten   yes       4.x.y-cl5u  2d:4h       1d:3h         1d:3h              Sun Oct 18 06:12:40
```

Stop. `spine01` is Rotten. Do not run a single `netq check` until that is fixed — the result would be built on day-old data. Nine times out of ten: `sudo netq config show` on spine01 reveals no `vrf mgmt`. Fix it, restart the agent, re-run `netq show agents rotten` until the list is empty.

### Step 3 — Run the validation suites

```bash
netq check agents                  # all Fresh?
netq check bgp                     # sessions established, address families, duplicate router-ids
```
```text
bgp check result summary:

Total nodes         : 4
Checked nodes       : 4
Failed nodes        : 1
Rotten nodes        : 0
Warning nodes       : 0

Additional summary:
Total Sessions      : 16
Failed Sessions     : 2

Session Establishment Test   : 0 warnings, 1 errors
Address Families Test        : passed
Router ID Test               : passed

Session Establishment Test details:
Hostname  VRF      Peer Name  Peer Hostname  Reason
--------  -------  ---------  -------------  ------------------------------------
leaf02    default  swp62      spine02        BGP session with peer spine02 (swp62 vrf default) failed, reason: Active
```

Read it like a report card: 1 failed node, 2 failed sessions (one session seen from each end), and the *exact* interface. That is already more than `nv show` on four switches would hand you.

```bash
netq check bgp include 0,1         # run only tests 0 and 1
netq check bgp hostnames leaf01,leaf02
netq check bgp around 1h           # was it broken an hour ago too?
netq check evpn                    # VNI consistency, VTEP reachability, type-2/5 presence, L3VNI mapping
netq check mlag                    # peer alive, backup IP, bond consistency, VLAN/MTU mismatch
netq check interfaces              # admin/oper mismatch, MTU mismatch between LLDP peers, speed/autoneg/FEC
netq check vlan                    # VLAN consistency across links
netq check vxlan
netq check mtu
netq check ntp
netq check sensors                 # PSU/fan/temp thresholds
netq check addresses
netq check roce                    # RoCE config consistency (lossless mode, PFC/ECN/trust uniform) — 4.x
```

Run them in that order after any fabric change; a clean `netq check interfaces` before `netq check bgp` saves you chasing an MTU problem as a BGP problem.

### Step 4 — Show commands and events

```bash
netq show interfaces type swp leaf01
netq leaf01 show interfaces swp1
netq show lldp                       # fabric-wide cabling — compare against the rail-optimized design
netq show bgp                        # all sessions fabric-wide
netq show evpn vni 10100
netq show macs 44:38:39:00:00:5e
netq show ip routes 10.100.1.0/24
netq show mlag
netq show sensors
netq show inventory brief            # platform/ASIC/OS per device
netq show events severity error between now and 24h    # the daily operator sweep
netq show events type roce
netq show events-config
```

Every `netq show` accepts `around <time>` to look back in time and `json` for machine output.

### Step 5 — RoCE telemetry

```bash
netq show roce-config                  # mode lossless/lossy, PFC priority, ECN thresholds, trust, per switch
```
```text
Hostname  RoCE Mode  Enabled TC  Mode      Priority  ECN Min   ECN Max   Trust  Last Changed
--------  ---------  ----------  --------  --------  --------  --------  -----  -------------------
leaf01    lossless   3           lossless  3         150000    1500000   l3     Mon Oct 19 08:02:11
leaf02    lossy      3           lossy     3         150000    1500000   l3     Mon Oct 19 08:05:40
spine01   lossless   3           lossless  3         150000    1500000   l3     Mon Oct 19 08:01:03
spine02   lossless   3           lossless  3         150000    1500000   l3     Mon Oct 19 08:01:09
```

Look at leaf02 — `lossy` in a lossless fabric. That single row explains a week of "job X is slow" tickets. `netq check roce` would have flagged it automatically.

```bash
netq leaf01 show roce-config
netq show roce-counters leaf01 swp1               # per-port: PFC pause rx/tx, ECN marked, RoCE pkts, drops
netq show roce-counters leaf01 swp1 ingress
netq show roce-counters leaf01 swp1 egress
netq show roce-counters leaf01 swp1 pool          # buffer pool usage
netq show roce-counters leaf01 swp1 around 30m    # what was it doing half an hour ago?
netq show roce-counters egress-queue leaf01 swp1  # per TC queue depth/drops (naming varies; verify)
netq check roce
```

Workflow for "job X is slow": `netq check roce` to confirm every switch has the same RoCE profile, then `netq show roce-counters` on the suspect leaf's host-facing ports for Tx pause (host slow) or uplinks for Rx pause (spine congestion), then correlate timestamps with `netq show events type roce` and WJH drops.

### Step 6 — WJH

```bash
netq show wjh-drop                                   # all drops fabric-wide, grouped by reason
netq show wjh-drop leaf01                            # one switch
netq show wjh-drop buffer between now and 10m        # congestion/tail-drop/PFC-WD drops in last 10 minutes
```
```text
Matching wjh records:
Hostname  Ingress Port  Reason                  Severity  Agg Count  Src Ip        Dst Ip        Proto  Dst Port  First Timestamp      Last Timestamp
--------  ------------  ----------------------  --------  ---------  ------------  ------------  -----  --------  -------------------  -------------------
leaf01    swp12         Tail drop               Error     1204       10.1.3.7      10.1.3.22     udp    4791      Mon Oct 19 09:40:02  Mon Oct 19 09:40:09
```

UDP 4791 is RoCEv2. 1,204 tail drops in seven seconds on a host-facing port — that is the incast event the job felt. Other categories:
```bash
netq show wjh-drop l1 | l2 | router | tunnel | acl
netq show wjh-drop router                            # e.g. TTL expired, blackhole route, non-routable
```

### Step 7 — Trace and snapshot

```bash
netq trace 10.100.1.10 from 10.200.1.20 vrf tenantA pretty     # computed from collected state, every ECMP path
netq trace 10.100.1.10 from 10.200.1.20 around 1h              # the path as it was an hour ago
netq trace 44:38:39:00:00:5e vlan 100 from leaf01              # L2 trace
```

In the UI, Snapshot → Create before a change, Create after, Compare: every interface, BGP, EVPN, MLAG, route and MAC difference is listed.

### Step 8 — LCM

```bash
netq lcm add credentials username cumulus password <pw>           # SSH creds used for all switches
netq lcm add cl-image /path/cumulus-linux-5.11.0-mlx-amd64.bin    # upload image to NetQ
netq lcm show cl-images
netq lcm show switches                                             # discovered switches with CL/agent versions
netq lcm upgrade cl-image name Upgrade-Leafs cl-version 5.11.0 hostnames leaf01,leaf02 \
   order leaf01,leaf02 run-restore-on-failure run-snapshot-before-after
netq lcm show upgrade-jobs cl-image
netq lcm upgrade netq-image name AgentUp hostnames leaf01,leaf02 to-version 4.x.y
```

`order leaf01,leaf02` keeps an MLAG pair from upgrading simultaneously; `run-snapshot-before-after` is your proof the upgrade changed nothing operationally.

### Step 9 — Notifications and TCAs

```bash
# Channels
netq add notification channel slack slack-noc webhook https://hooks.slack.com/services/XXX/YYY/ZZZ
netq add notification channel pagerduty pd-oncall integration-key <key>
netq add notification channel syslog syslog-siem host 10.10.0.20 port 514 severity error
netq add notification channel email noc-email to noc@example.com
# Rules match event fields
netq add notification rule bgp-down key message value "BGP session.*down"
netq add notification rule leaf-only key hostname value leaf*
# Filters tie rules to channels
netq add notification filter bgp-to-slack rule bgp-down channel slack-noc severity error
netq show notification channel
netq show notification rule
netq show notification filter

# Threshold-crossing events
netq add tca event_id TCA_RXBYTES_UPPER scope 'leaf*,swp1*' threshold 1200000000 channel slack-noc
netq add tca event_id TCA_SENSOR_TEMPERATURE_UPPER scope '*' threshold 70 channel pd-oncall
netq add tca event_id TCA_TCAM_IPV4_ROUTE_UPPER scope '*' threshold 90                 # ASIC table utilization % — ties to cl-resource-query
netq add tca event_id TCA_RoCE_PFC_RX_PAUSE_UPPER scope 'leaf*,swp*' threshold 1000    # verify event_id names
netq show tca
```

## 4. Troubleshooting / common gotchas

### Gotcha 1 — Every agent is Rotten

**Symptom:** `netq show agents` shows Rotten for all Cumulus switches; `ip vrf exec mgmt ping 10.10.0.100` works.

**Why it happens:** the agent was configured without `vrf mgmt`, so it tries to reach the server from the default VRF.

**How to debug:**
```bash
sudo netq config show                 # look for vrf: mgmt
systemctl status netq-agent
sudo journalctl -u netq-agent -n 50   # connection refused / unreachable
```

**Fix:** `sudo netq config add server 10.10.0.100 port 31980 vrf mgmt && sudo netq config restart agent`.

### Gotcha 2 — `netq check bgp` passes but the fabric is broken

**Symptom:** validation green; users report an outage.

**Why it happens:** one or more agents are Rotten, so the check validated stale state. Or you ran the check immediately after a change and the agent had not reported yet.

**How to debug:** `netq show agents rotten`; `netq check agents`; look at `Rotten nodes` in the check summary.

**Fix:** repair agents first; re-run checks; use `around` to compare with a known-good time.

### Gotcha 3 — No WJH records although drops are happening

**Symptom:** `netq show wjh-drop` is empty while `nv show interface swpN counters` shows drops.

**Why it happens:** WJH collection is off by default on the agent, or a drop-filter suppressed the reason.

**How to debug:** `sudo netq config show` → is `wjh` enabled? `netq show events-config`.

**Fix:** `sudo netq config add agent wjh && sudo netq config restart agent`; review `wjh-drop-filter` entries.

### Gotcha 4 — Slack channel configured, nothing arrives

**Symptom:** events appear in `netq show events`, Slack is silent.

**Why it happens:** a channel alone does nothing; it needs a rule and a filter binding them (or a TCA bound to the channel).

**How to debug:** `netq show notification filter` — empty.

**Fix:** add a rule and a filter: `netq add notification rule ... ; netq add notification filter ... rule <r> channel <c>`.

## 5. Hands-on assignment — alert rules and a validation runbook

**Scenario.** The AI fabric goes into production next week. Operations needs (a) alerting that pages on the things that actually hurt GPU jobs and (b) a repeatable runbook that proves the fabric is healthy after every change.

**Requirements**
1. Agents on `leaf01`, `leaf02`, `spine01`, `spine02` and GPU host `dgx01` all Fresh; WJH enabled on the switches.
2. Channels: Slack `slack-noc`, PagerDuty `pd-oncall`, syslog `syslog-siem` (10.10.0.20:514, severity error).
3. Rules + filters: BGP session down → Slack (error); any `leaf*` event of severity error → syslog.
4. TCAs: ASIC IPv4 route utilization > 90% → PagerDuty; sensor temperature > 70 °C → PagerDuty; RoCE PFC Rx pause on `leaf*,swp*` above a threshold you justify → Slack; WJH buffer-drop rate → Slack (verify the event_id names in the TCA reference).
5. A runbook script `fabric-validate.sh` that runs `netq check agents|interfaces|mtu|bgp|evpn|roce` in that order, prints each summary, and exits non-zero if any check reports failed nodes.
6. Evidence that the runbook catches a fault: deliberately set `nv set qos roce mode lossy` on leaf02, run the runbook, show the failure, revert, show the pass.

**Deliverables**
- `netq show notification channel|rule|filter` and `netq show tca` output.
- `fabric-validate.sh` and its output in the failing and passing runs.
- `netq show roce-config` before and after the fault.
- A one-page explanation of why each TCA threshold was chosen.

**Acceptance criteria**
- `netq show agents` lists five Fresh agents including `dgx01`.
- Breaking a BGP session (`nv set interface swp61 link state down` on leaf01) produces a Slack message within two minutes; restoring it clears.
- `netq show tca` lists at least four TCAs bound to channels.
- The runbook exits 0 on a healthy fabric and non-zero when leaf02 is lossy, with `netq check roce` identifying leaf02.
- `netq show wjh-drop` returns at least one record after a deliberate ACL drop test.

### Hint
- The host agent does not take `vrf`; the switch agents must.
- A channel needs a rule *and* a filter before anything is sent. Test with a link flap, not a reboot.
- `netq check <x> json` gives you `summary.failedNodeCount` to parse in the script — far more reliable than grepping text.
- `netq check roce` is the check that spots a profile mismatch; `netq show roce-config` shows you *which* switch.
- For WJH evidence, an ACL deny on a host-facing port and a ping from the host produces an `acl`-category drop immediately.

## Appendix A — Quick command / config reference

```bash
# Server
netq install standalone full interface eth0 bundle /mnt/installables/NetQ-<ver>.tgz
netq install cluster full interface eth0 bundle <tgz> workers <ip> <ip> cluster-vip <vip>
netq install opta standalone full interface eth0 bundle <tgz> config-key <key>
netq show status [verbose]; netq show version; netq upgrade bundle <tgz>; netq show opta-health

# Agent (switch/host)
sudo netq config add server <ip> port 31980 vrf mgmt     # 31443 for cloud/OPTA; no vrf on hosts
sudo netq config restart agent; sudo netq config show
sudo netq config add agent wjh; sudo netq config add agent wjh-drop-filter ...; sudo netq config add agent cpu-limit 40
netq show agents [rotten]

# LCM
netq lcm add credentials username cumulus password <pw>
netq lcm add cl-image <file>; netq lcm show cl-images; netq lcm show switches
netq lcm upgrade cl-image name <job> cl-version <ver> hostnames <h1,h2> [order ...] [run-restore-on-failure] [run-snapshot-before-after]
netq lcm upgrade netq-image name <job> hostnames <h1,h2> to-version <ver>
netq lcm show upgrade-jobs cl-image

# Validate / show
netq check bgp|evpn|mlag|interfaces|vlan|mtu|vxlan|ntp|sensors|agents|roce|addresses [hostnames ...] [around 1h] [include 0,1] [json]
netq show interfaces|bgp|evpn|mlag|lldp|macs|ip routes|events|inventory|sensors [around <t>] [json]
netq show roce-config; netq show roce-counters <host> <port> [ingress|egress|pool]
netq show wjh-drop [l1|l2|router|tunnel|acl|buffer] [between now and 10m]
netq trace <dst> from <src> [vrf <v>] [around <t>] [pretty]

# Notifications
netq add notification channel slack|pagerduty|syslog|email <name> ...
netq add notification rule <name> key <field> value <regex>
netq add notification filter <name> rule <rule> channel <channel> [severity ...]
netq add tca event_id <ID> scope '<host>,<iface>' threshold <n> channel <channel>
netq show notification channel|rule|filter; netq show tca
```

## Appendix B — Exam traps & key takeaways

- Agents on Cumulus must be told the server *and* the VRF: `netq config add server <ip> port 31980 vrf mgmt`. Omitting `vrf mgmt` is the #1 reason agents show Rotten.
- Port 31980 = on-prem server; 31443 = cloud/OPTA (TLS). 443 is the UI. 8765 is NVUE's API, not NetQ.
- `netq check` validates *collected* state across the fabric; `nv show` is one switch's live state. A Rotten agent makes `netq check` unreliable — check `netq show agents` first.
- `netq trace` is state-based path computation, not an active probe like `traceroute`; it works with `around` to trace the past.
- OPTA exists only in the cloud-hosted model; on-prem servers do not use an OPTA.
- LCM needs SSH credentials for the switches and the image uploaded to NetQ first; it can snapshot before/after and restore on failure.
- WJH collection must be enabled on the agent (`netq config add agent wjh`); drops are *categorised* (buffer, router, L2, L1, tunnel, ACL) — PFC-WD and tail drops are **buffer** category.
- Notifications are channel → rule → filter; TCAs are separate threshold events bound directly to a channel; scope syntax is `hostname,interface` with wildcards.
- Snapshots compare *state*; `nv config diff` compares *configuration*.
- `netq show roce-config` reveals a leaf accidentally left in lossy mode; `netq check roce` automates that comparison.
- NetQ server = Kubernetes appliance (standalone VM or 3-node cluster) with Kafka/Cassandra; install with `netq install standalone|cluster|opta full interface eth0 bundle <tgz>`; verify with `netq show status`.

## Appendix C — Knowledge check

1. Agents on all leaves show as Rotten in `netq show agents`. Switch `eth0` is in the mgmt VRF and the server IP is reachable from `ip vrf exec mgmt ping`. Most likely fix?
   A. Open port 443 on the switches
   B. Re-run `netq config add server <ip> port 31980 vrf mgmt` and restart the agent
   C. Install an OPTA
   D. Run `netq check agents`

2. After a fabric-wide EVPN change, which command verifies that every VTEP and VNI is consistent across all switches?
   A. `nv show evpn` on each switch
   B. `netq check evpn`
   C. `netq trace`
   D. `netq show lldp`

3. Which port do NetQ agents use to reach an on-premises NetQ server?
   A. 443
   B. 8765
   C. 31980
   D. 31443

4. An operator wants to know why packets to a tenant subnet were dropped on a leaf five minutes ago, with the exact drop reason. Which command?
   A. `netq show interfaces counters`
   B. `netq show wjh-drop router between now and 5m`
   C. `netq check interfaces`
   D. `nv show interface counters`

5. Which deployment includes an OPTA?
   A. On-premises standalone
   B. On-premises cluster
   C. Cloud-hosted NetQ
   D. All of the above

6. What does `netq trace 10.1.1.10 from 10.2.2.20 around 1h` do?
   A. Sends ICMP probes and records hops
   B. Computes the forwarding path from NetQ's stored state as it was an hour ago
   C. Configures a mirror session
   D. Validates BGP sessions

7. Which NetQ feature upgrades Cumulus Linux on a group of switches with automatic configuration backup and optional before/after state comparison?
   A. ZTP
   B. Lifecycle Management (LCM)
   C. TCA
   D. OPTA

8. To be paged when any leaf's ASIC IPv4 route table exceeds 90% utilization, you would configure a:
   A. Notification rule on syslog
   B. Threshold-crossing event (TCA) for the TCAM route resource bound to a PagerDuty channel
   C. `netq check bgp` schedule
   D. WJH drop filter

### Answer key
1. B — The agent must reference the mgmt VRF to reach the server over eth0.
2. B — `netq check evpn` validates EVPN consistency fabric-wide.
3. C — 31980 for on-prem; 31443 for cloud/OPTA.
4. B — WJH router-category drops include the reason and time window.
5. C — OPTA aggregates on-prem agents for the cloud-hosted backend.
6. B — `netq trace` is state-based and time-travel capable.
7. B — LCM handles image/agent upgrades with backup, restore and snapshots.
8. B — TCAs raise events on ASIC resource thresholds and route them to a channel.

## References
- NVIDIA NetQ User Guide — https://docs.nvidia.com/networking-ethernet-software/cumulus-netq/
- NetQ: Install NetQ (on-premises, cloud, OPTA) — https://docs.nvidia.com/networking-ethernet-software/cumulus-netq/Installation-Management/Install-NetQ/
- NetQ: Lifecycle Management — https://docs.nvidia.com/networking-ethernet-software/cumulus-netq/Lifecycle-Management/
- NetQ: Validation Checks; Monitor RoCE; What Just Happened; Threshold-Crossing Events Reference
- NetQ CLI Reference — https://docs.nvidia.com/networking-ethernet-software/cumulus-netq/More-Documents/NetQ-CLI-Reference-Manual/
- NVIDIA Academy course: NetQ Deployment and Installation
- Related: [`01-spectrum-x.md`](01-spectrum-x.md), [`03-cumulus-linux-administration.md`](03-cumulus-linux-administration.md), [`../08-troubleshooting/`](../08-troubleshooting/), [`../06-automation/01-nvue-and-ansible-automation.md`](../06-automation/01-nvue-and-ansible-automation.md)

---
## Solution guide — Hands-on assignment

### Requirement 1 — agents

On each switch:
```bash
sudo netq config add server 10.10.0.100 port 31980 vrf mgmt
sudo netq config add agent wjh
sudo netq config restart agent
```
On `dgx01` (Ubuntu):
```bash
sudo apt install netq-agent          # from the NetQ repo added as in §3 Step 2
sudo netq config add server 10.10.0.100 port 31980      # no vrf on a host
sudo netq config restart agent
```
Verify on the server: `netq show agents` → five rows, all `Fresh`. Criterion met when `netq show agents rotten` prints nothing.

### Requirement 2 — channels

```bash
netq add notification channel slack slack-noc webhook https://hooks.slack.com/services/XXX/YYY/ZZZ
netq add notification channel pagerduty pd-oncall integration-key 0123456789abcdef
netq add notification channel syslog syslog-siem host 10.10.0.20 port 514 severity error
netq show notification channel
```

### Requirement 3 — rules and filters

```bash
netq add notification rule bgp-down key message value "BGP session.*down"
netq add notification rule leaf-only key hostname value leaf*
netq add notification filter bgp-to-slack rule bgp-down channel slack-noc severity error
netq add notification filter leaf-errors-to-siem rule leaf-only channel syslog-siem severity error
netq show notification rule
netq show notification filter
```

Test: on leaf01 `nv set interface swp61 link state down && nv config apply -y`. Within the agent interval a "BGP session with peer spine01 ... down" event appears in `netq show events type bgp` and in Slack. `nv unset interface swp61 link state && nv config apply -y` restores it; the follow-up "up" event is informational and does not match severity error, so Slack goes quiet — correct behaviour.

### Requirement 4 — TCAs

```bash
netq add tca event_id TCA_TCAM_IPV4_ROUTE_UPPER scope '*' threshold 90 channel pd-oncall
netq add tca event_id TCA_SENSOR_TEMPERATURE_UPPER scope '*' threshold 70 channel pd-oncall
netq add tca event_id TCA_RoCE_PFC_RX_PAUSE_UPPER scope 'leaf*,swp*' threshold 1000 channel slack-noc      # verify exact event_id
netq add tca event_id TCA_WJH_BUFFER_DROP_AGG_UPPER scope 'leaf*,swp*' threshold 100 channel slack-noc     # verify exact event_id
netq show tca
```

Threshold justification (the one-pager):
- **Route TCAM 90%** — `cl-resource-query` shows silent software forwarding at 100%; 90% leaves headroom for one more tenant VRF while a human responds. Pages because the failure mode is silent.
- **Temperature 70 °C** — comfortably below Spectrum-4 thermal throttling and matches the warning threshold `smonctl` uses on most SN platforms (verify per platform). Pages because sustained heat leads to a hardware shutdown.
- **PFC Rx pause 1000/interval on leaf uplinks** — a few hundred pauses per interval is normal incast damping; sustained thousands means a spine is persistently pausing a leaf and GPU step time is already stretched. Slack, not page: it degrades, it does not break.
- **WJH buffer drops 100/interval** — in a lossless RoCE fabric, *any* buffer drop on a RoCE queue is abnormal; 100 filters out the occasional best-effort tail drop while catching a PFC-WD event. Slack for triage.

### Requirement 5 — runbook

```bash
#!/usr/bin/env bash
# fabric-validate.sh — run the NetQ validation suite in dependency order; exit non-zero on any failed node.
set -u
CHECKS=(agents interfaces mtu bgp evpn roce)
rc=0
for c in "${CHECKS[@]}"; do
  out=$(netq check "$c" json 2>/dev/null)
  failed=$(echo "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("summary",{}).get("failedNodeCount",0))' 2>/dev/null || echo 1)
  rotten=$(echo "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("summary",{}).get("rottenNodeCount",0))' 2>/dev/null || echo 0)
  printf '%-12s failed=%s rotten=%s\n' "$c" "$failed" "$rotten"
  if [ "$failed" != "0" ] || [ "$rotten" != "0" ]; then
    rc=1
    netq check "$c" | sed -n '/details/,$p'      # human-readable detail for the failing check
  fi
done
exit $rc
```

`agents` runs first: if anything is Rotten, every later result is suspect and the script fails fast. `interfaces` and `mtu` precede `bgp` so an MTU mismatch is reported as what it is.

### Requirement 6 — prove it catches a fault

```bash
# Healthy baseline
./fabric-validate.sh; echo "exit=$?"
# agents       failed=0 rotten=0
# interfaces   failed=0 rotten=0
# mtu          failed=0 rotten=0
# bgp          failed=0 rotten=0
# evpn         failed=0 rotten=0
# roce         failed=0 rotten=0
# exit=0
netq show roce-config                       # all four: lossless

# Inject the fault on leaf02
nv set qos roce mode lossy && nv config apply -y
sleep 90                                    # let the agent report

./fabric-validate.sh; echo "exit=$?"
# ...
# roce         failed=1 rotten=0
# RoCE Config Consistency Test details:
# Hostname  Reason
# leaf02    RoCE mode lossy differs from fabric majority lossless
# exit=1
netq show roce-config                       # leaf02 row reads lossy

# Revert
nv set qos roce mode lossless && nv config apply -y && nv config save
sleep 90
./fabric-validate.sh; echo "exit=$?"        # exit=0
```

### WJH evidence

On leaf01:
```bash
nv set acl drop-test type ipv4
nv set acl drop-test rule 10 match ip source-ip 10.100.1.10/32
nv set acl drop-test rule 10 action deny
nv set interface swp1s0 acl drop-test inbound
nv config apply -y
```
From the host behind swp1s0: `ping -c 20 10.100.1.1`. On the NetQ server:
```bash
netq show wjh-drop acl between now and 5m
# leaf01  swp1s0  Ingress port ACL  Error  20  10.100.1.10  10.100.1.1  icmp ...
```
Then `nv unset acl drop-test; nv unset interface swp1s0 acl drop-test; nv config apply -y`.

### Deliverable checklist

| Deliverable | Source |
|---|---|
| Channel/rule/filter/TCA listings | `netq show notification channel`, `... rule`, `... filter`, `netq show tca` |
| Runbook + two runs | `fabric-validate.sh`, terminal captures with exit codes |
| RoCE config before/after | `netq show roce-config` ×2 |
| Threshold rationale | the four bullets above |
