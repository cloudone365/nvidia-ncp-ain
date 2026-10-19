# Module 5.1 — BlueField DPU Administration

> **Exam domain:** NVIDIA Spectrum Networking (30%) — SuperNIC/DPU items; also touches AI Data Center Design (5%)  
> **Source course(s):** BlueField DPU Administration (NVIDIA Academy)  
> **Source docs:** NVIDIA BlueField-3 DPU/SuperNIC Product Documentation; BlueField DPU BSP (BlueField Software) Documentation; DOCA Installation Guide; MFT (Mellanox Firmware Tools) User Manual; BlueField Modes of Operation  
> **Estimated study time:** 5 hours

## Learning objectives
- Describe the BlueField-3 architecture (Arm subsystem, ConnectX-7 network engine, DPA, memory, PCIe) and distinguish a DPU from a **SuperNIC**.
- Explain the BlueField modes of operation (DPU mode, NIC mode, zero-trust/restricted host) and switch between them with **mlxconfig**.
- Install a BlueField software image (BFB) over RShim with `bfb-install` and access the Arm console and network over `/dev/rshim0`.
- Update NIC/DPU firmware with `mlxfwmanager` and set key `mlxconfig` parameters (SR-IOV, VF count, link type).
- Describe the host/DPU interface relationships (p0/p1, pf0hpf, pf0vfN representors) and enable OVS hardware offload on the DPU.
- Describe BlueField management paths (RShim, BMC, OOB port), bfcfg/cloud-init provisioning, and the DPU Provisioning Framework.
- Perform basic troubleshooting with `mst status`, `ibdev2netdev`, `mlxconfig -q` and `mlxlink`.

## 1. BlueField-3 architecture

The **BlueField-3** is NVIDIA's third-generation data processing unit: a full server-class system on a PCIe card that also happens to be a 400G NIC. Its building blocks:

| Block | Detail | Why it matters |
|---|---|---|
| Arm subsystem | 16 Arm Cortex-A78 cores (8 on some SKUs), running Ubuntu/other Linux from an eMMC/NVMe | Runs infrastructure software (OVS, firewall, storage target, HBN routing, telemetry) *off* the host CPU and *outside* the host's trust domain |
| Network engine | Integrated **ConnectX-7** class NIC: 1×400G or 2×200G (also 2×400G on some SKUs), RoCE, GPUDirect, hardware packet processing (flow steering, encryption, ASAP² switching) | Same RDMA/RoCE data path as a ConnectX-7 card; wire-speed OVS/flow offload |
| **DPA** (Data Path Accelerator) | 16 programmable RISC-V style cores for packet-paced datapath code, programmed via DOCA DPA | Hosts RoCE congestion-control algorithms and custom datapath logic — the engine behind Spectrum-X congestion control |
| Memory | 16–32 GB DDR5 on-card (SKU dependent) | Local memory for the Arm OS and for datapath tables independent of the host |
| PCIe | Gen5 x16 host interface; also acts as a PCIe switch/root for downstream devices (NVMe, GPUs in some designs) | Can present itself to the host as a NIC while owning devices behind it |
| Accelerators | Crypto (IPsec/TLS inline), compression, regex, SHA | Zero-trust and storage offloads at line rate |
| Management | **RShim** (over PCIe or USB), BMC with its own 1GbE OOB port, UART console | Out-of-band control even when the host or Arm OS is down |

Think of it as three personalities sharing one card: a NIC the host sees; a Linux server (the Arm side) sitting in the network path between the host and the wire; and a management controller.

## 2. DPU vs SuperNIC

Both are BlueField-3 silicon; the difference is firmware personality, form factor and intended role:

| | BlueField-3 **DPU** | BlueField-3 **SuperNIC** |
|---|---|---|
| Purpose | Infrastructure offload: OVS/vSwitch, firewall, storage (NVMe-oF, SNAP), telemetry, tenant isolation, HBN | GPU-to-GPU east-west RoCE networking in AI clusters — the NIC side of **Spectrum-X** |
| Ports | 2×200G or 1×400G, typical | 1×400G (also 2×200G) per GPU, 8 per DGX/HGX node |
| Arm usage | Fully used: runs the infrastructure stack | Minimal; the Arm cores are present but the card is tuned as a high-performance NIC with the DPA running Spectrum-X congestion control |
| Key features | DOCA services, zero-trust host isolation, PCIe switch | **RoCE adaptive routing** support with out-of-order tolerance (Direct Data Placement), programmable congestion control, performance isolation, low-profile/power-optimised |
| Pairs with | Any Ethernet switch; Spectrum for best telemetry | Spectrum-4 (SN5600) switches running Spectrum-X RoCE AR/CC |

The SuperNIC exists because, in Spectrum-X, the switch sprays packets of one flow over all paths (Module 4.1 §3.1). A conventional NIC reassembles RoCE messages in order; the SuperNIC's DDP engine places each arriving packet directly at its final memory address, so order no longer matters. It also terminates the switch-to-NIC telemetry that drives congestion control. These capabilities are enabled by firmware/DOCA configuration on the card plus matching switch configuration — see [`02-doca-sdk-overview.md`](02-doca-sdk-overview.md) §7 and [`../04-spectrum-ethernet/01-spectrum-x.md`](../04-spectrum-ethernet/01-spectrum-x.md).

In an AI data center you typically find *both*: SuperNICs on the compute (GPU) fabric and DPUs on the north-south/storage/management side of the same servers.

## 3. Modes of operation

BlueField can present itself to the host in several ways. Mode is a firmware setting changed with **mlxconfig** (from MFT or DOCA tools) and takes effect after a power cycle.

| Mode | Who owns the network | Host sees | Use |
|---|---|---|---|
| **DPU mode** (default; also "embedded CPU mode", ECPF) | The Arm side. Host traffic passes through the Arm's eswitch (OVS) before reaching the wire | PFs/VFs whose traffic is steered via representors on the Arm | Full offload/isolation, HBN, zero-trust |
| **NIC mode** | The host. The card behaves like a ConnectX-7; Arm cores are not in the data path (and may be inaccessible) | Standard ConnectX NIC | SuperNIC deployments, maximum simplicity, when no DPU services are needed |
| **Zero-trust / Restricted host mode** | The Arm side, and additionally the host is *blocked* from RShim access, from changing firmware/mlxconfig and from reaching the Arm OS | Standard NIC with no management privileges | Bare-metal cloud where the tenant owns the host but the provider owns the DPU |
| Separated host mode (legacy) | Both host and Arm have independent PFs to the wire | Two independent NICs | Older designs; check current support |

Switching to NIC mode (exact parameter names vary by firmware generation; confirm against the current "BlueField Modes of Operation" document):
```bash
# From the host (or the Arm) — find the device
mst start && mst status -v
# BlueField-3: set NIC mode
mlxconfig -d /dev/mst/mt41692_pciconf0 s INTERNAL_CPU_OFFLOAD_ENGINE=1
# Older guidance / BlueField-2 style settings:
#   INTERNAL_CPU_MODEL=1 INTERNAL_CPU_PAGE_SUPPLIER=1 INTERNAL_CPU_ESWITCH_MANAGER=1 \
#   INTERNAL_CPU_IB_VPORT0=1 INTERNAL_CPU_OFFLOAD_ENGINE=1
# Back to DPU mode
mlxconfig -d /dev/mst/mt41692_pciconf0 s INTERNAL_CPU_OFFLOAD_ENGINE=0
# Then power-cycle the host (a warm reboot is not sufficient on most platforms)
```
Restricted host mode is set from the *Arm* side (so the host cannot undo it):
```bash
mlxprivhost -d /dev/mst/mt41692_pciconf0 r --disable_rshim --disable_tracer --disable_counter_rd --disable_port_owner   # restrict
mlxprivhost -d /dev/mst/mt41692_pciconf0 p     # privileged (undo)
```
Exam framing: NIC mode = "make it a ConnectX"; DPU mode = "Arm in the path"; restricted host = "host loses management rights". SuperNICs ship/operate in NIC mode.

## 4. Software stack: BSP, BFB and DOCA

- **BlueField BSP** (Board Support Package, "BlueField Software") — the Arm-side OS image: Ubuntu (default) with kernel, drivers, firmware, OVS, MFT, DOCA runtime and tools. Versioned (e.g., BSP 4.x) and bundled with a DOCA version.
- **BFB** (BlueField Boot stream) — the single installable file (`*.bfb`) containing boot loader, kernel, root filesystem and (optionally) NIC firmware. Pushed to the card over RShim.
- **DOCA** — the SDK/runtime for developing on and operating BlueField; it also provides the host drivers (DOCA-OFED). Covered in Module 5.2.
- **NIC firmware** — separate from the BSP but usually bundled in the BFB (`bfb-install` will update it if `--fw` is included or the BFB carries firmware). Managed with `mlxfwmanager`.

Compatibility: a given DOCA release matches a BSP release and a minimum firmware. Mixing a new DOCA host driver with an old BFB causes subtle problems (missing mlx5 features, DPA image mismatch). Check the DOCA release notes compatibility table.

## 5. RShim and installing a BFB

**RShim** is a management channel between host and BlueField exposed by the `rshim` driver as a directory `/dev/rshim0/` containing:
- `boot` — write a BFB here to install it,
- `console` — the Arm's serial console,
- `misc` — control/status (`cat /dev/rshim0/misc` shows boot mode, device name; `echo "SW_RESET 1" > /dev/rshim0/misc` resets the Arm),
- a virtual network interface `tmfifo_net0` on the host paired with one on the Arm.

Install the rshim driver on the host (`apt install rshim` from the DOCA repo, or it comes with `doca-all`), then:
```bash
systemctl status rshim
ls /dev/rshim0/
cat /dev/rshim0/misc

# Install the BFB (BSP + optionally firmware and a bf.cfg customisation file)
bfb-install --bfb /path/DOCA_2.x_BSP_4.x_Ubuntu_22.04-<date>.prod.bfb --rshim rshim0
bfb-install --bfb <file.bfb> --config /path/bf.cfg --rshim rshim0      # with cloud-init/bf.cfg answers
# Progress appears on the console; the Arm reboots into the new image
```
Over the **BMC** you can also push a BFB via Redfish/`scp` without a host — useful for rack-scale provisioning and when the host is in restricted mode.

### 5.1 Connecting to the Arm
```bash
# Serial console
minicom -D /dev/rshim0/console            # or screen /dev/rshim0/console 115200
# Default login ubuntu / ubuntu (forced password change on first login in current BSPs)

# Network over the RShim virtual NIC
ip addr add 192.168.100.1/30 dev tmfifo_net0      # host side (DOCA packages often do this automatically)
ssh ubuntu@192.168.100.2                          # Arm side default address

# Out-of-band: the DPU's 1GbE OOB port (oob_net0 on the Arm) gets DHCP; SSH to that address from the management network
```
The 192.168.100.1 (host) / 192.168.100.2 (Arm) pair is a well-known exam fact.

### 5.2 bf.cfg and cloud-init
`bf.cfg` is a shell-style file passed with `--config` to `bfb-install` to customise first boot: set `ubuntu_PASSWORD` (hashed), enable/disable the BMC/OOB, choose `grub` options, or embed a cloud-init `user-data`. The BSP image uses **cloud-init**; `/var/lib/cloud/seed/nocloud-net/user-data` on the Arm can set users, SSH keys, packages and run commands. This is how fleets of DPUs are provisioned identically.

### 5.3 Firmware
```bash
mlxfwmanager --query                         # installed vs available firmware for all NVIDIA NICs/DPUs
mlxfwmanager -u -i fw-BlueField-3-rel-32_xx.bin   # update from a file
mlxfwmanager --online -u                     # fetch from NVIDIA online repo (where allowed)
# Alternative via MFT:
flint -d /dev/mst/mt41692_pciconf0 q
flint -d /dev/mst/mt41692_pciconf0 -i <fw.bin> b
mlxfwreset -d /dev/mst/mt41692_pciconf0 reset   # activate without full power cycle, when supported
```
Firmware updates on a BlueField normally run from the Arm (or via `bfb-install` with bundled firmware). Always match firmware to the DOCA/BSP compatibility matrix.

## 6. mlxconfig key parameters

**mlxconfig** reads and writes non-volatile NIC configuration (takes effect after reset/power cycle).
```bash
mlxconfig -d /dev/mst/mt41692_pciconf0 q                      # query all
mlxconfig -d /dev/mst/mt41692_pciconf0 q SRIOV_EN NUM_OF_VFS LINK_TYPE_P1 LINK_TYPE_P2
mlxconfig -d /dev/mst/mt41692_pciconf0 s SRIOV_EN=1 NUM_OF_VFS=16
mlxconfig -d /dev/mst/mt41692_pciconf0 s LINK_TYPE_P1=2        # 1 = InfiniBand, 2 = Ethernet
mlxconfig -d /dev/mst/mt41692_pciconf0 s INTERNAL_CPU_OFFLOAD_ENGINE=1   # NIC mode (BF-3)
mlxconfig -d /dev/mst/mt41692_pciconf0 reset                   # factory defaults
```
| Parameter | Meaning |
|---|---|
| `SRIOV_EN` | Enable SR-IOV virtual functions |
| `NUM_OF_VFS` | Number of VFs per PF to expose |
| `LINK_TYPE_P1` / `_P2` | Port protocol: 1 = IB, 2 = ETH (BlueField-3 is Ethernet or IB depending on SKU/firmware — check capability) |
| `INTERNAL_CPU_OFFLOAD_ENGINE` | 0 = DPU mode, 1 = NIC mode (BlueField-3) |
| `INTERNAL_CPU_MODEL` | Embedded CPU vs separated host (BlueField-2 era) |
| `NUM_OF_PF` | PFs per port |
| `ROCE_CC_PRIO_MASK_P1`, `CNP_DSCP_P1`, `CNP_802P_PRIO_P1` | RoCE congestion-control settings exposed in firmware — relevant to SuperNIC/Spectrum-X tuning (names verify) |
| `ADVANCED_PCI_SETTINGS`, `MAX_ACC_OUT_READ` | PCIe/RDMA performance tunables used in GPU hosts |

The device path `/dev/mst/mt41692_pciconf0` is BlueField-3 (41692 is its PCI device ID; BlueField-2 is 41686, ConnectX-7 is 4129). You may also address devices by PCI BDF: `mlxconfig -d 0000:03:00.0 q`.

## 7. Host ↔ DPU network interfaces and OVS offload

In DPU mode the Arm side runs Open vSwitch with hardware offload. Interface naming on the **Arm**:

| Arm interface | Represents |
|---|---|
| `p0`, `p1` | The physical uplink ports (wire side) |
| `pf0hpf`, `pf1hpf` | "Host PF" representors: the host's physical function on port 0/1 — traffic the host sends arrives here |
| `pf0vf0`…`pf0vfN` | Representors for each host VF (when SR-IOV is enabled) |
| `pf0sf0`… | Scalable function representors (DPU-side SFs for services) |
| `en3f0pf0sf0` style names | SF netdevs usable by Arm applications |
| `oob_net0` | The 1GbE out-of-band management port |
| `tmfifo_net0` | Virtual NIC over RShim to the host |

The default BSP creates two bridges, `ovsbr1` (p0 + pf0hpf) and `ovsbr2` (p1 + pf1hpf), so host traffic flows transparently. Everything the host sends on its PF/VFs appears on the matching representor; OVS rules decide forwarding; with offload, the first packet of a flow hits OVS in software and the resulting rule is pushed to the ConnectX eswitch (ASAP²), after which the flow runs in hardware without touching the Arm CPU.

```bash
# On the Arm
ovs-vsctl show
ovs-vsctl set Open_vSwitch . other_config:hw-offload=true
systemctl restart openvswitch-switch
ovs-vsctl add-br ovsbr1
ovs-vsctl add-port ovsbr1 p0
ovs-vsctl add-port ovsbr1 pf0hpf
ovs-vsctl add-port ovsbr1 pf0vf0
ovs-dpctl dump-flows type=offloaded                 # confirm flows are in hardware
ovs-appctl dpctl/dump-flows type=offloaded
devlink dev eswitch show pci/0000:03:00.0           # mode switchdev required for representors
```
If `devlink dev eswitch show` reports `legacy` instead of `switchdev`, representors do not exist and offload cannot work; the BSP sets switchdev by default in DPU mode.

On the **host**, the interfaces are plain ConnectX netdevs (`ens3f0np0`, `ens3f1np1`, and VFs); the host cannot see representors or OVS. The host's only view into the DPU is RShim (unless restricted).

## 8. Management paths

| Path | Reaches | When |
|---|---|---|
| **RShim** over PCIe (`/dev/rshim0`) | Console, BFB push, tmfifo network, reset | From the host; blocked in restricted mode |
| RShim over USB | Same functions from a laptop/rack controller via the card's USB-C | Field provisioning without host cooperation |
| **BMC** (own Arm Cortex-M/ASPEED, Redfish/IPMI, port 443/623) | Power, sensors, BFB install via Redfish, serial-over-LAN to Arm, firmware for BMC/CEC/golden images | Rack-scale provisioning; the preferred OOB path in DPF deployments |
| **OOB 1GbE port** (`oob_net0`) | SSH to the Arm OS | Normal day-2 management |
| In-band (through p0/p1 to the Arm) | Arm OS via an SF/VLAN | When no OOB cabling |

The BMC and OOB port often share the 1GbE RJ45 via NC-SI depending on card SKU; check the hardware guide.

## 9. Use cases in AI data centers

1. **East-west GPU fabric (SuperNIC)** — RoCE AR/DDP, congestion control, per-tenant isolation with Spectrum-X; 8 SuperNICs per node in rail-optimized design.
2. **Storage** — NVMe-oF initiator/target offload, GPUDirect Storage path, SNAP (emulating NVMe/virtio devices to the host) so the host sees "local" disks served over the network.
3. **In-band / tenant management and isolation** — the DPU is the provider-controlled boundary in bare-metal GPU clouds: firewall, VXLAN/EVPN termination (**HBN**), telemetry, encryption, all invisible to and un-tamperable by the tenant OS (restricted host mode).
4. **Zero-trust security** — DOCA App Shield for host memory introspection, inline IPsec/TLS, distributed firewall.
5. **Kubernetes** — the Network Operator and DOCA Platform Framework deploy and manage DPUs as cluster resources (Module 7.x).
6. **DPF (DPU Provisioning Framework / DOCA Platform Framework)** — a Kubernetes-native system that discovers BlueFields via their BMCs, installs BFBs, applies DPU "flavors" and deploys DOCA services (HBN, OVN-Kubernetes offload) declaratively across hundreds of nodes. For the exam: DPF is how you provision DPUs at scale instead of running `bfb-install` host by host.

## 10. Basic troubleshooting

```bash
# Host side
mst start; mst status -v                       # lists /dev/mst devices and PCI BDFs; is the card seen?
lspci | grep -i -E 'mellanox|nvidia'           # BlueField appears as a ConnectX NIC (and possibly a PCIe bridge)
ibdev2netdev [-v]                              # maps mlx5_0 → ens3f0np0, shows port state Up/Down
ibstat / ibv_devinfo                           # RDMA device state, link layer Ethernet/IB, firmware version
ethtool -i ens3f0np0                           # driver, firmware version
mlxconfig -d <dev> q | grep -E 'INTERNAL_CPU|SRIOV|NUM_OF_VFS|LINK_TYPE'
mlxfwmanager --query
cat /dev/rshim0/misc                           # BOOT_MODE, DEV_NAME; "DISPLAY_LEVEL 1" for more
dmesg | grep -i -E 'rshim|mlx5'

# Link
mlxlink -d /dev/mst/mt41692_pciconf0 -p 1      # port state, speed, FEC, troubleshooting hints (Module 8.x)
mlxlink -d <dev> -m                            # transceiver DOM
mlxlink -d <dev> -e                            # eye/BER

# Arm side
ovs-vsctl show; ovs-dpctl show
ip -br link                                    # p0/p1, pf0hpf, oob_net0 states
devlink dev eswitch show pci/0000:03:00.0
systemctl status openvswitch-switch mlx-regex doca-telemetry   # services
journalctl -u rshim -b                         # on host
```
Common symptoms: no `/dev/rshim0` → rshim driver not loaded or host in restricted mode; `tmfifo_net0` present but no SSH → Arm still booting or wrong 192.168.100.x; host sees NIC but no RoCE → check `LINK_TYPE`, firmware, and whether DOCA-OFED drivers are installed; OVS flows not offloaded → eswitch mode legacy or `hw-offload` not set.

## Configuration / command reference

```bash
# Discovery
mst start && mst status -v; ibdev2netdev -v; lspci | grep -i mellanox

# Modes
mlxconfig -d /dev/mst/mt41692_pciconf0 s INTERNAL_CPU_OFFLOAD_ENGINE=1     # NIC mode (BF-3; verify)
mlxconfig -d /dev/mst/mt41692_pciconf0 s INTERNAL_CPU_OFFLOAD_ENGINE=0     # DPU mode
mlxprivhost -d <dev> r --disable_rshim ...                                  # restricted host (from Arm)

# Image / console / network
bfb-install --bfb <file.bfb> [--config bf.cfg] --rshim rshim0
minicom -D /dev/rshim0/console
ip addr add 192.168.100.1/30 dev tmfifo_net0; ssh ubuntu@192.168.100.2
cat /dev/rshim0/misc; echo "SW_RESET 1" > /dev/rshim0/misc

# Firmware
mlxfwmanager --query; mlxfwmanager -u -i <fw.bin>; mlxfwreset -d <dev> reset

# mlxconfig
mlxconfig -d <dev> q; mlxconfig -d <dev> s SRIOV_EN=1 NUM_OF_VFS=16 LINK_TYPE_P1=2

# OVS offload (Arm)
ovs-vsctl set Open_vSwitch . other_config:hw-offload=true
ovs-vsctl add-br ovsbr1; ovs-vsctl add-port ovsbr1 p0; ovs-vsctl add-port ovsbr1 pf0hpf
ovs-appctl dpctl/dump-flows type=offloaded; devlink dev eswitch show pci/<bdf>
```

## Common pitfalls & exam traps
- DPU ≠ SuperNIC. Same silicon; SuperNIC = NIC-mode BlueField-3 optimised for Spectrum-X RoCE AR/CC with DDP; DPU = Arm in the data path running services.
- mlxconfig changes need a *power cycle* (cold reboot), not just a warm reboot, for mode changes.
- `/dev/rshim0/boot` receives the BFB; `/dev/rshim0/console` is the serial console; `/dev/rshim0/misc` is status/control.
- Host 192.168.100.1 ↔ Arm 192.168.100.2 over `tmfifo_net0`.
- Representors (`pf0hpf`, `pf0vf0`) exist only on the Arm in switchdev mode; the host never sees them.
- OVS offload needs `other_config:hw-offload=true` *and* switchdev eswitch mode; verify with `dump-flows type=offloaded`.
- Restricted host mode is set from the Arm with `mlxprivhost`; once set, the host cannot use RShim or change firmware.
- `LINK_TYPE_P1=1` is IB, `2` is Ethernet.
- `mt41692` = BlueField-3; `mt41686` = BlueField-2; `mt4129` = ConnectX-7.
- Firmware, BSP and DOCA versions are tied; update together from the compatibility matrix.
- DPF is the scalable provisioning path; `bfb-install` is per-host.

## Key takeaways
- BlueField-3 = Arm cores + ConnectX-7 NIC + DPA + on-card memory + crypto/compression + BMC/RShim management, on a PCIe Gen5 card.
- SuperNIC = BlueField-3 in NIC mode tuned for Spectrum-X; DPU = full Arm offload platform.
- Modes: DPU (Arm in path), NIC (behaves as ConnectX), restricted host (host loses management); set with `mlxconfig`, needs power cycle.
- BFB = installable BSP image; `bfb-install --bfb <f> --rshim rshim0` pushes it over RShim.
- Access the Arm via `minicom -D /dev/rshim0/console`, SSH to 192.168.100.2 over `tmfifo_net0`, or the OOB port.
- `mlxfwmanager --query/-u` for firmware; `mlxconfig` for SRIOV_EN, NUM_OF_VFS, LINK_TYPE, mode.
- Arm interfaces: p0/p1 (wire), pf0hpf (host PF representor), pf0vfN (VF representors); OVS bridges them with hardware offload.
- Management: RShim (PCIe/USB), BMC (Redfish), OOB 1GbE.
- AI DC roles: SuperNIC GPU fabric, storage offload, tenant isolation/HBN, zero-trust, DPF provisioning.
- Troubleshoot with `mst status`, `ibdev2netdev`, `mlxconfig -q`, `/dev/rshim0/misc`, `mlxlink`, `ovs-vsctl show`.

## Review questions

1. A customer wants BlueField-3 cards in GPU servers to behave exactly like ConnectX-7 NICs for a Spectrum-X RoCE fabric, with no Arm services. Which mode and command?
   A. DPU mode — `mlxconfig -d <dev> s INTERNAL_CPU_OFFLOAD_ENGINE=0`
   B. NIC mode — `mlxconfig -d <dev> s INTERNAL_CPU_OFFLOAD_ENGINE=1`, then power cycle
   C. Restricted host — `mlxprivhost -d <dev> r`
   D. Separated host — `mlxconfig -d <dev> s LINK_TYPE_P1=1`

2. Which command installs a new BlueField software image on the Arm from the host?
   A. `mlxfwmanager -u -i image.bfb`
   B. `bfb-install --bfb image.bfb --rshim rshim0`
   C. `flint -i image.bfb b`
   D. `scp image.bfb ubuntu@192.168.100.2:`

3. From the host, which address reaches the DPU's Arm OS over the RShim virtual network interface by default?
   A. 192.168.100.1
   B. 192.168.100.2
   C. 169.254.0.1
   D. 10.0.0.2

4. On the Arm, which interface carries traffic sent by the host's physical function on port 0?
   A. `p0`
   B. `pf0hpf`
   C. `oob_net0`
   D. `tmfifo_net0`

5. OVS on the DPU is forwarding but CPU load is high and `ovs-appctl dpctl/dump-flows type=offloaded` shows nothing. Most likely cause?
   A. `other_config:hw-offload=true` not set, or eswitch mode is legacy rather than switchdev
   B. Firmware too new
   C. The host is in NIC mode
   D. RShim driver missing

6. A bare-metal GPU cloud provider wants tenants to control the host OS but be unable to access the DPU, its firmware or RShim. Which setting?
   A. NIC mode
   B. `SRIOV_EN=0`
   C. Restricted host mode set via `mlxprivhost` from the Arm
   D. `LINK_TYPE_P1=2`

7. What distinguishes a BlueField-3 SuperNIC from a BlueField-3 DPU?
   A. The SuperNIC has no network ports
   B. The SuperNIC is optimised as a NIC for Spectrum-X RoCE adaptive routing with Direct Data Placement; the DPU runs infrastructure services on its Arm cores
   C. The DPU supports only InfiniBand
   D. They use different ASIC families

8. Which tool shows whether the host kernel sees the BlueField's RDMA device and maps it to a netdev?
   A. `ovs-vsctl show`
   B. `ibdev2netdev`
   C. `cat /dev/rshim0/misc`
   D. `bfb-install --query`

### Answer key
1. B — NIC mode makes the BlueField-3 behave as a ConnectX NIC; mode changes require a power cycle.
2. B — `bfb-install` streams the BFB through `/dev/rshim0/boot`.
3. B — Host is .1, Arm is .2 on `tmfifo_net0`.
4. B — `pf0hpf` is the host-PF representor for port 0.
5. A — Offload requires `hw-offload=true` and switchdev mode.
6. C — Restricted host mode removes host management privileges and is set from the Arm.
7. B — Same silicon, different personality and tuning.
8. B — `ibdev2netdev` maps mlx5 RDMA devices to netdevs with link state.

## References
- NVIDIA BlueField-3 DPU and SuperNIC documentation — https://docs.nvidia.com/networking/display/bluefield3
- NVIDIA BlueField DPU BSP Documentation (Modes of Operation, RShim, bfb-install, bf.cfg) — https://docs.nvidia.com/networking/display/bluefielddpuosv470 (version varies)
- DOCA Installation Guide for Linux — https://docs.nvidia.com/doca/sdk/
- MFT User Manual (mlxconfig, mlxfwmanager, flint, mlxprivhost, mlxlink) — https://docs.nvidia.com/networking/display/mftv4
- DOCA Platform Framework (DPF) — https://docs.nvidia.com/doca/sdk/ (DPF section)
- NVIDIA Academy course: BlueField DPU Administration
- Related: [`02-doca-sdk-overview.md`](02-doca-sdk-overview.md), [`../04-spectrum-ethernet/01-spectrum-x.md`](../04-spectrum-ethernet/01-spectrum-x.md), [`../07-kubernetes/`](../07-kubernetes/), [`../08-troubleshooting/`](../08-troubleshooting/)
