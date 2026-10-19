# Module 4.5 — NetQ Deployment and Installation

> **Exam domain:** NVIDIA Spectrum Networking (30%) — also supports Troubleshooting Tools (20%)  
> **Source course(s):** NetQ Deployment and Installation (NVIDIA Academy)  
> **Source docs:** NVIDIA NetQ User Guide (4.x) — Installation Management, Lifecycle Management, Validation Checks, Monitor Operations, Events and Notifications; NetQ CLI Reference  
> **Estimated study time:** 4–5 hours

## Learning objectives
- Describe the NetQ architecture: server (on-premises VM or cloud-hosted), agents, OPTA, and data flow.
- Size and install an on-premises NetQ server VM with `netq install`/`netq bootstrap`.
- Install and configure the NetQ agent on Cumulus Linux, SONiC and Linux hosts with `netq config add server`.
- Perform validation checks (`netq check bgp|evpn|mlag|interfaces|roce`) and interpret results.
- Use NetQ for RoCE telemetry (`netq show roce-counters`, `netq show roce-config`) and **WJH** drop analysis (`netq show wjh-drop`).
- Run traces (`netq trace`), inspect topology and events, and configure threshold-based notifications to Slack/PagerDuty/syslog.
- Describe Lifecycle Management (LCM) for switch image and NetQ agent upgrades.

## 1. What NetQ is

**NetQ** is NVIDIA's network operations and telemetry platform for Cumulus Linux, NVIDIA SONiC, and Linux hosts. It continuously collects state (not just counters) from every device — interfaces, LLDP, BGP, EVPN, MLAG, routes, MAC/ARP tables, sensors, RoCE/PFC/ECN statistics, WJH drops — stores it as a time series, and lets you validate the *whole fabric* with a single command or UI card. It is the operational layer in the Spectrum-X stack (Module 4.1) and the primary troubleshooting tool for Ethernet in the exam's Troubleshooting domain.

Mental model for Cisco/Arista engineers: NetQ ≈ CloudVision/DNA Center assurance, but agent-based, CLI-first and fabric-wide validation oriented.

### 1.1 Components
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

### 1.2 Sizing
Published requirements change by release; verify in the current install guide. Representative on-prem standalone VM: 16 vCPU, 64 GB RAM, 500 GB SSD (cluster nodes similar each). The OPTA is lighter (8 vCPU, 64 GB, 256 GB — again verify). NetQ scales to several hundred to low thousands of devices per server depending on telemetry rate; a Spectrum-X SuperPOD with a few hundred switches and GPU hosts with agents is squarely within one on-prem cluster.

Hypervisors: VMware ESXi (OVA) and KVM (qcow2). The VM needs a routable management-network address reachable from every switch's `eth0` (mgmt VRF) and outbound HTTPS if cloud or for LCM image downloads.

## 2. Installing the on-premises server

Steps (standalone on-prem):
1. Download the NetQ platform image (`.ova`/`.qcow2`) and the installation bundle (`NetQ-<ver>.tgz`) from the NVIDIA Enterprise Support portal.
2. Deploy the VM, log in as `cumulus` (default password `cumulus`, forced change), set hostname/IP/DNS/NTP on `eth0`.
3. Copy the bundle to `/mnt/installables/` on the VM.
4. Run the installer:

```bash
# Verify prerequisites
netq bootstrap master interface eth0 tarball /mnt/installables/netq-bootstrap-4.x.tgz   # older releases split bootstrap from install
# Single-command install (4.x):
netq install standalone full interface eth0 bundle /mnt/installables/NetQ-4.x.tgz
# Cluster:
netq install cluster full interface eth0 bundle /mnt/installables/NetQ-4.x.tgz \
   workers <worker1-ip> <worker2-ip> cluster-vip <vip>
# OPTA for cloud:
netq install opta standalone full interface eth0 bundle /mnt/installables/NetQ-4.x-opta.tgz \
   config-key <key-from-cloud-portal>
```
The installer bootstraps Kubernetes, loads container images and starts the services; it takes 30–60 minutes. Check:
```bash
netq show status [verbose]           # all pods/services running
netq show opta-health                # for OPTA
kubectl get pods -n default          # underlying k8s
netq show version
```
Post-install: log in to `https://<server>/` as `admin`/`admin` (change it), add a premises name, and optionally install the CLI on other machines with `netq config add cli server <ip>`.

### 2.1 Upgrading the server
```bash
netq upgrade bundle /mnt/installables/NetQ-4.y.tgz
netq show status
```
Server upgrades keep data. Agent versions must stay within the server's supported range — LCM (Section 4) upgrades agents fleet-wide.

## 3. Installing agents

### 3.1 Cumulus Linux
Cumulus ships with the `netq-agent` and `netq-apps` (CLI) packages installed but pointed at nothing. Point them at your server:
```bash
sudo netq config add server 10.10.0.100 port 31980 vrf mgmt     # on-prem server
# Cloud/OPTA:
# sudo netq config add server <opta-ip> port 31443 vrf mgmt
sudo netq config restart agent
sudo netq config show
sudo netq config add agent cpu-limit 40                          # optional: cap agent CPU
```
To upgrade agents to the server's version, add the NetQ apt repo and install:
```bash
sudo bash -c 'echo "deb https://apps3.cumulusnetworks.com/repos/deb $(lsb_release -cs) netq-4.x" > /etc/apt/sources.list.d/netq.list'
sudo apt update && sudo apt install netq-agent netq-apps
```
(Repository path and release codename vary; check the agent install page.) In practice, ZTP scripts add the server, and LCM upgrades agents afterwards.

Verify from the server:
```bash
netq show agents                     # every agent, version, status Fresh/Rotten, last heard
netq show agents rotten              # agents the server has not heard from
netq leaf01 show agent               # one device
```
"Fresh" means recent heartbeat; "Rotten" means the agent has stopped reporting — the first thing to check before trusting any `netq check`.

### 3.2 SONiC
NVIDIA SONiC images include the agent as a container or Debian package depending on release: `sudo netq config add server <ip> port 31980` and `sudo netq config restart agent` behave the same. Check the SONiC agent page for the install package.

### 3.3 Linux hosts (GPU servers)
Install `netq-agent` from the NetQ repo on Ubuntu/RHEL hosts, then run the same `netq config add server` command. Host agents report interfaces, LLDP (so NetQ builds host-to-leaf topology), routes/neighbours, and on NVIDIA NICs the RoCE counters. Hosts have no mgmt VRF, so omit `vrf`. On DGX/GPU nodes this is how NetQ correlates a slow NCCL job with a specific leaf port's PFC storm.

## 4. Lifecycle Management (LCM)

LCM lets NetQ upgrade Cumulus Linux images and NetQ agents, and (in newer releases) push NVUE configuration, across many switches from one place.

Workflow (UI: Manage Switches / LCM; CLI shown):
```bash
netq lcm add credentials username cumulus password <pw>           # SSH creds used for all switches
netq lcm add cl-image /path/cumulus-linux-5.11.0-mlx-amd64.bin    # upload image to NetQ
netq lcm show cl-images
netq lcm show switches                                             # discovered switches with CL/agent versions
netq lcm upgrade cl-image name Upgrade-Leafs cl-version 5.11.0 hostnames leaf01,leaf02 \
   [order leaf01,leaf02] [run-restore-on-failure] [run-snapshot-before-after]
netq lcm show upgrade-jobs cl-image
netq lcm upgrade netq-image name AgentUp hostnames leaf01,leaf02 to-version 4.x.y
```
Key behaviours to know: LCM backs up the configuration before upgrade and can restore on failure; it can take a NetQ **snapshot** before and after (Section 9) to prove nothing changed in BGP/EVPN/interfaces state; it upgrades sequentially or in configured groups to protect MLAG pairs. For NVUE configuration management, LCM can store NVUE YAML "configuration profiles" and apply them to switches — a GUI alternative to Ansible (Module 6.1).

## 5. Validation with `netq check`

A **validation** compares the collected state of *every* device against protocol expectations and against each other. Checks run on demand or on a schedule and feed the UI "Validation Summary" card.

```bash
netq check bgp                     # sessions established, address families, duplicate router-ids
netq check bgp include 0,1         # run only test 0 and 1
netq check evpn                    # VNI consistency, VTEP reachability, type-2/5 presence, L3VNI mapping
netq check mlag                    # peer alive, backup IP, bond consistency, VLAN/MTU mismatch
netq check interfaces              # admin/oper mismatch, MTU mismatch between LLDP peers, speed/autoneg/FEC
netq check vlan                    # VLAN consistency across links
netq check vxlan
netq check mtu
netq check ntp
netq check sensors                 # PSU/fan/temp thresholds
netq check agents
netq check roce                    # RoCE config consistency (lossless mode, PFC/ECN/trust uniform) — 4.x
netq check addresses
```
Output lists total nodes, checked, failed, warnings and the specific session/link that failed with a reason. Typical exam usage: "after an EVPN change, which single command confirms every VTEP and VNI across the fabric agrees?" → `netq check evpn`. `netq check interfaces` catching an MTU mismatch between two LLDP neighbours is another favourite — a problem no single-switch `nv show` reveals.

Filters: `netq check bgp hostnames leaf01,leaf02`, `netq check bgp around 1h` (state as of an hour ago — NetQ is time-series, so you can validate the *past*).

## 6. Show commands and events

```bash
netq show interfaces [type bond|swp|vlan] [hostname]
netq leaf01 show interfaces swp1
netq show lldp
netq show bgp                        # all sessions fabric-wide
netq leaf01 show bgp
netq show evpn vni 10100
netq show macs 44:38:39:00:00:5e
netq show ip routes 10.100.1.0/24
netq show mlag
netq show sensors
netq show inventory brief            # platform/ASIC/OS per device
netq show events [type bgp|interfaces|roce|...] [severity error] [between now and 2h]
netq show events-config
```
Every `netq show` accepts `around <time>` to look back in time and `json` for machine output. **Events** are generated when state changes or thresholds are crossed; `netq show events severity error between now and 24h` is the daily operator sweep.

## 7. RoCE telemetry

For AI fabrics the RoCE views are the reason NetQ is in the exam:
```bash
netq show roce-config [hostname]                  # mode lossless/lossy, PFC priority, ECN thresholds, trust, per switch
netq leaf01 show roce-config
netq show roce-counters leaf01 swp1               # per-port: PFC pause rx/tx, ECN marked, RoCE pkts, drops
netq show roce-counters leaf01 swp1 ingress|egress
netq show roce-counters leaf01 swp1 pool          # buffer pool usage
netq show roce-counters leaf01 swp1 around 30m
netq show roce-counters egress-queue leaf01 swp1  # per TC queue depth/drops (naming varies; verify)
netq check roce
```
Workflow for "job X is slow": `netq check roce` to confirm every switch has the same RoCE profile (a single leaf left in lossy mode is a classic cause), then `netq show roce-counters` on the suspect leaf's host-facing ports for Tx pause (host slow) or uplinks for Rx pause (spine congestion), then correlate timestamps with `netq show events type roce` and WJH drops.

## 8. What Just Happened (WJH)

**WJH** is the Spectrum ASIC's drop-reason telemetry (Module 4.1 and Module 8.x). The NetQ agent collects WJH records when enabled and streams them so you can query fleet-wide.

Enable on the switch:
```bash
sudo netq config add agent wjh                            # enable WJH collection
sudo netq config add agent wjh-threshold latency tc 3 ...  # optional latency/congestion thresholds (verify syntax)
sudo netq config add agent wjh-drop-filter drop-type l2 drop-reasons "Source MAC equals destination MAC" # suppress noise
sudo netq config restart agent
```
Query:
```bash
netq show wjh-drop                                   # all drops fabric-wide, grouped by reason
netq show wjh-drop leaf01                            # one switch
netq show wjh-drop l1 | l2 | router | tunnel | acl | buffer
netq show wjh-drop buffer between now and 10m        # congestion/tail-drop/PFC-WD drops in last 10 minutes
netq show wjh-drop router                            # e.g. TTL expired, blackhole route, non-routable
```
Drop categories: **L1** (link down/flaps, FEC errors), **L2** (MLAG/VLAN mismatch, STP, unknown VLAN), **Router** (blackhole, TTL, uRPF), **Tunnel** (VXLAN decap failures), **ACL**, **Buffer** (tail drop, WRED, PFC-WD), plus congestion/latency events. Each record includes the switch, port, drop reason, 5-tuple of the sample packet and a count. WJH answers *why* a packet died without mirroring or guesswork — it is a Spectrum differentiator the exam highlights.

## 9. Traces, topology and snapshots

### 9.1 Trace
```bash
netq trace 10.100.1.10 from 10.200.1.20 [vrf tenantA] [around 1h] [pretty] [debug]
netq trace 44:38:39:00:00:5e vlan 100 from leaf01
```
`netq trace` computes the L2/L3 path through the fabric *from collected state* — it does not inject packets. It shows every ECMP path, hops, and flags issues (MTU mismatch, missing route, down link) along the way. Because it uses stored state it can trace a path as it was an hour ago.

### 9.2 Topology
The UI topology view is built from LLDP data reported by agents (including hosts). Use it to confirm the rail-optimized cabling matches the design; `netq show lldp` is the CLI equivalent.

### 9.3 Snapshots
A **snapshot** captures the fabric state (interfaces, BGP, EVPN, MLAG, routes, MACs…) at a point in time. Comparing two snapshots (before/after a change or upgrade) lists every difference. LCM takes them automatically when asked; in the UI, Snapshot → Create / Compare.

## 10. Notifications and thresholds

NetQ events can be routed to external systems through *channels*, *rules* and *filters*:
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
netq show notification channel | rule | filter
```
**Threshold-crossing events (TCA)** fire when a metric exceeds a value you set:
```bash
netq add tca event_id TCA_RXBYTES_UPPER scope 'leaf*,swp1*' threshold 1200000000 channel slack-noc
netq add tca event_id TCA_SENSOR_TEMPERATURE_UPPER scope '*' threshold 70 channel pd-oncall
netq add tca event_id TCA_TCAM_IPV4_ROUTE_UPPER scope '*' threshold 90     # ASIC table utilization % — ties to cl-resource-query
netq add tca event_id TCA_RoCE_PFC_RX_PAUSE_UPPER scope 'leaf*,swp*' threshold 1000   # verify event_id names
netq show tca
```
TCA event IDs exist for interface rates/errors, sensors, ASIC resource utilization (hosts, routes, ACLs, MAC), RoCE counters, WJH drop rates, and digital-optics (DOM) values. Exact IDs are listed in the Threshold-Crossing Events reference; the exam asks what categories exist and that the scope syntax is `hostname,interface` with wildcards.

## 11. UI workbench

The UI organises everything into *cards* on a *workbench*: Validation Summary, Network Health, Events, Inventory, Device/Interface cards, RoCE cards, WJH cards, Topology. Each card has a small/medium/large/full view; clicking into a validation card shows failed checks with drill-down to the device. The default workbench covers the premises; you build custom workbenches per team (e.g., an "AI fabric" workbench showing RoCE validation + WJH buffer drops + PFC rate cards). The UI also exposes LCM, snapshot compare, and notification configuration graphically.

## Configuration / command reference

```bash
# Server
netq install standalone full interface eth0 bundle /mnt/installables/NetQ-<ver>.tgz
netq install cluster full interface eth0 bundle <tgz> workers <ip> <ip> cluster-vip <vip>
netq install opta standalone full interface eth0 bundle <tgz> config-key <key>
netq show status [verbose]; netq show version; netq upgrade bundle <tgz>

# Agent (switch/host)
sudo netq config add server <ip> port 31980 vrf mgmt     # 31443 for cloud/OPTA
sudo netq config restart agent; sudo netq config show
sudo netq config add agent wjh; sudo netq config add agent wjh-drop-filter ...
netq show agents [rotten]

# LCM
netq lcm add credentials username cumulus password <pw>
netq lcm add cl-image <file>; netq lcm show switches
netq lcm upgrade cl-image name <job> cl-version <ver> hostnames <h1,h2> run-snapshot-before-after
netq lcm upgrade netq-image name <job> hostnames <h1,h2> to-version <ver>

# Validate / show
netq check bgp|evpn|mlag|interfaces|vlan|mtu|vxlan|ntp|sensors|agents|roce [hostnames ...] [around 1h]
netq show interfaces|bgp|evpn|mlag|lldp|macs|ip routes|events|inventory
netq show roce-config; netq show roce-counters <host> <port> [ingress|egress|pool]
netq show wjh-drop [l1|l2|router|tunnel|acl|buffer] [between now and 10m]
netq trace <dst> from <src> [vrf <v>] [pretty]

# Notifications
netq add notification channel slack|pagerduty|syslog|email <name> ...
netq add notification rule <name> key <field> value <regex>
netq add notification filter <name> rule <rule> channel <channel> [severity ...]
netq add tca event_id <ID> scope '<host>,<iface>' threshold <n> channel <channel>
```

## Common pitfalls & exam traps
- Agents on Cumulus must be told the server *and* the VRF: `netq config add server <ip> port 31980 vrf mgmt`. Omitting `vrf mgmt` is the #1 reason agents show Rotten.
- Port 31980 = on-prem server; 31443 = cloud/OPTA (TLS). 443 is the UI.
- `netq check` validates *collected* state across the fabric; `nv show` is one switch's live state. A Rotten agent makes `netq check` unreliable — check `netq show agents` first.
- `netq trace` is state-based path computation, not an active probe like `traceroute`.
- OPTA exists only in the cloud-hosted model; on-prem servers do not use an OPTA.
- LCM needs SSH credentials for the switches and the image uploaded to NetQ first; it can snapshot before/after and restore on failure.
- WJH collection must be enabled on the agent (`netq config add agent wjh`); drops are *categorised* (buffer, router, L2…) — know which category a PFC-WD or tail drop belongs to (buffer).
- Notifications are channel → rule → filter; TCAs are separate threshold events bound directly to a channel.
- Snapshots compare *state*; `nv config diff` compares *configuration*. Different tools for different questions.
- `netq show roce-config` reveals a leaf accidentally left in lossy mode; `netq check roce` automates that comparison.

## Key takeaways
- NetQ = server (on-prem VM/cluster or cloud + OPTA) + agents on switches and hosts streaming state on TCP 31980/31443.
- Install with `netq install standalone|cluster|opta full interface eth0 bundle <tgz>`; verify with `netq show status`.
- Agents: `netq config add server <ip> port 31980 vrf mgmt` + `netq config restart agent`; check `netq show agents` for Fresh/Rotten.
- `netq check bgp|evpn|mlag|interfaces|roce` validates the whole fabric in one command, including historical state via `around`.
- RoCE telemetry: `netq show roce-config` (profile consistency) and `netq show roce-counters` (PFC/ECN/queue/pool per port).
- WJH gives drop *reasons* per packet; `netq show wjh-drop [category]` queries them fleet-wide.
- `netq trace` computes paths from collected state; snapshots diff fabric state before/after changes.
- LCM upgrades Cumulus images and agents fleet-wide with backup/restore and snapshots.
- Notifications: channels (Slack/PagerDuty/syslog/email), rules, filters; TCAs for thresholds on counters, sensors, ASIC tables and RoCE metrics.
- The UI workbench presents the same data as cards; the CLI is exam-primary.

## Review questions

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
