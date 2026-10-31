#ifndef FALLOUT_NIXBOY_PERK_REWARD_H_
#define FALLOUT_NIXBOY_PERK_REWARD_H_

#include <utility>

#include "nixboy_training_state.h"
#include "object.h"
#include "perk.h"

namespace fallout {
namespace nixboy {

// Called only on the game thread. Allocate/validate the candidate ledger before
// changing the production perk and publish it only after a successful grant.
// Exact duplicates remain accepted acknowledgments, never another effect.
inline PerkClaimResult applyPerkTrainingReward(TrainingState& state, const TrainingReceipt& profile,
    uint16_t perk, uint8_t rank, const TrainingReceipt& receipt)
{
#ifdef FALLOUT_NIXBOY_PERK_TRAINING
    auto candidate = state;
    auto result = candidate.acceptPerk(profile, perk, rank, receipt,
        perkGetRank(gDude, perk), perkGetMaximumRank(gDude, perk));
    if (result == PerkClaimResult::Rejected) return result;
    if (result == PerkClaimResult::New) {
        if (perkAddForce(gDude, perk) != 0) return PerkClaimResult::Rejected;
        state = std::move(candidate);
    }
    return result;
#else
    return PerkClaimResult::Rejected;
#endif
}

} // namespace nixboy
} // namespace fallout
#endif
