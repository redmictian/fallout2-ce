# Trusted training content

This nix-boy catalog has two independent tracks, Unarmed (engine skill 3) and
Science (engine skill 12), with five distinct authored tasks at each of two
levels. `tracks.yaml` attaches each level to inclusive current effective Fallout
skill bands: Unarmed 15–19 / 20–24%, Science 10–14 / 15–19%. Only the matching
band is eligible; uncovered values offer no challenge. Each YAML file is a
concrete pool entry; bounded parameters give that task several reproducible
instances. Filenames never define the challenge ID.

`challenge_content.py` safely loads data and checks the limited schema.
`challenge_env.py` owns setup and state validators on the host; these trusted
modules are not installed into the learner images. Content cannot choose an
executable, Docker endpoint, host path, arbitrary Compose file or reward value.
New task kinds need reviewed application setup/validation code in this version.

`instructions` is formatted with the saved resolved parameters and written to
`~/CHALLENGE.txt`. Practice uses the same task environment without a reward; its
policy belongs to the training manager. Definitions contain the task objective
and bounded fixture parameters, with setup and validation in trusted code.

One-service file/text tasks use `compose/single.yaml`; SSH tasks use
`compose/network.yaml` with a workstation and SSH server on an internal network.
There are no host binds, published ports, external resources or Docker sockets.
Home directories and runtime files are disposable tmpfs. Reset reproduces the
same parameters and preserves the external character progress ledger.

The `training` password is a public lab credential on the isolated SSH server.
It is deliberately instructional, not a secret, and is never used on the host.
Most SSH exercises have prepared keys; the key-login task requires installing
the workstation public key. The test-only solver lives under `tests/`, outside
all learner images.

Build from `tools/nixboy` after the base image is available:

```sh
docker build -f container/Dockerfile.training -t fallout2-nixboy-training:dev .
docker build -f container/Dockerfile.sshd -t fallout2-nixboy-sshd:dev .
python smoke_challenges.py --run
```

The opt-in smoke creates each fixture, rejects a wrong state, resets it, applies
a separate reference solution, validates repeatably, tests stopped-dependency
classification for network labs, and removes its owned projects. Unit tests
also exercise 100 seeds per definition. These checks cover this authored catalog;
they do not prove arbitrary future generators solvable.
