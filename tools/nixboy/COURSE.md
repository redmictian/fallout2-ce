# Bounded assessments and canonical projects

course.yaml remains an optional sidecar to declarative tracks/challenges. It now
configures active assessments, not an independent educational progression system.
The default catalog keeps current skill/bracket selection. See CLEANUP.md for
save/load policy, runtime state and deliberately deferred cross-load records.

Formats are practical, theory and mixed. Theory requires no lab; mixed requires
both practical and conceptual success, with mutable practical results rechecked
at final submission. ALL parents and non-project educational prerequisites are
not supported by this candidate.

```yaml
schema: 1
gates:
  - id: example.assessment
    version: 1
    title: Bounded mixed assessment
    format: mixed
    prerequisites: []
    alternatives:
      - id: a
        title: Prepared alternative
        instructions: Complete the practical task and commit the complete answer set.
        challenge: existing.trusted-challenge-id
        questions:
          - id: q1
            type: choice
            prompt: Which components are required?
            choices: [Both components, Either component]
            answers: ["1"]
            feedback: Practical and conceptual success are both required.
```

Keys/feedback remain operator-owned; only public_questions exports uncommitted
prompts/choices. Each alternative has id/title/instructions and challenge/questions
as required by its format. Challenge IDs used by assessments stay out of ordinary
skill-band pools, preventing a practical-only bypass of mixed validation.

Questions retain bounded choice, number and text keys. Choice has 2–8 choices and
one numbered key. Numbers use decimal comparison. Text normalization is exact,
space or space-casefold. Optional option_feedback must explain every choice;
feedback is withheld until complete-set COMMIT. This is bounded key checking,
not prose grading. Drafts/commitments persist for the current run and cannot be
edited after commitment; T selects an explicit fresh equivalent reassessment.

Alternative queues defer failed alternatives and consume successes for that run.
When exhausted, they do not invent new alternatives or independently unlock the
character. A game load starts a new assessment run; this is not cross-save learning.

## Ordered practical evidence

checkpoints names implemented trusted observers, never authored executable code.
The fixed file-policy rollback/reapply and native-nginx reload observers remain.
They bind attempt ID, generation and content digest, and require actual operations
and preservation. Correct final files alone do not replace required checkpoints.
Reset/abandon/cancellation/facility loss cannot silently turn stale evidence into
credit. Unavailable facilities are distinct from incomplete learner outcomes.

## Canonical project stages

Optional project metadata is {id, stage, preparation}; stage numbers are unique
and contiguous from1. Stage1 has no prerequisite; each later stage may name only
its canonical immediate predecessor. The manager keeps one current project cursor,
not a global graph/completion record. Stage success removes its environment;
next-stage issuance uses that stage's independent canonical preparation. No learner
file/snapshot transfer is implied. Current-stage reset preserves the live cursor.
A game load discards external task progress and restarts the project canonically.

The retained native nginx project uses nginx-policy-v1 then nginx-reload-v1, real
client source addresses and native reload checks. Only its final stage maps to
the explicit test-only owned Toughness12 rank1→2 reward. Native current ownership
and maximum-rank checks remain required. Other mappings are not selected here.

Wiki reading is independent and carries no assessment/reward authority. Keep keys,
validators and precommit explanations out of learner exports. Full course authoring,
independent reward-free prerequisites and cross-load stage records remain deferred.
