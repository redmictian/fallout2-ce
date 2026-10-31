# nix-boy challenge progression

nix-boy is one mod: its Linux terminal and challenge-based Fallout skill
progression share the release version in [`nixboy.version`](../../nixboy.version).
The initial bridge-owned hub has two authored tracks, two levels per track and
five challenge options per level (20 authored options). All 18 Fallout skills
can be mapped through the editable catalog:

| Fallout skill → knowledge track | Level 1: current skill | Level 2: current skill |
| --- | --- | --- |
| Unarmed → Filesystem | 15–19%: files and navigation | 20–24%: permissions |
| Science → Networking | 10–14%: text processing | 15–19%: SSH and networking |

The character's current effective value for that specific Fallout skill selects
the only eligible level. A value outside the authored bands has no challenge
available; it never falls back to an introductory pool. Ordinary skill spending
and other in-game modifiers therefore affect the eligible band on the next
snapshot. One track's progress does not select another track's difficulty.

Each level awards its configured **`reward_points`**, an integer from 1 to 300
skill percentage points; omitted means 5. The shipped four levels explicitly
use +5. The engine's existing 300 skill cap still applies. Tags do not multiply
the bonus. Passing any one
assessment completes that level; it is not necessary to solve all five options.
Each level's pool is shuffled once. A failed assessment moves its challenge to
the back of the queue. Docker/validator errors preserve the attempt instead.

## Prepare the existing WSL installation

Run from the installed game/package root, with Docker Engine and Compose already
available. The workstation deployment uses Ubuntu-26.04, not Docker Desktop.

```sh
/home/mictian/build/nixboy-venv/bin/python -m pip install -r nixboy/requirements.txt
docker build -f nixboy/container/Dockerfile -t fallout2-nixboy:dev nixboy
docker build -f nixboy/container/Dockerfile.training -t fallout2-nixboy-training:dev nixboy
docker build -f nixboy/container/Dockerfile.sshd -t fallout2-nixboy-sshd:dev nixboy
```

Use a separate Linux virtual environment on other installations. The last two
images derive from the foundation image and add the SSH client and SSH server.
These commands build locally; there is no runtime package installation inside a
challenge. Image tags are development defaults; record tested image IDs when
distributing a particular build.

Start the bridge with the existing private installation token. For this
workstation, from PowerShell:

```powershell
wsl -d Ubuntu-26.04 -- /home/mictian/build/nixboy-venv/bin/python /mnt/c/games/GameDevelopment/mods/nix-boy/tests/game/nixboy/bridge.py --token-file /home/mictian/.local/state/nixboy/ingame-test.token --content-dir /mnt/c/games/GameDevelopment/mods/nix-boy/tests/game/nixboy/content --state-dir /mnt/c/games/GameDevelopment/mods/nix-boy/runtime/training
```

The bridge must stay running. It does not install automatic startup. Training
state defaults to the user's local state directory (`~/.local/state/nixboy/training`
in Linux); `--state-dir PATH` selects another private runtime directory. Keep
that directory when restarting/upgrading: it contains earned receipts and queues,
not disposable build output. Do not regenerate the existing token to restart.
The workstation command explicitly uses this mod's ignored `runtime/training/`
directory; preserve it when cleaning `out/` or rebuilding the game.

The game must have `[nixboy] enabled=1`. Training is enabled by default;
`training=0` selects the original ephemeral free-shell mode and its explicit
legacy global bindings. Training mode ignores those legacy result bindings.

## Play

Use the configured shortcut in normal gameplay or the Pip-Boy (default Ctrl+N),
or click the Pip-Boy's nix-boy button. Plain N retains the game's attack-mode
action with the default configuration. The hub shows current character skills,
their knowledge-track names and available levels. Select the displayed track number,
then `P` for unrewarded practice or `A` for an assessment. Choices use Enter.

Set `[nixboy] hotkey=...` in `nixboy.ini` to change the shortcut, then restart
the game. See [supported shortcut values](README.md#game-binding-and-linux-helpers).

| Control | Action |
| --- | --- |
| F5 | Assess actual exercise state |
| F6 | Return to the hub, keeping the exercise |
| F7 | Reset the same seeded exercise |
| F8 | Abandon the exercise, keeping earned progress |
| F10 / Close | Return to the previous game screen and detach |
| R in the hub | Resume the active exercise |

Escape and Ctrl+C belong to Linux. Leaving a shell returns to the hub; a shell's
exit code is not a completion result. Reopening attaches a fresh shell to the
existing exercise files. This does not promise continuation of the same editor
or foreground process.

The exercise instructions describe the required final state. F5 runs a trusted
bridge-side validator against that state, independently of terminal output and
command history. Equivalent valid solutions are accepted unless the objective
explicitly requires a technique. Learner `nixboy emit` commands cannot award
training progress.

## Game progress and active work

Saving/loading uses the ordinary game save system with its existing native
training bonus/claim ledger. Saving needs no bridge, Docker, QEMU or external
education files. The loaded character's skills, bonuses, owned perk ranks and
native claims are authoritative. Later external results are never restored into
an earlier save; a rolled-back advancement can be genuinely earned again.

The existing opaque training identity is renewed on first terminal open after a
game load/new game. Reopening without loading keeps that live assessment run.
The bridge's runtime.json contains only active task state, alternatives, current
project cursor and one completion awaiting acknowledgment. It is not an earned
achievement database and never imports previous training.json histories.

Lost ACK may retry the exact current completion within that run. Native duplicate
checks prevent another effect. Accepted delivery or an exact observed character
claim stops retry; a new run discards old pending results. No receipt-history
recovery, snapshot reference, external save association or activation protocol is
required. New writes remain v4/v5; a small v6 reader ignores obsolete reference
bytes for earlier experimental saves. See CLEANUP.md and PERK_REWARDS.md.

Detach/reopen keeps the active environment/files while its provider runs. Loading
starts a fresh assessment run and cleans the prior scoped runtime on next open;
practical labs restart canonically. The project cursor and theory drafts are
current-run work, not game-save progress. Provider restart may reissue that same
run's canonical task, without a learner penalty. No VM/process state is restored.
Within-run reset affects the current stage, not earlier stage order. Independent
course-wide prerequisites/ALL completions and cross-load project progress are deferred.

Use a fresh candidate state directory and scoped provider controls. If task state
is lost/corrupt, preserve it and start fresh task state; the game save remains
loadable with its own skills/perks. Do not merge old progression snapshots, run
another writer against live state, expose the Docker socket or use global prune.

## Authoring and validation

The installed `nixboy/content/tracks.yaml` maps Fallout skills to knowledge
titles, inclusive percentage bands and per-level `reward_points`. Each YAML file under
`nixboy/content/challenges/` is an authored pool entry with bounded
parameter choices. `challenge_content.py` interprets these definitions;
`challenge_env.py` supplies trusted setup, semantic validation and the shared
Compose orchestration. `content/compose/` contains single-service and networking
topologies. YAML never supplies game code or executes a host command itself.
See [content authoring](content/README.md) for adding a level or skill using an
existing task kind. Restart the bridge after catalog edits; preserve the state
directory and stable skill/level IDs. New task kinds still require trusted code.

The engine accepts all 18 skill IDs and level IDs 1–65535, with at most 2048
earned receipts per character. The shipped two-track slice is authored content,
not a hardcoded progression limit. Source catalog edits belong in
`tools/nixboy/content/`; installed edits affect the separate runtime copy.

Selection, seeds, current-run queues, attempt generations, one-result delivery and lifecycle
belong to `training.py`; character bonuses and their save record belong to
`src/nixboy_training_state.*`. The provisional content-to-skill mapping and
reward magnitude can be balanced independently of these mechanics.

Run the ordinary Python and C++ suites before real Docker acceptance. Live
exercise checks are opt-in and must inspect cleanup as well as solutions.
See the mod workspace's runtime acceptance record for what was actually run;
these operator instructions do not themselves establish gameplay verification.
