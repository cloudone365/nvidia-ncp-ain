# Module 7.1 — NVIDIA Network Operator: RDMA and InfiniBand in Kubernetes

> **Exam domain:** Kubernetes Integration (5%)  
> **Source course(s):** AI Infrastructure and Operations Fundamentals (Kubernetes section); NVIDIA Network Operator training materials  
> **Source docs:** NVIDIA Network Operator Application Notes — Getting Started with Kubernetes; Getting Started with Red Hat OpenShift; DGX BasePOD Deployment Guide (RHEL/OpenShift); NVIDIA GPU Operator documentation (GPUDirect RDMA section); Kubernetes Network Plumbing Working Group Multus/NAD specification  
> **Estimated study time:** 3 hours

## Learning objectives
- Describe each component the **Network Operator** deploys and what problem each solves (driver container, RDMA shared device plugin, SR-IOV device plugin and SR-IOV Network Operator, Multus, IPoIB CNI, macvlan, NVIDIA IPAM, ib-kubernetes).
- Explain the relationship between the Network Operator and the GPU Operator and why GPUDirect RDMA in pods needs both.
- Install the Network Operator with Helm and author a `values.yaml` for an RDMA shared-device or SR-IOV deployment.
- Describe the `NicClusterPolicy` CRD, its sections, and how it drives the operator.
- Configure `NetworkAttachmentDefinition` objects for RDMA shared device (macvlan), SR-IOV InfiniBand, and IPoIB networks.
- Write a pod spec that requests an RDMA resource and attaches a secondary network with the Multus annotation.
- Verify a deployment end-to-end: operator pods, node resources, `ibstat` in a pod, **ib_write_bw** between pods, and an nccl-tests job.
- Troubleshoot common failures: driver/kernel mismatch, hugepages, **PKey** mismatch, RDMA netns mode, missing capabilities.

## 1. Why Kubernetes needs a Network Operator

Kubernetes assumes one network interface per pod, provisioned by a single CNI plugin, carrying ordinary TCP/IP. AI workloads need something different on every count:

- A pod must reach an **RDMA** device (`/dev/infiniband/uverbsN`) to use ibverbs, NCCL's IB transport, or GPUDirect RDMA.
- The compute fabric is usually a *second* network (InfiniBand or dedicated RoCE Ethernet), separate from the pod network, so pods need multiple interfaces.
- The NIC drivers (MOFED / DOCA-OFED) and firmware config (SR-IOV VF counts, link type) must be consistent across nodes, and in containers the kernel modules must match the host kernel.
- Resources that are not CPU/memory — "an RDMA-capable NIC", "a VF on rail 3" — must be scheduled.

The **NVIDIA Network Operator** uses the Kubernetes operator pattern (a controller watching custom resources) to deploy and lifecycle-manage all of these pieces consistently across the cluster. It is the networking counterpart of the **GPU Operator**, which does the same for the GPU driver, container toolkit, device plugin, DCGM and MIG.

## 2. What the Network Operator deploys

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

### 2.1 Relationship to the GPU Operator
GPUDirect RDMA inside a pod requires:
1. The NVIDIA GPU driver (GPU Operator, `driver` component) with the **nvidia-peermem** module loaded — enabled in GPU Operator values by `driver.rdma.enabled=true` (and `driver.rdma.useHostMofed=true` when OFED is on the host rather than in the Network Operator's container).
2. The OFED driver (host or Network Operator) that nvidia-peermem links against.
3. An RDMA device exposed to the pod (RDMA shared device plugin or SR-IOV).

Order matters: install the Network Operator (OFED) *first*, then the GPU Operator with `driver.rdma.enabled=true`, so the GPU driver container can find the OFED symbols when building nvidia-peermem. On DGX OS / BasePOD where both drivers are preinstalled on the host, both operators are typically deployed with `driver.enabled=false` / `ofedDriver.deploy=false` and only the device plugins and CNIs are used. See [Module 1.3](../01-ai-datacenter-design/03-gpu-to-gpu-communication-nccl-gpudirect.md) for the GPUDirect mechanics.

## 3. Installing with Helm

### 3.1 Prerequisites
- Kubernetes 1.2x cluster (check the compatibility matrix for the operator release); `kubectl` and Helm 3.
- Nodes with ConnectX-6/7/8 or BlueField-3 NICs; firmware supporting the desired mode; for SR-IOV, `SRIOV_EN=1` and `NUM_OF_VFS` set with **mlxconfig** and IOMMU enabled in the kernel (`intel_iommu=on iommu=pt` or AMD equivalent).
- For InfiniBand, a running Subnet Manager (**UFM** or opensm) and, for ib-kubernetes, UFM REST credentials.
- Hugepages configured on nodes if you will run DPDK or some RDMA workloads that request them.
- Node Feature Discovery (NFD) — the chart can deploy it.

### 3.2 Install

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

### 3.3 Example `values.yaml` — RDMA shared device (InfiniBand compute fabric, host OFED)

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

### 3.4 Example `values.yaml` — SR-IOV InfiniBand with ib-kubernetes

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

## 4. The NicClusterPolicy CRD

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

Watch the policy converge:
```bash
kubectl get nicclusterpolicy
# NAME                 STATUS   AGE
# nic-cluster-policy   ready    6m
kubectl describe nicclusterpolicy nic-cluster-policy   # per-component state in .status.appliedStates
```
A `notReady` status almost always means one DaemonSet (usually `mofed-*`) is not running on every targeted node.

## 5. NetworkAttachmentDefinitions

Multus attaches a secondary interface when a pod carries the annotation `k8s.v1.cni.cncf.io/networks: <nad-name>`. The NAD's `config` is a CNI JSON document. NVIDIA also provides higher-level CRs (`MacvlanNetwork`, `IPoIBNetwork`, `HostDeviceNetwork`) that the operator renders into NADs; the raw NAD forms are shown here because the exam may show either.

### 5.1 RDMA shared device over an Ethernet/RoCE PF (macvlan + NV-IPAM)

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
The `resourceName` annotation ties the network to the device-plugin resource so Multus and the scheduler agree on which device the pod gets.

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

### 5.2 SR-IOV InfiniBand (`ib-sriov` CNI, with PKey)

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

### 5.3 IPoIB (shared PF, one IPoIB child per pod)

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

## 6. Pod specs

### 6.1 Shared RDMA device + IPoIB

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

### 6.2 SR-IOV IB VF + GPU (GPUDirect RDMA)

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

## 7. Verification

```bash
# 1. Operator and components
kubectl -n nvidia-network-operator get pods -o wide
kubectl get nicclusterpolicy
kubectl -n nvidia-network-operator get ds

# 2. Node resources advertised
kubectl describe node dgx01 | grep -A12 Allocatable
#   rdma/rdma_shared_device_a:  63        <- RDMA shared device plugin
#   nvidia.com/hostdev:         8         <- SR-IOV device plugin
#   nvidia.com/gpu:             8         <- GPU Operator

# 3. Secondary network attached
kubectl get net-attach-def
kubectl exec rdma-test-1 -- ip -br addr          # expect net1 with an IPoIB/macvlan address
kubectl describe pod rdma-test-1 | grep -A3 'k8s.v1.cni.cncf.io/network-status'

# 4. RDMA device visible inside the pod
kubectl exec rdma-test-1 -- ls /dev/infiniband   # uverbs0 rdma_cm ...
kubectl exec rdma-test-1 -- ibstat               # Port state Active, Rate 400
kubectl exec rdma-test-1 -- ibv_devinfo

# 5. RDMA bandwidth between two pods on different nodes
kubectl exec rdma-test-1 -- ib_write_bw -d mlx5_0 -a --report_gbits -F
kubectl exec rdma-test-2 -- ib_write_bw -d mlx5_0 -a --report_gbits -F <net1-ip-of-rdma-test-1>
# expect ~390 Gb/s at large messages on NDR 400G

# 6. SR-IOV state (when using the SR-IOV Network Operator)
kubectl -n nvidia-network-operator get sriovnetworknodestates -o yaml | grep -A5 'ibs1f0'
```

### 7.1 nccl-tests as a Kubernetes job
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

## 8. OpenShift differences

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

## 9. Common failures

| Symptom | Cause | Fix |
|---|---|---|
| `mofed-*` pod CrashLoopBackOff; log shows module build failures or `Unknown symbol` | DOCA-OFED container version does not support the node kernel (new kernel after a node OS update), or precompiled image unavailable for that kernel | Pin the node kernel, pick a DOCA-OFED version from the support matrix, or set `forcePrecompiled: false` and provide build deps; on OpenShift ensure Driver Toolkit matches |
| `NicClusterPolicy` stays `notReady` | One component DaemonSet not scheduled on all targeted nodes (taints, nodeAffinity, image pull) | `kubectl -n nvidia-network-operator describe ds <name>`; check NGC pull secret |
| Pod `Pending`: `Insufficient rdma/rdma_shared_device_a` | Device plugin not running, selectors match no device (wrong `ifNames`/`deviceIDs`), or `rdmaHcaMax` exhausted | Check device plugin logs; `ls /sys/class/infiniband`; verify `ifNames` match host netdev names |
| Pod runs but `ibstat` shows nothing / `/dev/infiniband` empty | RDMA subsystem netns mode is `shared` while using SR-IOV or IPoIB isolation | Set exclusive mode on each node: `rdma system set netns exclusive` (persist via udev/systemd or MachineConfig; must be set before the device plugins start and typically requires no RDMA devices in use) |
| `ibv_reg_mr` / `Couldn't allocate MR` errors in the pod | Missing `IPC_LOCK` capability or `ulimit -l` too low | Add `capabilities.add: ["IPC_LOCK"]`; check memlock limits in the container runtime |
| DPDK or some RDMA apps fail with hugepage errors | No hugepages reserved on the node | Kernel args `default_hugepagesz=1G hugepagesz=1G hugepages=16` (MachineConfig on OpenShift); request `hugepages-1Gi` in the pod |
| SR-IOV IB pods come up but cannot ping each other / `ibping` fails | **PKey** in the NAD not configured in the SM, or ib-kubernetes cannot reach UFM, or `pkey` omitted so VFs land in different partitions | Verify `ufmSecret`, check ib-kubernetes logs, confirm PKey exists in UFM, confirm `ibKubernetesEnabled: true` |
| IPoIB pods have IP but no RDMA | IPoIB CNI gives IP connectivity only; the RDMA device still comes from the device plugin resource request | Add the `rdma/...` resource request and `resourceName` annotation |
| NCCL falls back to `NET/Socket` | `/dev/infiniband` not mounted (no RDMA resource), or `NCCL_IB_HCA` excludes everything, or peermem missing | Request the RDMA resource; check GPU Operator `driver.rdma.enabled`; `lsmod` on host for `nvidia_peermem` |
| GPUDirect RDMA disabled in NCCL log | GPU Operator installed before Network Operator; nvidia-peermem failed to build | Reinstall/restart GPU driver DaemonSet after OFED is ready; `useHostMofed` setting correct |
| VFs disappear after node reboot | `mlxconfig` set but SR-IOV Network Operator policy not applied, or `NUM_OF_VFS` not persisted | Confirm `mlxconfig -d <dev> q | grep -E 'SRIOV_EN|NUM_OF_VFS'`; confirm `SriovNetworkNodeState` is `Succeeded` |

## Configuration / command reference

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
```

## Common pitfalls & exam traps
- RDMA shared device plugin (`rdma/<name>`, many pods per HCA) vs SR-IOV device plugin (`nvidia.com/<name>`, one VF per pod) — know which prefix and which use case.
- The Multus annotation attaches an *interface*; the resource request attaches the *RDMA device*. Both are needed for RDMA on a secondary network.
- Installing the GPU Operator before the Network Operator (when OFED comes from a container) breaks nvidia-peermem; order is Network Operator → GPU Operator with `driver.rdma.enabled=true`.
- On OpenShift do not deploy Multus from the NVIDIA chart and install the SR-IOV Network Operator from OperatorHub; node tuning is done with MachineConfig.
- `rdma system set netns exclusive` is a *host* setting that must be applied before the device plugin starts; in `shared` mode pods see all or no RDMA devices depending on the plugin.
- IPoIB CNI provides IP over InfiniBand, not RDMA by itself.
- ib-kubernetes manages PKeys via UFM for *SR-IOV IB* only; it is irrelevant to RDMA shared device deployments.
- `IPC_LOCK` is required in nearly every RDMA pod; its absence shows as memory-registration failures, not as a scheduling error.
- `rdmaHcaMax` caps how many pods can share one HCA; a surprisingly small Allocatable value is usually this setting.
- Driver version mismatch with the kernel is the most common bring-up failure; always check the DOCA-OFED/kernel support matrix.

## Key takeaways
- The Network Operator deploys and manages OFED drivers, device plugins (RDMA shared, SR-IOV), Multus and CNIs (macvlan, IPoIB), IPAM (Whereabouts/NV-IPAM), ib-kubernetes and telemetry via one `NicClusterPolicy`.
- GPUDirect RDMA in pods = Network Operator (OFED + RDMA device) + GPU Operator (`driver.rdma.enabled=true`, nvidia-peermem).
- Helm: `helm repo add nvidia https://helm.ngc.nvidia.com/nvidia`; install to `nvidia-network-operator`; `deployCR: true` generates the policy.
- RDMA shared device plugin → `rdma/<resource>`, selectors by `vendors`, `deviceIDs`, `ifNames`, `linkTypes`.
- SR-IOV device plugin → `nvidia.com/<resource>`; VFs created by SR-IOV Network Operator `SriovNetworkNodePolicy`.
- NAD `type`: `macvlan` (RoCE/Ethernet), `ib-sriov` (IB VF with `pkey`), `ipoib` (IP over IB).
- Pod: resource request + `k8s.v1.cni.cncf.io/networks` annotation + `IPC_LOCK`.
- Verify: `kubectl get nicclusterpolicy` → node Allocatable → `ibstat` in pod → `ib_write_bw` pod-to-pod → nccl-tests MPIJob.
- OpenShift: OperatorHub install, separate SR-IOV Network Operator, built-in Multus, MachineConfig for kernel args/hugepages.
- Top failures: OFED/kernel mismatch, netns mode not `exclusive`, missing PKey in SM/NAD, missing hugepages, missing `IPC_LOCK`.

## Review questions

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
