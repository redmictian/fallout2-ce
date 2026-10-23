#ifndef FALLOUT_NIXBOY_TERMINAL_FONT_H_
#define FALLOUT_NIXBOY_TERMINAL_FONT_H_

#include <cstdint>

#include "text_font.h"

namespace fallout {
namespace nixboy {

    // Select the game's Pip-Boy font (101) before measuring or drawing the grid.
    // FONT_MONO centers its original AAF glyphs in the engine's native fixed-pitch
    // cells. The normal font renderer retains its palette antialiasing against
    // each actual background, including reverse-video and cursor cells.
    // The caller owns a complete, background-filled cell at (x,y).
    inline void drawTerminalGlyph(unsigned char* buffer, int pitch, int x, int y,
        uint32_t codepoint, unsigned char foreground, int cellWidth, int cellHeight)
    {
        if (buffer == nullptr || cellWidth <= 0 || cellHeight <= 0
            || x < 0 || y < 0 || cellWidth > pitch || x > pitch - cellWidth
            || fontGetCurrent() != 101 || fontDrawText == nullptr
            || fontGetMonospacedCharacterWidth == nullptr || fontGetLineHeight == nullptr
            || fontGetCharacterWidth == nullptr || fontGetLetterSpacing == nullptr) return;
        if (fontGetMonospacedCharacterWidth() != cellWidth
            || fontGetLineHeight() > cellHeight) return;
        const char glyph = codepoint == 0         ? ' '
            : codepoint >= 32 && codepoint <= 126 ? static_cast<char>(codepoint)
                                                  : '?';
        // Replaced/localized font assets must not let a wide glyph escape the
        // fixed cell: the native FONT_MONO path otherwise offsets it leftwards.
        if (fontGetCharacterWidth(glyph) + fontGetLetterSpacing() > cellWidth) return;
        const char text[] = { glyph, '\0' };
        fontDrawText(buffer + y * pitch + x, text, cellWidth, pitch, foreground | FONT_MONO);
    }

} // namespace nixboy
} // namespace fallout

#endif // FALLOUT_NIXBOY_TERMINAL_FONT_H_
