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
        int cellHeight, bool fullscreen, TerminalViewLayout& layout, int chromeCellHeight = 0);

    // Original RGB CRT renderer. Projection and working buffers are allocated once;
    // timed frames add persistence, two scales of bloom and restrained tube noise.
    // Output pixels are SDL RGB888 / 0x00RRGGBB, without palette quantization.
    class TerminalCrt {
    public:
        bool initialize(int width, int height);
        void update(const unsigned char* source, const std::array<unsigned char, 256>& luminance);
        void render(uint32_t* destination, int pitchPixels, uint32_t ticks);

    private:
        struct Sample {
            int offset;
            uint8_t xFraction;
            uint8_t yFraction;
            uint8_t shade;
            uint8_t coverage;
        };

        void updateLight(unsigned int elapsed);
        void blur(const std::vector<uint16_t>& source, std::vector<uint16_t>& destination,
            int radius, bool horizontal);
        int bloomAt(const std::vector<uint16_t>& bloom, int x, int y) const;

        int width_ = 0;
        int height_ = 0;
        int bloomWidth_ = 0;
        int bloomHeight_ = 0;
        uint32_t previousTicks_ = 0;
        bool hasPreviousTicks_ = false;
        bool lightDirty_ = false;
        bool hasTrails_ = false;
        std::vector<Sample> samples_;
        std::vector<uint8_t> glass_;
        std::vector<uint8_t> current_;
        std::vector<uint8_t> history_;
        std::vector<uint8_t> emission_;
        std::vector<uint16_t> downsampled_;
        std::vector<uint16_t> bloomNear_;
        std::vector<uint16_t> bloomWide_;
        std::vector<uint16_t> scratch_;
        std::vector<int> bloomX_;
        std::vector<int> bloomY_;
        std::vector<uint8_t> bloomXFraction_;
        std::vector<uint8_t> bloomYFraction_;
        std::array<uint8_t, 256> tone_;
    };

} // namespace nixboy
} // namespace fallout

#endif // FALLOUT_NIXBOY_TERMINAL_VIEW_H_
