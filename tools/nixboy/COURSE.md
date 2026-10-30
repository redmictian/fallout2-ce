# Optional educational catalog, schema 1

`course.yaml` is an optional trusted sidecar in an existing challenge-content
directory. Legacy schema-2 `tracks.yaml` and challenge definitions retain their
meaning. The default catalog has no course and preserves its skill-band behavior.
`content-assessment-test/` is a runnable integration fixture, not course authoring.

Educational gate IDs are stable lower-case identifiers (up to 64 characters).
They are not engine skill IDs, skill percentages, rewards or stage environments.
This slice adds no project-stage transfer, wiki authority or perk reward mapping.

```yaml
schema: 1
gates:
  - id: example.gate
    version: 1
    title: Example gate
    format: mixed
    prerequisites: [example.preparation]
    alternatives:
      - id: a
        title: Prepared alternative A
        instructions: Complete the practical task, then commit the answer set.
        challenge: existing.trusted-challenge-id
        questions:
          - id: q1
            type: choice
            prompt: Which declared condition is required?
            choices: [Both components, Either component]
            answers: ["1"]
            feedback: Practical and conceptual success are both required.
```

The example omits its prerequisite definition; a real catalog must define every
referenced ID. Unknown references, duplicate IDs/keys and cycles are rejected.

Gate formats are `practical`, `theory`, `mixed` and `all`. Practical alternatives
reference an existing trusted challenge ID and have no questions. Theory
alternatives have questions and no challenge, therefore need no lab. Mixed
alternatives have both. An `all` parent has `components: [id.one, id.two]` and no
alternatives; completion requires every component. It cannot be satisfied by a
parent-only marker. Prerequisites may reference either concrete or ALL gates.
Use explicit preparation dependencies; no implicit whole-track or reading locks.

Each alternative needs `id`, `title`, `instructions`; it may declare `challenge`,
`questions` and `checkpoints` as allowed by its format. IDs are scoped to a gate.
Challenge IDs referenced by any course alternative become **course-exclusive**
and are removed from legacy skill-band pools, preventing a practical-only reward
bypass. Keep separate legacy challenges if that band should remain selectable.
Becoming course-exclusive invalidates a previously selected unfinished legacy
attempt through its content fingerprint; already earned receipts are preserved.

Question fields are `id`, `type`, `prompt`, `answers` and `feedback`:

- `choice`: two to eight numbered `choices`, exactly one string key such as
  `answers: ["2"]`. Optional `option_feedback` must explain every choice.
- `number`: one or more exact finite numeric keys as strings. Decimal/exponent
  notation is normalized without floating-point rounding; expressions, infinities,
  NaN, oversized exponents and unsupported input are invalid, not incorrect grades.
- `text`: bounded keys with `normalization: exact`, `space` (default), or
  `space-casefold`. Whitespace normalization collapses runs and trims edges;
  casefold additionally ignores letter case. No fuzzy matching or prose grading.

Answers are at most 256 UTF-8 bytes. Gates are limited to 512, alternatives to 32
per gate and questions to 32 per alternative; the sidecar is at most 1 MiB.
An alternative's worst-case encoded grading result must fit 7 KiB, reserving
space for commitment identity in the manager's compact result record.
All schemas reject unknown fields. Keys and explanations are backend material:
never copy the sidecar into a learner environment or learner wiki export.

`CourseCatalog.public_questions()` is the only precommit question projection.
It returns ID, type, prompt and choices, never keys or feedback. Draft validation
normalizes only supplied known IDs; complete-set validation requires every ID.
Only the manager calls trusted `grade()` after explicit durable commitment.
Catalog grading itself is stateless; it does not create records, grant rewards,
run Docker, rotate queues or finalize attempts. Manager/session contracts govern
commitment, feedback, retries and final practical revalidation.

The current ordered observer is deliberately narrow: a `checkpoint-policy`
challenge may declare `checkpoints: [rollback, reapply]`. The manager binds trusted
observations to attempt/generation and enforces order. Adding an observer requires
reviewed runtime code; authored YAML never supplies executable validation code.

Fingerprints bind selected alternative content, question keys/feedback, the
referenced practical fixture and dependency graph structure/versions. Unchanged
selected alternatives survive pool growth. Materially changed unfinished evidence
must be reissued; an earned educational completion is not silently erased by a
content edit. Course rewards are separate: this integration catalog awards none.

## Runtime and retry rules

In the terminal hub, `C` lists educational gates; choose a gate number. `Q` opens
questions for the active attempt. The pure-theory path provisions no Docker
environment. For a practical fixture, `F6` returns from its shell to the hub;
`R` resumes the same attempt and its files. These controls reuse the existing
terminal rather than adding a browser-to-game assessment channel.

In the question view:

- `N` / `B` page between questions; `H` returns to the hub.
- Enter `<question-id> <answer>`, for example `q1 2`, to save an editable draft.
- Enter `COMMIT` to commit the **entire** set. Missing/invalid input does not
  commit, grade, reveal feedback, rotate a queue or remove an environment.
- After commitment answers are immutable; reconnect shows the same commitment
  and postcommit feedback. There is no hidden precommit answer check.
- `F5` submits the required final practical/conceptual combination. Mixed
  completion rechecks mutable practical invariants then; an earlier P pass is
  not a reusable stale certificate. P-only submission remains pending.

Ordered fixtures use `X rollback` and then `X reapply` from the hub after the
corresponding actual operation. A checkpoint is nonfinal and accepts no submitted
evidence file. Failed/incomplete intermediate work does not finalize an attempt,
rotate alternatives or clean up its environment.

After feedback, `T` explicitly starts a **fresh attempt**, preferring an unseen
alternative where available. Once all finite alternatives have been exposed,
the new attempt is honestly labeled a remediation retry, not unseen retention
evidence. No endless pool, forced wait or starvation is required. `F7` refuses
to reset already committed questions into editable drafts: use `T` instead.
Before commitment, reset restores canonical preparation; abandonment is explicit.

Course completions do not issue game rewards. Existing legacy skill receipts,
acknowledgment and save reconciliation remain separate and unchanged. A failed
facility is not learner noncompletion: provider loss requires explicit reissue,
or `T` when a questionnaire was already committed. Ordinary assignments do not
silently expire during reading/breaks. Attachment-owned shell commands may stop
on detach; attempt-owned prepared workers and files survive ordinary detach.

## Durable records, backup and recovery

Educational records use nested `education.version: 1` inside the existing
bridge snapshot version 4. The bridge persists records atomically in
`STATE_DIRECTORY/training.json`, bound to the existing character profile.
Completions, drafts/commitments, alternative exposure and compact trusted evidence
live outside disposable containers. Game wire/save versions are unchanged.

Loading an older game save with the **same profile** does not erase earned
educational progress in the bridge. Existing game-save skill claims cannot
reconstruct gate completions, P/K evidence, drafts or checkpoints if bridge state
is lost. Keep a private backup of the entire bridge snapshot, not only a selected
educational subtree or learner success files.

Using the existing bridge Python environment, export to a new private file:

```sh
python education_records.py export --state-dir /path/to/state --output /path/to/private/backup.json
python education_records.py validate /path/to/private/backup.json
```

Export reads a complete atomic snapshot and refuses to overwrite an existing
backup or the active state file. Keep profile IDs, answers and reward receipts
private; do not commit these files into source or distribute them as content.

Recovery is deliberately offline:

1. Stop the **owning** bridge/provider using its scoped operational controls.
2. Validate the backup and preserve the existing `training.json` separately.
3. Restore the complete backup as that state directory's `training.json`, with
   private ownership/permissions; retain the source backup unchanged.
4. Restart the provider against the same content/state directory and reconnect
   the matching profile. A live questionnaire retains its saved commitment.
   An execution environment is not restored by the JSON backup; follow explicit
   facility reissue rules for an interrupted attempt.

Do not edit or merge live snapshots, reuse someone else's profile, or infer later
completion from an older backup. A backup preserves records up to its export;
it is not a general reconstruction or active-environment transfer mechanism.
