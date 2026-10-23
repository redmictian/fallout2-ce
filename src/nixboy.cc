#include "nixboy.h"

#ifdef FALLOUT_NIXBOY
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <limits>
#include <string>
#include <unordered_map>
#include <vector>

#include "color.h"
#include "config.h"
#include "critter.h"
#include "dbox.h"
#include "debug.h"
#include "game.h"
#include "map.h"
#include "nixboy_client.h"
#include "nixboy_terminal.h"
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
        uint16_t port = 47821;
        std::string tokenFile = "nixboy.token";
        std::unordered_map<std::string, int> bindings;
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
            error = "Cannot allocate Nix-Boy configuration.";
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

    void showError(const std::string& error)
    {
        debugPrint("Nix-Boy: %s\n", error.c_str());
        showDialogBox(error.c_str(), nullptr, 0, 192, 135, _colorTable[992], nullptr, _colorTable[992], DIALOG_BOX_LARGE);
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
            static_assert(SKILL_UNARMED == 3 && SKILL_SCIENCE == 12, "Training track wire IDs must remain stable");
            for (int skill = 0; skill < SKILL_COUNT; skill++) {
                context.emplace_back(std::string("skill.") + skillNames[skill], skillGetValue(gDude, skill));
            }
            context.emplace_back("training.version", 1);
            for (size_t index = 0; index < 4; index++) {
                context.emplace_back("training.profile." + std::to_string(index), nixboy::trainingState().profileWord(index));
            }
            context.emplace_back("training.completed.unarmed", nixboy::trainingState().completed(SKILL_UNARMED));
            context.emplace_back("training.completed.science", nixboy::trainingState().completed(SKILL_SCIENCE));
            for (int skill : { SKILL_UNARMED, SKILL_SCIENCE }) {
                for (int level = 1; level <= nixboy::trainingState().completed(skill); level++) {
                    auto receipt = nixboy::trainingState().receipt(skill, level);
                    for (int index = 0; index < 4; index++) {
                        const uint8_t* bytes = receipt.data() + index * 4;
                        uint32_t bits = (uint32_t(bytes[0]) << 24) | (uint32_t(bytes[1]) << 16) | (uint32_t(bytes[2]) << 8) | bytes[3];
                        int32_t word = bits <= uint32_t(INT32_MAX) ? static_cast<int32_t>(bits) : -1 - static_cast<int32_t>(~bits);
                        context.emplace_back(std::string("training.receipt.") + skillNames[skill] + "." + std::to_string(level) + "." + std::to_string(index), word);
                    }
                }
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
        showError("Cannot read a 64-digit authentication token. Start the Nix-Boy bridge first.");
        return;
    }

    if (options.training && !nixboy::trainingState().ensureProfile()) {
        showError("Cannot create the training character identity.");
        return;
    }
    NixboyTrainingCallbacks training;
    training.enabled = options.training;
    training.reward = [](uint8_t skill, uint8_t level, const nixboy::TrainingReceipt& receipt) {
        bool accepted = nixboy::trainingState().accept(skill, level, receipt);
        debugPrint("Nix-Boy training: skill %d level %d %s.\n", skill, level, accepted ? "accepted" : "rejected");
        return accepted;
    };
    training.snapshot = [&options]() { return collectContext(options); };

    bool opened = nixboyTerminalOpen(options.port, token, collectContext(options), [&options](const std::string& name, int32_t value) {
        if (options.training) return;
        auto binding = options.bindings.find(name);
        if (binding == options.bindings.end()) {
            debugPrint("Nix-Boy: ignored unbound result '%s'.\n", name.c_str());
            return;
        }
        // The modal's game thread is the only writer. Never accept an index or
        // pointer from the container; bounds are checked again by the game API.
        gameSetGlobalVar(binding->second, value); }, training);
    if (!opened) {
        showError("Cannot create the Nix-Boy terminal window. Check available memory and game fonts.");
    }
#endif
}

} // namespace fallout
