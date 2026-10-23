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

## Progress and recovery

The game stores a random character training identity and a sparse ledger of
skill/level receipt IDs and earned amounts in a versioned, checksummed trailer
at the end of SAVE.DAT. Old saves
without a trailer load with empty training state. Present but incomplete or
corrupt trailers are errors, not silently accepted empty progress. The bonus is
derived from the stored earned amounts, so duplicate messages cannot stack
rewards. Earlier training saves migrate to the version-4 ledger without losing
receipts or bonuses; each older earned level retains its original five points.
Changing `reward_points` in the catalog does not change rewards already earned.
Levels can be earned
independently; a higher current skill band does not require earlier rewards.
Active attempts record their selected reward. An edited reward or an older
attempt without that snapshot requires abandoning and selecting again; this
does not remove earned progress.

The bridge durably records a successful assessment before sending its receipt.
It retries delivery after interruption. Earned learning can be restored into an
earlier save containing the same training identity, without repeatedly adding
to raw skills. A save made before that identity existed starts another profile.
Save after first opening nix-boy when you want to retain that identity. A level
already claimed cannot grant another reward, even if a later modifier lowers
the skill back into its band. Ordinary skill spending may move directly into
level 2 without completing level 1; only the eligible band's unclaimed level can
be assessed for a reward. Previously earned learning remains additive.

Closing the terminal keeps the active environment while the bridge is running.
Reset uses the same seed; it does not remove earned levels. If the skill changed
bands, checking preserves the exercise but asks you to abandon and select from
the current band. A bridge shutdown or detached expiry removes exercise resources
but retains the attempt definition,
queue and receipts. Resume then recreates the seeded starting exercise; unsaved
Linux edits do not survive that recreation.

The bridge owns all orchestration. One-service exercises have no network;
network exercises use workstation/server services on an internal Docker network,
with no host-published ports or Docker socket inside containers. Cleanup is
limited to the exact owned attempt resources. Never use global Docker prune for
nix-boy recovery. Preserve the state directory if a cleanup or validation error
needs investigation.

The initial Compose integration targets Docker's default local Unix socket (or
default Windows named pipe). Training does not yet support the legacy bridge's
custom Docker endpoint option. This workstation uses the supported WSL socket.

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

Selection, seeds, queues, attempt generations, receipt delivery and lifecycle
belong to `training.py`; character bonuses and their save record belong to
`src/nixboy_training_state.*`. The provisional content-to-skill mapping and
reward magnitude can be balanced independently of these mechanics.

Run the ordinary Python and C++ suites before real Docker acceptance. Live
exercise checks are opt-in and must inspect cleanup as well as solutions.
See the mod workspace's runtime acceptance record for what was actually run;
these operator instructions do not themselves establish gameplay verification.
