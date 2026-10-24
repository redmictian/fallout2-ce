#include "nixboy_ui_art.h"

#include <algorithm>
#include <cstdint>

#include "art.h"
#include "color.h"
#include "text_font.h"
#include "window_manager.h"

namespace fallout {
namespace {

    int sampleNativeRun(int position, int length, int sourceStart, int sourceLength)
    {
        if (length <= 1) return sourceStart + sourceLength / 2;
        // Keep both native endpoints. Adjacent runs therefore meet at adjacent
        // source pixels, rather than restarting a small, visibly repeated tile.
        return sourceStart + static_cast<int>(static_cast<int64_t>(position) * (sourceLength - 1) / (length - 1));
    }

    bool isPaint(unsigned char pixel)
    {
        const int red = _cmap[3 * pixel];
        const int green = _cmap[3 * pixel + 1];
        const int blue = _cmap[3 * pixel + 2];
        // Identify the actual ochre paint, preserving its indexed weathering.
        // Rust, scratches and bare panel pixels must remain at the destination.
        return red >= 18 && green >= 18 && std::min(red, green) >= 2 * blue;
    }

    bool nativeLabelMatches(const unsigned char* background, int pitch)
    {
        const auto count = [&](int x, int y, int width, int height) {
            int result = 0;
            for (int row = 0; row < height; row++) {
                for (int col = 0; col < width; col++) {
                    if (isPaint(background[(y + row) * pitch + x + col])) result++;
                }
            }
            return result;
        };
        // Fixed coordinates are specific to the original English painted art.
        // Check both paint coverage and the O's hole before borrowing strokes;
        // translated/replacement resources can fall back to a real game font.
        const int i = count(116, 422, 3, 13);
        const int o = count(103, 395, 11, 13);
        const int r = count(84, 422, 9, 13);
        const int v = count(121, 422, 10, 13);
        return i >= 24 && i <= 36 && o >= 55 && o <= 95
            && r >= 45 && r <= 90 && v >= 35 && v <= 75
            && count(107, 399, 3, 5) == 0
            && count(103, 399, 3, 5) >= 6 && count(111, 399, 3, 5) >= 6;
    }

    class PaintedLabel {
    public:
        PaintedLabel(unsigned char* destination, const unsigned char* background, int pitch)
            : destination_(destination)
            , background_(background)
            , pitch_(pitch)
        {
        }

        void pixel(int sourceX, int sourceY, int x, int y)
        {
            const unsigned char value = background_[sourceY * pitch_ + sourceX];
            if (isPaint(value)) {
                destination_[y * pitch_ + x] = value;
            }
        }

        void stroke(int sourceX, int sourceY, int width, int height, int x, int y)
        {
            for (int row = 0; row < height; row++) {
                for (int col = 0; col < width; col++) {
                    pixel(sourceX + col, sourceY + row, x + col, y + row);
                }
            }
        }

    private:
        unsigned char* destination_;
        const unsigned char* background_;
        int pitch_;
    };

} // namespace

bool nixboyDrawFrame(unsigned char* buffer, int width, int height,
    int glassX, int glassY, int glassWidth, int glassHeight)
{
    if (buffer == nullptr || width < 96 || height < 96 || glassX < 14 || glassY < 14
        || glassWidth < 48 || glassHeight < 48 || glassX + glassWidth > width - 14
        || glassY + glassHeight > height - 14) {
        return false;
    }
    FrmImage frame;
    // AUTOMAP.FRM supplies one coherent tube housing: native lighting, wear,
    // rounded bevel and top latch. Exclude its Scanner/Cancel plates and knob.
    if (!frame.lock(buildFid(OBJ_TYPE_INTERFACE, 171, 0, 0, 0))
        || frame.getWidth() < 519 || frame.getHeight() < 480) {
        return false;
    }

    const int right = glassX + glassWidth;
    const int bottom = glassY + glassHeight;
    const int latchWidth = std::min(64, glassWidth / 2);
    const int leftRun = (glassWidth - latchWidth) / 2;
    const int latchX = glassX + leftRun;
    const auto nativeX = [&](int x) {
        if (x < glassX) return sampleNativeRun(x, glassX, 20, 18);
        if (x >= right) return sampleNativeRun(x - right, width - right, 446, 18);
        if (x < latchX) return sampleNativeRun(x - glassX, leftRun, 38, 170);
        if (x < latchX + latchWidth) return sampleNativeRun(x - latchX, latchWidth, 208, 64);
        return sampleNativeRun(x - latchX - latchWidth,
            glassWidth - leftRun - latchWidth, 272, 174);
    };
    for (int y = 0; y < height; y++) {
        const int sourceY = y < glassY ? sampleNativeRun(y, glassY, 0, 36)
            : y >= bottom              ? sampleNativeRun(y - bottom, height - bottom, 420, 18)
                                       : sampleNativeRun(y - glassY, glassHeight, 36, 384);
        const unsigned char* source = frame.getData() + sourceY * frame.getWidth();
        // Only the surround is copied. The terminal owns the entire glass area.
        // At normal 18/36/18 margins, corner pixels and bevel thickness are 1:1;
        // only each long edge's longitudinal wear is extended to the new size.
        for (int x = 0; x < width; x++) {
            if (y >= glassY && y < bottom && x >= glassX && x < right) continue;
            buffer[y * width + x] = source[nativeX(x)];
        }
    }
    return true;
}

bool nixboyDrawTerminalFrame(int window, int width, int height,
    int glassX, int glassY, int glassWidth, int glassHeight, int statusY)
{
    if (width != windowGetWidth(window) || height != windowGetHeight(window)
        || statusY < glassY + glassHeight || statusY >= height) {
        return false;
    }
    return nixboyDrawFrame(windowGetBuffer(window), width, height,
        glassX, glassY, glassWidth, glassHeight);
}

bool nixboyDrawPipboyLabel(unsigned char* buffer, int width, int height,
    const unsigned char* background)
{
    if (buffer == nullptr || width != 640 || height != 480) return false;
    if (background == nullptr || !nativeLabelMatches(background, width)) {
        const int oldFont = fontGetCurrent();
        fontSetCurrent(101);
        fontDrawText(buffer + width * 368 + 73, "NIX-BOY", 100, width,
            _colorTable[(23 << 10) | (20 << 5) | 5]);
        fontSetCurrent(oldFont);
        return false;
    }
    PaintedLabel label(buffer, background, width);
    int x = 73;
    constexpr int y = 368;

    // PIP.FRM's yellow labels are painted into the background, not a shipped
    // reusable font. Reuse their exact I/O/strokes at runtime: the new N/X/B/Y
    // outlines are composed, while all paint colors/grime come from that art.
    label.stroke(116, 422, 3, 13, x, y);
    label.stroke(116, 422, 3, 13, x + 8, y);
    for (int row = 0; row < 13; row++) {
        for (int col = 0; col < 3; col++) {
            label.pixel(116 + col, 422 + row, x + row * 8 / 12 + col, y + row);
        }
    }
    x += 13;
    label.stroke(116, 422, 3, 13, x, y); // I from ARCHIVES.
    x += 5;
    for (int row = 0; row < 13; row++) {
        for (int col = 0; col < 3; col++) {
            label.pixel(116 + col, 422 + row, x + row * 8 / 12 + col, y + row);
            label.pixel(116 + col, 422 + row, x + 8 - row * 8 / 12 + col, y + row);
        }
    }
    x += 13;
    label.stroke(106, 427, 6, 2, x, y + 6); // H crossbar as the hyphen.
    x += 8;
    label.stroke(84, 422, 3, 13, x, y);
    label.stroke(84, 422, 9, 7, x, y); // R bowl, repeated to make B.
    label.stroke(84, 422, 9, 7, x, y + 6);
    x += 11;
    label.stroke(103, 395, 11, 13, x, y); // O from AUTOMAPS.
    x += 13;
    for (int row = 0; row < 7; row++) {
        for (int col = 0; col < 10; col++) {
            label.pixel(121 + col, 422 + row * 12 / 6, x + col, y + row);
        }
    }
    label.stroke(116, 428, 3, 7, x + 4, y + 6); // V fork and I tail make Y.
    return true;
}

} // namespace fallout
