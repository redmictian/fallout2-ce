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

The bridge durably earns a typed receipt before delivery. A lost ACK/reopen may
redeliver the exact receipt. The opening game's separate perk claim ledger lets
the bridge suppress claims already present, or replay them into an older save
of the **same** profile. Replaying into that save's owned rank1 state applies
the earned rank once to that loaded state. Loading a save without prerequisite
ownership does not grant ownership; the game rejects the upgrade safely.

## Save format and inspection

The game retains the existing `NXBTRAIN` trailer and skill records. With no perk
claims the writer emits byte-for-byte version4. With perk claims it emits
**version5**, adding the uint16 perk count and19-byte records after all skill
records, before the trailer CRC32. The payload length and CRC cover the extension.
The new reader accepts versions1–5 and preserves prior skill IDs, receipts and
point amounts. Serialized duplicate or cross-kind reused receipts reject the
trailer even with a valid CRC. The outer perk-training save policy remains1.2T.

An old0.7.0 binary cannot read a version5 trailer containing perk progress. Keep
pilot saves in the dedicated review copy; do not overwrite original saves or
promise backwards loading after a perk reward. Existing version4 saves remain
readable in the new build. There is no player-data migration or release promotion.

`inspect_training_save.py` reads versions4/5 without changing the file and reports
separate `claims` and `perk_claims`. It verifies trailer length/count/CRC, profile
and receipt constraints. The native raw perk/effect lives in the ordinary game
save records, so use actual game context/stat observations alongside the trailer
when verifying a reward. The trailer alone does not establish an effective perk.

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
