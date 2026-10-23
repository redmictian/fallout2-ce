#ifndef FALLOUT_NIXBOY_UI_ART_H_
#define FALLOUT_NIXBOY_UI_ART_H_

namespace fallout {

// Loads the installed game's dialog/options FRMs through the normal art cache.
// All corners and texture pixels retain their native size; edges are tiled.
bool nixboyDrawTerminalFrame(int window, int width, int height,
    int glassX, int glassY, int glassWidth, int glassHeight, int statusY);

// Composes new lettering from painted strokes in the installed English PIP.FRM.
// No proprietary glyph pixels or palette data are embedded in the executable.
// Returns true for native strokes; false uses a native-font fallback if possible.
bool nixboyDrawPipboyLabel(unsigned char* buffer, int width, int height,
    const unsigned char* background);

} // namespace fallout

#endif // FALLOUT_NIXBOY_UI_ART_H_
