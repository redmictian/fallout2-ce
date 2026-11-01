# Optional BT05-A integration pilot catalog

Opt in with the bridge's `--content-dir tools/nixboy/content-qemu-pilot`.
The standard shipped catalog is unchanged.

This catalog is **test only** and must use a disposable character/profile and a
separate bridge state directory. Science skill ID 12, level 60000, effective skill
0..300, awards exactly +1 point for the BT05-A assessment. Unarmed skill ID 3,
level 60001, effective skill 0..300, awards +1 point for either ordinary container
regression alternative. Practice follows the existing no-reward contract.
These broad bands and IDs facilitate acceptance checks; they do not choose any
permanent educational-track mapping or change game skill behavior.

BT05-A uses the explicit guest runner and immutable artifact manifest in
`../container/qemu/`. `compose/single.yaml` is only for the ordinary exercises.
No learner-editable file can grant a reward: the bridge's trusted validator must
corroborate reboot, filesystem, protected content, service and external request
evidence before the existing receipt path issues a reward.
