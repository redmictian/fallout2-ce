#ifndef FALLOUT_NIXBOY_TERMINAL_VIEW_H_
#define FALLOUT_NIXBOY_TERMINAL_VIEW_H_

#include <array>
#include <cstdint>
#include <vector>

namespace fallout {
namespace nixboy {

    // The Linux grid occupies a padded source surface. The CRT bends that complete
    // surface into the glass aperture; its decoration never consumes terminal cells.
    struct TerminalViewLayout {
        int x = 0;
        int y = 0;
        int width = 0;
        int height = 0;
        int glassX = 0;
        int glassY = 0;
        int glassWidth = 0;
        int glassHeight = 0;
        int textX = 0;
        int textY = 0;
        int rows = 0;
        int cols = 0;
        int statusY = 0;
    };

    bool terminalViewLayout(int screenWidth, int screenHeight, int cellWidth,
        int cellHeight, bool fullscreen, TerminalViewLayout& layout);

    // Palette-independent geometry and software filtering. Map construction is done
    // once per opening, never per frame. Rendering has no allocations or floating
    // point work, and callers only redraw when the terminal changes.
    class TerminalCrt {
    public:
        bool initialize(int width, int height, bool retro);
        void render(const unsigned char* source, unsigned char* destination, int pitch,
            const std::array<unsigned char, 256>& luminance,
            const std::array<unsigned char, 256>& phosphor) const;

    private:
        struct Sample {
            int offset;
            uint8_t xFraction;
            uint8_t yFraction;
            uint8_t shade;
        };

        int width_ = 0;
        int height_ = 0;
        bool retro_ = false;
        std::vector<Sample> samples_;
    };

} // namespace nixboy
} // namespace fallout

#endif // FALLOUT_NIXBOY_TERMINAL_VIEW_H_
