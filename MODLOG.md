# Movie audio wait fix

## Goal and state

Fix Windows CE v1.3.0 freezes during cinematic playback, without skipping movies.
Branch: `fix/movie-audio-wait`; base: tag `v1.3.0` (`9fb917c`).
Worktree: `C:\Games\GameDevelopment\fallout2-ce-movie-fix`.
Runtime test copy: `C:\Games\Fallout2-CE-Clean`.

## Evidence and implementation

- User reproduced freezes at startup and after character selection. Logs stop at `iplogo.mve` and `elder.mve` respectively, after initialization completes.
- Six instruction-pointer samples from the live elder-movie hang include the repeated audio-cursor/status loop in `_MVE_sndSync` (release RVA `0xbf780`, `0xbf7ae`).
- Focus loss pauses SDL audio. The movie loop waits for cursor progress but does not pump focus events, preventing resume if it enters this state. This is a demonstrated deadlock mechanism; its role in the user's exact freeze still requires a runtime test.
- Process `_GNW95_process_message()` and yield 1 ms while waiting. Do not invoke full game input/tickers recursively.
- Separate portability correction: use `_WIN32`, not `_MSC_VER`, for the Windows FindClose implementation. Required by the MinGW cross-build.

## Build and validation

Native Microsoft Build Tools bootstrap stalled and was stopped; no working MSVC setup established.
Temporary diagnostic build route: Ubuntu WSL, MinGW-w64 GCC 13.2, CMake 4.2.3, Ninja. Build outputs in `/home/mictian/build/fallout2-movie/windows`, separate from source.
Pinned dependencies: SDL 2.26.1 from libsdl.org; zlib 1.3.1 from zlib.net/fossils. fpattern is fetched by upstream CMake.

Configure (in WSL; dependencies extracted under `$HOME/build/fallout2-movie/deps`):

```sh
cmake -S /mnt/c/Games/GameDevelopment/fallout2-ce-movie-fix \
  -B "$HOME/build/fallout2-movie/windows" -G Ninja \
  -DCMAKE_SYSTEM_NAME=Windows \
  -DCMAKE_C_COMPILER=x86_64-w64-mingw32-gcc \
  -DCMAKE_CXX_COMPILER=x86_64-w64-mingw32-g++ \
  -DCMAKE_RC_COMPILER=x86_64-w64-mingw32-windres \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DCMAKE_EXE_LINKER_FLAGS=-static \
  -DFETCHCONTENT_SOURCE_DIR_SDL2="$HOME/build/fallout2-movie/deps/SDL2-2.26.1" \
  -DFETCHCONTENT_SOURCE_DIR_ZLIB="$HOME/build/fallout2-movie/deps/zlib-1.3.1"
cmake --build "$HOME/build/fallout2-movie/windows" -j 6
```

Full Windows x64 executable build passed. PE imports checked: Windows system DLLs only, no external SDL/MinGW runtime DLL requirement.
Installed as `fallout2-ce-movie-fix.exe` beside the official executable, not replacing it.
SHA256: `25EA4CF114D9FB9686327603C3029095F6F7EE912CD5FF648E471C8BEF09EA2E`.
`ddraw.ini` has `SkipOpeningMovies=0`; prior config saved as `ddraw.before-movie-fix.ini`.

Regression test (Linux host console, no game/window):

```sh
cd /mnt/c/Games/GameDevelopment/fallout2-ce-movie-fix
root="$HOME/build/fallout2-movie"
g++ -std=c++17 -ffunction-sections -fdata-sections -I src \
  -I "$root/deps/zlib-1.3.1" -I "$root/windows/_deps/zlib-build" \
  -I "$root/deps/SDL2-2.26.1/include" \
  tests/movie_audio_wait_test.cc -Wl,--gc-sections -o "$root/movie_audio_wait_test"
"$root/movie_audio_wait_test"
```

PASS: stalled cursor resumes after event processing; already-ready and absent-buffer paths return without waiting. Same harness using the original v1.3.0 movie source fails at its watchdog, reproducing the missing-event-pump defect.
An attempted Windows standalone harness link did not complete because PE section retention requires additional unrelated stubs; the passing harness is Linux-only. The full game build targets Windows.

## Next user test

Close the old frozen process and launch `fallout2-ce-movie-fix.exe`. Check opening movies, New Game -> character -> Proceed (`elder.mve`), and focus loss/return during playback. Do not load original Sfall saves for this baseline test.
Game was not launched by the agent, per user preference. If it still freezes, inspect `debug.log` and sample the patched process with symbols; do not mark the root cause fixed until actual cinematic playback is verified.

## User finding — October 18, 2026

The machine had no working audio output. Connecting headphones made playback/game work. The user did not specify whether the successful run used the official or experimental executable. The sampled wait location was correct, but focus loss was not established as the trigger. The regression test covers a simulated focus deadlock, not missing-device recovery. Do not treat this patch as necessary or merge it on the basis of this result. Keep working audio connected for normal testing; investigate no-device handling separately if needed.
