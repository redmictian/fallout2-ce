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
using fallout::nixboy::TerminalCrtSettings;
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

TerminalCrtSettings noEffects()
{
    TerminalCrtSettings settings;
    settings.warp = 0;
    settings.glow = 0;
    settings.persistenceMs = 0;
    settings.scanlines = 0;
    settings.noise = 0;
    settings.jitter = 0;
    settings.bloom = 0;
    settings.rgbShift = 0;
    return settings;
}

TerminalCrtSettings settingsFor(int warp, int glow, int persistenceMs)
{
    auto settings = noEffects();
    settings.warp = warp;
    settings.glow = glow;
    settings.persistenceMs = persistenceMs;
    return settings;
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
            require(layout.textX >= 8 && layout.textY >= 8
                    && layout.textX + layout.cols * 8 <= layout.glassWidth - 8
                    && layout.textY + layout.rows * 16 <= layout.glassHeight - 8,
                "whole first/last cells fit within rounded aperture padding");
            require(layout.statusY == layout.glassY + layout.glassHeight
                    && layout.statusY <= layout.height - 18,
                "frame guard follows glass and leaves a native bezel without a status footer");
        }
    }
    for (const auto& cell : { std::array<int, 2> { 5, 9 }, { 8, 11 }, { 12, 18 }, { 16, 24 } }) {
        TerminalViewLayout dynamic;
        require(terminalViewLayout(1280, 720, cell[0], cell[1], false, dynamic, 11), "native runtime font metrics accepted");
        require(dynamic.textX >= 8 && dynamic.textY >= 8
                && dynamic.textX + dynamic.cols * cell[0] <= dynamic.glassWidth - 8
                && dynamic.textY + dynamic.rows * cell[1] <= dynamic.glassHeight - 8,
            "variable native font cells fit the entire source grid");
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
    constexpr int cols = 38;
    constexpr int rows = 14;
    constexpr int textX = 8;
    constexpr int textY = 8;
    const auto palette = identityPalette();
    std::vector<unsigned char> source(width * height, 0);
    std::vector<uint32_t> target(width * height, 0);
    TerminalCrt crt;
    require(crt.initialize(width, height, settingsFor(100, 0, 0)), "edge fixture initialized");
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
    require(green(target[width / 2]) > 100 && green(target[height / 2 * width]) > 100,
        "curvature does not bow the glass away from the frame or add a black annulus");
    require(green(target[6 * width + 6]) > 100, "glass fills the native housing except tiny rounded corners");
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
    auto settings = settingsFor(0, 0, 600);
    settings.bloom = 100;
    settings.noise = 3;
    require(crt.initialize(width, height, settings), "bloom fixture initialized");
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

void testSettingsBounds()
{
    TerminalCrt crt;
    require(crt.initialize(32, 32, noEffects()), "all effects can be disabled independently");
    for (auto member : { &TerminalCrtSettings::warp, &TerminalCrtSettings::glow,
             &TerminalCrtSettings::scanlines, &TerminalCrtSettings::noise,
             &TerminalCrtSettings::jitter, &TerminalCrtSettings::bloom,
             &TerminalCrtSettings::rgbShift, &TerminalCrtSettings::persistenceMs }) {
        const int maximum = member == &TerminalCrtSettings::persistenceMs ? 2000 : 100;
        auto settings = noEffects();
        settings.*member = maximum;
        require(crt.initialize(32, 32, settings), "maximum effect value accepted");
        for (int value : { -1, maximum + 1 }) {
            settings.*member = value;
            require(!crt.initialize(32, 32, settings), "invalid effect values rejected");
            std::vector<uint32_t> target(32 * 32, 0xdeadbeef);
            crt.render(target.data(), 32, 0);
            require(std::all_of(target.begin(), target.end(), [](uint32_t pixel) { return pixel == 0xdeadbeef; }),
                "invalid settings cannot reuse an earlier initialized renderer");
        }
    }
}

void testWarpControl()
{
    constexpr int width = 320;
    constexpr int height = 240;
    const auto palette = identityPalette();
    std::vector<unsigned char> source(width * height, 0);
    std::vector<uint32_t> target(width * height, 0);
    double previousDistance = 0;
    for (int warp : { 0, 25, 50, 75, 100 }) {
        TerminalCrt crt;
        require(crt.initialize(width, height, settingsFor(warp, 0, 0)), "warp control initialized");
        std::fill(source.begin(), source.end(), 0);
        crt.update(source.data(), palette);
        crt.render(target.data(), width, 100);
        const auto empty = target;
        for (int y = 48; y < 64; y++)
            std::fill_n(source.begin() + y * width + 48, 16, 255);
        crt.update(source.data(), palette);
        crt.render(target.data(), width, 100);
        double weightedX = 0;
        double weightedY = 0;
        int total = 0;
        for (int y = 0; y < height; y++) {
            for (int x = 0; x < width; x++) {
                const int light = green(target[y * width + x]) - green(empty[y * width + x]);
                require(light >= 0, "lighting a cell cannot darken another pixel");
                if (warp == 0) {
                    require((light > 0) == (x >= 48 && x < 64 && y >= 48 && y < 64),
                        "zero warp samples source coordinates exactly without spill or lost pixels");
                }
                total += light;
                weightedX += light * x;
                weightedY += light * y;
            }
        }
        require(total > 0, "warped patch remains visible");
        const double dx = weightedX / total - width / 2.0;
        const double dy = weightedY / total - height / 2.0;
        const double distance = dx * dx + dy * dy;
        if (warp != 0) require(distance < previousDistance, "increasing warp moves peripheral text progressively inward");
        previousDistance = distance;

        for (const auto& corner : { std::array<int, 2> { 24, 24 }, { width - 32, 24 },
                 { 24, height - 40 }, { width - 32, height - 40 } }) {
            std::fill(source.begin(), source.end(), 0);
            for (int y = corner[1]; y < corner[1] + 16; y++)
                std::fill_n(source.begin() + y * width + corner[0], 8, 255);
            crt.update(source.data(), palette);
            crt.render(target.data(), width, 100);
            require(std::count_if(target.begin(), target.end(), [](uint32_t pixel) { return green(pixel) > 130; }) >= 32,
                "all four corner cells remain readable at every warp strength");
        }
        std::fill(source.begin(), source.end(), 255);
        crt.update(source.data(), palette);
        crt.render(target.data(), width, 100);
        require(target.front() == 0 && target.back() == 0, "rounded glass remains when warp is disabled or reduced");
    }
}

void testGlowControl()
{
    constexpr int width = 320;
    constexpr int height = 240;
    const auto palette = identityPalette();
    std::vector<unsigned char> source(width * height, 0);
    std::vector<uint32_t> target(width * height, 0);
    long long previousHalo = -1;
    for (int glow : { 0, 20, 50, 100 }) {
        TerminalCrt crt;
        require(crt.initialize(width, height, settingsFor(0, glow, 0)), "glow control initialized");
        std::fill(source.begin(), source.end(), 0);
        crt.update(source.data(), palette);
        crt.render(target.data(), width, 100);
        const auto empty = target;
        for (int y = 80; y < 160; y++)
            std::fill_n(source.begin() + y * width + 156, 8, 255);
        crt.update(source.data(), palette);
        crt.render(target.data(), width, 100);
        long long halo = 0;
        for (int y = 0; y < height; y++) {
            for (int x = 0; x < width; x++) {
                if (x >= 156 && x < 164 && y >= 80 && y < 160) continue;
                halo += green(target[y * width + x]) - green(empty[y * width + x]);
            }
        }
        if (glow == 0) require(halo == 0, "zero glow creates no text halo outside source strokes");
        require(halo > previousHalo, "increasing glow monotonically increases surrounding light");
        require(green(target[120 * width + 160]) > 170, "lowering glow preserves the sharp text core");
        previousHalo = halo;
    }
}

void testPersistenceControl()
{
    constexpr int width = 80;
    constexpr int height = 80;
    const auto palette = identityPalette();
    std::vector<unsigned char> source(width * height, 0);
    std::vector<uint32_t> target(width * height, 0);
    for (int warp : { 0, 100 }) {
        for (int glow : { 0, 20, 100 }) {
            const TerminalCrtSettings settings = settingsFor(warp, glow, 0);
            TerminalCrt crt;
            require(crt.initialize(width, height, settings), "zero-persistence combination initialized");
            std::fill(source.begin(), source.end(), 255);
            crt.update(source.data(), palette);
            crt.render(target.data(), width, 100);
            std::fill(source.begin(), source.end(), 0);
            crt.update(source.data(), palette);
            crt.render(target.data(), width, 100);
            TerminalCrt fresh;
            require(fresh.initialize(width, height, settings), "fresh dark renderer initialized");
            fresh.update(source.data(), palette);
            std::vector<uint32_t> empty(target.size());
            fresh.render(empty.data(), width, 100);
            require(target == empty, "zero persistence removes old pixels and bloom immediately even at identical ticks");
        }
    }
    int previousTrail = -1;
    for (int persistence : { 0, 120, 600, 2000 }) {
        TerminalCrt crt;
        require(crt.initialize(width, height, settingsFor(0, 0, persistence)), "persistence control initialized");
        std::fill(source.begin(), source.end(), 255);
        crt.update(source.data(), palette);
        crt.render(target.data(), width, 100);
        std::fill(source.begin(), source.end(), 0);
        crt.update(source.data(), palette);
        crt.render(target.data(), width, 100);
        if (persistence != 0) {
            const auto sameTick = target;
            crt.render(target.data(), width, 100);
            require(target == sameTick, "unchanged clock does not age phosphor history");
        }
        crt.render(target.data(), width, 220);
        const int trail = green(target[40 * width + 40]);
        require(trail > previousTrail, "longer persistence retains progressively stronger old text");
        previousTrail = trail;
        crt.render(target.data(), width, 20220);
        require(green(target[40 * width + 40]) < 20, "every supported persistence setting eventually clears old light");
    }
    for (int persistence : { 120, 2000 }) {
        const TerminalCrtSettings settings = settingsFor(0, 0, persistence);
        TerminalCrt crt;
        TerminalCrt dark;
        require(crt.initialize(width, height, settings) && dark.initialize(width, height, settings), "decay-time fixtures initialized");
        std::fill(source.begin(), source.end(), 255);
        crt.update(source.data(), palette);
        crt.render(target.data(), width, 100);
        std::fill(source.begin(), source.end(), 0);
        crt.update(source.data(), palette);
        crt.render(target.data(), width, 100);
        dark.update(source.data(), palette);
        std::vector<uint32_t> empty(target.size());
        dark.render(empty.data(), width, 100);
        const int initialTrail = green(target[40 * width + 40]) - green(empty[40 * width + 40]);
        crt.render(target.data(), width, 100 + persistence);
        dark.render(empty.data(), width, 100 + persistence);
        const int oldLight = green(target[40 * width + 40]) - green(empty[40 * width + 40]);
        require(oldLight >= initialTrail / 20 && oldLight <= initialTrail / 6,
            "configured decay time leaves approximately one tenth of the initial afterimage, including intervals above one second");
    }
}

void testIndependentEffects()
{
    constexpr int width = 128;
    constexpr int height = 96;
    const auto palette = identityPalette();
    std::vector<unsigned char> source(width * height, 0);
    for (int y = 24; y < 72; y++)
        std::fill_n(source.begin() + y * width + 60, 3, 255);
    const auto frame = [&](const TerminalCrtSettings& settings, uint32_t ticks) {
        TerminalCrt crt;
        require(crt.initialize(width, height, settings), "isolated effect initialized");
        crt.update(source.data(), palette);
        std::vector<uint32_t> target(source.size());
        crt.render(target.data(), width, ticks);
        return target;
    };
    const auto disabled = noEffects();
    const auto original = frame(disabled, 100);
    require(original == frame(disabled, 4300), "all effects at zero produce a steady image without hidden noise or flicker");
    for (auto member : { &TerminalCrtSettings::warp, &TerminalCrtSettings::glow,
             &TerminalCrtSettings::scanlines, &TerminalCrtSettings::noise,
             &TerminalCrtSettings::jitter, &TerminalCrtSettings::bloom,
             &TerminalCrtSettings::rgbShift }) {
        auto settings = disabled;
        settings.*member = 100;
        require(frame(settings, 100) != original, "each enabled effect changes the image independently");
    }

    auto scanlines = disabled;
    scanlines.scanlines = 100;
    const auto raster = frame(scanlines, 100);
    require(raster[48 * width + 61] == original[48 * width + 61]
            && green(raster[49 * width + 61]) < green(original[49 * width + 61]) / 2,
        "scanlines darken alternate phosphor rows without shifting ink");

    auto glow = disabled;
    glow.glow = 100;
    const auto local = frame(glow, 100);
    require(green(local[48 * width + 59]) > green(original[48 * width + 59]) + 15,
        "local glow lights the immediate stroke neighbour");
    require(local[48 * width + 56] == original[48 * width + 56], "local phosphor glow does not silently add broad bloom");
    auto bloom = disabled;
    bloom.bloom = 100;
    const auto broad = frame(bloom, 100);
    require(green(broad[48 * width + 48]) > green(original[48 * width + 48]),
        "bloom independently lights pixels well beyond the phosphor halo");

    auto rgb = disabled;
    rgb.rgbShift = 100;
    const auto separated = frame(rgb, 100);
    require(((separated[48 * width + 58] >> 16) & 255) > 80
            && green(separated[48 * width + 58]) == green(original[48 * width + 58]),
        "RGB shift creates a red leading fringe without moving the green glyph");
    require((separated[48 * width + 64] & 255) > (original[48 * width + 64] & 255) + 50,
        "RGB shift creates the opposite blue fringe");

    for (auto member : { &TerminalCrtSettings::noise, &TerminalCrtSettings::jitter }) {
        auto settings = disabled;
        settings.*member = 100;
        const auto first = frame(settings, 100);
        require(first == frame(settings, 100), "timed effects are deterministic at the same clock");
        require(first != frame(settings, 199), "noise and jitter animate without new terminal output");
        settings.*member = member == &TerminalCrtSettings::noise ? 3 : 2;
        require(frame(settings, 100) != original, "restrained default noise and jitter remain real effects");
    }
    auto minimumNoise = disabled;
    minimumNoise.noise = 1;
    require(frame(minimumNoise, 100) != original, "minimum nonzero static noise is not rounded away");

    TerminalCrt animated;
    auto all = disabled;
    all.warp = all.glow = all.scanlines = all.noise = all.jitter = all.bloom = all.rgbShift = 100;
    all.persistenceMs = 2000;
    require(animated.initialize(width, height, all), "all effects coexist at their maximums");
    animated.update(source.data(), palette);
    std::vector<uint32_t> target(source.size());
    animated.render(target.data(), width, 0xfffffff0u);
    animated.render(target.data(), width, 20u);
    require(std::any_of(target.begin(), target.end(), [](uint32_t pixel) { return green(pixel) > 100; }),
        "maximum effects and wrapped clock retain visible text");
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
            for (int gy = 0; gy < 10; gy++) {
                source[(y + gy) * width + x] = 230;
                source[(y + gy) * width + x + 5] = 230;
                if (gy < 4) source[(y + gy) * width + x + 2] = 230;
            }
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
        testRgbBounds();
        testLensAndEdgeCells();
        testBloomAndTime();
        testSettingsBounds();
        testWarpControl();
        testGlowControl();
        testPersistenceControl();
        testIndependentEffects();
        std::cout << "nix-boy terminal layout and CPU CRT effects: all tests passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << "nix-boy terminal view failure: " << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
