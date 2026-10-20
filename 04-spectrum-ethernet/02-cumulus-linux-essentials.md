# Module 4.2 — Cumulus Linux Essentials

> **Exam domain:** NVIDIA Spectrum Networking (30%)  
> **Source course(s):** Cumulus Linux Essentials (NVIDIA Academy)  
> **Source docs:** Cumulus Linux User Guide (5.x); NVUE CLI and Command Reference; NVUE Cheat Sheet  
> **Estimated study time:** 5–6 hours

> **How to study this module:** read §1–§2, work through the §3 walkthrough on real gear or NVIDIA Air, then attempt the §5 hands-on assignment using only the Hint section. Open the Solution guide (last section) only after you have a working attempt. Finish with the Appendix C knowledge check closed-book.

## 1. Learning objectives

By the end of this module you will be able to…

- **Describe** the Cumulus Linux architecture: Debian base, `switchd`, the Spectrum SDK, and which traffic is hardware- vs software-forwarded. *(Spectrum Networking — NOS architecture)*
- **Explain** the **NVUE** object model and the lifecycle of a configuration change (pending → applied → startup), including revisions and rollback. *(Spectrum Networking — NVUE; Automation and Configuration)*
- **Configure** interfaces (breakout, speed, MTU, bonds/LACP) with `nv set interface`. *(Spectrum Networking — interface configuration)*
- **Configure** VLAN-aware bridging, access/trunk ports and SVIs using `nv set bridge domain`. *(Spectrum Networking — L2)*
- **Configure** MLAG peers and basic L3 (addresses, static routes, VRFs, BGP unnumbered). *(Spectrum Networking — L2/L3 redundancy and routing)*
- **Verify** state with `nv show` and with the underlying Linux tools (`ip`, `bridge`, `vtysh`). *(Troubleshooting Tools)*
- **Describe** ONIE image installation, licensing and zero-touch provisioning (ZTP) at a conceptual level. *(Spectrum Networking — installation)*

## 2. Key technical concepts

### 2.1 Architecture: Debian + FRR + `switchd`

*Analogy:* the Linux kernel is the architect's drawing of how traffic *should* flow; `switchd` is the construction foreman who makes the ASIC build exactly that, at line rate.

Cumulus Linux is a Debian-based network operating system. Everything you know about a Linux server applies: `apt`, `systemd`, `/etc/network/interfaces`, `ip route`, `tcpdump`. What makes it a switch OS is one daemon:

- **`switchd`** — the "switch daemon". It watches the Linux kernel's networking state (interfaces, bridge FDB, neighbor table, routing table from FRR) and programs the Spectrum ASIC through the NVIDIA SDK so the forwarding decision the kernel *would* make is made in hardware. Think of the kernel as the control plane's source of truth and `switchd` as the synchroniser.
- **SDK / ASIC** — the Spectrum (Spectrum-2/3/4) ASIC does all steady-state forwarding: L2 switching, L3 routing, VXLAN, ACLs, QoS, RoCE PFC/ECN. `systemctl status switchd` is the first check when "the config applied but traffic isn't flowing".
- **Hardware vs software forwarding** — anything with a matching ASIC entry is forwarded in hardware. Traffic *to* the switch (BGP, SSH, LLDP, ARP replies destined to the switch's own MAC) and traffic the ASIC can't handle (exception processing, control-plane protocols) is punted to the CPU over an internal port. The CPU path is rate-limited and slow (tens of thousands of pps, not terabits). If a show command reports a route as "offloaded" false, that prefix is being software-forwarded — a problem you diagnose with `cl-resource-query` in Module 4.3.
- **FRRouting (FRR)** — the routing suite (`bgpd`, `zebra`, `ospfd`, …). NVUE writes FRR configuration for you; `vtysh` exposes it directly.
- **`cumulus` user** — default local administrator (default password `cumulus`, which you are forced to change on first login in 4.x/5.x). It has passwordless `sudo`. NVUE commands run as `cumulus` without `sudo`; Linux-level tools (`vtysh`, editing `/etc/...`) need `sudo`.

| Component | What it does | How you check it |
|---|---|---|
| Linux kernel | Control-plane state (interfaces, FDB, RIB/FIB) | `ip link`, `bridge fdb`, `ip route` |
| `switchd` | Programs ASIC from kernel state | `systemctl status switchd` |
| FRR | Routing protocols | `sudo vtysh -c "show bgp summary"` |
| `nvued` | NVUE daemon, REST API on 8765 | `systemctl status nvued`, `nv show system` |
| `ifupdown2` | Interface configuration from `/etc/network/interfaces` | `ifquery -a`, `ifreload -a` |

**Why the exam cares:** "config applied, traffic not flowing" questions are answered by distinguishing kernel state from ASIC state. `switchd` is the bridge between them.

### 2.2 NVUE — the object model

**NVUE** (NVIDIA User Experience) is the supported CLI and REST API. Legacy **NCLU** (`net add`, `net commit`) was removed in Cumulus 5.x; if you see `net` commands in older study material, translate them to `nv`.

NVUE presents configuration as a tree of objects. The top level includes `system`, `interface`, `bridge`, `mlag`, `router`, `vrf`, `nve`, `evpn`, `qos`, `acl`, `service`. Every command is a path into that tree plus a value:

```text
nv set interface swp1 link mtu 9216
       └─object─┘└──id──┘└─attribute path─┘└value┘
```

Because it is a tree, `nv show <path>` shows the whole subtree at any depth, and `nv unset <path>` removes it. Ranges work on identifiers: `swp1-4,swp10`, `vlan10-20`.

### 2.3 The change lifecycle: pending → applied → startup

*Analogy:* think of a blog post. **Pending** is your draft — only you see it. **Applied** is published — the world sees it now. **Startup** is the copy saved to disk — what survives if the server reboots. Publishing does not save to disk, and saving to disk does not publish your draft.

| Name | What it is | Commands |
|---|---|---|
| **Pending** | Edits you have made with `nv set`/`nv unset` but not applied | `nv config diff` (pending vs applied), `nv config detach` discards |
| **Applied** | What the switch is running now | `nv config show` (applied, in YAML) |
| **Startup** | What will load at boot: `/etc/nvue.d/startup.yaml` | `nv config save` copies applied → startup |

Exam trap: `nv config apply` does **not** persist. A reboot after apply-without-save reverts to the last saved startup. Conversely, `nv config save` without `apply` saves whatever is *applied*, not pending.

### 2.4 Revisions, history, replace

Every apply creates a numbered **revision**. `nv config history` lists them; `nv config show --rev <n>` (or `nv config diff <rev1> <rev2>`) compares; `nv config apply <rev>` rolls back to one. NVUE also supports declarative files:

```bash
nv config show -o commands              # applied config as nv set commands (great for backup)
nv config show -o yaml > backup.yaml    # applied config as YAML
nv config replace /path/new.yaml        # pending := exactly this file (declarative)
nv config patch /path/partial.yaml      # merge file into pending
nv config apply -y
```

`replace` is what Ansible and git-based pipelines use (Module 6.1). The file format is the same YAML `nv config show` produces: a list with a top-level `- set:` key.

Pending changes are per-user-session by default (a "detached" revision). If another user has unapplied changes, `nv config apply` warns about it. In automation you may see errors about a locked or conflicting revision; `nv config detach` clears your pending set. NVUE also has the REST API (`https://<switch>:8765/nvue_v1/`) that exposes exactly the same tree — covered in Module 6.1.

### 2.5 Interfaces

Front-panel ports are `swp1`, `swp2`, … (switch port). Breakout sub-ports are `swp1s0`–`swp1s3`. The management port is `eth0`. Loopback is `lo`. SVIs are `vlan<N>`. Bonds you name yourself (`bond1`, `uplink`, or `peerlink` for the MLAG link).

Default MTU on Cumulus is 9216 on swp ports (verify for your release); the bridge MTU follows the lowest member. For RoCE you typically want 9000+ end to end with the host set to 9000 or 4200 — but keep the switch at 9216 to leave room for VXLAN headers (50 bytes) in EVPN fabrics.

A breakout changes the ASIC port mapping; on most platforms it requires a `switchd` reload or restart, and on some, neighbouring ports are disabled (e.g., every other port on an SN2700). Check the platform's port-mapping note.

**Bonds:** members must have matching speed and must not have their own IP/bridge config. Cumulus 5.x bonds default to LACP; a `lacp-bypass` option lets a single member forward before LACP negotiates (useful for PXE/ZTP of hosts).

### 2.6 The VLAN-aware bridge and SVIs

*Analogy:* one apartment building (`br_default`) with many floors (VLANs). An access port is a resident with a key to one floor; a trunk port is the building manager with keys to several.

Cumulus uses a single **VLAN-aware bridge** (`br_default`) by default. One bridge carries all VLANs; ports are access or trunk members of it. (Traditional per-VLAN bridges exist but are legacy.) SVIs (routed interfaces per VLAN) are `vlan<N>` interfaces.

**VRR** (Virtual Router Redundancy) is Cumulus's active-active first-hop redundancy for MLAG pairs — both switches own the virtual IP/MAC and both forward. VRRP (standards-based, active-standby) is also supported; the exam contrasts the two.

### 2.7 MLAG

*Analogy:* two receptionists sharing one phone number and one appointment book. The caller (host) dials one number and either can answer; the shared book (`clagd` sync over the peerlink) keeps them consistent.

**MLAG** (Multi-Chassis Link Aggregation) lets two Cumulus switches present as one LACP partner to a dual-attached host or downstream switch. The two switches are joined by a **peerlink** bond carrying a dedicated peer VLAN (4094 by default) over which `clagd` synchronises MAC tables and LACP state. Each dual-attached bond needs an `mlag id` identical on both peers; the pair shares a system `mac-address`; a `backup` IP over the management network detects peer death if the peerlink fails.

Design note for AI fabrics: GPU-side RoCE ports are *not* usually MLAG'd — each SuperNIC single-attaches to a rail leaf. MLAG shows up on management, storage and traditional service leaves. For EVPN fabrics NVIDIA increasingly recommends EVPN multihoming instead of MLAG.

### 2.8 Layer 3: VRFs and BGP unnumbered

*Analogy:* a **VRF** is a separate building with its own address book. The `mgmt` VRF (Module 4.3) is the security office's building — `eth0` lives there so fabric routes can never hijack SSH.

Each VRF is a Linux VRF device with its own routing table; FRR runs a routing instance per VRF.

Cumulus pioneered **BGP unnumbered**: eBGP sessions over interfaces with no IPv4 address, using IPv6 link-local addresses discovered via router advertisements, with IPv4 routes carried using RFC 5549 extended next hops. The benefit is that every leaf and spine have *identical* interface configuration — no per-link /31 planning — which is why it underpins every NVIDIA EVPN reference design. `remote-as external` (or `internal`) means you do not hard-code the neighbour's ASN. Spines typically share one ASN; each leaf (or leaf pair) has its own.

### 2.9 Installation, licensing and ZTP

- **ONIE** (Open Network Install Environment) is the boot loader shipped on every Spectrum switch. `onie-select -i` from a running Cumulus or the ONIE menu lets you (re)install an image by HTTP/TFTP/USB, or via DHCP option 114 pointing to the installer URL. ONIE also handles "uninstall" to wipe the OS.
- **Licensing** — Cumulus Linux historically required a license file (`cl-license -i`) to enable front-panel ports; with NVIDIA Spectrum hardware (Cumulus 5.x on NVIDIA switches) a license is no longer required on NVIDIA-branded switches. Check the current doc for your platform; on older third-party hardware you may still see `cl-license`.
- **ZTP** (Zero Touch Provisioning) — on first boot Cumulus looks for a script: on USB, via DHCP option 239 (URL), or by hostname default. The script (bash or Python) typically installs an image, sets passwords, pulls an NVUE YAML and runs `nv config replace` + `apply`, and installs the NetQ agent. `ztp -s <url>` runs it manually; `ztp -e` enables and `ztp -d` disables ZTP. Full detail in Module 4.3.

## 3. Live-style walkthrough

We will do two things on `leaf01`: first walk the full NVUE lifecycle on a trivial change so you *see* pending, applied and startup behave differently, then build a VLAN-aware bridge with an SVI and an LACP bond.

### Part A — the NVUE lifecycle on one MTU change

```bash
# Fresh login as cumulus. First, what is swp1's MTU right now (operational/applied)?
nv show interface swp1 link mtu
# -> 9216 (platform default)

# Make a change. This goes ONLY into pending — the "draft".
nv set interface swp1 link mtu 1500

# Prove it: nv show reads applied/operational state, so it still says 9216.
nv show interface swp1 link mtu
# -> 9216          <- classic mistake: reading nv show to "confirm" an unapplied change

# The right tool for pending: diff pending against applied.
nv config diff
```

```text
- set:
    interface:
      swp1:
        link:
          mtu: 1500
```

```bash
# Publish the draft. Without -y you get a confirmation prompt listing the diff.
nv config apply -y
# -> applied [rev_id: 12]

# Now nv show reflects it.
nv show interface swp1 link mtu
# -> 1500

# And the Linux kernel agrees — NVUE rendered /etc/network/interfaces and ifreload'd it.
ip -br link show swp1
# -> swp1  UP  ... mtu 1500

# Still NOT on disk. Prove it: compare startup to applied.
nv config diff startup applied        # shows the mtu line — startup lacks it
# Reboot now and swp1 goes back to 9216.

# Save: copies applied -> /etc/nvue.d/startup.yaml
nv config save
nv config diff startup applied        # empty — in sync

# History: every apply created a revision.
nv config history
```

```text
rev_id  date                 user     message
------  -------------------  -------  ---------
12      2026-10-19 09:14:02  cumulus  nv config apply
11      2026-10-19 08:50:31  cumulus  nv config apply
...
```

```bash
# Rollback: make revision 11 (pre-MTU) pending, review, apply.
nv config apply 11            # pending := rev 11, prompts with the diff; or nv config diff 11 12 first
nv show interface swp1 link mtu
# -> 9216
nv config save                # persist the rollback too

# Housekeeping: if you have a half-finished draft you want to abandon:
nv set interface swp1 link mtu 1400
nv config detach              # throw the draft away; nv config diff is now empty
```

Watch the pattern: `set` → `diff` → `apply` → `save`. Two of those four are optional in a lab and mandatory in production.

### Part B — VLAN-aware bridge, SVI, LACP bond

```bash
# --- Interfaces ---
# Break out an 800G port to 2x400G — creates swp2s0 and swp2s1. Expect a switchd reload.
nv set interface swp2 link breakout 2x
# Force 100G and disable autoneg on swp3 (DAC to an older NIC).
nv set interface swp3 link speed 100G
nv set interface swp3 link auto-negotiate off
# Jumbo everywhere on the host-facing range. 9216 leaves VXLAN headroom.
nv set interface swp1-32 link mtu 9216
nv set interface swp2s0 link state up

# --- LACP bond to a dual-NIC storage server ---
nv set interface bond1 bond member swp5,swp6        # members: same speed, NO ip/bridge config of their own
nv set interface bond1 bond mode lacp               # default; also static, lacp-bypass
nv set interface bond1 bond lacp-rate fast          # 1 s LACPDUs -> faster failure detection
nv set interface bond1 link mtu 9216

# --- VLAN-aware bridge ---
nv set bridge domain br_default vlan 10,20,30           # VLANs exist on the bridge
nv set interface swp1 bridge domain br_default           # trunk, ALL bridge VLANs
nv set interface swp2s0 bridge domain br_default vlan 10,20   # trunk, limited VLANs
nv set interface swp3 bridge domain br_default access 10      # access port: untagged in VLAN 10
nv set interface bond1 bridge domain br_default access 20     # bond is the bridge member, not swp5/6
nv set bridge domain br_default untagged 1                    # native VLAN on trunks (default 1)

# --- SVIs with VRR anycast gateway ---
nv set interface vlan10 ip address 10.1.10.1/24
nv set interface vlan20 ip address 10.1.20.1/24
nv set interface vlan10 ip vrr address 10.1.10.254/24     # VIP shared with the MLAG peer
nv set interface vlan10 ip vrr mac-address 00:00:5e:00:01:0a

# Review everything before apply.
nv config diff
nv config apply -y
nv config save
```

Checkpoints — what good looks like:

```bash
nv show interface bond1 bond
```
```text
                 operational  applied
---------------  -----------  -------
mode             lacp         lacp
lacp-rate        fast         fast
member
  swp5           up
  swp6           up
```
Both members `up`. If one is `down` with LACP configured, the server side is probably not running LACP — consider `lacp-bypass` for PXE.

```bash
nv show bridge domain br_default vlan
```
```text
VLAN  Flags  Member Ports
----  -----  ------------------------------
1     PVID   swp1, swp2s0
10    PVID   swp3                 <- access
10           swp1, swp2s0
20    PVID   bond1
20           swp1, swp2s0
30           swp1
```
Read the `PVID` flag: it marks untagged membership. `swp3` untagged in 10 = access; `swp1` untagged in 1 = native VLAN.

```bash
nv show bridge domain br_default mac-table      # learned MACs with VLAN and port
nv show interface vlan10                         # SVI up, address, VRR state
bridge vlan show                                 # kernel view — must agree with nv show
```

### Part C — a quick L3 touch

```bash
nv set interface lo ip address 10.0.0.11/32
nv set interface swp51 ip address 192.168.51.1/31
nv set vrf default router static 0.0.0.0/0 via 192.168.51.0
nv set vrf blue                                       # new VRF: its own routing table
nv set interface swp10 ip vrf blue                    # move swp10 into the "blue building"
nv set interface swp10 ip address 172.16.10.1/24
nv set vrf blue router static 172.16.0.0/16 via 172.16.10.254

# BGP unnumbered to two spines — identical lines on every leaf.
nv set router bgp autonomous-system 65011
nv set router bgp router-id 10.0.0.11
nv set vrf default router bgp neighbor swp51 remote-as external     # "external" = any ASN != mine
nv set vrf default router bgp neighbor swp52 remote-as external
nv set vrf default router bgp address-family ipv4-unicast network 10.0.0.11/32
nv set vrf default router bgp address-family ipv4-unicast redistribute connected enable on
nv config apply -y

nv show vrf                              # default, mgmt, blue
nv show vrf blue router rib ipv4         # 172.16.10.0/24 connected, 172.16.0.0/16 static
nv show vrf default router bgp neighbor  # swp51/swp52 Established, Remote-AS learned
sudo vtysh -c "show ip bgp summary"      # same data from FRR
```

Peer-groups reduce repetition: `nv set vrf default router bgp peer-group fabric remote-as external` then `nv set vrf default router bgp neighbor swp51 peer-group fabric`.

### Part D — the Linux underneath

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

## 4. Troubleshooting / common gotchas

### Gotcha 1 — "My change disappeared after reboot"

**Symptom:** MTU/VLAN/BGP change worked yesterday; after a reboot it is gone.

**Why it happens:** `nv config apply` changed running state; `nv config save` was never run, so `/etc/nvue.d/startup.yaml` never got it.

**How to debug:**
```bash
nv config history                     # the apply is there
nv config diff startup applied        # before reboot this would have shown the gap
```

**Fix:** re-apply the revision (`nv config apply <rev>`) and `nv config save`. In automation, every play ends with save.

### Gotcha 2 — "nv show doesn't show my config"

**Symptom:** you typed `nv set ...`, then `nv show ...` and the value is unchanged.

**Why it happens:** `nv show` reads applied/operational state; your edit is pending.

**How to debug:** `nv config diff` — your change is listed there.

**Fix:** `nv config apply -y`. Remember: diff for pending, show for applied.

### Gotcha 3 — Bond stays down

**Symptom:** `nv show interface bond1 bond` shows a member `down`, or the bond never forwards.

**Why it happens:** a member carries its own IP or bridge config (not allowed), the member speeds differ, or the host is not speaking LACP.

**How to debug:**
```bash
nv show interface swp5                    # any ip/bridge config on the member? remove it
nv show interface swp5 link               # speed matches swp6?
nv show interface bond1 bond              # LACP partner info present?
```

**Fix:** move VLAN/IP config onto the bond; match speeds; use `nv set interface bond1 bond mode lacp-bypass` if the host needs to PXE before LACP.

### Gotcha 4 — Edited `/etc/network/interfaces`, change vanished

**Symptom:** a hand-edit to `/etc/network/interfaces` or `/etc/frr/frr.conf` reverts.

**Why it happens:** NVUE regenerates both files on every apply.

**How to debug:** `nv config show -o commands` — your hand edit is not represented.

**Fix:** express the change in NVUE, or use the documented `/etc/nvue.d/` snippet mechanism for unmanaged settings.

## 5. Hands-on assignment — 2-leaf / 2-spine NVIDIA Air topology with MLAG and BGP unnumbered

**Scenario.** Build the management/storage leaf tier for a GPU pod in NVIDIA Air: two leaves in an MLAG pair with a dual-attached server, two spines, BGP unnumbered everywhere.

**Requirements**
1. Hostnames `leaf01`, `leaf02`, `spine01`, `spine02`; loopbacks 10.0.0.11, .12, .1, .2.
2. MLAG between leaf01 and leaf02: peerlink bond on `swp49,swp50`, shared system MAC `44:38:39:BE:EF:AA`, `peer-ip linklocal`, backup IP over the `mgmt` VRF, leaf01 primary (priority 1000).
3. Dual-attached `server01` on `bond1` (leaf01 `swp1`, leaf02 `swp1`), `mlag id 1`, access VLAN 10.
4. VLAN 10 SVI with VRR gateway 10.1.10.254/24 on both leaves (leaf01 10.1.10.1, leaf02 10.1.10.2), VRR MAC `00:00:5e:00:01:0a`.
5. BGP unnumbered: leaves ASN 65011/65012, spines ASN 65000, `remote-as external`, loopbacks advertised, `redistribute connected` on leaves so 10.1.10.0/24 reaches the spines.
6. Everything saved to startup.

**Deliverables**
- `nv config show -o yaml` from all four switches.
- Output of `nv show mlag`, `nv show mlag consistency-checker`, `nv show interface bond1 bond`, `nv show vrf default router bgp neighbor` from both leaves.
- `nv config history` from leaf01 showing at least three revisions.

**Acceptance criteria**
- `nv show mlag` on leaf01: role `primary`, peer alive, backup active; leaf02 `secondary`.
- `nv show interface bond1 bond` on both leaves: swp1 up, LACP partner present.
- `server01` can ping 10.1.10.254 and the spine loopbacks.
- Both spines' `nv show vrf default router rib ipv4` contain 10.1.10.0/24 with two next hops (ECMP to both leaves).
- After `sudo reboot` on leaf02, all configuration persists.

### Hint
- Build the peerlink and MLAG *before* the host bond; `nv show mlag` must say peer alive before bond1 will come up on both.
- `backup` is the *other* peer's eth0 address, and it needs `vrf mgmt`.
- The `mlag id` and the system `mac-address` must be identical on both peers; the `priority` must differ.
- VRR: same virtual IP and MAC on both leaves, different real SVI addresses.
- For requirement 6, `nv config diff startup applied` empty on every switch is your proof.

## Appendix A — Quick command / config reference

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
| MLAG status | `nv show mlag` / `nv show mlag consistency-checker` |
| Pending vs applied | `nv config diff` |
| Applied config as commands | `nv config show -o commands` |
| LLDP neighbours | `nv show interface --view=lldp` or `nv show interface swp1 lldp` |
| Every valid path | `nv list-commands` |

Output formats: `-o json`, `-o yaml`, `-o commands`.

## Appendix B — Exam traps & key takeaways

- `nv config apply` is not `nv config save`. Apply changes the running system; save writes `/etc/nvue.d/startup.yaml`. Both are needed for a persistent change.
- `nv show` reflects *applied/operational* state; `nv config diff` shows what is pending. Reading `nv show` to confirm an un-applied change is a classic mistake.
- Revisions give rollback: `nv config history`, `nv config apply <rev>`. `nv config replace <yaml>` is the declarative path used by automation.
- Hand-editing `/etc/network/interfaces` or `/etc/frr/frr.conf` on an NVUE switch — overwritten at next apply.
- NCLU `net add` syntax no longer exists in Cumulus 5.x; the exam expects `nv`.
- A bond member cannot carry its own IP or bridge configuration; put VLAN/IP config on the bond.
- Breakout changes may disable adjacent ports or require `switchd` restart depending on platform.
- BGP unnumbered requires IPv6 link-local on the interface (the default) and `remote-as external`, not a literal ASN, in reference designs — identical config on every fabric link.
- VRR is active-active (Cumulus), VRRP is active-standby (standard). NVIDIA uses VRR with MLAG.
- MLAG `mac-address` and the bond `mlag id` must match on both peers; `backup` must point to the *other* peer's management IP; `priority` lower = primary.
- `cumulus` user has passwordless sudo; NVUE itself does not require sudo.
- Cumulus Linux = Debian + FRR + `switchd` syncing kernel state into the Spectrum ASIC. One VLAN-aware bridge `br_default`; `swpN`, `swpNsM`, `eth0`, `vlanN`.

## Appendix C — Knowledge check

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

---
## Solution guide — Hands-on assignment

### Topology

```text
        spine01 (10.0.0.1, AS65000)      spine02 (10.0.0.2, AS65000)
           swp1        swp2                 swp1        swp2
            |            \                 /             |
          swp51          swp52         swp51           swp52
        leaf01 (10.0.0.11, AS65011) ==peerlink swp49/50== leaf02 (10.0.0.12, AS65012)
          swp1 \                                        / swp1
                \________ server01 bond0 (LACP) _______/
                          10.1.10.10/24, gw 10.1.10.254
```

### Spines (spine02: hostname, lo 10.0.0.2/32, router-id 10.0.0.2)

```bash
nv set system hostname spine01
nv set interface lo ip address 10.0.0.1/32
nv set interface swp1-2 link state up
nv set router bgp autonomous-system 65000
nv set router bgp router-id 10.0.0.1
nv set vrf default router bgp neighbor swp1 remote-as external     # to leaf01
nv set vrf default router bgp neighbor swp2 remote-as external     # to leaf02
nv set vrf default router bgp address-family ipv4-unicast network 10.0.0.1/32
nv config apply -y
nv config save
```

### leaf01 (revision 1: identity + L3)

```bash
nv set system hostname leaf01
nv set interface lo ip address 10.0.0.11/32
nv set interface eth0 ip address 192.168.200.11/24          # Air mgmt network; adjust to your lab
nv set interface eth0 ip vrf mgmt
nv set router bgp autonomous-system 65011
nv set router bgp router-id 10.0.0.11
nv set vrf default router bgp neighbor swp51 remote-as external
nv set vrf default router bgp neighbor swp52 remote-as external
nv set vrf default router bgp address-family ipv4-unicast network 10.0.0.11/32
nv set vrf default router bgp address-family ipv4-unicast redistribute connected enable on   # requirement 5
nv config apply -y
nv show vrf default router bgp neighbor        # CHECKPOINT: swp51/swp52 Established, Remote-AS 65000
```

### leaf01 (revision 2: MLAG)

```bash
nv set interface peerlink bond member swp49,swp50
nv set mlag mac-address 44:38:39:BE:EF:AA          # IDENTICAL on leaf02
nv set mlag peer-ip linklocal                        # clagd over peerlink.4094 IPv6 LL
nv set mlag backup 192.168.200.12 vrf mgmt          # leaf02's eth0
nv set mlag priority 1000                            # lower than leaf02's default 32768 -> primary
nv set mlag init-delay 10
nv config apply -y
nv show mlag                                         # CHECKPOINT: peer alive may show "no" until leaf02 is done
```

### leaf01 (revision 3: bridge, bond, SVI)

```bash
nv set bridge domain br_default vlan 10
nv set interface bond1 bond member swp1
nv set interface bond1 bond mlag id 1                # IDENTICAL on leaf02
nv set interface bond1 bridge domain br_default access 10
nv set interface vlan10 ip address 10.1.10.1/24
nv set interface vlan10 ip vrr address 10.1.10.254/24
nv set interface vlan10 ip vrr mac-address 00:00:5e:00:01:0a
nv config apply -y
nv config save
```

### leaf02

Same three revisions with: hostname `leaf02`, lo `10.0.0.12/32`, eth0 `192.168.200.12/24`, ASN `65012`, router-id `10.0.0.12`, `mlag backup 192.168.200.11 vrf mgmt`, **no** `mlag priority` line (stays 32768 → secondary), SVI `10.1.10.2/24`. Keep `mlag mac-address`, `bond mlag id 1`, VRR address and VRR MAC identical.

### server01 (Ubuntu in Air)

```bash
sudo ip link add bond0 type bond mode 802.3ad lacp_rate fast
sudo ip link set eth1 down; sudo ip link set eth2 down
sudo ip link set eth1 master bond0; sudo ip link set eth2 master bond0
sudo ip link set eth1 up; sudo ip link set eth2 up; sudo ip link set bond0 up
sudo ip addr add 10.1.10.10/24 dev bond0
sudo ip route add 10.0.0.0/24 via 10.1.10.254
```

### Verifying each acceptance criterion

**MLAG roles** — leaf01:
```bash
nv show mlag
```
```text
                operational              applied
--------------  -----------------------  -----------------
enable                                   on
mac-address     44:38:39:be:ef:aa        44:38:39:be:ef:aa
peer-ip         fe80::...                linklocal
priority        1000                     1000
backup          192.168.200.12           192.168.200.12
backup-active   True
peer-alive      True
role            primary
```
leaf02 shows `role secondary`. Then `nv show mlag consistency-checker` — every row `Status: OK`; a VLAN or MTU mismatch between the peers appears here.

**Bond up with LACP partner** — on both leaves `nv show interface bond1 bond`: `swp1 up`, `mlag id 1`, and under LACP the partner system-id is server01's MAC. If leaf02 shows `swp1 down` while leaf01 is up, the `mlag id` differs (Knowledge check Q4).

**Ping from server01**:
```bash
ping -c3 10.1.10.254     # VRR anycast — answered by whichever leaf hashed the bond
ping -c3 10.0.0.1        # spine01 loopback via redistributed connected + BGP
```

**ECMP on spines** — `nv show vrf default router rib ipv4` on spine01 shows `10.1.10.0/24` with two next hops (`swp1` and `swp2`, IPv6 LL via RFC 5549). `sudo vtysh -c "show ip route 10.1.10.0/24"` lists both leaf paths with `*`.

**Persistence** — on every switch `nv config diff startup applied` returns nothing. Then `sudo reboot` leaf02; after it returns, `nv show mlag` recovers to `peer-alive True`, `nv show interface bond1 bond` swp1 up, and server01 never lost more than LACP failover time because leaf01 kept forwarding.

**History** — `nv config history` on leaf01 shows at least rev 1–3 with timestamps, satisfying the deliverable.
