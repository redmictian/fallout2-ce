#include "debug_launch.h"

#include <string.h>

namespace fallout {

bool debugLaunchParseOptions(int argc, const char* const* argv, DebugLaunchOptions& options)
{
    if (argc < 1 || argv == nullptr) {
        return false;
    }

    DebugLaunchOptions parsed;
    bool skipMoviesSpecified = false;
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
        } else if (strncmp(argument, "--debug-new-game", 16) == 0
            || strncmp(argument, "--debug-no-movies", 17) == 0) {
            return false;
        }
    }

    options = parsed;
    return true;
}

} // namespace fallout
