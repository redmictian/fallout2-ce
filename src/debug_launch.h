#ifndef DEBUG_LAUNCH_H
#define DEBUG_LAUNCH_H

namespace fallout {

struct DebugLaunchOptions {
    bool skipMovies = false;
    const char* newGameCharacter = nullptr;
    int newGameLevel = 1;
    bool reviewAllPerks = false;
    bool reviewPerkPicker = false;
    int reviewPerk = -1;
    int reviewRank = 1;

    bool reviewPerks() const { return reviewAllPerks || reviewPerkPicker || reviewPerk >= 0; }
};

// Explicit test-run switches; unrelated engine/config arguments are preserved.
// A failed parse leaves options unchanged.
bool debugLaunchParseOptions(int argc, const char* const* argv, DebugLaunchOptions& options);

} // namespace fallout

#endif /* DEBUG_LAUNCH_H */
