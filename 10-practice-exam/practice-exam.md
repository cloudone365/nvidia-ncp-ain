# NCP-AIN Practice Exam

75 questions, weighted to the official blueprint (Spectrum 22 · InfiniBand 22 · Troubleshooting 15 · Automation 8 · AI DC Design 4 · Kubernetes 4). Give yourself 120 minutes, closed book. Answer key with rationales is at the bottom — don't scroll until you're done.

Scoring guide: ≥ 85% (64+) you're ready; 70–84% re-read the pitfalls sections of the domains you missed; < 70% go back to the modules.

---

## Section 1 — NVIDIA Spectrum Networking (Q1–Q22)

**1.** You run `nv set qos roce mode lossless` and `nv config apply` on a Spectrum-4 leaf. Which of the following does Cumulus Linux configure automatically as a result?
A. PFC on all eight priorities and ECN on priority 0
B. PFC and ECN on priority 3 for RoCE data, with CNP mapped to a higher-priority traffic class
C. PFC on priority 3 only; ECN must be enabled separately with `nv set qos congestion-control`
D. Nothing on the data plane; the command only enables RoCE telemetry

**2.** What is the primary purpose of adaptive routing on a Spectrum-X fabric?
A. To replace BGP with a link-state protocol for faster convergence
B. To spray packets of a flow across multiple equal-cost paths and avoid ECMP hash collisions caused by low-entropy AI traffic
C. To encrypt RoCE traffic between leaf and spine
D. To enable VXLAN without EVPN

**3.** Spectrum-X adaptive routing sends packets of the same flow over different paths. What handles the resulting out-of-order delivery?
A. The spine switch reorders packets before forwarding
B. NCCL retransmits out-of-order packets
C. The BlueField-3 SuperNIC places data directly in order at the receiver (Direct Data Placement)
D. TCP sequence numbers in the RoCE header

**4.** Which Spectrum-4 switch provides 64 × 800G OSFP ports (51.2 Tb/s)?
A. SN2700
B. SN3700
C. SN4600
D. SN5600

**5.** RoCE v2 encapsulates InfiniBand transport in:
A. Ethernet frames with EtherType 0x8915, non-routable
B. UDP/IP with destination port 4791, routable
C. TCP/IP with destination port 4791
D. GRE tunnels over IP

**6.** In a standard NVIDIA RoCE deployment, what are the default DSCP values for RoCE data and CNP (congestion notification) packets respectively?
A. 0 and 7
B. 26 and 48
C. 46 and 26
D. 3 and 6

**7.** A tenant's RoCE flows must be isolated from another tenant's on the same Spectrum-X leaf/spine fabric. Which combination provides L3 multi-tenancy?
A. Separate VLANs with MLAG
B. VXLAN with BGP-EVPN and a VRF per tenant
C. Separate PKeys per tenant
D. Port-based ACLs only

**8.** Which EVPN route type advertises IP prefixes (e.g. for inter-subnet routing in a tenant VRF)?
A. Type 2
B. Type 3
C. Type 5
D. Type 1

**9.** What does NVIDIA Air (DSX Air) provide?
A. A physical lab you can rent by the hour
B. A cloud-hosted digital twin where you can build and validate Cumulus/Spectrum topologies and automation before deploying
C. Firmware updates for Spectrum switches
D. A GPU scheduling service

**10.** Which NVUE command sequence enables adaptive routing globally on a Cumulus Linux Spectrum switch?
A. `nv set router adaptive-routing enable on` then `nv config apply`
B. `nv set system ar on`
C. `nv set qos roce adaptive-routing on`
D. `nv set interface swp1-64 ar enable`

**11.** You want to drop RoCE packets that exceed buffer thresholds rather than pause upstream senders, accepting retransmissions. Which NVUE mode do you choose?
A. `nv set qos roce mode lossless`
B. `nv set qos roce mode lossy`
C. `nv set qos roce enable off`
D. `nv set qos pfc mode drop`

**12.** What is "ECN" doing in a DCQCN deployment?
A. Switches mark packets when a queue exceeds a threshold; the receiver sends a CNP; the sender reduces its rate
B. Switches pause the sender's port for a quantum of time
C. Switches drop packets to signal congestion
D. The NIC marks its own packets to request priority

**13.** Which statement about PFC is TRUE?
A. PFC pauses the entire link for all traffic classes
B. PFC pauses a single priority (traffic class) on a link, leaving other priorities flowing
C. PFC only works on InfiniBand
D. PFC and ECN cannot be enabled at the same time

**14.** A "PFC storm" is best described as:
A. Many ECN marks in a short period
B. Pause frames propagating hop by hop back through the fabric, stalling unrelated traffic on the same priority
C. A broadcast storm on the RoCE VLAN
D. Excessive BGP updates after a link flap

**15.** Which software stack ships the host-side drivers, libraries, and tools for ConnectX/BlueField NICs today (successor to MLNX_OFED)?
A. OpenSM
B. DOCA (including DOCA-OFED)
C. NetQ
D. Cumulus Linux

**16.** In the Spectrum-X platform, the BlueField-3 SuperNIC differs from a standard ConnectX NIC primarily because it:
A. Has more PCIe lanes
B. Implements RoCE adaptive routing reordering and telemetry-based congestion control in concert with the Spectrum-4 switch
C. Runs the Subnet Manager
D. Replaces the need for PFC on the switch

**17.** You enable What Just Happened on a Cumulus switch. What does WJH report?
A. Per-flow bandwidth
B. ASIC-level packet drops with the specific reason (e.g. no route, ACL deny, tail drop, PFC) and packet headers
C. BGP route changes
D. Link transceiver temperatures

**18.** Which NetQ command validates the health of the EVPN overlay across the fabric?
A. `netq show evpn`
B. `netq check evpn`
C. `netq trace evpn`
D. `nv show evpn status`

**19.** On Cumulus Linux, which tool reports hardware forwarding-table utilization (routes, MAC entries, ECMP groups) in the Spectrum ASIC?
A. `cl-resource-query`
B. `nv show system`
C. `ethtool -S`
D. `cl-support`

**20.** In SONiC, where is the persistent switch configuration stored?
A. `/etc/network/interfaces`
B. `/etc/sonic/config_db.json`, loaded into the Redis CONFIG_DB
C. `/etc/nvue.d/startup.yaml`
D. `/etc/frr/frr.conf` only

**21.** Which BlueField mode makes the DPU behave as a conventional NIC with no Arm-side data path?
A. DPU mode
B. NIC mode
C. Zero-trust mode
D. Embedded mode

**22.** Which of the following is a valid NVUE command to break an 800G OSFP port into two 400G ports?
A. `nv set interface swp1 link breakout 2x`
B. `nv set interface swp1 speed 400G x2`
C. `nv set port swp1 split 2`
D. `nv set interface swp1 breakout 2x400`

---

## Section 2 — NVIDIA InfiniBand Networking (Q23–Q44)

**23.** What is the link rate of a single NDR InfiniBand port (4x)?
A. 100 Gb/s
B. 200 Gb/s
C. 400 Gb/s
D. 800 Gb/s

**24.** Which of the following is responsible for discovering the fabric, assigning LIDs, and programming the forwarding tables of every switch in an InfiniBand subnet?
A. The Subnet Manager (SM)
B. BGP
C. UFM Telemetry
D. Each switch independently via spanning tree

**25.** `ibstat` on a host shows `State: Initializing`, `Physical state: LinkUp`, `Base lid: 0`. The most likely cause is:
A. A bad cable
B. The port is administratively down
C. No Subnet Manager is running (or reachable) in the subnet
D. The wrong firmware on the HCA

**26.** Two SMs are configured in a subnet. Which one becomes master?
A. The one on the lowest LID
B. The one with the highest priority (ties broken by lowest GUID)
C. The one that started first, always
D. Both run simultaneously as masters

**27.** What is the default InfiniBand partition key?
A. 0x0001
B. 0x7fff
C. 0xffff
D. 0x8000

**28.** In a PKey, what does setting the most-significant bit (e.g. 0x8001 vs 0x0001) indicate?
A. The partition is disabled
B. Full membership (can communicate with all members) vs limited membership (can only talk to full members)
C. The PKey is reserved for management
D. IPoIB is enabled on that partition

**29.** Which opensm routing engine is designed for fat-tree (Clos) topologies and typically used in DGX SuperPODs?
A. minhop
B. updn
C. ftree
D. dor

**30.** An InfiniBand fabric is "lossless" because:
A. It uses PFC like RoCE
B. It uses credit-based link-level flow control — a sender only transmits when the receiver has advertised buffer credits
C. It retransmits every lost packet at the link layer
D. Switches have unlimited buffers

**31.** Which InfiniBand mechanism allows traffic of different Service Levels to be separated into independent buffer queues on a link?
A. PKeys
B. Virtual Lanes (VLs) with SL-to-VL mapping
C. LMC
D. GIDs

**32.** Which NCCL environment variable selects the InfiniBand Service Level used for NCCL traffic?
A. `NCCL_IB_SL`
B. `NCCL_IB_HCA`
C. `NCCL_NET_GDR_LEVEL`
D. `NCCL_IB_TC`

**33.** SHARP improves all-reduce performance by:
A. Compressing data before transmission
B. Performing reduction operations inside the switch ASICs so data crosses the network fewer times
C. Replacing InfiniBand with NVLink
D. Increasing the MTU

**34.** Which component manages SHARP aggregation trees in the fabric?
A. sharp_am (SHARP Aggregation Manager), typically co-located with the SM/UFM
B. NetQ
C. nvidia-peermem
D. ibdiagnet

**35.** Which NCCL environment variable must be set so NCCL will use SHARP collectives (via the CollNet plugin)?
A. `NCCL_IB_DISABLE=1`
B. `NCCL_COLLNET_ENABLE=1`
C. `NCCL_SHARP=on`
D. `NCCL_ALGO=Ring`

**36.** Adaptive routing on Quantum-2 InfiniBand switches is enabled where?
A. On each HCA with mlxconfig
B. In the Subnet Manager configuration (opensm / UFM SM parameters), which programs AR groups into the switches
C. With `nv set router adaptive-routing`
D. In NetQ

**37.** Which UFM edition adds cybersecurity/anomaly analytics on top of enterprise fabric management?
A. UFM Telemetry
B. UFM Enterprise
C. UFM Cyber-AI
D. UFM Lite

**38.** UFM Enterprise high availability is typically achieved by:
A. Running two UFM servers in an active/standby pair with replicated state (and the SM failing over with them)
B. Running UFM inside each switch
C. Having NetQ monitor UFM
D. Running UFM on a Kubernetes cluster with three replicas

**39.** Which UFM REST endpoint family would you call to list all ports and their counters?
A. `/ufmRest/resources/ports`
B. `/ufmRest/app/sm`
C. `/nvue_v1/interface`
D. `/api/v1/ib/ports`

**40.** IPoIB "connected mode" differs from "datagram mode" in that connected mode:
A. Requires RoCE
B. Uses RC queue pairs allowing an MTU up to 65520 bytes, whereas datagram mode uses UD with MTU limited to the IB MTU (e.g. 2044 bytes)
C. Is the only mode that supports PKeys
D. Does not require a Subnet Manager

**41.** How do you check the Subnet Manager status and priority on an NVIDIA InfiniBand switch running MLNX-OS?
A. `nv show router sm`
B. `show ib sm`
C. `ibstat -sm`
D. `ufm show sm`

**42.** A host's `ibv_devinfo` shows the HCA `link_layer: Ethernet` but the port is cabled to a Quantum-2 InfiniBand switch. What is the fix?
A. Replace the cable with an OSFP cable
B. Set `LINK_TYPE_P1=1` (IB) with mlxconfig and reset/reboot
C. Enable RoCE on the switch
D. Configure a PKey

**43.** Which mechanism allows a single physical IB port to be assigned multiple consecutive LIDs for multipath routing?
A. LMC (LID Mask Control)
B. PKey
C. GID index
D. QPN

**44.** The IB utility `ibportstate` can be used to:
A. Enable, disable, or reset a specific port (by LID and port number) and set link speed/width
B. Display partition membership
C. Run a bandwidth test
D. Start the Subnet Manager

---

## Section 3 — Troubleshooting Tools (Q45–Q59)

**45.** Which utility lists every link in the fabric with its negotiated width and speed, making it easy to spot a port running at 2x instead of 4x?
A. `ibstat`
B. `iblinkinfo`
C. `ibping`
D. `ib_write_bw`

**46.** `ibping` differs from ICMP `ping` because it:
A. Tests TCP connectivity
B. Tests InfiniBand-layer reachability to a LID or GUID using MADs, independent of IPoIB — and requires an `ibping -S` server on the target
C. Only works over RoCE
D. Measures RDMA bandwidth

**47.** Which tool performs a comprehensive fabric scan — routing checks, port counters, cable info, and a summary of errors/warnings — writing its output under `/var/tmp/ibdiagnet2/`?
A. `ibnodes`
B. `ibdiagnet`
C. `perfquery`
D. `sminfo`

**48.** To measure RDMA write latency between two hosts with GPU memory as the buffer, you run:
A. `ib_write_lat --use_cuda=0` on the server and `ib_write_lat --use_cuda=0 <server>` on the client
B. `ping -s 65000`
C. `iperf3 -c <server>`
D. `ibping -L <lid>`

**49.** `ib_write_bw` between two NDR-connected hosts reports ~195 Gb/s instead of ~390 Gb/s. `iblinkinfo` shows the host port at `2X 106.25 Gbps ... ( Could be 4X )`. Most likely cause:
A. SM is not running
B. The link negotiated reduced width — check the cable/transceiver with mlxlink and reseat or replace
C. PFC is misconfigured
D. The wrong GID index was used

**50.** Which mlxlink option displays cable/module EEPROM information such as vendor, part number, and per-lane Tx/Rx power?
A. `-m`
B. `-c`
C. `-e`
D. `-a`

**51.** In `mlxlink -c` output, which counter should ideally be zero on a healthy NDR link?
A. Raw physical BER
B. Effective physical BER (post-FEC)
C. Time since last clear
D. Link up count

**52.** `mlxconfig -d /dev/mst/mt4129_pciconf0 s LINK_TYPE_P1=2` changes the first port to which link type (after reset)?
A. InfiniBand
B. Ethernet
C. Auto-sense
D. Disabled

**53.** Which command on Cumulus shows WJH drops of category L1 (physical layer) through NetQ?
A. `netq show wjh-drop l1`
B. `nv show interface swp1 wjh`
C. `cl-resource-query --l1`
D. `netq check l1`

**54.** The Cable Validation Tool (CVT) is used to:
A. Measure cable BER
B. Compare the discovered fabric cabling against the planned topology and report miscabled or swapped ports/rails
C. Update transceiver firmware
D. Generate UFM reports

**55.** `cl-resource-query` shows the IPv4 host (neighbor) table at 100% utilization on a leaf. What symptom would you expect?
A. BGP sessions flapping
B. New hosts/ARP entries cannot be installed in hardware, causing intermittent reachability for new endpoints
C. PFC storms
D. Link flaps

**56.** Which NetQ command would you use to see PFC pause and ECN-marked counters per switch port to validate a RoCE deployment?
A. `netq show roce-counters`
B. `netq show interfaces`
C. `netq show bgp`
D. `netq check mlag`

**57.** Which `perfquery` counter indicates the local port received packets with physical errors (e.g. bad symbols), often a cable issue?
A. `PortXmitWait`
B. `PortRcvErrors` / `SymbolErrorCounter`
C. `PortXmitData`
D. `LinkDownedCounter`

**58.** Which UFM feature generates a bundle of logs and configuration for NVIDIA support?
A. `netq show events`
B. UFM system dump (log collection)
C. `cl-support`
D. `ibdiagnet -r`

**59.** A `cl-support` bundle on Cumulus Linux is used for:
A. Collecting switch logs, configuration, and state for troubleshooting/support
B. Applying an NVUE configuration
C. Enabling WJH
D. Upgrading the switch image

---

## Section 4 — Automation and Configuration (Q60–Q67)

**60.** Which NVUE command shows the pending configuration changes that have not yet been applied?
A. `nv config diff`
B. `nv config show`
C. `nv show config`
D. `nv config apply --dry-run`

**61.** After `nv config apply`, what is required for the configuration to persist across a reboot?
A. Nothing; apply is persistent
B. `nv config save`
C. `nv config commit`
D. `write memory`

**62.** You maintain a Spectrum-X leaf template as NVUE YAML. Which command replaces the entire running configuration with the contents of `leaf.yaml`?
A. `nv config replace leaf.yaml` then `nv config apply`
B. `nv config patch leaf.yaml`
C. `nv config load leaf.yaml`
D. `nv set file leaf.yaml`

**63.** Which Ansible collection provides modules for configuring Cumulus Linux via NVUE?
A. `cisco.ios`
B. `nvidia.nvue`
C. `cumulus.nclu`
D. `community.network`

**64.** The NVUE REST API on a Cumulus switch listens on which port by default?
A. 443
B. 8080
C. 8765
D. 22

**65.** In an Ansible playbook using `nvidia.nvue`, what is typically the final task after pushing configuration objects?
A. A reboot
B. An apply step (`nvidia.nvue.config` with `state: apply`, or `nv config apply` via the command module) so the staged revision takes effect
C. `nv config save` only
D. `netq install`

**66.** Which NVUE top-level key appears in an NVUE YAML configuration file?
A. `- set:`
B. `interfaces:`
C. `config:`
D. `frr:`

**67.** Which command on Cumulus Linux lets you view a previously applied configuration revision?
A. `nv config history`
B. `nv show revision`
C. `git log`
D. `nv config diff --all`

---

## Section 5 — AI Data Center Design and Optimization (Q68–Q71)

**68.** In a rail-optimized topology, what is connected to a single leaf switch ("rail")?
A. All 8 NICs of one DGX node
B. The same-numbered NIC/GPU (e.g. NIC 0) from every node in the scalable unit
C. All spine uplinks
D. Only storage NICs

**69.** The main benefit of a rail-optimized topology for NCCL collectives is:
A. Lower cabling cost
B. Same-rail GPU-to-GPU traffic traverses a single leaf (one hop), avoiding spine congestion for the most common communication pattern
C. It eliminates the need for NVLink
D. It allows oversubscription of 4:1

**70.** Which NCCL collective is used by data-parallel training to synchronize gradients across GPUs?
A. All-to-all
B. Broadcast
C. All-reduce
D. Scatter

**71.** GPUDirect RDMA allows:
A. GPUs to boot without a CPU
B. The NIC to read/write GPU memory directly over PCIe, bypassing a staging copy through host memory
C. NVLink to carry Ethernet frames
D. Two GPUs in different hosts to share a PCIe bus

---

## Section 6 — Kubernetes Integration (Q72–Q75)

**72.** Which Custom Resource configures the NVIDIA Network Operator's components (driver, device plugins, CNIs)?
A. `NicClusterPolicy`
B. `ClusterPolicy`
C. `NetworkAttachmentDefinition`
D. `SriovNetworkNodePolicy`

**73.** To give a pod access to an RDMA device shared by multiple pods on the same node, you request which kind of resource?
A. `nvidia.com/gpu`
B. A resource exposed by the RDMA shared device plugin, e.g. `rdma/rdma_shared_device_a`
C. `hugepages-2Mi`
D. `sriov/vf`

**74.** Which CNI / annotation mechanism lets a pod get a secondary RDMA/IPoIB interface in addition to its default cluster network?
A. Calico IPIP
B. Multus with `k8s.v1.cni.cncf.io/networks` referencing a NetworkAttachmentDefinition
C. kube-proxy
D. CoreDNS

**75.** To verify the Network Operator deployment is functional, which is the most direct test?
A. `kubectl get nodes`
B. Run `ib_write_bw` (or nccl-tests) between two pods that have the RDMA resource and NAD attached
C. `helm list`
D. `kubectl logs coredns`

---
---

## Answer key

### Section 1 — Spectrum
1. **B** — Lossless mode configures PFC + ECN on the RoCE priority (3) and maps CNP to a high-priority class; `nv show qos roce` displays the result.
2. **B** — AR sprays flows across paths to defeat hash collisions from low-entropy, high-bandwidth AI flows.
3. **C** — Spectrum-X relies on the BlueField-3 SuperNIC to reorder via Direct Data Placement.
4. **D** — SN5600 is the 64 × 800G Spectrum-4 switch.
5. **B** — RoCE v2 = IB transport over UDP 4791, routable.
6. **B** — DSCP 26 (TC 3) for RoCE data, DSCP 48 (TC 6) for CNP are NVIDIA's standard defaults.
7. **B** — VXLAN + BGP-EVPN with per-tenant VRFs is the Spectrum multi-tenancy model.
8. **C** — EVPN Type 5 carries IP prefixes; Type 2 is MAC/IP.
9. **B** — NVIDIA Air is a cloud digital twin for Cumulus/Spectrum.
10. **A** — `nv set router adaptive-routing enable on` (verify exact path in your Cumulus version).
11. **B** — Lossy mode uses ECN without PFC and drops on overflow.
12. **A** — ECN mark → CNP → sender rate reduction is DCQCN.
13. **B** — PFC is per-priority pause.
14. **B** — PFC storms are cascading pause frames across hops.
15. **B** — DOCA/DOCA-OFED replaced MLNX_OFED.
16. **B** — The SuperNIC's AR reordering and CC are what make Spectrum-X "end-to-end".
17. **B** — WJH is reason-coded drop telemetry from the ASIC.
18. **B** — `netq check` runs validation suites; `netq show` displays state.
19. **A** — cl-resource-query reads ASIC table utilization.
20. **B** — SONiC config lives in config_db.json / Redis CONFIG_DB.
21. **B** — NIC mode disables the DPU data path.
22. **A** — `nv set interface swpN link breakout 2x`.

### Section 2 — InfiniBand
23. **C** — NDR = 400 Gb/s per 4x port.
24. **A** — The SM owns discovery, LID assignment, and LFT programming.
25. **C** — Initializing + LID 0 = physical link up but no SM has configured the port.
26. **B** — Highest priority wins; lowest GUID breaks ties.
27. **B** — 0x7fff is the default partition.
28. **B** — MSB set = full membership.
29. **C** — ftree is the fat-tree routing engine.
30. **B** — Credit-based flow control is why IB needs no PFC.
31. **B** — VLs separated by SL2VL mapping.
32. **A** — `NCCL_IB_SL`. `NCCL_IB_TC` is the traffic class (used for RoCE DSCP).
33. **B** — In-network reduction.
34. **A** — sharp_am manages trees.
35. **B** — `NCCL_COLLNET_ENABLE=1`.
36. **B** — AR is an SM/switch-side feature configured in SM (opensm/UFM) parameters.
37. **C** — Cyber-AI adds anomaly detection.
38. **A** — Active/standby pair with replication.
39. **A** — `/ufmRest/resources/ports`.
40. **B** — Connected mode = RC QPs, large MTU; datagram = UD.
41. **B** — `show ib sm` on MLNX-OS.
42. **B** — `LINK_TYPE_P1=1` (IB) then reset.
43. **A** — LMC assigns 2^LMC LIDs per port.
44. **A** — ibportstate manages remote port state/speed.

### Section 3 — Troubleshooting
45. **B** — iblinkinfo shows every link's width/speed.
46. **B** — ibping is IB-layer, needs a server.
47. **B** — ibdiagnet.
48. **A** — `--use_cuda` uses GPU memory via GPUDirect.
49. **B** — 2x width = half bandwidth; physical layer issue.
50. **A** — `-m` for module info.
51. **B** — Effective (post-FEC) BER should be ~0; raw BER up to ~1e-5 on NDR is normal.
52. **B** — 1 = IB, 2 = Ethernet.
53. **A** — `netq show wjh-drop l1`.
54. **B** — CVT compares planned vs discovered cabling.
55. **B** — Table exhaustion prevents hardware install of new entries.
56. **A** — `netq show roce-counters`.
57. **B** — PortRcvErrors/SymbolErrorCounter point to physical problems.
58. **B** — UFM system dump.
59. **A** — cl-support is the Cumulus support bundle.

### Section 4 — Automation
60. **A** — `nv config diff` shows pending vs applied.
61. **B** — `nv config save` writes startup.yaml.
62. **A** — `nv config replace` then apply.
63. **B** — `nvidia.nvue`.
64. **C** — 8765.
65. **B** — Apply is a separate step.
66. **A** — NVUE YAML starts with `- set:`.
67. **A** — `nv config history`.

### Section 5 — AI DC Design
68. **B** — Rail N = NIC N from every node.
69. **B** — Same-rail traffic is one hop.
70. **C** — All-reduce for gradient sync.
71. **B** — NIC ↔ GPU memory directly.

### Section 6 — Kubernetes
72. **A** — NicClusterPolicy.
73. **B** — RDMA shared device plugin resources.
74. **B** — Multus + NAD annotation.
75. **B** — An RDMA perf test between pods proves the data path.
