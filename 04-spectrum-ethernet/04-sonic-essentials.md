# Module 4.4 — SONiC Essentials (NVIDIA)

> **Exam domain:** NVIDIA Spectrum Networking (30%)  
> **Source course(s):** SONiC Essentials by NVIDIA (NVIDIA Academy)  
> **Source docs:** SONiC community documentation (Architecture, Command Reference, Configuration); NVIDIA Networking SONiC release notes and user manual; NVIDIA Spectrum SAI documentation  
> **Estimated study time:** 4–5 hours

## Learning objectives
- Describe the SONiC architecture: containerized services, SAI, the Redis-based CONFIG_DB / APPL_DB / ASIC_DB, `orchagent`, `syncd` and FRR.
- Explain NVIDIA's role in SONiC (Spectrum SAI, NVIDIA-supported "pure SONiC" distribution) and when to choose SONiC over Cumulus Linux.
- Configure interfaces, VLANs and BGP with the SONiC CLI and `config_db.json`, and manage configuration with `config save/load/reload`.
- Configure RoCE on SONiC: buffer profiles, PFC, PFC watchdog and ECN/WRED, and verify with `show pfc counters`/`show queue counters`.
- Install and upgrade SONiC images with `sonic-installer` and manage service containers.
- Troubleshoot SONiC with `show techsupport`, `show logging`, `redis-cli` and container inspection.
- Compare Cumulus Linux and SONiC for an AI fabric.

## 1. What SONiC is

**SONiC** (Software for Open Networking in the Cloud) is an open-source network OS started by Microsoft for Azure and now governed by the Linux Foundation. Two ideas define it:

1. **Everything is a container.** A Debian host runs Docker; each functional block (BGP, LLDP, SNMP, telemetry, the switch state service, the ASIC sync daemon) is a separate container with its own lifecycle. A crashed BGP container is restarted without touching the data plane.
2. **State lives in Redis.** Instead of config files and inter-process RPCs, every daemon reads and writes tables in a Redis database. Configuration, derived application state and the ASIC's programmed state are three separate databases, and the differences between them are how you troubleshoot.

### 1.1 The database stack
| Database | Redis DB # | Who writes it | What it holds |
|---|---|---|---|
| **CONFIG_DB** | 4 | You (`config` CLI, `config_db.json` load) | Intended configuration: PORT, VLAN, VLAN_MEMBER, INTERFACE, BGP_NEIGHBOR, BUFFER_PROFILE, WRED_PROFILE, PORT_QOS_MAP… |
| **APPL_DB** | 0 | Protocol daemons (FRR via `fpmsyncd`, `portsyncd`, `intfmgrd`, `vlanmgrd`) | Application-level state: routes to install, neighbors, ports as the control plane sees them |
| **ASIC_DB** | 1 | `orchagent` | SAI objects the ASIC *should* contain, in SAI terms (SAI_OBJECT_TYPE_ROUTE_ENTRY…) |
| **STATE_DB** | 6 | Various | Operational state (port oper status, transceiver info, PFC WD status) |
| COUNTERS_DB | 2 | `syncd` via flex counters | Interface/queue/PFC counters |

The flow for a static route: `config route add` → CONFIG_DB → `*mgrd` daemon → APPL_DB → **`orchagent`** (inside the `swss` container) translates it to SAI calls → ASIC_DB → **`syncd`** (inside the `syncd` container) reads ASIC_DB and calls the vendor SAI library → ASIC programmed. If a route is in APPL_DB but not ASIC_DB, orchagent rejected it; if it is in ASIC_DB but not in hardware, syncd/SAI failed.

### 1.2 Key containers and daemons
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

## 2. NVIDIA and SONiC

NVIDIA (Mellanox before acquisition) has been a top SONiC contributor since 2016 and ships:
- **NVIDIA Networking SONiC** (sometimes "NVIDIA pure SONiC"): an NVIDIA-built, tested and supported distribution based on upstream community SONiC with Spectrum SAI, platform drivers and NVIDIA-validated fixes, delivered as `.bin` images for Spectrum switches (SN2000–SN5000). Support comes with the NVIDIA switch support contract.
- Spectrum SAI and platform modules upstreamed to the community tree, so community SONiC also runs on Spectrum.
- Tooling integrations: NetQ agents for SONiC, WJH on SONiC, NVIDIA Air images for SONiC.

### 2.1 When SONiC vs Cumulus Linux
| Choose Cumulus Linux when… | Choose SONiC when… |
|---|---|
| You want a vendor-integrated OS with NVUE, full feature set (EVPN MH, MLAG, RoCE one-liner, AR, telemetry) on day one | Your organisation already standardises on SONiC (hyperscaler/cloud model) and runs it on other vendors' silicon |
| Operational tooling is NVIDIA-centric (NetQ LCM, Air, Ansible `nvidia.nvue`) | You need a fully open-source, auditable stack and the freedom to build your own image |
| The Spectrum-X reference architecture is being followed as published (Cumulus is the primary NOS in Spectrum-X RA documents) | You have in-house development to extend containers/features and manage the faster-moving release cadence |
| Smaller team, wants supported defaults | Large team, config generated by an orchestrator writing `config_db.json` directly |

The exam expects you to know that *both* are NVIDIA-supported on Spectrum and that Cumulus offers the richer, more opinionated feature set, while SONiC offers openness and multi-vendor uniformity.

## 3. Configuration model: `config_db.json`

The CONFIG_DB is persisted as `/etc/sonic/config_db.json`. Everything you configure through `config` commands ends up here. Structure is a JSON object keyed by table, then by object key (pipe-separated for compound keys):

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
Points to remember:
- Interface names are `EthernetN` where N is the *first lane index*, not a port number. `etp1a` style aliases map to front-panel labels; `show interfaces alias` translates. A 4-lane breakout of port 1 yields Ethernet0/4/8/12 on an 8-lane 800G platform — the HWSKU defines this.
- The **HWSKU** (`Mellanox-SN5600-C256S1`, etc.) encodes platform, port configuration and default buffer profile. Changing breakout configuration on SONiC is done via `config interface breakout` (Dynamic Port Breakout) or by selecting a different HWSKU — much more rigid than NVUE's `link breakout`.
- MTU default is 9100 on most SONiC platforms.

### 3.1 Save / load / reload
```bash
sudo config save -y                      # running CONFIG_DB → /etc/sonic/config_db.json
sudo config load -y [file]               # merge file into CONFIG_DB (additive, no restart)
sudo config reload -y [file]             # restart swss/syncd and rebuild CONFIG_DB from file — disruptive
sudo config load_minigraph               # Azure-style XML topology (rare outside hyperscalers)
show runningconfiguration all            # dump CONFIG_DB as JSON
```
Exam trap: `config save` is to SONiC what `nv config save` is to Cumulus — without it, a reboot loses changes. `config reload` is disruptive (data-plane reset); `config load` is not.

## 4. CLI essentials

SONiC has two CLI families: `show` (read) and `config` (write). Both are Python (click) front-ends to Redis.

```bash
# Interfaces
show interfaces status                   # name, lanes, speed, MTU, FEC, alias, oper/admin, type
show interfaces counters
show interfaces transceiver eeprom Ethernet0
show interfaces alias
sudo config interface startup Ethernet0
sudo config interface shutdown Ethernet0
sudo config interface speed Ethernet0 400000
sudo config interface mtu Ethernet0 9100
sudo config interface fec Ethernet0 rs
sudo config interface ip add Ethernet248 192.168.1.0/31
sudo config interface ip add Loopback0 10.0.0.11/32

# VLANs
sudo config vlan add 100
sudo config vlan member add -u 100 Ethernet0      # -u untagged (access); omit for tagged
sudo config interface ip add Vlan100 10.100.1.1/24
show vlan brief

# LAG
sudo config portchannel add PortChannel1
sudo config portchannel member add PortChannel1 Ethernet0
show interfaces portchannel

# BGP
sudo config bgp startup neighbor 192.168.1.1
show ip bgp summary
show ip route
show bgp neighbors
vtysh                                              # FRR directly (inside bgp container, exposed on host)

# System
show version
show platform summary
show platform psustatus | fan | temperature
show environment
show services                                      # per-container service status
show runningconfiguration bgp
```
Unlike Cumulus, SONiC's `config` commands take effect *immediately* (no pending/apply model). There is no built-in diff; the discipline is: change → verify → `config save`.

### 4.1 BGP on SONiC
FRR runs inside the `bgp` container; CONFIG_DB BGP_NEIGHBOR entries are rendered to FRR config by `bgpcfgd` using templates. For anything beyond basic neighbours (route-maps, EVPN, unnumbered), operators either enable the FRR "unified" config mode (`frr_mgmt_framework_config`/`docker_routing_config_mode: split|unified` in DEVICE_METADATA — check current docs) and edit FRR directly with `vtysh`, or use SONiC's newer `config bgp` extensions. BGP unnumbered and EVPN VXLAN are supported in recent SONiC releases but are configured partly in CONFIG_DB (VXLAN_TUNNEL, VXLAN_TUNNEL_MAP, VRF) and partly in FRR.

## 5. RoCE on SONiC: buffers, PFC, PFC watchdog, ECN

SONiC has no single "roce lossless" command. You assemble the pieces that Cumulus's `nv set qos roce` sets for you. This is a favourite exam contrast.

### 5.1 QoS pipeline in CONFIG_DB
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

### 5.2 Representative snippet
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
Values marked `"..."` depend on platform, speed and cable length; NVIDIA ships default buffer templates per HWSKU (`/usr/share/sonic/device/<platform>/<hwsku>/buffers_defaults_t0.j2`) and the lossless profile name encodes speed and cable length (`pg_lossless_400000_100m_profile` = 400G, 100 m). Loading QoS is done with:
```bash
sudo config qos reload              # regenerate QoS/buffer tables from the platform templates
sudo config qos clear
sudo config load -y /etc/sonic/roce_qos.json   # or merge your own
```

### 5.3 PFC watchdog
A **PFC storm** (a NIC stuck sending pause frames) can freeze a lossless queue fabric-wide. **`pfcwd`** detects a queue paused continuously longer than `detection_time` ms and takes an `action` (`drop` the queue's traffic, `forward` ignoring pause, or `alert` only), restoring after `restoration_time` ms without pause.
```bash
sudo config pfcwd start --action drop --restoration-time 200 Ethernet0 200
sudo config pfcwd start_default
sudo config pfcwd stop Ethernet0
show pfcwd config
show pfcwd stats
```
Cumulus has the same concept (`nv set qos pfc-watchdog` / `nv set interface swpN qos pfc-watchdog` — verify object name).

### 5.4 Verify RoCE health
```bash
show pfc counters                    # per port, per priority: Rx/Tx pause frames
show pfc asymmetric
show queue counters Ethernet0        # per-queue packets/bytes, drops — look at UC3 (RoCE) and UC6 (CNP)
show queue watermark unicast         # peak occupancy per queue
show priority-group watermark headroom
show buffer configuration            # effective pools/profiles
show qos                             # maps and bindings (newer releases)
sonic-clear pfccounters; sonic-clear queuecounters
```
Interpretation is identical to Cumulus: Tx pause on a downlink → host is slow; Rx pause on an uplink → spine is pausing you; rising ECN-marked counts with no PFC → congestion control working as intended.

## 6. Images, installation and containers

### 6.1 `sonic-installer`
```bash
sudo sonic-installer list                        # Current, Next, Available images
sudo sonic-installer install /tmp/sonic-mellanox-202405.bin
sudo sonic-installer set-default <image>
sudo sonic-installer set-next-boot <image>
sudo sonic-installer remove <image>
sudo reboot
```
SONiC keeps multiple images on disk and selects via GRUB — a true dual-image capability (unlike Cumulus's single partition). Initial installation uses ONIE exactly as Cumulus does (`onie-nos-install http://.../sonic.bin`). After install, `config_db.json` is carried over from the previous image when upgrading in place; still back it up (`show runningconfiguration all > backup.json`).

### 6.2 Container management
```bash
docker ps                                   # running containers: swss, syncd, bgp, lldp, pmon, database, ...
show services                               # processes inside each container
sudo systemctl restart bgp                  # each container is also a systemd unit
docker exec -it bgp vtysh                   # into FRR
docker exec -it swss bash
docker logs syncd --tail 100
sudo config feature state telemetry enabled # enable optional containers
show feature status
```
Restarting `swss` or `syncd` resets the data plane (like `switchd` restart on Cumulus). Restarting `bgp` or `lldp` does not. SONiC "warm reboot" (`sudo warm-reboot`) and "fast reboot" exist to upgrade with minimal data-plane impact when the platform supports it.

### 6.3 ZTP on SONiC
SONiC ZTP (`ztp` service) uses DHCP option 67 / 239 / provisioning URL to fetch a JSON ZTP spec describing ordered plugins (`firmware`, `config-db-json`, `graphservice`, `connectivity-check`, custom scripts). `show ztp status`, `sudo config ztp enable|disable|run`. Concept is the same as Cumulus; the input is a JSON document rather than a bash script.

## 7. Troubleshooting

```bash
show techsupport                              # tarball under /var/dump/ — the cl-support equivalent
show logging | tail -200                      # /var/log/syslog
show logging -f
show logging orchagent
docker logs swss | grep -i err
redis-cli -n 4 keys 'PORT|*'                  # CONFIG_DB
redis-cli -n 0 hgetall 'ROUTE_TABLE:10.1.1.0/24'   # APPL_DB: what the control plane asked for
redis-cli -n 1 keys 'ASIC_STATE:SAI_OBJECT_TYPE_ROUTE_ENTRY*' | wc -l   # ASIC_DB: what orchagent programmed
sonic-db-cli CONFIG_DB hgetall 'PORT|Ethernet0'    # friendlier wrapper
show ip route 10.1.1.0/24
show mac
show arp
show lldp table
show interfaces counters errors
show platform ssdhealth
```
Diagnostic logic: compare the three databases. Route in CONFIG/FRR but missing from APPL_DB → FRR/fpmsyncd problem (`show ip route` in vtysh, `docker logs bgp`). In APPL_DB but not ASIC_DB → orchagent rejected (check `show logging orchagent` for SAI errors, often table-full or invalid next hop). In ASIC_DB but not forwarding → syncd/SAI/hardware (`docker logs syncd`). NVIDIA SONiC also supports **WJH** (`show what-just-happened` — verify availability per release) and the NetQ agent for the same drop-reason visibility as Cumulus.

## 8. Cumulus Linux vs SONiC

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
| ASIC resources | `cl-resource-query` | `show platform ...`/`crm` (Critical Resource Monitoring: `crm show resources all`) |
| Telemetry | NetQ, WJH, gNMI, OTLP | NetQ, WJH (NVIDIA builds), gNMI telemetry container |
| Automation | `nvidia.nvue` Ansible collection, REST | `sonic-net` community Ansible roles, generic `copy` of config_db.json, gNMI |
| Best fit | NVIDIA-centric AI fabrics following Spectrum-X RA | Multi-vendor, hyperscaler-style operations |

`crm` (Critical Resource Monitoring) is SONiC's analogue of **cl-resource-query**: `crm show resources all` shows used/available for routes, nexthops, FDB, ACL, with configurable thresholds that raise syslog alerts.

## Configuration / command reference

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
show interfaces status | counters | transceiver eeprom EthernetN; show vlan brief

# BGP
show ip bgp summary; show ip route; vtysh

# RoCE
sudo config qos reload; show buffer configuration; show qos
sudo config pfcwd start --action drop --restoration-time 200 EthernetN 200
show pfc counters; show pfcwd stats; show queue counters EthernetN; show queue watermark unicast

# Images / containers
sudo sonic-installer list | install <bin> | set-default <img>
docker ps; show services; show feature status; sudo systemctl restart <container>

# Troubleshooting
show techsupport; show logging; redis-cli -n <db> ...; sonic-db-cli <DB> hgetall '<key>'
crm show resources all
```

## Common pitfalls & exam traps
- SONiC `config` commands are immediate; there is no pending/apply step. `config save` is still required to persist.
- `config reload` resets the data plane; `config load` merges without restart.
- `EthernetN` is the lane index, not the panel port. Use `show interfaces alias`.
- There is no one-command RoCE on SONiC; lossless RoCE is buffer profiles + PFC enable in PORT_QOS_MAP + WRED profile + pfcwd.
- PFC watchdog actions: `drop` (default in most templates), `forward`, `alert`. Know what each does to a stuck queue.
- `orchagent` lives in `swss`; `syncd` plus the SAI library live in `syncd`. Restarting either disrupts forwarding.
- CONFIG_DB = DB 4, APPL_DB = DB 0, ASIC_DB = DB 1 — the three you compare when a route "exists but doesn't forward".
- `show techsupport` ≈ `cl-support`; `crm` ≈ `cl-resource-query`.
- SONiC has multi-image boot (`sonic-installer`); Cumulus does not.
- NVIDIA supports *both* Cumulus and SONiC on Spectrum; Spectrum-X RAs are written around Cumulus. Don't assume SONiC lacks Spectrum support or that Cumulus is "closed".

## Key takeaways
- SONiC = Debian host + Docker containers + Redis state + SAI abstraction; FRR in the `bgp` container.
- CONFIG_DB (intent) → APPL_DB (control plane) → ASIC_DB (SAI objects) → hardware via `syncd`.
- `/etc/sonic/config_db.json` persists CONFIG_DB; `config save/load/reload` manage it.
- NVIDIA ships a supported SONiC distribution with Spectrum SAI; community SONiC runs on Spectrum too.
- Choose Cumulus for an NVIDIA-integrated feature-rich fabric; SONiC for open, multi-vendor uniformity.
- RoCE on SONiC: DSCP→TC→PG maps, `pfc_enable`, lossless buffer profiles with xon/xoff, WRED/ECN, `pfcwd`.
- `show pfc counters`, `show queue counters`, `show queue watermark` verify lossless behaviour.
- `sonic-installer` manages multiple on-disk images; ONIE performs first install.
- Troubleshoot by comparing Redis DBs with `redis-cli`/`sonic-db-cli`; collect `show techsupport`.
- `crm show resources all` is SONiC's ASIC-table utilization view.

## Review questions

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
