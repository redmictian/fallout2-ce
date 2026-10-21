# Nix-Boy Docker bridge

Nix-Boy adds an in-game terminal backed by an ephemeral Debian Linux container.
The host bridge uses the official Docker Python SDK, a real Bash PTY and a separate
structured control channel. The game supplies integer context; Linux helpers send
named integer results. Only explicit engine bindings can affect game globals.
See [protocol.md](protocol.md) for the wire format and trust boundaries.

## Status and prerequisites

On October 21, 2026, all 31 Python tests passed under WSL Python 3.14.4. Native
Windows Python 3.12.14 passed 29 tests with two Linux socket tests skipped. These
cover fake-Docker lifecycle and socket streams, authentication before Docker
operations, protocol bounds, startup failure, disconnection, graceful shutdown,
context, events, reserved exit names, actual exit status and Linux control sockets.
The actual Docker smoke also passed with Docker Engine 29.9.2 in WSL, including
PTY I/O/signals/resize, context/results/exits, isolation, quiet/repeated sessions,
failed-start recovery and close/disconnect/shutdown cleanup. The native Windows
C++ client reached that bridge through Windows localhost and passed PTY/context/
result/exit checks with every created container removed. The game itself has not
been launched with this mod; Pip-Boy/input/rendering and save effects remain pending.

The tested workstation deployment uses the existing `Ubuntu-26.04` Docker Engine;
Docker Desktop is not required for that route. Native Windows named pipes remain
untested. Docker is not installed or configured by this feature. The bridge
needs Python 3.10+ and dependencies in [requirements.txt](requirements.txt):
[Docker SDK 7.2.0](https://pypi.org/project/docker/7.2.0/) and, on Windows,
[pywin32 312](https://pypi.org/project/pywin32/312/). The SDK's
[exec and socket APIs](https://docker-py.readthedocs.io/en/stable/api.html)
are the implementation interface. The bridge does not launch the Docker CLI or a
host command shell.

Use an already configured Docker Desktop in Linux-container mode on Windows, or
Docker Engine accessible through a local Unix socket on Linux/WSL. The bridge
defaults to `npipe:////./pipe/docker_engine` on Windows and
`unix:///var/run/docker.sock` on Linux. `--docker-endpoint` can select another
local named pipe or Unix socket. Remote SSH/TCP endpoints and `DOCKER_HOST` are
deliberately unsupported; Docker contexts are not inherited. Start the daemon
yourself, then build the image explicitly.

## Alternative: native Windows bridge with Docker Desktop

From the engine worktree in PowerShell, using a normal local CPython installation:

```powershell
py -3 -m venv tools/nixboy/.venv
tools/nixboy/.venv/Scripts/python.exe -m pip install -r tools/nixboy/requirements.txt
docker build -f tools/nixboy/container/Dockerfile -t fallout2-nixboy:dev tools/nixboy
tools/nixboy/.venv/Scripts/python.exe tools/nixboy/smoke_docker.py --run
```

Use a separate Fallout 2 CE test installation containing the mod executable and
original required assets. Keep the clean installation untouched. The bridge
creates a token if the file does not already exist; keep it private to your user.
It must be exactly 64 hex characters with no newline. Start the bridge with the
token path inside that test installation, then configure the game to read the same
file. Substitute your test directory in this example:

```powershell
$testDir = 'C:\Games\Fallout2-NixBoy-Test'
tools/nixboy/.venv/Scripts/python.exe tools/nixboy/bridge.py --token-file "$testDir/nixboy.token"
```

The bridge runs in this terminal until Ctrl+C. It binds only `127.0.0.1` and never
prints the token. Windows token privacy follows the directory's ACL; use a private
test directory. Avoid shared directories and source control. The file suffix
`.token` is ignored in this tooling directory, but tokens placed elsewhere need
equivalent protection.

## Linux and WSL setup

In WSL with a working local Linux Docker socket, from the engine worktree:

```sh
python3 -m venv tools/nixboy/.venv
tools/nixboy/.venv/bin/python -m pip install -r tools/nixboy/requirements.txt
docker build -f tools/nixboy/container/Dockerfile -t fallout2-nixboy:dev tools/nixboy
tools/nixboy/.venv/bin/python tools/nixboy/smoke_docker.py --run
mkdir -p "$HOME/.local/state/nixboy"
chmod 700 "$HOME/.local/state/nixboy"
tools/nixboy/.venv/bin/python tools/nixboy/bridge.py --token-file "$HOME/.local/state/nixboy/game.token"
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
listener, so let the listener become reachable before opening Nix-Boy. A transient
connect failure is visible in the modal and can be retried by closing/reopening.
Do not expose either service on a LAN. Keep token copies private and identical.

To reproduce the Windows transport check without game assets, build the optional
`nixboy-live-probe` target in the standalone `tests` project for Windows, or use
the probe included in the test package. From native Windows Python:

```powershell
py -3 tools/nixboy/smoke_windows_wsl.py --run --probe .\nixboy-live-probe.exe --wsl-python /path/to/linux/venv/bin/python
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
port=47821
token_file=nixboy.token

[bindings]
; Use actual globals allocated by your mod:
; challenge_result=700
; terminal_exit=701
```

Click NIX-BOY on the Pip-Boy's left button row, or press N while in the Pip-Boy.
F10 or the modal Close button leaves Nix-Boy. Esc and Ctrl+C go to Linux, as do
the other function keys. The engine terminal supports ASCII and box drawing;
unsupported Unicode characters appear as `?`.

The engine snapshot includes `game.time_ticks`, `game.map`, `player.tile`,
`player.elevation`, and `player.hp`, plus configured `global.NAME` bindings.
Opening the terminal supplies the current values. Helpers can read the live map;
`NIXBOY_CONTEXT_JSON` is a startup environment snapshot and
`NIXBOY_CONTEXT_PATH` points at the atomically replaced current JSON file.

Configured binding names are case-sensitive and at most 57 characters, leaving
space for `global.` in the outgoing context. At most 120 bindings are supported.

Inside Nix-Boy:

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
listed stale Nix-Boy IDs with `docker rm -f CONTAINER_ID`. Never run a global prune
as feature cleanup.

## Validation

Without Docker or third-party packages, from the engine worktree:

```sh
python -m unittest discover -s tools/nixboy/tests -v
```

For real Docker, build the image as above, install the pinned requirements into a
virtual environment, then run `python tools/nixboy/smoke_docker.py --run`. It checks
Linux PTY input/output, Ctrl+C, resize, initial/live context, named results, command
exit codes, final shell status, event delivery before exit, a 12-second quiet
session, repeated sessions, failed-image startup/recovery, isolation and
close/disconnect/active-shutdown cleanup. Receive waits and collected output are
bounded. It never pulls images implicitly, installs Docker, or launches the game.
The WSL Docker/backend and Windows-client route are runtime-verified at the
recorded configuration; in-game acceptance is still required. See the root
`MODLOG.md` for exact evidence and remaining work.

The pinned SDK's [named-pipe implementation](https://github.com/docker/docker-py/blob/7.2.0/docker/transport/npipesocket.py)
and [socket helpers](https://github.com/docker/docker-py/blob/7.2.0/docker/utils/socket.py)
were source-checked: the bridge uses `recv_into` for named-pipe timeout support,
treats Windows pipe-ended error 109 as EOF, handles partial writes, and disables
the HTTP timeout on the long-lived PTY. Fake stream regression tests cover these
adaptations; native Docker Desktop execution remains pending. Live WSL tests
additionally verified that the owning SDK HTTP response is closed before its
raw SocketIO, avoiding Python 3.14 finalizer warnings on cleanup.
