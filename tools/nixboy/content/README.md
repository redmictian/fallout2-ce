# Trusted training content

The installed catalog is `nixboy/content/` beside the game executable. Source
authors edit `tools/nixboy/content/`; the package deploys a separate editable
copy. Start the installed bridge with `--content-dir /path/to/game/nixboy/content`
or use its default, the `content/` folder beside `bridge.py`. Restart the bridge
after editing. Keep the existing state directory and back up local catalog edits
before replacing the runtime folder during an upgrade.

The initial catalog maps Unarmed (engine skill 3) to **Filesystem** and Science
(engine skill 12) to **Networking**, with five authored options at each of two
levels. `tracks.yaml` attaches levels to inclusive current effective Fallout
skill bands: Unarmed 15–19 / 20–24%, Science 10–14 / 15–19%. Only a matching band
is eligible; uncovered values offer no challenge. These are initial data choices,
not engine limits. Each challenge YAML is a concrete pool entry with bounded
parameters for reproducible instances. Filenames never define challenge identity.

## Add a level or skill

`tracks.yaml` uses schema 2. Each track supplies a stable `id`, Fallout-facing
`title`, visible real-world `knowledge_title`, numeric engine `skill`, and a
`levels` map. Each level ID has a `title`, `min_skill` and `max_skill`:

```yaml
schema: 2
tracks:
  - id: unarmed
    title: Unarmed
    knowledge_title: Filesystem
    skill: 3
    levels:
      3:
        title: File placement
        min_skill: 25
        max_skill: 29
```

This is a minimal illustrative catalog; when extending the installed catalog,
add the level to its existing track and retain levels 1 and 2. Add at least one
matching challenge file anywhere beneath `challenges/`, for example
`challenges/unarmed/level3/copy-manifest.yaml`:

```yaml
id: unarmed.files.copy-manifest-advanced
version: 1
skill: 3
level: 3
title: Copy a delivery manifest
kind: copy-file
instructions: "Copy ~/lab/incoming/{name}.txt to ~/lab/delivery/manifest.txt without changing or removing the original."
parameters:
  name: [supplies, medicine, tools]
```

To add another skill, add its track and matching challenge definitions using the
same schema. The stable engine IDs are:

| ID | Fallout skill | ID | Fallout skill |
| --- | --- | --- | --- |
| 0 | Small Guns | 9 | Lockpick |
| 1 | Big Guns | 10 | Steal |
| 2 | Energy Weapons | 11 | Traps |
| 3 | Unarmed | 12 | Science |
| 4 | Melee Weapons | 13 | Repair |
| 5 | Throwing | 14 | Speech |
| 6 | First Aid | 15 | Barter |
| 7 | Doctor | 16 | Gambling |
| 8 | Sneak | 17 | Outdoorsman |

Level IDs may be 1–65535. Bands within a track must not overlap and must stay
within 0–300%; gaps are allowed. A catalog supports at most 2048 authored levels
in total and 8192 challenge definitions. Pools need not contain exactly five
entries. Five is the shipped curriculum choice. Knowledge and level titles
control the displayed mapping; they do not change reward identity.

Keep skill/level IDs stable after earning rewards: an earned pair grants its
bonus once even when the definition, pool or title changes. Do not renumber
levels to edit their wording. Increment a challenge's `version` when changing
its task. Restarting with changed or removed active content preserves earned
receipts and requires abandoning/reselecting the stale attempt rather than
validating it against a different definition.

Validate the installed catalog without Docker or the game:

```sh
python nixboy/challenge_content.py --content-dir nixboy/content
```

Run that command from the game/package root with the bridge's Python environment.
It checks data/schema, references, supported kinds, bands and nonempty pools;
it does not prove that the exercise is solvable or its instructions accurate.

`challenge_content.py` safely loads data and checks the limited schema.
`challenge_env.py` owns setup and state validators on the host; these trusted
modules are not installed into the learner images. Content cannot choose an
executable, Docker endpoint, host path, arbitrary Compose file or reward value.
New task kinds need reviewed application setup/validation code in this version.
For another instance of a supported task, copy an existing definition and retain
the parameters and final-state contract expected by its `kind`. The available
kinds are file/navigation operations, permissions, text processing and SSH tasks
already represented in the shipped catalog. Changing a YAML `title` does not
change what the validator checks.

Definitions require nonempty string choices for `parameters.name`; the existing
extract-field task also requires integer `channel` choices. Instruction
placeholders must be plain declared names such as `{name}`, without formatting
conversions or attribute access. Use the matching shipped definition as the
starting point when changing parameters for another supported kind.

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

Build from `nixboy/` in the installed package after the base image is available:

```sh
docker build -f container/Dockerfile.training -t fallout2-nixboy-training:dev .
docker build -f container/Dockerfile.sshd -t fallout2-nixboy-sshd:dev .
```

Source-only reference solvers and opt-in smokes remain under `tools/nixboy/tests/`
and `tools/nixboy/smoke_challenges.py`; they are not installed into learner images
or the runtime package. The opt-in source smoke creates each fixture, rejects a wrong state, resets it, applies
a separate reference solution, validates repeatably, tests stopped-dependency
classification for network labs, and removes its owned projects. Unit tests
also exercise 100 seeds per definition. These checks cover this authored catalog;
they do not prove arbitrary future generators solvable.
