#ifndef FALLOUT_NIXBOY_TERMINAL_VIEW_H_
#define FALLOUT_NIXBOY_TERMINAL_VIEW_H_

#include <array>
#include <cstdint>
#include <vector>

namespace fallout {
namespace nixboy {

    // The Linux grid occupies a padded source surface. The CRT bends its text,
    // while the glass remains fitted to the complete native frame opening.
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

    constexpr int kTerminalCrtStrengthMin = 0;
    constexpr int kTerminalCrtStrengthMax = 100;
    constexpr int kTerminalCrtPersistenceMinMs = 0;
    constexpr int kTerminalCrtPersistenceMaxMs = 2000;

    struct TerminalCrtSettings {
        int warp = 35;
        int glow = 15;
        // Time for old light to fall to one tenth intensity. Zero clears it at once.
        int persistenceMs = 120;
        int scanlines = 8;
        int noise = 3;
        int jitter = 2;
        int bloom = 10;
        int rgbShift = 0;
    };

    // Original RGB CRT renderer. Projection and working buffers are allocated once;
    // timed frames add independent local glow, broad bloom, raster, noise, jitter,
    // transient burn-in and chromatic separation. Zero disables each effect.
    // Output pixels are SDL RGB888 / 0x00RRGGBB, without palette quantization.
    class TerminalCrt {
    public:
        bool initialize(int width, int height, const TerminalCrtSettings& settings = {});
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
        int lightAt(const std::vector<uint8_t>& light, int x256, int y) const;

        int width_ = 0;
        int height_ = 0;
        int bloomWidth_ = 0;
        int bloomHeight_ = 0;
        uint32_t previousTicks_ = 0;
        bool hasPreviousTicks_ = false;
        bool lightDirty_ = false;
        bool hasTrails_ = false;
        TerminalCrtSettings settings_;
        std::vector<Sample> samples_;
        std::vector<uint8_t> glass_;
        std::vector<uint8_t> current_;
        std::vector<uint8_t> history_;
        std::vector<uint8_t> emission_;
        std::vector<uint8_t> localGlow_;
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
