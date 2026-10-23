#include "nixboy_terminal_view.h"

#include <algorithm>
#include <array>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <vector>

using fallout::nixboy::TerminalCrt;
using fallout::nixboy::TerminalViewLayout;
using fallout::nixboy::terminalViewLayout;

namespace {

void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

std::array<unsigned char, 256> identityPalette()
{
    std::array<unsigned char, 256> palette;
    for (int index = 0; index < 256; index++)
        palette[index] = static_cast<unsigned char>(index);
    return palette;
}

void testLayouts()
{
    for (const auto& screen : { std::array<int, 2> { 640, 480 }, { 960, 540 },
             { 1280, 720 }, { 1920, 1080 }, { 3840, 2160 }, { 8192, 4320 } }) {
        for (const auto& cell : { std::array<int, 2> { 5, 9 }, { 8, 12 }, { 10, 17 } }) {
            for (bool fullscreen : { false, true }) {
                TerminalViewLayout layout;
                require(terminalViewLayout(screen[0], screen[1], cell[0], cell[1], fullscreen, layout), "supported layout");
                require(layout.width <= screen[0] && layout.height <= screen[1], "window fits screen");
                require(layout.x == (screen[0] - layout.width) / 2
                        && layout.y == (screen[1] - layout.height) / 2,
                    "window is centered");
                if (fullscreen) {
                    require(layout.width == screen[0] && layout.height == screen[1]
                            && layout.x == 0 && layout.y == 0,
                        "fullscreen covers the actual internal screen");
                } else {
                    require(layout.x >= 16 && layout.y >= 16, "modal keeps game UI visible on every edge");
                }
                require(layout.rows >= 10 && layout.rows <= 200
                        && layout.cols >= 20 && layout.cols <= 300,
                    "Linux dimensions satisfy the protocol bounds");
                require(layout.glassWidth <= 2048 && layout.glassHeight <= 1536, "CRT work stays bounded on large screens");
                require(layout.glassX >= 18 && layout.glassY >= 36, "glass leaves accessible chrome");
                require(layout.textX >= 12 && layout.textY >= 12, "CRT grid keeps source padding");
                require(layout.textX + layout.cols * cell[0] <= layout.glassWidth - 12
                        && layout.textY + layout.rows * cell[1] <= layout.glassHeight - 12,
                    "entire last cell fits inside the padded source");
                require(layout.glassY + layout.glassHeight < layout.statusY
                        && layout.statusY + 3 * cell[1] <= layout.height - 3,
                    "glass and status controls cannot overlap");
            }
        }
    }
    TerminalViewLayout layout;
    require(!terminalViewLayout(319, 480, 8, 12, false, layout), "undersized screen rejected");
    require(!terminalViewLayout(640, 239, 8, 12, false, layout), "undersized screen height rejected");
    require(!terminalViewLayout(640, 480, 0, 12, false, layout), "zero cell width rejected");
    require(!terminalViewLayout(640, 480, 8, 1, false, layout), "invalid cell height rejected");
    require(!terminalViewLayout(640, 480, 64, 64, false, layout), "unusable grid rejected");
    require(!terminalViewLayout(20000, 480, 8, 12, true, layout), "unbounded screen rejected");
    require(layout.width == 0 && layout.rows == 0, "failed layout clears prior values");
}

void testBypassAndBounds()
{
    constexpr int width = 160;
    constexpr int height = 120;
    constexpr int pitch = width + 13;
    constexpr int guard = 17;
    const auto palette = identityPalette();
    std::vector<unsigned char> source(width * height);
    for (size_t index = 0; index < source.size(); index++)
        source[index] = static_cast<unsigned char>(index);
    std::vector<unsigned char> target(2 * guard + pitch * height, 253);
    TerminalCrt crt;
    require(crt.initialize(width, height, true), "CRT initialization");
    crt.render(source.data(), target.data() + guard, pitch, palette, palette);
    for (bool retro : { true, false }) {
        require(crt.initialize(width, height, retro), "CRT reinitialization");
        std::fill(target.begin(), target.end(), 253);
        crt.render(source.data(), target.data() + guard, pitch, palette, palette);
        require(std::all_of(target.begin(), target.begin() + guard, [](unsigned char ch) { return ch == 253; })
                && std::all_of(target.end() - guard, target.end(), [](unsigned char ch) { return ch == 253; }),
            "destination edge guards unchanged");
        for (int y = 0; y < height; y++) {
            const auto row = target.begin() + guard + y * pitch;
            require(std::all_of(row + width, row + pitch, [](unsigned char ch) { return ch == 253; }), "destination pitch guards unchanged");
            if (!retro) {
                require(std::equal(row, row + width, source.begin() + y * width), "disabled CRT preserves every palette index");
            }
        }
    }
    const auto unchanged = target;
    crt.render(source.data(), target.data() + guard, width - 1, palette, palette);
    crt.render(nullptr, target.data() + guard, pitch, palette, palette);
    crt.render(source.data(), nullptr, pitch, palette, palette);
    require(target == unchanged, "invalid render arguments cannot write");
    require(!crt.initialize(2049, height, true), "unbounded CRT width rejected");
    crt.render(source.data(), target.data() + guard, pitch, palette, palette);
    require(target == unchanged, "failed reinitialization cannot render old geometry");
    require(!crt.initialize(width, 1537, true) && !crt.initialize(1, height, true), "other invalid CRT sizes rejected");
}

void testWarpPreservesEdgeCells()
{
    constexpr int width = 160;
    constexpr int height = 120;
    constexpr int cellWidth = 6;
    constexpr int cellHeight = 9;
    constexpr int cols = 22;
    constexpr int rows = 10;
    constexpr int textX = (width - cols * cellWidth) / 2;
    constexpr int textY = (height - rows * cellHeight) / 2;
    const auto palette = identityPalette();
    std::vector<unsigned char> source(width * height, 0);
    std::vector<unsigned char> target(width * height, 0);
    TerminalCrt crt;
    require(crt.initialize(width, height, true), "CRT edge test initialized");
    for (int row = 0; row < rows; row++) {
        for (int col = 0; col < cols; col++) {
            if (row != 0 && row != rows - 1 && col != 0 && col != cols - 1) continue;
            std::fill(source.begin(), source.end(), 0);
            for (int y = 0; y < cellHeight; y++) {
                std::fill_n(source.begin() + (textY + row * cellHeight + y) * width + textX + col * cellWidth, cellWidth, 255);
            }
            crt.render(source.data(), target.data(), width, palette, palette);
            const int visible = static_cast<int>(std::count_if(target.begin(), target.end(), [](unsigned char value) { return value > 150; }));
            require(visible >= cellWidth * cellHeight / 3, "each edge/corner cell and inverse cursor remains visible under warp");
        }
    }

    std::fill(source.begin(), source.end(), 255);
    crt.render(source.data(), target.data(), width, palette, palette);
    require(target.front() == 0 && target[width - 1] == 0
            && target[(height - 1) * width] == 0 && target.back() == 0,
        "barrel projection creates bowed glass corners");
    require(target[width / 2] > 150 && target[height / 2 * width] > 150
            && target[(height - 1) * width + width / 2] > 150,
        "glass remains illuminated at edge centers");
    require(target[(height / 2) * width + width / 2] > target[2 * width + width / 2], "vignette dims glass edges");
    require(target[2 * width + width / 2] < target[3 * width + width / 2], "scanlines modulate without blank rows");

    std::fill(source.begin(), source.end(), 0);
    for (int y = textY; y < height - textY; y++)
        source[y * width + textX] = 255;
    crt.render(source.data(), target.data(), width, palette, palette);
    const auto peakAt = [&](int y) {
        const auto row = target.begin() + y * width;
        return static_cast<int>(std::max_element(row, row + width / 2) - row);
    };
    require(peakAt(22) > peakAt(height / 2) + 1, "straight terminal strokes visibly bend toward corners");
    require(*std::max_element(target.begin(), target.end()) > 100, "one-pixel glyph strokes survive filtering");
}

} // namespace

int main()
{
    try {
        testLayouts();
        testBypassAndBounds();
        testWarpPreservesEdgeCells();
        std::cout << "nix-boy terminal layout and CRT: all tests passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << "nix-boy terminal view failure: " << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
