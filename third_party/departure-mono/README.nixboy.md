# Terminal font

The unmodified Departure Mono font and its SIL Open Font License 1.1 are from
Helena Zhang's official repository, commit
`75152a3f1e6dacdd248a6c397c97dbf27e33eea0`:
[font](https://github.com/rektdeckard/departure-mono/blob/75152a3f1e6dacdd248a6c397c97dbf27e33eea0/public/assets/DepartureMono-Regular.otf),
[license](https://github.com/rektdeckard/departure-mono/blob/75152a3f1e6dacdd248a6c397c97dbf27e33eea0/public/assets/LICENSE).
Font SHA256: `4d53f663155cf8bf7ffc8e688776e719625f7bbb80a8d90073438b249261a2e0`.

The terminal uses an ASCII bitmap derived at the font's native 11px size,
placed in 8x16 cells. This is the font selected by cool-retro-term's current
Monochrome Green profile, obtained from the font's author rather than that app.
Its font license and copyright remain separate from the engine license.
No GPL application or shader code is included.

`nixboy_ascii_bitmap.h` is the derived font subset, also under OFL 1.1. Its
internal identifier is Nixboy ASCII Bitmap. The header retains the attribution;
the complete license is OFL.txt. Regenerate with
`python tools/nixboy/generate_terminal_font.py` (Pillow is needed for authoring),
or pass `--check` to verify it. Runtime has no font-library dependency.
Original Fallout fonts still draw the game chrome.
