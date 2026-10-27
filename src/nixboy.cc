#include "nixboy.h"

#ifdef FALLOUT_NIXBOY
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <limits>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "color.h"
#include "config.h"
#include "critter.h"
#include "dbox.h"
#include "debug.h"
#include "game.h"
#include "kb.h"
#include "map.h"
#include "nixboy_client.h"
#include "nixboy_hotkey.h"
#include "nixboy_terminal.h"
#include "nixboy_wiki.h"
#include "obj_types.h"
#include "object.h"
#include "scripts.h"
#include "skill.h"
#endif

namespace fallout {

#ifdef FALLOUT_NIXBOY
namespace {

    struct Options {
        bool enabled = false;
        bool training = true;
        NixboyTerminalOptions terminal;
        uint16_t port = 47821;
        std::string tokenFile = "nixboy.token";
        nixboy::Hotkey hotkey { nixboy::HotkeyKind::ControlLetter, 'N' };
        std::unordered_map<std::string, int> bindings;
    };

    struct WikiOptions {
        bool enabled = false;
        bool fullscreen = false;
        nixboy::Hotkey hotkey { nixboy::HotkeyKind::ControlLetter, 'W' };
        std::string home = "nixboy/wiki/index.html";
    };

    bool parseInteger(const char* text, int minimum, int maximum, int& result)
    {
        if (text == nullptr || *text == '\0') return false;
        char* end = nullptr;
        errno = 0;
        long value = std::strtol(text, &end, 10);
        if (errno != 0 || end == text || *end != '\0' || value < minimum || value > maximum) return false;
        result = static_cast<int>(value);
        return true;
    }

    bool validBindingName(const std::string& name)
    {
        // Leave seven bytes for the outgoing "global." context namespace.
        if (name.empty() || name.size() > 57) return false;
        auto alpha = [](unsigned char ch) { return (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || ch == '_'; };
        if (!alpha(name[0])) return false;
        for (unsigned char ch : name) {
            if (!alpha(ch) && !(ch >= '0' && ch <= '9') && ch != '.' && ch != '-') return false;
        }
        return true;
    }

    bool readOptions(Options& options, std::string& error)
    {
        Config config;
        if (!configInit(&config)) {
            error = "Cannot allocate nix-boy configuration.";
            return false;
        }
        struct Cleanup {
            Config* config;
            ~Cleanup() { configFree(config); }
        } cleanup { &config };

        // Missing configuration means disabled; nothing is written to disk.
        if (!configRead(&config, "nixboy.ini", false)) return true;
        configGetBool(&config, "nixboy", "enabled", &options.enabled);
        if (!options.enabled) return true;

        char* value = nullptr;
        if (configGetString(&config, "nixboy", "hotkey", &value)
            && !nixboy::parseHotkey(value, options.hotkey)) {
            error = "nixboy.ini: hotkey must be Ctrl+A..Z (except H/I/M), Alt+A..Z, A..Z, F1..F12 or none.";
            return false;
        }
        if (configGetString(&config, "nixboy", "training", &value)) {
            if (std::string(value) == "1" || std::string(value) == "true")
                options.training = true;
            else if (std::string(value) == "0" || std::string(value) == "false")
                options.training = false;
            else {
                error = "nixboy.ini: training must be 1/true or 0/false.";
                return false;
            }
        }
        for (const auto& flag : { std::make_pair("fullscreen", &options.terminal.fullscreen),
                 std::make_pair("retro_terminal", &options.terminal.retro) }) {
            if (!configGetString(&config, "nixboy", flag.first, &value)) continue;
            if (std::string(value) == "1" || std::string(value) == "true") {
                *flag.second = true;
            } else if (std::string(value) == "0" || std::string(value) == "false") {
                *flag.second = false;
            } else {
                error = std::string("nixboy.ini: ") + flag.first + " must be 1/true or 0/false.";
                return false;
            }
        }
        struct EffectSetting {
            const char* name;
            int* value;
            int maximum;
        };
        const EffectSetting effects[] = {
            { "crt_warp", &options.terminal.crt.warp, nixboy::kTerminalCrtStrengthMax },
            { "crt_glow", &options.terminal.crt.glow, nixboy::kTerminalCrtStrengthMax },
            { "crt_bloom", &options.terminal.crt.bloom, nixboy::kTerminalCrtStrengthMax },
            { "crt_scanlines", &options.terminal.crt.scanlines, nixboy::kTerminalCrtStrengthMax },
            { "crt_noise", &options.terminal.crt.noise, nixboy::kTerminalCrtStrengthMax },
            { "crt_jitter", &options.terminal.crt.jitter, nixboy::kTerminalCrtStrengthMax },
            { "crt_rgb_shift", &options.terminal.crt.rgbShift, nixboy::kTerminalCrtStrengthMax },
            { "crt_persistence_ms", &options.terminal.crt.persistenceMs, nixboy::kTerminalCrtPersistenceMaxMs },
        };
        for (const auto& effect : effects) {
            if (!configGetString(&config, "nixboy", effect.name, &value)) continue;
            if (!parseInteger(value, 0, effect.maximum, *effect.value)) {
                error = std::string("nixboy.ini: ") + effect.name
                    + " must be an integer from 0 to " + std::to_string(effect.maximum) + ".";
                return false;
            }
        }
        if (configGetString(&config, "nixboy", "port", &value)) {
            int port;
            if (!parseInteger(value, 1, 65535, port)) {
                error = "nixboy.ini: port must be in 1..65535.";
                return false;
            }
            options.port = static_cast<uint16_t>(port);
        }
        if (configGetString(&config, "nixboy", "token_file", &value)) {
            if (*value == '\0' || std::string(value).size() > 1024) {
                error = "nixboy.ini: invalid token_file.";
                return false;
            }
            options.tokenFile = value;
        }

        int sectionIndex = dictionaryGetIndexByKey(&config, "bindings");
        if (sectionIndex != -1) {
            auto* section = static_cast<ConfigSection*>(config.entries[sectionIndex].value);
            if (section->entriesLength > 120) {
                error = "nixboy.ini: at most 120 result bindings are supported.";
                return false;
            }
            for (int index = 0; index < section->entriesLength; ++index) {
                auto& entry = section->entries[index];
                int global;
                if (!validBindingName(entry.key) || !parseInteger(*static_cast<char**>(entry.value), 0, std::numeric_limits<int>::max(), global)) {
                    error = "nixboy.ini: a binding requires a valid name and nonnegative global index.";
                    return false;
                }
                options.bindings.emplace(entry.key, global);
            }
        }
        return true;
    }

    bool readWikiOptions(WikiOptions& options, std::string& error)
    {
        Config config;
        if (!configInit(&config)) {
            error = "Cannot allocate wiki configuration.";
            return false;
        }
        struct Cleanup {
            Config* config;
            ~Cleanup() { configFree(config); }
        } cleanup { &config };
        if (!configRead(&config, "nixboy.ini", false)) return true;
        configGetBool(&config, "nixboy", "enabled", &options.enabled);
        if (!options.enabled) return true;
        char* value = nullptr;
        if (configGetString(&config, "wiki", "enabled", &value)) {
            if (std::string(value) == "1" || std::string(value) == "true") {
                options.enabled = true;
            } else if (std::string(value) == "0" || std::string(value) == "false") {
                options.enabled = false;
            } else {
                error = "nixboy.ini: [wiki] enabled must be 1/true or 0/false.";
                return false;
            }
        }
        if (!options.enabled) return true;
        if (configGetString(&config, "wiki", "fullscreen", &value)) {
            if (std::string(value) == "1" || std::string(value) == "true") {
                options.fullscreen = true;
            } else if (std::string(value) == "0" || std::string(value) == "false") {
                options.fullscreen = false;
            } else {
                error = "nixboy.ini: [wiki] fullscreen must be 1/true or 0/false.";
                return false;
            }
        }
        if (configGetString(&config, "wiki", "hotkey", &value)
            && !nixboy::parseHotkey(value, options.hotkey)) {
            error = "nixboy.ini: [wiki] hotkey must be Ctrl+A..Z (except H/I/M), Alt+A..Z, A..Z, F1..F12 or none.";
            return false;
        }
        if (configGetString(&config, "wiki", "home", &value)) {
            if (*value == '\0' || std::string(value).size() > 1024) {
                error = "nixboy.ini: [wiki] home must name a local generated index.html.";
                return false;
            }
            options.home = value;
        }
        return true;
    }

    bool matchesHotkey(const nixboy::Hotkey& hotkey, int eventCode)
    {
        static constexpr int altKeys[] = {
            KEY_ALT_A, KEY_ALT_B, KEY_ALT_C, KEY_ALT_D, KEY_ALT_E, KEY_ALT_F,
            KEY_ALT_G, KEY_ALT_H, KEY_ALT_I, KEY_ALT_J, KEY_ALT_K, KEY_ALT_L,
            KEY_ALT_M, KEY_ALT_N, KEY_ALT_O, KEY_ALT_P, KEY_ALT_Q, KEY_ALT_R,
            KEY_ALT_S, KEY_ALT_T, KEY_ALT_U, KEY_ALT_V, KEY_ALT_W, KEY_ALT_X,
            KEY_ALT_Y, KEY_ALT_Z
        };
        switch (hotkey.kind) {
        case nixboy::HotkeyKind::Letter:
            return eventCode == hotkey.key || eventCode == hotkey.key - 'A' + 'a';
        case nixboy::HotkeyKind::ControlLetter:
            static_assert(KEY_CTRL_A == 1 && KEY_CTRL_N == 14 && KEY_CTRL_Z == 26,
                "Control-letter logical keys must match ASCII control codes");
            return eventCode == hotkey.key - 'A' + 1;
        case nixboy::HotkeyKind::AltLetter:
            return eventCode == altKeys[hotkey.key - 'A'];
        case nixboy::HotkeyKind::Function:
            return eventCode == (hotkey.key <= 10 ? KEY_F1 + hotkey.key - 1 : KEY_F11 + hotkey.key - 11);
        case nixboy::HotkeyKind::None:
            return false;
        }
        return false;
    }

    void showError(const std::string& error)
    {
        debugPrint("Nix-Boy: %s\n", error.c_str());
        showDialogBox(error.c_str(), nullptr, 0, 192, 135, _colorTable[992], nullptr, _colorTable[992], DIALOG_BOX_LARGE);
    }

    void showWikiError(const std::string& error)
    {
        debugPrint("Nix-Boy wiki: %s\n", error.c_str());
        const char* body[] = { error.c_str() };
        showDialogBox("nix-boy wiki", body, 1, 192, 135, _colorTable[992], nullptr, _colorTable[992], DIALOG_BOX_LARGE);
    }

    nixboy::Context collectContext(const Options& options)
    {
        const auto ticks = gameTimeGetTime();
        nixboy::Context context {
            { "game.time_ticks", static_cast<int32_t>(ticks > INT32_MAX ? INT32_MAX : ticks) },
            { "game.map", mapGetCurrentMap() },
            { "player.tile", gDude->tile },
            { "player.elevation", gDude->elevation },
            { "player.hp", critterGetHitPoints(gDude) },
        };
        if (options.training) {
            static constexpr const char* skillNames[] = {
                "small_guns", "big_guns", "energy_weapons", "unarmed", "melee_weapons", "throwing",
                "first_aid", "doctor", "sneak", "lockpick", "steal", "traps", "science", "repair",
                "speech", "barter", "gambling", "outdoorsman"
            };
            static_assert(sizeof(skillNames) / sizeof(skillNames[0]) == SKILL_COUNT, "Update training skill identifiers");
            for (int skill = 0; skill < SKILL_COUNT; skill++) {
                context.emplace_back(std::string("skill.") + skillNames[skill], skillGetValue(gDude, skill));
            }
            context.emplace_back("training.version", 4);
            for (size_t index = 0; index < 4; index++) {
                context.emplace_back("training.profile." + std::to_string(index), nixboy::trainingState().profileWord(index));
            }
        } else {
            for (const auto& binding : options.bindings)
                context.emplace_back("global." + binding.first, gameGetGlobalVar(binding.second));
        }
        return context;
    }

} // namespace
#endif

bool nixboyIsEnabled()
{
#ifdef FALLOUT_NIXBOY
    Options options;
    std::string error;
    readOptions(options, error);
    // Keep the button reachable so malformed enabled configuration has feedback.
    return options.enabled;
#else
    return false;
#endif
}

bool nixboyIsHotkey(int eventCode)
{
#ifdef FALLOUT_NIXBOY
    // Negative values are mouse/idle events. Do not read configuration on each
    // frame: capture the shortcut once, before dispatching the first key event.
    if (eventCode <= 0) return false;
    static const nixboy::Hotkey hotkey = []() {
        Options options;
        std::string error;
        if (!readOptions(options, error)) {
            debugPrint("nix-boy shortcut disabled: %s\n", error.c_str());
            return nixboy::Hotkey {};
        }
        return options.enabled ? options.hotkey : nixboy::Hotkey {};
    }();
    return matchesHotkey(hotkey, eventCode);
#else
    return false;
#endif
}

bool nixboyIsWikiEnabled()
{
#ifdef FALLOUT_NIXBOY
    WikiOptions options;
    std::string error;
    readWikiOptions(options, error);
    return options.enabled;
#else
    return false;
#endif
}

bool nixboyIsWikiHotkey(int eventCode)
{
#ifdef FALLOUT_NIXBOY
    if (eventCode <= 0) return false;
    static const nixboy::Hotkey hotkey = []() {
        WikiOptions options;
        std::string error;
        if (!readWikiOptions(options, error)) {
            debugPrint("nix-boy wiki shortcut disabled: %s\n", error.c_str());
            return nixboy::Hotkey {};
        }
        return options.enabled ? options.hotkey : nixboy::Hotkey {};
    }();
    return matchesHotkey(hotkey, eventCode);
#else
    return false;
#endif
}

void nixboyOpenWiki()
{
#ifdef FALLOUT_NIXBOY
    WikiOptions options;
    std::string error;
    if (!readWikiOptions(options, error)) {
        showWikiError(error);
        return;
    }
    if (!options.enabled || gDude == nullptr) return;
    if (!nixboyWikiWindowOpen(options.home, options.fullscreen, error)) {
        showWikiError(error);
    }
#endif
}

void nixboyOpen()
{
#ifdef FALLOUT_NIXBOY
    Options options;
    std::string error;
    if (!readOptions(options, error)) {
        showError(error);
        return;
    }
    if (!options.enabled || gDude == nullptr) return;

    for (const auto& binding : options.bindings) {
        if (!options.training && binding.second >= gGameGlobalVarsLength) {
            showError("nixboy.ini: a result binding is outside this game's global variable range.");
            return;
        }
    }

    std::ifstream stream(options.tokenFile, std::ios::binary);
    char bytes[68];
    stream.read(bytes, sizeof(bytes));
    std::string token(bytes, static_cast<size_t>(stream.gcount()));
    while (!token.empty() && (token.back() == '\r' || token.back() == '\n'))
        token.pop_back();
    bool valid = token.size() == 64;
    for (unsigned char ch : token) {
        valid = valid && ((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f') || (ch >= 'A' && ch <= 'F'));
    }
    if (!valid || stream.gcount() == static_cast<std::streamsize>(sizeof(bytes))) {
        showError("Cannot read a 64-digit authentication token. Start the nix-boy bridge first.");
        return;
    }

    if (options.training && !nixboy::trainingState().ensureProfile()) {
        showError("Cannot create the training character identity.");
        return;
    }
    NixboyTrainingCallbacks training;
    training.enabled = options.training;
    training.reward = [](uint8_t skill, uint16_t level, uint16_t points, const nixboy::TrainingReceipt& receipt) {
        bool accepted = nixboy::trainingState().accept(skill, level, receipt, points);
        debugPrint("Nix-Boy training: skill %d level %d points %d %s.\n", skill, level, points, accepted ? "accepted" : "rejected");
        return accepted;
    };
    training.snapshot = [&options]() { return collectContext(options); };
    training.progress = []() { return nixboy::trainingState().claims(); };

    bool opened = nixboyTerminalOpen(options.port, token, collectContext(options), [&options](const std::string& name, int32_t value) {
        if (options.training) return;
        auto binding = options.bindings.find(name);
        if (binding == options.bindings.end()) {
            debugPrint("Nix-Boy: ignored unbound result '%s'.\n", name.c_str());
            return;
        }
        // The modal's game thread is the only writer. Never accept an index or
        // pointer from the container; bounds are checked again by the game API.
        gameSetGlobalVar(binding->second, value); }, training, options.terminal);
    if (!opened) {
        showError("Cannot create the nix-boy terminal window. Check available memory and game fonts.");
    }
#endif
}

} // namespace fallout
