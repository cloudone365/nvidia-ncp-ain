# Module 4.4 — SONiC Essentials (NVIDIA)

> **Exam domain:** NVIDIA Spectrum Networking (30%)  
> **Source course(s):** SONiC Essentials by NVIDIA (NVIDIA Academy)  
> **Source docs:** SONiC community documentation (Architecture, Command Reference, Configuration); NVIDIA Networking SONiC release notes and user manual; NVIDIA Spectrum SAI documentation  
> **Estimated study time:** 4–5 hours

## 1. Learning objectives

By the end of this module you will be able to…

- **Describe** the SONiC architecture: containerized services, SAI, the Redis-based CONFIG_DB / APPL_DB / ASIC_DB, `orchagent`, `syncd` and FRR. *(Spectrum Networking — NOS architecture)*
- **Explain** NVIDIA's role in SONiC (Spectrum SAI, NVIDIA-supported "pure SONiC" distribution) and when to choose SONiC over Cumulus Linux. *(Spectrum Networking — NOS selection)*
- **Configure** interfaces, VLANs and BGP with the SONiC CLI and `config_db.json`, and **manage** configuration with `config save/load/reload`. *(Spectrum Networking — configuration)*
- **Configure** RoCE on SONiC: buffer profiles, PFC, PFC watchdog and ECN/WRED, and **verify** with `show pfc counters`/`show queue counters`. *(Spectrum Networking — RoCE)*
- **Install** and **upgrade** SONiC images with `sonic-installer` and **manage** service containers. *(Spectrum Networking — lifecycle)*
- **Troubleshoot** SONiC with `show techsupport`, `show logging`, `redis-cli` and container inspection. *(Troubleshooting Tools)*
- **Compare** Cumulus Linux and SONiC for an AI fabric. *(AI Data Center Design)*

## 2. Key technical concepts

### 2.1 What SONiC is

*Analogy:* Cumulus is a single company where everyone sits in one office and talks directly. SONiC is a campus of separate buildings (containers) that communicate only by writing on shared whiteboards (Redis databases). Any one building can be rebuilt without evacuating the others.

**SONiC** (Software for Open Networking in the Cloud) is an open-source network OS started by Microsoft for Azure and now governed by the Linux Foundation. Two ideas define it:

1. **Everything is a container.** A Debian host runs Docker; each functional block (BGP, LLDP, SNMP, telemetry, the switch state service, the ASIC sync daemon) is a separate container with its own lifecycle. A crashed BGP container is restarted without touching the data plane.
2. **State lives in Redis.** Instead of config files and inter-process RPCs, every daemon reads and writes tables in a Redis database. Configuration, derived application state and the ASIC's programmed state are three separate databases, and the differences between them are how you troubleshoot.

### 2.2 The database stack — whiteboards each daemon reads

| Database | Redis DB # | Who writes it | What it holds |
|---|---|---|---|
| **CONFIG_DB** | 4 | You (`config` CLI, `config_db.json` load) | Intended configuration: PORT, VLAN, VLAN_MEMBER, INTERFACE, BGP_NEIGHBOR, BUFFER_PROFILE, WRED_PROFILE, PORT_QOS_MAP… |
| **APPL_DB** | 0 | Protocol daemons (FRR via `fpmsyncd`, `portsyncd`, `intfmgrd`, `vlanmgrd`) | Application-level state: routes to install, neighbors, ports as the control plane sees them |
| **ASIC_DB** | 1 | `orchagent` | SAI objects the ASIC *should* contain, in SAI terms (SAI_OBJECT_TYPE_ROUTE_ENTRY…) |
| **STATE_DB** | 6 | Various | Operational state (port oper status, transceiver info, PFC WD status) |
| COUNTERS_DB | 2 | `syncd` via flex counters | Interface/queue/PFC counters |

The flow for a static route: `config route add` → CONFIG_DB → `*mgrd` daemon → APPL_DB → **`orchagent`** (inside the `swss` container) translates it to SAI calls → ASIC_DB → **`syncd`** (inside the `syncd` container) reads ASIC_DB and calls the vendor SAI library → ASIC programmed. If a route is in APPL_DB but not ASIC_DB, orchagent rejected it; if it is in ASIC_DB but not in hardware, syncd/SAI failed.

**Why the exam cares:** "route exists but doesn't forward" on SONiC is answered by naming which whiteboard it is missing from and therefore which daemon dropped it.

### 2.3 Key containers and daemons

| Container | Contents |
|---|---|
| `swss` | Switch State Service: `orchagent`, `portsyncd`, `intfmgrd`, `vlanmgrd`, `neighsyncd`, `buffermgrd` |
| `syncd` | `syncd` + the vendor **SAI** library (for Spectrum: NVIDIA's SAI implementation on top of the SDK) |
| `bgp` | **FRR** (`bgpd`, `zebra`, `staticd`) + `fpmsyncd` pushing routes to APPL_DB |
| `lldp` | `lldpd` |
| `teamd` | LAG/LACP |
| `pmon` | Platform monitor: fans, PSUs, thermals, transceivers |
| `database` | Redis itself |
| `snmp`, `telemetry` (gNMI), `dhcp_relay`, `radv`, `mgmt-framework` | Optional services |

**SAI** (Switch Abstraction Interface) is the C API between orchagent and the ASIC vendor's code. Because every ASIC vendor ships a SAI, SONiC's upper layers are hardware-agnostic. NVIDIA's contribution is the Spectrum SAI, which exposes Spectrum features (shared buffers, WJH, RoCE, adaptive routing on newer releases) through standard and extended SAI attributes.

### 2.4 NVIDIA and SONiC

NVIDIA (Mellanox before acquisition) has been a top SONiC contributor since 2016 and ships:
- **NVIDIA Networking SONiC** (sometimes "NVIDIA pure SONiC"): an NVIDIA-built, tested and supported distribution based on upstream community SONiC with Spectrum SAI, platform drivers and NVIDIA-validated fixes, delivered as `.bin` images for Spectrum switches (SN2000–SN5000). Support comes with the NVIDIA switch support contract.
- Spectrum SAI and platform modules upstreamed to the community tree, so community SONiC also runs on Spectrum.
- Tooling integrations: NetQ agents for SONiC, WJH on SONiC, NVIDIA Air images for SONiC.

| Choose Cumulus Linux when… | Choose SONiC when… |
|---|---|
| You want a vendor-integrated OS with NVUE, full feature set (EVPN MH, MLAG, RoCE one-liner, AR, telemetry) on day one | Your organisation already standardises on SONiC (hyperscaler/cloud model) and runs it on other vendors' silicon |
| Operational tooling is NVIDIA-centric (NetQ LCM, Air, Ansible `nvidia.nvue`) | You need a fully open-source, auditable stack and the freedom to build your own image |
| The Spectrum-X reference architecture is being followed as published (Cumulus is the primary NOS in Spectrum-X RA documents) | You have in-house development to extend containers/features and manage the faster-moving release cadence |
| Smaller team, wants supported defaults | Large team, config generated by an orchestrator writing `config_db.json` directly |

The exam expects you to know that *both* are NVIDIA-supported on Spectrum and that Cumulus offers the richer, more opinionated feature set, while SONiC offers openness and multi-vendor uniformity.

### 2.5 Configuration model: `config_db.json`

The CONFIG_DB is persisted as `/etc/sonic/config_db.json`. Everything you configure through `config` commands ends up here. Structure is a JSON object keyed by table, then by object key (pipe-separated for compound keys). Points to remember:
- Interface names are `EthernetN` where N is the *first lane index*, not a port number. `etp1a` style aliases map to front-panel labels; `show interfaces alias` translates. A 4-lane breakout of port 1 yields Ethernet0/4/8/12 on an 8-lane 800G platform — the HWSKU defines this.
- The **HWSKU** (`Mellanox-SN5600-C256S1`, etc.) encodes platform, port configuration and default buffer profile. Changing breakout configuration on SONiC is done via `config interface breakout` (Dynamic Port Breakout) or by selecting a different HWSKU — much more rigid than NVUE's `link breakout`.
- MTU default is 9100 on most SONiC platforms.

Unlike Cumulus, SONiC's `config` commands take effect *immediately* (no pending/apply model). There is no built-in diff; the discipline is: change → verify → `config save`.

| Command | Effect |
|---|---|
| `sudo config save -y` | running CONFIG_DB → `/etc/sonic/config_db.json` |
| `sudo config load -y [file]` | merge file into CONFIG_DB (additive, no restart) |
| `sudo config reload -y [file]` | restart swss/syncd and rebuild CONFIG_DB from file — **disruptive** |
| `sudo config load_minigraph` | Azure-style XML topology (rare outside hyperscalers) |
| `show runningconfiguration all` | dump CONFIG_DB as JSON |

### 2.6 BGP on SONiC

FRR runs inside the `bgp` container; CONFIG_DB BGP_NEIGHBOR entries are rendered to FRR config by `bgpcfgd` using templates. For anything beyond basic neighbours (route-maps, EVPN, unnumbered), operators either enable the FRR "unified" config mode (`frr_mgmt_framework_config`/`docker_routing_config_mode: split|unified` in DEVICE_METADATA — check current docs) and edit FRR directly with `vtysh`, or use SONiC's newer `config bgp` extensions. BGP unnumbered and EVPN VXLAN are supported in recent SONiC releases but are configured partly in CONFIG_DB (VXLAN_TUNNEL, VXLAN_TUNNEL_MAP, VRF) and partly in FRR.

### 2.7 RoCE on SONiC — assembling what Cumulus does in one line

*Analogy:* Cumulus's `nv set qos roce` is a pre-built kit. SONiC hands you the parts list and a screwdriver.

SONiC has no single "roce lossless" command. You assemble the pieces that Cumulus's `nv set qos roce` sets for you. This is a favourite exam contrast.

| Table | Purpose |
|---|---|
| `DSCP_TO_TC_MAP` | DSCP → traffic class (e.g. DSCP 26 → TC 3 for RoCE, DSCP 48 → TC 6 for CNP) |
| `TC_TO_QUEUE_MAP` | TC → egress queue |
| `TC_TO_PRIORITY_GROUP_MAP` | TC → ingress priority group (PG) — PGs are where PFC and lossless buffers live |
| `PFC_PRIORITY_TO_QUEUE_MAP` | Which PFC priority pauses which queue |
| `PORT_QOS_MAP` | Binds the maps to ports and sets `pfc_enable: "3"` |
| `BUFFER_POOL` / `BUFFER_PROFILE` | Shared pools and per-PG/queue profiles; lossless profiles have `xon`/`xoff` headroom |
| `BUFFER_PG` / `BUFFER_QUEUE` | Attach profiles to port PGs and queues |
| `WRED_PROFILE` | ECN marking thresholds |
| `QUEUE` | Attach WRED profile and scheduler to queues |
| `SCHEDULER` | DWRR weights / strict priority |
| `PFC_WD` | PFC watchdog per port |

A **PFC storm** (a NIC stuck sending pause frames) can freeze a lossless queue fabric-wide. **`pfcwd`** detects a queue paused continuously longer than `detection_time` ms and takes an `action` (`drop` the queue's traffic, `forward` ignoring pause, or `alert` only), restoring after `restoration_time` ms without pause. Cumulus has the same concept (`nv set qos pfc-watchdog` / `nv set interface swpN qos pfc-watchdog` — verify object name).

### 2.8 Images and containers

SONiC keeps multiple images on disk and selects via GRUB — a true dual-image capability (unlike Cumulus's single partition). Initial installation uses ONIE exactly as Cumulus does (`onie-nos-install http://.../sonic.bin`). After install, `config_db.json` is carried over from the previous image when upgrading in place; still back it up. Restarting `swss` or `syncd` resets the data plane (like `switchd` restart on Cumulus). Restarting `bgp` or `lldp` does not. SONiC "warm reboot" (`sudo warm-reboot`) and "fast reboot" exist to upgrade with minimal data-plane impact when the platform supports it.

SONiC ZTP (`ztp` service) uses DHCP option 67 / 239 / provisioning URL to fetch a JSON ZTP spec describing ordered plugins (`firmware`, `config-db-json`, `graphservice`, `connectivity-check`, custom scripts). `show ztp status`, `sudo config ztp enable|disable|run`. Concept is the same as Cumulus; the input is a JSON document rather than a bash script.

### 2.9 Cumulus Linux vs SONiC

| Aspect | Cumulus Linux | NVIDIA SONiC |
|---|---|---|
| Heritage | NVIDIA (Cumulus Networks), proprietary distribution on open components | Open source (LF), Microsoft origin; NVIDIA builds/supports a distribution |
| Architecture | Debian, `switchd` + SDK, FRR on host, `ifupdown2` | Debian + Docker containers, SAI, Redis DBs, FRR in container |
| CLI | **NVUE** `nv set/show/config apply` with pending/apply/save, revisions, REST API | `config`/`show` immediate; `config save`; `config_db.json`; gNMI/REST via mgmt-framework |
| Config model | Declarative YAML, `nv config replace` | JSON CONFIG_DB |
| RoCE | `nv set qos roce mode lossless` one-liner | Buffer/PG/WRED/PFC tables assembled manually or via templates; `config qos reload` |
| Adaptive routing | Yes (Spectrum-2+), NVUE | Platform-dependent, newer NVIDIA releases — verify |
| EVPN / MLAG / MH | Full | EVPN VXLAN yes; MC-LAG community support varies; MH limited |
| Images | Single partition, ONIE reinstall or apt | Multi-image via `sonic-installer`, warm/fast reboot |
| Support bundle | `cl-support` | `show techsupport` |
| ASIC resources | `cl-resource-query` | `crm show resources all` (Critical Resource Monitoring) |
| Telemetry | NetQ, WJH, gNMI, OTLP | NetQ, WJH (NVIDIA builds), gNMI telemetry container |
| Automation | `nvidia.nvue` Ansible collection, REST | `sonic-net` community Ansible roles, generic `copy` of config_db.json, gNMI |
| Best fit | NVIDIA-centric AI fabrics following Spectrum-X RA | Multi-vendor, hyperscaler-style operations |

`crm` (Critical Resource Monitoring) is SONiC's analogue of **cl-resource-query**: `crm show resources all` shows used/available for routes, nexthops, FDB, ACL, with configurable thresholds that raise syslog alerts.

## 3. Live-style walkthrough — from `config_db.json` to a verified RoCE leaf

We take an SN5600 running NVIDIA SONiC from a bare `config_db.json` to a PFC/ECN-enabled RoCE leaf and prove it with counters.

### Step 1 — Inspect the starting point

```bash
show version                                 # SONiC version, HWSKU, platform, ASIC
show platform summary                        # Platform: x86_64-nvidia_sn5600-r0, HwSKU: Mellanox-SN5600-C256S1
show interfaces status | head               # EthernetN names, lanes, speed, oper state
show interfaces alias | head                 # Ethernet0 <-> etp1a: the panel label you will cable to
show runningconfiguration all | head -40    # the live CONFIG_DB as JSON
```

### Step 2 — Base identity and interfaces

The minimum `config_db.json` for a leaf. Notice every key is a *table*, and compound keys use `|`:

```json
{
  "DEVICE_METADATA": {
    "localhost": {
      "hostname": "leaf01",
      "hwsku": "Mellanox-SN5600-C256S1",
      "platform": "x86_64-nvidia_sn5600-r0",
      "type": "LeafRouter",
      "bgp_asn": "65011"
    }
  },
  "PORT": {
    "Ethernet0":  { "alias": "etp1a", "lanes": "0,1,2,3", "speed": "400000", "mtu": "9100", "admin_status": "up", "fec": "rs" },
    "Ethernet4":  { "alias": "etp1b", "lanes": "4,5,6,7", "speed": "400000", "mtu": "9100", "admin_status": "up", "fec": "rs" }
  },
  "VLAN": {
    "Vlan100": { "vlanid": "100" }
  },
  "VLAN_MEMBER": {
    "Vlan100|Ethernet0": { "tagging_mode": "untagged" }
  },
  "VLAN_INTERFACE": {
    "Vlan100": {},
    "Vlan100|10.100.1.1/24": {}
  },
  "LOOPBACK_INTERFACE": {
    "Loopback0": {},
    "Loopback0|10.0.0.11/32": {}
  },
  "BGP_NEIGHBOR": {
    "Ethernet248": { "asn": "65000", "name": "spine01", "admin_status": "up", "local_addr": "" }
  }
}
```

Load it. `load` merges without a data-plane reset; `reload` rebuilds everything (disruptive) — on a greenfield switch either is fine:

```bash
sudo config load -y /home/admin/leaf01-base.json      # merge into CONFIG_DB; takes effect immediately
sudo config save -y                                   # persist to /etc/sonic/config_db.json NOW — SONiC has no "apply" safety net
```

Or build the same thing interactively — every command writes CONFIG_DB immediately:

```bash
sudo config interface startup Ethernet0               # admin up
sudo config interface speed Ethernet0 400000          # 400G (in Mb/s)
sudo config interface mtu Ethernet0 9100
sudo config interface fec Ethernet0 rs                # RS-FEC: mandatory at 400G
sudo config vlan add 100
sudo config vlan member add -u 100 Ethernet0          # -u untagged (access); omit for tagged
sudo config interface ip add Vlan100 10.100.1.1/24
sudo config interface ip add Loopback0 10.0.0.11/32
sudo config interface ip add Ethernet248 192.168.1.0/31   # numbered uplink to spine01
sudo config bgp startup neighbor 192.168.1.1
sudo config save -y
```

Checkpoints:

```bash
show interfaces status Ethernet0
```
```text
  Interface  Lanes    Speed  MTU   FEC  Alias  Vlan   Oper  Admin  Type         Asym PFC
-----------  -------  -----  ----  ---  -----  -----  ----  -----  -----------  --------
  Ethernet0  0,1,2,3  400G   9100  rs   etp1a  trunk  up    up     OSFP 8X      N/A
```
```bash
show vlan brief                 # Vlan100, 10.100.1.1/24, Ethernet0 untagged
show ip bgp summary             # 192.168.1.1 AS 65000 state Established (once the spine is up)
show ip route                   # Loopback0 and Vlan100 connected, spine routes via BGP
```

### Step 3 — RoCE: DSCP→TC→PG maps, PFC, buffers, ECN, watchdog

Start from the platform templates, then confirm/override. `config qos reload` regenerates QoS/buffer tables from `/usr/share/sonic/device/<platform>/<hwsku>/buffers_defaults_t0.j2` and the QoS template:

```bash
sudo config qos reload              # regenerate QoS/buffer tables from the platform templates
show buffer configuration           # effective pools and profiles after reload
show qos                            # maps and bindings (newer releases)
```

Now the RoCE-specific merge file. Every table maps to a Cumulus concept you already know:

```json
{
  "DSCP_TO_TC_MAP": {
    "AZURE": { "0": "0", "26": "3", "48": "6" }
  },
  "TC_TO_QUEUE_MAP": {
    "AZURE": { "0": "0", "3": "3", "6": "6" }
  },
  "TC_TO_PRIORITY_GROUP_MAP": {
    "AZURE": { "0": "0", "3": "3", "6": "6" }
  },
  "PFC_PRIORITY_TO_QUEUE_MAP": {
    "AZURE": { "3": "3" }
  },
  "PORT_QOS_MAP": {
    "Ethernet0": {
      "dscp_to_tc_map": "AZURE",
      "tc_to_queue_map": "AZURE",
      "tc_to_pg_map": "AZURE",
      "pfc_to_queue_map": "AZURE",
      "pfc_enable": "3"
    }
  },
  "BUFFER_POOL": {
    "ingress_lossless_pool": { "type": "ingress", "mode": "dynamic", "size": "..." },
    "egress_lossless_pool":  { "type": "egress",  "mode": "dynamic", "size": "..." },
    "egress_lossy_pool":     { "type": "egress",  "mode": "dynamic", "size": "..." }
  },
  "BUFFER_PROFILE": {
    "pg_lossless_400000_100m_profile": {
      "pool": "ingress_lossless_pool", "size": "...", "xon": "...", "xoff": "...", "dynamic_th": "0"
    },
    "egress_lossy_profile": { "pool": "egress_lossy_pool", "size": "...", "dynamic_th": "3" }
  },
  "BUFFER_PG": {
    "Ethernet0|3": { "profile": "pg_lossless_400000_100m_profile" },
    "Ethernet0|0": { "profile": "ingress_lossy_profile" }
  },
  "WRED_PROFILE": {
    "AZURE_LOSSLESS": {
      "ecn": "ecn_all",
      "green_min_threshold": "1000000",
      "green_max_threshold": "2000000",
      "green_drop_probability": "5",
      "wred_green_enable": "true"
    }
  },
  "QUEUE": {
    "Ethernet0|3": { "scheduler": "scheduler.1", "wred_profile": "AZURE_LOSSLESS" },
    "Ethernet0|6": { "scheduler": "scheduler.0" }
  },
  "SCHEDULER": {
    "scheduler.0": { "type": "STRICT" },
    "scheduler.1": { "type": "DWRR", "weight": "50" }
  },
  "PFC_WD": {
    "Ethernet0": { "action": "drop", "detection_time": "200", "restoration_time": "200" },
    "GLOBAL": { "POLL_INTERVAL": "100" }
  }
}
```

Reading it as an instructor:
- `DSCP_TO_TC_MAP` — DSCP 26 → TC 3 (RoCE), DSCP 48 → TC 6 (CNP). Identical to the Cumulus profile's DSCP→switch-priority table.
- `TC_TO_PRIORITY_GROUP_MAP` — TC 3 lands in **PG 3**. PFC and lossless headroom live on the *ingress* PG, so this mapping is what makes PFC meaningful.
- `PORT_QOS_MAP ... "pfc_enable": "3"` — the single most-tested field: PFC on priority 3 for this port.
- `BUFFER_PROFILE pg_lossless_400000_100m_profile` — the name encodes 400G and a 100 m cable; `xon`/`xoff` are the pause-resume thresholds, platform-computed (that is why the values are `"..."` here — NVIDIA ships them per HWSKU).
- `WRED_PROFILE AZURE_LOSSLESS` with `ecn: ecn_all` — this is the ECN marking that Cumulus calls `congestion-control`.
- `QUEUE Ethernet0|6 → scheduler.0 STRICT` — CNPs leave first, exactly as on Cumulus where CNP is strict-priority TC 6.
- `PFC_WD` — detection 200 ms, restoration 200 ms, action drop.

```bash
sudo config load -y /home/admin/roce_qos.json       # merge the RoCE tables
sudo config save -y
# Or drive pfcwd from the CLI:
sudo config pfcwd start --action drop --restoration-time 200 Ethernet0 200
sudo config pfcwd start_default                       # all ports, platform defaults
```

### Step 4 — Verify lossless behaviour

```bash
show pfc counters
```
```text
     Port Rx    PFC0    PFC1    PFC2    PFC3    PFC4    PFC5    PFC6    PFC7
----------  ------  ------  ------  ------  ------  ------  ------  ------
 Ethernet0       0       0       0    1842       0       0       0       0

     Port Tx    PFC0    PFC1    PFC2    PFC3    PFC4    PFC5    PFC6    PFC7
----------  ------  ------  ------  ------  ------  ------  ------  ------
 Ethernet0       0       0       0     311       0       0       0       0
```

Only the PFC3 column moves — exactly what a correct priority-3 lossless profile looks like. Interpretation is identical to Cumulus: Tx pause on a downlink → the host is slow; Rx pause on an uplink → the spine is pausing you; rising ECN-marked counts with no PFC → congestion control working as intended.

```bash
show pfc asymmetric
show queue counters Ethernet0        # per-queue packets/bytes, drops — look at UC3 (RoCE) and UC6 (CNP); UC3 drops must be 0
show queue watermark unicast         # peak occupancy per queue since last clear
show priority-group watermark headroom
show buffer configuration            # effective pools/profiles
show pfcwd config                    # Ethernet0 action drop 200/200
show pfcwd stats                     # storm count per queue — should be 0 in a healthy fabric
sonic-clear pfccounters; sonic-clear queuecounters   # zero before a benchmark
```

### Step 5 — Peek at the whiteboards

```bash
redis-cli -n 4 keys 'PORT_QOS_MAP|*'                            # CONFIG_DB: intent
sonic-db-cli CONFIG_DB hgetall 'PORT_QOS_MAP|Ethernet0'          # pfc_enable 3
redis-cli -n 0 hgetall 'ROUTE_TABLE:10.100.1.0/24'               # APPL_DB: the control plane asked for it
redis-cli -n 1 keys 'ASIC_STATE:SAI_OBJECT_TYPE_ROUTE_ENTRY*' | wc -l   # ASIC_DB: what orchagent programmed
```

If a route is in DB 0 but not DB 1, read `show logging orchagent` — usually a SAI error such as table-full or an unresolved next hop.

### Step 6 — Image lifecycle and containers

```bash
sudo sonic-installer list                        # Current, Next, Available images
sudo sonic-installer install /tmp/sonic-mellanox-202405.bin
sudo sonic-installer set-default <image>
sudo sonic-installer set-next-boot <image>       # try once, fall back automatically if it fails to boot
sudo sonic-installer remove <image>
show runningconfiguration all > backup.json      # always, even though config carries over
sudo reboot

docker ps                                   # swss, syncd, bgp, lldp, pmon, database, ...
show services                               # processes inside each container
sudo systemctl restart bgp                  # each container is also a systemd unit; bgp restart is NOT disruptive
docker exec -it bgp vtysh                   # into FRR
docker logs syncd --tail 100
sudo config feature state telemetry enabled # enable optional containers
show feature status
```

## 4. Troubleshooting / common gotchas

### Gotcha 1 — VLANs vanished after reboot

**Symptom:** `config vlan add` worked, `show vlan brief` was correct, then a reboot lost everything.

**Why it happens:** SONiC `config` is immediate but *not* persistent; `config save` was skipped.

**How to debug:** `diff <(show runningconfiguration all) /etc/sonic/config_db.json` before the reboot would have shown the gap.

**Fix:** re-apply and `sudo config save -y`. Make it muscle memory: change → verify → save.

### Gotcha 2 — Route in `show ip route`, traffic blackholes

**Symptom:** FRR has the route, APPL_DB has it, hosts are unreachable.

**Why it happens:** `orchagent` rejected it (not in ASIC_DB) — table full, bad next hop, or SAI error.

**How to debug:**
```bash
redis-cli -n 0 hgetall 'ROUTE_TABLE:<prefix>'          # present
redis-cli -n 1 keys '*ROUTE_ENTRY*<prefix>*'            # absent
show logging orchagent | grep -i -E 'fail|err'
crm show resources all                                  # route/nexthop at capacity?
```

**Fix:** summarise routes or raise `crm` thresholds for early warning; if SAI logged an error, `docker logs syncd` and escalate with `show techsupport`.

### Gotcha 3 — PFC configured, counters never move / host still drops

**Symptom:** `show pfc counters` all zeros under load, or RoCE retransmits on the host.

**Why it happens:** `pfc_enable` is set but the TC→PG map does not place TC 3 in PG 3, or the lossless `BUFFER_PG` for `Ethernet0|3` is missing — so there is no lossless headroom and no pause generation.

**How to debug:**
```bash
sonic-db-cli CONFIG_DB hgetall 'PORT_QOS_MAP|Ethernet0'
sonic-db-cli CONFIG_DB hgetall 'TC_TO_PRIORITY_GROUP_MAP|AZURE'
sonic-db-cli CONFIG_DB hgetall 'BUFFER_PG|Ethernet0|3'
show buffer configuration
```

**Fix:** add the missing `BUFFER_PG` with the lossless profile, or `sudo config qos reload` to regenerate from templates and re-merge your overrides.

### Gotcha 4 — `config reload` caused an outage

**Symptom:** you meant to merge one table and the whole data plane reset.

**Why it happens:** `config reload` restarts `swss`/`syncd`; `config load` merges live.

**How to debug:** `show logging | grep -i 'swss\|syncd'` shows the restart.

**Fix:** use `config load -y <file>` for additive changes; reserve `reload` for rebuilds and maintenance windows.

## 5. Hands-on assignment — a `config_db.json` for a RoCE leaf

**Scenario.** You are standardising on NVIDIA SONiC for a storage/GPU leaf. Produce a complete, loadable `config_db.json` for an SN5600 leaf with two 400G host ports and one uplink, lossless RoCE on priority 3 with ECN and PFC watchdog, and prove it loads cleanly.

**Requirements**
1. `DEVICE_METADATA`: hostname `leaf01`, HWSKU `Mellanox-SN5600-C256S1`, type `LeafRouter`, ASN 65011.
2. `PORT`: Ethernet0 and Ethernet4 at 400G, MTU 9100, RS-FEC, admin up; Ethernet248 as the uplink.
3. L3: Loopback0 10.0.0.11/32; Ethernet248 192.168.1.0/31; BGP neighbour 192.168.1.1 AS 65000; Vlan100 with Ethernet0 and Ethernet4 untagged, SVI 10.100.1.1/24.
4. RoCE: DSCP 26→TC 3→PG 3→queue 3; DSCP 48→TC 6 strict; `pfc_enable "3"` on both host ports; lossless `BUFFER_PG` for PG 3 on both; WRED/ECN on queue 3; `PFC_WD` on both host ports (drop, 200/200).
5. The file must be valid JSON and load with `config load` without errors; `config save`.
6. Verification commands captured.

**Deliverables**
- `leaf01-roce.json`.
- Output of `show interfaces status`, `show vlan brief`, `show ip bgp summary`, `show qos` (or the `sonic-db-cli` dumps of `PORT_QOS_MAP|Ethernet0` and `BUFFER_PG|Ethernet0|3`), `show pfcwd config`, `show pfc counters`.
- `python3 -m json.tool leaf01-roce.json > /dev/null && echo OK` output.

**Acceptance criteria**
- JSON parses; `sudo config load -y leaf01-roce.json` returns without errors.
- Both host ports show `pfc_enable 3` and a lossless profile on PG 3.
- `show pfcwd config` lists Ethernet0 and Ethernet4 with action drop.
- `show queue counters Ethernet0` shows queue UC3 and UC6 present.
- After `sudo reboot`, `show runningconfiguration all` still contains the `PFC_WD` and `PORT_QOS_MAP` tables.

### Hint
- Start by dumping the platform's generated QoS after `config qos reload` — copy the real `BUFFER_POOL`/`BUFFER_PROFILE` values rather than inventing `xon`/`xoff`.
- `PORT_QOS_MAP` binds maps *per port*; both Ethernet0 and Ethernet4 need their own entry.
- The compound key format is `Table|Key|Subkey` — a typo like `Ethernet0:3` loads silently and does nothing.
- `show interfaces alias` tells you which panel port Ethernet248 actually is before you cable it.
- `config save` is the only thing between you and acceptance criterion 5.

## Appendix A — Quick command / config reference

```bash
# Config lifecycle
sudo config save -y | sudo config load -y <file> | sudo config reload -y <file>
show runningconfiguration all

# Interfaces / VLAN / LAG
sudo config interface startup|shutdown EthernetN
sudo config interface speed EthernetN 400000; ... mtu EthernetN 9100; ... fec EthernetN rs
sudo config vlan add 100; sudo config vlan member add [-u] 100 EthernetN
sudo config interface ip add Vlan100 10.100.1.1/24
sudo config portchannel add PortChannel1; sudo config portchannel member add PortChannel1 EthernetN
show interfaces status | counters | transceiver eeprom EthernetN; show interfaces alias; show vlan brief

# BGP
sudo config bgp startup neighbor <ip>; show ip bgp summary; show ip route; show bgp neighbors; vtysh

# RoCE
sudo config qos reload; sudo config qos clear; show buffer configuration; show qos
sudo config pfcwd start --action drop --restoration-time 200 EthernetN 200; sudo config pfcwd start_default; sudo config pfcwd stop EthernetN
show pfc counters; show pfc asymmetric; show pfcwd config|stats; show queue counters EthernetN; show queue watermark unicast
sonic-clear pfccounters; sonic-clear queuecounters

# Images / containers
sudo sonic-installer list | install <bin> | set-default <img> | set-next-boot <img> | remove <img>
docker ps; show services; show feature status; sudo systemctl restart <container>; sudo warm-reboot

# Troubleshooting
show techsupport; show logging [-f] [orchagent]; docker logs <container>
redis-cli -n <db> ...; sonic-db-cli <DB> hgetall '<key>'
crm show resources all; show mac; show arp; show lldp table; show interfaces counters errors
```

## Appendix B — Exam traps & key takeaways

- SONiC `config` commands are immediate; there is no pending/apply step. `config save` is still required to persist. NVUE is transactional (pending/apply/save with revisions).
- `config reload` resets the data plane; `config load` merges without restart.
- `EthernetN` is the lane index, not the panel port. Use `show interfaces alias`.
- There is no one-command RoCE on SONiC; lossless RoCE is DSCP→TC→PG maps + `pfc_enable` in PORT_QOS_MAP + lossless buffer profiles with xon/xoff + WRED profile + pfcwd.
- PFC watchdog actions: `drop` (default in most templates), `forward`, `alert`. Know what each does to a stuck queue.
- `orchagent` lives in `swss`; `syncd` plus the SAI library live in `syncd`. Restarting either disrupts forwarding; restarting `bgp` or `lldp` does not.
- CONFIG_DB = DB 4, APPL_DB = DB 0, ASIC_DB = DB 1, STATE_DB = DB 6 — compare the first three when a route "exists but doesn't forward".
- `show techsupport` ≈ `cl-support`; `crm show resources all` ≈ `cl-resource-query`.
- SONiC has multi-image boot (`sonic-installer`); Cumulus does not.
- SAI is the vendor-neutral ASIC API; NVIDIA supplies the Spectrum implementation.
- NVIDIA supports *both* Cumulus and SONiC on Spectrum; Spectrum-X RAs are written around Cumulus. Don't assume SONiC lacks Spectrum support or that Cumulus is "closed".
- `/etc/sonic/config_db.json` persists CONFIG_DB; MTU default 9100; HWSKU defines lanes and default buffers.

## Appendix C — Knowledge check

1. On a SONiC switch a static route appears in `show ip route` and in APPL_DB but is not in ASIC_DB. Which component most likely rejected it?
   A. `fpmsyncd`
   B. `orchagent`
   C. `syncd`
   D. `lldpd`

2. After configuring VLANs with `config vlan add`, an engineer reboots and the VLANs are gone. What was skipped?
   A. `config reload`
   B. `config save`
   C. `sonic-installer set-default`
   D. `systemctl restart swss`

3. Which CONFIG_DB table enables PFC on priority 3 for a port?
   A. `WRED_PROFILE`
   B. `PORT_QOS_MAP` with `pfc_enable: "3"`
   C. `BUFFER_POOL`
   D. `SCHEDULER`

4. A NIC sends continuous PFC pause frames, freezing queue 3 across the fabric. Which SONiC feature limits the damage?
   A. ECN
   B. PFC watchdog (`pfcwd`) with action `drop`
   C. `config qos reload`
   D. `crm`

5. Which SONiC command is the closest equivalent to Cumulus `cl-support`?
   A. `show logging`
   B. `show techsupport`
   C. `docker logs syncd`
   D. `show platform summary`

6. What is the role of SAI?
   A. The REST API for SONiC
   B. The abstraction layer between orchagent and the ASIC vendor's SDK
   C. The Redis database engine
   D. The LACP daemon

7. Which statement correctly contrasts configuration handling?
   A. SONiC has pending/apply; Cumulus applies immediately.
   B. Cumulus NVUE has pending/apply/save with revisions; SONiC `config` applies immediately and requires `config save` to persist.
   C. Both require a reboot to apply changes.
   D. Neither persists configuration across reboots.

8. Which Redis database number holds CONFIG_DB?
   A. 0
   B. 1
   C. 4
   D. 6

### Answer key
1. B — orchagent translates APPL_DB to ASIC_DB; a route missing there was rejected by orchagent.
2. B — `config save` writes CONFIG_DB to `/etc/sonic/config_db.json`.
3. B — `pfc_enable` in PORT_QOS_MAP turns PFC on for the listed priorities.
4. B — pfcwd detects a persistently paused queue and drops/forwards to break the storm.
5. B — `show techsupport` builds the support archive.
6. B — SAI is the vendor-neutral ASIC API; NVIDIA supplies the Spectrum implementation.
7. B — NVUE is transactional; SONiC CLI is immediate.
8. C — CONFIG_DB is DB 4 (APPL_DB 0, ASIC_DB 1, STATE_DB 6).

## References
- SONiC Architecture — https://github.com/sonic-net/SONiC/wiki/Architecture
- SONiC Command Reference — https://github.com/sonic-net/sonic-utilities/blob/master/doc/Command-Reference.md
- SONiC QoS / Buffer configuration and PFC watchdog design docs — https://github.com/sonic-net/SONiC/tree/master/doc
- NVIDIA Networking SONiC documentation and release notes — https://docs.nvidia.com/networking/ (SONiC section)
- NVIDIA Spectrum SAI — https://github.com/Mellanox/SAI-Implementation
- NVIDIA Academy course: SONiC Essentials by NVIDIA
- Related: [`02-cumulus-linux-essentials.md`](02-cumulus-linux-essentials.md), [`03-cumulus-linux-administration.md`](03-cumulus-linux-administration.md), [`../03-rdma/03-roce-a-to-z.md`](../03-rdma/03-roce-a-to-z.md)

---
## Solution guide — Hands-on assignment

### Step 0 — harvest real buffer values

```bash
sudo config qos reload
show runningconfiguration all | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({k:d[k] for k in ("BUFFER_POOL","BUFFER_PROFILE") if k in d}, indent=2))'
```

Copy the `ingress_lossless_pool`, `egress_lossless_pool`, `egress_lossy_pool` sizes and the `pg_lossless_400000_100m_profile` (or the profile matching your cable length) `size`/`xon`/`xoff` into the file below in place of the `"..."` placeholders. Do not invent them: they are platform-calculated.

### `leaf01-roce.json`

```json
{
  "DEVICE_METADATA": {
    "localhost": {
      "hostname": "leaf01",
      "hwsku": "Mellanox-SN5600-C256S1",
      "platform": "x86_64-nvidia_sn5600-r0",
      "type": "LeafRouter",
      "bgp_asn": "65011"
    }
  },
  "PORT": {
    "Ethernet0":   { "alias": "etp1a",  "lanes": "0,1,2,3",         "speed": "400000", "mtu": "9100", "admin_status": "up", "fec": "rs" },
    "Ethernet4":   { "alias": "etp1b",  "lanes": "4,5,6,7",         "speed": "400000", "mtu": "9100", "admin_status": "up", "fec": "rs" },
    "Ethernet248": { "alias": "etp32a", "lanes": "248,249,250,251", "speed": "400000", "mtu": "9100", "admin_status": "up", "fec": "rs" }
  },
  "LOOPBACK_INTERFACE": {
    "Loopback0": {},
    "Loopback0|10.0.0.11/32": {}
  },
  "INTERFACE": {
    "Ethernet248": {},
    "Ethernet248|192.168.1.0/31": {}
  },
  "BGP_NEIGHBOR": {
    "192.168.1.1": { "asn": "65000", "name": "spine01", "admin_status": "up", "local_addr": "192.168.1.0", "holdtime": "180", "keepalive": "60" }
  },
  "VLAN": {
    "Vlan100": { "vlanid": "100" }
  },
  "VLAN_MEMBER": {
    "Vlan100|Ethernet0": { "tagging_mode": "untagged" },
    "Vlan100|Ethernet4": { "tagging_mode": "untagged" }
  },
  "VLAN_INTERFACE": {
    "Vlan100": {},
    "Vlan100|10.100.1.1/24": {}
  },
  "DSCP_TO_TC_MAP": {
    "AZURE": { "0": "0", "26": "3", "48": "6" }
  },
  "TC_TO_QUEUE_MAP": {
    "AZURE": { "0": "0", "3": "3", "6": "6" }
  },
  "TC_TO_PRIORITY_GROUP_MAP": {
    "AZURE": { "0": "0", "3": "3", "6": "6" }
  },
  "PFC_PRIORITY_TO_QUEUE_MAP": {
    "AZURE": { "3": "3" }
  },
  "PORT_QOS_MAP": {
    "Ethernet0": { "dscp_to_tc_map": "AZURE", "tc_to_queue_map": "AZURE", "tc_to_pg_map": "AZURE", "pfc_to_queue_map": "AZURE", "pfc_enable": "3" },
    "Ethernet4": { "dscp_to_tc_map": "AZURE", "tc_to_queue_map": "AZURE", "tc_to_pg_map": "AZURE", "pfc_to_queue_map": "AZURE", "pfc_enable": "3" },
    "Ethernet248": { "dscp_to_tc_map": "AZURE", "tc_to_queue_map": "AZURE", "tc_to_pg_map": "AZURE", "pfc_to_queue_map": "AZURE", "pfc_enable": "3" }
  },
  "BUFFER_POOL": {
    "ingress_lossless_pool": { "type": "ingress", "mode": "dynamic", "size": "..." },
    "egress_lossless_pool":  { "type": "egress",  "mode": "dynamic", "size": "..." },
    "egress_lossy_pool":     { "type": "egress",  "mode": "dynamic", "size": "..." }
  },
  "BUFFER_PROFILE": {
    "pg_lossless_400000_100m_profile": { "pool": "ingress_lossless_pool", "size": "...", "xon": "...", "xoff": "...", "dynamic_th": "0" },
    "ingress_lossy_profile":           { "pool": "ingress_lossless_pool", "size": "0", "dynamic_th": "3" },
    "egress_lossy_profile":            { "pool": "egress_lossy_pool",     "size": "...", "dynamic_th": "3" },
    "egress_lossless_profile":         { "pool": "egress_lossless_pool",  "size": "0", "dynamic_th": "7" }
  },
  "BUFFER_PG": {
    "Ethernet0|0":   { "profile": "ingress_lossy_profile" },
    "Ethernet0|3":   { "profile": "pg_lossless_400000_100m_profile" },
    "Ethernet4|0":   { "profile": "ingress_lossy_profile" },
    "Ethernet4|3":   { "profile": "pg_lossless_400000_100m_profile" },
    "Ethernet248|0": { "profile": "ingress_lossy_profile" },
    "Ethernet248|3": { "profile": "pg_lossless_400000_100m_profile" }
  },
  "BUFFER_QUEUE": {
    "Ethernet0|3":   { "profile": "egress_lossless_profile" },
    "Ethernet4|3":   { "profile": "egress_lossless_profile" },
    "Ethernet248|3": { "profile": "egress_lossless_profile" }
  },
  "WRED_PROFILE": {
    "AZURE_LOSSLESS": { "ecn": "ecn_all", "green_min_threshold": "1000000", "green_max_threshold": "2000000", "green_drop_probability": "5", "wred_green_enable": "true" }
  },
  "SCHEDULER": {
    "scheduler.0": { "type": "STRICT" },
    "scheduler.1": { "type": "DWRR", "weight": "50" }
  },
  "QUEUE": {
    "Ethernet0|3":   { "scheduler": "scheduler.1", "wred_profile": "AZURE_LOSSLESS" },
    "Ethernet0|6":   { "scheduler": "scheduler.0" },
    "Ethernet4|3":   { "scheduler": "scheduler.1", "wred_profile": "AZURE_LOSSLESS" },
    "Ethernet4|6":   { "scheduler": "scheduler.0" },
    "Ethernet248|3": { "scheduler": "scheduler.1", "wred_profile": "AZURE_LOSSLESS" },
    "Ethernet248|6": { "scheduler": "scheduler.0" }
  },
  "PFC_WD": {
    "Ethernet0":   { "action": "drop", "detection_time": "200", "restoration_time": "200" },
    "Ethernet4":   { "action": "drop", "detection_time": "200", "restoration_time": "200" },
    "Ethernet248": { "action": "drop", "detection_time": "200", "restoration_time": "200" },
    "GLOBAL": { "POLL_INTERVAL": "100" }
  }
}
```

Design notes: the uplink Ethernet248 also gets the lossless profile — PFC must be end-to-end or the spine's pause frames are ignored. `BUFFER_QUEUE` on queue 3 with an egress lossless profile is what the platform templates generate; if your `config qos reload` output names them differently, use its names.

### Load and verify

```bash
python3 -m json.tool leaf01-roce.json > /dev/null && echo OK          # criterion: parses
sudo config load -y /home/admin/leaf01-roce.json                      # criterion: no errors
sudo config save -y

show interfaces status                      # Ethernet0/4/248 400G rs up
show vlan brief                             # Vlan100 10.100.1.1/24, Ethernet0 + Ethernet4 untagged
show ip bgp summary                         # 192.168.1.1 65000 Established (once spine is up)
sonic-db-cli CONFIG_DB hgetall 'PORT_QOS_MAP|Ethernet0'      # pfc_enable 3 ...
sonic-db-cli CONFIG_DB hgetall 'PORT_QOS_MAP|Ethernet4'
sonic-db-cli CONFIG_DB hgetall 'BUFFER_PG|Ethernet0|3'       # profile pg_lossless_400000_100m_profile
sonic-db-cli CONFIG_DB hgetall 'BUFFER_PG|Ethernet4|3'
show pfcwd config                           # Ethernet0, Ethernet4, Ethernet248: drop 200 200
show queue counters Ethernet0               # UC0..UC7 rows incl. UC3 and UC6
show pfc counters                           # all zero until traffic — PFC3 column is the one to watch
show buffer configuration
```

### Persistence

```bash
sudo reboot
# after boot
show runningconfiguration all | python3 -c 'import json,sys; d=json.load(sys.stdin); print("PFC_WD" in d, "PORT_QOS_MAP" in d)'
# -> True True
```

### Proving it under load (hardware only)

From a host with a ConnectX/BlueField NIC set to DSCP 26 (`NCCL_IB_TC=106`, `mlnx_qos -i <if> --trust dscp --pfc 0,0,0,1,0,0,0,0`), run `ib_write_bw` toward a peer behind Ethernet4 while a second sender hammers the same destination. `show pfc counters` increments **only** PFC3 Tx on the congested downlink; `show queue counters Ethernet4` shows UC3 bytes rising with zero drops; `show pfcwd stats` stays at zero storms. That is a healthy lossless RoCE leaf.
