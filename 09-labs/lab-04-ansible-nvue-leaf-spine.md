# Lab 4 — Automating the Leaf-Spine with Ansible and NVUE

## Objective
Re-create the Lab 1 configuration from an Ansible playbook, using the NVUE REST API via the `nvidia.nvue` collection (preferred) and the `nv` CLI via `ansible.builtin.command` as the fallback, with idempotent runs, a `nv config diff` dry-run pattern, and templated per-switch variables.

## Environment
NVIDIA Air, same topology as Lab 1 (fully simulated). Ansible runs from the `oob-mgmt-server` node in the simulation (or from your laptop via the Air SSH service). Cumulus 5.x switches expose the NVUE REST API on TCP 8765 over HTTPS; verify it is enabled (`nv show system api`).

## Prerequisites
- Lab 1 completed at least once (so you know what the result should look like). Optionally reset the switches to a clean state: `nv config apply empty -y` wipes the NVUE config (keep the management interface! — on Air the `eth0`/mgmt VRF is default, but confirm before applying).
- On the control node: `python3 -m pip install ansible` (ansible-core 2.14+), then `ansible-galaxy collection install nvidia.nvue`.
- Module `../06-automation/`.
- SSH key or password access from the control node to `leaf01`, `leaf02`, `spine01`, `spine02` as user `cumulus`.

## Tasks

### Task 1 — Inventory and group variables

```ini
# inventory.ini
[spines]
spine01 loopback=10.10.10.101/32 bgp_asn=65100 router_id=10.10.10.101
spine02 loopback=10.10.10.102/32 bgp_asn=65100 router_id=10.10.10.102

[leaves]
leaf01 loopback=10.10.10.1/32 bgp_asn=65101 router_id=10.10.10.1 server_if=swp1 server_ip=10.1.1.1/24
leaf02 loopback=10.10.10.2/32 bgp_asn=65102 router_id=10.10.10.2 server_if=swp1 server_ip=10.1.2.1/24

[cumulus:children]
spines
leaves

[cumulus:vars]
ansible_user=cumulus
ansible_python_interpreter=/usr/bin/python3
```

```yaml
# group_vars/spines.yml
fabric_ifaces: [swp1, swp2]
# group_vars/leaves.yml
fabric_ifaces: [swp51, swp52]
```

`ansible -i inventory.ini cumulus -m ping` must return `pong` from all four.

### Task 2 — Playbook with the nvidia.nvue collection (REST API)

The collection talks to the NVUE API, so it runs *from the control node* (`connection: local`) and needs the API credentials (same as the CLI user). Module names and argument shapes below follow the collection's documented pattern; **verify exact module/argument names against the installed collection** (`ansible-doc -l | grep nvue`).

```yaml
# fabric.yml
- name: Configure Cumulus leaf-spine via NVUE API
  hosts: cumulus
  connection: local
  gather_facts: false
  vars:
    nvue_url: "https://{{ ansible_host | default(inventory_hostname) }}:8765/nvue_v1"
    nvue_user: cumulus
    nvue_pass: "{{ lookup('env', 'NVUE_PASS') }}"
  tasks:
    - name: Create a new revision
      nvidia.nvue.config:
        state: new
        provider: {url: "{{ nvue_url }}", username: "{{ nvue_user }}", password: "{{ nvue_pass }}", validate_certs: false}
      register: rev

    - name: Hostname and loopback
      nvidia.nvue.system:
        state: merged
        revid: "{{ rev.revid }}"
        data: {hostname: "{{ inventory_hostname }}"}
        provider: {url: "{{ nvue_url }}", username: "{{ nvue_user }}", password: "{{ nvue_pass }}", validate_certs: false}

    - name: Interfaces (loopback, fabric, server-facing)
      nvidia.nvue.interface:
        state: merged
        revid: "{{ rev.revid }}"
        data:
          - id: lo
            ip: {address: {"{{ loopback }}": {}}}
          - id: "{{ fabric_ifaces | join(',') }}"
            link: {state: {up: {}}}
        provider: {url: "{{ nvue_url }}", username: "{{ nvue_user }}", password: "{{ nvue_pass }}", validate_certs: false}

    - name: BGP
      nvidia.nvue.router:
        state: merged
        revid: "{{ rev.revid }}"
        data:
          bgp: {autonomous-system: "{{ bgp_asn }}", router-id: "{{ router_id }}"}
        provider: {url: "{{ nvue_url }}", username: "{{ nvue_user }}", password: "{{ nvue_pass }}", validate_certs: false}

    - name: Apply revision
      nvidia.nvue.config:
        state: apply
        revid: "{{ rev.revid }}"
        provider: {url: "{{ nvue_url }}", username: "{{ nvue_user }}", password: "{{ nvue_pass }}", validate_certs: false}
```

Run: `NVUE_PASS=yourpass ansible-playbook -i inventory.ini fabric.yml`. The key NVUE API concept the exam tests: **create a revision → PATCH objects into it → apply the revision** (`nv config apply` in CLI terms). Inspect what the collection sent with `-vvv`.

### Task 3 — CLI fallback: rendered `nv set` commands, idempotent

When the collection does not cover an object (QoS, for instance, in some versions), render commands and run them. Idempotence trick: compute the diff first and only apply when it is non-empty.

```yaml
# roce.yml
- name: RoCE lossless profile on leaves via nv CLI
  hosts: leaves
  gather_facts: false
  tasks:
    - name: Stage RoCE and BGP neighbor commands
      ansible.builtin.command: "nv set {{ item }}"
      loop:
        - "qos roce mode lossless"
        - "interface {{ server_if }} ip address {{ server_ip }}"
        - "vrf default router bgp peer-group underlay remote-as external"
        - "vrf default router bgp neighbor {{ fabric_ifaces[0] }} peer-group underlay"
        - "vrf default router bgp neighbor {{ fabric_ifaces[1] }} peer-group underlay"
        - "vrf default router bgp address-family ipv4-unicast redistribute connected"
      changed_when: false

    - name: Show pending diff
      ansible.builtin.command: nv config diff
      register: diff
      changed_when: false

    - name: Apply only if something changed
      ansible.builtin.command: nv config apply -y
      when: diff.stdout | trim | length > 0

    - name: Save
      ansible.builtin.command: nv config save
      when: diff.stdout | trim | length > 0
      changed_when: true
```

Run twice. The second run must report `changed=0` for the apply/save tasks — that is your idempotence proof. (If a pending revision lingers from a failed run, `nv config detach` clears it.)

### Task 4 — Check mode / dry run

Add `--check` support: use `nv config diff` output as the "would change" report and skip apply when `ansible_check_mode` is true:

```yaml
    - name: Apply only if something changed
      ansible.builtin.command: nv config apply -y
      when: (diff.stdout | trim | length > 0) and not ansible_check_mode
```

`ansible-playbook -i inventory.ini roce.yml --check --diff` then prints the pending NVUE diff without applying.

### Task 5 — Verify with Ansible

```yaml
# verify.yml
- hosts: leaves
  gather_facts: false
  tasks:
    - ansible.builtin.command: nv show vrf default router bgp neighbor -o json
      register: bgp
      changed_when: false
    - ansible.builtin.assert:
        that: "(bgp.stdout | from_json).values() | map(attribute='state') | select('equalto','established') | list | length == 2"
        fail_msg: "Not all BGP neighbors established on {{ inventory_hostname }}"
    - ansible.builtin.command: nv show qos roce -o json
      register: roce
      changed_when: false
    - ansible.builtin.assert:
        that: "(roce.stdout | from_json).mode == 'lossless'"
```

`-o json` on `nv show` is what makes NVUE automatable; the JSON key names come from the NVUE object model (verify the exact path for `state` and `mode` in your release by running the command by hand first).

## Verification checklist
- [ ] `ansible cumulus -m ping` returns pong from all four switches.
- [ ] `fabric.yml` (API) applies a revision; `nv config history` on a switch shows an entry from the API user.
- [ ] `roce.yml` second run: `changed=0`.
- [ ] `--check --diff` prints the NVUE diff without changing the switch.
- [ ] `verify.yml` asserts pass; BGP Established on both leaves; `nv show qos roce` lossless.

## Break/fix challenge
1. **Drift.** Someone runs `nv set qos roce mode lossy ; nv config apply -y` on leaf02 by hand. Run `verify.yml` — it must fail on leaf02. Run `roce.yml` — it must show `changed=1` for leaf02 only and restore lossless. This is the configuration-management argument in one playbook.
2. **Stale revision.** Run `nv set interface swp3 description oops` on leaf01 without applying, then run `roce.yml`. Observe the diff includes the stray change (NVUE revisions are per-user and pending). Fix: `nv config detach` as a pre-task, or always apply a named revision via the API.
3. **API disabled.** `nv set system api state disabled ; nv config apply -y` on spine01 (verify command). `fabric.yml` fails for spine01 with a connection error; the CLI playbooks still work. Re-enable and discuss why both paths are worth having.

## Cleanup
Leave the fabric configured for Lab 5. To reset fully: `nv config apply empty -y && nv config save` on each switch (confirm management access is not NVUE-managed first).
