# Nix-Boy development workspace

Development tracking, decisions, build history and acceptance evidence now live
in `../MODLOG.md`, the parent `mods/nix-boy/` workspace. This file is a
migration pointer; keep future mod tracking in that parent mod folder.

Redistributable runtime setup and protocol instructions remain in
[tools/nixboy/README.md](tools/nixboy/README.md). The canonical mod folder also
contains this `source/` worktree, its `knowledge-base/` worktree, the ignored
disposable game copy in `tests/game/` and local artifacts in `out/`. Shared process
guidance is in the project's `game-docs/MODDING.md` and `game-docs/TESTING.md`.
Keep original game assets, saves and authentication tokens out of Git.
