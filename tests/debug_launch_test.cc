#include "debug_launch.h"

#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

bool parse(std::initializer_list<const char*> arguments, fallout::DebugLaunchOptions& options)
{
    std::vector<const char*> argv { "fallout2-ce" };
    argv.insert(argv.end(), arguments.begin(), arguments.end());
    return fallout::debugLaunchParseOptions(static_cast<int>(argv.size()), argv.data(), options);
}

} // namespace

int main()
{
    try {
        fallout::DebugLaunchOptions options;
        require(parse({}, options), "ordinary launch rejected");
        require(!options.skipMovies && options.newGameCharacter == nullptr, "ordinary launch changed");
        require(parse({ "[system]language=english", "unrelated-option" }, options), "legacy config arguments rejected");
        require(!options.skipMovies && options.newGameCharacter == nullptr, "legacy arguments enabled debug mode");

        require(parse({ "--debug-no-movies" }, options), "movie-only launch rejected");
        require(options.skipMovies && options.newGameCharacter == nullptr, "movie-only launch started a new game");

        for (const char* name : { "narg", "chitsa", "mingun" }) {
            const std::string argument = std::string("--debug-new-game=") + name;
            require(parse({ argument.c_str() }, options), "valid premade rejected");
            require(options.skipMovies && std::strcmp(options.newGameCharacter, name) == 0, "premade did not imply movie skip");
        }
        require(parse({ "--debug-no-movies", "--debug-new-game=narg" }, options), "explicit combined switches rejected");
        require(parse({ "--debug-new-game=narg", "--debug-no-movies" }, options), "switch order mattered");
        require(parse({ "--debug-new-game=narg", "--debug-new-game-level=3" }, options) && options.newGameLevel == 3, "review level rejected");
        require(parse({ "--debug-new-game-level=99", "--debug-new-game=narg" }, options) && options.newGameLevel == 99, "maximum review level rejected");
        for (const char* level : { "", "0", "100", "-1", "+3", "3x", "999999999999999999" }) {
            std::string argument = std::string("--debug-new-game-level=") + level;
            require(!parse({ "--debug-new-game=narg", argument.c_str() }, options), "invalid review level accepted");
        }
        require(!parse({ "--debug-new-game-level=3" }, options), "review level without a new character accepted");
        require(!parse({ "--debug-new-game=narg", "--debug-new-game-level=3", "--debug-new-game-level=9" }, options), "duplicate review level accepted");
        require(parse({ "--debug-perks=all" }, options) && options.reviewAllPerks && options.skipMovies
                && std::strcmp(options.newGameCharacter, "narg") == 0,
            "one-switch review must skip menus and create a disposable character");
        require(parse({ "--debug-perk=12:4" }, options) && options.reviewPerk == 12 && options.reviewRank == 4 && options.skipMovies,
            "specific perk review rejected");
        require(parse({ "--debug-perk-picker" }, options) && options.reviewPerkPicker && options.newGameLevel == 3 && options.skipMovies,
            "one-switch picker must create a level-three character");
        require(parse({ "--debug-perks=all", "--debug-new-game=chitsa" }, options) && std::strcmp(options.newGameCharacter, "chitsa") == 0,
            "explicit review character ignored");
        for (const char* argument : { "--debug-perks=", "--debug-perks=other", "--debug-perk=", "--debug-perk=12", "--debug-perk=:2", "--debug-perk=12:0", "--debug-perk=12:5", "--debug-perk=119:1", "--debug-perk=27:1", "--debug-perk=53:1", "--debug-perk=-1:1", "--debug-perk=12:2:1" }) {
            require(!parse({ argument }, options), "malformed, special or disabled perk review accepted");
        }
        require(!parse({ "--debug-perks=all", "--debug-perk=12:2" }, options), "conflicting review modes accepted");
        require(!parse({ "--debug-perks=all", "--debug-perk-picker" }, options), "gallery/picker conflict accepted");
        require(!parse({ "--debug-perks=all", "--debug-perks=all" }, options), "duplicate review mode accepted");

        for (const char* argument : { "--debug-new-game", "--debug-new-game=", "--debug-new-game=Narg", "--debug-new-game=../player", "--debug-new-game=narg-extra", "--debug-no-movies=0" }) {
            options.skipMovies = false;
            options.newGameCharacter = "sentinel";
            require(!parse({ argument }, options), "invalid debug switch accepted");
            require(!options.skipMovies && std::strcmp(options.newGameCharacter, "sentinel") == 0, "failed parse changed options");
        }
        require(!parse({ "--debug-new-game=narg", "--debug-new-game=chitsa" }, options), "conflicting premades accepted");
        require(!parse({ "--debug-no-movies", "--debug-no-movies" }, options), "duplicate movie switch accepted");
        require(!parse({ nullptr }, options), "null argument accepted");
        require(!fallout::debugLaunchParseOptions(0, nullptr, options), "missing argv accepted");

        std::cout << "Debug launch options passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
