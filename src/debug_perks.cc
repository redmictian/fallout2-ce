#include "debug_perks.h"

#include <array>

#include "debug.h"
#include "object.h"
#include "perk.h"
#include "perk_training.h"
#include "skill.h"
#include "stat.h"
#include "trait.h"

namespace fallout {
namespace {

struct Snapshot {
    std::array<int, STAT_COUNT> stats {};
    std::array<int, SAVEABLE_STAT_COUNT> bonus {};
    std::array<int, SKILL_COUNT> skills {};
    std::array<int, PC_STAT_COUNT> pc {};
    std::array<int, NUM_TAGGED_SKILLS> tags {};
    std::array<int, 2> traits {};

    bool operator==(const Snapshot& other) const
    {
        return stats == other.stats && bonus == other.bonus && skills == other.skills
            && pc == other.pc && tags == other.tags && traits == other.traits;
    }
};

Snapshot snapshot()
{
    Snapshot result;
    for (int i = 0; i < STAT_COUNT; ++i) result.stats[i] = critterGetStat(gDude, i);
    for (int i = 0; i < SAVEABLE_STAT_COUNT; ++i) result.bonus[i] = critterGetBonusStat(gDude, i);
    for (int i = 0; i < SKILL_COUNT; ++i) result.skills[i] = skillGetValue(gDude, i);
    for (int i = 0; i < PC_STAT_COUNT; ++i) result.pc[i] = pcGetStat(i);
    skillsGetTagged(result.tags.data(), NUM_TAGGED_SKILLS);
    traitsGetSelected(&result.traits[0], &result.traits[1]);
    return result;
}

void logChanges(const Snapshot& before, const Snapshot& after)
{
    for (int i = 0; i < STAT_COUNT; ++i) {
        if (before.stats[i] != after.stats[i]) debugPrint("\n  stat[%d] %d -> %d", i, before.stats[i], after.stats[i]);
    }
    for (int i = 0; i < SAVEABLE_STAT_COUNT; ++i) {
        if (before.bonus[i] != after.bonus[i]) debugPrint("\n  bonus[%d] %d -> %d", i, before.bonus[i], after.bonus[i]);
    }
    for (int i = 0; i < SKILL_COUNT; ++i) {
        if (before.skills[i] != after.skills[i]) debugPrint("\n  skill[%d] %d -> %d", i, before.skills[i], after.skills[i]);
    }
    for (int i = 0; i < PC_STAT_COUNT; ++i) {
        if (before.pc[i] != after.pc[i]) debugPrint("\n  pc[%d] %d -> %d", i, before.pc[i], after.pc[i]);
    }
}

} // namespace

bool debugGrantReviewPerks(const DebugLaunchOptions& options)
{
    for (int perk = 0; perk < PERK_COUNT; ++perk) {
        if (!(options.reviewAllPerks ? perkIsLevelUpChoice(perk) : perk == options.reviewPerk)) continue;
        int target = options.reviewAllPerks ? 1 : options.reviewRank;
        if (target > perkGetMaximumRank(gDude, perk)) {
            debugPrint("\nPERK REVIEW ERROR: perk %d maximum rank is %d; requested %d\n", perk, perkGetMaximumRank(gDude, perk), target);
            return false;
        }
        for (int rank = 1; rank <= target; ++rank) {
            auto before = snapshot();
            if (perkAddForce(gDude, perk) != 0) return false;
            auto after = snapshot();
            debugPrint("\nPERK REVIEW: id=%d name=%s raw=%d benefit=%d selectable=%d maximum=%d snapshot=%s\n",
                perk, perkGetName(perk), perkGetRank(gDude, perk), perkGetBenefitRank(gDude, perk),
                perkCanSelect(gDude, perk), perkGetMaximumRank(gDude, perk), before == after ? "unchanged" : "changed");
            logChanges(before, after);
            if (perkUsesTraining(perk) && rank == 1 && !(before == after)) {
                debugPrint("\nPERK REVIEW ERROR: rank-one snapshot changed\n");
                return false;
            }
        }
    }
    debugPrint("\nPERK REVIEW: ready; no save slot written\n");
    return true;
}

} // namespace fallout
