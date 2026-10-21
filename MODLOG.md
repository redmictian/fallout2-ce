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

Implementation is complete as a test build; live runtime acceptance is pending.
Docker was not available and no new Docker installation was authorized during
implementation. Follow `tools/nixboy/README.md` to install pinned Python
dependencies, build the image and run `smoke_docker.py --run`. This must establish
real Linux PTY interaction, Ctrl+C, resize, context/results, actual exits and
container removal on the selected Docker Desktop/WSL route. Test both quiet
sessions and graceful service shutdown on Windows named pipes. Hard process kills
or a lost Docker create response can still leave a labeled container; recovery
is documented and cleanup never prunes unrelated containers.

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
