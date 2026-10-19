# Lab 5 — NetQ and WJH Troubleshooting

## Objective
Use **NetQ** to validate the fabric (`netq check`), read events, trace a path, compare **RoCE** configuration across switches, and query **WJH** drops; use Cumulus-local commands (`nv show system wjh`, `nv show interface … counters`, `cl-resource-query`, `cl-support`) when NetQ is not available; then find three injected faults.

## Environment
- **Preferred:** NVIDIA Air demo topology that includes a NetQ server (search the Air marketplace for "NetQ"; these ship with Cumulus switches whose agents are already pointed at the NetQ server, plus the `netq` CLI on the server). You can also add a NetQ server to your Lab 1 topology — the NetQ OVA/cloud-appliance install steps are in the NetQ docs and take 30–60 minutes.
- **Hardware alternative:** Spectrum switches with Cumulus 5.x and a NetQ OPTA appliance or NetQ Cloud.
- **What is simulated:** NetQ agents, `netq check`, events, LLDP/BGP/EVPN state, `netq trace`, `roce-config`. **What needs hardware:** WJH drop records (the ASIC generates them) and real PFC/ECN counters. Steps that need hardware are marked **[HW]**; the lab still shows the exact commands and expected output so you can recognize them.

## Prerequisites
- Lab 1 fabric (or demo topology) up, with Lab 1's RoCE profile applied on both leaves.
- Modules `../04-spectrum-ethernet/` (NetQ) and `08-troubleshooting/04-wjh-netq-and-cumulus-troubleshooting.md`.
- Shell on the NetQ server (`netq` CLI) and on the switches.

## Tasks

### Task 1 — Agent health first

On the NetQ server:

```bash
netq show agents
netq check agents
```

Expected: every switch `Fresh`, agent version, last-seen time. A `Rotten` row means the rest of the lab lies about that node. On a switch, `netq config show agent` shows the server IP/port and VRF (`mgmt`), `sudo systemctl status netq-agent` shows it running.

### Task 2 — Validate the fabric

```bash
netq check interfaces
netq check mtu
netq check bgp
netq check ntp
netq check roce          # verify availability in your NetQ version
netq check bgp around 1h
```

Expected: `Failed nodes : 0` for each. Read one full `netq check bgp` output and identify the tests it runs (Session Establishment, Address Families, Router ID). Run `netq check bgp include 1` to see how to run a single test.

### Task 3 — Events, interfaces, path

```bash
netq show events between now and 24h
netq show events type link
netq show interfaces type swp
netq leaf01 show interfaces swp51 counters          # errors/discards (wording differs by release)
netq show lldp
netq show bgp
netq trace 10.1.2.11 from 10.1.1.11
```

Expected `netq trace` output: one or more paths `server01 → leaf01:swp51 → spine01:swp2 → leaf02:swp1 → server02` and the ECMP alternative via spine02, each with MTU and state checks passing.

### Task 4 — RoCE configuration consistency

```bash
netq show roce-config
netq show roce-config hostname leaf01
netq leaf01 show roce-counters            # [HW] real values; simulated shows zeros or no data
netq show roce-counters pool              # [HW]
```

Expected: both leaves report `mode lossless`, PFC priority 3, matching ECN min/max thresholds, same DSCP→SP mapping. Save this output; Task 7 relies on it.

### Task 5 — WJH on the switch

On leaf01:

```bash
nv show system wjh
nv show system wjh channel
```

Expected (Cumulus 5.15+): state `enabled`, default channel(s) with triggers. If no channels exist, create the standard set:

```bash
nv set system wjh channel forwarding trigger l2
nv set system wjh channel forwarding trigger l3
nv set system wjh channel forwarding trigger tunnel
nv set system wjh channel layer-1 trigger l1
nv config apply -y
nv show system wjh channel
```

(On Cumulus ≤5.14 the enable command is `nv set system wjh enable on`; on ≤5.17 the Linux CLI is `what-just-happened`, on 5.18+ `nv-wjh-cli` — verify in your release.)

**[HW]** Generate a drop and watch it:

```bash
# from server01: send to an address with no route
ping -c 5 10.99.99.99
# on leaf01
nv show system wjh packet-buffer
sudo nv-wjh-cli poll --data drops --output-type table --channels forwarding
```

Expected record: `Drop group L3`, `Drop reason IPv4 routing table unicast miss`, `sPort swp1`, `Src IP 10.1.1.11`, `Dst IP 10.99.99.99`, severity `Warning`, recommended action about validating the routing table. In Air (no ASIC) the service runs but shows no records — that is expected, not a fault.

### Task 6 — WJH in NetQ **[HW]**

On the switch, hand the WJH channel to the NetQ agent:

```bash
nv set system wjh state disabled ; nv config apply -y      # 'enable off' on older releases
sudo netq config add agent wjh
sudo netq config restart agent
```

On the NetQ server:

```bash
netq show wjh-drop
netq show wjh-drop router between now and 30m
netq leaf01 show wjh-drop router ingress-port swp1 reason "IPv4 routing table unicast miss"
netq show wjh-drop l3 dst-port 4791          # RoCEv2 only
netq show wjh-drop buffer severity warning
```

Repeat the no-route ping and confirm the record appears in NetQ. Note the two-step dependency: local WJH service off, agent `wjh` on.

### Task 7 — Cumulus-local toolbox

On leaf01:

```bash
nv show interface swp1 link
nv show interface swp1 counters
nv show qos roce
sudo vtysh -c "show bgp summary"
cl-resource-query | head -40
sudo tail -20 /var/log/switchd.log
sudo cl-support                        # produces /var/support/cl_support_leaf01_<date>.txz
ls -lh /var/support/
```

Read `cl-resource-query`: note the route, host (ARP/ND), MAC and ACL table utilizations and what "100%" would imply (route in FRR, missing in ASIC, WJH "routing table miss" for valid destinations).

## Verification checklist
- [ ] `netq show agents` all Fresh.
- [ ] `netq check interfaces|mtu|bgp` pass with 0 failed nodes.
- [ ] `netq trace` shows both ECMP paths between the servers.
- [ ] `netq show roce-config` identical on both leaves.
- [ ] `nv show system wjh channel` shows L1/L2/L3/tunnel triggers.
- [ ] [HW] no-route ping produces a Router-category WJH record locally, and in NetQ after re-pointing the channel.
- [ ] `cl-support` bundle created; you know where it lands.

## Break/fix challenge
Have a partner inject these; find each with NetQ first, then confirm on the switch.

1. **PFC disabled on one leaf:** `nv set qos roce mode lossy ; nv config apply -y` on leaf02. Find with `netq show roce-config` (mode differs) or `netq check roce`. **[HW]** With RoCE traffic from Lab 3 running through leaf02, `netq show wjh-drop buffer` shows `Tail drop` on TC3 and `netq leaf02 show roce-counters` shows no PFC activity. Fix: `nv set qos roce mode lossless`.
2. **MTU mismatch:** `nv set interface swp51 link mtu 1500 ; nv config apply -y` on leaf01. Find with `netq check mtu` / `netq check interfaces` (MTU mismatch with spine01:swp1) and `netq trace` (path flagged for MTU). Fix by unsetting the MTU.
3. **BGP peer removed:** `nv unset vrf default router bgp neighbor swp52 ; nv config apply -y` on leaf02. Find with `netq check bgp` (Failed nodes 1, Session Establishment test) and `netq show events type bgp`. Fix by re-adding the neighbor (or re-running Lab 4's playbook — notice it does this for you).
4. **[HW] Degraded cable:** half-seat an optic or use a known-bad DAC between leaf01 and spine01. Find with `netq show events type link` (link flap), `netq show wjh-drop l1`, `nv show interface swp51 link` (lanes/speed), then `mlxlink -d <dev> -p <port> -m -c` (Module 8.3).

For each fault, record the *first* NetQ command that exposed it.

## Cleanup
```bash
# switch: return WJH to local mode if you re-pointed it to NetQ
sudo netq config del agent wjh ; sudo netq config restart agent
nv set system wjh state enabled ; nv config apply -y
# remove injected faults via 'nv config apply <good-revision>' or re-run Lab 4 playbooks
sudo rm /var/support/cl_support_*.txz      # optional
```
