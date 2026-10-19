# Module 2.1 — InfiniBand Network Administration

> **Exam domain:** NVIDIA InfiniBand Networking (30%)  
> **Source course(s):** InfiniBand Essentials; InfiniBand Network Administration  
> **Source docs:** NVIDIA MLNX-OS User Manual; MLNX_OFED / DOCA-OFED User Manual; OpenSM / MLNXSM Release Notes; NVIDIA Quantum-2 and Quantum-X800 product briefs  
> **Estimated study time:** 6–8 hours

## Learning objectives
- Describe the InfiniBand layered architecture and how it differs from the Ethernet/IP model you already know.
- Identify IB link generations (SDR through XDR), lane counts and per-port bandwidth, and map them to Quantum switch families.
- Explain the roles of GUID, LID, GID, PKey and QPN and which layer each belongs to.
- Describe what the **SM** (Subnet Manager) does, where it can run, how master/standby election works, and how to read its state.
- Select an appropriate routing engine (minhop, updn, ftree, dor, AR-capable engines) for a given topology.
- Configure partitions (**PKey**) for multi-tenancy, including full vs. limited membership and IPoIB child interfaces.
- Perform initial MLNX-OS switch provisioning and enable/verify the embedded SM.
- Verify a host HCA with **ibstat**, **ibstatus**, `ibv_devinfo`, and switch port mode with **mlxconfig**.

## 1. Why InfiniBand exists, and how it is layered

InfiniBand (IB) was designed from the start as a lossless, low-latency, RDMA-native fabric for clustered computing. Where Ethernet grew RDMA later (RoCE, see [Module 3.3](../03-rdma/03-roce-a-to-z.md)), IB was built around the idea that the network adapter (the **HCA**, Host Channel Adapter) moves data directly between application memory on two hosts without the host CPU or kernel touching the payload.

The IB specification defines a five-layer model that maps loosely onto OSI:

| IB layer | What it does | Rough OSI analogue | Key identifiers |
|---|---|---|---|
| Physical | Lanes (1x/4x/12x), encoding, signalling rate, cables/transceivers | L1 | — |
| Link | Local Route Header (LRH), **LID**-based forwarding inside a subnet, Virtual Lanes, credit-based flow control, link-level CRC | L2 | LID, SL, VL |
| Network | Global Route Header (GRH), **GID**-based routing between subnets (rarely used in practice; also carried for RoCE) | L3 | GID |
| Transport | Queue Pairs, reliable/unreliable service, segmentation to MTU, end-to-end ACK/retry, RDMA operations | L4 | QPN, PKey (checked here) |
| Upper layers / verbs | The `libibverbs` API, ULPs such as IPoIB, SRP, NFSoRDMA, and the libraries AI uses (NCCL, UCX, NVSHMEM) | L5–7 | — |

Two architectural points matter for everything else in this module:

1. **IB is centrally managed.** There is no spanning tree, no distributed routing protocol, no ARP-style learning. One entity, the **Subnet Manager**, discovers the whole subnet and programs every switch's forwarding table. If the SM is absent, links come up physically but nothing is forwarded and no host can talk.
2. **IB is lossless by design.** Credit-based flow control at the link layer means a transmitter only sends when the receiver has advertised buffer space. There is no PFC to tune, no ECN to turn on; this is the fundamental contrast with RoCE.

### Switches and the subnet
An IB *subnet* is the set of switches and end-ports under one SM, addressed by 16-bit LIDs. Almost every real-world fabric, including multi-thousand-GPU SuperPODs, is a single subnet. Routers exist (for connecting subnets via GIDs) but are a niche topic for the exam; know that they exist and that GIDs are what cross them.

## 2. Link speeds and the Quantum family

Each generation names the *per-lane* signalling rate; a standard port is 4 lanes (4x). The exam expects you to know the table below, and especially the Quantum-2 and Quantum-X800 numbers.

| Generation | Per-lane rate | 4x port (nominal) | Encoding | Typical switch ASIC |
|---|---|---|---|---|
| SDR | 2.5 Gb/s | 10 Gb/s | 8b/10b | — |
| DDR | 5 Gb/s | 20 Gb/s | 8b/10b | — |
| QDR | 10 Gb/s | 40 Gb/s | 8b/10b | — |
| FDR | 14 Gb/s | 56 Gb/s | 64b/66b | SwitchX |
| EDR | 25 Gb/s | 100 Gb/s | 64b/66b | Switch-IB / Switch-IB 2 |
| HDR | 50 Gb/s | 200 Gb/s | 64b/66b, PAM4 | Quantum (QM8700, 40 ports) |
| NDR | 100 Gb/s | 400 Gb/s | PAM4 | **Quantum-2** (QM9700/QM9790, **64 × 400G ports**, 32 OSFP cages, 51.2 Tb/s) |
| XDR | 200 Gb/s | 800 Gb/s | PAM4 | **Quantum-X800** (Q3400 series, **144 × 800G ports**, 115 Tb/s) |

Notes that come up as exam distractors:

- Quantum-2 physically has 32 OSFP cages; each cage carries two 400G NDR ports, giving 64 ports. Splitting an NDR port further (NDR200, 2x) is possible with breakout cables, which is how 128 × 200G HDR-speed connections to older HCAs are achieved.
- "HDR100" is a 2-lane HDR port running at 100 Gb/s — same wire rate as EDR but a different protocol generation. Likewise "NDR200" is 2-lane NDR.
- Quantum-X800 pairs with the ConnectX-8 SuperNIC and Spectrum-X800 on the Ethernet side; it is the first IB generation to integrate SHARPv4 at 800G (see [Module 2.3](03-infiniband-qos-adaptive-routing-sharp.md)).
- Link width and speed are negotiated; **ibstat** shows the result as `Rate: 400` with `Active width: 4X` and `Active speed: 100 Gbps` (per lane). If you see `Rate: 200` on an NDR cable, a lane or speed negotiation problem exists; this is a classic troubleshooting scenario covered with **mlxlink** in Module 8.

## 3. Addressing: GUID, LID, GID, PKey, QPN

New IB administrators confuse these constantly. Learn them by *scope* and *who assigns them*.

| Identifier | Size | Scope | Assigned by | Analogy |
|---|---|---|---|---|
| **GUID** (Globally Unique ID) | 64-bit | Permanent; burned in | Vendor (EUI-64) | MAC address |
| **LID** (Local ID) | 16-bit | One subnet; assigned at discovery, can change | **SM** | A dynamically assigned L2 address used for switching |
| **GID** (Global ID) | 128-bit | Global; `subnet prefix (64) + port GUID (64)` | Derived (default prefix `fe80::/64`) | IPv6 link-local address |
| **PKey** (Partition Key) | 16-bit | Subnet; membership enforced at HCA/switch | SM via partitions.conf | VLAN tag |
| **QPN** (Queue Pair Number) | 24-bit | Per HCA port | HCA at QP creation | TCP/UDP port number |

Details worth knowing:

- Every HCA has a **node GUID**, each **port** has its own **port GUID**, and the system has a **system image GUID**. Switches also have node and port GUIDs. Tools such as **ibnodes**, **iblinkinfo** and **ibdiagnet** key everything on GUIDs because LIDs can change between SM runs.
- LIDs range 1–0xBFFF for unicast; 0xC000–0xFFFE are multicast LIDs. LID 0 is reserved and 0xFFFF is the permissive LID used by SM/SMA traffic before LIDs exist. The SM may assign a *range* of LIDs to a port (LMC, LID Mask Control) to enable multiple paths; `ibstat` shows `LMC: 0` in most installs.
- A packet carries LRH (with source/destination LID) always, and GRH (with GIDs) only when it leaves the subnet or when the application chose to include it. RoCE always uses the GRH since there are no LIDs on Ethernet; this is why `show_gids` matters so much in Module 3.3.
- The PKey is in the Base Transport Header and is validated on every packet by the HCA and optionally by switch ports. A mismatch is dropped silently and counted in `PortRcvConstraintErrors`-style counters.

## 4. The Subnet Manager

### 4.1 What the SM does
The **SM** is the brain of the subnet. On start-up and on every topology change (a trap from a switch when a port goes up or down), it runs a sweep:

1. **Discovery** — walks the fabric using directed-route SMPs (Subnet Management Packets, sent on the permissive LID over VL15 with the QP0 management queue pair) and builds a database of all nodes, ports and links.
2. **LID assignment** — gives every end-port and switch a LID (persistent across restarts if `guid2lid` caching is used).
3. **Routing table computation** — runs the selected routing engine and writes a **Linear Forwarding Table (LFT)** into every switch: for each destination LID, which output port to use. Multicast forwarding tables (MFT) are also programmed for IPoIB broadcast/multicast groups.
4. **Port configuration** — sets MTU, link operational VLs, **SL→VL mapping** tables, VL arbitration tables, PKey tables on switch/HCA ports.
5. **Subnet Administration (SA)** — the SM also runs the SA, a database service hosts query (via QP1 / GSI) to resolve paths: "I want to talk to GID X, give me a path record (DLID, SL, MTU, rate)". `ibping`, IPoIB neighbour resolution and RDMA CM all rely on SA queries.

Heavy sweeps are the source of the common complaint "the fabric stalled for a few seconds when a node rebooted"; large fabrics tune sweep behaviour and use `ibdiagnet` rather than making the SM poll constantly.

### 4.2 Where the SM runs
Three options, in order of what you will meet in production:

| Location | Product | When used |
|---|---|---|
| **Host-based** | `opensm` package from MLNX_OFED / DOCA-OFED, or the enhanced `MLNXSM` | Small fabrics, labs, clusters where a management node exists |
| **Switch-embedded** | MLNX-OS on managed Quantum switches (`ib sm` command) | Up to roughly 2,000 nodes — NVIDIA documents a recommended upper bound; verify the figure for your switch model |
| **UFM** | UFM Enterprise / Cyber-AI appliance or software ([Module 2.2](02-ufm-dc-management.md)) | Large fabrics, SuperPODs; adds telemetry, REST API, HA |

A fabric must have exactly one *master* SM. You may (and should) run several SMs for redundancy; the others sit in standby.

### 4.3 Priority and master election
Every SM has a **priority** 0–15 (15 is highest). When multiple SMs are present:

- They discover each other via SMInfo attributes during sweeps.
- The SM with the **highest priority** becomes master. On a tie, the **lowest port GUID** wins.
- The master periodically polls standbys; standbys poll the master. If the master disappears (polling times out), the next-best standby runs a discovery and takes over. Because LIDs are cached (`guid2lid` file / `lid_matrix` on MLNX-OS), the takeover is normally hitless for existing connections.
- Some implementations support a *master_sm_priority* behaviour so a recovered high-priority SM does not immediately pre-empt a running master (the "sticky master" or `master_sm_priority` option in opensm.conf; check the current doc).

### 4.4 SM states
You will read these in `show ib sm` on MLNX-OS, in `sminfo` on a host, or in UFM:

| State | Meaning |
|---|---|
| **DISCOVERING** | SM is sweeping; it has not yet decided whether to be master |
| **MASTER** | This SM owns the subnet and programs routing |
| **STANDBY** | Another SM with higher priority is master; this one is ready to take over |
| **NOT ACTIVE** | SM process is up but does not participate (e.g. configured not to run or lost its port) |

`sminfo` from any host prints the master SM's LID, GUID, priority and state, which is the fastest way to confirm "do we have a master, and who is it?"

## 5. Routing engines

The SM's routing engine turns topology into LFTs. The choice is set with `routing_engine` in `opensm.conf` (comma-separated list; the first one that succeeds is used).

| Engine | How it routes | Use when |
|---|---|---|
| **minhop** | Shortest-path, balances by port load; default fallback | Arbitrary or unknown topology; small fabrics |
| **updn** | Up/down routing: avoids credit loops by forbidding "down then up" turns, needs root GUIDs | Non-pure fat-trees with irregular connectivity where loops would deadlock |
| **ftree** | Fat-tree-aware: detects CBB (constant bisectional bandwidth) trees, spreads destinations across spines deterministically, deadlock-free | **Clos/fat-tree fabrics — the normal choice for every GPU cluster** (SuperPOD, BasePOD). Falls back to minhop if topology is not a valid fat-tree |
| **dor** | Dimension-order routing for meshes/tori/hypercubes | Torus topologies (rare in AI DCs) |
| **torus-2QoS** | Torus with two QoS levels for deadlock avoidance | Large tori (HPC) |
| **sssp / dfsssp** | Single-source shortest path, deadlock-free variant | Irregular HPC topologies |
| **ar_ftree / ar_updn / ar_dor / ar_torus** | Adaptive-routing-capable variants (MLNXSM) | When you want **adaptive routing** on Quantum switches — covered in [Module 2.3](03-infiniband-qos-adaptive-routing-sharp.md) |

Why `ftree` for AI fabrics: static routing in a Clos has a known weakness — two flows hashed onto the same spine-bound link collide. `ftree` at least guarantees deterministic, balanced LFTs and deadlock freedom; adaptive routing is layered on top of it to fix the collision problem at run time. `ftree` needs to identify the roots (spines); it usually auto-detects them, but a `root_guid_file` is recommended for anything non-trivial and is mandatory for `updn`.

Relevant `opensm.conf` keys (names are stable across OpenSM 3.x/MLNXSM 5.x; always diff against the shipped default file):

```text
# /etc/opensm/opensm.conf (host-based) — key routing parameters
routing_engine ftree,minhop         # try ftree, fall back to minhop
root_guid_file /etc/opensm/root_guids.conf
guid_routing_order_file /etc/opensm/guid_routing_order.conf   # optional, ftree
lmc 0
sm_priority 14
sweep_interval 10                   # seconds; 0 disables periodic sweeps
subnet_prefix 0xfe80000000000000
guid2lid_file /var/cache/opensm/guid2lid
partition_config_file /etc/opensm/partitions.conf
qos TRUE
```

## 6. Partitions (PKeys) for multi-tenancy

Partitions are IB's VLAN equivalent, enforced per packet at the transport layer. The 16-bit field is split: the low 15 bits are the partition number, the high bit is the **membership** bit.

| Value | Meaning |
|---|---|
| `0x7fff` | **Default partition** (full-membership form `0xffff`). Every port is a full member unless the SM is told otherwise |
| `0x0001`–`0x7ffe` | Admin-defined partitions (limited membership form) |
| `0x8000 \| key` (high bit set, e.g. `0x8001`) | **Full membership** in that partition |
| high bit clear (e.g. `0x0001`) | **Limited membership** |

Membership rules, which the exam loves:

- **Full ↔ full**: can communicate.
- **Full ↔ limited**: can communicate.
- **Limited ↔ limited**: **cannot** communicate.

Use case: put a shared storage server as a *full* member and tenants' compute nodes as *limited* members of the same PKey. Each tenant reaches storage, but tenants cannot see each other — without needing N separate partitions.

### 6.1 partitions.conf syntax
On a host-based SM the file is `/etc/opensm/partitions.conf` (path set by `partition_config_file`). On MLNX-OS, partitions are configured with `ib partition` CLI commands; on UFM through the web UI/REST, which writes the same file under `/opt/ufm/files/conf/opensm/partitions.conf`.

```text
# name=pkey[,flags...] : port-guid=membership, ... ;
Default=0x7fff, ipoib, mtu=5, rate=7, sl=0, defmember=full : ALL, SELF=full ;

# Tenant A: compute nodes limited, storage full
tenantA=0x0a01, ipoib, defmember=limited :
    0x0c42a10300a1b2c0=full,          # storage HCA port GUID
    0x0c42a10300a1b2c1=full,
    0xb83fd20300112233,               # compute node (inherits defmember=limited)
    0xb83fd20300112244 ;

# Tenant B: everyone full, SL 1 so QoS can isolate it
tenantB=0x0a02, ipoib, sl=1 : 0xb83fd20300556677=full, 0xb83fd20300556688=full ;
```

Flags you should recognise:

- `ipoib` — create the IPoIB broadcast multicast group for this partition (without it, IPoIB will not come up on the child interface).
- `mtu=` and `rate=` — encoded values for the multicast group (mtu 4 = 2048, 5 = 4096; rate 7 = 40G, 16 = 100G, 17 = 200G, etc.; check the table in the opensm man page).
- `sl=` — service level for the group, which ties partitions to QoS (Module 2.3).
- `defmember=full|limited` — default membership for ports listed without an explicit one.
- `ALL`, `ALL_CAS`, `ALL_SWITCHES`, `ALL_ROUTERS`, `SELF` — keywords instead of GUID lists.

A port may be a member of up to the size of its PKey table (typically 128 entries on ConnectX; `ibv_devinfo -v` shows `pkey_tbl_len`). After editing the file, send `SIGHUP` to opensm (`kill -HUP $(pidof opensm)`) or restart it; the SM reprograms PKey tables on the next sweep.

### 6.2 IPoIB over a PKey: child interfaces
Each HCA port's IPoIB device (e.g. `ib0`) runs in the default partition. To carry IP traffic in another partition you create a *child* interface whose name is `<parent>.<pkey-hex>` and whose PKey has the full-membership bit set:

```bash
# Create child interface for PKey 0x0a01 (full member form 0x8a01)
echo 0x8a01 > /sys/class/net/ib0/create_child
ip link set ib0.8a01 up
ip addr add 10.10.1.21/24 dev ib0.8a01

# or persistently with NetworkManager / netplan; MLNX_OFED also supports
# /etc/infiniband/openib.conf-style child definitions on some distros.
```

If the SM did not grant this port membership (or granted only limited and the peer is also limited), the child interface will stay in `NO-CARRIER` or ping will fail with no counter increments on the switch — the classic "PKey mismatch" symptom.

## 7. MLNX-OS switch provisioning

Managed Quantum switches (QM87xx, QM97xx, Q3400 managed variants) run **MLNX-OS**, a Cisco-flavoured CLI with `enable` / `configure terminal` modes. Unmanaged variants (e.g. QM9790) have no CPU running an OS and are fully dependent on an external SM/UFM.

### 7.1 Initial setup
On first boot, connect to the console (115200 8N1) and log in as `admin/admin`. A configuration **wizard** runs:

```text
Do you want to use the wizard for initial configuration? yes
Step 1: Hostname? ib-spine-01
Step 2: Use DHCP on mgmt0 interface? no
Step 3: Use zeroconf on mgmt0 interface? no
Step 4: Primary IPv4 address and masklen? 10.0.100.11/24
Step 5: Default gateway? 10.0.100.1
Step 6: Primary DNS server? 10.0.0.53
Step 7: Domain name? dc1.example.net
Step 8: Enable IPv6? no
Step 9: Admin password? ********
```

After the wizard you can SSH to the management IP and use the CLI or the web UI (HTTPS). Same settings by hand:

```text
switch > enable
switch # configure terminal
switch (config) # hostname ib-spine-01
ib-spine-01 (config) # interface mgmt0 ip address 10.0.100.11 /24
ib-spine-01 (config) # ip default-gateway 10.0.100.1
ib-spine-01 (config) # ntp server 10.0.0.123
ib-spine-01 (config) # configuration write
```

### 7.2 Enabling and checking the embedded SM

```text
ib-spine-01 (config) # ib sm                      # enable SM on this switch
ib-spine-01 (config) # ib sm sm-priority 13       # 0-15; higher wins
ib-spine-01 (config) # ib sm routing-engines ftree
ib-spine-01 (config) # no ib sm                   # disable it

ib-spine-01 # show ib sm
SM Configuration
  SM enabled:            yes
  SM state:              master
  SM priority:           13
  Routing engine:        ftree
  ...
ib-spine-01 # show ib sm log                      # recent SM events
ib-spine-01 # show ib smnodes                     # all SMs seen in the subnet and their states
```

Exact sub-command names vary slightly by MLNX-OS release (e.g. `ib sm virt enable`, `ib sm sharp enable`); use `ib sm ?` on the box and verify in the user manual for the release.

### 7.3 Interfaces and inventory

```text
ib-spine-01 # show interfaces ib status
Interface   Description   Speed      Width   Current line rate   Logical port state   Physical port state
----------  ------------  ---------  ------  ------------------  -------------------  -------------------
IB1/1                     NDR        4X      400 Gbps            Active               LinkUp
IB1/2                     NDR        4X      400 Gbps            Active               LinkUp
IB1/3                     --         --      --                  Down                 Polling
...
ib-spine-01 # show interfaces ib 1/1 counters      # link-level error counters
ib-spine-01 # show ib fabric                        # SM's view of discovered nodes (when SM is local)
ib-spine-01 # show inventory
ib-spine-01 # show version
ib-spine-01 (config) # interface ib 1/3 shutdown    # administratively disable a port
ib-spine-01 (config) # interface ib 1/3 description "to leaf-07 port 33"
```

Port states to recognise: **Physical** `Polling` (no partner/cable), `Disabled`, `LinkUp`; **Logical** `Down`, `Initialize` (link is up but SM has not yet configured it — a tell-tale sign *there is no master SM*), `Armed`, `Active`.

### 7.4 Firmware and image upgrade

```text
ib-spine-01 (config) # image fetch scp://admin@10.0.0.50/images/image-X86_64-3.12.xxxx.img
ib-spine-01 (config) # show images
ib-spine-01 (config) # image install image-X86_64-3.12.xxxx.img
ib-spine-01 (config) # image boot next
ib-spine-01 (config) # configuration write
ib-spine-01 (config) # reload
```

MLNX-OS images bundle the switch ASIC firmware; a separate firmware step is not normally needed on managed switches. Unmanaged switches get firmware via **mlxfwmanager**/**flint** from a host or via UFM. Switch-to-SM compatibility and "upgrade standbys first, master last" ordering should be checked in the release notes for each hop.

### 7.5 What "HA" means in IB management
Two unrelated things get called HA, and the exam may test that you can tell them apart:

- **SM HA** — simply running **multiple SMs with different priorities** (e.g. two spines with `ib sm` enabled at priorities 13 and 12, or a UFM HA pair). Failover is handled by the master/standby protocol described in §4.3. There is no shared state beyond the cached LIDs.
- **MLNX-OS management HA / UFM HA** — redundancy of the *management application* itself: UFM uses an active/standby pair with replicated storage (see Module 2.2). Some MLNX-OS modular systems also replicate the management module. This protects the management plane, not the data plane; the data plane keeps forwarding on its programmed LFTs even when every SM is down — but no new links or hosts will be configured until an SM returns.

## 8. Host side: OFED, tools and port configuration

### 8.1 MLNX_OFED and DOCA-OFED
NVIDIA's host driver stack has historically shipped as **MLNX_OFED** (`mlnxofedinstall` script, `.iso`/`.tgz`). Since 2024 the stack is distributed as part of **DOCA** (`doca-ofed` / `doca-all` meta-packages, installed via the distro package manager after adding NVIDIA's repo), and MLNX_OFED standalone is end-of-life as of the 23.10 / 24.x branches. Functionally the same kernel modules (`mlx5_core`, `mlx5_ib`, `ib_ipoib`, `ib_umad`, `rdma_cm`) and user tools arrive either way.

```bash
# DOCA-OFED on Ubuntu (repo set-up omitted; follow the DOCA installation guide)
sudo apt install doca-ofed
sudo /etc/init.d/openibd restart          # or reboot
# Legacy MLNX_OFED
sudo ./mlnxofedinstall --with-nfsrdma --without-fw-update
```

Inbox (distro) `rdma-core` works for basic IB, but NVIDIA features such as SHARP, DOCA libraries, and the latest firmware tools need the NVIDIA stack. The **MFT** (Mellanox Firmware Tools: `mst`, `mlxconfig`, `mlxlink`, `flint`, `mlxfwmanager`) comes with it.

### 8.2 Verifying the HCA

```bash
$ ibstat
CA 'mlx5_0'
    CA type: MT4129
    Number of ports: 1
    Firmware version: 28.41.1000
    Hardware version: 0
    Node GUID: 0xb83fd203001a2b3c
    System image GUID: 0xb83fd203001a2b3c
    Port 1:
        State: Active
        Physical state: LinkUp
        Rate: 400
        Base lid: 37
        LMC: 0
        SM lid: 1
        Capability mask: 0xa651e848
        Port GUID: 0xb83fd203001a2b3c
        Link layer: InfiniBand
```

Reading it: `State: Active` means the SM has configured the port; `Initializing` with `Physical state: LinkUp` means *link is fine but no SM*. `SM lid` tells you where the master is. `Link layer: InfiniBand` vs `Ethernet` tells you the port mode.

```bash
$ ibstatus                         # terse per-port summary incl. rate and link_layer
$ ibv_devices                      # list device names and node GUIDs
$ ibv_devinfo -d mlx5_0 -v         # verbose capabilities: max QPs, MRs, pkey_tbl_len, GIDs, MTU
$ sminfo                           # who is master SM, priority, state
$ ibnodes ; ibswitches ; ibhosts   # discovery from this host (needs SM-managed fabric)
$ iblinkinfo                       # every link, width/speed, both ends
$ ibdiagnet                        # full fabric health report (Module 8)
```

### 8.3 opensm.conf essentials for a host-based SM

```bash
sudo opensm --create-config /etc/opensm/opensm.conf   # dump defaults, then edit
sudo systemctl enable --now opensm                     # or opensmd on older distros
sudo journalctl -u opensm -f                           # watch "SUBNET UP" after sweep
```

Key parameters already shown in §5; also: `guid` (bind SM to a specific port GUID on multi-port hosts), `log_file`, `log_flags`, `sminfo_polling_timeout`, `polling_retry_number` (how fast standby detects master loss), `qos_*` (Module 2.3), `ipoib_mtu`/`max_op_vls`. When UFM is used, do **not** also run `opensm` on hosts — UFM's SM must be the only master.

### 8.4 Setting port type with mlxconfig
ConnectX VPI adapters can run each port as IB or Ethernet. The setting is in firmware and takes effect after a reboot/driver reload:

```bash
sudo mst start
sudo mlxconfig -d /dev/mst/mt4129_pciconf0 query | grep LINK_TYPE
         LINK_TYPE_P1                        IB(1)
         LINK_TYPE_P2                        ETH(2)
sudo mlxconfig -d /dev/mst/mt4129_pciconf0 set LINK_TYPE_P1=1 LINK_TYPE_P2=1   # 1 = IB, 2 = ETH, 3 = VPI/auto (where supported)
sudo reboot
```

Note: on BlueField and some ConnectX-7/8 SKUs the port mode is fixed or set differently; `mlxconfig -d <dev> query` shows what is tunable. **mlxconfig** also controls `NUM_OF_VFS`, `SRIOV_EN`, `ADVANCED_PCI_SETTINGS`, and ATS/relaxed-ordering knobs relevant to GPUDirect.

## 9. IPoIB: datagram vs connected mode

IPoIB encapsulates IP in IB packets so legacy TCP/UDP applications (and SSH, NFS, Slurm control traffic) work over the fabric. It is *not* RDMA and is slow relative to verbs, but every cluster uses it for management.

| Mode | Transport | Max MTU | Pros / cons |
|---|---|---|---|
| **Datagram (UD)** | Unreliable Datagram QP, one QP per interface | Limited to IB link MTU minus 4-byte IPoIB header: **2044** at 2K IB MTU, **4092** at 4K IB MTU | Supports multicast (so ARP/ND works natively), lower memory, works with SR-IOV VFs and many offloads; default and recommended on modern stacks |
| **Connected (CM)** | Reliable Connected QP per peer | Up to **65520** | Segmentation done in hardware per connection; good single-stream TCP throughput, but one RC QP per neighbour, no multicast (falls back to UD for broadcast), poor scaling to thousands of peers |

```bash
cat /sys/class/net/ib0/mode             # datagram | connected
echo connected > /sys/class/net/ib0/mode
ip link set ib0 mtu 65520
# Return to datagram with the IB-MTU-limited size
echo datagram > /sys/class/net/ib0/mode && ip link set ib0 mtu 4092
```

The IB link MTU itself (2048 or 4096 bytes, set by the SM as `partition mtu=` and limited by HCA/switch capability) caps datagram IPoIB. RDMA applications negotiate the *path MTU* separately (Module 3.1). Choosing 4096 fabric MTU is standard in GPU clusters.

## Configuration / command reference

| Task | Command |
|---|---|
| Host HCA status | `ibstat`, `ibstatus`, `ibv_devinfo -v`, `ibv_devices` |
| Who is master SM | `sminfo`; MLNX-OS `show ib sm`, `show ib smnodes` |
| Enable SM on MLNX-OS | `ib sm`, `ib sm sm-priority <0-15>`, `ib sm routing-engines ftree` |
| Port status on switch | `show interfaces ib status`, `show interfaces ib 1/<n> counters` |
| Disable a switch port | `interface ib 1/<n> shutdown` |
| Switch image upgrade | `image fetch <url>`, `image install <file>`, `image boot next`, `reload` |
| Start host SM | `systemctl enable --now opensm`; config `/etc/opensm/opensm.conf` |
| Reload partitions | edit `partitions.conf`, `kill -HUP $(pidof opensm)` |
| IPoIB child interface | `echo 0x8<pkey> > /sys/class/net/ib0/create_child` |
| Port type IB/ETH | `mlxconfig -d <mst dev> set LINK_TYPE_P1=1` (1=IB, 2=ETH) |
| IPoIB mode | `echo connected|datagram > /sys/class/net/ib0/mode` |
| Fabric discovery from host | `ibnodes`, `ibswitches`, `ibhosts`, `iblinkinfo`, `ibdiagnet` |

## Common pitfalls & exam traps
- **Port `Initialize`/`Initializing` with `LinkUp`** = no master SM, not a cable problem. Candidates reach for mlxlink when they should run `sminfo`.
- **GUID vs LID**: GUIDs are permanent and vendor-assigned; LIDs are SM-assigned and can change. Scripts and partitions key on GUIDs.
- **PKey membership**: limited-to-limited cannot talk; `0x7fff` is the default partition and `0x8000` is the full-membership bit, so `0xffff` is "default, full". A child interface must be created with the full-membership form (`0x8xxx`) of the key.
- **Highest priority wins**, tie → *lowest* GUID. Priority 15 is maximum.
- **ftree** is the standard engine for Clos fabrics; `updn` requires root GUIDs and is for irregular trees; `dor`/torus engines are for meshes.
- **Quantum-2 = 64 × NDR 400G in 32 OSFP cages**; **Quantum-X800 = 144 × XDR 800G**. Do not confuse port count with cage count.
- **IPoIB MTU**: datagram is capped by IB MTU (2044/4092); connected mode allows 65520 but does not support multicast and scales poorly.
- **Unmanaged switches** (e.g. QM9790) cannot run an SM; they need a host SM or UFM. Managed ones (QM9700) can.
- Running `opensm` on a host *and* UFM/switch SM simultaneously is allowed in principle (election sorts it out) but is a configuration smell; UFM docs tell you to disable every other SM.
- MLNX_OFED is superseded by DOCA-OFED; the tool names are unchanged.

## Key takeaways
- IB is centrally managed: no SM, no forwarding. The SM discovers, assigns LIDs, computes LFTs, programs SL2VL/VLArb/PKey tables and answers SA path queries.
- GUID = permanent (like MAC); LID = SM-assigned 16-bit subnet address; GID = prefix + GUID (like IPv6 LL); PKey = VLAN-like partition; QPN = like a port number.
- SDR 10 → DDR 20 → QDR 40 → FDR 56 → EDR 100 → HDR 200 → NDR 400 → XDR 800 Gb/s per 4x port.
- Quantum-2: NDR, 64 × 400G; Quantum-X800: XDR, 144 × 800G.
- SM election: highest priority wins, ties broken by lowest GUID; states are DISCOVERING / MASTER / STANDBY / NOT ACTIVE.
- SM can run on a host (opensm/MLNXSM), in MLNX-OS (`ib sm`), or in UFM.
- `routing_engine ftree` for Clos; AR variants add adaptive routing.
- PKey 0x7fff is default; bit 0x8000 = full membership; limited↔limited blocked.
- partitions.conf: `name=pkey,ipoib,defmember=... : guid=full, ... ;`
- `ibstat` state `Active` requires an SM; `Initializing` means link up, no SM.
- `mlxconfig LINK_TYPE_P1=1` → IB, `=2` → Ethernet.
- IPoIB datagram MTU 2044/4092; connected mode up to 65520.

## Review questions

1. A new node's HCA shows `State: Initializing`, `Physical state: LinkUp`, `Rate: 400` in `ibstat`. What is the most likely cause?
   A. A bad transceiver on the switch side
   B. No master Subnet Manager is active in the subnet
   C. The port is set to Ethernet mode
   D. The PKey table is full

2. Two SMs are running: switch A at priority 10 with port GUID 0x...0010, switch B at priority 10 with port GUID 0x...0002. Which becomes master?
   A. Switch A, because higher GUID wins
   B. Switch B, because lower GUID wins on a priority tie
   C. Whichever booted first
   D. Neither; equal priorities cause both to go NOT ACTIVE

3. Two compute nodes are both *limited* members of PKey 0x0a01, and a storage node is a *full* member. Which statement is true?
   A. All three can communicate
   B. Compute nodes can reach storage but not each other
   C. Compute nodes can reach each other but not storage
   D. No communication is possible until all are full members

4. Which routing engine should you configure for an NDR Quantum-2 fat-tree fabric serving GPU servers, before enabling adaptive routing?
   A. minhop
   B. dor
   C. ftree
   D. torus-2QoS

5. How many 400G ports does a Quantum-2 QM9700 switch provide, and in how many physical cages?
   A. 32 ports in 32 cages
   B. 64 ports in 64 cages
   C. 64 ports in 32 OSFP cages
   D. 128 ports in 64 cages

6. An administrator wants to run IP traffic from `ib0` inside partition 0x0a02 and runs `echo 0x0a02 > /sys/class/net/ib0/create_child`. What is wrong?
   A. Nothing; the interface will work
   B. The PKey must be written with the full-membership bit set (0x8a02)
   C. Child interfaces require connected mode
   D. Partitions can only be created through UFM

7. Which identifier is burned into the HCA by the vendor and never changes?
   A. LID
   B. GID
   C. GUID
   D. QPN

8. A host IPoIB interface is in datagram mode on a 4096-byte MTU fabric. What is the maximum interface MTU?
   A. 1500
   B. 2044
   C. 4092
   D. 65520

### Answer key
1. B — Link is physically up but the port stays in Initialize until an SM programs it; check `sminfo`.
2. B — Equal priority is resolved by the lowest port GUID.
3. B — Full↔limited works; limited↔limited is blocked by design.
4. C — ftree is the deadlock-free, balanced engine for Clos/fat-tree topologies.
5. C — Each OSFP cage carries two NDR 400G ports.
6. B — Child interfaces are created with the 0x8000 full-membership bit set.
7. C — GUIDs are EUI-64 vendor-assigned; LIDs are SM-assigned, GIDs are derived, QPNs are per-connection.
8. C — 4096-byte IB MTU minus the 4-byte IPoIB encapsulation header.

## References
- NVIDIA Academy: *InfiniBand Essentials*; *InfiniBand Network Administration*
- NVIDIA MLNX-OS User Manual (Quantum / Quantum-2 switch systems) — https://docs.nvidia.com/networking/ (MLNX-OS section)
- NVIDIA DOCA-OFED / MLNX_OFED User Manual — InfiniBand Fabric utilities, IPoIB, OpenSM chapters — https://docs.nvidia.com/networking/
- MLNXSM (InfiniBand Subnet Manager) Utility Release Notes — https://docs.nvidia.com/networking/display/mlnxsmrnv5150
- NVIDIA Quantum-2 QM9700/QM9790 and Quantum-X800 Q3400 product briefs — https://www.nvidia.com/en-us/networking/infiniband-switching/
- OpenSM man pages: `opensm(8)`, `partitions.conf` section
- Related modules: [2.2 UFM](02-ufm-dc-management.md), [2.3 QoS/AR/SHARP](03-infiniband-qos-adaptive-routing-sharp.md), [3.1 RDMA fundamentals](../03-rdma/01-rdma-fundamentals.md)
