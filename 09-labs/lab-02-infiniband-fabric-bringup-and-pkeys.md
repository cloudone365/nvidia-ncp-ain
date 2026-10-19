# Lab 2 — InfiniBand Fabric Bring-up, Subnet Manager and PKeys

## Objective
Bring an InfiniBand link from `Down` to `Active`, understand what the **SM** contributes (**LID** assignment, routing), inventory the fabric with `ibstat`/`ibhosts`/`iblinkinfo`, verify reachability with **ibping**, run **ibdiagnet**, and create a non-default **PKey** partition with IPoIB on it.

## Environment
InfiniBand is not simulated by NVIDIA Air or any mainstream simulator. Choose one:

| Option | What you get | Limits |
|---|---|---|
| **A. Two hosts, ConnectX cards back-to-back** (one cable, no switch), `opensm` on host A | Everything about port states, SM, LIDs, PKeys, ibping, perftest. Cheapest IB lab that exists | `ibswitches`/`iblinkinfo` show no switch; `ibdiagnet -r` routing checks are trivial |
| **B. Two hosts + one Quantum/QM87xx/QM97xx switch (managed)** | Adds switch-side SM, iblinkinfo per-link view, ibroute/ibtracert, switch CLI | Hardware |
| **C. UFM demo / vendor lab** | UFM GUI, fabric health, PKey management via UFM | Access dependent |

Commands below are written for Option A/B on Linux hosts with MLNX_OFED or DOCA-Host installed (`ofed_info -s`). The cards must be in IB mode (`mlxconfig -d <dev> q | grep LINK_TYPE` → `IB(1)`; see Module 8.3 to change it).

## Prerequisites
- Modules `../02-infiniband/` and `08-troubleshooting/01-infiniband-diagnostics-and-perftest.md`.
- Root on both hosts; `infiniband-diags`, `opensm`, `perftest` installed (`dpkg -l | grep -E 'infiniband-diags|opensm|perftest'` or `rpm -qa`).
- Host names `hostA` (will run the SM) and `hostB`.

## Tasks

### Task 1 — Observe the link without an SM

Ensure no SM is running anywhere (`sudo systemctl stop opensm` on both; on a managed switch `no ib sm` or UFM stopped). Then:

```bash
sudo ibstat
```

Expected: `State: Initializing`, `Physical state: LinkUp`, `Base lid: 0`, `SM lid: 0`. The cable and both ports are fine; without an SM nothing progresses. Try `sudo sminfo` — it times out. Try `sudo ibhosts` — also fails or times out (MAD tools need an Active port).

### Task 2 — Start the Subnet Manager

On hostA:

```bash
sudo systemctl enable --now opensm
sudo journalctl -u opensm -n 20 --no-pager
sudo ibstat | grep -E 'State|lid'
```

Expected within a few seconds on both hosts: `State: Active`, `Base lid: 1` and `2` (or similar), `SM lid: 1`. `sudo sminfo` now returns `... state 3 SMINFO_MASTER`. Log lines include `SUBNET UP`.

Record which host holds which LID (`ibstat` decimal; `ibstatus` hex).

### Task 3 — Inventory and link view

```bash
sudo ibhosts
sudo ibswitches            # empty for Option A
sudo ibnodes
sudo iblinkinfo
```

Expected (Option A):

```text
CA: hostA mlx5_0:
      0x946dae0300a1b2c0      1    1[  ] ==( 4X  106.25 Gbps Active/  LinkUp)==>       2    1[  ] "hostB mlx5_0" ( )
```

Check the width and lane speed match the cards and cable (`4X 106.25 Gbps` = NDR; `4X 53.125 Gbps` = HDR; `4X 25.78125 Gbps` = EDR). If you see `Could be …`, you already have a Break/fix case.

### Task 4 — ibping

```bash
# hostB
sudo ibping -S
# hostA   (use hostB's LID)
sudo ibping -L 2 -c 5
sudo ibping -G 0x<hostB port GUID> -c 5
```

Expected: five `Pong from hostB ... time 0.0xx ms` lines. Stop the server with Ctrl-C and repeat `ibping -L 2` — note the timeout: no responder, no pong.

### Task 5 — Counters and ibdiagnet baseline

```bash
sudo perfquery -x            # local port, 64-bit counters
sudo ibdiagnet --pc --pm_pause_width=20 -P all=1 --extended_speeds all --get_cable_info
ls /var/tmp/ibdiagnet2/
tail -40 /var/tmp/ibdiagnet2/ibdiagnet2.log
grep -i -E 'vendor|PN|SN|length' /var/tmp/ibdiagnet2/ibdiagnet2.cables | head
```

Expected: summary table with 0 errors (warnings about "no switches" are normal in Option A). The `.cables` file shows the transceiver/DAC vendor, part number, serial and length.

### Task 6 — Default partition and IPoIB

```bash
cat /sys/class/net/ib0/pkey          # 0x7fff = default partition, full member
cat /sys/class/net/ib0/mode          # datagram or connected
sudo ip addr add 192.168.70.1/24 dev ib0 && sudo ip link set ib0 up     # hostA
sudo ip addr add 192.168.70.2/24 dev ib0 && sudo ip link set ib0 up     # hostB
ping -c 3 192.168.70.2
```

Expected: ping works. Compare `ping` to `ibping` — this one needed an IP, the PKey multicast group, ARP; `ibping` needed only LIDs.

### Task 7 — Create a non-default PKey

On hostA, edit `/etc/opensm/partitions.conf` (create it if absent; verify the path for your distribution — some use `/etc/rdma/opensm/partitions.conf`):

```text
Default=0x7fff, ipoib, rate=7, mtu=5 : ALL=full, SELF=full;
Tenant1=0x8001, ipoib, rate=7, mtu=5 : ALL=full;
```

`ipoib` tells the SM to create the IPoIB broadcast multicast group for that partition; `rate=7`/`mtu=5` set the group's rate/MTU (check the opensm partition file syntax for your version). Restart the SM: `sudo systemctl restart opensm`. Confirm the SM distributed the PKey:

```bash
sudo smpquery pkeys 2         # hostB's LID; expect 0x7fff and 0x8001 in the table
sudo saquery -p               # SA's partition records
```

Create child IPoIB interfaces on both hosts and address them:

```bash
echo 0x8001 | sudo tee /sys/class/net/ib0/create_child
ip link show ib0.8001
sudo ip addr add 192.168.81.1/24 dev ib0.8001 && sudo ip link set ib0.8001 up    # hostA (.2 on hostB)
ping -c 3 192.168.81.2
cat /sys/class/net/ib0.8001/pkey        # 0x8001
```

Expected: ping works on the new partition. Now demonstrate isolation: on hostA only, change `Tenant1` to `0x8002`, restart opensm, and try the ping again — `ib0.8001` on both hosts now has a PKey the SM no longer serves and traffic fails even though `ibping` between the LIDs still works.

## Verification checklist
- [ ] Without SM: `Initializing/LinkUp`, LID 0. With SM: `Active`, non-zero LID, SM LID identical on both hosts.
- [ ] `sminfo` shows `SMINFO_MASTER`.
- [ ] `iblinkinfo` shows expected width and lane speed, no `Could be`.
- [ ] `ibping -S` on one host, `ibping -L <lid>` from the other succeeds; without `-S`, it times out.
- [ ] `ibdiagnet` completes with 0 errors; `/var/tmp/ibdiagnet2/ibdiagnet2.log`, `.pm`, `.db_csv`, `.cables` present.
- [ ] IPoIB ping on default PKey and on 0x8001 child interface works; `smpquery pkeys` shows both PKeys on both ports.

## Break/fix challenge
1. **Wrong PKey.** Partner changes `Tenant1` to limited membership on one side (`: <hostB GUID>=limited;`) or removes hostB from the partition. Symptom: `ib0.8001` ping fails one way or both, default-partition ping and `ibping` still work. Diagnose with `smpquery pkeys <lid>` on both ports and `saquery -p`; a limited member cannot talk to another limited member. Fix the partitions file and restart opensm.
2. **SM gone.** Partner stops opensm. Symptom: ports fall back to `Initializing`?  No — ports *stay* Active for a while (LIDs persist until a port event), but `sminfo` fails and new ports will not join. Discuss why a fabric survives a short SM outage and what the SM lid in `ibstat` tells you.
3. **Degraded cable (if you have a spare, deliberately worn cable or an HDR cable on NDR cards).** Symptom: `iblinkinfo` shows `Could be 4X` or a lower lane speed; `perfquery` SymbolErrorCounter climbs. Confirm with `mlxlink -d <dev> -m -c` (Module 8.3), swap cable, re-verify.
4. **Mode mismatch.** Set `echo connected > /sys/class/net/ib0/mode` on one host only and set its MTU to 65520. Small pings work, `ping -s 8000` fails. Diagnose with `cat /sys/class/net/ib0/mode` on both.

## Cleanup
```bash
sudo ip link del ib0.8001 2>/dev/null || echo 0x8001 | sudo tee /sys/class/net/ib0/delete_child
sudo ip addr flush dev ib0
# restore /etc/opensm/partitions.conf to Default only, then:
sudo systemctl restart opensm      # or stop it if the hosts return to a switch-managed fabric
```
