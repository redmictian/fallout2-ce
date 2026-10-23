#include "nixboy_terminal_font.h"
#include "nixboy_terminal_view.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

using fallout::nixboy::TerminalCrt;
using fallout::nixboy::TerminalViewLayout;
using fallout::nixboy::terminalViewLayout;

namespace {

void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

int green(uint32_t pixel)
{
    return static_cast<int>((pixel >> 8) & 255);
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
        for (bool fullscreen : { false, true }) {
            TerminalViewLayout layout;
            require(terminalViewLayout(screen[0], screen[1], 8, 16, fullscreen, layout, 11), "supported terminal/font layout");
            require(layout.width <= screen[0] && layout.height <= screen[1], "window fits screen");
            require(layout.x == (screen[0] - layout.width) / 2 && layout.y == (screen[1] - layout.height) / 2, "window is centered");
            if (fullscreen) {
                require(layout.width == screen[0] && layout.height == screen[1] && layout.x == 0 && layout.y == 0,
                    "fullscreen covers the actual internal screen");
            } else {
                require(layout.x >= 16 && layout.y >= 16, "modal keeps game UI visible on every edge");
            }
            require(layout.rows >= 10 && layout.rows <= 200 && layout.cols >= 20 && layout.cols <= 300, "Linux grid stays within protocol limits");
            require(layout.glassWidth <= 2048 && layout.glassHeight <= 1536, "CRT work stays bounded");
            require(layout.glassX >= 18 && layout.glassY >= 36, "native chrome and close button fit");
            require(layout.textX >= 20 && layout.textY >= 20
                    && layout.textX + layout.cols * 8 <= layout.glassWidth - 20
                    && layout.textY + layout.rows * 16 <= layout.glassHeight - 20,
                "whole first/last cells fit within rounded aperture padding");
            require(layout.statusY >= layout.glassY + layout.glassHeight + 18
                    && layout.statusY + 33 <= layout.height - 3,
                "status clears native14px bezel and uses chrome metrics");
        }
    }
    TerminalViewLayout layout;
    require(!terminalViewLayout(319, 480, 8, 16, false, layout, 11), "undersized width rejected");
    require(!terminalViewLayout(640, 239, 8, 16, false, layout, 11), "undersized height rejected");
    require(!terminalViewLayout(640, 480, 0, 16, false, layout, 11), "zero cell width rejected");
    require(!terminalViewLayout(640, 480, 8, 1, false, layout, 11), "invalid cell height rejected");
    require(!terminalViewLayout(640, 480, 8, 16, false, layout, 1), "invalid chrome height rejected");
    require(!terminalViewLayout(640, 480, 64, 64, false, layout, 11), "unusable grid rejected");
    require(!terminalViewLayout(20000, 480, 8, 16, true, layout, 11), "unbounded screen rejected");
    require(layout.width == 0 && layout.rows == 0, "failed layout clears previous values");
}

void testFontBounds()
{
    constexpr int pitch = 19;
    constexpr int height = 22;
    std::vector<unsigned char> target(pitch * height, 17);
    fallout::nixboy::drawTerminalGlyph(target.data(), pitch, 5, 3, 'M', 231);
    int ink = 0;
    for (int y = 0; y < height; y++) {
        for (int x = 0; x < pitch; x++) {
            const unsigned char pixel = target[y * pitch + x];
            if (x >= 5 && x < 13 && y >= 3 && y < 19) {
                require(pixel == 17 || pixel == 231, "font only writes foreground ink");
                if (pixel == 231) ink++;
            } else {
                require(pixel == 17, "glyph stays inside its8x16 cell");
            }
        }
    }
    require(ink >= 15 && ink < 100, "dedicated terminal glyph has legible sparse strokes");
    std::vector<unsigned char> space(target.size(), 17);
    fallout::nixboy::drawTerminalGlyph(space.data(), pitch, 5, 3, ' ', 231);
    require(std::all_of(space.begin(), space.end(), [](unsigned char pixel) { return pixel == 17; }), "space preserves reverse-video background");
    auto fallback = space;
    fallout::nixboy::drawTerminalGlyph(space.data(), pitch, 5, 3, '?', 231);
    fallout::nixboy::drawTerminalGlyph(fallback.data(), pitch, 5, 3, 0x1f600, 231);
    require(space == fallback, "unavailable Unicode glyph uses a visible question mark");
    const auto unchanged = fallback;
    fallout::nixboy::drawTerminalGlyph(fallback.data(), 6, 0, 0, 'X', 231);
    fallout::nixboy::drawTerminalGlyph(fallback.data(), pitch, -1, 0, 'X', 231);
    fallout::nixboy::drawTerminalGlyph(nullptr, pitch, 0, 0, 'X', 231);
    require(fallback == unchanged, "invalid glyph arguments cannot write");
}

void testRgbBounds()
{
    const auto palette = identityPalette();
    for (const auto& size : { std::array<int, 2> { 8, 8 }, { 9, 11 }, { 17, 19 }, { 163, 123 } }) {
        const int width = size[0];
        const int height = size[1];
        const int pitch = width + 13;
        constexpr int guard = 17;
        constexpr uint32_t sentinel = 0xdeadbeef;
        std::vector<unsigned char> source(static_cast<size_t>(width) * height);
        for (size_t index = 0; index < source.size(); index++)
            source[index] = static_cast<unsigned char>(index);
        std::vector<uint32_t> target(2 * guard + pitch * height, sentinel);
        TerminalCrt crt;
        require(crt.initialize(width, height), "small/odd RGB dimensions accepted");
        crt.update(source.data(), palette);
        crt.render(target.data() + guard, pitch, 0xfffffff0u);
        crt.render(target.data() + guard, pitch, 20u); // Wrapped millisecond clock.
        require(std::all_of(target.begin(), target.begin() + guard, [](uint32_t pixel) { return pixel == sentinel; })
                && std::all_of(target.end() - guard, target.end(), [](uint32_t pixel) { return pixel == sentinel; }),
            "RGB edge guards unchanged");
        for (int y = 0; y < height; y++) {
            const auto row = target.begin() + guard + y * pitch;
            require(std::all_of(row + width, row + pitch, [](uint32_t pixel) { return pixel == sentinel; }), "RGB pitch padding unchanged");
            require(std::all_of(row, row + width, [](uint32_t pixel) { return (pixel & 0xff000000u) == 0; }), "output is packed32bit RGB888");
        }
        const auto unchanged = target;
        crt.render(target.data() + guard, width - 1, 30);
        crt.render(nullptr, pitch, 30);
        crt.update(nullptr, palette);
        require(target == unchanged, "invalid render arguments cannot write");
        require(!crt.initialize(2049, height) && !crt.initialize(width, 1537) && !crt.initialize(7, height), "unbounded or undersized CRT rejected");
        crt.render(target.data() + guard, pitch, 30);
        require(target == unchanged, "failed initialization cannot reuse old geometry");
    }
}

void testLensAndEdgeCells()
{
    constexpr int width = 320;
    constexpr int height = 240;
    constexpr int cols = 34;
    constexpr int rows = 12;
    constexpr int textX = 24;
    constexpr int textY = 24;
    const auto palette = identityPalette();
    std::vector<unsigned char> source(width * height, 0);
    std::vector<uint32_t> target(width * height, 0);
    TerminalCrt crt;
    require(crt.initialize(width, height), "edge fixture initialized");
    uint32_t ticks = 0;
    for (int row = 0; row < rows; row++) {
        for (int col = 0; col < cols; col++) {
            if (row != 0 && row != rows - 1 && col != 0 && col != cols - 1) continue;
            std::fill(source.begin(), source.end(), 0);
            for (int y = 0; y < 16; y++)
                std::fill_n(source.begin() + (textY + row * 16 + y) * width + textX + col * 8, 8, 255);
            crt.update(source.data(), palette);
            crt.render(target.data(), width, ticks += 1000);
            require(std::count_if(target.begin(), target.end(), [](uint32_t pixel) { return green(pixel) > 150; }) >= 32,
                "every first/last cell and corner cursor survives rounded radial lens");
        }
    }
    std::fill(source.begin(), source.end(), 255);
    crt.update(source.data(), palette);
    crt.render(target.data(), width, ticks += 1000);
    require(target.front() == 0 && target[width - 1] == 0 && target[(height - 1) * width] == 0 && target.back() == 0,
        "rounded glass corners expose deep black outside aperture");
    require(target[width / 2] == 0 && target[height / 2 * width] == 0, "full radial lens bows cardinal aperture edges");
    require(green(target[12 * width + width / 2]) > 100 && green(target[height / 2 * width + 12]) > 100, "glass lights up inside bowed perimeter");
    require(green(target[height / 2 * width + width / 2]) > 180, "sharp center phosphor remains bright");
    int maskAsymmetry = 0;
    for (int y = 0; y < height; y++) {
        for (int x = 0; x < width / 2; x++) {
            if ((target[y * width + x] != 0) != (target[y * width + width - 1 - x] != 0)) maskAsymmetry++;
        }
    }
    require(maskAsymmetry < 8, "barrel aperture is horizontally symmetric");
}

void testBloomAndTime()
{
    constexpr int width = 320;
    constexpr int height = 240;
    const auto palette = identityPalette();
    std::vector<unsigned char> source(width * height, 0);
    std::vector<uint32_t> target(width * height, 0);
    TerminalCrt crt;
    require(crt.initialize(width, height), "bloom fixture initialized");
    crt.update(source.data(), palette);
    crt.render(target.data(), width, 100);
    const auto empty = target;
    require(green(target[height / 2 * width + width / 2]) >= 7, "unlit glass has dark green ambient light");
    for (int y = height / 2 - 24; y < height / 2 + 24; y++) {
        source[y * width + width / 2] = 255;
        source[y * width + width / 2 + 1] = 255;
    }
    crt.update(source.data(), palette);
    crt.render(target.data(), width, 133);
    const int core = green(target[height / 2 * width + width / 2]);
    require(core > 170, "phosphor strokes retain a sharp bright core");
    require(green(target[height / 2 * width + width / 2 + 6]) > green(empty[height / 2 * width + width / 2 + 6]) + 5,
        "real soft bloom extends beyond the bilinear glyph footprint");
    require(green(target[height / 2 * width + width / 2 + 14]) > green(empty[height / 2 * width + width / 2 + 14]) + 1,
        "wide bloom retains faint energy far from a stroke");
    const auto unchangedClock = target;
    crt.render(target.data(), width, 133);
    require(target == unchangedClock, "steady source and identical clock yield deterministic RGB output");
    crt.render(target.data(), width, 166);
    require(target != unchangedClock, "idle frames still advance subtle noise/flicker");
    std::fill(source.begin(), source.end(), 0);
    crt.update(source.data(), palette);
    crt.render(target.data(), width, 199);
    const int afterimage = green(target[height / 2 * width + width / 2]);
    require(afterimage > 35 && afterimage < core - 50, "cleared text leaves a subdued phosphor afterimage");
    crt.render(target.data(), width, 1200);
    require(green(target[height / 2 * width + width / 2]) < 20, "afterimage decays without permanent ghosting");
}

void benchmark()
{
    constexpr int width = 1084;
    constexpr int height = 500;
    TerminalCrt crt;
    require(crt.initialize(width, height), "benchmark initialized");
    std::vector<unsigned char> source(width * height, 0);
    std::vector<uint32_t> target(width * height);
    for (int y = 32; y < height - 32; y += 16) {
        for (int x = 24; x < width - 24; x += 8)
            fallout::nixboy::drawTerminalGlyph(source.data(), width, x, y, 'M', 230);
    }
    crt.update(source.data(), identityPalette());
    crt.render(target.data(), width, 0);
    const auto start = std::chrono::steady_clock::now();
    for (uint32_t frame = 1; frame <= 60; frame++)
        crt.render(target.data(), width, frame * 33);
    const auto elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    std::cout << "RGB CRT1084x500 idle frame: " << elapsed / 60 << "ms (60frames)\n";
}

} // namespace

int main(int argc, char** argv)
{
    try {
        if (argc == 2 && std::string(argv[1]) == "--benchmark") {
            benchmark();
            return EXIT_SUCCESS;
        }
        testLayouts();
        testFontBounds();
        testRgbBounds();
        testLensAndEdgeCells();
        testBloomAndTime();
        std::cout << "nix-boy terminal layout, font and RGB CRT: all tests passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << "nix-boy terminal view failure: " << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
