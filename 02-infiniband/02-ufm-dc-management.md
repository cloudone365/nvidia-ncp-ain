# Module 2.2 — Data Center Management with NVIDIA UFM

> **Exam domain:** NVIDIA InfiniBand Networking (30%)  
> **Source course(s):** DC Management Made Easy with NVIDIA UFM  
> **Source docs:** NVIDIA UFM Enterprise User Manual (6.x); UFM Enterprise REST API Guide; UFM Telemetry User Manual; UFM Cyber-AI User Manual; UFM High Availability User Guide  
> **Estimated study time:** 4–6 hours

## Learning objectives
- Describe the three UFM editions (Telemetry, Enterprise, Cyber-AI) and what each layer adds.
- Describe the UFM Enterprise architecture — SM, telemetry, REST API, web UI — and how an HA pair is built.
- Install UFM Enterprise (bare metal, Docker or appliance) and perform initial configuration and fabric discovery.
- Navigate the UFM web UI to find topology, managed elements, events, telemetry and logical elements.
- Configure PKeys through the UI and REST API.
- Interpret port counters, congestion, BER, link-flap and temperature data and set alert thresholds.
- Verify UFM and fabric health and collect a system dump for support.
- Perform common admin tasks: change SM priority, disable a port, isolate a bad link, manage firmware.

> **Version note.** UFM evolves quickly. Paths, UI labels and REST endpoints below reflect UFM Enterprise 6.x as documented at time of writing; treat specifics as "as of UFM 6.x" and verify in the manual for your release.

## 1. What UFM is and why large fabrics need it

Module 2.1 showed that an InfiniBand subnet is run by one **SM**. On a 16-node lab the SM on a switch is enough. On a 1,000-GPU SuperPOD you need, in addition:

- A **scalable SM** with HA, persistent LID/GUID state and advanced routing (adaptive routing, SHARP aggregation manager).
- **Telemetry** from every port: counters, BER, temperature, congestion, collected every few seconds without flooding the fabric with per-host `perfquery` scripts.
- A **single pane of glass** with topology maps, inventory, events and alarms, plus hooks into the data center's existing monitoring (SNMP, syslog, Prometheus).
- An **API** so cluster orchestration (Slurm, Kubernetes, BCM) can create partitions, isolate failing links and pull health data automatically.

**UFM** (Unified Fabric Manager) is NVIDIA's product for this. Its SM is the enhanced MLNXSM, so everything about routing engines, PKeys and QoS from Module 2.1/2.3 applies; UFM wraps it with management, monitoring and automation.

## 2. UFM editions

| Edition | What it is | Adds |
|---|---|---|
| **UFM Telemetry** | Stand-alone, containerised telemetry collector | High-frequency port/counter collection from IB switches and HCAs, exported over HTTP in CSV/Prometheus formats. No SM, no UI. Used when you already have an SM elsewhere or want to feed Grafana/NetQ-style dashboards |
| **UFM Enterprise** | The full fabric manager (the subject of this module) | Everything in Telemetry **plus** the SM, fabric discovery and topology, web UI, REST API, events/alarms with SNMP/syslog/email forwarding, logical elements, PKey management, firmware/upgrade management, HA, SHARP AM integration |
| **UFM Cyber-AI** | Enterprise **plus** an analytics appliance | AI/ML models trained on telemetry history: anomaly detection, link failure prediction, cable degradation trends, security anomalies (unexpected topology changes), capacity analytics. Sold as an appliance |

Mental model: Telemetry ⊂ Enterprise ⊂ Cyber-AI. The exam tends to ask "which edition gives predictive/anomaly analytics" (Cyber-AI) and "which can run without an SM" (Telemetry).

## 3. UFM Enterprise architecture

A UFM Enterprise server is a Linux host (or VM, or container) with at least one HCA port on the IB fabric plus a management Ethernet interface. Internally it runs several cooperating services:

```text
+----------------------------------------------------------------------+
|  UFM Enterprise server                                               |
|                                                                      |
|   web UI (HTTPS 443)  <---->  REST API  (/ufmRest, /ufmRestV2)       |
|            |                        |                                |
|       UFM core / model DB (MySQL or SQLite, per release)             |
|            |                 |                 |                     |
|      opensm (MLNXSM)    UFM Telemetry     Event/alarm engine         |
|      SM + SA             (collector)      SNMP/syslog/email          |
|      SHARP AM (opt.)         |                                       |
|            |                 |                                       |
|       HCA port (mlx5_x) on the InfiniBand fabric                     |
+----------------------------------------------------------------------+
```

- **SM**: UFM starts and supervises its own `opensm` process. You do not edit opensm.conf by hand in the usual way; UFM generates it from its settings (see §5). A host still running a separate `opensm` is a conflict: disable every other SM before starting UFM in management mode.
- **Telemetry**: an embedded instance of UFM Telemetry polls counters via in-band management datagrams (PM MADs) and feeds the UI graphs, thresholds and the REST `/monitoring` endpoints.
- **Modes**: *Management* mode (UFM runs the master SM) is the normal case. *Monitoring* mode lets UFM observe a fabric whose SM runs elsewhere (set `monitoring_mode` in `gv.cfg`) — useful during migrations.
- **SHARP Aggregation Manager** (`sharp_am`) can run on the UFM server, so the same HA pair protects in-network computing (Module 2.3).

### 3.1 High availability
UFM HA is an **active/standby pair** of UFM servers, both connected to the fabric and to each other on a dedicated back-to-back or management link:

- Built with standard Linux clustering: **Pacemaker/Corosync** for resource management and failover, **DRBD** for block-level replication of `/opt/ufm/files` (the configuration and database volume). The `ufm_ha` package installs these and the `ufm_ha_cluster` wrapper command.
- A **virtual IP** floats to the active node, so the UI/REST clients and SNMP consumers keep a single address.
- The SM on the active node is master; the standby's SM is either not started or runs at a lower priority (UFM manages this), so the normal IB SM election is not what drives failover — the cluster does.
- Commands: `ufm_ha_cluster status`, `ufm_ha_cluster failover` (move mastership away from this node), `ufm_ha_cluster takeover` (claim it), `ufm_ha_cluster start|stop`, `ufm_ha_cluster cleanup`. Configuration is done once with `ufm_ha_cluster config` and its options (local/peer IPs, virtual IP, DRBD device); see the UFM HA guide for the exact flags.
- Split-brain avoidance relies on the dedicated replication link; losing *both* the management network and the DRBD link simultaneously is the scenario to design against.

Contrast with plain **SM HA** (Module 2.1 §7.5): two switch SMs with different priorities give SM redundancy only; UFM HA also replicates events, telemetry history, logical elements and PKey definitions.

## 4. Installation options

| Option | Packaging | Notes |
|---|---|---|
| **Bare metal / VM** | `ufm-enterprise-<ver>.tgz` with `install.sh`; requires MLNX_OFED / DOCA-OFED already installed and the HCA port Active | Most common for self-managed clusters; supported on specific RHEL/Rocky and Ubuntu releases — check the matrix |
| **Docker** | `ufm-enterprise` container image, run with `--net=host --privileged` and the `/opt/ufm/files` volume mapped from the host; `ufm-installation` helper container prepares the host | Lets you keep host OS and UFM lifecycles separate; HA is supported with the ufm_ha package on the host |
| **Appliance** | UFM Enterprise Appliance / Cyber-AI appliance: NVIDIA-shipped server with pre-installed OS and UFM, managed via a restricted CLI (`ufm` command tree) | Enterprise and SuperPOD deployments; licence tied to the appliance |

UFM is licensed per managed node; the licence file is uploaded in the UI or placed in `/opt/ufm/files/licenses/`. Without a licence UFM runs in evaluation mode for a limited period.

Typical bare-metal flow:

```bash
# Prerequisites: DOCA-OFED installed, HCA port 'Active', no other SM
ibstat | grep -E "State|Link layer"
tar xzf ufm-enterprise-6.x.y-z.tgz && cd ufm-enterprise-6.x.y-z
sudo ./install.sh                         # choose SM port, DB, etc. when prompted
sudo systemctl start ufm-enterprise       # legacy: /etc/init.d/ufmd start
sudo /opt/ufm/scripts/show_ufm_status.sh  # all core processes should be 'running'
# UI: https://<ufm-ip>/ufm   (default admin/123456 — change it)
```

## 5. Initial configuration and discovery

### 5.1 gv.cfg
UFM's main configuration file is **`/opt/ufm/files/conf/gv.cfg`** (older docs show `/opt/ufm/conf/gv.cfg`, which is a symlink into `files/`). It is an INI-style file; a few sections you should recognise:

```ini
[Server]
monitoring_mode = no          # yes = observe a fabric whose SM is elsewhere
ws_protocol = https
ws_port = 443

[SubnetManager]
sm_priority = 15              # UFM's SM should normally be the highest
routing_engine = ar_ftree     # written into the generated opensm.conf
event_plugin_option = ...

[Logging]
level = INFO

[Telemetry]
...
```

Edit, then restart UFM (`systemctl restart ufm-enterprise`). The generated SM configuration lives under `/opt/ufm/files/conf/opensm/opensm.conf`; a monitoring-mode variant `opensm_mon.conf` exists alongside it. UFM rewrites some parameters at start-up, so persistent SM changes should be made through the UI (Settings → Subnet Manager) or in `gv.cfg`, not by editing the generated file. Partitions are kept in `/opt/ufm/files/conf/opensm/partitions.conf` and also regenerated from the UFM database.

### 5.2 Discovery
Once UFM's SM becomes master it sweeps the fabric, and the web UI's Network Map populates within a minute or two. In monitoring mode the discovery interval is `mon_mode_discovery_period` (default 60 s). Things to check if discovery is empty:

- `ibstat` on the UFM host shows the port `Active` and `SM lid` equal to the UFM host's own LID.
- `sminfo` reports the UFM port GUID as master.
- No other SM is master: `show ib sm` on switches should show `standby` or disabled.
- Firewall allows 443 (UI/REST), 8005/8000 internal, SNMP 162 outbound, and the HA ports if clustered.

## 6. Web UI tour

The left navigation (as of 6.x) groups into these areas. Know what you would open to answer a given operational question.

| Area | What you find there | Typical use |
|---|---|---|
| **Dashboard** | Fabric summary, top congested ports, top alarms, utilisation gauges | Shift hand-over view |
| **Network Map** | Interactive topology: switches, hosts, links, colour-coded by state/alarm; can group by rack/site | "Which leaf is this GPU server on?" |
| **Managed Elements** | Devices (switches, HCAs/hosts, gateways, cables), their ports, firmware, PSIDs, serial numbers; actions such as reboot, upgrade FW, enable/disable port, set SM priority on a switch | Inventory and per-device operations |
| **Logical Elements** | **Environments** (a tenant/business grouping) → **Logical Servers** (groups of compute hosts) → **Networks** (PKey-backed); plus the **PKeys** view | Multi-tenant provisioning, mapping hosts to partitions |
| **Events & Alarms** | Event log with severity, source, time; alarms (persistent events) with acknowledge/clear; filtering by category (Hardware, Fabric Topology, Communication, Security, Gateway) | Incident investigation |
| **Telemetry** | Counter graphs per port/device, history, congestion and BER views; "monitoring sessions" for ad-hoc counter sets | Performance troubleshooting |
| **System Health** | UFM server health, SM status, HA status, licence, Telemetry status, fabric health report run | Confirm UFM itself is OK |
| **Settings** | SM settings (priority, routing engine, AR, SHARP, QoS), event policy & thresholds, SNMP/syslog/email destinations, users & RBAC/LDAP, licence, backups | Configuration |

### 6.1 PKey management via UI
Logical Elements → PKeys → "+" → enter the key (e.g. `0x0a01`), index-0 option (whether this key is placed at index 0 of the port's table, usually no), IPoIB enable, MTU/rate/SL, then add port GUIDs with membership *full* or *limited*. Save pushes the change into the SM's partitions file and triggers a resweep; no SM restart is needed. The same screen shows which GUIDs are members and lets you remove them.

## 7. Monitoring: what UFM collects and what it means

| Metric family | Examples | What a bad value means |
|---|---|---|
| **Port traffic counters** | `PortXmitData`, `PortRcvData`, packets/sec, utilisation % | Capacity / hot-spot analysis; a 100 % link under NCCL all-reduce is normal, persistent imbalance across spines suggests routing issue |
| **Error counters** | `SymbolErrorCounter`, `LinkErrorRecoveryCounter`, `LinkDownedCounter`, `PortRcvErrors`, `PortRcvRemotePhysicalErrors`, `PortXmitDiscards`, `PortRcvSwitchRelayErrors`, `ExcessiveBufferOverrunErrors`, `VL15Dropped` | Symbol errors / remote physical errors → bad cable, transceiver or connector; `LinkDowned` → link flaps; `XmitDiscards` → packets dropped due to lifetime/congestion |
| **Congestion** | `PortXmitWait` (cycles the port had data but no credits), congestion-control counters, `XmitWait` normalised to time | High `XmitWait` = downstream receiver not returning credits = congestion; map top offenders on the dashboard |
| **BER / PHY** | Effective and raw BER, FEC corrected/uncorrected blocks, eye margins (from PHY diagnostics, like **mlxlink**) | Raw BER above ~1e-8 on NDR with FEC still correcting is a warning; uncorrectable errors = replace the cable/module |
| **Link flaps** | Count of link up/down transitions per period | Flapping links cause SM re-sweeps and NCCL time-outs; UFM can auto-isolate them (see §9) |
| **Environment** | Switch and transceiver temperature, PSU/fan status, power | Overheating optics raise BER before they fail |

UFM Telemetry collects at a configurable period (default a few tens of seconds for the full set, faster for a "low-frequency vs high-frequency" split in newer releases) and stores history in a round-robin fashion; long-term retention is where Cyber-AI or an external Prometheus/Grafana stack comes in.

### 7.1 Events, severities and thresholds
Events have a severity (**Info / Minor / Warning / Critical**) and belong to a category. Many are **threshold events** generated by the telemetry engine: e.g. "Symbol error rate exceeded threshold", "Port temperature high", "Link utilisation > 90 %". Settings → Events Policy lets you:

- enable/disable each event type,
- set the threshold value and the evaluation period,
- choose the action: log, raise alarm, send **SNMP trap**, **syslog**, **email**, call a script, or (for link errors) **isolate the port**.

Alarm = persistent condition derived from events; it stays open until cleared or acknowledged.

### 7.2 Northbound integration
- **SNMP**: UFM sends traps (v2c/v3) to configured managers; the UFM MIB is shipped under `/opt/ufm/files/...` — load it into your NMS.
- **Syslog**: remote syslog of events, useful for SIEM.
- **Email**: SMTP relay for critical alarms.
- **Prometheus**: the telemetry endpoint (`http://<ufm>:9001/csv/...` or a Prometheus-format exporter, depending on release) can be scraped directly.
- **REST**: polling `/ufmRest/app/events` or `/ufmRest/app/alarms` from automation.

## 8. REST API

All management operations have a REST equivalent at `https://<ufm>/ufmRest/...` (and `/ufmRestV2`, `/ufmRestV3` for newer endpoints). Authentication is HTTP basic with a UFM user, or a token (`/ufmRest/app/tokens`) for scripts. Responses are JSON. Use `-k` only in the lab; production should have a real certificate.

```bash
UFM=https://ufm.example.net
AUTH="admin:password"

# Fabric inventory
curl -sk -u $AUTH $UFM/ufmRest/resources/systems | jq '.[] | {name, type, fw, state}'
curl -sk -u $AUTH $UFM/ufmRest/resources/ports | jq '.[] | select(.physical_state!="Link Up") | {name, node_description, physical_state}'
curl -sk -u $AUTH $UFM/ufmRest/resources/links

# Events and alarms
curl -sk -u $AUTH "$UFM/ufmRest/app/events?limit=50&severity=Critical"
curl -sk -u $AUTH $UFM/ufmRest/app/alarms

# PKeys
curl -sk -u $AUTH $UFM/ufmRest/resources/pkeys
curl -sk -u $AUTH $UFM/ufmRest/resources/pkeys/0x0a01?guids_data=true

# Create / update a PKey with members (POST or PUT, check the API guide for your version)
curl -sk -u $AUTH -X POST $UFM/ufmRest/resources/pkeys \
  -H 'Content-Type: application/json' \
  -d '{
        "pkey": "0x0a01",
        "index0": false,
        "ip_over_ib": true,
        "mtu_limit": 4,
        "service_level": 0,
        "rate_limit": 7,
        "guids": ["0c42a10300a1b2c0", "b83fd20300112233"],
        "membership": "full"
      }'

# Remove a member
curl -sk -u $AUTH -X POST $UFM/ufmRest/resources/pkeys/delete \
  -H 'Content-Type: application/json' \
  -d '{"pkey": "0x0a01", "guids": ["b83fd20300112233"]}'

# Port actions (disable a port): action name/parameters per release — verify in the REST guide
curl -sk -u $AUTH -X POST $UFM/ufmRest/actions \
  -H 'Content-Type: application/json' \
  -d '{"action": "disable_port", "object_ids": ["<port_guid>_<port_num>"], "object_type": "System", "identifier": "id", "description": "isolate flapping link"}'

# Telemetry snapshot (counter names per release)
curl -sk -u $AUTH "$UFM/ufmRest/monitoring/..."       # see 'Monitoring' section of the API guide
```

Field names (`guids`, `membership`, `index0`, `ip_over_ib`) are those documented for 6.x; the request format for setting membership per-GUID differs between endpoints, so copy the exact body from the REST API Guide for the version you run. The exam is more likely to ask *which resource* you would call (ports vs. systems vs. pkeys vs. events) than to test JSON field names.

## 9. UFM CLI and admin tasks

On the appliance, a restricted CLI (`ufm` prefix) wraps common operations; on bare-metal installs you use scripts under `/opt/ufm/scripts/` and `systemctl`. A non-exhaustive reference:

```bash
# Service lifecycle
systemctl status ufm-enterprise
systemctl restart ufm-enterprise
/opt/ufm/scripts/show_ufm_status.sh -e          # core + sub-processes

# HA
ufm_ha_cluster status
ufm_ha_cluster failover                          # move master to peer (run on current master)
ufm_ha_cluster takeover                          # pull master to this node

# Logs
ls /opt/ufm/files/log/        # ufm.log, event.log, opensm.log, telemetry logs, console.log

# System dump for support (name of the script as of 6.x; verify)
/opt/ufm/scripts/ufm_log_collect.sh              # or ufm_log_collect / 'UFM system dump' in the UI (System Health → Dump)
# Backup / restore of configuration + DB
/opt/ufm/scripts/ufm_backup.sh  ; /opt/ufm/scripts/ufm_restore.sh -f <backup.tgz>
```

### 9.1 Health checks
- **UFM health**: System Health page or `show_ufm_status.sh`: model service, SM, telemetry, DB, web server all "running"; HA shows which node is master and DRBD "UpToDate/UpToDate".
- **Fabric health report**: System Health → Fabric Health → Run. It executes an `ibdiagnet`-based sweep plus UFM's own checks (duplicate GUIDs, mixed firmware, non-optimal links such as width/speed degraded, cable errors) and stores an HTML/text report. REST: `/ufmRest/reports/Fabric_Health` (POST to run, GET to fetch; name may vary by release).
- **Periodic ibdiagnet** can be scheduled from UFM and its output is parsed into events (e.g. "Degraded link width").

### 9.2 Common administrative tasks

| Task | Where / how |
|---|---|
| Change UFM's SM priority | Settings → Subnet Manager → SM Priority; or `sm_priority` in `gv.cfg`; restart SM from the UI (no full UFM restart needed on newer releases) |
| Change a *switch's* embedded SM priority or disable it | Managed Elements → switch → Actions (MLNX-OS managed switches) or do it on the switch CLI (`ib sm sm-priority`) |
| Enable/disable a port | Managed Elements → device → Ports → select → Disable/Enable; or REST `actions` with `disable_port`/`enable_port` |
| **Isolate a bad link** | Same as disable on one or both ends; better, enable the event policy "isolate link on excessive errors" so UFM does it automatically and raises an alarm. After replacing the cable, re-enable and clear counters |
| Reset port counters | Telemetry/Managed Elements → Reset counters, or `perfquery -R` style via REST action `reset_ports_counters` (name per release) |
| Firmware management | Managed Elements → select devices → Software Update: upload MLNX-OS image for managed switches, or HCA/unmanaged switch firmware (uses in-band burning for unmanaged Quantum switches); UFM checks PSID compatibility |
| Topology validation | Upload a reference topology (`.topo`/ibdiagnet format) in Settings and enable "Topology Compare"; mismatches raise events — invaluable after cabling a new SuperPOD |
| User management | Settings → Users: roles (System Admin, Fabric Admin, Monitoring Only…), LDAP/AD/Kerberos integration |
| Backup configuration | Settings → System → Backup, or `ufm_backup.sh` |

## Configuration / command reference

| Item | Value / command |
|---|---|
| Main config | `/opt/ufm/files/conf/gv.cfg` |
| Generated SM config | `/opt/ufm/files/conf/opensm/opensm.conf` (`opensm_mon.conf` for monitoring mode) |
| Partitions | `/opt/ufm/files/conf/opensm/partitions.conf` (managed from UI/REST) |
| Logs | `/opt/ufm/files/log/` |
| Service | `systemctl {start|stop|restart|status} ufm-enterprise` |
| Status script | `/opt/ufm/scripts/show_ufm_status.sh [-e]` |
| HA | `ufm_ha_cluster {status|start|stop|failover|takeover|cleanup}` |
| Log collection | `/opt/ufm/scripts/ufm_log_collect.sh` (verify name) / UI System Dump |
| UI | `https://<ufm>/ufm` |
| REST base | `https://<ufm>/ufmRest/` (`/ufmRestV2`, `/ufmRestV3` where documented) |
| Inventory endpoints | `resources/systems`, `resources/ports`, `resources/links`, `resources/modules` |
| Events / alarms | `app/events`, `app/alarms` |
| PKeys | `resources/pkeys`, `resources/pkeys/<key>?guids_data=true`, `resources/pkeys/delete` |
| Actions | `actions` with `disable_port`, `enable_port`, `reset_ports_counters`, `sw_upgrade`, … |
| Telemetry export | `http://<ufm>:9001/csv/...` or Prometheus endpoint (release-specific) |

## Common pitfalls & exam traps
- **Any other master SM blocks UFM management mode.** Disable switch SMs (`no ib sm`) and host `opensm` before starting UFM, or run UFM in *monitoring* mode deliberately.
- **UFM HA ≠ SM HA.** UFM HA uses Pacemaker + DRBD with a virtual IP and replicates the entire UFM state; SM HA is just priority-based election.
- The edition with **predictive analytics/anomaly detection is Cyber-AI**; **Telemetry** is the only edition that runs without an SM.
- Edit `gv.cfg` or use the UI for SM settings; hand-edits of the *generated* `opensm.conf` under `/opt/ufm/files/conf/opensm/` can be overwritten.
- UFM's SM priority should be the highest in the fabric (commonly 15) so a switch SM never pre-empts it.
- Disabling a port in UFM is an *administrative* action on one end; to truly isolate a link with a failing cable, disable at least one side and raise a ticket — do not just reset counters.
- PKey changes from the UI are pushed into the SM live; the SM does **not** need a restart, but hosts need the matching IPoIB child interface.
- Logical Elements (Environments / Logical Servers / Networks) are an *abstraction* for provisioning; they map onto PKeys underneath.
- REST paths and action names change between 6.x releases; questions usually test *which resource family* (`resources/ports` vs `app/events`), not exact JSON.
- Licence is per managed node; evaluation mode expires.

## Key takeaways
- UFM editions: **Telemetry** (collection only, no SM) ⊂ **Enterprise** (SM + UI + REST + events + HA) ⊂ **Cyber-AI** (adds ML analytics appliance).
- UFM Enterprise runs its own MLNXSM; it must be the only master SM in management mode.
- HA = active/standby pair, **Pacemaker/Corosync + DRBD**, virtual IP, managed with `ufm_ha_cluster`.
- Install options: bare metal (`install.sh`), Docker container, or appliance.
- Main config `/opt/ufm/files/conf/gv.cfg`; generated SM config under `/opt/ufm/files/conf/opensm/`.
- UI areas: Network Map, Managed Elements, Logical Elements (Environments/Logical Servers/PKeys), Events & Alarms, Telemetry, System Health, Settings.
- Key telemetry: traffic, error counters (symbol errors, link downed, xmit discards), `XmitWait` congestion, BER/FEC, link flaps, temperature.
- Events: Info/Minor/Warning/Critical; actions = alarm, SNMP, syslog, email, script, port isolation.
- REST: `/ufmRest/resources/{systems,ports,links,pkeys}`, `/ufmRest/app/{events,alarms}`, `/ufmRest/actions`.
- Health: `show_ufm_status.sh`, Fabric Health report, `ufm_log_collect` / System Dump for support.

## Review questions

1. A customer wants link-failure prediction based on historical telemetry trends. Which UFM edition provides this?
   A. UFM Telemetry
   B. UFM Enterprise
   C. UFM Cyber-AI
   D. Any edition with the Prometheus exporter enabled

2. UFM Enterprise HA replicates configuration and database between the two nodes using which technology?
   A. rsync on a cron schedule
   B. DRBD block replication managed by Pacemaker
   C. MySQL Galera multi-master
   D. The InfiniBand SM's standby synchronisation

3. After installing UFM Enterprise, the Network Map is empty and `ibstat` on the UFM host shows `SM lid: 1`, which belongs to a Quantum switch. What should you do?
   A. Increase UFM's telemetry interval
   B. Disable the switch's embedded SM (or lower its priority below UFM's) so UFM becomes master
   C. Switch UFM to Docker installation
   D. Add the switch GUID to partitions.conf

4. Which REST resource would an automation script query to find all ports whose physical state is not "Link Up"?
   A. `/ufmRest/app/events`
   B. `/ufmRest/resources/pkeys`
   C. `/ufmRest/resources/ports`
   D. `/ufmRest/actions`

5. A port shows rapidly increasing `SymbolErrorCounter` and several `LinkDownedCounter` increments in the last hour. What is the best immediate UFM action?
   A. Reset the counters and keep watching
   B. Disable (isolate) the port and raise a cable-replacement ticket
   C. Increase the SM sweep interval
   D. Move the port to a different PKey

6. Which UFM web UI area lets you group hosts into tenants and bind them to PKey-backed networks?
   A. Managed Elements
   B. Telemetry
   C. Logical Elements
   D. System Health

7. Which counter most directly indicates congestion (a port had data to send but no credits) on an InfiniBand link?
   A. `PortRcvErrors`
   B. `PortXmitWait`
   C. `SymbolErrorCounter`
   D. `VL15Dropped`

8. Which file holds UFM's primary server settings such as monitoring mode and web port?
   A. `/etc/opensm/opensm.conf`
   B. `/opt/ufm/files/conf/gv.cfg`
   C. `/opt/ufm/files/log/ufm.log`
   D. `/etc/ufm/ufm.yaml`

### Answer key
1. C — Cyber-AI adds the ML analytics layer; Enterprise alerts on thresholds but does not predict.
2. B — UFM HA is built on Pacemaker/Corosync with DRBD-replicated `/opt/ufm/files` and a virtual IP.
3. B — Only one master SM; UFM management mode requires its SM to win (or every other SM to be disabled).
4. C — Port inventory and state live under `resources/ports`.
5. B — Symbol errors plus link flaps indicate a failing cable/module; isolate to protect the fabric, then replace.
6. C — Environments → Logical Servers → Networks are the Logical Elements abstraction over PKeys.
7. B — `PortXmitWait` counts time waiting for credits, the IB congestion signal.
8. B — `gv.cfg` is UFM's main configuration file.

## References
- NVIDIA Academy: *DC Management Made Easy with NVIDIA UFM*
- NVIDIA UFM Enterprise User Manual v6.x — https://docs.nvidia.com/networking/display/ufmenterpriseumv6180 (and later), "Running UFM Server Software", "Troubleshooting" chapters
- NVIDIA UFM Enterprise REST API Guide — https://docs.nvidia.com/networking/ (UFM section)
- NVIDIA UFM High Availability User Guide — https://docs.nvidia.com/networking/
- NVIDIA UFM Telemetry User Manual; UFM Cyber-AI User Manual — https://docs.nvidia.com/networking/
- Related modules: [2.1 IB Administration](01-infiniband-network-administration.md), [2.3 QoS/AR/SHARP](03-infiniband-qos-adaptive-routing-sharp.md), Module 8 IB diagnostics (`../08-troubleshooting/`)
