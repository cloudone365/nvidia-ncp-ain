# Lab 6 — NVIDIA Network Operator on Kubernetes

## Objective
Install the NVIDIA **Network Operator** with Helm, deploy a `NicClusterPolicy` that brings up the OFED/DOCA driver container, the RDMA shared device plugin and (where hardware allows) SR-IOV, create a secondary network with Multus/macvlan, run a pod that requests an RDMA resource, and verify RDMA from inside the pod with `ibv_devinfo` and `ib_write_bw`.

## Environment
| Tier | What you need | What works |
|---|---|---|
| **Mechanics only** | Any Kubernetes 1.28+ cluster, including `kind` or `minikube`, Helm 3 | Operator install, CRDs, `NicClusterPolicy` object, status conditions, Multus and NetworkAttachmentDefinition objects. Driver/device-plugin pods will stay Pending or report no devices — expected |
| **Full** | 2 worker nodes with ConnectX-6/7 (IB or RoCE), Ubuntu 22.04, containerd; GPU optional | RDMA shared device plugin exposes `rdma/rdma_shared_device_a`; pods run perftest between nodes; SR-IOV VFs with `SRIOV_EN` from Module 8.3 |

Honesty: the operator itself is simple to install anywhere; the *value* (RDMA in pods) needs real NICs. Do the mechanics tier even if that is all you have — the exam's Kubernetes domain is 5% and mostly about objects and roles.

## Prerequisites
- `kubectl` and `helm` configured against the cluster; cluster-admin.
- Module `../07-kubernetes/`.
- Full tier: on each worker, `lspci | grep -i mellanox`, no host-installed MLNX_OFED if you want the operator's driver container to manage drivers (or set `ofedDriver` disabled and keep the host driver — both are supported; pick one). For SR-IOV: `mlxconfig -d <dev> q | grep -E 'SRIOV_EN|NUM_OF_VFS'` set to `True`/≥8 and firmware reset (Module 8.3).

## Tasks

### Task 1 — Install the operator

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia && helm repo update
helm search repo nvidia/network-operator --versions | head -5
kubectl create namespace nvidia-network-operator
helm install network-operator nvidia/network-operator \
  -n nvidia-network-operator \
  --version <latest-from-search> \
  --set nfd.enabled=true \
  --set sriovNetworkOperator.enabled=true \
  --wait
kubectl -n nvidia-network-operator get pods
kubectl get crd | grep -E 'mellanox|nvidia|k8s.cni'
```

Expected: `network-operator-...` controller pod Running, Node Feature Discovery (NFD) pods, SR-IOV network operator pods, and CRDs including `nicclusterpolicies.mellanox.com`, `hostdevicenetworks.mellanox.com`, `macvlannetworks.mellanox.com`, `ipoibnetworks.mellanox.com`, `network-attachment-definitions.k8s.cni.cncf.io`. NFD labels nodes that have ConnectX devices: `kubectl get nodes -o json | jq '.items[].metadata.labels | with_entries(select(.key|test("feature.node.kubernetes.io/pci-15b3")))'` (15b3 is the NVIDIA/Mellanox PCI vendor ID).

### Task 2 — NicClusterPolicy

```yaml
# ncp.yaml
apiVersion: mellanox.com/v1alpha1
kind: NicClusterPolicy
metadata:
  name: nic-cluster-policy
spec:
  ofedDriver:
    image: doca-driver
    repository: nvcr.io/nvidia/mellanox
    version: <doca-driver-tag>          # verify a tag matching your kernel/OS in current docs
    upgradePolicy:
      autoUpgrade: true
      maxParallelUpgrades: 1
  rdmaSharedDevicePlugin:
    image: k8s-rdma-shared-dev-plugin
    repository: ghcr.io/mellanox
    version: <tag>
    config: |
      {
        "configList": [{
          "resourceName": "rdma_shared_device_a",
          "rdmaHcaMax": 63,
          "selectors": { "vendors": ["15b3"], "ifNames": ["ens2f0np0"] }
        }]
      }
  secondaryNetwork:
    cniPlugins:
      image: plugins
      repository: ghcr.io/k8snetworkplumbingwg
      version: <tag>
    multus:
      image: multus-cni
      repository: ghcr.io/k8snetworkplumbingwg
      version: <tag>
    ipamPlugin:
      image: whereabouts
      repository: ghcr.io/k8snetworkplumbingwg
      version: <tag>
```

Replace `ifNames` with your NIC netdev. Image tags move fast; copy the current ones from the Network Operator release notes rather than guessing.

```bash
kubectl apply -f ncp.yaml
kubectl get nicclusterpolicy nic-cluster-policy -o jsonpath='{.status.state}{"\n"}'
kubectl -n nvidia-network-operator get pods -o wide
kubectl describe nicclusterpolicy nic-cluster-policy | sed -n '/Status/,$p'
```

Expected on the full tier: state `ready`; `mofed-ubuntu22.04-ds-*` (driver) pods Running on NIC nodes after several minutes (first boot compiles/loads the driver), `rdma-shared-dp-ds-*`, `kube-multus-ds-*`, `whereabouts-*`, `cni-plugins-ds-*` Running. On the mechanics tier the driver and device-plugin DaemonSets have 0 desired pods (no nodes match the NFD label) and the policy may sit in `notReady` — read the status conditions to see exactly why.

Confirm the extended resource:

```bash
kubectl get node <nic-node> -o jsonpath='{.status.allocatable}' | jq
```

Expected key `rdma/rdma_shared_device_a: "63"`.

### Task 3 — Secondary network (macvlan on the RDMA NIC)

```yaml
# macvlan.yaml
apiVersion: mellanox.com/v1alpha1
kind: MacvlanNetwork
metadata:
  name: rdma-net
spec:
  networkNamespace: default
  master: ens2f0np0
  mode: bridge
  mtu: 9000
  ipam: |
    { "type": "whereabouts", "range": "192.168.200.0/24", "exclude": ["192.168.200.1/32"] }
```

```bash
kubectl apply -f macvlan.yaml
kubectl get network-attachment-definitions.k8s.cni.cncf.io -A
kubectl get macvlannetwork rdma-net -o yaml | sed -n '/status/,$p'
```

Expected: the operator renders a `NetworkAttachmentDefinition` named `rdma-net` in `default`; status `ready`. (For InfiniBand NICs use `IPoIBNetwork` instead; for VF-per-pod use `HostDeviceNetwork` / SR-IOV `SriovNetwork` — see the module.)

### Task 4 — RDMA test pods

```yaml
# perftest-pods.yaml
apiVersion: v1
kind: Pod
metadata:
  name: rdma-server
  annotations:
    k8s.v1.cni.cncf.io/networks: rdma-net
spec:
  nodeSelector: {kubernetes.io/hostname: <node-a>}
  containers:
  - name: perftest
    image: nvcr.io/nvidia/doca/doca:<tag>        # or any image with rdma-core + perftest (verify tag)
    command: ["sleep","infinity"]
    securityContext: {capabilities: {add: ["IPC_LOCK"]}}
    resources:
      limits: {rdma/rdma_shared_device_a: 1}
---
apiVersion: v1
kind: Pod
metadata:
  name: rdma-client
  annotations:
    k8s.v1.cni.cncf.io/networks: rdma-net
spec:
  nodeSelector: {kubernetes.io/hostname: <node-b>}
  containers:
  - name: perftest
    image: nvcr.io/nvidia/doca/doca:<tag>
    command: ["sleep","infinity"]
    securityContext: {capabilities: {add: ["IPC_LOCK"]}}
    resources:
      limits: {rdma/rdma_shared_device_a: 1}
```

```bash
kubectl apply -f perftest-pods.yaml
kubectl get pods -o wide
kubectl exec rdma-server -- ip -br addr                 # eth0 (pod net) + net1 (192.168.200.x)
kubectl exec rdma-server -- ibv_devices
kubectl exec rdma-server -- ibv_devinfo -d mlx5_0 | grep -E 'state|link_layer'
# server
kubectl exec rdma-server -- ib_write_bw -d mlx5_0 -F --report_gbits -R -D 10 &
# client (use server's net1 IP)
kubectl exec rdma-client -- ib_write_bw -d mlx5_0 -F --report_gbits -R -D 10 192.168.200.<server>
```

Expected: `ibv_devices` lists the HCA inside the pod (shared device plugin mounts `/dev/infiniband/*`), `PORT_ACTIVE`, and `ib_write_bw` approaches the NIC rate — the same table as Lab 3. `IPC_LOCK` is needed for memory registration; forgetting it produces `Couldn't allocate MR` errors — a classic.

### Task 5 — Inspect what the operator did on the node

```bash
kubectl -n nvidia-network-operator logs ds/rdma-shared-dp-ds --tail=50
kubectl -n nvidia-network-operator exec ds/mofed-ubuntu22.04-ds -- ofed_info -s     # name varies
ssh <node-a> 'ls /dev/infiniband; cat /etc/cni/net.d/00-multus.conf | head; ls /opt/cni/bin'
```

Relate each pod to a job: driver container (loads mlx5_core/ib_* from a container), device plugin (advertises `rdma/...` resources and mounts devices), Multus (attaches `net1`), whereabouts (IPAM), CNI plugins (macvlan binary).

## Verification checklist
- [ ] Operator pod Running; CRDs present; NFD label `feature.node.kubernetes.io/pci-15b3.present=true` on NIC nodes.
- [ ] `NicClusterPolicy` status `ready` (full tier) or you can explain each `notReady` condition (mechanics tier).
- [ ] Node allocatable shows `rdma/rdma_shared_device_a`.
- [ ] `MacvlanNetwork` rendered a `NetworkAttachmentDefinition`.
- [ ] Pods have `net1` with a whereabouts IP; `ibv_devinfo` inside the pod shows `PORT_ACTIVE`.
- [ ] `ib_write_bw` between pods on different nodes approaches line rate.

## Break/fix challenge
1. **Missing IPC_LOCK.** Remove the capability from the client pod. Symptom: `ib_write_bw` fails registering memory. Fix: add it back (or raise `ulimit -l` via the container runtime).
2. **Wrong selector.** Change `ifNames` in the device plugin config to a non-existent netdev. Symptom: allocatable `rdma/...` becomes 0, pods Pending with `Insufficient rdma/rdma_shared_device_a`. Diagnose with `kubectl describe pod` and the device plugin logs.
3. **Driver/kernel mismatch.** Point `ofedDriver.version` to a tag for a different OS. Symptom: driver pod CrashLoopBackOff; `kubectl logs` shows the build/load failure; `NicClusterPolicy` `notReady`. Fix the tag (verify in release notes).
4. **RoCE class [HW with Lab 1 switches].** Run the perftest with and without `--tclass 106` while a second client floods the server; compare drops with `netq show wjh-drop buffer` (Lab 5). In pods, NCCL would use `NCCL_IB_TC`; the default device-plugin config does not set it for you.
5. **PKey / IPoIB [IB hardware].** Use `IPoIBNetwork` with a `pkey` the SM has not provisioned (Lab 2). Symptom: `net1` comes up but no IP connectivity between pods. Diagnose with `saquery -p` on the host.

## Cleanup
```bash
kubectl delete -f perftest-pods.yaml -f macvlan.yaml -f ncp.yaml
helm uninstall network-operator -n nvidia-network-operator
kubectl delete ns nvidia-network-operator
# Hosts: driver container unload happens with the DaemonSet; reboot a node if you want the host OFED back cleanly.
```
