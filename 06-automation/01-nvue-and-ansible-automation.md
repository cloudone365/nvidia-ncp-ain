# Module 6.1 — NVUE and Ansible Automation

> **Exam domain:** Automation and Configuration (10%)  
> **Source course(s):** Cumulus Linux Essentials / Administration (automation sections); NetQ Deployment (validation)  
> **Source docs:** NVUE CLI and Command Reference; NVUE Cheat Sheet; NVUE API (REST) documentation; Data Center Network Automation — Ansible Deployment Guide (NVUE Automation with Ansible); `nvidia.nvue` Ansible Collection; Cumulus Linux Upgrade Guide  
> **Estimated study time:** 5 hours

## Learning objectives
- Describe NVUE as a REST API and query/modify switch configuration with `curl` against `https://<switch>:8765/nvue_v1/`.
- Explain the NVUE YAML configuration structure and build Jinja2 templates for a Spectrum-X leaf.
- Apply full or partial configuration files with `nv config replace`, `nv config patch` and `nv config apply`.
- Build an Ansible inventory and group_vars for a Cumulus fabric and use the `nvidia.nvue` collection or generic modules to push configuration idempotently.
- Write playbooks for VLAN/SVI/bond rollout and for RoCE lossless + adaptive-routing rollout, including check/diff mode.
- Validate changes with NetQ (`netq check`) and automate upgrades (`nv action upgrade`, NetQ LCM).
- Describe git-based configuration management with CI pipelines and recognise common NVUE automation errors (apply timeouts, pending conflicts, revision locking).

## 1. NVUE as an API

Everything the `nv` CLI does is a call to the NVUE REST API served by `nvued` on TCP **8765** (HTTPS, self-signed certificate by default, basic auth with a Linux user). The CLI tree *is* the URL tree:

| CLI | REST |
|---|---|
| `nv show interface` | `GET /nvue_v1/interface` |
| `nv show interface swp1 link` | `GET /nvue_v1/interface/swp1/link` |
| `nv show qos roce` | `GET /nvue_v1/qos/roce` |
| `nv set interface swp1 link mtu 9216` | `PATCH /nvue_v1/interface/swp1/link` with `{"mtu": 9216}` to a revision |
| `nv config apply` | `PATCH /nvue_v1/revision/<rev>` with `{"state":"apply"}` |

```bash
# Read (operational + applied) state
curl -sk -u cumulus:'P@ss' https://leaf01:8765/nvue_v1/interface/swp1/link | jq .
curl -sk -u cumulus:'P@ss' "https://leaf01:8765/nvue_v1/qos/roce?rev=applied" | jq .
curl -sk -u cumulus:'P@ss' "https://leaf01:8765/nvue_v1/vrf/default/router/bgp/neighbor?rev=operational" | jq .

# Write: 1) create a revision (pending changeset), 2) PATCH into it, 3) apply it
REV=$(curl -sk -u cumulus:'P@ss' -X POST https://leaf01:8765/nvue_v1/revision | jq -r 'keys[0]')
curl -sk -u cumulus:'P@ss' -X PATCH -H 'Content-Type: application/json' \
  "https://leaf01:8765/nvue_v1/interface/swp1/link?rev=${REV}" -d '{"mtu": 9216}'
curl -sk -u cumulus:'P@ss' -X PATCH -H 'Content-Type: application/json' \
  "https://leaf01:8765/nvue_v1/revision/${REV}" -d '{"state":"apply","auto-prompt":{"ays":"ays_yes"}}'
# Poll until state is "applied"
curl -sk -u cumulus:'P@ss' "https://leaf01:8765/nvue_v1/revision/${REV}" | jq .
```
`rev=applied`, `rev=pending`, `rev=operational`, `rev=startup` select which view you read — the same three-plus-one states as the CLI (Module 4.2 §2.2). The REST API is what the `nvidia.nvue` Ansible collection, NetQ LCM configuration profiles and NVIDIA Air use. Enable/verify the API with `nv show system api` and `nv set system api state enabled` (on by default on 5.x; verify object name). Replace the self-signed cert via `nv set system api certificate ...` for production.

## 2. NVUE YAML structure

`nv config show -o yaml` emits the applied configuration as a list containing one `set` mapping (and, when relevant, `unset`). The hierarchy mirrors the CLI:

```yaml
- set:
    system:
      hostname: leaf01
    interface:
      lo:
        ip:
          address:
            10.0.0.11/32: {}
        type: loopback
      swp1:
        link:
          breakout:
            2x: {}
      swp1s0:
        bridge:
          domain:
            br_default:
              access: 100
        type: swp
      swp61-64:
        ip:
          ipv4:
            forward: on
        router:
          adaptive-routing:
            enable: on
        type: swp
    bridge:
      domain:
        br_default:
          vlan:
            '100': {}
            '200': {}
    router:
      bgp:
        autonomous-system: 65011
        router-id: 10.0.0.11
      adaptive-routing:
        enable: on
    vrf:
      default:
        router:
          bgp:
            neighbor:
              swp61:
                remote-as: external
                type: unnumbered
    qos:
      roce:
        enable: on
        mode: lossless
```
Observations: leaf values that are "present" are empty mappings (`10.0.0.11/32: {}`); ranges like `swp61-64` are valid keys; numbers that are keys (`'100'`) are quoted; enumerations use CLI words (`on`, `external`, `lossless`). The simplest way to learn the exact schema for anything is to configure it once by hand and read `nv config show -o yaml`.

### 2.1 Applying YAML
```bash
nv config replace /home/cumulus/leaf01.yaml    # pending := this file exactly (declarative, removes anything not in it)
nv config patch   /home/cumulus/roce.yaml      # merge (add/override only)
nv config diff                                 # review
nv config apply -y                             # --assume-yes
nv config save
```
`replace` is idempotent and complete: running it twice with the same file produces no diff. `patch` is additive; it cannot remove configuration unless the file carries an `- unset:` block. Pipelines use `replace` for full device configs and `patch` for targeted features.

## 3. Templates: a Spectrum-X leaf with Jinja2

A template renders one YAML per device from a variables file. Below is a working pattern for a Spectrum-X leaf: hostname, breakout, RoCE lossless, AR, BGP unnumbered, EVPN, tenant VRF.

### 3.1 Variables (`host_vars/leaf01.yml`)
```yaml
hostname: leaf01
loopback: 10.0.0.11
asn: 65011
uplinks: [swp61, swp62, swp63, swp64]
breakout_2x: [swp1, swp2, swp3, swp4]          # 800G → 2×400G for SuperNICs
roce_mode: lossless
ar_threshold: 70
tenants:
  - name: tenantA
    l3vni: 100100
    vlans:
      - { id: 100, vni: 10100, gw: 10.100.1.1/24, vrr: 10.100.1.254/24, vrr_mac: "00:00:5e:00:01:01" }
  - name: tenantB
    l3vni: 100200
    vlans:
      - { id: 200, vni: 10200, gw: 10.200.1.1/24, vrr: 10.200.1.254/24, vrr_mac: "00:00:5e:00:01:02" }
access:
  swp1s0: 100
  swp1s1: 200
```

### 3.2 Template (`templates/spectrumx-leaf.yaml.j2`)
```yaml
- set:
    system:
      hostname: {{ hostname }}
    interface:
      lo:
        type: loopback
        ip:
          address:
            {{ loopback }}/32: {}
{% for p in breakout_2x %}
      {{ p }}:
        type: swp
        link:
          breakout:
            2x: {}
{% endfor %}
{% for port, vlan in access.items() %}
      {{ port }}:
        type: swp
        link:
          mtu: 9216
        bridge:
          domain:
            br_default:
              access: {{ vlan }}
{% endfor %}
{% for u in uplinks %}
      {{ u }}:
        type: swp
        link:
          mtu: 9216
        ip:
          ipv4:
            forward: on
        router:
          adaptive-routing:
            enable: on
            link-utilization-threshold: {{ ar_threshold }}
{% endfor %}
{% for t in tenants %}{% for v in t.vlans %}
      vlan{{ v.id }}:
        type: svi
        vlan: {{ v.id }}
        ip:
          vrf: {{ t.name }}
          address:
            {{ v.gw }}: {}
          vrr:
            enable: on
            mac-address: {{ v.vrr_mac }}
            address:
              {{ v.vrr }}: {}
{% endfor %}{% endfor %}
    bridge:
      domain:
        br_default:
          vlan:
{% for t in tenants %}{% for v in t.vlans %}
            '{{ v.id }}':
              vni:
                '{{ v.vni }}': {}
{% endfor %}{% endfor %}
    nve:
      vxlan:
        enable: on
        source:
          address: {{ loopback }}
        arp-nd-suppress: on
    evpn:
      enable: on
    qos:
      roce:
        enable: on
        mode: {{ roce_mode }}
    router:
      bgp:
        enable: on
        autonomous-system: {{ asn }}
        router-id: {{ loopback }}
      adaptive-routing:
        enable: on
    vrf:
      default:
        router:
          bgp:
            enable: on
            peer-group:
              underlay:
                remote-as: external
                address-family:
                  l2vpn-evpn:
                    enable: on
            neighbor:
{% for u in uplinks %}
              {{ u }}:
                type: unnumbered
                peer-group: underlay
{% endfor %}
            address-family:
              ipv4-unicast:
                enable: on
                network:
                  {{ loopback }}/32: {}
              l2vpn-evpn:
                enable: on
{% for t in tenants %}
      {{ t.name }}:
        evpn:
          vni:
            '{{ t.l3vni }}': {}
        router:
          bgp:
            enable: on
            autonomous-system: {{ asn }}
            router-id: {{ loopback }}
            address-family:
              ipv4-unicast:
                enable: on
                redistribute:
                  connected:
                    enable: on
              l2vpn-evpn:
                enable: on
{% endfor %}
```
Exact attribute names (e.g. `type: svi`, `vrr.enable`) should be confirmed against `nv config show -o yaml` on your release — NVUE schemas evolve. Render with `ansible.builtin.template`, or offline with `j2`/Python, then `nv config replace` the result.

## 4. Ansible

### 4.1 Inventory and connection
Two connection styles work with Cumulus:

| Style | Inventory settings | Pros / cons |
|---|---|---|
| **SSH** (`ansible_connection: ssh`, run `nv` commands or copy YAML) | `ansible_user: cumulus`, `ansible_become: false` (nv needs no sudo) | Simple, works everywhere, also lets you run Linux tasks (apt, files). Config pushed via `nv config replace` + `apply` |
| **REST** via the `nvidia.nvue` collection | `ansible_connection: local` (modules call the API from the controller) with `ansible_host`, API credentials as module parameters / vars | Structured, no shell parsing, revision-aware. Requires port 8765 reachable from the controller |

```ini
# inventory/hosts.ini
[spines]
spine01 ansible_host=10.10.1.1
spine02 ansible_host=10.10.1.2

[leaves]
leaf01 ansible_host=10.10.1.11
leaf02 ansible_host=10.10.1.12
leaf03 ansible_host=10.10.1.13
leaf04 ansible_host=10.10.1.14

[cumulus:children]
spines
leaves

[cumulus:vars]
ansible_user=cumulus
ansible_password={{ vault_cumulus_password }}
ansible_connection=ssh
ansible_python_interpreter=/usr/bin/python3
```
```yaml
# group_vars/cumulus.yml
nvue_api_user: cumulus
nvue_api_password: "{{ vault_cumulus_password }}"
nvue_api_port: 8765
mtu: 9216
```
`ansible_network_os` is not required for Cumulus when using plain SSH (it is a Debian host); with `network_cli` you would set `ansible_network_os: cumulus` only for legacy NCLU modules, which are deprecated. Install the collection: `ansible-galaxy collection install nvidia.nvue`.

### 4.2 The `nvidia.nvue` collection
Modules (as published in the collection; confirm the current list with `ansible-doc -l nvidia.nvue`):

| Module | Purpose |
|---|---|
| `nvidia.nvue.command` | Run arbitrary `nv` CLI commands over SSH (`nv show ...`, `nv config apply`) |
| `nvidia.nvue.api` | Generic REST call to any `/nvue_v1/<path>` the typed modules don't cover (e.g. `qos`, `nve`, `acl` in some versions) |
| `nvidia.nvue.config` | Revision lifecycle via REST: create a revision, apply, save, show diff |
| `nvidia.nvue.interface` | `/interface` tree |
| `nvidia.nvue.bridge` | `/bridge` |
| `nvidia.nvue.router` | `/router` (BGP global, adaptive routing, policy) |
| `nvidia.nvue.vrf` | `/vrf` (including per-VRF BGP neighbours) |
| `nvidia.nvue.evpn`, `nvidia.nvue.vxlan` | `/evpn`, `/nve/vxlan` |
| `nvidia.nvue.mlag`, `nvidia.nvue.acl`, `nvidia.nvue.service`, `nvidia.nvue.system` | Corresponding trees |

Note: there is no dedicated `nvidia.nvue.qos` or `config_apply` module at the time of writing — QoS/RoCE is driven through `nvidia.nvue.api` (path `qos`) or the CLI, and apply is `nvidia.nvue.config` with `state: apply`. Typed modules take `state` (`merged` to set, `gathered` to read, `deleted` to unset), `revid` (the revision to write into) and `data` (the same structure as the YAML under that tree). The pattern is always: create revision → write into it → apply → (save).

### 4.3 Playbook A — VLANs, SVIs and bonds on all leaves (REST collection)
```yaml
# playbooks/leaf-l2.yml
- name: Rollout tenant VLANs, SVIs and host bonds on leaves
  hosts: leaves
  gather_facts: false
  connection: local
  vars:
    api: &api
      provider:
        host: "{{ ansible_host }}"
        port: "{{ nvue_api_port }}"
        username: "{{ nvue_api_user }}"
        password: "{{ nvue_api_password }}"
        validate_certs: false
  tasks:
    - name: Open a new NVUE revision
      nvidia.nvue.config:
        <<: *api
        state: new
      register: rev

    - name: Define VLANs on the VLAN-aware bridge
      nvidia.nvue.bridge:
        <<: *api
        state: merged
        revid: "{{ rev.revid }}"
        data:
          domain:
            - id: br_default
              vlan: "{{ vlans | map(attribute='id') | map('string') | list | map('community.general.dict_kv', 'id') | list }}"

    - name: Host-facing bonds and SVIs
      nvidia.nvue.interface:
        <<: *api
        state: merged
        revid: "{{ rev.revid }}"
        data: >-
          {{
            (bonds | map('combine', {'type': 'bond'}) | list) +
            (vlans | map('nvue_svi') | list)
          }}

    - name: Apply the revision (waits for completion)
      nvidia.nvue.config:
        <<: *api
        state: apply
        revid: "{{ rev.revid }}"
        force: true
        wait: 90

    - name: Save to startup
      nvidia.nvue.config:
        <<: *api
        state: save
```
```yaml
# group_vars/leaves.yml
vlans:
  - { id: 100, gw: 10.100.1.1/24 }
  - { id: 200, gw: 10.200.1.1/24 }
bonds:
  - id: bond1
    bond: { member: { swp1s0: {}, swp2s0: {} }, mode: lacp, lacp-rate: fast }
    bridge: { domain: { br_default: { access: 100 } } }
    link: { mtu: 9216 }
```
The `data` payloads mirror the YAML tree exactly (list of objects with `id` for keyed collections). The Jinja filters shown (`nvue_svi`, `dict_kv`) are illustrative of shaping variables into that structure; in practice most teams find it simpler to render a full YAML with `template` and use the SSH approach below.

### 4.4 Playbook A′ — same outcome via SSH and `nv config replace` (recommended for full configs)
```yaml
# playbooks/leaf-full.yml
- name: Render and apply complete NVUE configuration
  hosts: leaves
  gather_facts: false
  vars:
    remote_cfg: /home/cumulus/nvue-desired.yaml
  tasks:
    - name: Render desired state from template
      ansible.builtin.template:
        src: templates/spectrumx-leaf.yaml.j2
        dest: "{{ remote_cfg }}"
        mode: "0644"
      register: rendered

    - name: Load desired state into pending and compute diff
      ansible.builtin.shell: |
        nv config replace {{ remote_cfg }} && nv config diff
      register: diff
      changed_when: diff.stdout | trim | length > 0
      check_mode: false

    - name: Show diff
      ansible.builtin.debug:
        var: diff.stdout_lines
      when: ansible_diff_mode

    - name: Apply (skipped in --check)
      ansible.builtin.command: nv config apply --assume-yes
      when: diff.changed and not ansible_check_mode
      register: apply
      failed_when: "'error' in apply.stdout | lower or apply.rc != 0"

    - name: Discard pending when nothing to apply or in check mode
      ansible.builtin.command: nv config detach
      when: not diff.changed or ansible_check_mode
      changed_when: false

    - name: Save to startup
      ansible.builtin.command: nv config save
      when: apply is changed
```
Idempotency comes from `nv config replace` + `nv config diff`: an identical desired state yields an empty diff, no apply, no change reported. `--check --diff` renders and diffs without applying. An alternative is `ansible.builtin.uri` calling the REST endpoints in §1 — same semantics, no SSH.

### 4.5 Playbook B — RoCE lossless + adaptive routing + per-interface settings
```yaml
# playbooks/roce-ar.yml
- name: Enable Spectrum-X RoCE profile and adaptive routing on leaves and spines
  hosts: cumulus
  gather_facts: false
  vars:
    roce_patch: /home/cumulus/roce-ar.yaml
  tasks:
    - name: Render RoCE/AR patch
      ansible.builtin.copy:
        dest: "{{ roce_patch }}"
        content: |
          - set:
              qos:
                roce:
                  enable: on
                  mode: {{ roce_mode | default('lossless') }}
              router:
                adaptive-routing:
                  enable: on
              interface:
          {% for u in fabric_ports %}
                {{ u }}:
                  link:
                    mtu: 9216
                  router:
                    adaptive-routing:
                      enable: on
                      link-utilization-threshold: {{ ar_threshold | default(70) }}
          {% endfor %}

    - name: Patch pending and diff
      ansible.builtin.shell: nv config patch {{ roce_patch }} && nv config diff
      register: diff
      changed_when: diff.stdout | trim | length > 0
      check_mode: false

    - name: Apply with extended timeout (AR may reload/restart switchd)
      ansible.builtin.command: nv config apply --assume-yes
      when: diff.changed and not ansible_check_mode
      async: 600
      poll: 15
      register: apply

    - name: Detach when no change
      ansible.builtin.command: nv config detach
      when: not diff.changed or ansible_check_mode
      changed_when: false

    - name: Save
      ansible.builtin.command: nv config save
      when: apply is changed

    - name: Verify RoCE profile
      ansible.builtin.command: nv show qos roce -o json
      register: roce
      changed_when: false
    - ansible.builtin.assert:
        that:
          - (roce.stdout | from_json).mode is defined
        fail_msg: "RoCE profile not applied on {{ inventory_hostname }}"
```
```yaml
# group_vars/leaves.yml (excerpt)
fabric_ports: [swp61, swp62, swp63, swp64]
roce_mode: lossless
ar_threshold: 70
# group_vars/spines.yml
fabric_ports: "{{ ['swp'] | product(range(1, 65)) | map('join') | list }}"
```
Idempotency notes: the patch is a subset of desired state, so the diff is empty on reruns; `nv set qos roce` on an already-identical profile produces no diff. Because applying AR can restart `switchd` on Cumulus ≤5.12 (Module 4.1 §4.4), the task uses `async`/`poll` to survive a dropped SSH session and `serial` at the play level is advisable (`serial: 1` for MLAG pairs or rails) so you never reload every leaf at once.

### 4.6 Check and diff mode
`ansible-playbook playbooks/leaf-full.yml --check --diff` runs the render and `nv config replace` + `nv config diff` (marked `check_mode: false` so they execute) and skips the apply. The printed `nv config diff` is the exact change the switch would make — a safer preview than Ansible's own diff of the YAML file.

### 4.7 Host-side automation (brief)
The same inventory can carry GPU hosts: `apt install doca-ofed`, `mlnx_qos -i {{ roce_if }} --trust dscp --pfc 0,0,0,1,0,0,0,0`, ECN sysfs writes, a `/etc/nccl.conf` or environment file with `NCCL_IB_TC=106`, and persistent `mlxconfig` parameters. Because `mlnx_qos` is not idempotent by itself, read current state first (`mlnx_qos -i <if>`) and change only on mismatch. See Module 5.2 §7.

## 5. Validation after apply with NetQ

Configuration success ≠ operational success. Close the loop with NetQ (Module 4.5):
```yaml
- name: Validate fabric with NetQ
  hosts: netq_server
  gather_facts: false
  tasks:
    - ansible.builtin.command: "netq check {{ item }} json"
      loop: [bgp, evpn, interfaces, mtu, roce]
      register: checks
      changed_when: false
      failed_when: (checks.stdout | from_json).summary.failedNodeCount | default(0) > 0
```
Or from a CI runner with the NetQ CLI/API. A pipeline that applies config and then fails on `netq check bgp` failures catches the mis-typed ASN before users do. Snapshots before/after (Module 4.5 §9.3) are the audit record.

## 6. Upgrade automation

```yaml
- name: Upgrade Cumulus Linux via NVUE action
  hosts: leaves
  serial: 1
  tasks:
    - ansible.builtin.command: nv config save
    - ansible.builtin.fetch:
        src: /etc/nvue.d/startup.yaml
        dest: backups/{{ inventory_hostname }}/
        flat: true
    - name: Package upgrade within the major release
      ansible.builtin.command: >
        nv action upgrade system packages to-version {{ target_version }} use-vrf mgmt
      async: 1800
      poll: 30
    - ansible.builtin.reboot:
        reboot_timeout: 900
    - ansible.builtin.command: nv show system -o json
      register: sys
      failed_when: target_version not in sys.stdout
```
Exact `nv action upgrade` syntax varies by release (verify); for major-version jumps use ONIE image install (`onie-select`/`onie-nos-install`) followed by `nv config replace` of the backed-up YAML (Module 4.3 §1). **NetQ LCM** is the GUI/CLI alternative that handles ordering, backup, snapshot and restore without writing a playbook — a valid exam answer when the question stresses "minimal scripting".

## 7. Git-based configuration management and CI

Recommended repository layout:
```text
fabric-config/
├── inventory/hosts.ini
├── group_vars/{cumulus,leaves,spines}.yml
├── host_vars/leaf01.yml ...
├── templates/spectrumx-leaf.yaml.j2, spine.yaml.j2
├── rendered/           # CI artefact: one NVUE YAML per switch (committed for review)
└── playbooks/
```
Pipeline (GitLab CI/GitHub Actions):
1. **Lint/render** — `ansible-lint`, render templates, `yamllint` the NVUE YAML, diff `rendered/` against the previous commit so reviewers see the *device* change.
2. **Simulate** — spin up an NVIDIA Air twin via API, apply with `--check --diff` then for real, run `netq check` in the twin (control-plane validation only).
3. **Deploy** — on merge to `main`, run the playbook against production with `serial`, then `netq check` and snapshot compare; fail the job and open an incident if validation fails.
4. **Drift detection** — nightly `nv config show -o yaml` fetch per switch compared with `rendered/`; any diff means someone changed a switch by hand.

NVUE's declarative `replace` is what makes git the source of truth: the file *is* the switch configuration, so a revert in git is a rollback in the fabric. Keep secrets in Ansible Vault, never in YAML.

## 8. Common errors and how to handle them

| Symptom | Cause | Fix |
|---|---|---|
| `nv config apply` hangs or Ansible times out | Apply triggers `switchd` reload/restart (breakout, AR, forwarding profile) or many objects | Use `async`/`poll`, longer `wait` in `nvidia.nvue.config`, `serial`, schedule windows |
| "Another user has pending changes" / apply prompts to overwrite | A detached revision from a previous session or another operator | `nv config detach` first in the play; in REST use `force: true` on apply (overwrites other pending sets — coordinate) |
| Revision locked / `409 Conflict` from the API | Two revisions applying simultaneously or a stuck revision | Wait for the in-progress revision (`GET /nvue_v1/revision`) to reach `applied`/`failed`; re-create revision |
| Apply fails with validation error | YAML attribute invalid for this release or dependent object missing (VLAN not on bridge before SVI) | Read the error, fix template; `nv config diff` and `nv config apply --confirm-status` to inspect; test in Air |
| Playbook always reports changed | Using `command: nv set ...` without diff logic, or `patch` adding keys the switch normalises differently | Use `replace`/`patch` + `nv config diff` with `changed_when`; compare normalised `nv config show -o yaml` |
| Config applied but reverted after reboot | Missing `nv config save` | Always end with save; or write startup.yaml directly and apply |
| REST calls fail with certificate errors | Self-signed cert | `validate_certs: false` in lab; install CA-signed cert via `nv set system api certificate` in production |
| `nv` not found in Ansible | PATH/non-login shell issue or `become: true` running as root without profile | Use full path `/usr/bin/nv`, do not `become` |

## Configuration / command reference

```bash
# REST
curl -sk -u cumulus:<pw> https://<sw>:8765/nvue_v1/interface
curl -sk -u cumulus:<pw> -X POST https://<sw>:8765/nvue_v1/revision
curl -sk -u cumulus:<pw> -X PATCH -H 'Content-Type: application/json' "https://<sw>:8765/nvue_v1/<path>?rev=<rev>" -d '<json>'
curl -sk -u cumulus:<pw> -X PATCH -H 'Content-Type: application/json' https://<sw>:8765/nvue_v1/revision/<rev> -d '{"state":"apply","auto-prompt":{"ays":"ays_yes"}}'
nv show system api; nv set system api state enabled

# Files
nv config show -o yaml > desired.yaml
nv config replace desired.yaml | nv config patch partial.yaml
nv config diff; nv config apply -y; nv config save; nv config detach
nv config history; nv config apply <rev>

# Ansible
ansible-galaxy collection install nvidia.nvue
ansible-doc -l nvidia.nvue
ansible-playbook -i inventory/hosts.ini playbooks/leaf-full.yml --check --diff
ansible-playbook ... --limit leaf01 -e target_version=5.11.0

# Validation / upgrade
netq check bgp|evpn|interfaces|mtu|roce
nv action upgrade system packages to-version <ver> use-vrf mgmt    # verify syntax
```

## Common pitfalls & exam traps
- NVUE REST API is port **8765** under `/nvue_v1/`; the CLI path maps 1:1 to the URL path. NetQ agents use 31980 and the NetQ UI 443 — do not mix them up.
- Writes go into a *revision* and are applied by patching the revision state; a bare PATCH to an object without `rev=` does nothing persistent.
- `nv config replace` is declarative (removes what is absent); `nv config patch` merges. Use `replace` for full-device files.
- Idempotency in Ansible comes from `nv config diff`, not from Ansible's `command` module (which always reports changed).
- Applying AR/breakout can restart `switchd`; automate with `async`, `serial` and maintenance windows.
- `nv config apply` without `nv config save` is lost on reboot — every playbook must end with save.
- `nvidia.nvue.config` with `state: apply` is the apply step; there is no `config_apply` module. QoS/RoCE goes through `nvidia.nvue.api` or the CLI.
- `--check` cannot preview NVUE changes unless the diff tasks are marked `check_mode: false`; the apply must be guarded by `not ansible_check_mode`.
- Do not run `nv` with `become: true`; NVUE does not need root and sudo can break the environment.
- NVIDIA Air validates playbooks for control plane only; RoCE/AR effects need hardware.
- NetQ LCM is a non-Ansible way to do upgrades and config profiles — a legitimate answer to "least scripting".

## Key takeaways
- NVUE is an API first: `https://<switch>:8765/nvue_v1/<cli-path>`; revisions are created, patched, applied.
- NVUE YAML = `- set:` tree mirroring the CLI; obtain the schema from `nv config show -o yaml`.
- `nv config replace` (full, declarative) vs `nv config patch` (merge); both followed by `diff`, `apply -y`, `save`.
- Jinja2 templates + host_vars render one YAML per switch; a Spectrum-X leaf template carries hostname, breakout, RoCE, AR, BGP unnumbered, EVPN, VRFs.
- Ansible options: `nvidia.nvue` collection over REST (typed modules + `config` revision lifecycle) or SSH with `template` + `nv config replace`.
- Idempotency pattern: replace/patch → `nv config diff` → apply only if non-empty → save; `--check --diff` previews.
- Use `async`/`poll` and `serial` around applies that reload `switchd`.
- Validate with `netq check bgp|evpn|interfaces|roce` after every apply; snapshot before/after.
- Upgrades: `nv action upgrade` (minor), ONIE image + `nv config replace` (major), or NetQ LCM.
- Git is the source of truth; CI renders, simulates in Air, deploys with serial, validates with NetQ, detects drift nightly.

## Review questions

1. Which URL returns the applied RoCE configuration of switch leaf01 via the NVUE API?
   A. `https://leaf01:443/api/qos/roce`
   B. `https://leaf01:8765/nvue_v1/qos/roce?rev=applied`
   C. `https://leaf01:31980/nvue/qos`
   D. `https://leaf01:8765/netq/roce-config`

2. A playbook renders a full NVUE YAML and must remove any configuration not present in the file. Which command should it use before applying?
   A. `nv config patch <file>`
   B. `nv config replace <file>`
   C. `nv config save <file>`
   D. `nv set config <file>`

3. An Ansible task running `nv set interface swp1 link mtu 9216` reports "changed" on every run even though nothing changes. What is the correct fix?
   A. Add `become: true`
   B. Replace with `nv config patch` + `nv config diff` and set `changed_when` on a non-empty diff
   C. Use `ignore_errors`
   D. Run with `--check`

4. Applying adaptive routing on Cumulus 5.10 leaves drops the SSH session and the Ansible task fails with a timeout. Best remediation?
   A. Use `nvidia.nvue.qos` module
   B. Run the apply with `async`/`poll` and `serial`, since the apply restarts `switchd`
   C. Lower the MTU
   D. Disable the NVUE API

5. Which `nvidia.nvue` module creates a revision and applies it?
   A. `nvidia.nvue.config_apply`
   B. `nvidia.nvue.command`
   C. `nvidia.nvue.config` with `state: new` then `state: apply`
   D. `nvidia.nvue.system`

6. After a successful playbook run, the team wants proof that all BGP and EVPN sessions across the fabric are healthy. Which step closes the loop?
   A. `nv config save` on each switch
   B. `netq check bgp` and `netq check evpn`
   C. `ansible-lint`
   D. `nv config history`

7. In a git-based workflow, what makes a `git revert` equivalent to a network rollback?
   A. Ansible's `--diff` flag
   B. NVUE's declarative `nv config replace` applying the file as the complete desired state
   C. The NetQ agent
   D. ONIE

8. A REST apply returns 409 Conflict. Most likely cause?
   A. Wrong password
   B. Another revision is currently applying or a stale pending revision holds the lock
   C. Port 8765 closed
   D. The YAML used tabs

### Answer key
1. B — NVUE API is on 8765 under `/nvue_v1/`, with `rev=applied` selecting the applied view.
2. B — `replace` sets pending to exactly the file's contents; `patch` only merges.
3. B — Idempotency must come from `nv config diff`; `command` always reports changed.
4. B — Applying AR on ≤5.12 restarts `switchd`; async/serial survive the disruption.
5. C — `nvidia.nvue.config` manages the revision lifecycle; no `config_apply` module exists.
6. B — NetQ validation confirms operational state fabric-wide.
7. B — Declarative replace makes the file the full configuration, so reverting the file reverts the switch.
8. B — Revision conflicts occur when another apply is in progress or pending revisions collide.

## References
- NVUE CLI; NVUE API — https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/System-Configuration/NVIDIA-User-Experience-NVUE/
- NVUE Command Reference — https://docs.nvidia.com/networking-ethernet-software/nvue-reference/
- NVUE Cheat Sheet — https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/System-Configuration/NVIDIA-User-Experience-NVUE/NVUE-Cheat-Sheet/
- Data Center Network Automation: Ansible Deployment Guide — NVUE Automation with Ansible — https://docs.nvidia.com/networking-ethernet-software/guides/Data-Center-Network-Automation-Ansible-Deployment-Guide/NVUE-Automation-with-Ansible
- `nvidia.nvue` Ansible Collection — https://galaxy.ansible.com/ui/repo/published/nvidia/nvue/ and https://github.com/nvidia-networking/ansible-collection-nvue (verify current location)
- NVIDIA Developer Blog: Automating Data Center Networks with NVIDIA NVUE and Ansible — https://developer.nvidia.com/blog/automating-data-center-networks-with-nvidia-nvue-and-ansible/
- Cumulus Linux: Upgrading Cumulus Linux — https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/Installation-Management/Upgrading-Cumulus-Linux/
- NVIDIA Air — https://air.nvidia.com/
- Related: [`../04-spectrum-ethernet/02-cumulus-linux-essentials.md`](../04-spectrum-ethernet/02-cumulus-linux-essentials.md), [`../04-spectrum-ethernet/03-cumulus-linux-administration.md`](../04-spectrum-ethernet/03-cumulus-linux-administration.md), [`../04-spectrum-ethernet/01-spectrum-x.md`](../04-spectrum-ethernet/01-spectrum-x.md), [`../04-spectrum-ethernet/05-netq-deployment-and-installation.md`](../04-spectrum-ethernet/05-netq-deployment-and-installation.md), [`../05-bluefield-doca/02-doca-sdk-overview.md`](../05-bluefield-doca/02-doca-sdk-overview.md)
