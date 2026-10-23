#include "nixboy_terminal_view.h"

#include <algorithm>
#include <cmath>
#include <cstddef>

namespace fallout {
namespace nixboy {
    namespace {

        constexpr int kMaxGlassWidth = 2048;
        constexpr int kMaxGlassHeight = 1536;
        constexpr int kBezel = 18;
        constexpr int kTextPadding = 12;

    } // namespace

    bool terminalViewLayout(int screenWidth, int screenHeight, int cellWidth,
        int cellHeight, bool fullscreen, TerminalViewLayout& layout)
    {
        layout = {};
        // The limits also keep all pixel, font and protocol arithmetic bounded.
        if (screenWidth < 320 || screenHeight < 240 || screenWidth > 16384
            || screenHeight > 16384 || cellWidth <= 0 || cellWidth > 64
            || cellHeight <= 1 || cellHeight > 64) {
            return false;
        }
        layout.width = fullscreen ? screenWidth : std::min(1280, screenWidth - std::max(32, screenWidth / 8));
        layout.height = fullscreen ? screenHeight : std::min(900, screenHeight - std::max(32, screenHeight / 7));
        layout.x = (screenWidth - layout.width) / 2;
        layout.y = (screenHeight - layout.height) / 2;
        const int header = std::max(36, 2 * cellHeight + 12);
        const int footer = 3 * cellHeight + 14;
        layout.glassWidth = std::min({ layout.width - 2 * kBezel, kMaxGlassWidth, 300 * cellWidth + 2 * kTextPadding });
        layout.glassHeight = std::min({ layout.height - header - footer, kMaxGlassHeight, 200 * cellHeight + 2 * kTextPadding });
        layout.cols = (layout.glassWidth - 2 * kTextPadding) / cellWidth;
        layout.rows = (layout.glassHeight - 2 * kTextPadding) / cellHeight;
        if (layout.cols < 20 || layout.rows < 10) {
            layout = {};
            return false;
        }
        layout.glassX = (layout.width - layout.glassWidth) / 2;
        layout.glassY = header + (layout.height - header - footer - layout.glassHeight) / 2;
        layout.textX = (layout.glassWidth - layout.cols * cellWidth) / 2;
        layout.textY = (layout.glassHeight - layout.rows * cellHeight) / 2;
        layout.statusY = layout.height - footer + 8;
        return true;
    }

    bool TerminalCrt::initialize(int width, int height, bool retro)
    {
        width_ = 0;
        height_ = 0;
        samples_.clear();
        if (width < 2 || width > kMaxGlassWidth || height < 2 || height > kMaxGlassHeight) return false;
        width_ = width;
        height_ = height;
        retro_ = retro;
        if (!retro) return true;
        samples_.reserve(static_cast<size_t>(width) * height);
        for (int y = 0; y < height; y++) {
            const double ny = (2.0 * y + 1.0) / height - 1.0;
            for (int x = 0; x < width; x++) {
                const double nx = (2.0 * x + 1.0) / width - 1.0;
                // Inverse barrel projection bends straight grid edges inward. The
                // source includes padding, so the entire first/last cell survives.
                const double u = nx * (1.0 + 0.09 * ny * ny);
                const double v = ny * (1.0 + 0.09 * nx * nx);
                if (std::abs(u) > 1.0 || std::abs(v) > 1.0) {
                    samples_.push_back({ -1, 0, 0, 0 });
                    continue;
                }
                // Work at pixel centers, like a texture sampler. Using outer pixel
                // edges would mask the entire border on even-sized apertures, where
                // no pixel lies exactly on the horizontal or vertical centerline.
                const double sourceX = std::clamp((u + 1.0) * width / 2 - 0.5, 0.0, static_cast<double>(width - 1));
                const double sourceY = std::clamp((v + 1.0) * height / 2 - 0.5, 0.0, static_cast<double>(height - 1));
                const int sx = std::min(static_cast<int>(sourceX), width - 2);
                const int sy = std::min(static_cast<int>(sourceY), height - 2);
                int shade = 250 - static_cast<int>(22 * (nx * nx + ny * ny));
                // A restrained three-pixel scan pattern keeps small engine fonts
                // readable at 640x480; it never blanks a complete glyph row.
                if (y % 3 == 2) shade = shade * 88 / 100;
                samples_.push_back({ sy * width + sx,
                    static_cast<uint8_t>(std::clamp(static_cast<int>((sourceX - sx) * 255), 0, 255)),
                    static_cast<uint8_t>(std::clamp(static_cast<int>((sourceY - sy) * 255), 0, 255)),
                    static_cast<uint8_t>(shade) });
            }
        }
        return true;
    }

    void TerminalCrt::render(const unsigned char* source, unsigned char* destination, int pitch,
        const std::array<unsigned char, 256>& luminance,
        const std::array<unsigned char, 256>& phosphor) const
    {
        if (source == nullptr || destination == nullptr || pitch < width_ || width_ == 0) return;
        if (!retro_) {
            for (int y = 0; y < height_; y++) {
                std::copy_n(source + y * width_, width_, destination + y * pitch);
            }
            return;
        }
        for (int y = 0; y < height_; y++) {
            for (int x = 0; x < width_; x++) {
                const Sample& sample = samples_[static_cast<size_t>(y) * width_ + x];
                if (sample.offset < 0) {
                    destination[y * pitch + x] = phosphor[0];
                    continue;
                }
                const unsigned char* pixels = source + sample.offset;
                const int a = luminance[pixels[0]];
                const int b = luminance[pixels[1]];
                const int c = luminance[pixels[width_]];
                const int d = luminance[pixels[width_ + 1]];
                const int top = a * (255 - sample.xFraction) + b * sample.xFraction;
                const int bottom = c * (255 - sample.xFraction) + d * sample.xFraction;
                const int light = (top * (255 - sample.yFraction) + bottom * sample.yFraction) / 65025;
                // Bilinear phosphor plus a local halo retains one-pixel strokes
                // under minification. Both foreground and inverse video stay legible.
                const int glow = std::max({ a, b, c, d }) / 12;
                const int intensity = std::min(255, light + glow) * sample.shade / 255;
                destination[y * pitch + x] = phosphor[intensity];
            }
        }
    }

} // namespace nixboy
} // namespace fallout
