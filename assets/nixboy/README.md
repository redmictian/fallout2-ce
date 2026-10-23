# Pip-Boy nix-boy nameplate

`pipboy-plaque-source.png` is original generated artwork, created October 23,
2026 with the built-in `image_gen.imagegen` tool in transparent-background mode.
It contains no copied game graphics. The original tool output was selected
without image edits. `pipboy-plaque.png` is its cropped and downsampled 92x22
RGBA derivative. Generated transparency is retained.

The plaque replaces the temporary green engine-font label beside the original
red Pip-Boy button. Its olive-brown metal, dark screw heads and worn ivory stamped
letters are intended to fit the original interface's industrial design.

`tools/nixboy/embed_pipboy_plaque.py` crops to visible alpha bounds (threshold 16),
downsamples with Pillow's Lanczos filter, and embeds RGB555 plus eight-bit alpha
in `src/nixboy_pipboy_plaque.h`. The game blends edges against the actual panel
and maps through its loaded `_colorTable`; the asset needs no installed file,
external image library or proprietary palette. The Python/Pillow tool is needed
only to regenerate artwork derivatives. Verify reproducibility with `--check`.

## Final generation prompt

```text
Use case: stylized-concept
Asset type: tiny 2D game interface texture, a single horizontal industrial nameplate that will display at 92 by 22 pixels in a 1990s post-apocalyptic role-playing game.
Primary request: an original worn stamped metal plaque with the exact label NIX-BOY. Muted olive-brown, dark weathered gunmetal and dirty warm ivory lettering, designed to blend into a battered retrofuturistic 1950s military computer panel. Restrained Fallout 1/2 interface design language, handmade low-resolution pre-rendered texture aesthetic.
Composition/framing: straight-on orthographic, rectangular plaque, very wide and short, aspect ratio approximately 4.2:1. Center the plaque with transparent empty margins outside. Fill the plaque with large highly legible uppercase condensed sans-serif letters, single line reading exactly NIX-BOY. Shallow dark bevel on the rim, tiny dark countersunk screws at far left and far right, subtle fine scratches and grime. Keep the text simple and clear enough to remain readable after downsampling to 92x22. One plaque only.
Text (verbatim): "NIX-BOY"
Constraints: actual transparent background. Flat frontal face, horizontal edges, no perspective, no drop shadow outside plaque. Warm ivory text, subdued metal, no glow, no green screen, no extra symbols, no other text, no watermark. Original asset; no copied game graphics.
```
