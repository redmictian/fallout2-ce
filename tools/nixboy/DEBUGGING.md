# nix-boy debug launch guide

These are explicit engine launch options for disposable testing. They are
available in nix-boy 0.7.0; earlier executables may support only the original
new-game/movie options. `--debug` by itself is not an option. Debug launch does
not require a Debug compiler build, Docker, a bridge or an authentication token.
Wiki reading retains its normal content/WebView2 requirements.

Close the previous Fallout CE instance, then launch from a separate owned-data
game copy. The engine's single-instance guard can exit successfully before
parsing arguments when another instance exists. Check the new PID, screen and
log rather than relying on the launcher's exit code.

## Options

| Option | Behavior |
| --- | --- |
| `--debug-no-movies` | Keep normal menus and suppress MVE playback, including idle attract and later movies |
| `--debug-new-game=narg` | Fresh Narg, skip menus/character selection and all MVE playback; also accepts `chitsa` and `mingun` |
| `--debug-new-game-level=N` | Advance that fresh character to level 1-99 through normal XP/HP processing; requires a new-game or review option |
| `--debug-perks=all` | Fresh Narg; grant all 76 selectable perks at raw rank one; open the character screen on the first perk card |
| `--debug-perk-picker` | Fresh level-three Narg; open the genuine level-up picker without pregranted perks |
| `--debug-perk=ID:RANK` | Fresh Narg; force a selectable perk to the requested raw rank and open its card; enforce its real maximum |

An explicit `--debug-new-game=chitsa` or `mingun` overrides the review character.
An explicit level overrides the picker's level-three default. Original first-rank
prerequisites and Skilled's perk frequency still apply in the real picker;
choose level four for a premade with Skilled. Gallery/focused grants bypass
prerequisites. Their raw rank includes the harmless unlock rank in the standard
build. Disabled, equipment, withdrawal and world-reward IDs are not review targets.

Use only one perk review mode. Duplicate/conflicting options, malformed numbers,
levels outside 1-99, unsupported characters, nonselectable IDs, and ranks outside
1-4 are rejected. Perk-specific maxima are checked after initialization and before
granting: Survivalist cannot exceed rank two, for example. Unrelated engine/config
arguments remain supported. Option order is independent.

## Examples

From the game directory in PowerShell:

```powershell
./fallout2-ce.exe --debug-perks=all
./fallout2-ce.exe --debug-perk-picker
./fallout2-ce.exe --debug-perk=12:1  # Toughness unlock, no resistance bonus
./fallout2-ce.exe --debug-perk=12:2  # Toughness, +10 normal resistance
./fallout2-ce.exe --debug-perk=12:4  # Toughness, original +30 ceiling
./fallout2-ce.exe --debug-perk=16:2  # Survivalist, +25 Outdoorsman
./fallout2-ce.exe --debug-new-game=narg --debug-new-game-level=24
./fallout2-ce.exe --debug-no-movies
```

Useful IDs: Awareness 0; Earlier Sequence 6; Toughness 12; Survivalist 16;
Educated 18; Lifegiver 28; Tag! 51; Mutate! 52; Here and Now 92. Engine IDs are
stable indexes in `src/perk_defs.h`, not positions in the alphabetized picker.

Press Done to return to the world. Gallery/focused review defers a pending perk
opportunity until a normal character-screen visit. Normal play follows after the
review screen closes. F10/Yes returns to the main menu; Exit there closes the
process. The review flags never load or save automatically. Manual saves are real
saves and use the build's normal compatibility policy: standard 0.7.0 is 1.2T and
cannot read ordinary 1.2R saves. Use new characters and separate save directories.

## Logging and evidence limits

In the test copy's `fallout2.cfg`, use `[debug] mode=log` to write `debug.log`.
The game truncates this file at startup, so copy useful evidence before relaunch.
Each review grant logs ID/name, raw/benefit ranks, normal selection eligibility,
maximum rank, and changed stat/bonus-stat/skill/XP/point values. The gallery
compares stats, stored bonuses, skills, PC stats, tags and traits before/after
each grant; it stops on a rank-one change in the standard training build.

Snapshots do not exercise every later combat action, script branch, lighting
transition or historical reward. A forced Tag!/Mutate! upgrade does not run the
original choice dialogs; a reward adapter must supply those choices. Perk review
does not pretend to complete a nix-boy challenge or award a trusted receipt.

Implementation: `src/debug_launch.cc` parses options, `src/main.cc::mainNewGame`
uses normal new-session setup/teardown, `src/debug_perks.cc` grants and records
snapshots, and `src/character_editor.cc` focuses the requested card. Parser and
production grant fixtures live under `tests/`. Gameplay evidence belongs in the
mod workspace's `archive/`, separately from this portable usage guide.
