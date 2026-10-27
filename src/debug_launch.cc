#include "debug_launch.h"

#include <string.h>

#include "perk_training.h"

namespace fallout {

static bool debugParseNumber(const char* text, int maximum, int& value)
{
    if (*text == '\0') return false;
    value = 0;
    while (*text != '\0') {
        if (*text < '0' || *text > '9' || value > maximum) return false;
        value = value * 10 + (*text++ - '0');
    }
    return value <= maximum;
}

bool debugLaunchParseOptions(int argc, const char* const* argv, DebugLaunchOptions& options)
{
    if (argc < 1 || argv == nullptr) {
        return false;
    }

    DebugLaunchOptions parsed;
    bool skipMoviesSpecified = false;
    bool levelSpecified = false;
    for (int index = 1; index < argc; index++) {
        const char* argument = argv[index];
        if (argument == nullptr) {
            return false;
        }

        if (strcmp(argument, "--debug-no-movies") == 0) {
            if (skipMoviesSpecified) {
                return false;
            }
            skipMoviesSpecified = true;
            parsed.skipMovies = true;
        } else if (strncmp(argument, "--debug-new-game=", 17) == 0) {
            if (parsed.newGameCharacter != nullptr) {
                return false;
            }

            const char* name = argument + 17;
            if (strcmp(name, "narg") == 0) {
                parsed.newGameCharacter = "narg";
            } else if (strcmp(name, "chitsa") == 0) {
                parsed.newGameCharacter = "chitsa";
            } else if (strcmp(name, "mingun") == 0) {
                parsed.newGameCharacter = "mingun";
            } else {
                return false;
            }
            parsed.skipMovies = true;
        } else if (strncmp(argument, "--debug-new-game-level=", 23) == 0) {
            if (levelSpecified) {
                return false;
            }
            levelSpecified = true;
            const char* number = argument + 23;
            int level = 0;
            if (*number == '\0') {
                return false;
            }
            while (*number != '\0') {
                if (*number < '0' || *number > '9' || level > 99) {
                    return false;
                }
                level = level * 10 + (*number++ - '0');
            }
            if (level < 1 || level > 99) {
                return false;
            }
            parsed.newGameLevel = level;
        } else if (strcmp(argument, "--debug-perks=all") == 0) {
            if (parsed.reviewPerks()) return false;
            parsed.reviewAllPerks = true;
        } else if (strcmp(argument, "--debug-perk-picker") == 0) {
            if (parsed.reviewPerks()) return false;
            parsed.reviewPerkPicker = true;
        } else if (strncmp(argument, "--debug-perk=", 13) == 0) {
            if (parsed.reviewPerks()) return false;
            const char* value = argument + 13;
            const char* colon = strchr(value, ':');
            if (colon == nullptr || colon - value < 1 || colon - value > 3) return false;
            char id[4] {};
            memcpy(id, value, colon - value);
            if (!debugParseNumber(id, PERK_COUNT - 1, parsed.reviewPerk)
                || !perkIsLevelUpChoice(parsed.reviewPerk)
                || !debugParseNumber(colon + 1, 4, parsed.reviewRank)
                || parsed.reviewRank < 1) return false;
        } else if (strncmp(argument, "--debug-new-game", 16) == 0
            || strncmp(argument, "--debug-perk", 12) == 0
            || strncmp(argument, "--debug-no-movies", 17) == 0) {
            return false;
        }
    }

    if (parsed.reviewPerks()) {
        if (parsed.newGameCharacter == nullptr) parsed.newGameCharacter = "narg";
        parsed.skipMovies = true;
        if (parsed.reviewPerkPicker && !levelSpecified) parsed.newGameLevel = 3;
    }
    if (levelSpecified && parsed.newGameCharacter == nullptr) {
        return false;
    }
    options = parsed;
    return true;
}

} // namespace fallout
