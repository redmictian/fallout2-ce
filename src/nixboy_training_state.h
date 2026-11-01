#ifndef FALLOUT_NIXBOY_TRAINING_STATE_H_
#define FALLOUT_NIXBOY_TRAINING_STATE_H_

#include <array>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace fallout {
namespace nixboy {

    using TrainingReceipt = std::array<uint8_t, 16>;

    struct TrainingClaim {
        uint8_t skill;
        uint16_t level;
        TrainingReceipt receipt;
        uint16_t points = 5;
    };

    struct PerkTrainingClaim {
        uint16_t perk;
        uint8_t rank;
        TrainingReceipt receipt;
    };

    enum class PerkClaimResult { Rejected, New, Duplicate };

    // Game-owned reward bonuses and duplicate receipts. These are engine skill
    // IDs, not menu indexes. The opaque profile scopes live assessments, not
    // durable external achievements; a game load renews it lazily on next open.
    class TrainingState {
    public:
        static constexpr size_t kMaxClaims = 2048;
        static constexpr size_t kLegacyTrailerSize = 102;
        static constexpr size_t kMinTrailerSize = 38;
        static constexpr size_t kMaxPerkClaims = 64;
        static constexpr size_t kMaxSkillTrailerSize = kMinTrailerSize + 21 * kMaxClaims;
        // Read bound includes the obsolete v6 reference. New writes remain v4/v5
        // and never require an external file or service.
        static constexpr size_t kMaxTrailerSize = kMaxSkillTrailerSize + 2 + 19 * kMaxPerkClaims + 32;
        void reset();
        bool ensureProfile();
        int32_t profileWord(size_t index) const;
        const TrainingReceipt& profile() const { return profile_; }
        int completed(int skill) const;
        const std::vector<TrainingClaim>& claims() const { return claims_; }
        const std::vector<PerkTrainingClaim>& perkClaims() const { return perkClaims_; }
        int bonus(int skill) const;
        TrainingReceipt receipt(int skill, int level) const;
        // Levels may be claimed independently: the bridge selects the current
        // skill band. Exact repeats never stack; conflicting or reused receipts fail.
        bool accept(int skill, int level, const TrainingReceipt& receipt, int points = 5);
        // Typed passive-perk pilot. A duplicate is acknowledged without applying
        // another rank; missing ownership, conflicting profile/receipt/rank fail.
        PerkClaimResult acceptPerk(const TrainingReceipt& profile, int perk, int targetRank,
            const TrainingReceipt& receipt, int currentRank, int maximumRank);
        std::vector<uint8_t> encode() const;
        // Zero bytes mean an old save without a trailer. Any present trailer
        // must be complete, versioned, checksummed and internally consistent.
        bool decode(const uint8_t* bytes, size_t size);

    private:
        TrainingReceipt profile_ {};
        bool renewProfileOnOpen_ = false;
        std::vector<TrainingClaim> claims_;
        std::vector<PerkTrainingClaim> perkClaims_;
    };

    TrainingState& trainingState();

} // namespace nixboy
} // namespace fallout

#endif // FALLOUT_NIXBOY_TRAINING_STATE_H_
