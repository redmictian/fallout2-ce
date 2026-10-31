# nix-boy local protocol

The original OPEN mode below retains its ephemeral shell contract. Training
extension version 4 uses TRAIN_OPEN instead, with the same version-1 frame header
and bounded integer-context encoding; it requires `training.version=4`. The hub
becomes READY without provisioning Docker. See [TRAINING.md](TRAINING.md) in the
runtime package for player controls and lifecycle.

The game-configured opening shortcut (`[nixboy] hotkey`, default `Ctrl+N`) is a
local input binding. It changes neither wire messages nor challenge selection;
the Pip-Boy button opens the same connection path.

The game connects by TCP to `127.0.0.1:47821` by default. Every frame is
`type:uint8`, `payload_length:uint32 big endian`, then exactly that many payload
bytes. The maximum payload is 65,536 bytes. There is no text delimiter, native
structure packing, compression, or terminal escape based control channel.

The initial state requires AUTH followed by OPEN exactly once. Both can be sent
together. The game then waits for READY before sending more frames. Invalid
authentication, framing, names, counts, ordering, duplicate context entries, or
payload lengths disconnect the client. Invalid initial requests do not contact
Docker. Authentication and OPEN each have a five second socket deadline; active
partial frames have a one second socket deadline. Only one authenticated open
session and eight simultaneous connections are allowed.

The token file contains exactly 64 ASCII hexadecimal characters and no newline.
Comparison is case-sensitive and constant-time. This is a local bearer token;
transport is unencrypted and must remain on loopback. The bridge never logs the
token. Requests cannot choose a Docker image, host path, executable, mount, user,
environment variable name, or Docker endpoint.

## Client to bridge

| Type | Name | Payload |
| --- | --- | --- |
| 1 | AUTH | Exactly 64 ASCII hex token bytes |
| 2 | OPEN | `rows:uint16`, `cols:uint16`, context map |
| 3 | INPUT | 1..65,536 raw PTY input bytes |
| 4 | RESIZE | Exactly `rows:uint16`, `cols:uint16` |
| 5 | CLOSE | Empty |
| 6 | CONTEXT | Context map replacing the previous map |

All integers are big endian. Rows are 1..200 and columns 1..300. A context map is
`count:uint16`, followed by count entries. Each entry is `name_length:uint8`, name
bytes, `value:int32`. Count is 0..128. Names are unique, 1..64 ASCII characters,
matching `[A-Za-z_][A-Za-z0-9_.-]{0,63}`. Values are signed 32-bit integers.
Trailing bytes are invalid. Empty maps have the two-byte count field.

INPUT carries Ctrl+C as byte `03`, Ctrl+D as `04`, and ordinary terminal escape
sequences without reinterpretation by the bridge. Empty INPUT is invalid; clients
should treat an empty keyboard write as a local no-op. The shell runs inside
Linux with a real Docker PTY. Context updates are applied and acknowledged on the
separate control channel before subsequent queued input is delivered to the PTY.

## Bridge to client

| Type | Name | Payload |
| --- | --- | --- |
| 16 | READY | Empty, exactly once after control and PTY startup |
| 17 | OUTPUT | Raw terminal bytes, up to 65,536 |
| 18 | RESULT | `name_length:uint8`, name, `value:int32` |
| 19 | EXIT | Exactly signed `int32` actual shell exec exit status |
| 20 | ERROR | Up to 4,096 valid UTF-8 bytes; fatal |

OUTPUT, RESULT and EXIT are legal only after READY. ERROR can occur while
starting or running, and closes the session. EXIT is final, and closes the
session after previously acknowledged helper events have been forwarded. Bash
exit status is read from Docker's exec inspection; EOF alone never implies zero.
RESULT names and values use the same limits as context entries. `terminal_exit`
is reserved for the engine's interpretation of EXIT; helper events using that
name are rejected. The game independently applies its explicit binding whitelist
and validates each global index before changing game state. A syntactically valid
unbound RESULT has no game effect. Terminal output is never interpreted as a
RESULT.

## Separate Linux control transport

The container PID 1 is `/opt/nixboy/control.py serve`. It owns a private Unix
socket `/tmp/nixboy/control.sock` and atomic JSON context file
`/tmp/nixboy/context.json`. The bridge starts a second, non-TTY Docker exec,
`python3 -u /opt/nixboy/control.py relay`, using an attached duplex socket. The
bridge decodes Docker's eight-byte stdout multiplex headers, then bounded JSON
lines (32,768 bytes). Stderr is not a protocol stream. The shell PTY is a separate
Docker exec, so no ANSI escape, printed JSON, prompt, or user output can masquerade
as a control request.

The relay subscribes to the Unix socket. It receives `{"type":"ready"}`; the
bridge sends `{"type":"context","values":{...}}` and awaits
`{"type":"context_ack"}` before attaching Bash and sending READY. Later context
updates use the same exchange. Linux helpers connect directly to the Unix socket
for `{"type":"context"}` (reply `{"values":{...}}`) and
`{"type":"emit","name":"task","value":7}` (reply `{"ok":true}`). Valid events
are forwarded to the relay as `{"type":"event","name":"task","value":7}`.
Before EXIT, a `{"type":"sync"}` / `{"type":"sync_ack"}` barrier drains prior
acknowledged helper events. Every control message is structurally validated.

The Linux-side context and event values are untrusted. Arbitrary processes within
the session may publish allowed named integers or alter their own context files;
the engine whitelist is the authority for game effects. This is a mod extension
interface, not a game anti-cheat boundary.

## Training extension version 4

| Direction | Type | Name | Payload |
| --- | --- | --- | --- |
| Client -> bridge | 7 | TRAIN_OPEN | rows:uint16, cols:uint16, context_length:uint32, context map, claim_count:uint16, claim records |
| Client -> bridge | 8 | TRAIN_ACTION | One byte: 1 Check, 2 Hub, 3 Reset, 4 Abandon |
| Client -> bridge | 9 | TRAIN_ACK | skill:uint8, level:uint16, points:uint16, receipt:16 bytes, accepted:uint8 (22 bytes) |
| Bridge -> client | 21 | TRAIN_REWARD | skill:uint8, level:uint16, points:uint16, receipt:16 bytes (21 bytes) |

TRAIN_OPEN follows AUTH exactly once. Its context map requires training.version=4,
four training.profile.0..3 signed int32 words encoding the character's 16 identity
bytes in big-endian order, and skill.<stable_engine_name> values for all 18 current
effective skills. Together with the five ordinary map/player fields this is 28
entries. Training omits legacy global bindings and the earlier per-track
mask/count/receipt-word fields.

Each ledger record is skill:uint8, level:uint16, points:uint16, receipt:16 bytes,
using big-endian integers. Skill IDs are 0..17; level IDs are 1..65535; points
are 1..300. At most 2048 records are
accepted. Every skill/level pair and nonzero receipt must be unique. The payload
has exactly 10 + context_length + claim_count * 21 bytes and remains subject to
the common frame limit. Level 2 or any other level may be earned independently.

The bridge maps current skill values to the inclusive bands in tracks.yaml.
In the shipped initial catalog,
Unarmed 15..19 selects level 1 and 20..24 selects level 2; Science 10..14 selects
level 1 and 15..19 selects level 2. An uncovered value is unavailable, not a
fallback. Selection and validation both use the current snapshot's matching
band. A changed band preserves the active attempt but prevents its validation
from granting a reward; the player can abandon it and select again.

All 18 engine skills, level IDs 1..65535, nonzero 16-byte receipts and
acknowledgment values 0/1 are accepted. Authored YAML decides which of those
skill bands actually has challenges. The bridge records successful host validation
before sending its receipt. Fallout checks supported skill/level and receipt
identity and recorded points, sums earned amounts for the bonus, and
acknowledges exact duplicate receipts with matching amounts without changing
state. A repeated skill/level with a different amount is a conflict. Eligibility belongs
to the trusted bridge; Fallout accepts independent level claims and does not
require every earlier level. ACK is followed by fresh
CONTEXT; a game-memory acknowledgment is not a promise that a save was written.

The game stores identity, points and receipt records together in its checked
version-4 SAVE.DAT trailer. Versions 1, 2 and 3 migrate their existing receipts
with five points each. The bridge's version-4 JSON similarly retains old receipt
identity and assigns the original five points. Wire training versions 1, 2 and 3
are rejected, so upgrade game and bridge together. The bridge can import exact
receipts from the opening ledger and replays missing earned entries after
reconnect, including entries whose authored track was removed. Acknowledgments
update the bridge's current game ledger; later CONTEXT frames refresh skills
and profile values without resending the opening ledger.
The amount comes from the completed level's `reward_points` (default 5), then
remains part of the immutable earned record. Catalog edits cannot reprice an
earned reward within its selected progression.
It never invents success from shell exit, PTY text or learner helper events.

INPUT routes to a restricted hub menu or the current exercise PTY. RESIZE and
CONTEXT remain available. A profile cannot change within a session. F5-F8 send
TRAIN_ACTION; F10/CLOSE or a socket disconnect detach the frontend and stop its
specific shell attachment, preserving exercise files while the bridge runs.
A fresh attachment starts a fresh shell. EOF from that shell returns to the hub
and never generates EXIT as a training reward. Issued assignments have no silent
idle expiry. Reset, abandonment and provider interruption have distinct handling;
provider loss requires explicit reissue and is not learner failure.

The optional educational hub uses these same INPUT/OUTPUT/TRAIN_ACTION frames.
Its educational ledger remains bridge-hosted. Protocol/trailer 6 below associates
a complete educational snapshot with each game save; see COURSE.md and PROGRESSION.md.

The earlier typed passive-perk adapter negotiated `training.version=5`, appending
a separate opening perk ledger and uses profile-bound TRAIN_PERK_REWARD22 /
TRAIN_PERK_ACK10 frames. Version4 remains skill-only; a live session cannot
change its negotiated version. Existing inner skill payloads are unchanged. That
earlier writer used trailer-v5 only when perk claims existed. The current writer
always uses trailer 6 with the save reference described below; older binaries
cannot read it. See PERK_REWARDS.md for exact
fields, maximums, duplicate/new application and reconciliation semantics.

Legacy RESULT events are never forwarded by the training bridge, and the C++
client rejects RESULT in training mode. Legacy OPEN retains its original
RESULT/EXIT/global-binding behavior. The per-installation authentication token
never enters the learner environment. Docker/provisioning errors and validator
errors cannot grant a completion receipt.


## Save-bound progression extension (version 6)

The current game negotiates `training.version=6`. V6 TRAIN_OPEN appends 49 bytes to
its v5 skill/perk opening: `activation:16`, `snapshot_sha256:32`, `restore:uint8`.
The nonzero activation is runtime-only and changes on each successful game load;
the snapshot reference is saved in trailer 6. Repeated openings with the same
activation resume the current mutable profile. A new activation selects the exact
saved snapshot before any learning/reward access; it does not merge later history.
Zero snapshot explicitly means a legacy/new empty educational baseline containing
only known game reward claims. A missing/corrupt nonzero snapshot is an error.

Initial `TRAIN_CAPTURE=11` after AUTH carries that same V6 opening payload and
returns `TRAIN_SNAPSHOT=23` with exactly 32 digest bytes, then closes. It creates
no terminal or Docker environment. The existing game save transaction requires
this durable capture before writing the digest for a profile-bearing save.

V6 TRAIN_REWARD21/TRAIN_PERK_REWARD22 and TRAIN_ACK9/TRAIN_PERK_ACK10 prefix their
unchanged inner payloads with the 16-byte activation. Client/session checks reject
mismatches and retired activations. Skill/perk receipts still distinguish exact
duplicates from new applications. V4/v5 clients remain legacy-only and cannot
access a profile activated by V6. See PROGRESSION.md for the complete rollback,
practical-attempt reissue, compatibility and recovery policy.
