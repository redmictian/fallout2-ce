#ifndef FALLOUT_NIXBOY_UI_ART_H_
#define FALLOUT_NIXBOY_UI_ART_H_

namespace fallout {

// Reuses the installed game's automap CRT surround through the normal art cache.
// Normal margins preserve native corner/bevel thickness and one top-center latch;
// long edge runs are extended continuously rather than repeating texture tiles.
bool nixboyDrawTerminalFrame(int window, int width, int height,
    int glassX, int glassY, int glassWidth, int glassHeight, int statusY);

// Composes new lettering from painted strokes in the installed English PIP.FRM.
// No proprietary glyph pixels or palette data are embedded in the executable.
// Returns true for native strokes; false uses a native-font fallback if possible.
bool nixboyDrawPipboyLabel(unsigned char* buffer, int width, int height,
    const unsigned char* background);

} // namespace fallout

#endif // FALLOUT_NIXBOY_UI_ART_H_
