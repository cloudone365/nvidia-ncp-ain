# Module 8.3 — mlxlink, mlxcables and the MFT Debug Tools

> **Exam domain:** Troubleshooting Tools (20%)  
> **Source course(s):** MLXlink and MLX Cable Debug Tools (NVIDIA Academy)  
> **Source docs:** NVIDIA Firmware Tools (MFT) User Manual — mlxlink Utility, mlxcables Utility, mlxconfig, mlxfwmanager, mlxfwreset, mst; Cumulus Linux "Troubleshoot Layer 1" chapter; ConnectX-7 / OSFP transceiver and cable documentation  
> **Estimated study time:** 3.5 hours

## 1. Learning objectives

By the end of this module you will be able to:

- **Configure** the **MFT** environment (`mst start`, `mst status -v`) and map a `/dev/mst/...` device path to a NIC or switch port. *(Troubleshooting Tools — MFT basics)*
- **Describe** the four sections of **mlxlink** output (Operational, Supported, Troubleshooting, Tool) and read the Troubleshooting recommendation first. *(Troubleshooting Tools — mlxlink)*
- **Troubleshoot** a physical link with `mlxlink -m`, `-c`, `-e`, `--show_fec`, `--rx_fec_histogram`, judging BER, FEC and optical-power values against normal thresholds. *(Troubleshooting Tools — layer-1 diagnosis)*
- **Verify** and update cable/transceiver EEPROM and firmware with **mlxcables**. *(Troubleshooting Tools — cable tools)*
- **Configure** NIC firmware parameters with **mlxconfig** (link type, SR-IOV, VF count, advanced PCIe) and apply them with `mlxfwreset`. *(InfiniBand / Spectrum Networking — adapter configuration)*
- **Verify** and upgrade firmware with mlxfwmanager / mlxup. *(Troubleshooting Tools — firmware)*
- **Compare** `mlxlink`, `ethtool -m` and `iblinkinfo`, and **write** a structured flapping-link and degraded-width runbook. *(Troubleshooting Tools — tool selection)*

## 2. Key technical concepts

### 2.1 MFT and mst: the handle to the silicon

**Analogy.** The OS network stack is the car's dashboard. MFT is the OBD-II port under the steering column — it talks to the engine controller directly, whether or not the dashboard works. `mst` is the adapter cable that gives each tool a plug to connect to.

**Mechanism.** **MFT** (Mellanox/NVIDIA Firmware Tools) talks to ConnectX/BlueField NICs and Spectrum/Quantum switch ASICs through their register interface, independent of the OS network stack. It is installed on hosts as the `mft` package (bundled with MLNX_OFED/DOCA-Host) and already present on Cumulus Linux, MLNX-OS and NVOS switches. Most MFT tools need a *device* argument, and `mst` provides it:

```bash
$ sudo mst start
Starting MST (Mellanox Software Tools) driver set
Loading MST PCI module - Success
Loading MST PCI configuration module - Success
Create devices
$ sudo mst status -v
PCI devices:
------------
DEVICE_TYPE             MST                           PCI       RDMA            NET                       NUMA
ConnectX7(rev:0)        /dev/mst/mt4129_pciconf0      17:00.0   mlx5_0          net-ibp23s0               0
ConnectX7(rev:0)        /dev/mst/mt4129_pciconf1      3b:00.0   mlx5_1          net-ibp59s0               0
```

`mst status -v` is the Rosetta stone: MST device ↔ PCI address ↔ RDMA device (`mlx5_0`) ↔ netdev ↔ NUMA node. `mt4129` is the ConnectX-7 PCI device ID (`mt4125` = ConnectX-6 Dx, `mt41692` = BlueField-3; read them off, do not memorize). Most MFT tools also accept the PCI address directly (`-d 17:00.0`) or the RDMA name on recent versions, so `mst start` is sometimes optional — but the exam expects the `/dev/mst/` path.

On switches the device is the ASIC and you add a port number:

| Platform | Typical invocation |
|---|---|
| Cumulus Linux (Spectrum) | `sudo mlxlink -d /dev/mst/mt53104_pci_cr0 -p 1` — or, since Cumulus 5.x, `sudo mlxlink -d <pci-bdf> -p <swp#>`; run `sudo mst status` on the switch to find the device. Port numbering uses the ASIC port label, which for split ports differs from `swpN` — check `nv show interface swp1 link` for the mapping |
| MLNX-OS / NVOS (Quantum, Spectrum) | `show interfaces ib 1/1 transceiver` / `show interfaces ib 1/1 counters` call the same registers; `mlxlink` directly via `_shell`/debug mode only with support guidance |
| UFM | Exposes the same cable info and BER per port in the GUI/REST API |

**Why the exam cares.** "Which command maps `/dev/mst/mt4129_pciconf1` to `mlx5_1`?" → `mst status -v`. Running mlxlink on the wrong `pciconf` is the classic multi-rail mistake.

### 2.2 mlxlink output anatomy

**Analogy.** A doctor's chart has four blocks: vitals now (Operational), what the patient is capable of (Supported), the doctor's note (Troubleshooting), and the lab's equipment versions (Tool). Read the doctor's note first.

**Mechanism.** `mlxlink -d <dev> [-p <port>]` with no other flags prints four sections (illustrative, healthy ConnectX-7 NDR port):

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
| **Operational Info** | `State`/`Physical state` (same semantics as `ibstat`); `Speed` (IB-NDR, IB-HDR, 400G…); `Width` (4x/2x/1x — the degraded-cable tell); `FEC` (RS-FEC 544,514 is expected at HDR/NDR and 100G+/lane Ethernet; `No FEC` on a 400G link is wrong); `Loopback Mode`; `Auto Negotiation` |
| **Supported Info** | What the port *can* do and what the *cable* says it can do. If `Supported Cable Speed` lacks NDR, the cable is the limit, not the port |
| **Troubleshooting Info** | `Status Opcode` + `Recommendation`. 0 = fine. Non-zero codes carry text such as `Negotiation failure`, `Remote faults detected`, `Cable is unplugged`, `Signal not detected`, `Link partner does not support FEC`, `Speed degradation`, `Bad signal integrity`. MFT's own diagnosis — read first |
| **Tool Information** | Firmware and MFT versions — link-training fixes ship in NIC firmware |

**Why the exam cares.** It shows a snippet and asks what to do. `Width: 2x` → cable. `Polling` + `Cable is unplugged` → cable/transceiver not detected. `Negotiation failure` → speed/FEC mismatch, often one side forced.

### 2.3 Module information (`-m`): is the light getting through?

**Analogy.** Four garden hoses in a bundle. Rx power per lane is the water pressure at each nozzle. Three strong, one trickle = one hose is kinked or has a leak.

**Mechanism.** `mlxlink -d <dev> -m` (`--show_module`) reads the transceiver EEPROM via the port:

```text
Module Info
-----------
Identifier                      : OSFP
Compliance                      : 400GBASE-DR4, 800G-2xDR4 (Twin-port)
Cable Technology                : 1310 nm DFB, Single-mode
Vendor Name                     : NVIDIA
Vendor Part Number              : MMA4Z00-NS
Vendor Serial Number            : MT2342FT01234
Transfer Distance [m]           : 500
FW Version                      : 46.130.1104
Power Class                     : 8 (>14W)
LOS Alarm                       : Rx: ____ Tx: ____
Temperature [C]                 : 54 [-5..80]
Voltage [mV]                    : 3284 [2970..3630]
Bias Current [mA]               : 61.2,60.8,61.5,61.1
Rx Power Current [dBm]          : 0.8,0.6,0.9,-14.2 [-11.1..4.0]
Tx Power Current [dBm]          : 1.9,1.8,2.0,1.9 [-7.0..4.0]
```

- **Vendor PN/SN** — confirm the cable matches the design (NDR-rated, correct length); what you report for RMA.
- **Temperature** — bracketed range is the alarm window. Running near 70–80 °C in an OSFP cage usually means a cage heatsink/airflow problem; heat-induced BER follows. Verify thresholds for your part.
- **Voltage** — out of range = port power-delivery problem, rare.
- **Rx Power per lane** — the money field. All four lanes within ~1–2 dB of each other and inside range. Above, lane 3 at **−14.2 dBm** against ~+0.7 dBm: dirty, broken or disconnected fiber on that lane. Result: 2x training or failure. DAC (copper) cables show N/A here; rely on BER instead.
- **Tx Power per lane** — low Tx on *this* module means this module is failing; the far end sees low Rx.
- **LOS Alarm** — loss-of-signal flags per lane. **FW Version** — active cables and optics have firmware; `mlxcables` updates it.

### 2.4 Counters, BER and FEC (`-c`, `-e`, `--show_fec`, `--rx_fec_histogram`)

**Analogy.** Raw BER is typos per million characters typed. FEC is autocorrect. Effective BER is the typos autocorrect *missed* — the only ones the reader ever sees. A fast typist with autocorrect on produces plenty of raw typos and a clean document. You do not fire the typist for raw typos; you worry when autocorrect starts missing.

**Mechanism.** `mlxlink -d <dev> -c` (`--show_counters`):

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
```

| Term | What it is | Normal (NDR / 100G-per-lane PAM4) |
|---|---|---|
| **Raw BER** (pre-FEC) | Bit errors on the wire before forward error correction | Non-zero by design on PAM4; anything better than roughly 1e-6 to 1e-5 is within the FEC's correction budget. Lanes should be similar to each other |
| **Effective BER** (post-FEC) | Errors FEC could *not* correct | Essentially zero (`15E-255` = "none observed") — this is what upper layers see |
| **Symbol errors** | Uncorrectable symbols reported to the link layer (what `perfquery SymbolErrorCounter` counts) | Zero, or not incrementing |

In the sample, lane 3's raw BER is 100× worse than its siblings (1e-8 vs 2e-10). It is *still being corrected* (effective = 0), so the link works — but it is the lane that will fall over when the transceiver warms up. One lane off = connector/fiber lane problem; all four lanes elevated = cable length/quality or a port (host-side SerDes) problem. Rising **effective** errors or symbol errors = packets are being corrupted now → replace.

Counters accumulate since last clear; use `mlxlink -d <dev> -c --clear_counters` (verify the exact flag name in your MFT version; `-pc`/`--clear` existed in older releases) and re-read after a fixed interval to get a rate.

`-e` (`--show_eye`) reports per-lane receiver margin (height/phase, or a grade on newer SerDes). It is *relative*: compare lanes on the same port, or before/after a cable change. With `--port_type PCIE` it margins the host-to-NIC PCIe link — how you prove a Gen5 x16 slot really runs Gen5 x16.

`--show_fec` lists supported/active FEC per speed. `--rx_fec_histogram --show_histogram` prints how many FEC codewords had 0, 1, 2, … n corrected symbols:

```text
FEC Histogram
-------------
Bin 0  (0 errors)   : 9812003344120
Bin 1  (1 error)    : 4120033
Bin 2               : 102
...
Bin 15 (uncorrect.) : 0
```

Healthy links keep counts in the low bins with an exponential tail. Counts creeping toward the RS-FEC 544,514 limit of 15 correctable symbols mean the FEC is about to fail; non-zero in the top bin = uncorrectable = effective errors. The histogram is the best early warning for a cable that *will* fail. `--clear_histogram` resets it.

**Why the exam cares.** Non-zero raw BER with zero effective BER = **healthy**. Candidates who RMA a cable for raw 1e-9 fail that question.

### 2.5 Changing link parameters (`-a`, `--speeds`, `--fec`, `-l`)

| Flag | Effect | When |
|---|---|---|
| `-a UP` / `-a DN` / `-a TG` (`--port_state`) | Admin up / down / toggle the port at the PHY | Bounce a flapping link; isolate a bad link. Do **not** `DN` your own path to an unmanaged switch; on Socket-Direct NICs disable both MST devices of the port |
| `-s <speed>` / `--speeds NDR,HDR` | Restrict advertised speeds; with `--link_mode_force` disable auto-negotiation | Pin a link at HDR to stabilize while waiting for a replacement cable |
| `-k <mode>` / `--fec RS|NF|AU…` with `--fec_speed` | Override FEC for a speed | Only when the far end cannot negotiate; mismatch gives `Negotiation failure`. Default AU (auto) is almost always right |
| `-l PH|RM|EX|NO` (`--loopback`) | PHY internal (Tx→Rx), remote, external, none | Isolate the host port from the cable: internal loopback clean → the port is fine, blame cable/far end |
| `--port_type PCIE` | Operate on the NIC's PCIe link | Verify PCIe gen/width/margin for a GPUDirect-bound NIC |

`mlxlink` changes are *runtime* and not persistent across reboot/reset; persistent link settings belong in `mlxconfig` (host) or NVUE (switch: `nv set interface swp1 link speed 400G`, `nv set interface swp1 link fec rs`).

### 2.6 mlxcables: the cable as a device

**Mechanism.** **mlxcables** addresses the cable/transceiver itself, which `mlxlink -m` does not: raw EEPROM pages and **transceiver firmware updates**.

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

You may need `mst cable add` (on some MFT versions `mst start --with_cables` or `mst cable add --with_ib`) before cable devices appear; verify in current MFT docs. Transceiver firmware updates matter in NDR/800G fleets where NVIDIA has shipped link-stability fixes in transceiver firmware. The link drops during the burn — maintenance window.

### 2.7 mlxconfig and firmware tools

**Analogy.** `mlxlink -a` is adjusting the thermostat; `mlxconfig` is rewiring the furnace — it is permanent, and you must restart the system for it to take.

**Mechanism.** **mlxconfig** reads and sets *non-volatile* firmware configuration; changes take effect after `mlxfwreset` or a reboot.

```bash
$ sudo mlxconfig -d /dev/mst/mt4129_pciconf0 q
Configurations:                              Next Boot
         LINK_TYPE_P1                        IB(1)
         NUM_OF_VFS                          0
         SRIOV_EN                            False(0)
         ADVANCED_PCI_SETTINGS               False(0)
$ sudo mlxconfig -d /dev/mst/mt4129_pciconf0 s LINK_TYPE_P1=2 SRIOV_EN=1 NUM_OF_VFS=8
$ sudo mlxfwreset -d /dev/mst/mt4129_pciconf0 reset     # applies without reboot (link drops)
$ sudo mlxconfig -d /dev/mst/mt4129_pciconf0 r          # reset params to firmware defaults
```

| Parameter | Values | Why |
|---|---|---|
| `LINK_TYPE_P1` (`_P2` for port 2) | `1`=IB, `2`=ETH, `3`=VPI/auto on VPI-capable cards | Switch between InfiniBand and Ethernet/RoCE; `ibstat` `Link layer` reflects it after reset |
| `SRIOV_EN` / `NUM_OF_VFS` | True/False, 0–N | VF-based Kubernetes networking (`../07-kubernetes/`) |
| `ADVANCED_PCI_SETTINGS` | True/False | Unlocks `MAX_ACC_OUT_READ` and other PCIe tuning for GPUDirect/RoCE at 400G on Gen5 |
| `ATS_ENABLED`, `PCI_WR_ORDERING` | — | Platform-specific PCIe/ATS tuning; follow DGX/BlueField guides |
| `INTERNAL_CPU_MODEL` / `INTERNAL_CPU_OFFLOAD_ENGINE` | BlueField only | DPU vs NIC mode (`../05-bluefield-doca/`) |
| `ROCE_CC_PRIO_MASK_P1`, `CNP_DSCP_P1`, `CNP_802P_PRIO_P1` | — | RoCE CC defaults; usually left to OS-level `mlnx_qos` |

`mlxconfig -d <dev> i` shows parameter descriptions. Port 1 IB and port 2 Ethernet on one dual-port card is legitimate.

Firmware: `mlxfwmanager --query` (firmware + PSID per NIC), `-u`/`-i <bin>` to update, `--online`; `mlxup` is the standalone updater (`mlxup --online -u`); `flint -d <dev> q` is low-level; `mlxfwreset -d <dev> query|reset`. NDR SerDes tuning and transceiver interop fixes arrive as NIC firmware, so "upgrade to the recommended firmware" is a legitimate first step for a fabric-wide BER problem and mandatory before RMA.

### 2.8 mlxlink vs ethtool -m vs iblinkinfo, and OSFP twin-port

| | `mlxlink` | `ethtool -m <netdev>` / `ethtool <netdev>` | `iblinkinfo` |
|---|---|---|---|
| Layer | PHY/SerDes and module registers via MFT | Linux netdev view (SFF/CMIS EEPROM; speed/FEC) | IB link layer via MADs |
| Scope | One port at a time, any NVIDIA port (host or switch) | Ethernet netdevs on the local host only (no IB ports) | Every link in the IB subnet, from one host |
| Depth | Per-lane BER, FEC histogram, eye, opcodes, loopback, admin control | Module vendor/PN/power/temp; speed/duplex/FEC; counters via `ethtool -S` | Width/speed/state and "Could be 4X" |
| Needs | MFT, root; no SM | Nothing beyond the driver | Active port + SM |
| Use it for | *Why* a specific link is bad | Quick RoCE host checks, scripting | *Which* link in the fabric is bad |

NDR Quantum-2 switches (QM9700) use **OSFP twin-port** cages: one OSFP transceiver carries *two* 400G ports (800G per cage, 2 × 4 lanes). ConnectX-7 NDR NICs use single-port OSFP or QSFP112. Consequences: switch ports `1/1` and `1/2` share one module, so a hot transceiver or low Tx shows on *both*; breakout cables (1×800G twin-port switch end → 2×400G NIC ends) put each NIC on 4 of the module's 8 lanes, so the NIC sees 4 lanes while the switch sees 8; split configuration must match on the switch (`nv set interface swp1 link breakout 2x` on Spectrum; port split profiles on Quantum) or you get `Negotiation failure` / `Width 2x` symptoms that are configuration, not cable; power class 8 (>14 W) modules need the finned-top variant in Quantum-2 cages and flat-top in NIC cages — mixing them presents as temperature alarms and heat-driven BER.

`mlxdump -d <dev> pcie_uc --all` / `fsdump` and `mlxreg -d <dev> --reg_name PPCNT --get --indexes "local_port=1,..."` are what support bundles (`sysinfo-snapshot.py`, `cl-support`) call; `mlxlink` is a friendly front-end on `mlxreg` (PDDR, PPCNT registers).

## 3. Live-style walkthrough: a flapping NDR link

Scenario: UFM raised "Link downed counter exceeded" three times tonight for `dgx-04 mlx5_3`; `perfquery` shows LinkErrorRecoveryCounter climbing. The job on dgx-04 keeps getting `NCCL WARN ... error 12`. You have 20 minutes before the next training epoch. Output is illustrative.

```bash
# ---- Step 1: get a device handle. Without this, every MFT tool fails with "No such device".
dgx-04$ sudo mst start
Create devices
dgx-04$ sudo mst status -v | grep -E 'MST|mlx5_3'
DEVICE_TYPE             MST                           PCI       RDMA     NET            NUMA
ConnectX7(rev:0)        /dev/mst/mt4129_pciconf3      5e:00.0   mlx5_3   net-ibp94s0    0
# mlx5_3 is /dev/mst/mt4129_pciconf3 on this box. On a DGX the suffix usually tracks mlx5_N but NEVER
# assume it: a different node may enumerate differently. Decision: all commands below use pciconf3.

# ---- Step 2: the overview and MFT's own diagnosis. Read Troubleshooting Info FIRST.
dgx-04$ sudo mlxlink -d /dev/mst/mt4129_pciconf3
Operational Info
State                           : Active
Physical state                  : LinkUp
Speed                           : IB-NDR
Width                           : 4x                         # full width right now (it flaps, so this is a snapshot)
FEC                             : Standard_RS-FEC - (544,514) # correct for NDR
Auto Negotiation                : ON
Supported Info
Enabled Link Speed (Ext.)       : 0x00000e00 (NDR,HDR,EDR)
Supported Cable Speed (Ext.)    : 0x00000e00 (NDR,HDR,EDR)   # cable claims NDR: not a cable-spec limit
Troubleshooting Info
Status Opcode                   : 0
Recommendation                  : No issue was observed      # at this instant. Flaps are intermittent;
                                                             # we need history, i.e. counters and module data.
# Decision: configuration (speed/FEC/AN) is consistent; it is not a Negotiation failure.
# A flapping link with correct config is almost always physical: module, fiber, connector or heat.

# ---- Step 3: the module. Is anything marginal that would explain intermittent drops?
dgx-04$ sudo mlxlink -d /dev/mst/mt4129_pciconf3 -m
Identifier                      : OSFP
Vendor Part Number              : MMA4Z00-NS
Vendor Serial Number            : MT2342FT05517                 # write this down: it goes in the RMA
Power Class                     : 8 (>14W)
Temperature [C]                 : 77 [-5..80]                   # 3 degrees from the alarm ceiling!
Voltage [mV]                    : 3281 [2970..3630]
Rx Power Current [dBm]          : 0.7,0.8,-9.8,0.6 [-11.1..4.0] # lane 2 is 10 dB down on its siblings,
                                                                # still inside range... barely
Tx Power Current [dBm]          : 1.9,1.8,1.9,1.8 [-7.0..4.0]   # our transmitter is fine
# Decision: two independent red flags. (a) Module at 77 C — heat raises BER, and the link is cooler at
# 03:00 than under full load, which matches "flaps during training". (b) Lane 2 Rx is weak: dirty ferrule
# or damaged fiber on that lane, or the FAR END's lane-2 transmitter is dying (check its Tx later).

# ---- Step 4: counters and BER. Does the weak lane actually produce errors, and is FEC still coping?
dgx-04$ sudo mlxlink -d /dev/mst/mt4129_pciconf3 -c
Time Since Last Clear [Min]              : 612.4
Effective Physical Errors                : 3814                  # NON-ZERO: FEC is missing errors. Real corruption.
Effective Physical BER                   : 4E-13
Raw Physical Errors Per Lane             : 1304,1288,88120311,1402
Raw Physical BER Per Lane                : 2E-10,2E-10,1E-5,2E-10 # lane 2: 1e-5, at the edge of the FEC budget
Link Down Counter                        : 3                     # matches UFM's three events
Link Error Recovery Counter              : 41
Symbol Errors                            : 3814                  # what perfquery SymbolErrorCounter sees
# Decision: this is NOT "raw BER looks scary but effective is zero" (that would be healthy).
# Effective errors and symbol errors are non-zero and lane 2's raw BER is 50,000x its siblings.
# Classic single-lane fault. Confirm with the histogram: are we living in the high bins?
dgx-04$ sudo mlxlink -d /dev/mst/mt4129_pciconf3 --rx_fec_histogram --show_histogram
Bin 0  (0 errors)   : 7712003344120
Bin 1  (1 error)    : 920331204
Bin 2               : 51230112
...
Bin 13              : 4102
Bin 14              : 388
Bin 15 (uncorrect.) : 3814                                       # = Effective Physical Errors. FEC saturated.
# A healthy port has nothing above bin 3 or so. This one is spilling into bin 15.

# ---- Step 5: eye margin, to see if it is one lane or the whole port (host SerDes) before we touch hardware.
dgx-04$ sudo mlxlink -d /dev/mst/mt4129_pciconf3 -e
Eye Opening Info
Physical Grade                  : 72,71,19,73       # (illustrative units) lane 2 has a quarter of the margin
Height Eye Opening [mV]         : 118,121,34,120
Phase  Eye Opening [psec]       : 11,11,4,11
# Decision: three lanes excellent, one terrible. If all four were poor we would suspect the port, the
# PCIe slot's power, or firmware. One bad lane = fiber lane / connector / far-end Tx lane. Hardware swap it is.

# ---- Step 6: isolate the host port from the cable — is the NIC itself clean? 30 seconds, no RMA risk.
dgx-04$ sudo mlxlink -d /dev/mst/mt4129_pciconf3 -l PH          # internal PHY loopback: Tx -> Rx inside the NIC
dgx-04$ sudo mlxlink -d /dev/mst/mt4129_pciconf3 -c --clear_counters   # (verify flag name in your MFT)
dgx-04$ sleep 60; sudo mlxlink -d /dev/mst/mt4129_pciconf3 -c | grep -E 'Effective|Raw Physical BER'
Effective Physical Errors                : 0
Raw Physical BER Per Lane                : 1E-12,1E-12,1E-12,1E-12   # all four lanes clean in loopback
dgx-04$ sudo mlxlink -d /dev/mst/mt4129_pciconf3 -l NO          # back to normal
# Decision: the NIC port is good. The problem is in the module, the fiber, or the far end.

# ---- Step 7: replace. Order of cheapness: clean+reseat -> swap transceiver -> swap fiber.
#      Tonight we swap the OSFP at the NIC end for a known-good spare (same PN, finned/flat top correct for
#      the cage), clean the MPO ferrule, reseat. Then verify the far end's Tx while we are at it:
leaf-r3-01$ sudo mlxlink -d /dev/mst/mt53104_pci_cr0 -p 4 -m | grep -E 'Tx Power|Temperature'
Temperature [C]                 : 58 [-5..80]
Tx Power Current [dBm]          : 1.8,1.9,1.9,1.8                # far-end transmitter fine on all lanes: it WAS our module/fiber

# ---- Step 8: verify. Retrain, clear, wait, re-read. The acceptance test is the same -m/-c we started with.
dgx-04$ sudo mlxlink -d /dev/mst/mt4129_pciconf3 -a TG          # toggle the port to force a clean retrain
dgx-04$ sleep 20; sudo mlxlink -d /dev/mst/mt4129_pciconf3 | grep -E 'Width|Recommendation'
Width                           : 4x
Recommendation                  : No issue was observed
dgx-04$ sudo mlxlink -d /dev/mst/mt4129_pciconf3 -m | grep -E 'Serial|Temperature|Rx Power'
Vendor Serial Number            : MT2342FT09901                  # new module
Temperature [C]                 : 56 [-5..80]                    # 21 degrees cooler: the old module was cooking
Rx Power Current [dBm]          : 0.8,0.8,0.9,0.7 [-11.1..4.0]   # four matched lanes
dgx-04$ sudo mlxlink -d /dev/mst/mt4129_pciconf3 -c --clear_counters; sleep 600
dgx-04$ sudo mlxlink -d /dev/mst/mt4129_pciconf3 -c | grep -E 'Effective|Raw Physical BER|Link Down'
Effective Physical Errors                : 0
Raw Physical BER Per Lane                : 2E-10,2E-10,2E-10,2E-10
Link Down Counter                        : 0
# And the layer that complained in the first place:
dgx-05$ ib_write_bw -d mlx5_3 -i 1 -F --report_gbits -a            # server
dgx-04$ ib_write_bw -d mlx5_3 -i 1 -F --report_gbits -a -q 4 10.0.3.5
 1048576    5000           391.88             391.80               0.047
# Decision: closed. RMA the old module (SN MT2342FT05517, PN MMA4Z00-NS, 77 C, lane 2 Rx -9.8 dBm,
# raw 1e-5, effective errors 3814, histogram bin 15 populated). That sentence IS the RMA ticket.
```

What "good" looks like: Troubleshooting opcode 0; four Rx-power lanes within ~1–2 dB and well inside range; temperature mid-range; raw BER similar across lanes and below ~1e-6; effective errors 0; histogram empty above the low bins; LinkDown/LinkErrorRecovery flat over an hour.

## 4. Troubleshooting / common gotchas

### 4.1 `mlxlink` says "No such device" or shows the wrong port

- **Symptom:** `-E- Failed to open device /dev/mst/mt4129_pciconf3` — or the output is perfectly healthy for a link you know is broken.
- **Why it happens:** `mst start` was never run (no `/dev/mst/*` nodes), or you assumed `pciconf3` = `mlx5_3` and it does not on this node.
- **How to debug:** `ls /dev/mst/`; `sudo mst status -v` and read the RDMA column; cross-check with `ibdev2netdev`.
- **Fix:** `sudo mst start`, then take the device path from `mst status -v` every time; in scripts, derive it from the RDMA name rather than hard-coding.

### 4.2 Reading raw BER as a failure

- **Symptom:** an engineer opens an RMA because `Raw Physical BER Per Lane` shows `3E-10, 2E-10, 3E-10, 2E-10`.
- **Why it happens:** PAM4 at 100G/lane is *designed* to run with raw errors that RS-FEC corrects; raw BER is never zero on a working NDR link.
- **How to debug:** look at `Effective Physical Errors` / `Effective Physical BER` and `Symbol Errors` instead; check `--rx_fec_histogram` for population above the low bins; compare lanes to each other.
- **Fix:** replace only when effective errors are non-zero or climbing, symbol errors increment, or one lane is orders of magnitude worse than its siblings. Document raw BER as context, not cause.

### 4.3 Measuring counters since boot instead of over a window

- **Symptom:** every port in the fleet "has errors"; the list is useless.
- **Why it happens:** `-c` accumulates since the last clear (`Time Since Last Clear` of days); boot-time training noise and old incidents pollute the numbers.
- **How to debug:** read `Time Since Last Clear [Min]`; if large, clear and re-read after a fixed interval.
- **Fix:** `mlxlink -d <dev> -c --clear_counters` (verify flag name), wait 60–600 s, read again; compare the *rate* against your threshold.

### 4.4 Forcing speed or FEC on one end only

- **Symptom:** after "stabilizing" a link with `--speeds HDR --link_mode_force`, the port shows `Negotiation failure` or comes up at 1x.
- **Why it happens:** auto-negotiation is off on one side and on (or forced differently) on the other; FEC override mismatched (`--fec RS` vs `NF`).
- **How to debug:** `mlxlink` on both ends; compare `Auto Negotiation`, `Speed`, `FEC`; on a Spectrum switch also `nv show interface swpX link`.
- **Fix:** return both sides to AU/auto (`--fec AU`, re-enable AN) or force identically on both ends; make persistent settings in `mlxconfig`/NVUE, not in `mlxlink`.

## 5. Hands-on assignment: link triage runbook and outlier scanner

**Scenario.** Your 8-rail DGX cluster has no UFM telemetry yet. You need a script every on-call engineer can run on a node to pull module and BER data for every rail port and flag outliers, plus a one-page runbook for what to do with a flagged port.

**Requirements.**
1. `linkscan.sh`, run as root, auto-discovers every ConnectX port on the host via `mst status -v` (no hard-coded device names).
2. For each port, pull `mlxlink -m` and `mlxlink -c` and extract: module temperature, per-lane Rx power, raw BER per lane, effective physical errors, link down counter.
3. Flag: temperature within 10 °C of the module's own alarm ceiling (parse the bracket); any lane's Rx power more than 3 dB below the port's best lane; any lane's raw BER more than 100× the port's best lane or worse than 1e-6; effective errors > 0; link down > 0.
4. Print one row per port with values, then a FLAGGED list with the reason, and exit non-zero if anything is flagged.
5. `--json` mode that emits a machine-readable record per port.
6. `triage-runbook.md`: for each flag type, the next command to run and the replace/clean/escalate decision.

**Deliverables.** `linkscan.sh`, `triage-runbook.md`, sample output from a healthy node and from a node with one bad lane (fabricate `mlxlink` text to test the parser if you lack hardware).

**Acceptance criteria.**
- AC1: no device path or port name appears in the script source; everything comes from `mst status -v`.
- AC2: the Section 2.3 and 2.4 sample outputs, fed to the parser, flag lane 3 Rx power and lane 3 raw BER; the Section 2.2 healthy sample produces no flags.
- AC3: exit code 0 on a clean node, 1 when any flag fires.
- AC4: the runbook sends raw-BER-only flags to "monitor", effective-error flags to "replace", temperature flags to "airflow/heatsink before cable".

### Hint
- Section 2.1: `mst status -v` columns give you MST device and RDMA name on one line; `awk` them.
- The `-m` fields you need are `Temperature [C]`, `Rx Power Current [dBm]`; the `-c` fields are `Raw Physical BER Per Lane`, `Effective Physical Errors`, `Link Down Counter`. Comma-separated lanes split cleanly.
- BER values look like `2E-10`; `awk` handles scientific notation natively (`$1+0`).
- The alarm ceiling is the second number inside `[...]` on the temperature line.
- Section 2.4's table is your runbook: raw-only = monitor; effective/symbol = replace; one lane = connector/fiber; all lanes = port/firmware.

## Appendix A — Quick command / config reference

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

| Symptom | mlxlink evidence | Action |
|---|---|---|
| Width 2x on planned 4x | `-m` two lanes with no Rx power; `-c` raw errors on those lanes | Clean/reseat → swap transceiver → swap fiber |
| Flapping | `-c` LinkDown/LinkErrorRecovery rising; effective errors > 0; histogram high bins | `-m` temp/power; `-l PH` to clear the port; replace cable/module |
| `Negotiation failure` | AN/speed/FEC differ between ends | Fix config both ends (AU), not cables |
| `Cable is unplugged` / `Polling` | No module detected | Reseat; check cage; `mlxcables` lists nothing |
| All lanes high raw BER | Similar BER on all 4 lanes | Firmware (`mlxfwmanager`), cable length/type, port |
| Hot module | `-m` temp near ceiling, both twin-port ports | Airflow / heatsink variant (finned vs flat) |

## Appendix B — Exam traps & key takeaways

- `mst status -v` maps `/dev/mst/...` ↔ `mlx5_N` ↔ netdev ↔ NUMA. Running `mlxlink` on the wrong `pciconf` is the classic multi-rail mistake; `mt4129` = ConnectX-7.
- `mlxlink -d <dev>` prints Operational, Supported, Troubleshooting and Tool info; read the Troubleshooting recommendation first.
- Raw (pre-FEC) BER is **expected** non-zero on PAM4; *effective* BER and symbol errors must be zero. Do not RMA a cable for raw BER of 1e-9.
- One lane with much worse BER / much lower Rx power than its siblings = connector/fiber lane (or far-end Tx lane). All lanes bad = cable quality/length, firmware or the port itself.
- `--rx_fec_histogram` is the early warning: counts near bin 15 of RS-FEC (544,514) mean FEC is about to fail.
- `-l PH` internal loopback proves the port is good without a cable; `-a DN/UP/TG` is runtime and reversible; `ibportstate disable` is MAD-based and remote; `mlxconfig` is persistent firmware config.
- `mlxconfig` changes need `mlxfwreset` or a reboot. `LINK_TYPE_P1=1` is IB, `=2` is Ethernet.
- `mlxlink -p` is for *switch* ports; on an adapter the device already identifies the port.
- `ethtool -m` works only for Ethernet netdevs; for IB ports use `mlxlink -m`. `mlxcables` reads/dumps cable EEPROM and updates transceiver firmware.
- OSFP twin-port: two Quantum-2 switch ports share one module; module temperature/power affects both. Finned-top in switch cages, flat-top in NIC cages.
- `No FEC` on a 400G/NDR link, or FEC mismatch between ends, produces `Negotiation failure` — fix configuration before touching cables.
- `mlxlink` is a diagnostic, not a monitor; UFM/NetQ telemetry polls properly. `mlxfwmanager --query / -u`, `mlxup`, `flint` for firmware — upgrade before RMA.
- `iblinkinfo` finds *which* link; `mlxlink` explains *why*; `ethtool -m` is the Ethernet-only quick look.

## Appendix C — Knowledge check

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

---
## Solution guide — Hands-on assignment

### linkscan.sh

```bash
#!/usr/bin/env bash
# linkscan.sh — pull mlxlink -m / -c for every ConnectX port on this host and flag outliers (Module 8.3)
# Usage: sudo ./linkscan.sh [--json]
# AC1: no device names in source; everything is discovered from `mst status -v`.
set -u
JSON=0; [[ ${1:-} == "--json" ]] && JSON=1

# ---- thresholds ----
T_TEMP_MARGIN=10      # flag if temp >= ceiling - 10 C
T_RX_DELTA_DB=3       # flag a lane more than 3 dB below the best lane on the port
T_BER_RATIO=100       # flag a lane whose raw BER is >100x the best lane
T_BER_ABS=1e-6        # flag any lane worse than 1e-6 raw
# effective errors > 0 and link down > 0 are always flagged

[[ $EUID -eq 0 ]] || { echo "run as root"; exit 2; }
mst start >/dev/null 2>&1 || true

FLAGS=()
(( JSON )) || printf "%-8s %-28s %6s %-28s %-32s %8s %5s\n" RDMA MST TEMP "RX dBm (lanes)" "RAW BER (lanes)" EFF_ERR LDOWN

# mst status -v: columns DEVICE_TYPE MST PCI RDMA NET NUMA. Take MST (col 2) and RDMA (col 4) for ConnectX rows.
while read -r mstdev rdma; do
  m=$(mlxlink -d "$mstdev" -m 2>/dev/null)
  c=$(mlxlink -d "$mstdev" -c 2>/dev/null)
  [[ -z $m || -z $c ]] && { FLAGS+=("$rdma: mlxlink returned nothing (port down / module absent?)"); continue; }

  # --- parse module info ---
  temp=$(grep -m1 'Temperature \[C\]' <<<"$m" | sed -E 's/.*: *([-0-9]+).*/\1/')
  ceil=$(grep -m1 'Temperature \[C\]' <<<"$m" | sed -E 's/.*\.\.([-0-9]+)\].*/\1/')
  rx=$(grep -m1 'Rx Power Current' <<<"$m" | sed -E 's/.*: *([^ \[]+).*/\1/')       # "0.8,0.6,0.9,-14.2"
  # --- parse counters ---
  raw=$(grep -m1 'Raw Physical BER Per Lane' <<<"$c" | sed -E 's/.*: *//')          # "2E-10,2E-10,2E-10,1E-8"
  eff=$(grep -m1 'Effective Physical Errors' <<<"$c" | sed -E 's/.*: *//')
  ldown=$(grep -m1 'Link Down Counter' <<<"$c" | sed -E 's/.*: *//')

  (( JSON )) && printf '{"rdma":"%s","mst":"%s","temp_c":%s,"temp_ceiling_c":%s,"rx_dbm":[%s],"raw_ber":[%s],"effective_errors":%s,"link_down":%s}\n' \
      "$rdma" "$mstdev" "${temp:-null}" "${ceil:-null}" "${rx:-}" "$(tr 'E' 'e' <<<"${raw:-}")" "${eff:-null}" "${ldown:-null}" \
    || printf "%-8s %-28s %4s/%-3s %-28s %-32s %8s %5s\n" "$rdma" "$mstdev" "${temp:-?}" "${ceil:-?}" "${rx:-N/A}" "${raw:-?}" "${eff:-?}" "${ldown:-?}"

  # --- flags ---
  if [[ -n $temp && -n $ceil ]] && (( temp >= ceil - T_TEMP_MARGIN )); then
    FLAGS+=("$rdma: TEMP ${temp}C within ${T_TEMP_MARGIN}C of alarm ceiling ${ceil}C -> airflow/heatsink"); fi
  if [[ -n $rx && $rx != *N/A* ]]; then
    while read -r lane val; do FLAGS+=("$rdma: RX lane $lane at ${val} dBm is >${T_RX_DELTA_DB} dB below best lane -> fiber/connector lane"); done \
      < <(awk -v d="$T_RX_DELTA_DB" 'BEGIN{RS=","} {v[NR]=$1+0; if(NR==1||v[NR]>best) best=v[NR]} END{for(i=1;i<=NR;i++) if (best-v[i]>d) print i-1, v[i]}' <<<"$rx")
  fi
  if [[ -n $raw ]]; then
    while read -r lane val why; do FLAGS+=("$rdma: RAW BER lane $lane = $val ($why) -> monitor; replace only if effective errors appear"); done \
      < <(awk -v r="$T_BER_RATIO" -v a="$T_BER_ABS" 'BEGIN{RS=","} {v[NR]=$1+0; if(NR==1||v[NR]<best) best=v[NR]}
           END{for(i=1;i<=NR;i++){ if (v[i]>a) print i-1, v[i], "worse than " a; else if (best>0 && v[i]/best>r) print i-1, v[i], ">" r "x best lane"}}' <<<"$raw")
  fi
  [[ ${eff:-0} =~ ^[0-9]+$ ]] && (( eff > 0 ))   && FLAGS+=("$rdma: EFFECTIVE errors $eff > 0 -> FEC saturated, replace cable/module")
  [[ ${ldown:-0} =~ ^[0-9]+$ ]] && (( ldown > 0 )) && FLAGS+=("$rdma: LINK DOWN count $ldown -> flapping, correlate with -m temp/power")
done < <(mst status -v 2>/dev/null | awk '$1 ~ /^(ConnectX|BlueField)/ && $2 ~ /^\/dev\/mst\// {print $2, $4}')

if (( ${#FLAGS[@]} )); then
  (( JSON )) || { echo; echo "FLAGGED:"; printf '  %s\n' "${FLAGS[@]}"; }
  exit 1
fi
(( JSON )) || echo "All ports within thresholds."
exit 0
```

**AC1** — `grep -E 'mlx5_|pciconf|/dev/mst/mt' linkscan.sh` matches only the `mst status -v` filter regex, no concrete device.

**AC2** — Feed the module's Section 2.3 and 2.4 samples by stubbing `mlxlink`:

```bash
$ mkdir -p stub && cat > stub/mlxlink <<'EOF'
#!/bin/bash
case "$*" in *-m*) cat "$STUB_M";; *-c*) cat "$STUB_C";; esac
EOF
$ chmod +x stub/mlxlink; printf 'ConnectX7(rev:0) /dev/mst/mt4129_pciconf0 17:00.0 mlx5_0 net-ibp23s0 0\n' > stub/mststat
$ cat > stub/mst <<'EOF'
#!/bin/bash
[[ $1 == status ]] && cat "$(dirname "$0")/mststat"; exit 0
EOF
$ chmod +x stub/mst
$ STUB_M=sample-2.3-m.txt STUB_C=sample-2.4-c.txt PATH=$PWD/stub:$PATH sudo -E ./linkscan.sh; echo "exit=$?"
RDMA     MST                            TEMP RX dBm (lanes)               RAW BER (lanes)                   EFF_ERR LDOWN
mlx5_0   /dev/mst/mt4129_pciconf0      54/80  0.8,0.6,0.9,-14.2            2E-10,2E-10,2E-10,1E-8                 0     0

FLAGGED:
  mlx5_0: RX lane 3 at -14.2 dBm is >3 dB below best lane -> fiber/connector lane
  mlx5_0: RAW BER lane 3 = 1e-08 (>100x best lane) -> monitor; replace only if effective errors appear
exit=1
```

Lane 3 is flagged on both Rx power and raw BER, exactly as the assignment requires; effective errors are 0 so no "replace" flag fires. Running the same stub with a healthy `-m` (four lanes ~0.8 dBm) and a healthy `-c` (raw all 2E-10, effective 0, link down 0) prints `All ports within thresholds.` with exit 0 — **AC2** and **AC3**.

### triage-runbook.md (AC4)

```markdown
# Link triage runbook (run `sudo ./linkscan.sh` on the node; act on FLAGGED lines)

| Flag | Next command | Decision |
|---|---|---|
| RAW BER lane N only (effective = 0) | `mlxlink -d <dev> --rx_fec_histogram --show_histogram`; clear counters, re-read in 10 min | MONITOR. Non-zero raw BER is normal on PAM4. Open a ticket only if the histogram populates high bins or effective errors appear. |
| RX lane N low (>3 dB below siblings) | `mlxlink -d <dev> -m` on the FAR END (its Tx lane N); `mlxlink -e` | Clean MPO ferrule + reseat both ends. Still low -> swap transceiver at the low-Rx end. Still low -> fiber. |
| EFFECTIVE errors > 0 or Symbol Errors rising | `mlxlink -l PH` + 60 s + `-c` (port check); `mlxfwmanager --query` | REPLACE cable/module (after loopback proves the port clean). Capture PN/SN/temp/BER for RMA. |
| LINK DOWN > 0 (flapping) | `-m` temperature and power; `-c` histogram; UFM/perfquery LinkErrorRecovery | If temp flag also present -> airflow first. Else treat as EFFECTIVE. Stabilize with `--speeds HDR` or `-a DN` until replaced. |
| TEMP within 10 C of ceiling | `-m` on the twin-port sibling (switch side); check heatsink variant (finned vs flat) and fan status | AIRFLOW/HEATSINK BEFORE CABLE. Heat drives BER; replacing the cable into the same hot cage repeats the fault. |
| mlxlink returned nothing | `mst status -v`; `mlxlink -d <dev>` (Troubleshooting Info: "Cable is unplugged"?) | Reseat module; check cage; escalate if the port never detects a module. |
Escalation bundle: linkscan.sh --json output, `mlxlink` full output for the port, `mlxlink -m -c -e --rx_fec_histogram --show_histogram`, `mlxfwmanager --query`, far-end `-m`.
```

Raw-only flags route to MONITOR, effective-error flags to REPLACE, temperature flags to AIRFLOW/HEATSINK before cable — **AC4** met.
