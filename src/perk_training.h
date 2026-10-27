#ifndef FALLOUT_PERK_TRAINING_H_
#define FALLOUT_PERK_TRAINING_H_

#include "perk_defs.h"

namespace fallout {

// Explicit vanilla level-up set. Disabled, equipment, withdrawal and world
// reward IDs retain their original semantics. Independent of terminal support.
constexpr bool perkIsLevelUpChoice(int perk)
{
    return (perk >= PERK_AWARENESS && perk <= PERK_MUTATE
               && perk != PERK_MENTAL_BLOCK && perk != PERK_FRIENDLY_FOE
               && perk != PERK_SCROUNGER && perk != PERK_FLOWER_CHILD
               && perk != PERK_ANIMAL_FRIEND)
        || (perk >= PERK_ADRENALINE_RUSH && perk <= PERK_WEAPON_HANDLING);
}

constexpr bool perkUsesTraining(int perk)
{
#if defined(FALLOUT_NIXBOY_PERK_TRAINING)
    return perkIsLevelUpChoice(perk);
#else
    return false;
#endif
}

constexpr int perkBenefitRank(int perk, int rawRank)
{
    return perkUsesTraining(perk) && rawRank > 0 ? rawRank - 1 : rawRank;
}

constexpr int perkTrainingMaxRank(int perk, int originalMaxRank)
{
    return originalMaxRank + (perkUsesTraining(perk) ? 1 : 0);
}

constexpr int perkTrainingMinLevel(int perk, int rawRank, int originalMinLevel)
{
    return perkUsesTraining(perk) && rawRank > 0 ? 310 : originalMinLevel;
}

// Separate save version prevents mixing stored deltas with old rank semantics.
constexpr unsigned char perkTrainingSaveRelease()
{
#if defined(FALLOUT_NIXBOY_PERK_TRAINING)
    return 'T';
#else
    return 'R';
#endif
}

// Mutable text: the legacy card renderer temporarily inserts NULs for wrapping.
char* perkTrainingDescription(int perk);

} // namespace fallout

#endif
