# Progression follows the game save

Loading an older game save rolls back skills, perks, educational completions,
prerequisites, project stages, alternative accounting and earned receipts. There
is no cross-save learning mode. Reopening nix-boy without a game load resumes
the current progression and pending work.

## Existing save transaction and focused extension

The game still uses its ordinary save slots, subsystem handlers and SAVE.DAT
backup/write transaction. Native skills/perks remain in their existing records;
training bonuses and claims remain in the checked NXBTRAIN trailer. Trailer **6**
adds one 32-byte immutable bridge-snapshot reference after the existing skill
and perk ledgers. It does not contain a VM image, guest RAM or process snapshot.

Before writing that trailer, the existing save handler asks the authenticated
bridge to capture the current complete progression profile. The bridge writes
`STATE/progression-snapshots/SHA256.json` durably before returning its digest.
The game stores that digest in SAVE.DAT, covered by its trailer CRC. A failed
game write may leave an unused immutable snapshot; it cannot alter another save.
For a character with a training profile, an unavailable bridge or failed capture
cancels the game save with an explicit error and the existing slot rollback.
Characters that have never created a training profile can save without a bridge.

The mutable current profile still lives in `training.json` (bridge snapshot 5).
Its full save-captured state includes skill/perk receipts, legacy passes/queues,
education completions/queues/seen/history and the current attempt/questions.
Saving records the actual game's applied claim ledgers separately from earned
receipts: an earned but undelivered reward can be pending in that exact save.

## Load selection and stale delivery

Each successful game trailer decode creates a fresh runtime activation token.
The first training contact (opening nix-boy or saving again) selects the exact
saved snapshot and replaces the current profile, rather than merging later
history. This selection is deferred until bridge contact so ordinary game loading
does not need Docker or a running bridge. It runs before any eligibility, question,
checkpoint or reward handling. Two saves of one character have the same profile
but independently select their own snapshot references.

The activation token is **not saved**. Reopening uses the same activation and
resumes the mutable current profile, even when unsaved progress has accumulated.
Current/retired activation metadata is separate from rolled-back progression.
Protocol 6 reward and acknowledgment bodies carry the activation prefix, and
session/worker guards reject discarded activations. A new completion after
rollback can earn a fresh receipt; a delayed old receipt cannot fund it. Exact
duplicates within the current activation remain accepted without another effect.
The retained activation safety bound is 4,096 retired loads per profile; reaching
it blocks explicitly for operator recovery rather than recycling stale tokens.

## Attempts and facilities

A load cancels current work, removes the owned practical environment and discards
its runtime/evidence. A saved practical attempt receives a fresh attempt identity
and generation, with practical/checkpoint evidence cleared. R prepares its
canonical environment again without queue consumption or a learner penalty.
Saved question drafts and committed results are retained/rebound to that attempt;
changed content is still rejected by existing content fingerprints. Pure theory
attempts retain their exact saved drafts/commitments. No live container or guest
state is restored. Ordinary detach/reopen continues to preserve the current lab.

## Compatibility and missing state

Trailers 1–5 remain readable. They have no educational reference, so exact
historical education/queue/attempt rollback cannot be recovered. On first bridge
contact they explicitly start an empty educational baseline containing only the
game's saved skill/perk claims; no latest bridge records are imported and no
historical completion is invented. The hub reports this limitation. Old bridge
snapshot 4 migrates without destroying its records, but those records are not
silently attached to legacy saves. Preserve accepted states before testing.

A nonzero new reference whose file is missing, corrupt, wrong-profile or mismatched
to the saved claim ledgers blocks training/capture explicitly. The native game
save can still load its ordinary character state. Restore the matching backup;
there is no automatic fallback to latest progress or silent educational reset.
Earlier binaries cannot read trailer 6. V4/v5 clients remain supported for their
legacy profiles but cannot access a profile already activated under protocol 6.

## Backup and recovery

Keep game saves **and** the associated private bridge state. No snapshot garbage
collection is performed because multiple saves can reference older files.

```sh
python education_records.py export --state-dir /private/state --output /private/backup.json
python education_records.py validate /private/backup.json
```

Export keeps the existing atomic profile backup and copies immutable files to
`backup.json.progression-snapshots/`, with an integrity index in the backup.
Retain both together. Stop the owning provider before offline recovery, preserve
the current directory, then restore into a **new** state directory:

```sh
python education_records.py restore /private/backup.json --state-dir /private/restored-new
```

The restore refuses an existing destination. Restart the scoped provider against
that directory, then load the desired game save. Never merge live state or run a
second manager against a live provider. Tokens and prepared guest artifacts have
their existing separate lifetimes; snapshots contain no Docker environment.
