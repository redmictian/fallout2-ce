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
