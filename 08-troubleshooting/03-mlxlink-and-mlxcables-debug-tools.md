# Module 8.3 — mlxlink, mlxcables and the MFT Debug Tools

> **Exam domain:** Troubleshooting Tools (20%)  
> **Source course(s):** MLXlink and MLX Cable Debug Tools (NVIDIA Academy)  
> **Source docs:** NVIDIA Firmware Tools (MFT) User Manual — mlxlink Utility, mlxcables Utility, mlxconfig, mlxfwmanager, mlxfwreset, mst; Cumulus Linux "Troubleshoot Layer 1" chapter; ConnectX-7 / OSFP transceiver and cable documentation  
> **Estimated study time:** 3.5 hours

## Learning objectives
- Install and start **MFT** (`mst start`, `mst status -v`) and map a `/dev/mst/...` device path to a NIC or switch port.
- Run **mlxlink** against a host NIC or a switch port and read its Operational Info, Supported Info, Troubleshooting Info and Tool Info sections.
- Use `mlxlink -m`, `-c`, `-e`, `--show_fec`, `--rx_fec_histogram` to diagnose a physical link and judge BER, FEC and optical-power values against normal thresholds.
- Read and update cable/transceiver EEPROM and firmware with **mlxcables**.
- Query and change NIC firmware parameters with **mlxconfig** (link type, SR-IOV, VF count, advanced PCIe) and apply them with `mlxfwreset`.
- Query and upgrade firmware with mlxfwmanager / mlxup.
- Compare `mlxlink`, `ethtool -m` and `iblinkinfo`, and run a structured flapping-link and degraded-width workflow.

## 1. MFT and mst: getting a device handle

**MFT** (Mellanox/NVIDIA Firmware Tools) is the low-level toolbox that talks to ConnectX/BlueField NICs and Spectrum/Quantum switch ASICs directly through their register interface, independent of the OS network stack. It is installed on hosts as the `mft` package (bundled with MLNX_OFED/DOCA-Host) and is already present on Cumulus Linux, MLNX-OS and NVOS switches.

Most MFT tools need a *device* argument. On a host, `mst` provides it:

```bash
$ sudo mst start
Starting MST (Mellanox Software Tools) driver set
Loading MST PCI module - Success
Loading MST PCI configuration module - Success
Create devices
$ sudo mst status -v
MST modules:
------------
    MST PCI module is not loaded
    MST PCI configuration module loaded
PCI devices:
------------
DEVICE_TYPE             MST                           PCI       RDMA            NET                       NUMA
ConnectX7(rev:0)        /dev/mst/mt4129_pciconf0      17:00.0   mlx5_0          net-ibp23s0               0
ConnectX7(rev:0)        /dev/mst/mt4129_pciconf1      3b:00.0   mlx5_1          net-ibp59s0               0
```

`mst status -v` is the Rosetta stone: it maps MST device ↔ PCI address ↔ RDMA device (`mlx5_0`) ↔ netdev ↔ NUMA node. `mt4129` is the ConnectX-7 PCI device ID (`mt4125` = ConnectX-6 Dx, `mt41692` = BlueField-3; you do not need to memorize these, just read them off). Most MFT tools also accept the PCI address directly (`-d 17:00.0`) or the RDMA name on recent versions, so `mst start` is sometimes optional — but the exam expects the `/dev/mst/` path.

On switches, the device is the switch ASIC and you add a port number:

| Platform | Typical invocation |
|---|---|
| Cumulus Linux (Spectrum) | `sudo mlxlink -d /dev/mst/mt53104_pci_cr0 -p 1` — or, since Cumulus 5.x, `sudo mlxlink -d <pci-bdf> -p <swp#>`; run `sudo mst status` on the switch to find the device. Port numbering uses the ASIC port label, which for split ports differs from `swpN` — check `nv show interface swp1 link` for the mapping |
| MLNX-OS / NVOS (Quantum, Spectrum) | From the CLI: `show interfaces ib 1/1 transceiver` / `show interfaces ib 1/1 counters`, which call the same registers; `mlxlink` directly via `_shell`/debug mode only with support guidance |
| UFM | Exposes the same cable info and BER per port in the GUI/REST API |

## 2. mlxlink output anatomy

`mlxlink -d <dev> [-p <port>]` with no other flags prints four sections. Illustrative output from a healthy ConnectX-7 NDR port:

```text
Operational Info
----------------
State                           : Active
Physical state                  : LinkUp
Speed                           : IB-NDR
Width                           : 4x
FEC                             : Standard_RS-FEC - (544,514)
Loopback Mode                   : No Loopback
Auto Negotiation                : ON

Supported Info
--------------
Enabled Link Speed (Ext.)       : 0x00000e00 (NDR,HDR,EDR)
Supported Cable Speed (Ext.)    : 0x00000e00 (NDR,HDR,EDR)

Troubleshooting Info
--------------------
Status Opcode                   : 0
Group Opcode                    : N/A
Recommendation                  : No issue was observed

Tool Information
----------------
Firmware Version                : 28.41.1000
amBER Version                   : 2.19
MFT Version                     : mft 4.28.0-92
```

| Section | What to look at |
|---|---|
| **Operational Info** | `State` and `Physical state` (same semantics as `ibstat`); `Speed` (IB-NDR, IB-HDR, 400G, 200G…); `Width` (4x/2x/1x — the degraded-cable tell); `FEC` (RS-FEC 544,514 is expected at HDR/NDR and 100G+/lane Ethernet; `No FEC` on a 400G link is wrong); `Loopback Mode`; `Auto Negotiation` |
| **Supported Info** | What the port *can* do and what the *cable* says it can do. If `Supported Cable Speed` lacks NDR, the cable is the limit, not the port |
| **Troubleshooting Info** | `Status Opcode` + `Recommendation`. 0 = fine. Non-zero codes come with text such as `Negotiation failure`, `Remote faults detected`, `Cable is unplugged`, `Signal not detected`, `Link partner does not support FEC`, `Speed degradation`, `Bad signal integrity`. This section is MFT's own diagnosis and is the first thing to read on a bad link |
| **Tool Information** | Firmware and MFT versions — relevant because link-training fixes ship in NIC firmware |

The exam will give you a snippet and ask what to do. `Width: 2x` → cable. `Physical state: Polling` + `Cable is unplugged` → cable/transceiver not detected. `Status: Negotiation failure` → speed/FEC mismatch between the two ends, often one side forced.

## 3. Module (cable/transceiver) information: `-m`

`mlxlink -d <dev> -m` (long form `--show_module`) reads the transceiver/cable EEPROM via the port:

```text
Module Info
-----------
Identifier                      : OSFP
Compliance                      : 400GBASE-DR4, 800G-2xDR4 (Twin-port)
Cable Technology                : 1310 nm DFB, Single-mode
Cable Type                      : Optical Module (separated)
OUI                             : NVIDIA
Vendor Name                     : NVIDIA
Vendor Part Number              : MMA4Z00-NS
Vendor Serial Number            : MT2342FT01234
Rev                             : A1
Wavelength [nm]                 : 1310
Transfer Distance [m]           : 500
Attenuation (5g,7g,12g,25g) [dB]: N/A
FW Version                      : 46.130.1104
Digital Diagnostic Monitoring   : Yes
Power Class                     : 8 (>14W)
CDR RX                          : ON,ON,ON,ON
CDR TX                          : ON,ON,ON,ON
LOS Alarm                       : Rx: ____ Tx: ____
Temperature [C]                 : 54 [-5..80]
Voltage [mV]                    : 3284 [2970..3630]
Bias Current [mA]               : 61.2,60.8,61.5,61.1
Rx Power Current [dBm]          : 0.8,0.6,0.9,-14.2 [-11.1..4.0]
Tx Power Current [dBm]          : 1.9,1.8,2.0,1.9 [-7.0..4.0]
```

How to read it:

- **Vendor PN/SN** — confirm the cable matches the design (NDR-rated, correct length). Also what you report to RMA.
- **Temperature** — bracketed range is the alarm window. Transceivers running near 70–80 °C in an OSFP cage usually mean the cage heatsink/airflow problem, and heat-induced BER follows. Verify thresholds for your part.
- **Voltage** — out of range means a power-delivery problem on the port, rare.
- **Rx Power per lane** — the money field. All four lanes should sit within ~1–2 dB of each other and inside the bracketed range. The sample above shows lane 3 at **−14.2 dBm** against the others at ~+0.7 dBm: that lane's fiber is dirty, broken or disconnected. Result: the link trains at 2x or fails. For DAC (copper) cables the power fields show N/A and you rely on BER instead.
- **Tx Power per lane** — low Tx on *this* side's module means this module is failing; the far end will see low Rx.
- **LOS Alarm** — loss-of-signal flags per lane.
- **FW Version** — active cables and optical transceivers have firmware; `mlxcables` updates it (Section 6).

## 4. Counters, BER and FEC: `-c`, `-e`, `--show_fec`, `--rx_fec_histogram`

### 4.1 `-c` / `--show_counters`

```text
Physical Counters and BER Info
------------------------------
Time Since Last Clear [Min]              : 1420.3
Effective Physical Errors                : 0
Effective Physical BER                   : 15E-255
Raw Physical Errors Per Lane             : 1201,1189,1340,98811
Raw Physical BER Per Lane                : 2E-10,2E-10,2E-10,1E-8
Link Down Counter                        : 0
Link Error Recovery Counter              : 0
Symbol Errors                            : 0
Successful Recovery Events               : 0
```

The three BER concepts:

| Term | What it is | Normal (NDR / 100G-per-lane PAM4) |
|---|---|---|
| **Raw BER** (pre-FEC) | Bit errors on the wire before forward error correction | Non-zero by design on PAM4; anything better than roughly 1e-6 to 1e-5 is within the FEC's correction budget. Per-lane values should be similar to each other |
| **Effective BER** (post-FEC) | Errors FEC could *not* correct | Should be essentially zero (shown as 15E-255 meaning "none observed") — effective errors are what the upper layers see |
| **Symbol errors** | Uncorrectable symbols reported to the link layer (what `perfquery SymbolErrorCounter` counts) | Zero, or not incrementing |

Interpretation: in the sample, lane 3 has a raw BER 100× worse than the other lanes (1e-8 vs 2e-10). It is *still being corrected* (effective = 0), so the link works — but it is the lane most likely to fall over when the transceiver warms up. One lane off from its siblings is a connector/fiber lane problem; all four lanes elevated is a cable-length/quality or a port (host-side SerDes) problem. Rising **effective** errors or symbol errors = packets are being corrupted now → replace.

Counters accumulate since last clear; use `mlxlink -d <dev> -c --clear_counters` (verify the exact flag name in your MFT version; `-pc`/`--clear` existed in older releases) and re-read after a fixed interval to get a rate.

### 4.2 `-e` / `--show_eye`

Eye-opening information reports per-lane receiver margin (height/phase, and on newer SerDes a "grade"). It is a *relative* indicator: compare lanes on the same port, or the same port before/after a cable change. A lane with a much smaller eye than its neighbors matches the raw-BER outlier pattern above. On PCIe (`--port_type PCIE`) the same flag drives margin scans for the host-to-NIC link, which is how you prove a Gen5 x16 slot is really running Gen5 x16 at good margin.

### 4.3 `--show_fec` and `--rx_fec_histogram`

`--show_fec` lists which FEC modes the port supports per speed and which is active. `--rx_fec_histogram --show_histogram` prints how many FEC codewords had 0, 1, 2, … n corrected symbol errors:

```text
FEC Histogram
-------------
Bin 0  (0 errors)   : 9812003344120
Bin 1  (1 error)    : 4120033
Bin 2               : 102
...
Bin 14              : 0
Bin 15 (uncorrect.) : 0
```

A healthy link keeps counts in the low bins with an exponential tail. Counts creeping into the high bins (close to the RS-FEC 544,514 correction limit of 15 symbols) mean the FEC is about to fail; non-zero in the top bin = uncorrectable = effective errors. This histogram is the best early-warning for a cable that *will* fail. `--clear_histogram` resets it.

## 5. Changing link parameters: `-a`, `--speeds`, `--fec`, `-l`

| Flag | Effect | When |
|---|---|---|
| `-a UP` / `-a DN` / `-a TG` (`--port_state`) | Admin up / down / toggle the port at the PHY | Bounce a flapping link; isolate a bad link quickly. Do **not** `DN` the port that is your path to an unmanaged switch; on Socket-Direct NICs disable both MST devices of the port |
| `-s <speed>` / `--speeds NDR,HDR` | Restrict advertised speeds; with `--link_mode_force` disable auto-negotiation | Pin a link at HDR to stabilize while waiting for a cable replacement; test "is it the NDR rate that fails?" |
| `-k <mode>` / `--fec RS|NF|AU…` with `--fec_speed` | Override FEC for a speed | Only when the far end cannot negotiate; a mismatch gives `Negotiation failure`. Default AU (auto) is almost always right |
| `-l PH|RM|EX|NO` (`--loopback`) | PHY loopback (internal, Tx→Rx), remote, external, none | Isolate the host port from the cable: internal loopback passes → the port is fine, blame cable/far end |
| `--port_type PCIE` | Operate on the NIC's PCIe link instead of the network port | Verify PCIe gen/width/margin for a GPUDirect-bound NIC |

Changes made with `mlxlink` are *runtime* and not persistent across reboot/reset; persistent link settings belong in `mlxconfig` (host) or NVUE (switch, e.g. `nv set interface swp1 link speed 400G`, `nv set interface swp1 link fec rs`).

## 6. mlxcables: the cable's own EEPROM and firmware

**mlxcables** addresses the cable/transceiver as a device of its own, which matters for two things `mlxlink -m` does not do: dumping raw EEPROM pages and **updating the firmware on active cables and transceivers**.

```bash
$ sudo mlxcables                              # list detected cables as mst cable devices
Cable #1:
  Device:    mt4129_pciconf0_cable_0
  Identifier: OSFP
  Vendor:    NVIDIA   PN: MMA4Z00-NS   SN: MT2342FT01234   FW: 46.130.1104
$ sudo mlxcables -d mt4129_pciconf0_cable_0        # summary (same fields as mlxlink -m)
$ sudo mlxcables -d mt4129_pciconf0_cable_0 --dump # raw EEPROM pages (CMIS for OSFP/QSFP-DD)
$ sudo mlxcables -d mt4129_pciconf0_cable_0 --read -p 0 -o 128 -l 16   # page/offset/length read
$ sudo mlxcables -d mt4129_pciconf0_cable_0 --DDM  # digital diagnostics (temp, volt, power)
$ sudo mlxcables -d mt4129_pciconf0_cable_0 -q     # query firmware
$ sudo mlxcables -d mt4129_pciconf0_cable_0 -i <fw-image.bin> -b   # burn/update transceiver firmware
```

You may need `mst cable add` (on some MFT versions `mst start --with_cables` or `mst cable add --with_ib`) before cable devices appear in `mst status`; verify in current MFT docs. Transceiver firmware updates are relevant in NDR/800G fleets where NVIDIA has shipped link-stability fixes in transceiver firmware; the switch side can also push them (`mlxcables` on Cumulus; UFM/NVOS have bulk-update workflows). Apply updates in a maintenance window — the link drops during the burn.

## 7. mlxconfig: NIC firmware parameters

**mlxconfig** reads and sets *non-volatile* firmware configuration on a NIC. Changes take effect after `mlxfwreset` or a reboot.

```bash
$ sudo mlxconfig -d /dev/mst/mt4129_pciconf0 q
Device #1:
----------
Device type:    ConnectX7
Name:           MCX75310AAS-NEA_Ax
Configurations:                              Next Boot
         LINK_TYPE_P1                        IB(1)
         NUM_OF_VFS                          0
         SRIOV_EN                            False(0)
         ADVANCED_PCI_SETTINGS               False(0)
         MAX_ACC_OUT_READ                    0
         ATS_ENABLED                         False(0)
         ...
```

Exam-relevant parameters:

| Parameter | Values | Why |
|---|---|---|
| `LINK_TYPE_P1` (`_P2` for port 2) | `1`=IB, `2`=ETH, `3`=VPI/auto on VPI-capable cards | Switch a ConnectX between InfiniBand and Ethernet/RoCE. `ibstat` `Link layer` reflects it after reset |
| `SRIOV_EN` | True/False | Enable SR-IOV — required for VF-based Kubernetes networking (`../07-kubernetes/`) |
| `NUM_OF_VFS` | 0–N | Number of virtual functions exposed |
| `ADVANCED_PCI_SETTINGS` | True/False | Unlocks `MAX_ACC_OUT_READ` and other PCIe tuning used for GPUDirect/RoCE at 400G on Gen5 |
| `ATS_ENABLED`, `PCI_WR_ORDERING` | — | PCIe/ATS tuning for specific platforms; follow the DGX/BlueField tuning guides rather than guessing |
| `INTERNAL_CPU_MODEL` / `INTERNAL_CPU_OFFLOAD_ENGINE` | BlueField only | DPU vs NIC mode (`../05-bluefield-doca/`) |
| `ROCE_CC_PRIO_MASK_P1`, `CNP_DSCP_P1`, `CNP_802P_PRIO_P1` | — | RoCE congestion-control defaults; usually left to the OS-level `mlnx_qos`/DCB config |

```bash
$ sudo mlxconfig -d /dev/mst/mt4129_pciconf0 s LINK_TYPE_P1=2 SRIOV_EN=1 NUM_OF_VFS=8
$ sudo mlxfwreset -d /dev/mst/mt4129_pciconf0 reset     # applies without reboot (link drops)
$ sudo mlxconfig -d /dev/mst/mt4129_pciconf0 r          # reset params to firmware defaults
```

`mlxconfig -d <dev> i` shows parameter descriptions. Changing `LINK_TYPE` on one port of a dual-port card is legitimate (port 1 IB, port 2 Ethernet) and is how a single node can sit on both an IB compute fabric and an Ethernet storage fabric.

## 8. Firmware: mlxfwmanager, mlxup, mlxfwreset

| Tool | Role |
|---|---|
| `mlxfwmanager` | Query (`--query`) all NICs' firmware and PSID; `-u` update from a bundle, `-i <bin>` specific image; `--online` pulls from NVIDIA |
| `mlxup` | Standalone self-contained updater (same engine, no MFT install needed); `mlxup --online -u` |
| `flint` | Low-level burn/query (`flint -d <dev> q`), for when the managed updater refuses |
| `mlxfwreset` | `-d <dev> query` shows supported reset levels; `reset` applies new firmware or `mlxconfig` changes without a reboot (where the platform supports it) |

```bash
$ sudo mlxfwmanager --query
Device #1:
  Device Type:      ConnectX7
  Part Number:      MCX75310AAS-NEA_Ax
  PSID:             MT_0000000834
  Versions:         Current        Available
     FW             28.39.2048     28.41.1000
```

Firmware matters for link debugging more than many engineers expect: NDR SerDes tuning and transceiver interop fixes arrive as NIC firmware, so "upgrade to the recommended firmware for your switch OS release" is a legitimate first step for a fabric-wide BER problem, and a mandatory one before RMA.

## 9. mlxlink vs ethtool -m vs iblinkinfo

| | `mlxlink` | `ethtool -m <netdev>` / `ethtool <netdev>` | `iblinkinfo` |
|---|---|---|---|
| Layer | PHY/SerDes and module registers via MFT | Linux netdev view (SFF/CMIS EEPROM via driver; link speed/FEC via ethtool) | IB link layer via MADs |
| Scope | One port at a time, any NVIDIA port (host or switch) | Ethernet netdevs on the local host only (no IB ports) | Every link in the IB subnet, from one host |
| Depth | Per-lane BER, FEC histogram, eye, troubleshooting opcodes, loopback, admin control | Module vendor/PN/power/temp; speed/duplex/FEC; link counters via `ethtool -S` | Width/speed/state and "Could be 4X" |
| Needs | MFT, root; no SM needed | Nothing beyond the driver | Active port + SM |
| Use it for | *Why* a specific link is bad | Quick RoCE host checks, scripting | *Which* link in the fabric is bad |

Workflow: `iblinkinfo` (or NetQ / `nv show interface` on Ethernet) finds the suspect link → `mlxlink -m -c` on that port explains it → fix → `mlxlink -c` after clearing counters confirms.

## 10. OSFP twin-port and breakout considerations

NDR Quantum-2 switches (QM9700) use **OSFP twin-port** cages: one OSFP transceiver carries *two* 400G ports (800G per cage, 2 × 4 lanes). ConnectX-7 NDR NICs use single-port OSFP (or QSFP112). Implications for debugging:

- A switch "port" like `1/1` and `1/2` share one physical transceiver; a hot transceiver or low Tx power shows up on *both* logical ports. `mlxlink -m` on either returns the same module data.
- Breakout cables (1×800G twin-port switch end → 2×400G NIC ends, or 400G → 2×200G) mean the two NIC ends are on different lanes of one module; a bad lane pair affects one NIC only, and `mlxlink -m` on the NIC shows only its 4 lanes while the switch side shows 8.
- Split-port configuration must match on the switch (`nv set interface swp1 link breakout 2x` on Spectrum; port split profiles on Quantum); a 400G NIC on an unsplit 800G twin port will show `Negotiation failure` or `Width 2x`-style symptoms that are configuration, not cable.
- Power class 8 OSFP modules (>14 W) need the finned-top ("riding heatsink") variant in Quantum-2 cages and flat-top in NIC cages — mixing them is a real field problem that presents as temperature alarms and heat-driven BER.

## 11. mlxdump and mlxreg (brief)

`mlxdump -d <dev> pcie_uc --all` and `mlxdump ... fsdump` collect firmware/PCIe state for NVIDIA support when a NIC misbehaves below the link layer. `mlxreg -d <dev> --reg_name PPCNT --get --indexes "local_port=1,..."` reads individual firmware registers (PPCNT = port counters, PDDR = port diagnostics, which is what `mlxlink` itself reads). You rarely type these by hand; know that `mlxlink` is a friendly front-end on `mlxreg` register access and that support bundles (`sysinfo-snapshot.py`, `cl-support`) call `mlxdump` for you.

## 12. Worked examples

### 12.1 Link stuck at 200G instead of 400G

1. Symptom: `ib_write_bw` tops out at ~196 Gb/s on `mlx5_3`; `iblinkinfo -l | grep mlx5_3` shows `2X 106.25 Gbps ... ( Could be 4X )`.
2. `sudo mst start; sudo mst status -v` → `mlx5_3` is `/dev/mst/mt4129_pciconf3`.
3. `sudo mlxlink -d /dev/mst/mt4129_pciconf3` → `Width: 2x`, `Speed: IB-NDR`, Troubleshooting: `Speed degradation` (illustrative wording).
4. `sudo mlxlink -d /dev/mst/mt4129_pciconf3 -m` → Rx Power lanes `0.7, 0.9, -40.0, -40.0` → two lanes receive nothing.
5. `-c` → raw errors concentrated on lanes 2–3 before the retrain.
6. Reseat both ends and clean the MPO ferrule. Re-check `-m`: all four lanes ~0.8 dBm. `mlxlink -a TG` to retrain (or `ibportstate reset`). Width now 4x.
7. If reseating does not help, swap the transceiver with a known-good one on the same fiber; if lanes recover → bad transceiver; if not → bad fiber/MPO.
8. Confirm: `ib_write_bw` ≈ 390 Gb/s; clear counters and verify effective BER stays zero after an hour.

### 12.2 Flapping link workflow

1. Evidence: `perfquery` LinkErrorRecoveryCounter/LinkDownedCounter incrementing; UFM/NetQ link-flap events.
2. `mlxlink -c`: raw BER on one or all lanes near 1e-5 or worse; FEC histogram populated in high bins; effective errors > 0.
3. `mlxlink -m`: temperature near the alarm limit, or Rx power at the low edge of range.
4. Check firmware (`mlxfwmanager --query`) on both ends against the compatibility matrix.
5. Isolate: `mlxlink -l PH` internal loopback on the NIC → if clean, the port is fine. Move the cable to another port → if the problem follows the cable, replace it.
6. Stabilize meanwhile: `mlxlink --speeds HDR` to pin a lower rate, or `-a DN` so the SM/BGP route around it, rather than letting it flap.

## Configuration / command reference

```bash
# MFT basics
sudo mst start; sudo mst status -v
# mlxlink — host NIC
sudo mlxlink -d /dev/mst/mt4129_pciconf0                   # Operational / Supported / Troubleshooting / Tool info
sudo mlxlink -d /dev/mst/mt4129_pciconf0 -m                # module: vendor, PN, SN, temp, volt, Rx/Tx power per lane
sudo mlxlink -d /dev/mst/mt4129_pciconf0 -c                # raw/effective BER, symbol errors, link down/recovery
sudo mlxlink -d /dev/mst/mt4129_pciconf0 -e                # eye opening per lane
sudo mlxlink -d /dev/mst/mt4129_pciconf0 --show_fec
sudo mlxlink -d /dev/mst/mt4129_pciconf0 --rx_fec_histogram --show_histogram
sudo mlxlink -d /dev/mst/mt4129_pciconf0 -a DN|UP|TG       # admin state
sudo mlxlink -d /dev/mst/mt4129_pciconf0 --speeds HDR      # restrict speed (+ --link_mode_force to disable AN)
sudo mlxlink -d /dev/mst/mt4129_pciconf0 --fec RS --fec_speed 400G
sudo mlxlink -d /dev/mst/mt4129_pciconf0 -l PH             # internal PHY loopback
sudo mlxlink -d /dev/mst/mt4129_pciconf0 --port_type PCIE -e   # PCIe link margin
sudo mlxlink -d ... --json                                 # machine-readable
# mlxlink — switch port (Cumulus); find device with 'sudo mst status'
sudo mlxlink -d /dev/mst/mt53104_pci_cr0 -p 1 -m -c
# mlxcables
sudo mlxcables ; sudo mlxcables -d <cable_dev> --dump ; sudo mlxcables -d <cable_dev> --DDM
sudo mlxcables -d <cable_dev> -q ; sudo mlxcables -d <cable_dev> -i <fw.bin> -b
# mlxconfig
sudo mlxconfig -d /dev/mst/mt4129_pciconf0 q
sudo mlxconfig -d /dev/mst/mt4129_pciconf0 s LINK_TYPE_P1=2 SRIOV_EN=1 NUM_OF_VFS=8 ADVANCED_PCI_SETTINGS=1
sudo mlxfwreset -d /dev/mst/mt4129_pciconf0 reset
# firmware
sudo mlxfwmanager --query ; sudo mlxfwmanager -u -i fw.bin ; sudo mlxup --online -u
# Ethernet host equivalents
ethtool ibp23s0 ; ethtool -m ibp23s0 ; ethtool -S ibp23s0 | grep -i -E 'phy|fec|symbol|discard'
```

## Common pitfalls & exam traps
- `mst status -v` is how you map `/dev/mst/...` ↔ `mlx5_N` ↔ netdev. Running `mlxlink` on the wrong `pciconf` is the classic multi-rail mistake.
- Raw (pre-FEC) BER is **expected** to be non-zero on PAM4 links; it is the *effective* BER and symbol errors that must be zero. Do not RMA a cable for raw BER of 1e-9.
- One lane with much worse BER / much lower Rx power than its siblings = connector/fiber lane. All lanes bad = cable quality/length, firmware or the port itself.
- `mlxlink -a DN` is runtime and reversible; `ibportstate disable` is MAD-based and remote; `mlxconfig` is persistent firmware config. Know which is which.
- `mlxconfig` changes need `mlxfwreset` or a reboot. `LINK_TYPE_P1=1` is IB, `=2` is Ethernet — the exam asks this.
- `mlxlink -p` is for *switch* ports; on an adapter the device already identifies the port.
- `ethtool -m` works only for Ethernet netdevs; for IB ports use `mlxlink -m`.
- OSFP twin-port: two switch ports share one module; module temperature/power affects both.
- `No FEC` on a 400G/NDR link, or FEC mismatch between ends, produces `Negotiation failure` — fix configuration before touching cables.
- `mlxlink` is a diagnostic tool, not a monitor; do not poll it in a tight loop in production (UFM/NetQ telemetry does that properly).

## Key takeaways
- MFT: `mst start`, `mst status -v` → `/dev/mst/mt4129_pciconf0` for ConnectX-7.
- `mlxlink -d <dev>` prints Operational, Supported, Troubleshooting and Tool info; read the Troubleshooting recommendation first.
- `-m` module info: vendor/PN/SN, temperature, voltage, per-lane Rx/Tx power — one weak lane = dirty/broken fiber lane.
- `-c` counters: raw BER (non-zero OK), effective BER (must be ~0), symbol errors (must not grow); `--rx_fec_histogram` is the early warning.
- `-e` eye, `--show_fec`, `-a` admin state, `--speeds`, `--fec`, `-l` loopback, `--port_type PCIE`.
- `mlxcables` reads/dumps cable EEPROM and updates transceiver firmware.
- `mlxconfig q / s LINK_TYPE_P1=1|2, SRIOV_EN, NUM_OF_VFS, ADVANCED_PCI_SETTINGS` + `mlxfwreset`.
- `mlxfwmanager --query / -u`, `mlxup`, `flint` for firmware.
- `iblinkinfo` finds *which* link; `mlxlink` explains *why*; `ethtool -m` is the Ethernet-only quick look.
- Width 2x on a planned 4x link → reseat/clean → swap transceiver → swap fiber; verify with `-m`, `-c`, perftest.

## Review questions

1. Which command maps `/dev/mst/mt4129_pciconf1` to its RDMA device name and netdev?  
   A. `ibstat`  
   B. `mst status -v`  
   C. `mlxconfig q`  
   D. `ethtool -i`

2. `mlxlink -c` shows Raw Physical BER per lane of `3E-10, 2E-10, 3E-10, 2E-10` and Effective Physical BER `15E-255`. The correct conclusion is:  
   A. The cable must be replaced immediately  
   B. The link is healthy; raw errors are within FEC's correction capability and nothing uncorrected is reaching the link layer  
   C. FEC is disabled  
   D. The port is in loopback

3. `mlxlink -m` reports Rx Power `0.8, 0.7, -40.0, 0.9` dBm. The most likely cause is:  
   A. SM misconfiguration  
   B. A broken or dirty fiber on one lane (or a failed transmitter lane at the far end)  
   C. Wrong PKey  
   D. Transceiver firmware out of date

4. You need to change a ConnectX-7 port from InfiniBand to Ethernet. Which sequence is correct?  
   A. `mlxlink --speeds 400G` then reboot  
   B. `mlxconfig -d <dev> s LINK_TYPE_P1=2` then `mlxfwreset -d <dev> reset` (or reboot)  
   C. `ethtool -s <dev> link-type eth`  
   D. `mlxcables -d <dev> --update`

5. Which tool updates firmware on an active optical transceiver?  
   A. `mlxfwmanager`  
   B. `mlxconfig`  
   C. `mlxcables`  
   D. `ibportstate`

6. On a Quantum-2 switch, ports 1/1 and 1/2 both report module temperature 78 °C. Why is this expected to affect both ports?  
   A. They are on the same ASIC  
   B. OSFP twin-port cages share one physical transceiver between two logical ports  
   C. They have the same PKey  
   D. iblinkinfo aggregates adjacent ports

7. An engineer wants to prove a host NIC port itself is healthy before blaming a cable. Which `mlxlink` option helps most directly?  
   A. `-l PH` (internal PHY loopback) and check BER  
   B. `--show_fec`  
   C. `-a DN`  
   D. `--json`

8. Which statement correctly contrasts the tools?  
   A. `iblinkinfo` shows per-lane BER; `mlxlink` shows fabric-wide link width  
   B. `ethtool -m` works on IB ports; `mlxlink` only on Ethernet  
   C. `iblinkinfo` finds which link in the subnet is degraded; `mlxlink` explains why on a specific port; `ethtool -m` gives module data for Ethernet netdevs only  
   D. All three require a running SM

### Answer key
1. B — `mst status -v` prints MST device, PCI, RDMA name, netdev and NUMA in one table.
2. B — Non-zero raw BER with zero effective BER is normal PAM4 behavior; the FEC is doing its job.
3. B — One lane with no received power while the others are normal is a per-lane fiber/connector or far-end Tx fault.
4. B — Link type is a non-volatile mlxconfig parameter and needs a firmware reset or reboot.
5. C — mlxcables addresses the cable/transceiver as a device and can burn its firmware.
6. B — Twin-port OSFP: one module, two 400G ports.
7. A — Internal loopback removes the cable and far end from the path; clean BER means the port is good.
8. C — Scope and depth differ: fabric-wide (iblinkinfo), deep single-port (mlxlink), Ethernet-netdev module read (ethtool -m).

## References
- NVIDIA Firmware Tools (MFT) User Manual — mlxlink Utility: https://networking-docs.nvidia.com/mftswum/4350/mlxlink-utility
- MFT User Manual — mlxcables, mlxconfig, mlxfwmanager, mlxfwreset, mst sections: https://docs.nvidia.com/networking/display/MFT
- Cumulus Linux User Guide — "Troubleshoot Layer 1" (mlxlink on Spectrum switches): https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/
- NVIDIA LinkX cables and transceivers documentation (OSFP, twin-port, power classes): https://docs.nvidia.com/networking/display/linkx
- NVIDIA Academy: "MLXlink and MLX Cable Debug Tools"
- Related modules: `01-infiniband-diagnostics-and-perftest.md`, `02-cable-validation-tool-cvt.md`, `04-wjh-netq-and-cumulus-troubleshooting.md`, `../03-rdma/03-roce-a-to-z.md`, `../07-kubernetes/`
