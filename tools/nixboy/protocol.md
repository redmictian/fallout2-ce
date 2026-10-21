# Nix-Boy local protocol, version 1

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
