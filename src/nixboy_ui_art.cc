#include "nixboy_ui_art.h"

#include <algorithm>

#include "art.h"
#include "color.h"
#include "draw.h"
#include "text_font.h"
#include "window_manager.h"

namespace fallout {
namespace {

    struct ArtRegion {
        int x;
        int y;
        int width;
        int height;
    };

    void tileArt(unsigned char* destination, int pitch, int x, int y,
        int width, int height, const FrmImage& art, const ArtRegion& source)
    {
        for (int dy = 0; dy < height; dy += source.height) {
            for (int dx = 0; dx < width; dx += source.width) {
                blitBufferToBuffer(art.getData() + source.y * art.getWidth() + source.x,
                    std::min(source.width, width - dx), std::min(source.height, height - dy),
                    art.getWidth(), destination + (y + dy) * pitch + x + dx, pitch);
            }
        }
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

bool nixboyDrawTerminalFrame(int window, int width, int height,
    int glassX, int glassY, int glassWidth, int glassHeight, int statusY)
{
    unsigned char* buffer = windowGetBuffer(window);
    if (buffer == nullptr || width != windowGetWidth(window) || height != windowGetHeight(window)
        || width < 96 || height < 96 || glassX < 14 || glassY < 14
        || glassWidth < 48 || glassHeight < 48 || glassX + glassWidth > width - 14
        || glassY + glassHeight > height - 14 || statusY < glassY + glassHeight
        || statusY >= height) {
        return false;
    }
    FrmImage panel;
    FrmImage dialog;
    // The same artwork used by optionsWindowInit and showDialogBox.
    if (!panel.lock(buildFid(OBJ_TYPE_INTERFACE, 220, 0, 0, 0))
        || !dialog.lock(buildFid(OBJ_TYPE_INTERFACE, 217, 0, 0, 0))
        || panel.getWidth() < 164 || panel.getHeight() < 217
        || dialog.getWidth() < 302 || dialog.getHeight() < 127) {
        return false;
    }

    // OPBASE.FRM: native corner screws, border strips and uninterrupted metal.
    constexpr int edge = 16;
    tileArt(buffer, width, edge, edge, width - 2 * edge, height - 2 * edge,
        panel, { edge, edge, 132, 185 });
    for (int side = 0; side < 2; side++) {
        const int sourceX = side == 0 ? 0 : 148;
        const int destinationX = side == 0 ? 0 : width - edge;
        tileArt(buffer, width, destinationX, edge, edge, height - 2 * edge,
            panel, { sourceX, edge, edge, 185 });
        const int sourceY = side == 0 ? 0 : 201;
        const int destinationY = side == 0 ? 0 : height - edge;
        tileArt(buffer, width, edge, destinationY, width - 2 * edge, edge,
            panel, { edge, sourceY, 132, edge });
        for (int vertical = 0; vertical < 2; vertical++) {
            tileArt(buffer, width, destinationX, vertical == 0 ? 0 : height - edge,
                edge, edge, panel, { sourceX, vertical == 0 ? 0 : 201, edge, edge });
        }
    }

    // LGDIALOG.FRM: preserve the actual rounded glass-bezel corners. Repeat
    // clean edge strips that exclude the native top-center latch; no stretching.
    const int left = glassX - 14;
    const int top = glassY - 14;
    const int outerWidth = glassWidth + 28;
    const int outerHeight = glassHeight + 28;
    constexpr int corner = 24;
    tileArt(buffer, width, left + corner, top, outerWidth - 2 * corner, 14,
        dialog, { 36, 6, 90, 14 });
    tileArt(buffer, width, left + corner, top + outerHeight - 14,
        outerWidth - 2 * corner, 14, dialog, { 36, 83, 90, 14 });
    tileArt(buffer, width, left, top + corner, 14, outerHeight - 2 * corner,
        dialog, { 9, 35, 14, 38 });
    tileArt(buffer, width, left + outerWidth - 14, top + corner,
        14, outerHeight - 2 * corner, dialog, { 285, 35, 14, 38 });
    tileArt(buffer, width, left, top, corner, corner, dialog, { 9, 5, corner, corner });
    tileArt(buffer, width, left + outerWidth - corner, top,
        corner, corner, dialog, { 275, 5, corner, corner });
    tileArt(buffer, width, left, top + outerHeight - corner,
        corner, corner, dialog, { 9, 73, corner, corner });
    tileArt(buffer, width, left + outerWidth - corner, top + outerHeight - corner,
        corner, corner, dialog, { 275, 73, corner, corner });
    return true;
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
