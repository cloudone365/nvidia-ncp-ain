# Module 4.3 — Cumulus Linux Administration

> **Exam domain:** NVIDIA Spectrum Networking (30%)  
> **Source course(s):** Cumulus Linux Administration (NVIDIA Academy)  
> **Source docs:** Cumulus Linux User Guide — Installation Management, Upgrading, Authentication/AAA, Management VRF, Monitoring and Troubleshooting, Zero Touch Provisioning, Netfilter/ACLs, Quality of Service; NVUE Command Reference  
> **Estimated study time:** 5–6 hours

## Learning objectives
- Describe the two Cumulus upgrade paths (full image install vs package upgrade) and perform a config backup/restore around them.
- Configure local users, roles and TACACS+/RADIUS authentication with `nv set system aaa`.
- Configure the management VRF and core services (NTP, DNS, syslog, SNMP) using NVUE.
- Monitor hardware health and system resources with `nv show platform`, `smonctl`, `nv show system cpu/memory`.
- Read **cl-resource-query** output and explain why ASIC table utilization matters for scale.
- Configure basic ACLs and non-RoCE QoS with `nv set acl` / `nv set qos`.
- Apply a structured troubleshooting workflow from link to ASIC, and collect `cl-support` for escalation.
- Describe ZTP in depth, including DHCP options and script responsibilities.

## 1. Upgrades and image management

Cumulus Linux offers two upgrade methods. The exam asks you to pick the right one.

| Method | Command | What it does | When |
|---|---|---|---|
| **Binary image install** | `sudo onie-select -i` then reboot into ONIE, or `nv action upgrade system packages`/`nv action upgrade image` (5.x; verify exact path), or NetQ LCM | Wipes the OS partition and installs a fresh image. All config is lost unless restored. | Major version jumps (4.x → 5.x), hardware re-use, corrupted systems, or when the release notes require it |
| **Package upgrade** | `sudo apt update && sudo apt upgrade` (or `nv action upgrade system packages`) | Upgrades installed Debian packages in place; config preserved | Minor/maintenance releases within the same major train (5.9 → 5.10) when release notes permit |

Rules of thumb from the docs: package upgrade is only supported within a major release and only when the release notes do not state otherwise; a binary install is always safe but requires config restore. Always read the release notes for the target version first — some minor versions have had "image install required" notes because of kernel or SDK changes.

### 1.1 Backup before any upgrade
```bash
nv config save                                    # make startup.yaml current
nv config show -o commands > /tmp/leaf01-cmds.txt  # human-readable backup
nv config show -o yaml     > /tmp/leaf01.yaml      # restorable backup
scp /tmp/leaf01.yaml /etc/nvue.d/startup.yaml admin@backup-host:/backups/
# Non-NVUE items worth saving: /etc/ssh keys, /etc/passwd+/etc/shadow (local users), NetQ agent config, ZTP scripts
```
Restore after a fresh image:
```bash
scp admin@backup-host:/backups/leaf01.yaml /tmp/
nv config replace /tmp/leaf01.yaml
nv config apply -y
nv config save
```
Because the YAML is a full declarative description, `replace` + `apply` recreates the switch in one step. This is also how NetQ LCM and Ansible drive upgrades at scale (Module 4.5, Module 6.1).

### 1.2 NVUE upgrade actions (5.x)
```bash
nv action upgrade system packages to-version 5.11.0 use-vrf mgmt     # package upgrade; path varies by release
nv show system image                                                  # installed/active image slots (where supported)
nv action reboot system
```
Spectrum switches have a single OS partition by default (unlike some vendors' dual-image boot). ONIE handles the install/rescue flow. Confirm `nv show system` reports the expected version after reboot and `systemctl status switchd` is active.

## 2. Users, roles and AAA

### 2.1 Local users
```bash
nv set system aaa user netops full-name "Network Ops"
nv set system aaa user netops password 'S3cureP@ss'
nv set system aaa user netops role system-admin
nv set system aaa user auditor role nvue-monitor
nv config apply -y
nv show system aaa user
```
Built-in roles (verify names for your release): `system-admin` (full, includes sudo), `nvue-admin` (all NVUE objects, no shell sudo), `nvue-monitor` (show only). Custom roles can be built from NVUE object paths with `nv set system aaa role <name> class <class>` granting `read`/`write`/`all` to sets of object trees — useful for giving a QoS team access to `qos` without `router`.

### 2.2 TACACS+ and RADIUS
```bash
# TACACS+
nv set system aaa tacacs enable on
nv set system aaa tacacs server 1 host 10.10.0.5
nv set system aaa tacacs server 1 secret 'tacacskey'
nv set system aaa tacacs vrf mgmt
nv set system aaa tacacs authentication mode pap          # or chap/login — check doc
nv set system aaa tacacs accounting enable on
nv set system aaa authentication-order tacacs local       # fallback to local

# RADIUS
nv set system aaa radius enable on
nv set system aaa radius server 1 host 10.10.0.6
nv set system aaa radius server 1 secret 'radiuskey'
nv set system aaa radius vrf mgmt
nv config apply -y
```
Exam framing: AAA servers are almost always reached through the **management VRF**, so the `vrf mgmt` attribute matters; a working TACACS config that times out is usually missing it. TACACS+ maps the server's privilege level (`priv-lvl 15` → admin) to local roles; RADIUS uses the `shell:roles` or vendor attribute. Keep a local fallback account.

### 2.3 SSH and the default account
Change the `cumulus` password at first login (forced). Disable password auth and use keys where policy requires (`nv set system ssh-server ...` in newer releases, or `/etc/ssh/sshd_config` on older). Login banner: `nv set system message pre-login "..."`.

## 3. Management VRF

The **mgmt VRF** isolates out-of-band management (`eth0`) from the data plane so that a leaked default route in the fabric cannot hijack SSH, and so that management traffic has its own routing table. It is enabled by default on Cumulus 4.x/5.x.

```bash
nv set interface eth0 ip address 10.10.1.11/24
nv set interface eth0 ip vrf mgmt                       # usually already the case
nv set vrf mgmt router static 0.0.0.0/0 via 10.10.1.1
nv set interface eth0 ip address dhcp                   # alternative
nv config apply -y
```
Consequences you must know:
- Services that must talk out-of-band need to be told which VRF: `nv set service ntp mgmt ...`, `nv set service syslog mgmt ...`, `netq config add server ... vrf mgmt`, AAA `vrf mgmt`.
- From the shell, `ping 8.8.8.8` runs in the default VRF; use `ip vrf exec mgmt ping 8.8.8.8` (or `ping -I mgmt`). SSH *into* the switch arrives on eth0 in mgmt VRF automatically.
- `nv show vrf mgmt router rib ipv4` to verify the mgmt route table.

## 4. System services: NTP, DNS, syslog, SNMP

```bash
# NTP (per VRF)
nv set service ntp mgmt server 0.pool.ntp.org iburst on
nv set service ntp mgmt server 10.10.0.10 iburst on
nv set service ntp mgmt listen eth0

# DNS
nv set service dns mgmt server 10.10.0.53
nv set service dns mgmt search lab.example.com
nv set system hostname leaf01

# Syslog (remote)
nv set service syslog mgmt server 10.10.0.20 port 514 protocol udp
nv set service syslog mgmt server 10.10.0.20 severity info     # where supported

# SNMP
nv set service snmp-server enable on
nv set service snmp-server listening-address 10.10.1.11 vrf mgmt
nv set service snmp-server readonly-community public access any
nv set service snmp-server username snmpv3user auth-sha 'authpass' encrypt-aes 'privpass'
nv set service snmp-server trap-destination 10.10.0.30 community-password public version 2c

nv config apply -y
nv show service ntp mgmt
nv show service syslog
nv show service snmp-server
```
Time sync matters beyond logs: NetQ correlates events across devices, certificates for the NVUE API/gNMI expire by date, and PTP (`nv set service ptp`) for time-sensitive workloads depends on a sane system clock. `chronyc sources` and `ntpq -p` (depending on the release's NTP daemon) confirm sync at the Linux level.

### 4.1 Streaming telemetry / gNMI
Cumulus 5.x exposes **gNMI** (gRPC Network Management Interface) and OpenTelemetry streaming:
```bash
nv set system gnmi-server state enabled          # verify object name for your release
nv set system gnmi-server port 9339
nv set system telemetry export otlp ...          # OTLP exporter
nv show system gnmi-server
```
This complements NetQ (push vs pull) and is how third-party collectors (Prometheus via gNMIc, Grafana) consume interface counters, buffer histograms and WJH events without polling SNMP.

## 5. Linux services and system health

```bash
systemctl status switchd            # ASIC programming daemon
systemctl status frr                # routing
systemctl status nvued              # NVUE API/CLI backend
systemctl status netq-agent         # NetQ agent
sudo journalctl -u switchd -f
nv show system                      # hostname, platform, version, uptime
nv show system cpu
nv show system memory
nv show system disk                 # where supported
```
`switchd` crashing or restarting is the single most disruptive event on a Cumulus switch; every restart reprograms the ASIC (seconds of outage). `nv show system` plus `journalctl -u switchd` are where you confirm whether a "mystery blip" lined up with a switchd restart (e.g., after a breakout or AR change on older releases — see Module 4.1).

### 5.1 Hardware monitoring
```bash
nv show platform                    # model, serial, part number, firmware
nv show platform hardware
nv show platform environment fan
nv show platform environment psu
nv show platform environment sensor      # temperatures, voltages
nv show platform environment led
smonctl                             # legacy sensor monitor: temps, fans, PSUs with OK/alarm
smonctl -v
sudo decode-syseeprom               # ONIE EEPROM: serial, MAC base, platform name
```
Transceiver diagnostics (DOM — Tx/Rx power, temperature) are in `nv show interface swp1 link` / `nv show interface swp1 transceiver` and, in more depth, `mlxlink` (Module 8.x). PSU and fan alarms are exported as events to NetQ and as syslog messages.

## 6. cl-resource-query — ASIC table utilization

The Spectrum ASIC has finite hardware tables. When a table fills, new entries are *not* installed in hardware; the kernel still knows about them, so the config looks fine, but traffic for those entries is either dropped or punted to the CPU. **cl-resource-query** is the tool that shows table occupancy.

```bash
sudo cl-resource-query
```
Representative output (values illustrative; your platform and profile differ):
```text
Host entries:            1250,   16384   7.6%
IPv4 route entries:     41200,   81920  50.3%
IPv6 route entries:       120,   16384   0.7%
ECMP nexthops:            420,    4096  10.3%
ECMP groups:               64,     512  12.5%
MAC entries:            12500,   40960  30.5%
Ingress ACL entries:      310,    2048  15.1%
Egress ACL entries:        16,    1024   1.6%
Total routes:           41320,   98304  42.0%
```
Also: `nv show system forwarding resources` or `nv show platform forwarding-table` in newer releases exposes the same data through NVUE (verify object name).

How to read it:
- **Host entries** — directly connected neighbours (ARP/ND). In a large L2 domain or a VXLAN fabric with ARP suppression, this fills first.
- **IPv4/IPv6 route entries** — LPM routes. A full BGP table will not fit; a leaf should only carry fabric prefixes and tenant summaries.
- **ECMP nexthops / groups** — each unique set of next hops consumes a group. Many VRFs × many prefixes with different next-hop sets exhausts groups long before routes.
- **MAC entries** — bridge FDB. VXLAN EVPN fabrics with large L2 tenants hit this.
- **ACL entries** — TCAM slices; each NVUE ACL rule may consume several entries, and a rule with a port range expands into many.

Why the exam cares: when utilization approaches 100% you get silent software forwarding or drops, high CPU, and `switchd` log messages like "failed to add route — table full". The fix is architectural: route summarisation, fewer VRFs/VNIs per switch, moving to L3-only designs, enabling a different forwarding **profile** that trades table sizes (`nv set system forwarding profile`, e.g. optimising for more routes vs more hosts — check current docs for available profiles). Mention of the forwarding profile is a frequent exam item: the profile reallocates shared ASIC memory between route, host and ACL tables and requires a `switchd` restart.

Check `cl-resource-query` whenever: a new prefix exists in `ip route` but is unreachable; `nv show vrf default router rib` shows the route but traffic is CPU-forwarded; adding an ACL fails; or NetQ reports a resource-utilization threshold event.

## 7. ACLs

NVUE ACLs are stateless filters programmed into the ASIC TCAM.
```bash
nv set acl mgmt-filter type ipv4
nv set acl mgmt-filter rule 10 match ip source-ip 10.10.0.0/24
nv set acl mgmt-filter rule 10 match ip protocol tcp
nv set acl mgmt-filter rule 10 match ip dest-port 22
nv set acl mgmt-filter rule 10 action permit
nv set acl mgmt-filter rule 20 match ip protocol tcp
nv set acl mgmt-filter rule 20 match ip dest-port 22
nv set acl mgmt-filter rule 20 action deny
nv set acl mgmt-filter rule 20 action log           # where supported
nv set interface swp1 acl mgmt-filter inbound
nv config apply -y
nv show acl mgmt-filter
nv show acl mgmt-filter statistics
```
Types: `ipv4`, `ipv6`, `mac`. Direction: `inbound` or `outbound`. Control-plane protection ACLs (`nv set acl ... ` applied to `control-plane`) are supported for protecting the CPU. ACL entries consume TCAM — visible in `cl-resource-query`; a long rule list with port ranges expands quickly. Under the hood NVUE renders `iptables`/`ebtables` rules into `/etc/cumulus/acl/policy.d/` and `cl-acltool` installs them into hardware; `sudo cl-acltool -L ipv4` lists what is programmed.

## 8. QoS basics outside RoCE

RoCE has its own one-line profile (Module 4.1). General QoS objects it builds on:

| Object | Purpose | Example |
|---|---|---|
| Trust | Which marking to honour at ingress | `nv set qos mapping default-global trust l3` (DSCP) or `l2` (PCP) or `port` |
| Mapping | DSCP/PCP → switch-priority (0–7) | `nv set qos mapping default-global dscp 46 switch-priority 5` |
| Egress queue mapping | switch-priority → traffic class (queue) | `nv set qos egress-queue-mapping default-global switch-priority 5 traffic-class 5` |
| ETS / scheduler | Bandwidth per traffic class (DWRR or strict) | `nv set qos egress-scheduler default-global traffic-class 5 mode strict` / `... bw-percent 20` |
| Remark | Rewrite DSCP/PCP on egress | `nv set qos remark default-global ...` |
| Shaper / policer | Rate limit per port/TC | `nv set qos egress-shaper default-global traffic-class 2 max-rate 100000` |
| Congestion control (ECN/WRED) | Marking per TC | `nv set qos congestion-control default-global traffic-class 3 ecn enable` |
| PFC | Lossless per priority | `nv set qos pfc default-global switch-priority 3` |
| Buffer | Ingress/egress pools, headroom | `nv set qos traffic-pool ...` |

Profiles (`default-global`, or a named profile) are bound to interfaces: `nv set interface swp1 qos egress-scheduler profile my-sched`. Verify with `nv show qos ...` and `nv show interface swp1 qos`. For the exam, remember the pipeline order: ingress trust → switch-priority → (PFC applies per SP) → traffic class → scheduler/shaper → remark.

## 9. NetQ agent and log collection

```bash
# NetQ agent on the switch (full detail in Module 4.5)
sudo netq config add server 10.10.0.100 port 31980 vrf mgmt
sudo netq config restart agent
netq show agents            # from NetQ CLI
sudo netq config show
```
```bash
# Support bundle for NVIDIA support
sudo cl-support                       # creates /var/support/cl_support_<host>_<date>.txz
sudo cl-support -h                    # options; -e to include extra
```
**cl-support** gathers logs, configs, `cl-resource-query`, `switchd` state, FRR dumps, hardware info and core files. Collect it *while the problem is occurring* when possible; some ASIC counters are not retained.

## 10. Core troubleshooting workflow

Work bottom-up and verify each layer before moving on. Each step has a Cumulus-specific command:

1. **Link** — `nv show interface swp1 link` (admin/oper state, speed, FEC, autoneg), `nv show interface swp1 counters` (CRC, errors), `nv show interface swp1 transceiver` / `mlxlink -d ... -m` for optics. Mismatched FEC or speed is the #1 cause of a down 400G/800G link.
2. **LLDP / cabling** — `nv show interface swp1 lldp` confirms you are plugged into what the design says.
3. **Bridge / VLAN** — `nv show bridge domain br_default vlan` (is the VLAN on this port?), `nv show bridge domain br_default mac-table` (is the MAC learned on the expected port/VLAN?), `bridge fdb show`. For MLAG: `nv show mlag consistency-checker`.
4. **L3** — `nv show vrf <v> router rib ipv4` (route present?), `nv show vrf <v> router bgp neighbor` (session Established?), `ip neigh show` (ARP resolved?), `nv show interface vlan10` (SVI up and in correct VRF?).
5. **ASIC** — `sudo cl-resource-query` (table full?), `nv show router ...` for hardware offload flags; `systemctl status switchd` and `journalctl -u switchd` for programming errors.
6. **Counters and drops** — `nv show interface swp1 counters` deltas, `nv show interface swp1 qos roce counters` for PFC/ECN, **WJH** via NetQ (`netq show wjh-drop`) for *why* a packet dropped.
7. **Escalate** — `cl-support`, attach to NVIDIA Enterprise Support case.

A disciplined candidate uses this order on exam scenarios: a question describing "VLAN present, MAC not learned, link up" is a step-3 bridge issue, not a routing issue.

## 11. ZTP in depth

**ZTP** runs automatically on first boot (or when `ztp -e` is set and the switch has no saved config). Discovery order:
1. USB drive containing `cumulus-ztp` or `cumulus-ztp.sh`.
2. DHCP on `eth0` (mgmt VRF): option **239** (`cumulus-provision-url`) with the script URL; option 114 is for ONIE installer URL, not ZTP. Also supports the `user-class`/`vendor-class` identifiers for DHCP server matching (`vendor-class-identifier "cumulus-linux"`).
3. If the switch gets a hostname/DNS, default URL patterns on the provisioning server.

The script must start with `#!/bin/bash` (or Python) and include the marker comment `# CUMULUS-AUTOPROVISIONING` or ZTP refuses to run it. Typical responsibilities:
```bash
#!/bin/bash
# CUMULUS-AUTOPROVISIONING
set -e
# 1. Optionally upgrade the image first (ZTP re-runs after reboot)
# onie-install -a -f -i http://10.10.0.50/images/cumulus-linux-5.11-mlx-amd64.bin && reboot
# 2. Set credentials
echo 'cumulus:NewP@ssw0rd' | chpasswd
# 3. Fetch NVUE config by serial or hostname and apply
SERIAL=$(decode-syseeprom -e)
curl -fsSL http://10.10.0.50/nvue/${SERIAL}.yaml -o /tmp/startup.yaml
nv config replace /tmp/startup.yaml
nv config apply -y
nv config save
# 4. Install NetQ agent and point it at the server
netq config add server 10.10.0.100 port 31980 vrf mgmt
netq config restart agent
exit 0
```
Commands: `ztp -s http://...` (run now), `ztp -e` enable, `ztp -d` disable, `ztp -R` reset state, `ztp -i` status (`nv show system ztp` in newer releases). Logs in `/var/log/ztp.log` and syslog. ZTP exits on the first non-zero return code — use `set -e` deliberately. In AI data centers, ZTP combined with a serial-number-keyed YAML repository (git) means hundreds of leaves come up identical and auditable; Module 6.1 covers the pipeline side.

## Configuration / command reference

```bash
# Upgrade / backup
nv config save; nv config show -o yaml > backup.yaml; nv config show -o commands
nv config replace backup.yaml && nv config apply -y && nv config save
sudo onie-select -i && sudo reboot            # image reinstall via ONIE
nv action upgrade system packages ...         # verify exact path in current docs

# AAA
nv set system aaa user <u> password '<p>'; nv set system aaa user <u> role system-admin|nvue-admin|nvue-monitor
nv set system aaa tacacs enable on; ... server 1 host <ip>; ... secret '<s>'; ... vrf mgmt
nv set system aaa radius enable on; ... server 1 host <ip>; ... secret '<s>'
nv set system aaa authentication-order tacacs local

# Mgmt VRF
nv set interface eth0 ip address <ip/len>; nv set interface eth0 ip vrf mgmt
nv set vrf mgmt router static 0.0.0.0/0 via <gw>
ip vrf exec mgmt <command>

# Services
nv set service ntp mgmt server <ip> iburst on
nv set service dns mgmt server <ip>
nv set service syslog mgmt server <ip> port 514 protocol udp
nv set service snmp-server enable on; ... listening-address <ip> vrf mgmt; ... readonly-community <c> access any

# Health
nv show system | cpu | memory
nv show platform | environment fan | psu | sensor
smonctl; sudo decode-syseeprom
systemctl status switchd frr nvued netq-agent

# ASIC resources
sudo cl-resource-query
nv set system forwarding profile <profile>       # table allocation; restarts switchd

# ACL
nv set acl <n> type ipv4; nv set acl <n> rule 10 match ip ...; nv set acl <n> rule 10 action permit|deny
nv set interface swpN acl <n> inbound

# Support / ZTP
sudo cl-support
ztp -s <url> | ztp -e | ztp -d | ztp -i
```

## Common pitfalls & exam traps
- Package upgrade across major versions is unsupported; a binary image install is required (and wipes config — back up with `nv config show -o yaml`).
- Forgetting `vrf mgmt` on NTP/syslog/TACACS/NetQ when eth0 is in the management VRF — the service silently fails.
- `ping` from the shell runs in the default VRF; use `ip vrf exec mgmt`.
- `cl-resource-query` shows *ASIC* occupancy; `ip route` and `nv show ... rib` show kernel/FRR state. A route can be in the RIB and absent from hardware.
- Changing the forwarding profile reallocates tables and restarts `switchd`.
- ZTP scripts must contain `# CUMULUS-AUTOPROVISIONING`; DHCP option 239 (not 114) carries the ZTP URL.
- `nvue-monitor` role cannot apply; `nvue-admin` can apply but has no shell sudo; `system-admin` has both.
- ACL rules with port ranges expand into many TCAM entries — count them against `cl-resource-query`.
- `cl-support` is the support bundle; `smonctl` is sensors; `decode-syseeprom` is serial/platform — three different tools the exam mixes up.
- `nv show system` reports the *running* version; after a package upgrade you may need a reboot for the new kernel/switchd.

## Key takeaways
- Two upgrade paths: image install (ONIE, config lost) vs package upgrade (same major, config kept); always back up with `nv config show -o yaml`.
- `nv config replace` + `apply` + `save` restores a switch from YAML in three commands.
- Roles: `system-admin`, `nvue-admin`, `nvue-monitor`; TACACS+/RADIUS via `nv set system aaa` with `vrf mgmt`.
- The mgmt VRF isolates eth0; every OOB service must name it.
- `nv set service ntp|dns|syslog|snmp-server` configure core services; gNMI/OTLP for streaming telemetry.
- `nv show platform environment`, `smonctl`, `nv show system cpu/memory` for health.
- **cl-resource-query** reveals ASIC table occupancy; full tables mean silent CPU forwarding or drops; forwarding profiles rebalance tables.
- ACLs are TCAM entries applied inbound/outbound per interface; `nv set acl`.
- Troubleshoot bottom-up: link → LLDP → bridge → L3 → ASIC → counters/WJH → `cl-support`.
- ZTP: USB or DHCP option 239, script with `# CUMULUS-AUTOPROVISIONING`, typically pulls YAML and runs `nv config replace`.

## Review questions

1. A switch running Cumulus Linux 4.4 must move to 5.10. Which approach is correct?
   A. `sudo apt upgrade`
   B. Binary image install via ONIE, then restore configuration from a saved NVUE YAML
   C. `nv config apply --upgrade`
   D. ZTP will upgrade it automatically on the next reboot

2. TACACS+ is configured with the correct server and key, but logins time out and fall back to local. `eth0` is in the mgmt VRF. What is missing?
   A. `nv set system aaa tacacs vrf mgmt`
   B. `nv set system aaa tacacs enable on`
   C. A RADIUS fallback
   D. The `system-admin` role

3. A new tenant prefix appears in `nv show vrf blue router rib ipv4` but hosts behind it are unreachable, and `switchd` logs show table-full errors. Which tool confirms the diagnosis?
   A. `cl-support`
   B. `smonctl`
   C. `cl-resource-query`
   D. `decode-syseeprom`

4. Which DHCP option carries the ZTP script URL for Cumulus Linux?
   A. 66
   B. 114
   C. 150
   D. 239

5. A ZTP script downloads and runs but does nothing; `/var/log/ztp.log` says the script was rejected. Most likely cause?
   A. Missing `# CUMULUS-AUTOPROVISIONING` marker
   B. Script is in bash rather than Python
   C. The URL used HTTPS
   D. eth0 is in the mgmt VRF

6. Which role lets a user run `nv set` and `nv config apply` but not obtain a root shell with `sudo`?
   A. `nvue-monitor`
   B. `nvue-admin`
   C. `system-admin`
   D. `cumulus`

7. Which command produces the archive NVIDIA Support asks for?
   A. `nv show system`
   B. `cl-support`
   C. `smonctl -v`
   D. `nv config show -o commands`

8. You change the ASIC forwarding profile to allow more IPv4 routes. What side effect should you expect?
   A. BGP ASN changes
   B. `switchd` restarts and forwarding is interrupted briefly
   C. The mgmt VRF is disabled
   D. All ACLs are removed permanently

### Answer key
1. B — Major-version jumps require an image install; restore config via `nv config replace`.
2. A — AAA servers reached through eth0 need the `vrf mgmt` attribute.
3. C — `cl-resource-query` shows hardware table utilization.
4. D — Option 239 (`cumulus-provision-url`); 114 is ONIE's installer URL.
5. A — ZTP refuses scripts lacking the autoprovisioning marker.
6. B — `nvue-admin` has full NVUE rights without shell sudo.
7. B — `cl-support` builds the support bundle.
8. B — Changing table allocation reprograms the ASIC and restarts `switchd`.

## References
- Cumulus Linux User Guide: Upgrading Cumulus Linux — https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/Installation-Management/Upgrading-Cumulus-Linux/
- Cumulus Linux: Authentication, Authorization and Accounting (TACACS, RADIUS, User Accounts) — https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/System-Configuration/Authentication-Authorization-and-Accounting/
- Cumulus Linux: Management VRF — https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/Layer-3/VRFs/Management-VRF/
- Cumulus Linux: Monitoring and Troubleshooting; Resource Diagnostics (cl-resource-query) — https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/Monitoring-and-Troubleshooting/
- Cumulus Linux: Zero Touch Provisioning — https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/Installation-Management/Zero-Touch-Provisioning-ZTP/
- Cumulus Linux: Access Control List Configuration; Quality of Service
- NVIDIA Academy course: Cumulus Linux Administration
- Related: [`02-cumulus-linux-essentials.md`](02-cumulus-linux-essentials.md), [`05-netq-deployment-and-installation.md`](05-netq-deployment-and-installation.md), [`../08-troubleshooting/`](../08-troubleshooting/)
