# Clean candidate persistence and scope

This candidate keeps the existing game save system. Saving performs no bridge,
Docker or QEMU operation and writes no external progression reference.

The existing game-owned training ledger supplies skill bonuses and duplicate
protection; native perk ranks/effects use the normal character records. New
training trailers remain v4 without perk claims and v5 with them. V1–v5 readers
remain, with a small v6 compatibility reader that ignores the obsolete 32-byte
reference. No associated educational file is consulted. Old v6 saves are rewritten
as v4/v5 on the next ordinary save. The 1.2T perk-training policy is unchanged.

## Assessment lifetime

The preexisting opaque training profile now identifies the live assessment run,
not a durable achievement history. The ordinary load/reset hook marks it for
renewal through existing ensureProfile() on the next terminal opening. This does
not generate randomness during save/load and adds no new save fields or protocol.
Reopening nix-boy without loading keeps the same run and active lab. A new run
removes the previous provider's scoped attempt and pending result when next opened.

The existing manager's runtime.json retains only current attempt/question/checkpoint
state, alternative queues, a current project cursor and one completion delivery
slot. It never imports training.json achievements, earlier receipt maps or game
claims as externally earned progress. Current character claims are observations
for eligibility/duplicate checks, not another progression database.

A passed challenge creates one pending receipt. Exact retry/duplicate ACK is safe;
accepted delivery is not replayed. Lost ACK can retry only in that same live run;
reopen reconciles that single pending result against the character's actual claims.
Loading starts a fresh run and discards that old pending slot. Current game skills,
saved claim/bonus data and owned perk ranks decide which advancement is available.
A rolled-back reward is earnable again by genuinely completing the challenge.

## Retained assessments and projects

Bounded theory drafts, whole-set commitment, trusted keyed grading, delayed
feedback and explicit retry remain. Mixed assessments still require actual P AND K
and final practical revalidation. Failed alternatives defer; successful assessment
alternatives are consumed within the current run. No global completion/ALL graph,
independent educational prerequisites or achievement history remains.

A canonical project keeps only its current ID/next-stage cursor. Stage one removes
its environment and stage two prepares independently; reset affects the current
stage. That cursor can survive provider restart in the same live game run, but a
game load starts the project again. Cross-load project progress and independent
reward-free curriculum prerequisites are deliberately deferred, rather than adding
another save mechanism. Course YAML accepts only same-project predecessor links;
other prerequisites and ALL parents are rejected explicitly.

Docker remains the external management interface. Ordinary labs remain direct
containers. Optional QEMU/native service profiles retain their existing confined
runner, console, immutable bases, overlays, trusted probes and cancellation. No
QEMU image/base/tool is needed to save or use ordinary progression.

## Operation and recovery

Use a fresh candidate state directory and matching candidate game/bridge. Old
player states, snapshot directories and review branches are not imported or
modified. runtime.json is operational task state, not needed to load a game save.
If it is unavailable/corrupt, preserve it and start a fresh candidate state; the
character's saved rewards remain in the game. A practical facility is reissued
canonically without a learner-failure reward. No live process/VM state is restored.

Use scoped provider stop/cleanup only. Keep original saves/config/token private.
No release/tag promotion, permanent educational mappings or cross-save mode is
part of this candidate. Developer qualification/acceptance scripts stay in source,
not in the player package. The retained test-only Toughness1→2 mapping is still
not a permanent curriculum decision.
