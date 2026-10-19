# Module 8.4 — What Just Happened (WJH), NetQ, Cumulus and UFM Troubleshooting

> **Exam domain:** Troubleshooting Tools (20%)  
> **Source course(s):** Cumulus Linux Administration; NetQ Fundamentals; UFM Essentials (troubleshooting units)  
> **Source docs:** Cumulus Linux User Guide — "What Just Happened (WJH)", "Monitoring and Troubleshooting"; NVUE Command Reference (WJH); NetQ User Guide — "Monitor WJH Events", "Monitor the RoCE Service", "Validation Checks"; UFM Enterprise User Manual — "Fabric Health", "System Dump"  
> **Estimated study time:** 4 hours

## Learning objectives
- Describe what **WJH** adds over conventional counters: per-packet drop reason, ingress/egress port, and header fields, generated in the Spectrum ASIC.
- Enable and scope WJH with NVUE channels and triggers (`nv set system wjh …`) and read drops locally (`nv show system wjh …`, `nv-wjh-cli`).
- Collect and query WJH drops centrally in **NetQ** (`netq show wjh-drop …`) and filter by type, reason, port and time.
- Run NetQ validation suites (`netq check …`), event, interface, BGP/EVPN, and RoCE commands to validate a RoCE fabric end to end.
- Use Cumulus-local tools (`nv show interface … counters/link`, `nv show qos roce`, `vtysh`, `cl-support`, `switchd.log`, **cl-resource-query**) when NetQ is unavailable.
- Use **UFM** health reports, events, telemetry and system dump for InfiniBand fabrics, and isolate a port from UFM.
- Work a slow-NCCL scenario end to end and choose the right tool for each symptom across Modules 8.1–8.4.

## 1. What Just Happened (WJH)

### 1.1 The problem WJH solves

Traditional switch troubleshooting gives you a counter: "RX discards: 4,212". Not *which* packets, not *why*, not *where they were going*. Finding the cause means mirroring ports, guessing filters, and correlating timestamps. **WJH** is a Spectrum ASIC feature that, for every dropped (or congestion-affected) packet, records the **drop reason**, ingress and egress port, timestamp and the packet headers themselves (optionally a truncated copy of the packet). On Cumulus Linux the WJH service (`nv-wjh.service` in 5.18+, `what-just-happened.service` earlier) collects these from the ASIC and aggregates them; the **NetQ** agent can forward them to a NetQ server for fabric-wide search.

The key exam point: WJH is **ASIC-level telemetry, not sampling and not a counter**. It tells you *what just happened* to a specific packet.

### 1.2 Drop categories and reasons

| Category (trigger) | What it covers | Example reasons | Typical severity |
|---|---|---|---|
| **L1** | Port/link state and training | `Link training failure`, `Oper down`, `Port admin down`, `Autoneg failure`, `Bad signal integrity` | Error / Warning |
| **L2** | Bridging / VLAN | `Ingress spanning tree filter`, `Port loopback filter`, `Source MAC equals destination MAC`, `Source MAC is multicast`, `Source MAC is zero`, `Ingress VLAN filter` (untagged on tagged-only port, or VLAN not allowed), `MLAG port isolation` | Error / Notice |
| **Router (L3)** | Routing | `Router: IPv4 routing table unicast miss` (no route), `Blackhole route`, `TTL exceeded`, `Non-IP packet to router`, `Multicast MAC mismatch`, `Unresolved neighbor` | Warning |
| **Tunnel** | VXLAN encap/decap | `Overlay switch — source MAC equals destination MAC`, `Decapsulation error`, `Tunnel interface is disabled` | Warning |
| **ACL** | Access lists | `Ingress port ACL`, `Ingress router ACL`, `Egress port ACL` — includes the matching rule ID | Notice (intentional) / Error |
| **Buffer** | Congestion | `Tail drop` (queue full), `WRED`, `Port TC congestion threshold crossed`, `Packet latency threshold crossed`; with **PFC**, pause-storm / PFC-related buffer events | Warning |

Severity is WJH's own classification: *Error* = something is broken (loop, bad frame), *Warning* = probably misconfiguration or capacity (no route, tail drop), *Notice* = expected behavior (ACL deny) that you still might want to see.

For an AI RoCE fabric the Buffer category is the one you live in: `Tail drop` on a lossless class means PFC is not configured or not honored end to end; `WRED` tells you **ECN** marking thresholds are being hit (which is by design under congestion, but a flood of WRED *drops* on a class that should be lossless is a misconfiguration).

### 1.3 Enabling and scoping WJH (NVUE)

WJH is enabled by default on Spectrum switches running Cumulus Linux. You *scope* it with channels and triggers. Syntax changed in 5.14/5.15 — both forms below; prefer the newer one and verify in your release.

```bash
# Cumulus 5.15+ (state)                        # Cumulus ≤5.14 (enable on/off)
nv set system wjh state enabled                # nv set system wjh enable on
# Channels: a named stream of drop records, each with one or more trigger categories
nv set system wjh channel forwarding trigger l2
nv set system wjh channel forwarding trigger l3
nv set system wjh channel forwarding trigger tunnel
nv set system wjh channel layer-1 trigger l1
nv set system wjh channel buffer trigger buffer          # verify category name 'buffer' in your release
nv set system wjh channel forwarding polling-interval 30 # 5–300 s aggregation interval (5.18+)
nv config apply
```

Each trigger category can belong to only one channel; channel names are 4–16 characters. Later releases add drop filters and buffer thresholds (useful for RoCE):

```bash
nv set system wjh channel forwarding drop-filter ACL-NOISE drop-type acl severity notice
nv set system wjh channel forwarding drop-filter STORAGE ip 10.20.0.0/24         # (verify prefix support)
nv set system wjh channel buffer buffer-threshold congestion tc 3 interface all high 4   # alert when TC3 (RoCE) queue occupancy crosses level
nv set system wjh channel buffer buffer-threshold latency tc all interface swp1-32 high 100
nv config apply
```

### 1.4 Viewing WJH locally

```bash
nv show system wjh                       # service state
nv show system wjh channel               # channels, triggers, total drops
nv show system wjh channel forwarding    # one channel
nv show system wjh packet-buffer         # individual drop records with recommended action
nv show system wjh aggregate-buffer      # aggregated buffer (congestion) events
nv show system wjh l1-buffer             # layer-1 port state events
nv action export system wjh packet-buffer   # save recent drops as PCAP to /var/run/nv-wjh/
```

Illustrative `nv show system wjh packet-buffer` output:

```text
Timestamp            sPort   dPort   Drop group  Drop reason                                Severity  Src IP        Dst IP        Proto  Recommended action
-------------------  ------  ------  ----------  -----------------------------------------  --------  ------------  ------------  -----  --------------------------------------------
2026-10-19 09:12:04  swp3    N/A     L3          IPv4 routing table unicast miss            Warning   10.3.1.17     10.9.1.5      udp    Validate routing table for this destination
2026-10-19 09:12:04  swp3    N/A     L3          IPv4 routing table unicast miss            Warning   10.3.1.17     10.9.1.5      udp    Validate routing table for this destination
2026-10-19 09:12:05  swp7    swp49   Buffer      Tail drop                                  Warning   10.3.1.21     10.3.2.21     udp    Monitor network congestion
2026-10-19 09:12:06  swp12   N/A     L2          Ingress VLAN filter                        Error     -             -             -      Validate VLAN membership of the port
```

The Linux-level tool is `nv-wjh-cli` (5.18+; earlier releases used the `what-just-happened` CLI):

```bash
sudo nv-wjh-cli poll --data drops --output-type table --channels forwarding
sudo nv-wjh-cli poll --data drops --output-type pcap --no-metadata > drops.pcap   # open in Wireshark
sudo nv-wjh-cli poll --data aggregates
sudo nv-wjh-cli poll --data l1
sudo nv-wjh-cli dump      # diagnostics to /var/log/nv-wjh/diags-dump/<timestamp>
```

Older syntax you may still meet: `what-just-happened poll --aggregate`, `what-just-happened poll -o pcap`, `what-just-happened dump`. If a command is rejected, check the Cumulus version — this area has churned.

### 1.5 Exporting WJH

| Destination | How |
|---|---|
| **NetQ** | The NetQ agent reads WJH directly: on the switch `netq config add agent wjh` then `netq config restart agent`. The local WJH service must be **disabled** first (`nv set system wjh state disabled`), because only one consumer may own the ASIC drop channel. This is a classic exam/real-life gotcha: "NetQ shows no WJH drops" → local WJH service still running |
| **PCAP** | `nv action export system wjh packet-buffer` / `nv-wjh-cli ... --output-type pcap` |
| **Kafka / gRPC / OpenTelemetry** | Streaming export of WJH records exists for integration with external collectors (Kafka topics, gRPC streams, OTEL); the exact configuration surface differs by release — verify in current Cumulus docs ("Open Telemetry Export", gNMI streaming). The exam only expects you to know that export beyond NetQ is possible |

## 2. NetQ troubleshooting workflows

**NetQ** is the fabric-wide telemetry and validation platform (Module 4.5). The agent on each Cumulus switch (and optionally Linux hosts with ConnectX/BlueField) streams state to the NetQ server; the CLI (`netq …`) and UI query it. Three capabilities matter for troubleshooting: **validation**, **history/events**, and **RoCE/WJH telemetry**.

### 2.1 Validation suites: `netq check`

```bash
netq check agents                 # every agent fresh?
netq check interfaces             # link up/down + MTU mismatch between neighbors
netq check mtu
netq check bgp                    # sessions established, address-family mismatches
netq check evpn                   # VNI consistency, VTEP reachability
netq check vlan ; netq check vxlan ; netq check mlag
netq check ntp ; netq check sensors ; netq check license
netq check roce                   # RoCE config consistency (lossless/lossy mode, PFC/ECN parameters) across switches — verify availability in your NetQ release
netq check bgp include 1,3        # only specific tests
netq check interfaces around 2h   # as of two hours ago (time travel)
```

Output style:

```text
cumulus@netq-ts:~$ netq check bgp
bgp check result summary:

Total nodes         : 10
Checked nodes       : 10
Failed nodes        : 1
Rotten nodes        : 0
Warning nodes       : 0

Additional summary:
Total Sessions      : 64
Failed Sessions     : 2

Session Establishment Test   : 0 warnings, 1 errors
Address Families Test        : passed
Router ID Test               : passed

Session Establishment Test details:
Hostname  VRF   Peer Name  Peer Hostname  Reason                    Last Changed
--------  ----  ---------  -------------  ------------------------  ------------------
leaf03    default swp51    spine01        BGP session with peer spine01 (swp51 vrf default) failed, reason: Hold Timer Expired   Mon Oct 19 09:03:11 2026
```

"Rotten" = the agent has not reported recently; fix that before trusting any other check.

### 2.2 Show commands you will actually type

```bash
netq show events [severity error] [between now and 24h]     # fabric-wide event log (link down, BGP flap, sensor, agent)
netq show events type link ; netq show events type bgp
netq show interfaces type swp [hostname]                     # state, speed, MTU
netq leaf03 show interfaces swp51 counters                   # errors/discards history (wording differs by release)
netq show bgp ; netq show bgp hostname leaf03
netq show evpn ; netq show vxlan ; netq show mlag ; netq show lldp
netq show macs ; netq show ip routes <prefix>
netq show inventory ; netq show sensors temp
netq trace 10.3.1.17 from 10.9.1.5                           # L3 path (ECMP-aware) through the fabric
netq trace 10.9.1.5 from leaf01:swp3 vrf default             # from a specific ingress
netq show wjh-drop                                           # see §2.4
```

`netq trace` is the Ethernet equivalent of `ibtracert`: it computes the path(s) from NetQ's collected FIB/MAC state across every ECMP alternative and flags where MTU or state would break the path.

### 2.3 Validating a RoCE fabric with NetQ

NetQ collects **RoCE** configuration and counters from Spectrum switches (and from DPUs/NICs with a host agent):

```bash
netq show roce-config                      # per switch: RoCE mode (lossless/lossy), PFC priority, ECN min/max, DSCP→switch-priority map, buffer pool
netq show roce-config hostname leaf01
netq show roce-counters nic                # (or dpu) — host-side RoCE counters where an agent runs
netq show roce-counters pool               # buffer pool utilization
netq leaf01 show roce-counters             # per-switch RX/TX RoCE counters: PFC pause frames, ECN-marked packets, CNPs
netq check roce                            # consistency across the fabric (verify in your release)
```

What you are looking for:

| Observation | Meaning |
|---|---|
| `roce-config` differs on one leaf (e.g. mode lossy, or ECN max threshold different) | Configuration drift — the usual root cause of "one rack is slow". Fix with NVUE (`nv set qos roce mode lossless`) or Ansible |
| **PFC** pause RX rising on a *spine* port | Downstream leaf is congested and pushing back; check the leaf's host ports for oversubscription or a slow NIC |
| PFC pause TX counting on a *host-facing* port while the host shows RX drops | Host NIC not honoring PFC on that priority (host-side `mlnx_qos -i <if>` config) |
| ECN marked counts high but no drops | Working as designed under load (DCQCN engaged) |
| Tail drops on the RoCE traffic class | Lossless not actually configured on that port/class — WJH Buffer `Tail drop` confirms |
| PFC pause continuously at line rate on a port (pause storm) | A stuck receiver or a loop of PFC; isolate the port, check for a hung NIC/driver. WJH L1/Buffer events plus `nv show interface swpX counters` (pause frames) |

The NetQ UI adds RoCE and congestion heatmaps: per-switch, per-port views of PFC/ECN/buffer occupancy over time, which is how you spot that the slowness every night at 02:00 coincides with the checkpoint write from one rack. See `../03-rdma/03-roce-a-to-z.md` and `../04-spectrum-ethernet/` for the configuration side.

### 2.4 WJH in NetQ

```bash
netq show wjh-drop                                    # summary by type/reason, all hosts
netq show wjh-drop details between now and 7d
netq leaf03 show wjh-drop l1
netq show wjh-drop l2 reason "Ingress VLAN filter"
netq show wjh-drop router ingress-port swp3 between now and 1h
netq show wjh-drop acl                                # includes rule-id-acl
netq show wjh-drop buffer severity warning
netq show wjh-drop tunnel
netq show wjh-drop l3 src-ip 10.3.1.17 dst-ip 10.9.1.5 proto udp dst-port 4791     # RoCEv2 flow
```

Filters available per type: `ingress-port`, `egress-port`, `severity`, `reason`, `src-ip`/`dst-ip`, `proto`, `src-port`/`dst-port`, `src-mac`/`dst-mac`, `traffic-class`, `rule-id-acl`, `vlan`, `between … and …`, `around …`, `json`. UDP destination port **4791** is RoCEv2 — filtering WJH on it isolates RDMA drops from everything else.

### 2.5 When NetQ itself is the problem

```bash
netq show agents                     # Fresh / Rotten per switch, agent version, last heard
netq config show agent               # on the switch: server address, port, VRF, enabled modules
netq config show agent wjh           # is WJH collection enabled on this agent
netq config add server 10.0.100.10 vrf mgmt port 31980  # (re)point the agent; verify port for your deployment
netq config restart agent
sudo systemctl status netq-agent
netq show agents opta                # on-premises appliance health (OPTA)
```

Rotten agent → check management VRF reachability (`ping -I mgmt <netq-server>`), time sync (`netq check ntp`), and version compatibility between agent and server.

## 3. Cumulus-local troubleshooting (no NetQ)

```bash
# Interface and link
nv show interface swp1
nv show interface swp1 link                   # speed, autoneg, FEC, breakout, lanes, transceiver summary
nv show interface swp1 counters               # RX/TX packets, errors, discards; look for symbol/FEC errors (fields vary)
nv show interface swp1 counters --view=pause  # (view names vary; check 'nv show interface swp1 counters -h')
nv show interface --view=brief ; nv show interface --view=lldp
sudo ethtool -S swp1 | grep -i -E 'fec|symbol|pause|discard|crc'
sudo mlxlink -d <dev> -p <port> -m -c         # Module 8.3
# QoS / RoCE
nv show qos roce                              # mode, PFC priority, ECN thresholds, DSCP map
nv show qos roce counters                     # per-port RoCE counters where available
nv show qos buffer ; nv show qos pfc ; nv show qos congestion-control
nv show interface swp1 qos counters           # per-TC drops/queues (verify path)
# Routing
sudo vtysh -c "show bgp summary" ; sudo vtysh -c "show bgp ipv4 unicast neighbors swp51 received-routes"
sudo vtysh -c "show ip route 10.9.1.5" ; sudo vtysh -c "show evpn vni"
nv show router bgp ; nv show vrf default router bgp neighbor
# Hardware tables and resources
cl-resource-query                             # ASIC table utilization: routes, MACs, ACLs, ECMP next-hops
nv show system forwarding resources           # NVUE view (verify)
# Logs and bundles
sudo tail -f /var/log/switchd.log             # ASIC driver: port events, resource exhaustion, errors
sudo tail -f /var/log/frr/frr.log ; sudo journalctl -u nv-wjh -f
sudo cl-support                               # creates /var/support/cl_support_<host>_<date>.txz for NVIDIA support
```

**cl-resource-query** (detailed in `../04-spectrum-ethernet/`) is the tool for "the route/ACL/MAC was configured but traffic is dropped": if a table is at 100%, the ASIC silently fails to install new entries, FRR still shows the route, and WJH reports `routing table unicast miss` for destinations that "should" be routable.

## 4. UFM troubleshooting (InfiniBand)

**UFM** (Unified Fabric Manager, Module 2.x) is the IB-side counterpart of NetQ plus the SM. For troubleshooting:

| UFM function | What it gives you |
|---|---|
| **Fabric Health / Health report** | On-demand or scheduled run of validation tests (duplicate GUIDs, link width/speed vs. capability, high BER, error counters over threshold, SM status, cable info, routing checks); produces a report you can attach to a ticket. Built on **ibdiagnet** |
| **Events & alarms** | Link down/up, "Link downed counter exceeded", "Symbol error rate exceeded", "High BER" (from per-lane telemetry), module temperature, "SM changed / SM lost", "Node disappeared", PKey violations. Severities Critical/Major/Minor/Warning, configurable thresholds and actions (email, SNMP, script) |
| **Telemetry (UFM Telemetry / UFM Cyber-AI)** | Time-series per port: bandwidth, congestion (XmitWait), error counters, BER, temperature; graphs let you align a slowdown with a congestion spike |
| **Topology / Device view** | Cable inventory (vendor, PN, SN, length, FW), negotiated vs. supported speed; compare topologies between snapshots |
| **Port isolation** | Disable a port from the GUI/REST (equivalent to `ibportstate … disable`) to take a flapping link out of service while the SM reroutes; some deployments enable automatic isolation on BER thresholds |
| **System dump** | `ufm_log_collect` (or the GUI "System Dump" / `/opt/ufm/scripts/collect_logs.sh` depending on version — verify) gathers UFM logs, SM logs (`/opt/ufm/files/log/opensm.log`), ibdiagnet output and configuration into a tarball for NVIDIA support |
| **SM status** | Master/standby, sweep errors, routing engine in use, AR/SHARP state |

Typical IB escalation bundle: UFM system dump + `ibdiagnet` directory + `ibstat`/`mlxlink -m -c` from the affected host.

## 5. End-to-end example: "NCCL all_reduce is slow between two nodes" (RoCE)

Scenario: 2-node nccl-tests busbw is 60% of reference; single-node is fine. Fabric is Spectrum-X, Cumulus, RoCEv2 lossless on TC3.

1. **Is it one link?** `ib_write_bw -d mlx5_2 -R --report_gbits` for each rail pair. Rail 2 gives 190 Gb/s, others ~390. Now it is a link problem, not NCCL.
2. **Layer 1 on that rail:** on the leaf, `nv show interface swp17 link` → 2 lanes active / speed 200G. `sudo mlxlink -d <dev> -p 17 -m -c` → one lane Rx power very low, raw BER high on two lanes. Cable → reseat/replace (Module 8.3). Re-test: 390 Gb/s.
3. **Still slow at scale?** Rerun 2-node nccl-tests; busbw now 85%. Check congestion: `netq leaf01 show roce-counters` → PFC pause TX high on swp17; `netq show wjh-drop buffer severity warning` → `Tail drop` on swp17 TC3. Tail drop on a lossless class = the host side is not lossless.
4. **Host RoCE config:** on the node, `mlnx_qos -i ens2f0np0` → PFC enabled on priority 3? `cat /sys/class/net/ens2f0np0/ecn/roce_np/enable/3`; DSCP for RoCE traffic (`NCCL_IB_TC=106` → DSCP 26 → switch-priority 3 mapping must match `nv show qos roce`). The node had `NCCL_IB_TC` unset so RoCE went DSCP 0 → lossy class → tail drops. Set the env (or the default `tclass` in `/sys/class/infiniband/mlx5_2/tc/1/traffic_class`). Confirm `netq check roce` is consistent.
5. **Routing/AR:** if drops are gone but busbw is still uneven under multi-node load, check ECMP/adaptive routing on the spines (`nv show router adaptive-routing`, `nv show interface swp49 counters` for uneven uplink load), and `netq trace` between the two node IPs to confirm the path.
6. **NCCL side:** `NCCL_DEBUG=INFO` to confirm GDR is used (`NET/IB : Using [0]mlx5_2 ... GDRDMA`) and the right NICs are selected (`NCCL_IB_HCA`).
7. Document: WJH drop record (pcap), `roce-counters` before/after, `mlxlink` output, and the config change, so the next on-call has the pattern.

## 6. Symptom → tool across Modules 8.1–8.4

| Symptom | IB fabric | Ethernet / RoCE fabric | Module |
|---|---|---|---|
| Port down | `ibstat` (Down/Polling), `mlxlink -m` | `nv show interface swpX link`, WJH L1, `mlxlink -m` | 8.1, 8.3, 8.4 |
| Port up, no LID / no neighbor | `sminfo`, SM logs, UFM SM status | `nv show interface --view=lldp`, `netq show lldp` | 8.1, 8.4 |
| Link trained slow / 2x | `iblinkinfo` "Could be 4X", `mlxlink` width | `nv show interface swpX link` lanes, `mlxlink -p` | 8.1, 8.3 |
| Cabled to the wrong place | **CVT**, `ibdiagnet net_dump`, rail check | **CVT**, LLDP vs plan, `netq show lldp` | 8.2 |
| Rising errors on one link | `perfquery` SymbolError, `ibdiagnet -P`, UFM High BER event | `nv show interface counters`, `ethtool -S`, NetQ link events | 8.1, 8.3, 8.4 |
| Why was this cable bad? | `mlxlink -m -c -e`, `--rx_fec_histogram`, `mlxcables` | same | 8.3 |
| Packets dropped, counters only say "discard" | `perfquery` XmitDiscards; UFM | **WJH** (`nv show system wjh packet-buffer`, `netq show wjh-drop`) | 8.4 |
| Slow but no errors (congestion) | `perfquery` XmitWait, UFM congestion telemetry, AR/SHARP status | `netq show roce-counters` (PFC/ECN), WJH Buffer, `nv show qos roce` | 8.1, 8.4 |
| One rack configured differently | UFM topology compare; `saquery -p` for PKeys | `netq check roce`, `netq show roce-config`, `netq check mtu` | 8.4 |
| Route/ACL present but traffic dropped | `ibroute`, `ibdiagnet -r` | `cl-resource-query`, WJH Router/ACL reasons, `vtysh` | 8.4 |
| Which path does traffic take? | `ibtracert` | `netq trace` | 8.1, 8.4 |
| Raw RDMA performance | `ib_write_bw/lat` (+ `--use_cuda`) | `ib_write_bw -R -x <gid>` | 8.1 |
| Collective slow | nccl-tests, `NCCL_DEBUG=INFO`, `NCCL_IB_HCA` | same + `NCCL_IB_TC`/`NCCL_IB_GID_INDEX` | 8.1, 8.4 |
| Need a support bundle | UFM system dump, `ibdiagnet` dir, `sysinfo-snapshot` | `cl-support`, `nv-wjh-cli dump`, NetQ `netq show events json` | 8.1, 8.4 |
| Firmware / NIC parameter | `mlxfwmanager`, `mlxconfig` | same | 8.3 |

## Configuration / command reference

```bash
# WJH (Cumulus NVUE, 5.15+; use 'enable on' on older releases)
nv set system wjh state enabled
nv set system wjh channel forwarding trigger l2 ; ... trigger l3 ; ... trigger tunnel
nv set system wjh channel layer-1 trigger l1 ; nv set system wjh channel buffer trigger buffer
nv set system wjh channel forwarding polling-interval 30
nv set system wjh channel forwarding drop-filter <name> drop-type acl severity notice
nv config apply
nv show system wjh ; nv show system wjh channel ; nv show system wjh packet-buffer
nv show system wjh aggregate-buffer ; nv show system wjh l1-buffer
nv action export system wjh packet-buffer
sudo nv-wjh-cli poll --data drops|aggregates|l1 --output-type table|json|pcap [--channels <ch>]
sudo nv-wjh-cli dump
# WJH -> NetQ (disable local service first)
nv set system wjh state disabled ; nv config apply
sudo netq config add agent wjh ; sudo netq config restart agent
# NetQ
netq check agents|interfaces|mtu|bgp|evpn|vlan|vxlan|mlag|ntp|sensors|roce
netq show events [severity error] [between now and 24h] ; netq show events type link
netq show interfaces type swp ; netq show bgp ; netq show evpn ; netq show lldp
netq show roce-config ; netq show roce-counters nic|dpu|pool ; netq <host> show roce-counters
netq show wjh-drop [l1|l2|router|tunnel|acl|buffer] [reason "..."] [ingress-port swpX] [dst-port 4791] [between now and 1h]
netq trace <dst-ip> from <src-ip|host:port> [vrf <vrf>]
netq show agents ; netq config show agent ; netq config restart agent
# Cumulus local
nv show interface swp1 link|counters ; nv show qos roce ; nv show qos buffer
sudo vtysh -c "show bgp summary" ; cl-resource-query ; sudo tail -f /var/log/switchd.log ; sudo cl-support
# UFM
#  GUI: Fabric Health report, Events, Telemetry, Port isolate;  CLI (appliance): ufm_log_collect / system dump (verify path)
```

## Common pitfalls & exam traps
- WJH is per-packet ASIC telemetry with a **drop reason**; it is not sFlow sampling and not a counter.
- Drop categories: L1, L2, Router/L3, Tunnel, ACL, Buffer. `Tail drop`/`WRED` are **Buffer**; `routing table unicast miss`/`Blackhole` are **Router**; `Ingress VLAN filter`/`spanning tree filter` are **L2**.
- To send WJH to NetQ you must **disable the local WJH service** and add `wjh` to the NetQ agent. Two consumers cannot both read the channel.
- NVUE syntax changed: `nv set system wjh enable on` (≤5.14) vs `nv set system wjh state enabled` (5.15+); local CLI `what-just-happened` (≤5.17) vs `nv-wjh-cli` (5.18+). Say "verify in current docs" in the exam room — the exam usually uses whichever form is in its referenced release.
- A "Rotten" NetQ agent invalidates every `netq check` for that node — fix the agent first.
- RoCEv2 is UDP **4791**; filter WJH/pcap on it to see only RDMA drops.
- Tail drop on the lossless TC means PFC is not effective end-to-end (switch or host side) — do not tune ECN to fix tail drops.
- `netq trace` is L3 path computation from collected state, not a live probe; it cannot see a transient black hole that NetQ has not polled yet.
- `cl-resource-query` reveals table exhaustion that FRR/NVUE "show" commands will not — the route is in software but not in the ASIC.
- `cl-support` is the Cumulus support bundle; UFM system dump / `ufm_log_collect` is the IB one; `ibdiagnet` output is a *separate* bundle you add.
- UFM port isolation and `ibportstate disable` do the same thing; UFM is the preferred, audited way in production.

## Key takeaways
- WJH: ASIC-generated drop records with reason, ports and headers; categories L1/L2/Router/Tunnel/ACL/Buffer; severities Error/Warning/Notice.
- Scope WJH with `nv set system wjh channel <name> trigger <l1|l2|l3|tunnel|buffer|acl>`; read with `nv show system wjh packet-buffer` / `nv-wjh-cli poll`.
- WJH → NetQ: disable local WJH, `netq config add agent wjh`, then `netq show wjh-drop <type> [filters]`.
- `netq check <protocol>` validates fabric-wide; `netq show events`, `netq trace`, `netq show roce-config/roce-counters` for RoCE health.
- RoCE health signals: PFC pause counts (where/why), ECN marks (expected under load), tail drops on lossless TC (misconfig), config drift between switches.
- Cumulus local: `nv show interface swpX link/counters`, `nv show qos roce`, `vtysh`, `switchd.log`, `cl-resource-query`, `cl-support`.
- UFM: health report (ibdiagnet-based), events with thresholds (High BER, link down, SM change), telemetry graphs, port isolation, system dump.
- Methodology is identical on IB and Ethernet: one link? → layer 1 → config consistency → congestion → routing → application.

## Review questions

1. A Spectrum leaf shows RX discards incrementing on swp3. Which tool tells you the exact reason and the headers of the dropped packets?  
   A. `cl-resource-query`  
   B. WJH (`nv show system wjh packet-buffer` or `netq show wjh-drop`)  
   C. `netq check interfaces`  
   D. `vtysh -c "show bgp summary"`

2. NetQ shows no WJH drops for a switch although `nv show system wjh channel` on the switch shows hundreds. The most likely fix is:  
   A. Enable sFlow on the switch  
   B. Disable the local WJH service (`nv set system wjh state disabled`), run `netq config add agent wjh` and restart the agent  
   C. Increase the WJH polling interval  
   D. Add the switch to a NetQ check suite

3. WJH reports `Tail drop` on traffic class 3 for RoCEv2 (UDP 4791) packets on a host-facing port. Which conclusion is correct?  
   A. ECN thresholds are too high and should be raised further  
   B. This is expected behavior for a lossless class  
   C. PFC/lossless handling is not effective on that class end-to-end; verify switch `nv show qos roce` and host PFC/DSCP configuration  
   D. The cable is faulty

4. Which WJH category contains the reason "IPv4 routing table unicast miss"?  
   A. L2  
   B. Router (L3)  
   C. Buffer  
   D. ACL

5. `netq check bgp` reports one node as "Rotten". What does that mean?  
   A. The BGP session has hold-timer expired  
   B. The node's NetQ agent has not reported recently, so its data is stale  
   C. The node has a route table exhaustion  
   D. The node runs an unsupported Cumulus version

6. Which NetQ command would you use to confirm that every leaf has the same RoCE lossless mode, PFC priority and ECN thresholds?  
   A. `netq show wjh-drop buffer`  
   B. `netq show roce-config` (and `netq check roce` where available)  
   C. `netq trace`  
   D. `netq show events type link`

7. FRR shows a route for 10.9.1.5/32, but traffic to it is dropped and WJH reports a routing table miss. Which Cumulus tool explains it?  
   A. `cl-support`  
   B. `cl-resource-query` (ASIC route table full)  
   C. `netq show lldp`  
   D. `nv show qos roce`

8. For an InfiniBand fabric, which UFM capability most directly parallels NetQ's `netq check` validation suites?  
   A. Port isolation  
   B. Fabric Health report (ibdiagnet-based scheduled validation)  
   C. System dump  
   D. SM failover

### Answer key
1. B — WJH records drop reason, ports and headers per packet; counters and checks cannot.
2. B — Only one consumer may own the ASIC drop stream; NetQ requires the local WJH service off and `wjh` added to the agent.
3. C — Tail drops on a lossless class mean PFC is not engaging end-to-end; ECN tuning does not prevent tail drops.
4. B — No-route misses are Router/L3 category drops.
5. B — "Rotten" is NetQ's term for a stale agent; results for that node cannot be trusted.
6. B — `roce-config` shows the per-switch RoCE parameters so drift is visible; `check roce` automates the comparison.
7. B — Software has the route but the hardware table is exhausted; `cl-resource-query` shows utilization.
8. B — UFM's Fabric Health runs validation tests across the fabric and produces a report, the IB analogue of `netq check`.

## References
- Cumulus Linux User Guide — What Just Happened (WJH): https://docs.nvidia.com/networking-ethernet-software/cumulus-linux-518/Monitoring-and-Troubleshooting/Network-Troubleshooting/Mellanox-WJH/
- NVUE Command Reference — WJH set/unset and show: https://docs.nvidia.com/networking-ethernet-software/nvue-reference/Set-and-Unset-Commands/WJH/
- NetQ User Guide — Monitor WJH Events: https://docs.nvidia.com/networking-ethernet-software/cumulus-netq-415/Manage-Events-and-Notifications/Monitor-WJH-Events/
- NetQ User Guide — Monitor the RoCE Service: https://docs.nvidia.com/networking-ethernet-software/cumulus-netq-412/Monitor-Operations/Monitor-the-RoCE-Service
- NetQ CLI Reference Manual (netq check / show / trace)
- UFM Enterprise User Manual — Fabric Health, Events, Telemetry, System Dump: https://docs.nvidia.com/networking/display/ufmenterprise
- NVIDIA Academy: Cumulus Linux Administration; NetQ; UFM Essentials
- Related modules: `01-infiniband-diagnostics-and-perftest.md`, `03-mlxlink-and-mlxcables-debug-tools.md`, `../03-rdma/03-roce-a-to-z.md`, `../04-spectrum-ethernet/`, `../02-infiniband/`
