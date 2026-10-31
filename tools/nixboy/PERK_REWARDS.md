# Typed passive-perk reward pilot

This optional delivery pilot connects one genuine educational completion to an
owned passive perk: **Toughness (engine ID12), raw rank1 → rank2**. Rank1 remains
inert; rank2 uses the existing production perk grant/effect code and adds10 points
of damage resistance. The course mapping is explicitly test-only. No skill/
curriculum allocation, permanent achievement name or broader perk policy is chosen.
Tag!, Mutate!, immediate XP/HP awards and choice-dependent perks are excluded.

## Compatibility and wire extension

Existing skill reward/ACK frames and claim fields are unchanged. A version4
training client remains a skill-only client. New builds send `training.version=5`
and the existing skill ledger, followed by an additional uint16 perk-claim count
and fixed19-byte records: `perk:uint16`, `target_rank:uint8`, `receipt:16 bytes`.
All integers are network byte order. Empty perk ledgers still include that count
in a version5 opening. Version4 openings are byte-identical to their prior form.

Python `decode_training_open(..., include_perks=True)` returns the existing four
fields plus a separate perk dictionary (`"12:2": receipt_hex`). Its default
four-field return preserves existing skill-only callers. New bridge handlers use
the explicit extended return. Unsupported older bridges reject version5 rather
than silently misinterpreting new claims.

| Frame | Direction | Payload |
| --- | --- | --- |
| `TRAIN_PERK_REWARD=22` | Bridge → game | Nonzero profile16, perk:uint16, target_rank:uint8, nonzero receipt16 =35 bytes |
| `TRAIN_PERK_ACK=10` | Game → bridge | Exact reward payload plus accepted:uint8 (0 or1) =36 bytes |

The profile is explicit in the new reward, independent of current terminal text.
The initial allowlist is only ID12/rank2. Duplicate keys, zero/reused receipts and
receipts shared between skill/perk kinds are rejected. Catalog YAML and learner
files cannot name executable reward behavior. A version4 client cannot receive or
acknowledge perk frames.

New context fields are `perk.toughness`, `perk.toughness.max`,
`perk.training.enabled` and `stat.damage_resistance`. They describe the actual
player's raw rank, native cap, compiled feature policy and effective resistance.
They support trusted eligibility and acceptance observations, not learner claims.

## Applying a reward

`TrainingState::acceptPerk` returns **Rejected**, **New** or **Duplicate**.
Accepted duplicates are acknowledgments, not permission to apply another effect.
New claims require matching profile, owned prior rank exactly1, target exactly2,
adequate native maximum and no conflicting/reused receipt.

The game-thread `applyPerkTrainingReward` validates a candidate ledger before
mutating the real perk, calls production `perkAddForce` only for **New**, and
publishes that candidate only after successful rank acquisition. Wrong profile,
unowned rank0, already advanced/max rank, unsupported perk/target and receipt
conflicts leave both rank/effect and ledger unchanged. Feature-disabled builds
reject this typed reward. An exact duplicate with the matching existing ledger
acknowledges successfully without another rank/effect.

The bridge durably earns a typed receipt before delivery within the active loaded
progression. Lost ACK/reopen can redeliver that exact receipt without a new effect.
Loading an older save selects its own saved profile and reward ledgers; later
earned receipts are discarded from the current progression and may be earned again.
Protocol 6 prefixes reward/ACK bodies with the unsaved activation token, preventing
late delivery from a discarded load. See [PROGRESSION.md](PROGRESSION.md).

## Save format and inspection

The game retains `NXBTRAIN` and reads trailers 1�5, while new saves write trailer
**6**: existing profile/skill/perk ledgers (empty perk count is valid), then a
32-byte immutable progression snapshot SHA256, followed by the covering CRC32.
Only that saved profile can provide pending rewards after a load. Native perk
rank/effect still resides in the ordinary game records; no effect is applied by
trailer decode. The outer perk-training save policy remains 1.2T.

Earlier binaries cannot read trailer 6. Legacy v4/v5 saves lack education snapshots:
the new build retains their exact saved rewards but reports an empty historical
education baseline, never the latest external state. `inspect_training_save.py`
reads v4/v5/v6 without changing files. The claim trailer alone does not prove an
effective perk; use actual game context/stat observations as well.

Focused native tests cover the real production rank/effect adapter, duplicate
ACK, profile/ownership/rank/receipt rejection, older owned-state replay, v4/v5
serialization and typed client frames. Actual game/save evidence remains a
separate acceptance responsibility; unit tests do not substitute for it.

The opt-in local acceptance relay `tests/reward_duplicate_proxy.py --run
--reward-kind perk --drop-acks --copies 2 --listen PROXY_PORT --upstream
BRIDGE_PORT --output NEW_PRIVATE_DIRECTORY` repeats only the first genuine
received perk reward and can suppress real perk acknowledgments. It never
manufactures completion or reward data. Its private log records v5 opening
claims, profile, raw rank/max/resistance, context refreshes and exact received
reward/ACK receipts; authentication bytes are never logged. The default remains
the prior skill-only duplicate test. Remove `--drop-acks` for ordinary forwarding.
Use a fresh relay/output for another deliberate first-delivery duplication;
creating `OUTPUT/stop` stops that relay. These logs corroborate actual game/save
observations and do not themselves establish rank/effect or save correctness.
