#ifndef FALLOUT_DEBUG_PERKS_H_
#define FALLOUT_DEBUG_PERKS_H_

#include "debug_launch.h"

namespace fallout {

// Fresh-character review only. Grant through production paths and log before /
// after stats, skills, tags and traits. Does not save or load a game slot.
bool debugGrantReviewPerks(const DebugLaunchOptions& options);

} // namespace fallout

#endif
