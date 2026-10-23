# nix-boy terminal and Docker bridge

nix-boy is the mod and terminal name. The terminal and its skill-progression
feature are released together, using the version in
[`nixboy.version`](../../nixboy.version). The current game opens the **training
hub** by default: Unarmed and Science, two levels each selected from the
character's current skill percentage, with five authored options per level.
The shipped catalog maps Unarmed to **Filesystem** and Science to
**Networking**. Track labels, skill bands and additional levels are data in the
editable catalog, rather than a two-track engine limit.
Each level can set `reward_points` (1–300, default 5); earned amounts stay fixed
after completion even when the catalog changes.
Read [training setup and controls](TRAINING.md) first. The original ephemeral
shell and configured integer bindings remain available with `training=0` in
`nixboy.ini`. The foundation description below applies to that legacy mode;
training owns resumable attempts and uses trusted completion receipts instead.

## Terminal appearance

The default terminal is a centered metal-framed window, leaving the surrounding
game visible. Set `fullscreen=1` in the `[nixboy]` section of `nixboy.ini` to fill
the game's internal screen instead. This setting controls the in-game modal;
the game's own operating-system display mode stays in its existing configuration.

`retro_terminal=1` (the default) enables rounded curved green glass, broad bloom,
phosphor persistence and subtle tube noise/flicker. Set `retro_terminal=0` for a flat terminal with its
original ANSI colors. The flags are independent, accept `1`/`true` or `0`/`false`,
and reload when the terminal opens. F10 and the unwarped Close button remain local.
The CRT is an original software effect; it does not embed cool-retro-term or Qt.

The independent effect controls also reload on opening:

```ini
crt_warp=100
crt_glow=20
crt_persistence_ms=120
```

Warp and glow accept integers 0–100; 100 matches the previous preview's strength.
Persistence accepts 0–2000 milliseconds, the time for old light to fade to one
tenth intensity. Zero disables the corresponding effect. The defaults reduce
the previous glow to 20% and shorten its 600ms trail to 120ms. Invalid values
report a configuration error. Settings are ignored visually in flat mode.

The frame loads the installed game's interface FRMs through its normal art
cache: FRM 171 (`AUTOMAP.FRM`) supplies one coherent glass housing.
Corner/bevel thickness remains native while long edge runs extend continuously,
with no repeating panel tiles. Close uses the game's original
red button resources. The Pip-Boy label borrows painted ochre strokes from
FRM 127 (`PIP.FRM`); missing letter shapes are composed from those strokes. Modified or
localized art falls back to a native game font. No original/extracted game
graphics are embedded or packaged.

Connection/error notices and control hints appear inside the terminal. The
frame carries only the title and Close control; no status/footer panel is reserved.

The terminal's font bitmap derives from the openly licensed Departure Mono,
also selected by the reference green profile. See
[font attribution and license](../../third_party/departure-mono/README.nixboy.md).
Native Fallout fonts still draw the game chrome.

nix-boy adds an in-game terminal backed by Debian Linux containers.
The host bridge uses the official Docker Python SDK, a real Bash PTY and a separate
structured control channel. The game supplies integer context; Linux helpers send
named integer results. Only explicit engine bindings can affect game globals.
See [protocol.md](protocol.md) for the wire format and trust boundaries.

## Status and prerequisites

The following October 21, 2026 observations describe the terminal foundation,
before current-skill bands and detachable training were added. All 33 Python
tests passed under WSL Python 3.14.4. Native
Windows Python 3.12.14 passed 30 tests with three Linux-only tests skipped. These
cover fake-Docker lifecycle and socket streams, authentication before Docker
operations, protocol bounds, startup failure, disconnection, graceful shutdown,
context, events, reserved exit names, actual exit status and Linux control sockets.
The actual Docker smoke also passed with Docker Engine 29.9.2 in WSL, including
PTY I/O/signals/resize, context/results/exits, isolation, quiet/repeated sessions,
failed-start recovery and close/disconnect/shutdown cleanup. The native Windows
C++ client reached that bridge through Windows localhost and passed PTY/context/
result/exit checks with every created container removed. A separate Windows game
copy also passed the Pip-Boy button/hotkey, shell/editor controls, configured
integer results, actual shell exits, visible karma effect and save/reload checks.
The test used temporary reserved globals and a karma binding; these are not
defaults or a claim that arbitrary quest globals are safe for other mods.

The tested workstation deployment uses the existing `Ubuntu-26.04` Docker Engine;
Docker Desktop is not required for that route. Native Windows named pipes remain
untested. Docker is not installed or configured by this feature. The bridge
needs Python 3.10+ and dependencies in [requirements.txt](requirements.txt):
[Docker SDK 7.2.0](https://pypi.org/project/docker/7.2.0/) and, on Windows,
[pywin32 312](https://pypi.org/project/pywin32/312/). The SDK's
[exec and socket APIs](https://docker-py.readthedocs.io/en/stable/api.html)
are the interface for container inspection and PTY attachment. Training invokes
Docker Compose with bounded argument arrays for its environment topology; the
legacy free-shell mode uses the SDK alone. Neither mode invokes a host command
shell from game requests.

Use an already configured Docker Desktop in Linux-container mode on Windows, or
Docker Engine accessible through a local Unix socket on Linux/WSL. The bridge
defaults to `npipe:////./pipe/docker_engine` on Windows and
`unix:///var/run/docker.sock` on Linux. `--docker-endpoint` can select another
local named pipe or Unix socket. Remote SSH/TCP endpoints and `DOCKER_HOST` are
deliberately unsupported; Docker contexts are not inherited. Start the daemon
yourself, then build the image explicitly.

## Alternative: native Windows bridge with Docker Desktop

From the installed game/package root in PowerShell, using a normal local CPython installation:

```powershell
py -3 -m venv nixboy/.venv
nixboy/.venv/Scripts/python.exe -m pip install -r nixboy/requirements.txt
docker build -f nixboy/container/Dockerfile -t fallout2-nixboy:dev nixboy
nixboy/.venv/Scripts/python.exe nixboy/smoke_docker.py --run
```

Use a separate Fallout 2 CE test installation containing the mod executable and
original required assets. Keep the clean installation untouched. The bridge
creates a token if the file does not already exist; keep it private to your user.
It must be exactly 64 hex characters with no newline. Start the bridge with the
token path inside that test installation, then configure the game to read the same
file. Substitute your test directory in this example:

```powershell
$testDir = 'C:\Games\Fallout2-NixBoy-Test'
nixboy/.venv/Scripts/python.exe nixboy/bridge.py --token-file "$testDir/nixboy.token" --content-dir "$testDir/nixboy/content"
```

The bridge runs in this terminal until Ctrl+C. It binds only `127.0.0.1` and never
prints the token. Windows token privacy follows the directory's ACL; use a private
test directory. Avoid shared directories and source control. The file suffix
`.token` is ignored in this tooling directory, but tokens placed elsewhere need
equivalent protection.

## Linux and WSL setup

In WSL with a working local Linux Docker socket, from the installed game/package root:

```sh
python3 -m venv nixboy/.venv
nixboy/.venv/bin/python -m pip install -r nixboy/requirements.txt
docker build -f nixboy/container/Dockerfile -t fallout2-nixboy:dev nixboy
nixboy/.venv/bin/python nixboy/smoke_docker.py --run
mkdir -p "$HOME/.local/state/nixboy"
chmod 700 "$HOME/.local/state/nixboy"
nixboy/.venv/bin/python nixboy/bridge.py --token-file "$HOME/.local/state/nixboy/game.token" --content-dir "$PWD/nixboy/content"
```

Do not share a virtual environment between Windows and Linux. Create it on the
platform that runs the bridge. POSIX token files must have no group/other access
(normally mode 0600). WSL `/mnt/c` mounts without metadata can report permissive
modes even for files created with mode 0600, so keep the bridge token on the Linux
filesystem. For a Windows game connecting to a WSL bridge, privately copy the same
64 bytes into the Windows test directory without printing them, and point the
game's `token_file` there. For example, while the WSL bridge is stopped:

```sh
cp "$HOME/.local/state/nixboy/game.token" /mnt/c/Games/Fallout2-NixBoy-Test/nixboy.token
```

The Windows-game-to-WSL route was verified with the shipped native C++ client.
It uses Windows-to-WSL localhost forwarding; it does not expose the Docker daemon
over TCP. Forwarding can appear shortly after the WSL bridge announces its Linux
listener, so let the listener become reachable before opening nix-boy. A transient
connect failure is visible in the modal and can be retried by closing/reopening.
Do not expose either service on a LAN. Keep token copies private and identical.

The installed layout is `fallout2-ce.exe`, `nixboy.version` and a `nixboy/`
directory beside them. Edit `nixboy/content/tracks.yaml` and the YAML files
under `nixboy/content/challenges/` to change the active catalog, then restart
the bridge. `--content-dir PATH` selects another catalog; the default is the
`content/` directory beside `bridge.py`. Keep `--state-dir` unchanged when
editing content or upgrading. [Content authoring](content/README.md) explains
stable IDs, knowledge labels and adding bands with existing task kinds.

In a source checkout, the same files are authored under `tools/nixboy/`.
The package copies them into `nixboy/`; changes made in the installed copy do not
silently modify the source catalog. Preserve local content edits before replacing
that runtime folder with a newer package.

To reproduce the Windows transport check without game assets, build the optional
`nixboy-live-probe` target in the standalone `tests` project for Windows, or use
the probe included in the test package. From native Windows Python:

```powershell
py -3 nixboy/smoke_windows_wsl.py --run --probe .\nixboy-live-probe.exe --wsl-python /path/to/linux/venv/bin/python
```

Substitute the Linux Python environment where you installed the requirements.
The supervisor starts a temporary WSL bridge, waits up to five seconds for
Windows localhost readiness, runs the unchanged C++ client, and stops the bridge
and verifies its containers were removed. Authentication tokens exist only in
memory and anonymous stdin pipes. The probe does not launch Fallout 2.

## Game binding and Linux helpers

The game reads `nixboy.ini` from its working directory. Enable `[nixboy]` with
`enabled=1`, `port=47821`, and `token_file=nixboy.token`. Use the root
[`nixboy.ini.example`](../../nixboy.ini.example) as the starting configuration.
Results are inert until
explicitly mapped in `[bindings]`. Choose game globals that your mod owns;
existing base-game globals can affect quests. The engine checks bounds before
applying them. A schematic configuration is:

```ini
[nixboy]
enabled=1
hotkey=Ctrl+N
port=47821
token_file=nixboy.token

[bindings]
; Use actual globals allocated by your mod:
; challenge_result=700
; terminal_exit=701
```

`hotkey` selects the opening shortcut; omitted means `Ctrl+N`. Values are
case-insensitive, with optional surrounding whitespace:

| Value | Meaning |
| --- | --- |
| `Ctrl+A` through `Ctrl+Z`, except H/I/M | Control plus one letter |
| `Alt+A` through `Alt+Z` | Alt plus one letter |
| `A` through `Z` | One letter, matching either case |
| `F1` through `F12` | One function key |
| `none` | Disable the shortcut; retain the Pip-Boy button |

The selected shortcut takes precedence over an existing game action in gameplay
and the Pip-Boy. Choosing bare `N` therefore overrides attack-mode cycling;
the default avoids that conflict. Combined modifiers, modified function keys,
numeric key codes and empty values are invalid. An invalid value disables the
shortcut and is logged; the button reports the configuration error. Restart the
game after changing `hotkey` or `enabled`, because shortcut configuration is
cached for the process.

Ctrl+H, Ctrl+I and Ctrl+M are rejected because the game's input layer shares
their key codes with Backspace, Tab and Enter.

Click nix-boy on the Pip-Boy's left button row, or use the configured opening
shortcut in gameplay or the Pip-Boy (default Ctrl+N). Plain N keeps its normal
game action with the default configuration. F10 or the modal Close button
leaves nix-boy. Esc and Ctrl+C go to Linux. In training mode F5–F8 control
Check, Hub, Reset and Abandon; in free-shell mode those keys reach Linux.
The engine terminal supports ASCII and box drawing;
unsupported Unicode characters appear as `?`.

For disposable test runs, launch this feature executable with
`--debug-new-game=narg` (also `chitsa` or `mingun`) to start a fresh original
premade without the main menu, character selection or MVE playback. Use
`--debug-no-movies` for menu/selection tests. These switches are opt-in and do
not save/load automatically or fix the RNG seed. Slideshow images and dialogue
are separate from MVE playback. Avoid using debug runs for production saves.

The engine snapshot includes `game.time_ticks`, `game.map`, `player.tile`,
`player.elevation`, and `player.hp`, plus configured `global.NAME` bindings.
Opening the terminal supplies the current values. Helpers can read the live map;
`NIXBOY_CONTEXT_JSON` is a startup environment snapshot and
`NIXBOY_CONTEXT_PATH` points at the atomically replaced current JSON file.

Configured binding names are case-sensitive and at most 57 characters, leaving
space for `global.` in the outgoing context. At most 120 bindings are supported.

Inside nix-boy:

```sh
nixboy context
nixboy context player.hp
nixboy emit challenge_result 5
nixboy run challenge_result bash -c 'test "$(nixboy context player.hp)" -gt 50'
exit 23
```

`emit` sends a named signed 32-bit integer. `run NAME COMMAND...` runs argv inside
Linux without an implicit shell, emits its exit status, and exits with that
status. Use `bash -c '...'` explicitly for Linux shell syntax. Missing commands
produce 127, permission errors 126, signals use Bash-style `128 + signal`, and
control failures produce 125. Names match
`[A-Za-z_][A-Za-z0-9_.-]{0,63}`. `terminal_exit` is reserved for the actual final
Bash exit status and cannot be emitted by helpers.

## Isolation, extension and cleanup

Each opening creates a new container as UID/GID 1000 with a read-only root,
writable ephemeral home (64 MiB) and `/tmp` (16 MiB), 256 MiB memory/no extra swap,
one CPU quota and 128 processes. All capabilities are dropped and
`no-new-privileges` is set. No host directory, game asset, device, port, or Docker
socket is mounted. Container data is discarded on close. This is Linux userspace
using Docker's kernel, with shell tools, Python, editors, curl and process tools;
it is not a full VM or a systemd session. Runtime package installation as root is
unavailable. Add required tools in a derived image at build time and select it
with the operator's `--image` argument.

Networking defaults to `--network none`. An operator can explicitly choose
`--network bridge` to permit ordinary outbound container networking. Game requests
cannot change that policy. Docker access grants substantial host privileges to
the bridge process, so run only trusted bridge code and operator-selected images.

The Debian base is pinned to the multiarch digest recorded in
[Docker's official image metadata](https://github.com/docker-library/repo-info/blob/master/repos/debian/remote/bookworm-slim.md)
on October 21, 2026. Apt packages still follow Debian's current repository at build
time. Record the resulting image ID/digest for a tested release and use
`--image IMAGE@sha256:DIGEST` for repeatable deployment. Updating the base or
dependencies requires rebuilding and rerunning the smoke test.

CLOSE, game disconnect, startup failure and stream failure force-remove the
container. Graceful Ctrl+C waits up to 35 seconds for active cleanup. Docker API
operations have a ten second timeout; control acknowledgements have five seconds.
Container IDs are recorded from the create response before any follow-up API
request. Host Docker proxy configuration is not injected into the container.
If Docker itself is unavailable, removal may fail; the bridge logs the container
ID when known. A daemon losing the initial create response can leave an unknown
ID, and a hard-killed bridge cannot guarantee cleanup. After Docker recovers, inspect
`docker ps -a --filter label=org.fallout2-ce.nixboy=session`, then remove only the
listed stale nix-boy IDs with `docker rm -f CONTAINER_ID`. Never run a global prune
as feature cleanup.

## Validation

With the requirements installed and without Docker, from the source engine worktree:

```sh
python -m unittest discover -s tools/nixboy/tests -v
```

For real Docker, build the image as above, install the pinned requirements into a
virtual environment, then run `python nixboy/smoke_docker.py --run`. It checks
Linux PTY input/output, Ctrl+C, resize, initial/live context, named results, command
exit codes, final shell status, event delivery before exit, a 12-second quiet
session, repeated sessions, failed-image startup/recovery, isolation and
close/disconnect/active-shutdown cleanup. Receive waits and collected output are
bounded. It never pulls images implicitly, installs Docker, or launches the game.
The WSL Docker/backend, Windows-client route and bounded game scenarios above
were verified on this workstation. Native MSVC, macOS, Docker Desktop and
non-ASCII font rendering remain outside those results. Canonical development
tracking and detailed acceptance evidence live in the project workspace's
`mods/nix-boy/` folder, separate from this redistributable operator guide.

The pinned SDK's [named-pipe implementation](https://github.com/docker/docker-py/blob/7.2.0/docker/transport/npipesocket.py)
and [socket helpers](https://github.com/docker/docker-py/blob/7.2.0/docker/utils/socket.py)
were source-checked: the bridge uses `recv_into` for named-pipe timeout support,
treats Windows pipe-ended error 109 as EOF, handles partial writes, and disables
the HTTP timeout on the long-lived PTY. Fake stream regression tests cover these
adaptations; native Docker Desktop execution remains pending. Live WSL tests
additionally verified that the owning SDK HTTP response is closed before its
raw SocketIO, avoiding Python 3.14 finalizer warnings on cleanup.
