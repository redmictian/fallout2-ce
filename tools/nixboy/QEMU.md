# Optional BT05-A machine pilot

This is an opt-in Windows → Ubuntu-26.04 WSL → Docker Engine pilot. Ordinary
challenges keep their existing direct-container implementation. It uses the
existing training manager, terminal, validators and version-4 reward receipts.

Use a fresh disposable character, game copy and training state. The separate
`content-qemu-pilot/` catalog maps BT05-A to **Science 12 / level 60000 / 0..300 /
+1 point**, and ordinary regression exercises to Unarmed 3 / level 60001 / +1.
Practice gives no reward. This is a test mapping, not a curriculum decision.

Build explicitly in WSL from this directory:

```bash
docker build -t nixboy-qemu-bt05:20261030 container/qemu
/home/mictian/build/nixboy-venv/bin/python qemu_artifacts.py verify
```

The current installation retains the three qualified disks in Docker volume
`nbq-20261030-images`. `container/qemu/bt05-manifest.json` is the code-owned
identity/expected-state manifest. Runners verify those hashes before boot,
mount the bases read-only and create owned disposable qcow2 overlays. To import
the exact frozen artifacts from a trusted local directory:

```bash
/home/mictian/build/nixboy-venv/bin/python qemu_artifacts.py import --directory /private/bt05-disks
```

No images, keys or player data are in the package. No request silently downloads,
builds or blesses a new base. Fresh preparation is a reviewed authoring operation:
the mod workspace's `tools/qemu-qualification/README.md`, `seed.py` and
`boot_run.py --phase all --cpu-model Nehalem` describe the executable recipe.
Rebuilding generates different UUIDs/hashes: update the manifest and protected
expectations together and repeat the positive/negative/reset/stability checks.

The selected profile is **QEMU 7.2.22, pc-i440fx-7.2, Nehalem, TCG cache128,
two vCPUs, 768 MiB guest RAM**. The outer container has 2 GiB RAM, three CPU
equivalents, UID1000, all capabilities dropped, default seccomp and
no-new-privileges. No KVM, physical host disks, TAP device, privileged container
or host Docker socket inside the runner is required. `NIXBOY_QEMU_IMAGE` and
`NIXBOY_QEMU_IMAGES_VOLUME` are trusted operator overrides; requalify changes.

From PowerShell, start the bridge with the adjacent launcher (source checkout:
`tools/start-nixboy-wsl.ps1`; package: `start-nixboy-wsl.ps1`):

```powershell
./start-nixboy-wsl.ps1 -ProviderRecord C:\path\private-test\provider.json `
  -TokenFile /home/mictian/.local/state/nixboy/bt05-test.token `
  -StateDirectory C:\path\private-test\training `
  -ContentDirectory C:\path\package\nixboy\content-qemu-pilot -Port 47831
```

The launcher uses a hidden, long-lived Windows `wsl.exe` client scoped to the
bridge. No unrelated open terminal or global WSL setting is needed. Linux token
storage must have private POSIX permissions; copy its bytes privately to the test
game's token file. Configure that game with `training=1`, `port=47831` and the
matching `token_file`. Status/stop take the same `-ProviderRecord` plus
`-Action Status` or `-Action Stop`. Stop verifies Linux PID birth identity and
requests graceful bridge cleanup; its bounded fallback is logged.

Choose Science, assessment, then repair the wrong mandatory UUID of
`/srv/recovery-data` using the real recovery console. Identify disk serial
**nbq-boot-data**, preserve data/metadata/unrelated fstab lines and keep the
mount mandatory. Allow **60 seconds for a valid device** under emulation.
Run `systemctl reboot`; after normal startup, F5 independently checks reboot,
correct mounted UUID, protected state, actual unprivileged service and HTTP.
The identical-payload decoy and a removed required mount do not pass.

F10/F6 detach; R reattaches the same guest. Guest reboot preserves overlays.
F7 removes overlays and reissues the canonical fault; F8 abandons. During long
preparation F8 cancels/abandons, F7 cancels/restarts and F10 cancels provisional
work. An already active guest survives ordinary closure/breaks without idle
expiry. The serial console is sized to the game grid at its exclusive first
handoff. Later resize is deferred; resume never injects setup into an active
guest job. Historical terminal queries are omitted from display replay so
reattachment cannot inject stale terminal replies into the current guest shell.

Runner readiness, expected recovery, booting, normal startup, off and failure
are distinct. QMP `running` alone is not guest health. Recovery agent absence
does not cause a learner nonpass. Provider/bridge loss retains diagnostics and
requires explicit F7 reissue; transparent guest adoption is not supported and
no reward/learner failure is recorded. Validation decisions/checkpoints live
outside the guest; a success marker cannot issue a receipt.

External evidence is retained in `STATE/qemu-evidence/ATTEMPT/GENERATION/`.
Use `smoke_qemu_training.py --run --output NEW-PRIVATE-DIRECTORY` from source
for actual socket/Docker/serial checks; its ledger is modeled, not actual game
save evidence. `inspect_training_save.py SAVE.DAT` reads an actual saved v4
ledger without modification. The mod acceptance report distinguishes these
from real in-game save/load and stability observations.
