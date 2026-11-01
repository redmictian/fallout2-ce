# Current-completion passive perk reward

The retained test-only pilot advances owned inert Toughness (engine ID12) from
raw rank1 to2, adding10 damage-resistance points through existing perkAddForce.
Maximum-rank/ownership checks remain native. Tag!, Mutate! and immediate/choice
variants remain excluded; no permanent curriculum mapping is decided.

## Delivery and duplication

The game sends training.version=5 with native skill/perk claim ledgers and current
owned ranks. The existing typed reward22 contains profile16, perk:uint16,
target_rank:uint8 and receipt16; ACK10 adds accepted:uint8. Profile is the current
live assessment run. No snapshot/activation/capture protocol is used.

The bridge keeps ONE current-completion slot, not an earned-perk history. New
eligibility always reads current inert ownership/rank; an external record cannot
unlock or prevent an upgrade. Completion must follow the real trusted assessment.
Exact lost-ACK retries are possible only for that slot in the same run. Accepted
ACK or an exact current game claim suppresses further delivery. Unknown/new run
never imports old receipts or scans missing external rewards.

TrainingState::acceptPerk returns Rejected/New/Duplicate. Only New calls production
perkAddForce; the candidate native ledger is published after successful effect.
Wrong current run, unowned/already advanced/capped rank, conflicting/cross-kind
receipt or unsupported perk is rejected. Exact duplicates acknowledge without
applying another rank/effect. Native perk rank/effect remains in normal game state.

## Ordinary saving/loading

Saving needs no bridge, Docker, QEMU or associated educational file. The existing
NXBTRAIN writer remains v4 without perk claims and v5 with them; loaded native
claim/bonus data stays in that save. The compatibility reader also accepts old
v6 trailers, ignores obsolete32-reference bytes and rewrites v4/v5. Earlier 0.7.0
binaries cannot read perk-bearing v5 saves; keep candidate saves separate.

Loading marks the existing assessment identity for renewal on next open while
retaining the native loaded rank/claims. Later external completion slots cannot
replay into it. A rolled-back owned rank1 can earn a new receipt through a fresh
genuine assessment. Ordinary reopen without load preserves the same active run.
See CLEANUP.md for current task-state limits and canonical project restart policy.

The read-only save inspector and acceptance relay are developer tools in source,
not player-package dependencies. Actual game effect/save results are recorded
separately from automated/native and headless backend checks.
