# Module 7.1 — NVIDIA Network Operator: RDMA and InfiniBand in Kubernetes

> **Exam domain:** Kubernetes Integration (5%)  
> **Source course(s):** AI Infrastructure and Operations Fundamentals (Kubernetes section); NVIDIA Network Operator training materials  
> **Source docs:** NVIDIA Network Operator Application Notes — Getting Started with Kubernetes; Getting Started with Red Hat OpenShift; DGX BasePOD Deployment Guide (RHEL/OpenShift); NVIDIA GPU Operator documentation (GPUDirect RDMA section); Kubernetes Network Plumbing Working Group Multus/NAD specification  
> **Estimated study time:** 3 hours

## 1. Learning objectives

By the end of this module you will be able to…

- **Describe** each component the **Network Operator** deploys and what problem each solves (driver container, RDMA shared device plugin, SR-IOV device plugin and SR-IOV Network Operator, Multus, IPoIB CNI, macvlan, NVIDIA IPAM, ib-kubernetes). *(Exam objective: Kubernetes Integration — Network Operator components)*
- **Explain** the relationship between the Network Operator and the GPU Operator and why GPUDirect RDMA in pods needs both. *(Kubernetes Integration — GPUDirect in containers)*
- **Install** the Network Operator with Helm and author a `values.yaml` for an RDMA shared-device or SR-IOV deployment. *(Kubernetes Integration / Automation — Helm deployment)*
- **Describe** the `NicClusterPolicy` CRD, its sections, and how it drives the operator. *(Kubernetes Integration — CRDs)*
- **Configure** `NetworkAttachmentDefinition` objects for RDMA shared device (macvlan), SR-IOV InfiniBand, and IPoIB networks. *(Kubernetes Integration — secondary networks)*
- **Write** a pod spec that requests an RDMA resource and attaches a secondary network with the Multus annotation. *(Kubernetes Integration — pod networking)*
- **Verify** a deployment end-to-end: operator pods, node resources, `ibstat` in a pod, **ib_write_bw** between pods, and an nccl-tests job. *(Troubleshooting Tools — validation in Kubernetes)*
- **Troubleshoot** common failures: driver/kernel mismatch, hugepages, **PKey** mismatch, RDMA netns mode, missing capabilities. *(Troubleshooting Tools — Kubernetes failures)*

## 2. Key technical concepts

### 2.1 Why Kubernetes needs a Network Operator

*Analogy:* stock Kubernetes gives every pod one phone line (the pod network). AI workloads need a second, private express lane to the GPU fabric, plus a badge to the RDMA hardware. The Network Operator is the building manager that installs the extra lines, hands out the badges, and keeps the wiring consistent on every floor.

Kubernetes assumes one network interface per pod, provisioned by a single CNI plugin, carrying ordinary TCP/IP. AI workloads need something different on every count:

- A pod must reach an **RDMA** device (`/dev/infiniband/uverbsN`) to use ibverbs, NCCL's IB transport, or GPUDirect RDMA.
- The compute fabric is usually a *second* network (InfiniBand or dedicated RoCE Ethernet), separate from the pod network, so pods need multiple interfaces.
- The NIC drivers (MOFED / DOCA-OFED) and firmware config (SR-IOV VF counts, link type) must be consistent across nodes, and in containers the kernel modules must match the host kernel.
- Resources that are not CPU/memory — "an RDMA-capable NIC", "a VF on rail 3" — must be scheduled.

The **NVIDIA Network Operator** uses the Kubernetes operator pattern (a controller watching custom resources) to deploy and lifecycle-manage all of these pieces consistently across the cluster. It is the networking counterpart of the **GPU Operator**, which does the same for the GPU driver, container toolkit, device plugin, DCGM and MIG.

### 2.2 What the Network Operator deploys

| Component | Delivered as | Function | When needed |
|---|---|---|---|
| **NVIDIA DOCA-OFED / MOFED driver container** (`ofedDriver`) | DaemonSet on selected nodes | Builds/loads the Mellanox OFED (mlx5_core, ib_core, rdma_cm, etc.) kernel modules matching the node kernel, replacing inbox drivers | Whenever the host does not already run the required OFED version (DGX OS normally does, so DGX BasePOD guides often set `deploy: false`) |
| **NIC Feature Discovery** (`nicFeatureDiscovery`) | DaemonSet | Labels nodes with NIC capabilities (e.g. `feature.node.kubernetes.io/...`) used by the operator to target components | Works with Node Feature Discovery (NFD), which the chart can also install (`nfd.enabled`) |
| **RDMA Shared Device Plugin** (`rdmaSharedDevicePlugin`) | DaemonSet (device plugin) | Exposes a physical RDMA device as a *shareable* extended resource, e.g. `rdma/rdma_shared_device_a`; many pods can use the same HCA, each getting `/dev/infiniband/*` mounted | Shared PF access: InfiniBand compute fabric with one HCA per GPU, no SR-IOV |
| **SR-IOV Network Device Plugin** (`sriovDevicePlugin`) | DaemonSet | Exposes SR-IOV VFs (or whole PFs) as exclusive resources, e.g. `nvidia.com/hostdev` | Exclusive device per pod, VF isolation, DPDK |
| **SR-IOV Network Operator** (`sriovNetworkOperator`) | Separate operator (sub-chart) | Creates VFs on PFs (`SriovNetworkNodePolicy`), configures driver binding, generates NADs (`SriovNetwork`, `SriovIBNetwork`) | SR-IOV deployments; default on OpenShift |
| **Multus CNI** (`secondaryNetwork.multus`) | DaemonSet | Meta-CNI that attaches additional interfaces to pods based on `NetworkAttachmentDefinition` (NAD) objects | Any secondary network |
| **Container Networking Plugins** (`secondaryNetwork.cniPlugins`) | DaemonSet installing binaries | Reference CNIs: **macvlan**, ipvlan, host-device, etc., used by NADs | macvlan networks over RoCE/Ethernet PFs |
| **IPoIB CNI** (`secondaryNetwork.ipoib`) | DaemonSet | Creates IPoIB child interfaces (one per pod, with its own PKey-capable netdev) on an InfiniBand PF | IP connectivity over InfiniBand in pods (needed for NCCL bootstrap over IB, NFS over IPoIB) |
| **IPAM plugin** (`secondaryNetwork.ipamPlugin` = Whereabouts, or **NVIDIA IPAM** `nvIpam`) | DaemonSet + controller | Cluster-wide IP allocation for secondary networks (host-local IPAM cannot coordinate across nodes) | Every secondary network needs one; NV-IPAM is the NVIDIA-recommended, scalable choice |
| **ib-kubernetes** (`ibKubernetes`) | Deployment | Watches pods using SR-IOV IB networks and programs the **PKey** membership of their VF GUIDs into the Subnet Manager via the **UFM** REST API | InfiniBand SR-IOV with per-tenant partitions |
| **DOCA Telemetry Service** (`docaTelemetryService`) | DaemonSet | Exports NIC counters to Prometheus | Monitoring |

Everything above is declared in a single cluster-scoped custom resource, the **`NicClusterPolicy`**; the Helm chart with `deployCR: true` creates it from `values.yaml`.

**Why the exam cares:** it tests which component solves which problem — especially the two device plugins (shared `rdma/` vs exclusive `nvidia.com/`) and ib-kubernetes (PKeys for SR-IOV IB only).

### 2.3 Relationship to the GPU Operator

*Analogy:* GPUDirect RDMA is a two-key lock. The Network Operator holds the NIC key (OFED + an RDMA device in the pod); the GPU Operator holds the GPU key (a driver built with nvidia-peermem). Turn only one and the door stays shut — silently, with data bouncing through host memory.

GPUDirect RDMA inside a pod requires:
1. The NVIDIA GPU driver (GPU Operator, `driver` component) with the **nvidia-peermem** module loaded — enabled in GPU Operator values by `driver.rdma.enabled=true` (and `driver.rdma.useHostMofed=true` when OFED is on the host rather than in the Network Operator's container).
2. The OFED driver (host or Network Operator) that nvidia-peermem links against.
3. An RDMA device exposed to the pod (RDMA shared device plugin or SR-IOV).

Order matters: install the Network Operator (OFED) *first*, then the GPU Operator with `driver.rdma.enabled=true`, so the GPU driver container can find the OFED symbols when building nvidia-peermem. On DGX OS / BasePOD where both drivers are preinstalled on the host, both operators are typically deployed with `driver.enabled=false` / `ofedDriver.deploy=false` and only the device plugins and CNIs are used. See [Module 1.3](../01-ai-datacenter-design/03-gpu-to-gpu-communication-nccl-gpudirect.md) for the GPUDirect mechanics.

### 2.4 Installing with Helm

Prerequisites:
- Kubernetes 1.2x cluster (check the compatibility matrix for the operator release); `kubectl` and Helm 3.
- Nodes with ConnectX-6/7/8 or BlueField-3 NICs; firmware supporting the desired mode; for SR-IOV, `SRIOV_EN=1` and `NUM_OF_VFS` set with **mlxconfig** and IOMMU enabled in the kernel (`intel_iommu=on iommu=pt` or AMD equivalent).
- For InfiniBand, a running Subnet Manager (**UFM** or opensm) and, for ib-kubernetes, UFM REST credentials.
- Hugepages configured on nodes if you will run DPDK or some RDMA workloads that request them.
- Node Feature Discovery (NFD) — the chart can deploy it.

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update

# Export defaults for the release you are installing (pin the version in production)
helm show values nvidia/network-operator --version v25.4.0 > values.yaml   # verify current version

# Install with a custom values file
helm install network-operator nvidia/network-operator \
  -n nvidia-network-operator --create-namespace \
  --version v25.4.0 -f ./values.yaml --wait
```

**Example `values.yaml` — RDMA shared device (InfiniBand compute fabric, host OFED)**

```yaml
# values.yaml for Network Operator — DGX-style nodes, shared HCAs, IPoIB secondary network
nfd:
  enabled: true                 # deploy Node Feature Discovery
  deployNodeFeatureRules: true

sriovNetworkOperator:
  enabled: false

deployCR: true                  # chart creates the NicClusterPolicy from these values

ofedDriver:
  deploy: false                 # DGX OS already has DOCA-OFED; set true on plain Ubuntu/RHEL nodes
  # image: doca-driver
  # repository: nvcr.io/nvidia/mellanox
  # version: 25.04-0.6.1.0-2    # verify in current docs
  # upgradePolicy:
  #   autoUpgrade: true
  #   maxParallelUpgrades: 1

nicFeatureDiscovery:
  deploy: true                  # verify key name/availability in your chart version

rdmaSharedDevicePlugin:
  deploy: true
  resources:
    - name: rdma_shared_device_a
      vendors: [15b3]           # Mellanox/NVIDIA PCI vendor ID
      deviceIDs: [1021]         # ConnectX-7 (1021); ConnectX-6 Dx = 101d — verify
      ifNames: [ibp26s0, ibp60s0, ibp77s0, ibp94s0, ibp156s0, ibp188s0, ibp204s0, ibp220s0]
      # linkTypes: [infiniband]  # alternative selector
      # rdmaHcaMax: 63           # how many pods may share each device (default 1000; verify)

sriovDevicePlugin:
  deploy: false

secondaryNetwork:
  deploy: true
  multus:
    deploy: true
  cniPlugins:
    deploy: true
  ipoib:
    deploy: true
  ipamPlugin:
    deploy: false               # using NV-IPAM instead of Whereabouts

nvIpam:
  deploy: true
  enableWebhook: false

ibKubernetes:
  deploy: false                 # only for SR-IOV IB with UFM-managed PKeys
```

Selector semantics: each `resources` entry becomes one extended resource name under the `rdma/` prefix; a device matches if it satisfies *all* selectors listed (`vendors`, `deviceIDs`, `drivers`, `ifNames`, `linkTypes`). Use `ifNames` when you want to keep storage/in-band BlueField ports out of the compute resource.

**Example `values.yaml` — SR-IOV InfiniBand with ib-kubernetes**

```yaml
nfd:
  enabled: true
sriovNetworkOperator:
  enabled: true
deployCR: true
ofedDriver:
  deploy: true
rdmaSharedDevicePlugin:
  deploy: false
sriovDevicePlugin:
  deploy: false                 # the SR-IOV Network Operator deploys its own device plugin
secondaryNetwork:
  deploy: true
  multus:
    deploy: true
  cniPlugins:
    deploy: true
  ipamPlugin:
    deploy: true                # Whereabouts
ibKubernetes:
  deploy: true
  periodicUpdateSeconds: 5
  pKeyGUIDPoolRangeStart: "02:00:00:00:00:00:00:00"
  pKeyGUIDPoolRangeEnd: "02:FF:FF:FF:FF:FF:FF:FF"
  ufmSecret: ufm-secret         # Secret with UFM_USERNAME/UFM_PASSWORD/UFM_ADDRESS/UFM_HTTP_SCHEMA/UFM_PORT
```

### 2.5 The NicClusterPolicy CRD

*Analogy:* the NicClusterPolicy is the building's single work order. Add a line, a crew (DaemonSet) shows up on every floor; delete a line, the crew leaves.

`NicClusterPolicy` (`mellanox.com/v1alpha1`) is the operator's single source of truth. Each top-level key under `spec` corresponds to a component; the operator reconciles the DaemonSets to match. If a key is absent the component is not deployed (and is removed if it was). There is exactly one `NicClusterPolicy`, conventionally named `nic-cluster-policy`.

```yaml
apiVersion: mellanox.com/v1alpha1
kind: NicClusterPolicy
metadata:
  name: nic-cluster-policy
spec:
  nodeAffinity:                           # optional: restrict components to NIC-bearing worker nodes
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: feature.node.kubernetes.io/pci-15b3.present
              operator: In
              values: ["true"]

  ofedDriver:
    image: doca-driver
    repository: nvcr.io/nvidia/mellanox
    version: 25.04-0.6.1.0-2              # verify current version
    forcePrecompiled: false
    startupProbe:
      initialDelaySeconds: 10
      periodSeconds: 20
    livenessProbe:
      initialDelaySeconds: 30
      periodSeconds: 30
    readinessProbe:
      initialDelaySeconds: 10
      periodSeconds: 30
    upgradePolicy:
      autoUpgrade: true
      maxParallelUpgrades: 1
      safeLoad: false
      drain:
        enable: true
        force: true
        podSelector: ""
        timeoutSeconds: 300
        deleteEmptyDir: true

  nicFeatureDiscovery:
    image: nic-feature-discovery
    repository: ghcr.io/mellanox
    version: v0.0.1                       # verify

  rdmaSharedDevicePlugin:
    image: k8s-rdma-shared-dev-plugin
    repository: ghcr.io/mellanox
    version: v1.5.2                       # verify
    # config is a JSON string consumed by the plugin
    config: |
      {
        "configList": [
          {
            "resourceName": "rdma_shared_device_a",
            "rdmaHcaMax": 63,
            "selectors": {
              "vendors": ["15b3"],
              "deviceIDs": ["1021"],
              "ifNames": ["ibp26s0","ibp60s0","ibp77s0","ibp94s0","ibp156s0","ibp188s0","ibp204s0","ibp220s0"]
            }
          }
        ]
      }

  sriovDevicePlugin:
    image: sriov-network-device-plugin
    repository: ghcr.io/k8snetworkplumbingwg
    version: v3.9.0                       # verify
    config: |
      {
        "resourceList": [
          {
            "resourcePrefix": "nvidia.com",
            "resourceName": "hostdev",
            "selectors": {
              "vendors": ["15b3"],
              "isRdma": true
            }
          }
        ]
      }

  secondaryNetwork:
    cniPlugins:
      image: plugins
      repository: ghcr.io/k8snetworkplumbingwg
      version: v1.6.2-update.1            # verify
    multus:
      image: multus-cni
      repository: ghcr.io/k8snetworkplumbingwg
      version: v4.1.0                     # verify
    ipoib:
      image: ipoib-cni
      repository: ghcr.io/mellanox
      version: v1.2.0                     # verify
    ipamPlugin:
      image: whereabouts
      repository: ghcr.io/k8snetworkplumbingwg
      version: v0.7.0                     # verify

  nvIpam:
    image: nvidia-k8s-ipam
    repository: ghcr.io/mellanox
    version: v0.3.7                       # verify
    enableWebhook: false

  ibKubernetes:
    image: ib-kubernetes
    repository: ghcr.io/mellanox
    version: v1.1.0                       # verify
    periodicUpdateSeconds: 5
    pKeyGUIDPoolRangeStart: "02:00:00:00:00:00:00:00"
    pKeyGUIDPoolRangeEnd: "02:FF:FF:FF:FF:FF:FF:FF"
    ufmSecret: ufm-secret
```

A `notReady` status almost always means one DaemonSet (usually `mofed-*`) is not running on every targeted node.

### 2.6 NetworkAttachmentDefinitions

*Analogy:* a NAD is the wiring diagram for one extra phone line: which wall socket (master interface), which protocol (CNI type), and who hands out the numbers (IPAM). The `resourceName` annotation is the note saying "this line only works with badge X" — it ties the interface to the device the scheduler allocated.

Multus attaches a secondary interface when a pod carries the annotation `k8s.v1.cni.cncf.io/networks: <nad-name>`. The NAD's `config` is a CNI JSON document. NVIDIA also provides higher-level CRs (`MacvlanNetwork`, `IPoIBNetwork`, `HostDeviceNetwork`) that the operator renders into NADs; the raw NAD forms are shown here because the exam may show either.

**RDMA shared device over an Ethernet/RoCE PF (macvlan + NV-IPAM)**

```yaml
apiVersion: nv-ipam.nvidia.com/v1alpha1
kind: IPPool
metadata:
  name: roce-pool
  namespace: nvidia-network-operator
spec:
  subnet: 192.168.100.0/24
  perNodeBlockSize: 32
  gateway: 192.168.100.1
  nodeSelector:
    nodeSelectorTerms:
      - matchExpressions:
          - key: node-role.kubernetes.io/worker
            operator: Exists
---
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: roce-macvlan
  namespace: default
  annotations:
    k8s.v1.cni.cncf.io/resourceName: rdma/rdma_shared_device_a
spec:
  config: |
    {
      "cniVersion": "0.3.1",
      "name": "roce-macvlan",
      "type": "macvlan",
      "master": "ens2f0np0",
      "mode": "bridge",
      "mtu": 9000,
      "ipam": {
        "type": "nv-ipam",
        "poolName": "roce-pool"
      }
    }
```

Equivalent operator CR:
```yaml
apiVersion: mellanox.com/v1alpha1
kind: MacvlanNetwork
metadata:
  name: roce-macvlan
spec:
  networkNamespace: default
  master: ens2f0np0
  mode: bridge
  mtu: 9000
  ipam: |
    { "type": "nv-ipam", "poolName": "roce-pool" }
```

**SR-IOV InfiniBand (`ib-sriov` CNI, with PKey)**

```yaml
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: ib-sriov-tenant-a
  namespace: default
  annotations:
    k8s.v1.cni.cncf.io/resourceName: nvidia.com/hostdev
spec:
  config: |
    {
      "cniVersion": "0.3.1",
      "name": "ib-sriov-tenant-a",
      "type": "ib-sriov",
      "pkey": "0x8001",
      "link_state": "enable",
      "ibKubernetesEnabled": true,
      "ipam": {
        "type": "whereabouts",
        "range": "10.56.217.0/24",
        "exclude": ["10.56.217.0/32", "10.56.217.255/32"]
      }
    }
```
With `ibKubernetesEnabled: true`, ib-kubernetes reads the pod's VF GUID and adds it to **PKey** `0x8001` in **UFM**; the PKey in the NAD must exist (or be creatable) in the SM configuration. With the SR-IOV Network Operator you would instead create a `SriovIBNetwork` CR with `pkey: "0x8001"` and it renders this NAD.

Supporting `SriovNetworkNodePolicy` (creates VFs):
```yaml
apiVersion: sriovnetwork.openshift.io/v1
kind: SriovNetworkNodePolicy
metadata:
  name: ib-policy
  namespace: nvidia-network-operator
spec:
  resourceName: hostdev
  nodeSelector:
    feature.node.kubernetes.io/pci-15b3.present: "true"
  priority: 99
  numVfs: 8
  nicSelector:
    vendor: "15b3"
    pfNames: ["ibs1f0"]
  linkType: ib
  isRdma: true
  deviceType: netdevice
```

**IPoIB (shared PF, one IPoIB child per pod)**

```yaml
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: ipoib-net
  namespace: default
  annotations:
    k8s.v1.cni.cncf.io/resourceName: rdma/rdma_shared_device_a
spec:
  config: |
    {
      "cniVersion": "0.3.1",
      "name": "ipoib-net",
      "type": "ipoib",
      "master": "ibp26s0",
      "ipam": {
        "type": "whereabouts",
        "range": "10.60.0.0/16",
        "exclude": ["10.60.0.0/32", "10.60.255.255/32"]
      }
    }
```
Equivalent operator CR: `kind: IPoIBNetwork` with `master: ibp26s0` and the same `ipam` JSON. IPoIB child interfaces inherit the parent's PKey (default `0x7fff`); for a non-default PKey the parent IPoIB interface on the host must already be a child on that PKey.

**Why the exam cares:** NAD `type` → use case: `macvlan` (RoCE/Ethernet), `ib-sriov` (IB VF with `pkey`), `ipoib` (IP over IB, no RDMA by itself).

### 2.7 Pod specs

A pod needs three things for RDMA on a secondary network: the **resource request** (gets the device), the **Multus annotation** (gets the interface), and **`IPC_LOCK`** (lets it pin memory for registrations).

**Shared RDMA device + IPoIB**

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: rdma-test-1
  annotations:
    k8s.v1.cni.cncf.io/networks: ipoib-net
spec:
  restartPolicy: OnFailure
  containers:
    - name: rdma
      image: nvcr.io/nvidia/doca/doca:2.9.1-devel   # any image with rdma-core + perftest; verify tag
      command: ["sleep", "infinity"]
      securityContext:
        capabilities:
          add: ["IPC_LOCK"]        # required to pin memory for RDMA registrations
      resources:
        requests:
          rdma/rdma_shared_device_a: 1
        limits:
          rdma/rdma_shared_device_a: 1
```

**SR-IOV IB VF + GPU (GPUDirect RDMA)**

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: gdr-worker-0
  annotations:
    k8s.v1.cni.cncf.io/networks: ib-sriov-tenant-a
spec:
  containers:
    - name: worker
      image: nvcr.io/nvidia/pytorch:25.03-py3        # verify tag
      command: ["sleep", "infinity"]
      securityContext:
        capabilities:
          add: ["IPC_LOCK"]
      resources:
        requests:
          nvidia.com/gpu: 1
          nvidia.com/hostdev: 1
        limits:
          nvidia.com/gpu: 1
          nvidia.com/hostdev: 1
      volumeMounts:
        - name: shm
          mountPath: /dev/shm
  volumes:
    - name: shm
      emptyDir:
        medium: Memory
        sizeLimit: 8Gi
```
Multiple networks: `k8s.v1.cni.cncf.io/networks: "ib-sriov-tenant-a, ib-sriov-tenant-a"` attaches two VFs, or use the JSON list form to name interfaces explicitly. `/dev/shm` sizing matters for NCCL's SHM transport and PyTorch DataLoader.

### 2.8 OpenShift differences

| Area | Vanilla Kubernetes | Red Hat OpenShift |
|---|---|---|
| Install | Helm chart from `helm.ngc.nvidia.com` | **OperatorHub** (Red Hat certified operator) via OLM; a `Subscription` in `nvidia-network-operator` namespace. Helm also supported. |
| SR-IOV | Network Operator's sub-chart deploys the SR-IOV Network Operator | Install the **SR-IOV Network Operator** from OperatorHub separately (`openshift-sriov-network-operator` namespace); set `sriovNetworkOperator.enabled: false` in the NVIDIA operator |
| Node configuration | Kernel args, hugepages, modules via your config management | **MachineConfig** / **MachineConfigPool** objects (e.g. IOMMU kernel args, hugepages, `rdma` netns mode); nodes reboot on apply |
| Drivers | DOCA-OFED container compiles against node kernel | DOCA-OFED container uses the **Driver Toolkit** image matched to the RHCOS release; entitlement not needed on 4.9+ (verify) |
| NFD | Chart can install | Red Hat NFD Operator from OperatorHub |
| Multus | Chart deploys | Built into OpenShift SDN/OVN-Kubernetes; do **not** deploy Multus from the NVIDIA operator (`secondaryNetwork.multus.deploy: false`) |
| Security | PodSecurity admission | SCCs; privileged DaemonSets need the appropriate SCC, handled by the certified operator |
| DGX BasePOD on RHEL/OpenShift | — | Deployment guide installs host DOCA-OFED on RHEL and uses the operators without driver containers; verify in the current BasePOD RHEL guide |

### 2.9 nccl-tests as a Kubernetes job

Use the MPI Operator (`kubeflow.org/v2beta1 MPIJob`) or a plain StatefulSet with `mpirun`/`torchrun`. Key elements of the worker template: request `nvidia.com/gpu: 8` and 8 RDMA resources (or one shared resource), add the network annotation, set `IPC_LOCK`, mount a large `/dev/shm`, and set `NCCL_SOCKET_IFNAME=net1` (or the pod network if bootstrap runs there) and `NCCL_IB_HCA` as appropriate.

```yaml
apiVersion: kubeflow.org/v2beta1
kind: MPIJob
metadata:
  name: nccl-allreduce
spec:
  slotsPerWorker: 8
  runPolicy:
    cleanPodPolicy: Running
  mpiReplicaSpecs:
    Launcher:
      replicas: 1
      template:
        spec:
          containers:
            - name: launcher
              image: nvcr.io/nvidia/pytorch:25.03-py3      # contains nccl-tests in /opt; verify
              command:
                - mpirun
                - -np
                - "16"
                - --allow-run-as-root
                - -bind-to
                - none
                - -x
                - NCCL_DEBUG=INFO
                - -x
                - NCCL_SOCKET_IFNAME=net1
                - -x
                - NCCL_IB_HCA=mlx5
                - /opt/nccl-tests/build/all_reduce_perf
                - -b
                - 1G
                - -e
                - 8G
                - -f
                - "2"
                - -g
                - "1"
    Worker:
      replicas: 2
      template:
        metadata:
          annotations:
            k8s.v1.cni.cncf.io/networks: ipoib-net
        spec:
          containers:
            - name: worker
              image: nvcr.io/nvidia/pytorch:25.03-py3
              securityContext:
                capabilities:
                  add: ["IPC_LOCK"]
              resources:
                limits:
                  nvidia.com/gpu: 8
                  rdma/rdma_shared_device_a: 1
              volumeMounts:
                - name: shm
                  mountPath: /dev/shm
          volumes:
            - name: shm
              emptyDir:
                medium: Memory
                sizeLimit: 16Gi
```
Read the output exactly as in [Module 1.3](../01-ai-datacenter-design/03-gpu-to-gpu-communication-nccl-gpudirect.md): look for `NET/IB/.../GDRDMA` in the launcher log and compare busbw with the node's NIC capacity.

## 3. Live-style walkthrough — helm install to ib_write_bw, end to end

Let's bring RDMA to a 2-node Kubernetes cluster (`dgx01`, `dgx02`, DGX OS with host DOCA-OFED, ConnectX-7 on InfiniBand) and prove it with a pod-to-pod bandwidth test. Every command is commented with what it does and what you should see.

**Step 1 — confirm the host is ready.** The operator cannot fix the host underneath it.

```bash
ssh dgx01 "rdma system show"                  # RDMA subsystem network-namespace mode.
                                              # EXPECT: "netns exclusive" for per-pod device isolation with IPoIB/SR-IOV.
                                              # If "shared": run `rdma system set netns exclusive` BEFORE device plugins start.
ssh dgx01 "ibdev2netdev"                      # Map HCAs to netdevs; note the compute netdev names for ifNames.
                                              # EXPECT: mlx5_0 port 1 ==> ibp26s0 (Up) ... eight IB netdevs Up.
ssh dgx01 "lsmod | grep -E 'mlx5_ib|nvidia_peermem'"   # Host OFED loaded and peermem present (GPU Operator with rdma enabled).
                                              # EXPECT: both lines. No nvidia_peermem = GPUDirect will silently be off in pods.
```

**Step 2 — install the operator with Helm.**

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia      # Add the NGC Helm repo that hosts network-operator.
helm repo update                                             # Refresh the index so --version resolves.
helm show values nvidia/network-operator --version v25.4.0 > values.yaml   # Dump defaults; edit into the RDMA-shared values from Section 2.4. (verify version)

helm install network-operator nvidia/network-operator \      # Release name "network-operator".
  -n nvidia-network-operator --create-namespace \            # Conventional namespace; created if missing.
  --version v25.4.0 -f ./values.yaml --wait                  # --wait blocks until the operator Deployment is Ready (not the DaemonSets).
# EXPECT: "STATUS: deployed" and NOTES describing the NicClusterPolicy created by deployCR: true.
```

**Step 3 — watch the NicClusterPolicy converge.** This is the single status that tells you whether every component landed.

```bash
kubectl get nicclusterpolicy                                 # Cluster-scoped; exactly one object.
# NAME                 STATUS     AGE
# nic-cluster-policy   notReady   40s                        <- normal for the first minute while DaemonSets roll out
kubectl -n nvidia-network-operator get pods -o wide          # Watch each component become Running on both nodes.
# EXPECT (RDMA-shared design, host OFED):
#   network-operator-xxxx                      Running      (controller)
#   nic-feature-discovery-ds-xxxx  x2          Running      (one per NIC node)
#   rdma-shared-dp-ds-xxxx          x2         Running      (device plugin)
#   kube-multus-ds-xxxx             x2         Running
#   cni-plugins-ds-xxxx             x2         Running
#   ipoib-cni-ds-xxxx               x2         Running
#   nv-ipam-node-xxxx x2, nv-ipam-controller   Running
#   NO mofed-* pods: ofedDriver.deploy is false because DGX OS has DOCA-OFED on the host.
kubectl get nicclusterpolicy
# NAME                 STATUS   AGE
# nic-cluster-policy   ready    3m                           <- "good"
kubectl describe nicclusterpolicy nic-cluster-policy | sed -n '/Applied States/,$p'   # Per-component state; any "notReady" names the DaemonSet to inspect.
```

**Step 4 — confirm the node now advertises RDMA resources.** The device plugin turned eight HCAs into a schedulable extended resource.

```bash
kubectl describe node dgx01 | grep -A12 Allocatable          # Extended resources appear here once the plugin registers.
#   nvidia.com/gpu:              8                            <- GPU Operator
#   rdma/rdma_shared_device_a:   63                           <- our resource; 63 = rdmaHcaMax (how many pods may share)
# If the value is 0 or absent: the selectors (ifNames/deviceIDs) matched nothing — see Section 4.2.
kubectl -n nvidia-network-operator logs ds/rdma-shared-dp-ds | tail -20   # Plugin log lists matched devices and the resource it registered.
```

**Step 5 — create the secondary network.** We use IPoIB so the pods also get an IP on the InfiniBand fabric (NCCL bootstrap, perftest addressing). The `resourceName` annotation binds it to the device-plugin resource.

```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: ipoib-net                                  # Referenced by the pod annotation below.
  namespace: default
  annotations:
    k8s.v1.cni.cncf.io/resourceName: rdma/rdma_shared_device_a   # Tie this network to the RDMA resource so the scheduler and Multus agree.
spec:
  config: |
    {
      "cniVersion": "0.3.1",
      "name": "ipoib-net",
      "type": "ipoib",                             # IPoIB CNI: creates a child IPoIB interface per pod on the master PF.
      "master": "ibp26s0",                         # Host IPoIB netdev of the HCA (rail 0 here); must exist on every node.
      "ipam": {
        "type": "whereabouts",                     # Cluster-wide IPAM so dgx01 and dgx02 never hand out the same address.
        "range": "10.60.0.0/16",
        "exclude": ["10.60.0.0/32", "10.60.255.255/32"]
      }
    }
EOF
kubectl get net-attach-def                                   # EXPECT: ipoib-net listed.
```

**Step 6 — launch two test pods, one per node.**

```bash
for i in 1 2; do
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: rdma-test-$i
  annotations:
    k8s.v1.cni.cncf.io/networks: ipoib-net           # Multus: attach net1 from the NAD above.
spec:
  nodeName: dgx0$i                                   # Pin pod 1 to dgx01 and pod 2 to dgx02 so the test crosses the fabric.
  restartPolicy: OnFailure
  containers:
    - name: rdma
      image: nvcr.io/nvidia/doca/doca:2.9.1-devel    # Has rdma-core and perftest (ib_write_bw). verify tag
      command: ["sleep", "infinity"]
      securityContext:
        capabilities:
          add: ["IPC_LOCK"]                          # Without it, ibv_reg_mr fails: "Couldn't allocate MR".
      resources:
        requests:
          rdma/rdma_shared_device_a: 1               # The RDMA device. Without this /dev/infiniband is empty even if net1 exists.
        limits:
          rdma/rdma_shared_device_a: 1
EOF
done
kubectl get pods -o wide                                     # EXPECT: both Running, one on each node.
```

**Step 7 — verify inside the pods.** Two separate things must be true: the interface exists (Multus), and the device exists (device plugin).

```bash
kubectl exec rdma-test-1 -- ip -br addr                      # Interfaces in the pod netns.
# eth0@if123   UP   10.244.1.17/24          <- pod network (primary CNI)
# net1         UP   10.60.0.3/16            <- IPoIB child from the NAD. "good": has an address from the whereabouts range.
kubectl describe pod rdma-test-1 | grep -A6 'network-status'  # Multus records what it attached and the device it used.
kubectl exec rdma-test-1 -- ls /dev/infiniband               # EXPECT: rdma_cm uverbs0 (plus more uverbsN if several HCAs matched).
                                                             # Empty here with net1 present = netns mode "shared" (Section 4.1).
kubectl exec rdma-test-1 -- ibstat                           # EXPECT: "State: Active", "Physical state: LinkUp", "Rate: 400", a LID, link_layer InfiniBand.
kubectl exec rdma-test-1 -- ibv_devinfo | grep -E 'hca_id|state|active_mtu'   # libibverbs view: PORT_ACTIVE, MTU 4096.
kubectl exec rdma-test-2 -- ip -4 -br addr show net1         # Note pod 2's net1 IP for the client side below.
# net1   UP   10.60.0.4/16
```

**Step 8 — ib_write_bw across the fabric.** Server in pod 1, client in pod 2, addressed over the IPoIB `net1` IPs; the data path itself is RDMA on the HCA, not IPoIB.

```bash
kubectl exec rdma-test-1 -- ib_write_bw -d mlx5_0 -a --report_gbits -F &   # Server: -d device, -a all message sizes, Gb/s units,
                                                                            # -F ignore CPU-frequency warnings. Backgrounded.
sleep 2
kubectl exec rdma-test-2 -- ib_write_bw -d mlx5_0 -a --report_gbits -F 10.60.0.3   # Client: connect to server pod's net1 IP (the TCP rendezvous).
```

```text
---------------------------------------------------------------------------------------
                    RDMA_Write BW Test
 Dual-port       : OFF          Device         : mlx5_0
 Number of qps   : 1            Transport type : IB
 Connection type : RC           Using SRQ      : OFF
 PCIe relax order: ON
 ibv_wr* API     : ON
 TX depth        : 128
 CQ Moderation   : 100
 Mtu             : 4096[B]                       <- "good": 4K MTU on IB
 Link type       : IB
 Max inline data : 0[B]
 rdma_cm QPs     : OFF
 Data ex. method : Ethernet                      <- rendezvous over the net1 IP; data is still RDMA
---------------------------------------------------------------------------------------
 local address: LID 0x1d QPN 0x00a3 PSN 0x3f2b1c RKey 0x1fe300 VAddr 0x007f3a1c000000
 remote address: LID 0x25 QPN 0x0091 PSN 0x7a19e2 RKey 0x203400 VAddr 0x007f12c4000000
---------------------------------------------------------------------------------------
 #bytes     #iterations    BW peak[Gb/sec]    BW average[Gb/sec]   MsgRate[Mpps]
 2          5000             0.052              0.051              3.21
 ...
 65536      5000           390.11             389.87               0.74
 1048576    5000           391.02             390.95               0.047
 8388608    5000           391.40             391.33               0.0058
---------------------------------------------------------------------------------------
```

*Instructor note:* `Transport type: IB`, two LIDs (the SM assigned them — this is the fabric, not a loopback), and ~390 Gb/s at large messages on a 400G NDR link. That is "good": ~97 % of line rate. ~195 Gb/s means a port negotiated at 200; ~100 Gb/s or a hang at "Waiting for client" usually means the two pods are in different **PKey** partitions (Section 4.3) or the server pod's net1 IP was wrong.

**Step 9 — latency and the NCCL job.**

```bash
kubectl exec rdma-test-1 -- ib_write_lat -d mlx5_0 -F &      # Server for latency test.
kubectl exec rdma-test-2 -- ib_write_lat -d mlx5_0 -F 10.60.0.3   # EXPECT: ~1.8–2.5 µs typical one-hop (verify RA figure).
kubectl apply -f nccl-allreduce-mpijob.yaml                  # The MPIJob from Section 2.9.
kubectl logs -f job/nccl-allreduce-launcher | grep -E 'NET/IB|busbw|Avg bus'   # EXPECT: "via NET/IB/0/GDRDMA" lines, busbw high 300s GB/s.
```

If step 8 passes and step 9 shows `GDRDMA`, the Network Operator, the GPU Operator and the fabric are all correct.

## 4. Troubleshooting / common gotchas

### 4.1 Pod has `net1` with an IP, but `/dev/infiniband` is empty and `ibstat` finds nothing

**Symptom.** `ip addr` in the pod shows `net1` with an IPoIB address; `ls /dev/infiniband` is empty; `ibstat` says "No IB devices found".

**Why it happens.** The host's RDMA subsystem is in `shared` netns mode. The IPoIB CNI created the interface, but the device plugin cannot isolate an RDMA device into the pod's network namespace unless the host is in `exclusive` mode. Interface and device are delivered by different components.

**How to debug.**
```bash
ssh dgx01 "rdma system show"                           # EXPECT "netns exclusive". "netns shared" is the fault.
kubectl -n nvidia-network-operator logs ds/rdma-shared-dp-ds | grep -i netns   # Plugin may log a warning about the mode.
kubectl get pod rdma-test-1 -o jsonpath='{.spec.containers[0].resources}'      # Confirm the rdma/ resource was actually requested.
```

**Fix.** On every node, with no RDMA devices in use: `rdma system set netns exclusive`, persisted via udev/systemd (or a `MachineConfig` on OpenShift), then restart the device plugin DaemonSet and recreate the pod.

### 4.2 Pod stays `Pending` with `Insufficient rdma/rdma_shared_device_a`

**Symptom.** `kubectl describe pod` shows `0/2 nodes are available: 2 Insufficient rdma/rdma_shared_device_a`. The node's Allocatable shows the resource at 0, absent, or at 1 with one pod already scheduled.

**Why it happens.** Either the device plugin's selectors matched no device (an `ifNames` entry that does not match the host netdev name, a wrong `deviceIDs` value), the plugin is not running on that node, or `rdmaHcaMax` is set to 1 and the single slot is taken.

**How to debug.**
```bash
kubectl describe node dgx01 | grep -A12 Allocatable            # Is the resource present and how many?
kubectl -n nvidia-network-operator get ds rdma-shared-dp-ds    # DESIRED == READY on all NIC nodes?
kubectl -n nvidia-network-operator logs ds/rdma-shared-dp-ds   # "no devices matched" or the resource registration line.
ssh dgx01 "ls /sys/class/infiniband; ibdev2netdev"             # Real netdev names to compare with ifNames.
kubectl get nicclusterpolicy nic-cluster-policy -o jsonpath='{.spec.rdmaSharedDevicePlugin.config}'   # The JSON actually applied.
```

**Fix.** Correct `ifNames`/`deviceIDs` in the policy (or switch to `linkTypes: [infiniband]`), set `rdmaHcaMax` to the intended sharing count, and let the DaemonSet roll.

### 4.3 Two SR-IOV IB pods are `Active` but cannot talk; `ib_write_bw` hangs at "Waiting for client"

**Symptom.** Both pods show a VF with `State: Active` and an IP; `ibping`/`ib_write_bw` between them times out.

**Why it happens.** The pods' VF GUIDs are not members of the same **PKey**. The NAD's `pkey` does not exist in the SM, ib-kubernetes cannot reach **UFM** to add the GUIDs, or `pkey` was omitted so each VF defaulted differently. Link state is a layer below partitions — it tells you nothing about PKey membership.

**How to debug.**
```bash
kubectl -n nvidia-network-operator logs deploy/ib-kubernetes | tail -30   # UFM REST errors, "pkey 0x8001 not found", auth failures.
kubectl get secret ufm-secret -n nvidia-network-operator -o yaml          # UFM_ADDRESS/UFM_USERNAME present?
kubectl get net-attach-def ib-sriov-tenant-a -o jsonpath='{.spec.config}' | grep -E 'pkey|ibKubernetesEnabled'
kubectl exec gdr-worker-0 -- cat /sys/class/infiniband/mlx5_0/ports/1/pkeys/0   # PKey the VF actually holds (0x7fff = default).
# On the UFM/SM side: confirm the partition and its member GUIDs (UFM UI or REST /ufmRest/resources/pkeys — verify path).
```

**Fix.** Create the PKey in UFM (or the SM partition config) with the VF GUIDs as members, fix the UFM secret, set `ibKubernetesEnabled: true` and an explicit `pkey` in the NAD, and recreate the pods.

### 4.4 `mofed-*` pods in CrashLoopBackOff after a node OS update

**Symptom.** `NicClusterPolicy` flips to `notReady`; `mofed-ubuntu22.04-ds-*` pods crash; logs show module build errors or `Unknown symbol`.

**Why it happens.** The node's kernel changed and the DOCA-OFED driver container does not support (or has no precompiled modules for) the new kernel.

**How to debug.**
```bash
kubectl -n nvidia-network-operator logs ds/mofed-ubuntu22.04-ds | grep -iE 'error|unknown symbol|kernel'
ssh dgx01 "uname -r"                                   # New kernel version.
kubectl get nicclusterpolicy -o jsonpath='{.items[0].spec.ofedDriver.version}'   # Compare with the DOCA-OFED/kernel support matrix.
```

**Fix.** Pin the node kernel or move to a DOCA-OFED version that supports it; set `forcePrecompiled: false` with build dependencies available; on OpenShift confirm the Driver Toolkit matches the RHCOS release.

## 5. Hands-on assignment — ConnectX-7 InfiniBand cluster with a non-default PKey

**Scenario.** A tenant has been allocated an isolated **PKey** `0x8010` on a shared InfiniBand fabric managed by **UFM**. You must deliver a complete manifest set so that their pods get SR-IOV VFs on ConnectX-7, land in PKey `0x8010`, receive IPs, and can prove RDMA bandwidth between two nodes. Nodes are plain Ubuntu (no host OFED), 8 × ConnectX-7 per node with PF names `ibs1f0`–`ibs8f0`, `SRIOV_EN=1 NUM_OF_VFS=8` already set with `mlxconfig`, IOMMU enabled.

**Requirements.**
1. Write a `NicClusterPolicy` that deploys the DOCA-OFED driver container, NIC feature discovery, Multus, the CNI plugins, Whereabouts, and ib-kubernetes configured against a UFM secret; it must **not** deploy the RDMA shared device plugin or the standalone SR-IOV device plugin (the SR-IOV Network Operator will provide it).
2. Write a `SriovNetworkNodePolicy` that creates 8 VFs on `ibs1f0` as `linkType: ib`, RDMA-capable, exposed as resource `tenant_ib`.
3. Write a `NetworkAttachmentDefinition` (raw `ib-sriov` form) named `tenant-ib-8010` with `pkey: "0x8010"`, ib-kubernetes enabled, and Whereabouts IPAM on `10.80.10.0/24`.
4. Write a `Job` manifest set (or two Pods) — `perftest-server` and `perftest-client`, pinned to different nodes — that each request one `nvidia.com/tenant_ib`, attach `tenant-ib-8010`, carry `IPC_LOCK`, and run `ib_write_bw` server/client respectively with the client discovering the server's `net1` IP.
5. Write a `verify.sh` that checks: policy `ready`, node Allocatable shows `nvidia.com/tenant_ib: 8`, both pods Running on distinct nodes, the VF PKey inside each pod is `0x8010`, and the client log shows ≥ 380 Gb/s at the largest message size.

**Deliverables.** `nicclusterpolicy.yaml`, `sriov-node-policy.yaml`, `nad.yaml`, `perftest.yaml`, `verify.sh`, plus a short README with apply order.

**Acceptance criteria.**
- All YAML parses (`kubectl apply --dry-run=client -f`), and `nicclusterpolicy.yaml` contains `ofedDriver`, `nicFeatureDiscovery`, `secondaryNetwork.{multus,cniPlugins,ipamPlugin}` and `ibKubernetes`, with no `rdmaSharedDevicePlugin` or `sriovDevicePlugin` keys.
- `SriovNetworkNodePolicy` has `numVfs: 8`, `linkType: ib`, `isRdma: true`, `resourceName: tenant_ib`, `pfNames: ["ibs1f0"]`.
- The NAD annotation is `k8s.v1.cni.cncf.io/resourceName: nvidia.com/tenant_ib`, type `ib-sriov`, `pkey` `"0x8010"`, `ibKubernetesEnabled: true`.
- Pods request `nvidia.com/tenant_ib: 1`, carry the networks annotation, `IPC_LOCK`, and anti-affinity or `nodeName` guaranteeing different nodes.
- `verify.sh` reads the PKey from `/sys/class/infiniband/<dev>/ports/1/pkeys/0` inside each pod and compares to `0x8010`, and parses the bandwidth from the client log.
- README apply order is: UFM secret → NicClusterPolicy → wait ready → SriovNetworkNodePolicy → wait `SriovNetworkNodeState` Succeeded → NAD → perftest.

### Hint
- Section 2.5's full `NicClusterPolicy` is your template; delete the two device-plugin sections and keep `ibKubernetes` with `ufmSecret`.
- The `SriovNetworkNodePolicy` in Section 2.6 is nearly complete; change `resourceName`, `numVfs` and `pfNames`. Remember the SR-IOV Network Operator registers resources under `nvidia.com/`.
- Section 2.6's `ib-sriov` NAD shows the `pkey` and `ibKubernetesEnabled` fields; Section 4.3 shows where the PKey is visible inside a pod.
- For the client to find the server, consider a headless Service plus DNS, or have the client read the server pod's `network-status` annotation via `kubectl` in an init step; a simpler approach is a fixed Whereabouts IP for the server using the `ips` field in the networks annotation (JSON form).
- Walkthrough Step 8 shows exactly which `ib_write_bw` flags and which output row to parse.

## Appendix A — Quick command / config reference

```bash
# Helm
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia && helm repo update
helm show values nvidia/network-operator --version <ver> > values.yaml
helm install network-operator nvidia/network-operator -n nvidia-network-operator --create-namespace -f values.yaml --wait
helm upgrade network-operator nvidia/network-operator -n nvidia-network-operator -f values.yaml
helm uninstall network-operator -n nvidia-network-operator

# Operator state
kubectl get nicclusterpolicy
kubectl -n nvidia-network-operator get pods,ds
kubectl -n nvidia-network-operator logs ds/rdma-shared-dp-ds
kubectl get net-attach-def -A
kubectl get macvlannetwork,ipoibnetwork,hostdevicenetwork
kubectl get ippools.nv-ipam.nvidia.com -n nvidia-network-operator
kubectl -n nvidia-network-operator get sriovnetworknodestates -o yaml | grep -A5 'ibs1f0'

# Host-side prerequisites
mlxconfig -d /dev/mst/mt4129_pciconf0 set SRIOV_EN=1 NUM_OF_VFS=8     # then reboot
mlxconfig -d /dev/mst/mt4129_pciconf0 q | grep -E 'SRIOV_EN|NUM_OF_VFS|LINK_TYPE'
rdma system show                 # netns shared|exclusive
rdma system set netns exclusive  # required for per-pod RDMA device isolation (SR-IOV / IPoIB)
cat /proc/meminfo | grep -i huge
lsmod | grep -E 'mlx5_ib|nvidia_peermem'

# Inside a pod
ibstat; ibv_devinfo; ibdev2netdev
ib_write_bw -d mlx5_0 -a --report_gbits -F            # server
ib_write_bw -d mlx5_0 -a --report_gbits -F <peer-ip>  # client
ib_write_lat -d mlx5_0 <peer-ip>
cat /sys/class/infiniband/mlx5_0/ports/1/pkeys/0      # PKey held by the device
```

| Need | Component | Resource / object |
|---|---|---|
| Many pods share one HCA | RDMA shared device plugin | `rdma/<name>` |
| One exclusive VF per pod | SR-IOV Network Operator + device plugin | `nvidia.com/<name>`, `SriovNetworkNodePolicy` |
| Extra interface in pod | Multus + NAD | `k8s.v1.cni.cncf.io/networks` |
| IP over RoCE/Ethernet PF | macvlan CNI | NAD `type: macvlan` |
| IP over InfiniBand | IPoIB CNI | NAD `type: ipoib` |
| IB VF in a partition | ib-sriov CNI + ib-kubernetes + UFM | NAD `type: ib-sriov`, `pkey` |
| Cluster-wide IPs | Whereabouts or NV-IPAM | `IPPool` (nv-ipam) |

## Appendix B — Exam traps & key takeaways

- The Network Operator deploys and manages OFED drivers, device plugins (RDMA shared, SR-IOV), Multus and CNIs (macvlan, IPoIB), IPAM (Whereabouts/NV-IPAM), ib-kubernetes and telemetry via one `NicClusterPolicy`.
- RDMA shared device plugin → `rdma/<name>`, many pods per HCA, selectors by `vendors`, `deviceIDs`, `ifNames`, `linkTypes`. SR-IOV device plugin → `nvidia.com/<name>`, one VF per pod, VFs created by `SriovNetworkNodePolicy`. Know which prefix and which use case.
- The Multus annotation attaches an *interface*; the resource request attaches the *RDMA device*. Both are needed for RDMA on a secondary network. IPoIB CNI provides IP over InfiniBand, not RDMA by itself.
- GPUDirect RDMA in pods = Network Operator (OFED + RDMA device) + GPU Operator (`driver.rdma.enabled=true`, nvidia-peermem). Install order: Network Operator → GPU Operator when OFED comes from a container.
- `rdma system set netns exclusive` is a *host* setting applied before the device plugin starts; in `shared` mode pods see all or no RDMA devices.
- ib-kubernetes manages PKeys via UFM for *SR-IOV IB* only; irrelevant to RDMA shared device deployments.
- `IPC_LOCK` is required in nearly every RDMA pod; its absence shows as memory-registration failures, not a scheduling error.
- `rdmaHcaMax` caps how many pods can share one HCA; a surprisingly small Allocatable value is usually this setting.
- OpenShift: OperatorHub install, separate SR-IOV Network Operator, built-in Multus (do not deploy it from the chart), MachineConfig for kernel args/hugepages.
- Driver version mismatch with the kernel is the most common bring-up failure; always check the DOCA-OFED/kernel support matrix.
- Verify ladder: `kubectl get nicclusterpolicy` → node Allocatable → `ibstat` in pod → `ib_write_bw` pod-to-pod → nccl-tests MPIJob.

## Appendix C — Knowledge check

1. A pod requests `rdma/rdma_shared_device_a: 1` and has the annotation `k8s.v1.cni.cncf.io/networks: ipoib-net`. `ip addr` inside the pod shows `net1` with an address, but `/dev/infiniband` is empty and `ibstat` finds no devices. Which setting is most likely wrong?  
   A. The IPoIB NAD's `master` interface  
   B. The host RDMA subsystem netns mode (should be `exclusive`)  
   C. The Multus version  
   D. The pod's `IPC_LOCK` capability

2. Which pair of components must both be present for a PyTorch pod to use GPUDirect RDMA?  
   A. GPU Operator with `driver.rdma.enabled=true` and Network Operator providing OFED plus an RDMA device resource  
   B. ib-kubernetes and NV-IPAM  
   C. Multus and Whereabouts  
   D. SR-IOV Network Operator and NFD

3. Two SR-IOV InfiniBand pods on different nodes receive VFs and IPs but cannot communicate; `ibstat` in each pod shows the port `Active`. What should you check first?  
   A. Hugepage allocation  
   B. That the NAD's `pkey` exists in the SM and ib-kubernetes is reaching UFM to add the VF GUIDs  
   C. The Helm chart version  
   D. The `rdmaHcaMax` value

4. Which resource name would a pod request to get an exclusive SR-IOV VF exposed by the SR-IOV device plugin configured with `resourcePrefix: nvidia.com` and `resourceName: hostdev`?  
   A. `rdma/hostdev`  
   B. `nvidia.com/hostdev`  
   C. `mellanox.com/hostdev`  
   D. `sriov/hostdev`

5. On OpenShift, which statement about deploying the Network Operator is correct?  
   A. Deploy Multus from the NVIDIA chart because OpenShift lacks it.  
   B. Install from OperatorHub, install the SR-IOV Network Operator separately, and use MachineConfig for kernel arguments and hugepages.  
   C. Helm is not supported; only `kubectl apply` of raw manifests works.  
   D. The OFED driver must be compiled manually on each node.

6. The `mofed-ubuntu22.04-ds` pods are in CrashLoopBackOff after a node OS update. The most likely cause is:  
   A. The RDMA shared device plugin selectors changed  
   B. The DOCA-OFED driver container does not support the new kernel version  
   C. NV-IPAM ran out of addresses  
   D. The `NicClusterPolicy` has no `secondaryNetwork` section

7. What does the `k8s.v1.cni.cncf.io/resourceName` annotation on a NetworkAttachmentDefinition do?  
   A. Sets the pod's hostname  
   B. Links the network to a device-plugin resource so the attached interface uses the device allocated to the pod  
   C. Requests hugepages  
   D. Selects the IPAM pool

8. A node shows `rdma/rdma_shared_device_a: 1` under Allocatable although it has 8 ConnectX-7 HCAs matching the selectors, and only one pod can be scheduled. Which setting explains this?  
   A. `rdmaHcaMax` left at 1  
   B. `numVfs` set to 1  
   C. `perNodeBlockSize` set to 1  
   D. `maxParallelUpgrades` set to 1

### Answer key
1. B — With the RDMA subsystem in `shared` netns mode the device plugin cannot isolate devices per pod namespace; `rdma system set netns exclusive` on the host is required.
2. A — GDR needs the OFED stack and an RDMA device in the pod (Network Operator) plus nvidia-peermem from the GPU Operator's RDMA-enabled driver.
3. B — Port state is fine, so the partition is the issue: PKey must exist in the SM and the VF GUIDs must be members, which ib-kubernetes programs via UFM.
4. B — Resource names are `<prefix>/<name>`; the SR-IOV plugin here uses `nvidia.com`.
5. B — OpenShift uses OLM/OperatorHub, its own Multus, the separate SR-IOV Network Operator, and MachineConfig for node tuning.
6. B — The OFED container must match the kernel; an OS update changes the kernel and breaks the module build/load.
7. B — The annotation ties the NAD to the extended resource so Multus attaches the specific allocated device.
8. A — `rdmaHcaMax` controls how many pods may share each RDMA device and determines the advertised quantity.

## References
- NVIDIA Network Operator Application Notes — Getting Started with Kubernetes — https://docs.nvidia.com/networking/display/kubernetes2440/getting-started-kubernetes.html (check the current release: https://networking-docs.nvidia.com/kubernetes/network-operator)
- NVIDIA Network Operator — Getting Started with Red Hat OpenShift — https://docs.nvidia.com/networking/display/kubernetes2440/getting-started-openshift.html
- NVIDIA Network Operator — Helm chart (NGC) — https://catalog.ngc.nvidia.com/orgs/nvidia/helm-charts/network-operator
- NVIDIA GPU Operator — GPUDirect RDMA and GPUDirect Storage — https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-rdma.html
- NVIDIA DGX BasePOD Deployment Guide featuring Red Hat Enterprise Linux / OpenShift — https://docs.nvidia.com/dgx-basepod/
- k8s-rdma-shared-dev-plugin — https://github.com/Mellanox/k8s-rdma-shared-dev-plugin
- ib-kubernetes — https://github.com/Mellanox/ib-kubernetes
- SR-IOV Network Operator — https://github.com/k8snetworkplumbingwg/sriov-network-operator
- Multus CNI and NetworkAttachmentDefinition spec — https://github.com/k8snetworkplumbingwg/multus-cni
- NVIDIA Kubernetes IPAM (nv-ipam) — https://github.com/Mellanox/nvidia-k8s-ipam
- Related modules: [1.3 NCCL and GPUDirect](../01-ai-datacenter-design/03-gpu-to-gpu-communication-nccl-gpudirect.md), [IB administration (PKey, SM, UFM)](../02-infiniband/), [RDMA fundamentals](../03-rdma/), [BlueField/DOCA](../05-bluefield-doca/)

---
## Solution guide — Hands-on assignment

### README — apply order

```text
0. kubectl -n nvidia-network-operator create secret generic ufm-secret \
     --from-literal=UFM_USERNAME=admin --from-literal=UFM_PASSWORD='***' \
     --from-literal=UFM_ADDRESS=ufm01.lab --from-literal=UFM_HTTP_SCHEMA=https --from-literal=UFM_PORT=443
   (Install the operator with Helm first, deployCR: false, sriovNetworkOperator.enabled: true — we supply the policy by hand.)
1. kubectl apply -f nicclusterpolicy.yaml          → wait: kubectl get nicclusterpolicy shows ready (mofed-* Running on both nodes)
2. kubectl apply -f sriov-node-policy.yaml         → wait: sriovnetworknodestates .status.syncStatus Succeeded; nodes may reboot
3. kubectl apply -f nad.yaml
4. kubectl apply -f perftest.yaml
5. ./verify.sh
```

### `nicclusterpolicy.yaml`

```yaml
apiVersion: mellanox.com/v1alpha1
kind: NicClusterPolicy
metadata:
  name: nic-cluster-policy                   # exactly one policy, conventional name
spec:
  nodeAffinity:                              # only nodes that actually have NVIDIA NICs
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: feature.node.kubernetes.io/pci-15b3.present
              operator: In
              values: ["true"]
  ofedDriver:                                # plain Ubuntu: no host OFED, so the container builds/loads it
    image: doca-driver
    repository: nvcr.io/nvidia/mellanox
    version: 25.04-0.6.1.0-2                 # verify against the DOCA-OFED/kernel support matrix
    forcePrecompiled: false
    upgradePolicy:
      autoUpgrade: true
      maxParallelUpgrades: 1
      drain:
        enable: true
        force: true
        timeoutSeconds: 300
  nicFeatureDiscovery:
    image: nic-feature-discovery
    repository: ghcr.io/mellanox
    version: v0.0.1                          # verify
  # NO rdmaSharedDevicePlugin: the tenant gets exclusive VFs, not a shared PF.
  # NO sriovDevicePlugin: the SR-IOV Network Operator (enabled in Helm values) deploys its own.
  secondaryNetwork:
    multus:
      image: multus-cni
      repository: ghcr.io/k8snetworkplumbingwg
      version: v4.1.0                        # verify
    cniPlugins:
      image: plugins
      repository: ghcr.io/k8snetworkplumbingwg
      version: v1.6.2-update.1               # verify
    ipamPlugin:                              # Whereabouts for cluster-wide IPs on the tenant network
      image: whereabouts
      repository: ghcr.io/k8snetworkplumbingwg
      version: v0.7.0                        # verify
  ibKubernetes:                              # programs VF GUIDs into PKey 0x8010 via UFM REST
    image: ib-kubernetes
    repository: ghcr.io/mellanox
    version: v1.1.0                          # verify
    periodicUpdateSeconds: 5
    pKeyGUIDPoolRangeStart: "02:00:00:00:00:00:00:00"
    pKeyGUIDPoolRangeEnd: "02:FF:FF:FF:FF:FF:FF:FF"
    ufmSecret: ufm-secret
```

*Verify criterion:* `kubectl apply --dry-run=client -f nicclusterpolicy.yaml` succeeds; `grep -E 'rdmaSharedDevicePlugin|sriovDevicePlugin' nicclusterpolicy.yaml` returns only the comment lines.

### `sriov-node-policy.yaml`

```yaml
apiVersion: sriovnetwork.openshift.io/v1
kind: SriovNetworkNodePolicy
metadata:
  name: tenant-ib-policy
  namespace: nvidia-network-operator         # the namespace the SR-IOV Network Operator watches (verify for your install)
spec:
  resourceName: tenant_ib                    # becomes nvidia.com/tenant_ib
  nodeSelector:
    feature.node.kubernetes.io/pci-15b3.present: "true"
  priority: 99
  numVfs: 8                                  # matches NUM_OF_VFS set in firmware
  nicSelector:
    vendor: "15b3"
    pfNames: ["ibs1f0"]                      # rail 0 PF only for this tenant
  linkType: ib                               # InfiniBand VFs, not Ethernet
  isRdma: true                               # expose /dev/infiniband for the VF
  deviceType: netdevice                      # kernel driver (not vfio) so the VF has an IPoIB netdev
```

*Verify:* `kubectl -n nvidia-network-operator get sriovnetworknodestates -o jsonpath='{.items[*].status.syncStatus}'` → `Succeeded Succeeded`; `kubectl describe node dgx01 | grep tenant_ib` → `nvidia.com/tenant_ib: 8`.

### `nad.yaml`

```yaml
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: tenant-ib-8010
  namespace: default
  annotations:
    k8s.v1.cni.cncf.io/resourceName: nvidia.com/tenant_ib   # bind this network to the VF resource
spec:
  config: |
    {
      "cniVersion": "0.3.1",
      "name": "tenant-ib-8010",
      "type": "ib-sriov",
      "pkey": "0x8010",
      "link_state": "enable",
      "ibKubernetesEnabled": true,
      "ipam": {
        "type": "whereabouts",
        "range": "10.80.10.0/24",
        "exclude": ["10.80.10.0/32", "10.80.10.255/32"]
      }
    }
```

### `perftest.yaml`

```yaml
# Server pod: fixed IP via the JSON networks annotation so the client can find it without service discovery.
apiVersion: v1
kind: Pod
metadata:
  name: perftest-server
  labels: { app: perftest, role: server }
  annotations:
    k8s.v1.cni.cncf.io/networks: '[{"name":"tenant-ib-8010","ips":["10.80.10.10/24"]}]'   # Whereabouts honors a static ip request
spec:
  restartPolicy: Never
  containers:
    - name: perftest
      image: nvcr.io/nvidia/doca/doca:2.9.1-devel          # rdma-core + perftest; verify tag
      command: ["ib_write_bw", "-d", "mlx5_0", "-a", "--report_gbits", "-F"]   # server: waits for one client, prints table, exits
      securityContext:
        capabilities:
          add: ["IPC_LOCK"]
      resources:
        requests: { nvidia.com/tenant_ib: 1 }
        limits:   { nvidia.com/tenant_ib: 1 }
---
# Client pod: anti-affinity forces a different node than the server.
apiVersion: v1
kind: Pod
metadata:
  name: perftest-client
  labels: { app: perftest, role: client }
  annotations:
    k8s.v1.cni.cncf.io/networks: tenant-ib-8010
spec:
  restartPolicy: Never
  affinity:
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        - labelSelector:
            matchLabels: { app: perftest, role: server }
          topologyKey: kubernetes.io/hostname              # never co-locate with the server
  containers:
    - name: perftest
      image: nvcr.io/nvidia/doca/doca:2.9.1-devel
      command: ["sh", "-c", "sleep 15 && ib_write_bw -d mlx5_0 -a --report_gbits -F 10.80.10.10"]   # give the server time to listen
      securityContext:
        capabilities:
          add: ["IPC_LOCK"]
      resources:
        requests: { nvidia.com/tenant_ib: 1 }
        limits:   { nvidia.com/tenant_ib: 1 }
```

### `verify.sh`

```bash
#!/usr/bin/env bash
# verify.sh — acceptance checks for the tenant PKey 0x8010 deployment.
set -u
NS=nvidia-network-operator
ok(){ echo "PASS  $*"; } ; ko(){ echo "FAIL  $*"; }

# 1. Policy ready.
S=$(kubectl get nicclusterpolicy nic-cluster-policy -o jsonpath='{.status.state}')
[ "$S" = "ready" ] && ok "NicClusterPolicy ready" || ko "NicClusterPolicy state=$S"

# 2. Allocatable VFs on every NIC node.
for n in $(kubectl get nodes -l feature.node.kubernetes.io/pci-15b3.present=true -o name); do
  A=$(kubectl get $n -o jsonpath='{.status.allocatable.nvidia\.com/tenant_ib}')
  [ "$A" = "8" ] && ok "$n nvidia.com/tenant_ib=8" || ko "$n nvidia.com/tenant_ib=$A"
done

# 3. Pods running on distinct nodes.
N1=$(kubectl get pod perftest-server -o jsonpath='{.spec.nodeName}')
N2=$(kubectl get pod perftest-client -o jsonpath='{.spec.nodeName}')
[ -n "$N1" ] && [ -n "$N2" ] && [ "$N1" != "$N2" ] && ok "pods on $N1 and $N2" || ko "pods not on distinct nodes ($N1 / $N2)"

# 4. PKey inside each pod. The VF's first pkey table entry must be 0x8010 (full membership bit may show 0x8010; limited shows 0x0010).
for p in perftest-server perftest-client; do
  DEV=$(kubectl exec $p -- sh -c 'ls /sys/class/infiniband | head -1' 2>/dev/null)
  PK=$(kubectl exec $p -- cat /sys/class/infiniband/$DEV/ports/1/pkeys/0 2>/dev/null)
  [ "$PK" = "0x8010" ] && ok "$p $DEV pkey $PK" || ko "$p $DEV pkey=$PK (expected 0x8010)"
done

# 5. Bandwidth from the client log: last data row, average BW column (4th field) >= 380 Gb/s.
BW=$(kubectl logs perftest-client | awk '/^ *[0-9]+ +[0-9]+ +[0-9.]+ +[0-9.]+/{bw=$4} END{print bw}')
awk -v b="${BW:-0}" 'BEGIN{exit !(b>=380)}' && ok "ib_write_bw avg ${BW} Gb/s >= 380" || ko "ib_write_bw avg ${BW:-none} Gb/s < 380"
```

Expected output:

```text
PASS  NicClusterPolicy ready
PASS  node/dgx01 nvidia.com/tenant_ib=8
PASS  node/dgx02 nvidia.com/tenant_ib=8
PASS  pods on dgx01 and dgx02
PASS  perftest-server mlx5_0 pkey 0x8010
PASS  perftest-client mlx5_0 pkey 0x8010
PASS  ib_write_bw avg 390.95 Gb/s >= 380
```

### How each acceptance criterion is verified

| Criterion | Verification |
|---|---|
| YAML parses; policy has required sections and no device-plugin keys | `kubectl apply --dry-run=client -f .` exits 0; `grep -cE '^  (rdmaSharedDevicePlugin|sriovDevicePlugin):' nicclusterpolicy.yaml` → 0 |
| `SriovNetworkNodePolicy` fields | `kubectl get sriovnetworknodepolicy tenant-ib-policy -n nvidia-network-operator -o yaml` shows `numVfs: 8`, `linkType: ib`, `isRdma: true`, `resourceName: tenant_ib`, `pfNames: [ibs1f0]` |
| NAD annotation, type, pkey, ib-kubernetes | `kubectl get net-attach-def tenant-ib-8010 -o jsonpath='{.metadata.annotations}{.spec.config}'` contains `nvidia.com/tenant_ib`, `"ib-sriov"`, `"0x8010"`, `"ibKubernetesEnabled": true` |
| Pods request VF, annotation, IPC_LOCK, distinct nodes | `verify.sh` check 3; `kubectl get pod perftest-client -o jsonpath='{.spec.containers[0].securityContext}'` shows `IPC_LOCK` |
| PKey read from sysfs inside pods | `verify.sh` check 4 reads `/sys/class/infiniband/<dev>/ports/1/pkeys/0` |
| Bandwidth parsed from client log | `verify.sh` check 5 parses the last data row's average Gb/s |
| README order | Secret → policy → ready → node policy → Succeeded → NAD → perftest, as listed |

If check 4 fails with `0x7fff`, ib-kubernetes has not programmed the GUIDs: inspect `kubectl -n $NS logs deploy/ib-kubernetes` and confirm PKey `0x8010` exists in UFM (Section 4.3). If check 5 hangs, the two VFs are in different partitions or the server's static IP was not honored; `kubectl describe pod perftest-server | grep -A4 network-status` shows the IP Whereabouts actually assigned.
