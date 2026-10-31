# Optional native nginx delivery fixture

`qemu-nginx` is a fixed profile on the existing Docker-managed QEMU supervisor.
It complements ordinary direct-container labs; it does not change them into VMs.
The profile uses a genuine Debian kernel, native `nginx.service`/systemd/journal,
and the packaged nginx master with actual unprivileged `www-data` workers.

## Fixed topology and trusted observers

The server address is **10.77.0.1:8080**, with virtual host
`lesson.nixboy.invalid`. The guest contains two real network namespaces:

| Origin role | Namespace | Own veth address |
| --- | --- | --- |
| allowed | `nb-client-allowed` | `10.77.0.2/24` |
| denied | `nb-client-denied` | `10.77.0.3/24` |

They share the guest's kernel, not a machine/failure-domain boundary. Independent
client processes run through fixed `ip netns exec` origins. nginx's request log
corroborates their genuinely distinct source addresses using fresh probe IDs.
The QEMU runner's loopback HTTP forward is **not** source-policy evidence.
Outer containers keep network none, all capabilities dropped, no-new-privileges,
read-only root, and no host disk/device/socket exposed to the guest.

For a learner inspection, use the real tool directly:

```sh
ip netns exec nb-client-allowed curl --noproxy '*' -H 'Host: lesson.nixboy.invalid' http://10.77.0.1:8080/public.txt
ip netns exec nb-client-denied curl --noproxy '*' -H 'Host: lesson.nixboy.invalid' http://10.77.0.1:8080/staff.txt
```

This is prepared fixture topology, not a new fictional application interface.
The fixed systemd `nixboy-clients.service` recreates those namespaces on boot,
before nginx. Profile code defines origins; learner/YAML input cannot supply an
arbitrary topology, executable preparation command or trusted probe origin.

Trusted probes check full response bodies/status/host identity, both clients'
public controls, real request source logs, native manager/journal, enabled/active
service, actual worker UID/GID33 and protected file metadata/data. A broken
client, stopped server or wrong virtual-host response cannot count as a policy
denial. The default virtual host returns421 rather than the assessed site's data.
Incorrect public bytes returned by a reachable expected host remain an objective
nonpass; they are not mislabeled as a broken client or provider interruption.
Read-only probe code is supplied transiently through the private QGA transport;
no validator or assessment key is copied into learner files or wiki pages.

## Two canonical preparations

The same frozen installed-tool base supports two distinct fixed preparation IDs:

- `nginx-policy-v1`: public/stage-two routes are already operational; staff is
  mistakenly open to both clients. Restore source-specific staff access while
  keeping the public controls/data and unprivileged workers intact.
- `nginx-reload-v1`: the canonical stage-one staff policy is correct, while
  `/stage2` returns503 with the declared pending body. Restore the200 ready body
  while preserving the earlier contract. This state is freshly prepared from its
  own clean overlay; no learner file or previous attempt snapshot is transferred.

Configuration is `/etc/nginx/nginx.conf`. Each preparation's code/manifest digest
binds its identity. `NginxEnvironment.preparation_identity` is exposed to the
existing manager for attempt/completion binding; canonical preflight requires the
declared unsolved fault rather than merely a healthy already-solved service.

The `reload` checkpoint requires solved HTTP/policy/preservation outcomes,
unchanged master PID and boot identity, replaced workers, and real native
`ExecReload`/journal evidence. `systemctl restart nginx` does not satisfy it.
Use `nginx -t` then `systemctl reload nginx`; command spelling is not logged as
the proof. Actual resulting service/process/request observations are checked.

## Artifact preparation and identities

Current retained volume: `nbq-20261030-images`. New immutable base:

`nginx-native-root-v1.qcow2`

SHA-256: `a471d77fce9929a2becf1a64e45639187a097d3752d590bfaee59441c45308e8`.

The code-owned `container/nginx/manifest.json` records its expected identity,
kernel/emulator profile and observed package preparation. Every runtime launch
hash-checks the base and creates a disposable writable overlay. Existing BT05
bases are separate and mounted read-only; the nginx profile uses no BT05 data disk.

The current base was derived from the already qualified common
`debian-prepared.qcow2` SHA-256
`5644b6f9ddda404609a7d395a040fc8f57a9e7c712af532c0f5ac314e9f2dd62`.
It contains Debian13.7/kernel6.12.113, nginx1.26.3-3+deb13u10 and systemd257.13.
No host software installation is required. Explicit author preparation, in WSL:

```sh
/home/mictian/build/nixboy-venv/bin/python tools/nixboy/prepare_nginx_artifact.py --run --write-manifest --output /absolute/new/private/preparation
```

This requires the trusted common base and existing qualified runner image
`nixboy-qemu-bt05:20261030`. Only the disposable preparation guest gets package
network access. Runtime runners use network none. The tool refuses to overwrite
an existing target base. A fresh rebuild may have different filesystem/package
identities: it is a new reviewed preparation, not automatic trust-on-first-use or
a promise of byte-identical public builds. General artifact distribution remains
separate work.

## Focused verification and scope

```sh
/home/mictian/build/nixboy-venv/bin/python -m unittest discover -s tools/nixboy/tests -p test_nginx_env.py -q
/home/mictian/build/nixboy-venv/bin/python tools/nixboy/smoke_native_nginx.py --run --output /absolute/new/private/native-trial
```

The headless trial uses dedicated profiles/resources and external evidence. It
tests real client/identity/service negatives, reload versus restart, independently
prepared stage states and canonical-preparation rejection, then asserts scoped
container/work-volume cleanup. It does not run the game or grant a reward.
The manager's two-stage completion and reward acceptance are separate integration
checks. This small profile does not certify SA67, all Debian/RHEL layouts, SELinux,
TLS, routed multi-guest networks or long-running guest stability.
