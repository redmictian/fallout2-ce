# Nix-Boy foundation

Goal: an opt-in in-game Linux PTY reachable from the Pip-Boy, with a separate,
bounded, authenticated channel for signed integer parameters/results. Fallout 2
CE only; no JA2 integration or proprietary assets.

Branch: `mod/nix-boy`. Base: clean upstream `main`,
`e97087b9582f37075db347a89898887320753f8b`.
Worktree: sibling `fallout2-ce-nix-boy`. Original `fallout2-ce` stays on `main`.
Read the parent project specification/build notes and this log before continuing.

## Decisions

- Desktop engine build option; runtime disabled unless `nixboy.ini` enables it.
- A real libvterm terminal in a modal game window. Local F10/close button;
  terminal Esc and control keys belong to Linux.
- Loopback-only TCP bridge with per-installation token; fixed framed protocol.
  The game performs bounded nonblocking I/O. Docker work stays out of the game.
- Python host service controls an explicitly chosen Docker image; per-open
  unprivileged ephemeral container, resource limits, no host mounts and default
  network `none`. A container is Linux userspace sharing the Docker host kernel.
- Structured control messages use a separate container Unix socket/exec stream.
  Terminal escape sequences and printed text never become game commands.
- Context includes map/time/player snapshot and starting values of configured
  globals. Only explicitly configured result names can update existing globals;
  no indexes, pointers, scripts or code arrive from Linux. Existing game global
  serialization persists applied values; terminal/container state is ephemeral.
- No default global indexes: these are mod/content-specific. No save format change.
- MinGW's documented `_MSC_VER` cleanup mismatch is corrected for the build.
  No experimental movie/audio patch is included.

## Acceptance and validation

Required: complete Windows cross-build; standalone client/VT regressions; bridge
protocol/control/lifecycle tests; reproducible live Docker smoke test; documented
owned-data gameplay test and limitations. Live Docker and gameplay results must
be recorded separately from protocol tests and source traces.

Initial environment: WSL compiler/Python/CMake/Ninja available after sandbox
escalation. Docker not found on either native Windows or WSL PATH, and default
Windows Docker install directory absent. Native MSVC remains pending. No modified
game launch or test-copy asset operation has occurred.

## Delivered implementation

- `636d8f6`: Windows directory cleanup prerequisite.
- `094369a`: authenticated Docker PTY/control bridge, container/helpers, pinned
  dependencies, LF entrypoints, Python regressions and live smoke procedure.
- This commit adds the opt-in Pip-Boy button/modal, nonblocking C++ client,
  explicit global binding policy, pinned MIT libvterm and standalone tests.
- Coordinated knowledge-base worktree: sibling `fallout2-ce-docs-nix-boy`, branch
  `docs/nix-boy` from `5e21f81196b3247a07397dd1b52a9421898dfe2e`. Canonical new
  analysis: `systems/ui-rendering/nix-boy.md`; affected navigation/coverage/backlog
  and build notes are reviewed there. Its commit records this source revision.
- No push, main-branch merge, game launch, asset copy or save modification.

## Executed validation (October 21, 2026)

WSL `Ubuntu-26.04`: CMake 4.2.3, Ninja 1.13.2, Windows target MinGW G++13.0.0,
native GCC/G++15.2.0, Python3.14.4. Native bundled Python3.12.14 also executed.

1. Full Windows x64 `RelWithDebInfo` builds with `FALLOUT_NIXBOY=OFF` and `ON`
   linked successfully using retained SDL2.26.1/zlib1.3.1 and current fpattern
   pin. Final formatted/error-path/socket revision was incrementally rebuilt.
   Executable imports only Windows system DLLs (including Winsock), no separate
   libgcc/libstdc++/SDL DLL. Existing upstream warnings and libvterm's ignored
   MinGW visibility attribute remain; the new client macro warning was fixed.
2. Standalone CTest **3/3 passed**: malicious/fragmented/coalesced client framing,
   signed bounds, slow-reader/backpressure/EOF/reconnect; VT cursor/color/edit/
   scroll/alternate-screen/UTF-8 state and keyboard modes; real C++ client talking
   to the actual Python bridge with fake Docker calls, including context/results,
   binary PTY bytes, resize, EXIT ordering and removal.
3. Python tests **30/30 passed in WSL**; native Windows **28 passed, 2 Linux-only
   Unix-socket tests skipped**. Includes authentication before Docker, partial
   startup/create-ID cleanup, resource policy, real byte streams, Unix control
   socket/helpers, context ack ordering, result flush, real reported shell exit,
   idle PTY timeout removal, named-pipe timeout/EOF/partial-write adaptations and
   graceful service shutdown. Docker SDK methods are faked; no daemon was used.
4. Transport ASan/UBSan passed in WSL; Windows transport cross-compilation with
   warnings-as-errors passed. clang-format19.1.7 dry-run/Werror passed for all new
   C++ and the changed Pip-Boy source; Python compileall, Bash syntax and Git
   whitespace checks passed. Formatter ran through existing user-local WSL uv.
5. `um kb search pipboy` and `um win --help` ran in the existing WSL Universal
   Modder installation. The search did not supply a Fallout-specific Pip-Boy
   implementation; engine checks use the source-backed project knowledge base.
   Windows capture/control setup and actual game verification remain pending.

Reproduce from WSL (substitute your dependency/output paths):

```sh
bash tools/build-nixboy-windows.sh /path/to/deps /path/to/build-win64 ON
cmake -S tests -B /path/to/build-tests -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build /path/to/build-tests --parallel 6
ctest --test-dir /path/to/build-tests --output-on-failure
python3 -m unittest discover -s tools/nixboy/tests -v
```

Actual Windows build/test outputs: parent `out/nixboy-win64` and
`out/nixboy-tests`. OFF logs/executable have `disabled` suffixes. Package with
`tools/package-nixboy.ps1 -Executable <built-exe> -Destination <new-directory>`
after committing. The explicit package allowlist contains no assets or tokens.

## Remaining runtime acceptance

The initial unavailable-Docker limitation above was resolved by the user's WSL
Engine setup. The live backend/Windows-client checks below now pass; in-game
acceptance remains pending. Native Docker Desktop/named-pipe execution was not
tested and is not required by the selected WSL deployment. Hard process kills or
a lost Docker create response can still leave a labeled container; recovery is
documented and cleanup never prunes unrelated containers.

Then use a new owned-data test installation with a working audio device. Back up
any test saves before launch. Check: disabled config hides the button; enabled
NIX-BOY/N opens; unavailable/bad-token/slow backend gives a closable error; shell
editing/Tab/arrows/Ctrl+C/Escape/fullscreen editor/focus/close/quit work; actual
shell EXIT applies only the reserved configured binding; unbound results and a
spoofed terminal_exit cannot change globals. Use mod-owned global indexes to
demonstrate a Linux result changing game behavior, then save/reload those globals.
Repeat open/close and confirm no container/window/input-state leaks. Record the
binary hash, image digest, game data/save provenance and actual observations.

Known scope: ephemeral session/files; no persistent Linux disk or scrollback UI;
ASCII/approximate box-drawing font with `?` for unavailable Unicode glyphs;
opening game snapshot, while protocol/client support future live context updates;
desktop implementation, mobile build disabled; native MSVC/macOS runtime pending.
Container defaults to network-none, unprivileged read-only root and bounded
resources; packages are supplied in a custom image. This is Linux userspace on
Docker's Linux kernel, not a VM/kernel or systemd session.

## Live WSL Engine follow-up (October 21, 2026)

Continued on `mod/nix-boy` from `73eb064` after the user installed Docker Engine
in the existing `Ubuntu-26.04` distribution. No Desktop installation, additional
distribution, daemon TCP listener or system network change was made here.
Verified regular-user client/server Docker 29.9.2, API 1.56 (minimum 1.40), Linux/amd64.
Python 3.14.4's isolated host environment uses docker 7.2.0, requests 2.34.2,
urllib3 2.8.0, certifi 2026.10.19, charset-normalizer 3.5.2 and idna 3.20.

Built the existing pinned Dockerfile successfully. Local image inspect ID:
`sha256:2aa3950887028ef0fdbeae64dbbe6205822b36198bc93c18fb9bebdf0ae758db`.
Buildx used config blob
`sha256:7e2543b00adc1080d3c1fdc4af408edaad567608d207d44599f028c3baf3768d`;
the inspected ID is the local image index, not that config blob. No image was
pushed or established as remotely pullable. Debian base remains pinned in the
Dockerfile; apt package resolution occurred at this image build.

The first actual Docker smoke passed its functional checks but exposed Python
3.14 HTTPResponse finalizer warnings. Fixed `Duplex.close` to wake readers, then
close the Docker SDK's owning response before raw SocketIO. A regression uses
real stdlib HTTPResponse/socket I/O; the subsequent live tests completed without
those warnings. **31 Python tests passed in WSL; 29 passed on Windows with two
Linux-only skips.**

Expanded live Linux smoke **passed**: actual PTY input/output, Ctrl+C recovery,
size changes, initial/live context, signed named results, command status7, final
shell status23, acknowledged results before EXIT, UID1000/read-only root/network
none/256MiB/no host binds, a 12-second quiet session, repeated exits0/7/42, missing
image startup and recovery, explicit CLOSE, disconnect and active service
shutdown. Each created container was removed. The shutdown assertion drains
already-buffered prompt output before requiring EOF. Receive time and output
collection now have explicit total budgets.

The optional native Windows x64 `nixboy-live-probe` uses the shipped C++ Client
against a temporary WSL bridge and the real Docker daemon. **Three successive
Windows-to-WSL-to-Docker runs passed** PTY24x80, context73, named result-19,
EXIT23 and exact owned-container removal. Tokens are generated in memory and
passed through anonymous stdin pipes, never command arguments/files/output.
WSL's Windows localhost listener can appear just after Linux bind; the test
supervisor waits at most five seconds for it, then launches the unchanged
250ms-connect-deadline client. The first immediate-connect attempt established
this startup timing issue before creating any container; no network setting or
engine timeout was changed.

Reproduce the live Linux check after building the image and installing the
pinned requirements into a Linux environment:

```sh
python tools/nixboy/smoke_docker.py --run
```

Build `nixboy-live-probe` from the standalone `tests` CMake project for Windows,
then from native Windows Python:

```powershell
python tools/nixboy/smoke_windows_wsl.py --run --probe PATH/nixboy-live-probe.exe --wsl-python /path/to/linux/venv/bin/python
```

The live probe is opt-in and is not registered as an automatic CTest/Docker run.
No modified game, proprietary assets, saves or local game configuration were
opened/changed by these checks. In-game rendering/input, actual mod-owned global
effects and save/reload still need the acceptance procedure above.

## Terminal text-input correction (October 21, 2026)

Root's in-game test of binary `7adfede` found that text-only Windows/SDL typing
worked for the first command but was discarded after Return. The modal retained
`skipText=true` from Return while ignoring its queued key release. Replaced that
blanket flag with a shared, tested input policy: suppress at most one matching
single-character duplicate of a forwarded special/control/keypad key; account
for translated ASCII from active Ctrl/Alt chords and localized keypad decimal;
clear on matching release, another keydown, focus loss or an overflowed batch.
Independent text and IME commits do not require a physical keydown.

Focused native WSL terminal CTest passed, covering three text-only commands
separated by Return down/up, unrelated text without release, UTF-8/IME commits,
control/special/keypad duplicate suppression, repeats and reset behavior.
clang-format19.1.7 dry-run/Werror and Git whitespace checks passed. This fix was
not built into the full game or launched by the fixing agent; the parent task
will rebuild and repeat the in-game acceptance test.

```sh
cmake -S tests -B out/terminal-tests -G Ninja
cmake --build out/terminal-tests --target nixboy-terminal-test
ctest --test-dir out/terminal-tests -R nixboy-terminal --output-on-failure
```
