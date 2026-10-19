# Module 4.2 — Cumulus Linux Essentials

> **Exam domain:** NVIDIA Spectrum Networking (30%)  
> **Source course(s):** Cumulus Linux Essentials (NVIDIA Academy)  
> **Source docs:** Cumulus Linux User Guide (5.x); NVUE CLI and Command Reference; NVUE Cheat Sheet  
> **Estimated study time:** 5–6 hours

## Learning objectives
- Describe the Cumulus Linux architecture: Debian base, `switchd`, the Spectrum SDK, and which traffic is hardware- vs software-forwarded.
- Explain the **NVUE** object model and the lifecycle of a configuration change (pending → applied → startup).
- Configure interfaces (breakout, speed, MTU, bonds/LACP) with `nv set interface`.
- Configure VLAN-aware bridging, access/trunk ports and SVIs using `nv set bridge domain`.
- Configure MLAG peers and basic L3 (addresses, static routes, VRFs, BGP unnumbered).
- Verify state with `nv show` and with the underlying Linux tools (`ip`, `bridge`, `vtysh`).
- Describe ONIE image installation, licensing and zero-touch provisioning (ZTP) at a conceptual level.

## 1. Architecture

Cumulus Linux is a Debian-based network operating system. Everything you know about a Linux server applies: `apt`, `systemd`, `/etc/network/interfaces`, `ip route`, `tcpdump`. What makes it a switch OS is one daemon:

- **`switchd`** — the "switch daemon". It watches the Linux kernel's networking state (interfaces, bridge FDB, neighbor table, routing table from FRR) and programs the Spectrum ASIC through the NVIDIA SDK so the forwarding decision the kernel *would* make is made in hardware at line rate. Think of the kernel as the control plane's source of truth and `switchd` as the synchroniser.
- **SDK / ASIC** — the Spectrum (Spectrum-2/3/4) ASIC does all steady-state forwarding: L2 switching, L3 routing, VXLAN, ACLs, QoS, RoCE PFC/ECN. `switchd` depends on the kernel driver `mlxsw`-style stack packaged by NVIDIA; `systemctl status switchd` is the first check when "the config applied but traffic isn't flowing".
- **Hardware vs software forwarding** — anything with a matching ASIC entry is forwarded in hardware. Traffic *to* the switch (BGP, SSH, LLDP, ARP replies destined to the switch's own MAC) and traffic the ASIC can't handle (packets needing exception processing, control-plane protocols) is punted to the CPU over an internal port. The CPU path is rate-limited and slow (tens of thousands of pps, not terabits). If a show command reports a route as "offloaded" false, that prefix is being software-forwarded — a problem you will diagnose with `cl-resource-query` in Module 4.3.
- **FRRouting (FRR)** — the routing suite (`bgpd`, `zebra`, `ospfd`, …). NVUE writes FRR configuration for you; `vtysh` exposes it directly.
- **`cumulus` user** — default local administrator (default password `cumulus`, which you are forced to change on first login in 4.x/5.x). It has passwordless `sudo`. NVUE commands run as `cumulus` without `sudo`; Linux-level tools (`vtysh`, editing `/etc/...`) need `sudo`.

| Component | What it does | How you check it |
|---|---|---|
| Linux kernel | Control-plane state (interfaces, FDB, RIB/FIB) | `ip link`, `bridge fdb`, `ip route` |
| `switchd` | Programs ASIC from kernel state | `systemctl status switchd` |
| FRR | Routing protocols | `sudo vtysh -c "show bgp summary"` |
| `nvued` | NVUE daemon, REST API on 8765 | `systemctl status nvued`, `nv show system` |
| `ifupdown2` | Interface configuration from `/etc/network/interfaces` | `ifquery -a`, `ifreload -a` |

## 2. NVUE — the object model and CLI

**NVUE** (NVIDIA User Experience) is the supported CLI and REST API. Legacy **NCLU** (`net add`, `net commit`) was removed in Cumulus 5.x; if you see `net` commands in older study material, translate them to `nv`.

### 2.1 Object model
NVUE presents configuration as a tree of objects. The top level includes `system`, `interface`, `bridge`, `mlag`, `router`, `vrf`, `nve`, `evpn`, `qos`, `acl`, `service`. Every command is a path into that tree plus a value:

```text
nv set interface swp1 link mtu 9216
       └─object─┘└──id──┘└─attribute path─┘└value┘
```

Because it is a tree, `nv show <path>` shows the whole subtree at any depth, and `nv unset <path>` removes it. Ranges work on identifiers: `swp1-4,swp10`, `vlan10-20`.

### 2.2 Change lifecycle
NVUE keeps three configurations:

| Name | What it is | Commands |
|---|---|---|
| **Pending** | Edits you have made with `nv set`/`nv unset` but not applied | `nv config diff` (pending vs applied), `nv config detach` discards |
| **Applied** | What the switch is running now | `nv config show` (applied, in YAML) |
| **Startup** | What will load at boot: `/etc/nvue.d/startup.yaml` | `nv config save` copies applied → startup |

```bash
nv set interface swp1 link mtu 9216
nv config diff                    # review the pending change
nv config apply                   # prompts for confirmation
nv config apply --assume-yes      # or -y: no prompt (scripts, Ansible)
nv config save                    # persist to startup.yaml
```
Exam trap: `nv config apply` does **not** persist. A reboot after apply-without-save reverts to the last saved startup. Conversely, `nv config save` without `apply` saves whatever is *applied*, not pending.

### 2.3 Revisions, history, replace
Every apply creates a numbered **revision**. `nv config history` lists them; `nv config show --rev <n>` (or `nv config diff <rev1> <rev2>`) compares; `nv config apply <rev>` rolls back to one. NVUE also supports:

```bash
nv config show -o commands              # applied config as nv set commands (great for backup)
nv config show -o yaml > backup.yaml    # applied config as YAML
nv config replace /path/new.yaml        # pending := exactly this file (declarative)
nv config patch /path/partial.yaml      # merge file into pending
nv config apply -y
```
`replace` is what Ansible and git-based pipelines use (Module 6.1). The file format is the same YAML `nv config show` produces: a list with a top-level `- set:` key.

### 2.4 Pending config and multiple users
Pending changes are per-user-session by default (a "detached" revision). If another user has unapplied changes, `nv config apply` warns about it. In automation you may see errors about a locked or conflicting revision; `nv config detach` clears your pending set. NVUE also has the REST API (`https://<switch>:8765/nvue_v1/`) that exposes exactly the same tree — covered in Module 6.1.

## 3. Interfaces

### 3.1 Naming
Front-panel ports are `swp1`, `swp2`, … (switch port). Breakout sub-ports are `swp1s0`–`swp1s3`. The management port is `eth0`. Loopback is `lo`. SVIs are `vlan<N>`. Bonds you name yourself (`bond1`, `uplink`, or `peerlink` for the MLAG link).

### 3.2 Breakout, speed, autoneg, MTU
```bash
nv set interface swp1 link breakout 4x            # 400G → 4×100G: swp1s0..swp1s3
nv set interface swp2 link breakout 2x            # 800G → 2×400G
nv set interface swp3 link speed 100G
nv set interface swp3 link auto-negotiate off
nv set interface swp1-32 link mtu 9216
nv set interface swp1s0 link state up
nv config apply
```
Default MTU on Cumulus is 9216 on swp ports (verify for your release); the bridge MTU follows the lowest member. For RoCE you typically want 9000+ end to end with the host set to 9000 or 4200 — but keep the switch at 9216 to leave room for VXLAN headers (50 bytes) in EVPN fabrics.

A breakout changes the ASIC port mapping; on most platforms it requires a `switchd` reload or restart, and on some, neighbouring ports are disabled (e.g., every other port on an SN2700). Check the platform's port-mapping note.

### 3.3 Bonds (LACP)
```bash
nv set interface bond1 bond member swp5,swp6
nv set interface bond1 bond mode lacp             # default; also static, lacp-bypass
nv set interface bond1 bond lacp-rate fast
nv set interface bond1 link mtu 9216
nv config apply
nv show interface bond1 bond
```
Members must have matching speed and must not have their own IP/bridge config. Cumulus 5.x bonds default to LACP; a `lacp-bypass` option lets a single member forward before LACP negotiates (useful for PXE/ZTP of hosts).

## 4. VLANs and the VLAN-aware bridge

Cumulus uses a single **VLAN-aware bridge** (`br_default`) by default. One bridge carries all VLANs; ports are access or trunk members of it. (Traditional per-VLAN bridges exist but are legacy.)

```bash
nv set bridge domain br_default vlan 10,20,30          # VLANs exist on the bridge
nv set interface swp1 bridge domain br_default                 # trunk, all bridge VLANs
nv set interface swp2 bridge domain br_default vlan 10,20     # trunk, limited VLANs
nv set interface swp3 bridge domain br_default access 10      # access port
nv set bridge domain br_default untagged 1                     # native VLAN (default 1)
nv config apply
```
SVIs (routed interfaces per VLAN) are `vlan<N>` interfaces:
```bash
nv set interface vlan10 ip address 10.1.10.1/24
nv set interface vlan20 ip address 10.1.20.1/24
nv set interface vlan10 ip vrr address 10.1.10.254/24    # VRR: active-active gateway across MLAG pair
nv set interface vlan10 ip vrr mac-address 00:00:5e:00:01:0a
nv config apply
```
**VRR** (Virtual Router Redundancy) is Cumulus's active-active first-hop redundancy for MLAG pairs — both switches own the virtual IP/MAC and both forward. VRRP (standards-based, active-standby) is also supported; the exam contrasts the two.

Verify:
```bash
nv show bridge domain br_default
nv show bridge domain br_default vlan
nv show bridge domain br_default mac-table            # learned MACs with VLAN and port
nv show interface vlan10
```

## 5. MLAG basics

**MLAG** (Multi-Chassis Link Aggregation) lets two Cumulus switches present as one LACP partner to a dual-attached host or downstream switch. The two switches are joined by a **peerlink** bond carrying a dedicated peer VLAN (4094 by default) over which `clagd` synchronises MAC tables and LACP state.

```bash
# Both switches
nv set interface peerlink bond member swp49,swp50
nv set mlag mac-address 44:38:39:BE:EF:AA          # system ID — identical on both peers
nv set mlag peer-ip linklocal                        # peer address via IPv6 link-local on peerlink.4094
nv set mlag backup 10.0.0.12 vrf mgmt               # backup IP of the *other* switch (mgmt network)
nv set mlag priority 1000                            # lower = primary (default 32768)
nv set mlag init-delay 10
# Host-facing dual-attached bond needs an MLAG ID identical on both peers
nv set interface bond1 bond member swp1
nv set interface bond1 bond mlag id 1
nv set interface bond1 bridge domain br_default
nv config apply
```
Verify with `nv show mlag` (role primary/secondary, peer alive, backup active) and `nv show mlag consistency-checker` (VLAN/MTU mismatches across the pair). The peerlink itself is auto-added to the bridge; the `peerlink.4094` subinterface carries `clagd`.

Design note for AI fabrics: GPU-side RoCE ports are *not* usually MLAG'd — each SuperNIC single-attaches to a rail leaf. MLAG shows up on management, storage and traditional service leaves. For EVPN fabrics NVIDIA increasingly recommends EVPN multihoming instead of MLAG.

## 6. Layer 3

### 6.1 Addresses, static routes, VRFs
```bash
nv set interface lo ip address 10.0.0.11/32
nv set interface swp51 ip address 192.168.51.1/31
nv set vrf default router static 0.0.0.0/0 via 192.168.51.0
nv set vrf blue                                       # create VRF
nv set interface swp10 ip vrf blue
nv set interface swp10 ip address 172.16.10.1/24
nv set vrf blue router static 172.16.0.0/16 via 172.16.10.254
nv config apply
nv show vrf
nv show vrf blue router rib ipv4
```
Each VRF is a Linux VRF device with its own routing table; FRR runs a routing instance per VRF. The `mgmt` VRF (Module 4.3) is a special pre-created VRF for `eth0`.

### 6.2 BGP unnumbered
Cumulus pioneered **BGP unnumbered**: eBGP sessions over interfaces with no IPv4 address, using IPv6 link-local addresses discovered via router advertisements, with IPv4 routes carried using RFC 5549 extended next hops. The benefit is that every leaf and spine have *identical* interface configuration — no per-link /31 planning — which is why it underpins every NVIDIA EVPN reference design.

```bash
nv set router bgp autonomous-system 65011
nv set router bgp router-id 10.0.0.11
nv set vrf default router bgp neighbor swp51 remote-as external     # "external" = any ASN ≠ mine
nv set vrf default router bgp neighbor swp52 remote-as external
nv set vrf default router bgp address-family ipv4-unicast network 10.0.0.11/32
nv set vrf default router bgp address-family ipv4-unicast redistribute connected enable on
nv config apply
```
`remote-as external` (or `internal`) means you do not hard-code the neighbour's ASN. Spines typically share one ASN; each leaf (or leaf pair) has its own. Verify:
```bash
nv show vrf default router bgp neighbor
nv show vrf default router bgp neighbor swp51
nv show vrf default router bgp address-family ipv4-unicast route
sudo vtysh -c "show ip bgp summary"
```
Peer-groups reduce repetition: `nv set vrf default router bgp peer-group fabric remote-as external` then `nv set vrf default router bgp neighbor swp51 peer-group fabric`.

## 7. Useful show commands

| Need | Command |
|---|---|
| Platform, version, uptime | `nv show system` |
| All interfaces, state, speed | `nv show interface` |
| One port's physical link details (speed, autoneg, FEC, transceiver) | `nv show interface swp1 link` |
| Port counters | `nv show interface swp1 counters` |
| Bridge VLAN membership | `nv show bridge domain br_default vlan` |
| MAC table | `nv show bridge domain br_default mac-table` |
| BGP neighbours | `nv show vrf default router bgp neighbor` |
| Routes | `nv show vrf default router rib ipv4` |
| MLAG status | `nv show mlag` |
| Pending vs applied | `nv config diff` |
| Applied config as commands | `nv config show -o commands` |
| LLDP neighbours | `nv show interface --view=lldp` or `nv show interface swp1 lldp` |

Output formats: `-o json`, `-o yaml`, `-o commands`. `nv list-commands` dumps every valid path — a handy reference when you cannot remember a subtree.

## 8. Linux underneath

Because NVUE generates standard Linux configuration, you can always drop down a level to confirm what was actually programmed:

```bash
ip -br link                       # interface states
ip -br addr
ip route show vrf blue
bridge vlan show                  # VLAN membership per port
bridge fdb show br br_default     # MAC table (kernel view)
sudo vtysh                        # FRR shell: show bgp summary, show ip route, show evpn vni
cat /etc/network/interfaces       # GENERATED by NVUE — do not hand-edit when using NVUE
cat /etc/frr/frr.conf             # also generated
```
The rule: on an NVUE-managed switch, *do not* edit `/etc/network/interfaces` or `/etc/frr/frr.conf` directly — the next `nv config apply` overwrites them. Cumulus does support a "mixed" mode for files NVUE does not manage (`/etc/nvue.d/` snippets) — check current docs if you need it.

## 9. Installation, licensing and ZTP

- **ONIE** (Open Network Install Environment) is the boot loader shipped on every Spectrum switch. `onie-select -i` from a running Cumulus or the ONIE menu lets you (re)install an image by HTTP/TFTP/USB, or via DHCP option 114 pointing to the installer URL. ONIE also handles "uninstall" to wipe the OS.
- **Licensing** — Cumulus Linux historically required a license file (`cl-license -i`) to enable front-panel ports; with NVIDIA Spectrum hardware (Cumulus 5.x on NVIDIA switches) a license is no longer required on NVIDIA-branded switches. Check the current doc for your platform; on older third-party hardware you may still see `cl-license`.
- **ZTP** (Zero Touch Provisioning) — on first boot Cumulus looks for a script: on USB, via DHCP option 239 (URL), or by hostname default. The script (bash or Python) typically installs an image, sets passwords, pulls an NVUE YAML and runs `nv config replace` + `apply`, and installs the NetQ agent. `ztp -s <url>` runs it manually; `ztp -e` enables and `ztp -d` disables ZTP. Full detail in Module 4.3.

## Configuration / command reference

```bash
# NVUE lifecycle
nv set ... | nv unset ...
nv config diff
nv config apply [-y]
nv config save
nv config history
nv config show [-o commands|yaml|json] [--rev N]
nv config replace <file.yaml> | nv config patch <file.yaml>
nv config detach

# Interfaces
nv set interface swp1 link breakout 4x
nv set interface swp1 link speed 100G
nv set interface swp1 link auto-negotiate off
nv set interface swp1 link mtu 9216
nv set interface bond1 bond member swp5,swp6
nv set interface bond1 bond mode lacp

# Bridging
nv set bridge domain br_default vlan 10,20
nv set interface swp2 bridge domain br_default access 10
nv set interface swp3 bridge domain br_default vlan 10,20
nv set interface vlan10 ip address 10.1.10.1/24
nv set interface vlan10 ip vrr address 10.1.10.254/24

# MLAG
nv set interface peerlink bond member swp49,swp50
nv set mlag mac-address <sys-mac>
nv set mlag peer-ip linklocal
nv set mlag backup <peer-mgmt-ip> vrf mgmt
nv set mlag priority 1000
nv set interface bond1 bond mlag id 1

# L3
nv set interface lo ip address 10.0.0.11/32
nv set vrf blue
nv set interface swp10 ip vrf blue
nv set vrf default router static 0.0.0.0/0 via <nh>
nv set router bgp autonomous-system 65011
nv set router bgp router-id 10.0.0.11
nv set vrf default router bgp neighbor swp51 remote-as external
nv set vrf default router bgp address-family ipv4-unicast network 10.0.0.11/32
```

## Common pitfalls & exam traps
- `nv config apply` is not `nv config save`. Apply changes the running system; save writes `/etc/nvue.d/startup.yaml`. Both are needed for a persistent change.
- Hand-editing `/etc/network/interfaces` or `/etc/frr/frr.conf` on an NVUE switch — overwritten at next apply.
- NCLU `net add` syntax no longer exists in Cumulus 5.x; the exam expects `nv`.
- A bond member cannot carry its own IP or bridge configuration; put VLAN/IP config on the bond.
- Breakout changes may disable adjacent ports or require `switchd` restart depending on platform.
- BGP unnumbered requires IPv6 link-local to be enabled on the interface (the default) and `remote-as external`, not a literal ASN, in reference designs.
- VRR is active-active (Cumulus), VRRP is active-standby (standard). Know which one NVIDIA uses with MLAG.
- `nv show` output reflects *applied/operational* state; `nv config diff` shows what is pending. Reading `nv show` to confirm an un-applied change is a classic mistake.
- MLAG `mac-address` and the bond `mlag id` must match on both peers; `backup` must point to the *other* peer's management IP.
- `cumulus` user has passwordless sudo; NVUE itself does not require sudo.

## Key takeaways
- Cumulus Linux = Debian + FRR + `switchd` syncing kernel state into the Spectrum ASIC.
- NVUE: tree of objects; `nv set/unset` edits pending; `nv config apply` makes it running; `nv config save` makes it startup.
- Revisions give rollback: `nv config history`, `nv config apply <rev>`.
- `nv config replace <yaml>` is the declarative path used by automation.
- Interfaces: `swpN`, breakout → `swpNsM`, `eth0` is management, `vlanN` are SVIs.
- One VLAN-aware bridge `br_default`; ports are access or trunk members.
- VRR = Cumulus active-active anycast gateway for MLAG pairs.
- MLAG: peerlink bond, shared system MAC, `mlag id` per dual-attached bond, backup IP via mgmt.
- BGP unnumbered: `neighbor swpN remote-as external` — identical config on every fabric link.
- Underneath: `ip`, `bridge`, `vtysh`; generated files are not for hand-editing.

## Review questions

1. An engineer runs `nv set interface swp1 link mtu 9216` and `nv config apply -y`, verifies traffic, then reboots the switch. After reboot the MTU is back to its old value. Why?
   A. MTU cannot be set via NVUE.
   B. `nv config save` was never run, so startup.yaml did not include the change.
   C. The change required `sudo`.
   D. `-y` applies the change only temporarily.

2. Which command shows the difference between what you have typed but not applied and what the switch is currently running?
   A. `nv show config`
   B. `nv config history`
   C. `nv config diff`
   D. `nv config show -o commands`

3. You need to roll a switch back to the configuration it had three applies ago. Which sequence is correct?
   A. `nv config history` to find the revision, then `nv config apply <rev>`
   B. `nv config save --rev 3`
   C. `git checkout /etc/network/interfaces`
   D. `nv config detach` three times

4. A dual-attached server's LACP bond stays down on one MLAG peer. Both peers show `nv show mlag` peer alive. What is the most likely configuration error?
   A. Different `bond mlag id` values on the two peers
   B. The peerlink uses VLAN 4094
   C. `mlag priority` is identical on both
   D. VRR is not configured

5. In a BGP unnumbered fabric, what does `remote-as external` mean?
   A. The neighbour must be in ASN 65535.
   B. Accept any neighbour ASN that differs from the local ASN (eBGP).
   C. Peer only over IPv4.
   D. Use the external route-map.

6. Which file does NVUE generate, meaning you should not hand-edit it on an NVUE-managed switch?
   A. `/etc/hostname`
   B. `/etc/network/interfaces`
   C. `/etc/nvue.d/startup.yaml`
   D. `/var/log/syslog`

7. What is the default bridge model in Cumulus Linux 5.x?
   A. One traditional bridge per VLAN
   B. A single VLAN-aware bridge, `br_default`
   C. Open vSwitch
   D. Linux macvlan

8. What is the role of `switchd`?
   A. Runs the BGP protocol
   B. Programs the Spectrum ASIC to mirror the Linux kernel's forwarding state
   C. Serves the NVUE REST API
   D. Handles SSH logins

### Answer key
1. B — Apply changes running state; only `nv config save` persists to startup.yaml.
2. C — `nv config diff` compares pending against applied.
3. A — Revisions are listed by `nv config history` and re-applied with `nv config apply <rev>`.
4. A — The MLAG ID identifies the same logical bond on both peers; a mismatch breaks LACP.
5. B — `external` accepts any non-local ASN, enabling identical config across links.
6. B — `/etc/network/interfaces` (and `/etc/frr/frr.conf`) are rendered by NVUE.
7. B — VLAN-aware bridge `br_default` carries all VLANs by default.
8. B — `switchd` synchronises kernel state into the ASIC via the SDK.

## References
- Cumulus Linux User Guide (5.x) — https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/
- NVUE CLI — https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/System-Configuration/NVIDIA-User-Experience-NVUE/NVUE-CLI/
- NVUE Command Reference — https://docs.nvidia.com/networking-ethernet-software/nvue-reference/
- NVUE Cheat Sheet — https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/System-Configuration/NVIDIA-User-Experience-NVUE/NVUE-Cheat-Sheet/
- Cumulus Linux: Multi-Chassis Link Aggregation (MLAG); Border Gateway Protocol (BGP); Zero Touch Provisioning
- NVIDIA Academy course: Cumulus Linux Essentials
- Related: [`01-spectrum-x.md`](01-spectrum-x.md), [`03-cumulus-linux-administration.md`](03-cumulus-linux-administration.md), [`../06-automation/01-nvue-and-ansible-automation.md`](../06-automation/01-nvue-and-ansible-automation.md)
