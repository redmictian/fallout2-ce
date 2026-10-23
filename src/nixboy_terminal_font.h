#ifndef FALLOUT_NIXBOY_TERMINAL_FONT_H_
#define FALLOUT_NIXBOY_TERMINAL_FONT_H_

#include <cstdint>

#include "../third_party/departure-mono/nixboy_ascii_bitmap.h"

namespace fallout {
namespace nixboy {

    constexpr int kTerminalCellWidth = 8;
    constexpr int kTerminalCellHeight = 16;

    // The caller owns a complete, background-filled cell at (x,y). Only ink is
    // written, preserving reverse-video backgrounds and the full cell's padding.
    inline void drawTerminalGlyph(unsigned char* buffer, int pitch, int x, int y,
        uint32_t codepoint, unsigned char foreground)
    {
        if (buffer == nullptr || pitch < x + kTerminalCellWidth || x < 0 || y < 0) return;
        const uint32_t glyph = codepoint >= 32 && codepoint <= 126 ? codepoint : '?';
        for (int row = 0; row < kTerminalCellHeight; row++) {
            const unsigned char bits = kTerminalAsciiBitmap[glyph - 32][row];
            for (int col = 0; col < 8; col++) {
                if ((bits & (1u << col)) != 0) {
                    buffer[(y + row) * pitch + x + col] = foreground;
                }
            }
        }
    }

} // namespace nixboy
} // namespace fallout

#endif // FALLOUT_NIXBOY_TERMINAL_FONT_H_
