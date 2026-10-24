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
        constexpr int kTextPadding = 8;
        constexpr int kBloomScale = 4;

    } // namespace

    bool terminalViewLayout(int screenWidth, int screenHeight, int cellWidth,
        int cellHeight, bool fullscreen, TerminalViewLayout& layout, int chromeCellHeight)
    {
        layout = {};
        if (chromeCellHeight == 0) chromeCellHeight = cellHeight;
        if (screenWidth < 320 || screenHeight < 240 || screenWidth > 16384
            || screenHeight > 16384 || cellWidth <= 0 || cellWidth > 64
            || cellHeight <= 1 || cellHeight > 64 || chromeCellHeight < 2 || chromeCellHeight > 64) {
            return false;
        }
        layout.width = fullscreen ? screenWidth : std::min(1280, screenWidth - std::max(32, screenWidth / 8));
        layout.height = fullscreen ? screenHeight : std::min(900, screenHeight - std::max(32, screenHeight / 7));
        layout.x = (screenWidth - layout.width) / 2;
        layout.y = (screenHeight - layout.height) / 2;
        const int header = std::max(36, 2 * chromeCellHeight + 12);
        layout.glassY = header;
        layout.glassWidth = std::min({ layout.width - 2 * kBezel, kMaxGlassWidth, 300 * cellWidth + 2 * kTextPadding });
        layout.glassHeight = std::min({ layout.height - layout.glassY - kBezel, kMaxGlassHeight, 200 * cellHeight + 2 * kTextPadding });
        layout.cols = (layout.glassWidth - 2 * kTextPadding) / cellWidth;
        layout.rows = (layout.glassHeight - 2 * kTextPadding) / cellHeight;
        if (layout.cols < 20 || layout.rows < 10) {
            layout = {};
            return false;
        }
        layout.glassX = (layout.width - layout.glassWidth) / 2;
        layout.textX = (layout.glassWidth - layout.cols * cellWidth) / 2;
        layout.textY = (layout.glassHeight - layout.rows * cellHeight) / 2;
        layout.statusY = layout.glassY + layout.glassHeight;
        return true;
    }

    bool TerminalCrt::initialize(int width, int height, const TerminalCrtSettings& settings)
    {
        width_ = 0;
        height_ = 0;
        samples_.clear();
        if (width < 8 || width > kMaxGlassWidth || height < 8 || height > kMaxGlassHeight
            || settings.warp < kTerminalCrtStrengthMin || settings.warp > kTerminalCrtStrengthMax
            || settings.glow < kTerminalCrtStrengthMin || settings.glow > kTerminalCrtStrengthMax
            || settings.scanlines < kTerminalCrtStrengthMin || settings.scanlines > kTerminalCrtStrengthMax
            || settings.noise < kTerminalCrtStrengthMin || settings.noise > kTerminalCrtStrengthMax
            || settings.jitter < kTerminalCrtStrengthMin || settings.jitter > kTerminalCrtStrengthMax
            || settings.bloom < kTerminalCrtStrengthMin || settings.bloom > kTerminalCrtStrengthMax
            || settings.rgbShift < kTerminalCrtStrengthMin || settings.rgbShift > kTerminalCrtStrengthMax
            || settings.persistenceMs < kTerminalCrtPersistenceMinMs || settings.persistenceMs > kTerminalCrtPersistenceMaxMs) {
            return false;
        }
        width_ = width;
        height_ = height;
        settings_ = settings;
        bloomWidth_ = std::max(2, (width + kBloomScale - 1) / kBloomScale);
        bloomHeight_ = std::max(2, (height + kBloomScale - 1) / kBloomScale);
        const size_t pixels = static_cast<size_t>(width) * height;
        samples_.reserve(pixels);
        glass_.resize(pixels);
        current_.assign(pixels, 0);
        history_.assign(pixels, 0);
        emission_.assign(pixels, 0);
        localGlow_.assign(pixels, 0);
        const size_t bloomPixels = static_cast<size_t>(bloomWidth_) * bloomHeight_;
        downsampled_.assign(bloomPixels, 0);
        bloomNear_.assign(bloomPixels, 0);
        bloomWide_.assign(bloomPixels, 0);
        scratch_.assign(bloomPixels, 0);
        bloomX_.resize(width);
        bloomY_.resize(height);
        bloomXFraction_.resize(width);
        bloomYFraction_.resize(height);
        for (int value = 0; value < 256; value++) {
            tone_[value] = static_cast<uint8_t>(std::pow(value / 255.0, 0.85) * 255.0 + 0.5);
        }
        for (int x = 0; x < width; x++) {
            const double position = std::clamp((x + 0.5) / kBloomScale - 0.5, 0.0, static_cast<double>(bloomWidth_ - 1));
            bloomX_[x] = std::min(static_cast<int>(position), bloomWidth_ - 2);
            bloomXFraction_[x] = static_cast<uint8_t>(std::clamp(static_cast<int>((position - bloomX_[x]) * 255), 0, 255));
        }
        for (int y = 0; y < height; y++) {
            const double position = std::clamp((y + 0.5) / kBloomScale - 0.5, 0.0, static_cast<double>(bloomHeight_ - 1));
            bloomY_[y] = std::min(static_cast<int>(position), bloomHeight_ - 2);
            bloomYFraction_[y] = static_cast<uint8_t>(std::clamp(static_cast<int>((position - bloomY_[y]) * 255), 0, 255));
        }
        const double radius = std::min(6.0, std::min(width, height) * 0.12);
        for (int y = 0; y < height; y++) {
            const double ny = (2.0 * y + 1.0) / height - 1.0;
            for (int x = 0; x < width; x++) {
                const double nx = (2.0 * x + 1.0) / width - 1.0;
                // The lens only projects source light. Its opaque glass stays in
                // the native frame opening instead of leaving a bowed black moat.
                const double radialSquared = nx * nx + ny * ny;
                const double radial = 1.0 + settings_.warp / 100.0 * (0.055 * radialSquared + 0.002 * radialSquared * radialSquared);
                // The zero setting must not acquire fractional sampling through
                // floating-point roundoff: it preserves the source pixel positions.
                const double sx = settings_.warp == 0 ? x : (nx * radial + 1.0) * width / 2 - 0.5;
                const double sy = settings_.warp == 0 ? y : (ny * radial + 1.0) * height / 2 - 0.5;
                const double dx = std::max(std::abs(x - (width - 1) / 2.0) - (width / 2.0 - radius), 0.0);
                const double dy = std::max(std::abs(y - (height - 1) / 2.0) - (height / 2.0 - radius), 0.0);
                const double roundedDistance = radius - std::sqrt(dx * dx + dy * dy);
                const double edge = std::min({ x + 0.5, y + 0.5, width - 0.5 - x, height - 0.5 - y, roundedDistance });
                const int coverage = std::clamp(static_cast<int>((edge + 0.5) * 255), 0, 255);
                if (coverage == 0) {
                    samples_.push_back({ -1, 0, 0, 0, 0 });
                    glass_[static_cast<size_t>(y) * width + x] = 0;
                    continue;
                }
                const double sourceX = std::clamp(sx, 0.0, static_cast<double>(width - 1));
                const double sourceY = std::clamp(sy, 0.0, static_cast<double>(height - 1));
                const int ix = std::min(static_cast<int>(sourceX), width - 2);
                const int iy = std::min(static_cast<int>(sourceY), height - 2);
                const int shade = 255 - static_cast<int>(28 * (nx * nx + ny * ny));
                samples_.push_back({ iy * width + ix,
                    static_cast<uint8_t>(std::clamp(static_cast<int>((sourceX - ix) * 255), 0, 255)),
                    static_cast<uint8_t>(std::clamp(static_cast<int>((sourceY - iy) * 255), 0, 255)),
                    static_cast<uint8_t>(shade), static_cast<uint8_t>(coverage) });
                const double reflection = std::max(0.0, 1.0 - edge / 3.0) * (1.0 - ny) * 3.0;
                glass_[static_cast<size_t>(y) * width + x] = static_cast<uint8_t>(8 + (1.0 - ny) * 2 + reflection);
            }
        }
        hasPreviousTicks_ = false;
        lightDirty_ = true;
        hasTrails_ = false;
        return true;
    }

    void TerminalCrt::update(const unsigned char* source, const std::array<unsigned char, 256>& luminance)
    {
        if (source == nullptr || width_ == 0) return;
        for (size_t index = 0; index < samples_.size(); index++) {
            const Sample& sample = samples_[index];
            if (sample.offset < 0) {
                current_[index] = 0;
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
            current_[index] = tone_[light];
        }
        lightDirty_ = true;
    }

    void TerminalCrt::blur(const std::vector<uint16_t>& source, std::vector<uint16_t>& destination,
        int radius, bool horizontal)
    {
        const int lines = horizontal ? bloomHeight_ : bloomWidth_;
        const int length = horizontal ? bloomWidth_ : bloomHeight_;
        const int stride = horizontal ? 1 : bloomWidth_;
        for (int line = 0; line < lines; line++) {
            const int base = horizontal ? line * bloomWidth_ : line;
            unsigned int sum = 0;
            for (int step = -radius; step <= radius; step++) {
                sum += source[base + std::clamp(step, 0, length - 1) * stride];
            }
            for (int position = 0; position < length; position++) {
                destination[base + position * stride] = static_cast<uint16_t>(sum / (2 * radius + 1));
                sum -= source[base + std::clamp(position - radius, 0, length - 1) * stride];
                sum += source[base + std::clamp(position + radius + 1, 0, length - 1) * stride];
            }
        }
    }

    void TerminalCrt::updateLight(unsigned int elapsed)
    {
        if (!lightDirty_ && !hasTrails_) return;
        const int decay = settings_.persistenceMs == 0 ? 0
                                                       : static_cast<int>(std::pow(0.1, elapsed / static_cast<double>(settings_.persistenceMs)) * 256);
        hasTrails_ = false;
        for (size_t index = 0; index < current_.size(); index++) {
            history_[index] = static_cast<uint8_t>(std::max<int>(current_[index], history_[index] * decay / 256));
            hasTrails_ = hasTrails_ || history_[index] > current_[index];
            emission_[index] = static_cast<uint8_t>(std::max<int>(current_[index], history_[index] * 3 / 8));
        }
        lightDirty_ = false;
        if (settings_.glow != 0) {
            // A narrow, full-resolution phosphor halo is deliberately separate
            // from the broad quarter-resolution optical bloom below.
            for (int y = 0; y < height_; y++) {
                for (int x = 0; x < width_; x++) {
                    const int index = y * width_ + x;
                    const int left = y * width_ + std::max(0, x - 1);
                    const int right = y * width_ + std::min(width_ - 1, x + 1);
                    const int above = std::max(0, y - 1) * width_ + x;
                    const int below = std::min(height_ - 1, y + 1) * width_ + x;
                    localGlow_[index] = static_cast<uint8_t>((emission_[index] * 4
                                                                 + emission_[left] + emission_[right] + emission_[above] + emission_[below])
                        / 8);
                }
            }
        }
        if (settings_.bloom == 0) return;
        for (int by = 0; by < bloomHeight_; by++) {
            for (int bx = 0; bx < bloomWidth_; bx++) {
                int sum = 0;
                int peak = 0;
                int count = 0;
                for (int y = by * kBloomScale; y < std::min(height_, (by + 1) * kBloomScale); y++) {
                    for (int x = bx * kBloomScale; x < std::min(width_, (bx + 1) * kBloomScale); x++) {
                        const int light = emission_[static_cast<size_t>(y) * width_ + x];
                        sum += light;
                        peak = std::max(peak, light);
                        count++;
                    }
                }
                // Keep 8.8 precision through every blur pass: faint wide halos must
                // not disappear through repeated integer rounding in a dark scene.
                downsampled_[by * bloomWidth_ + bx] = static_cast<uint16_t>((sum * 256 / std::max(1, count) + peak * 256) / 2);
            }
        }
        blur(downsampled_, scratch_, 1, true);
        blur(scratch_, bloomNear_, 1, false);
        blur(bloomNear_, scratch_, 1, true);
        blur(scratch_, bloomNear_, 1, false);
        blur(downsampled_, scratch_, 3, true);
        blur(scratch_, bloomWide_, 3, false);
        blur(bloomWide_, scratch_, 3, true);
        blur(scratch_, bloomWide_, 3, false);
    }

    int TerminalCrt::bloomAt(const std::vector<uint16_t>& bloom, int x, int y) const
    {
        const int offset = bloomY_[y] * bloomWidth_ + bloomX_[x];
        const int xf = bloomXFraction_[x];
        const int yf = bloomYFraction_[y];
        const int top = (bloom[offset] * (255 - xf) + bloom[offset + 1] * xf) / 255;
        const int bottom = (bloom[offset + bloomWidth_] * (255 - xf) + bloom[offset + bloomWidth_ + 1] * xf) / 255;
        return (top * (255 - yf) + bottom * yf) / 255;
    }

    int TerminalCrt::lightAt(const std::vector<uint8_t>& light, int x256, int y) const
    {
        x256 = std::clamp(x256, 0, (width_ - 1) * 256);
        const int x = x256 / 256;
        const int fraction = x256 % 256;
        return (light[y * width_ + x] * (256 - fraction)
                   + light[y * width_ + std::min(x + 1, width_ - 1)] * fraction)
            / 256;
    }

    void TerminalCrt::render(uint32_t* destination, int pitchPixels, uint32_t ticks)
    {
        if (destination == nullptr || pitchPixels < width_ || width_ == 0) return;
        const unsigned int elapsed = hasPreviousTicks_ ? std::min(ticks - previousTicks_, 20000u) : 33u;
        previousTicks_ = ticks;
        hasPreviousTicks_ = true;
        updateLight(elapsed);
        const int flicker = 256 + static_cast<int>((3 * std::sin(ticks * 0.017) + 2 * std::sin(ticks * 0.0113)) * settings_.noise / 100);
        const uint32_t noiseFrame = ticks / 33;
        const int chroma256 = settings_.rgbShift * 768 / 100;
        for (int y = 0; y < height_; y++) {
            uint32_t lineHash = static_cast<uint32_t>(y / 3) * 668265263u + noiseFrame * 2246822519u;
            lineHash = (lineHash ^ (lineHash >> 13)) * 1274126177u;
            const int jitter256 = (static_cast<int>((lineHash >> 16) & 255) - 128) * settings_.jitter * 6 / 100;
            for (int x = 0; x < width_; x++) {
                const size_t index = static_cast<size_t>(y) * width_ + x;
                const Sample& sample = samples_[index];
                if (sample.offset < 0) {
                    destination[y * pitchPixels + x] = 0;
                    continue;
                }
                const int sourceX256 = x * 256 + jitter256;
                const int light = settings_.jitter == 0 ? emission_[index] : lightAt(emission_, sourceX256, y);
                int core = light * sample.shade / 255;
                const int raster = (y & 1) ? 256 - settings_.scanlines * 166 / 100 : 256;
                core = core * raster / 256;
                core = core * flicker / 256;
                const int phosphor = settings_.glow == 0 ? 0
                                                         : lightAt(localGlow_, sourceX256, y) * settings_.glow * 3 / (100 * 4);
                const int near = settings_.bloom == 0 ? 0 : bloomAt(bloomNear_, x, y);
                const int wide = settings_.bloom == 0 ? 0 : bloomAt(bloomWide_, x, y);
                const int bloom = (near * 3 / 4 + wide / 2) * settings_.bloom / (256 * 100);
                uint32_t hash = static_cast<uint32_t>(x) * 374761393u
                    + static_cast<uint32_t>(y) * 668265263u + noiseFrame * 2246822519u;
                hash = (hash ^ (hash >> 13)) * 1274126177u;
                const int grain = static_cast<int>((hash >> 24) & 255) - 128;
                // Stochastic rounding keeps even strength 1 observable, without
                // forcing an entire extra brightness step onto every dark pixel.
                const int rounding = static_cast<int>((hash >> 8) & 255);
                const int noise = (grain * settings_.noise + (grain < 0 ? -rounding : rounding)) / 300;
                const int ambient = glass_[index];
                const int redFringe = settings_.rgbShift == 0 ? 0
                                                              : std::max(0, lightAt(emission_, sourceX256 + chroma256, y) - light) * settings_.rgbShift / 200;
                const int blueLight = settings_.rgbShift == 0 ? core
                                                              : (core * (100 - settings_.rgbShift)
                                                                    + lightAt(emission_, sourceX256 - chroma256, y) * settings_.rgbShift * sample.shade * raster / (255 * 256))
                        / 100;
                const int halo = phosphor + bloom;
                const int red = std::clamp(1 + core * 12 / 255 + halo / 24 + redFringe, 0, 255) * sample.coverage / 255;
                const int green = std::clamp(ambient + core * 204 / 255 + halo + noise, 0, 255) * sample.coverage / 255;
                const int blue = std::clamp(3 + blueLight * 104 / 255 + halo * 3 / 8 + noise / 2, 0, 255) * sample.coverage / 255;
                destination[y * pitchPixels + x] = static_cast<uint32_t>((red << 16) | (green << 8) | blue);
            }
        }
    }

} // namespace nixboy
} // namespace fallout
