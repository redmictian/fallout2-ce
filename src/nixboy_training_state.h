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
    };

    // Save-owned learning progress. These are engine skill IDs, not menu indexes.
    // A completed level contributes five percentage points after tag modifiers.
    class TrainingState {
    public:
        static constexpr size_t kMaxClaims = 2048;
        static constexpr size_t kLegacyTrailerSize = 102;
        static constexpr size_t kMinTrailerSize = 38;
        static constexpr size_t kMaxTrailerSize = kMinTrailerSize + 19 * kMaxClaims;
        void reset();
        bool ensureProfile();
        int32_t profileWord(size_t index) const;
        int completed(int skill) const;
        const std::vector<TrainingClaim>& claims() const { return claims_; }
        int bonus(int skill) const;
        TrainingReceipt receipt(int skill, int level) const;
        // Levels may be claimed independently: the bridge selects the current
        // skill band. Exact repeats never stack; conflicting or reused receipts fail.
        bool accept(int skill, int level, const TrainingReceipt& receipt);
        std::vector<uint8_t> encode() const;
        // Zero bytes mean an old save without a trailer. Any present trailer
        // must be complete, versioned, checksummed and internally consistent.
        bool decode(const uint8_t* bytes, size_t size);

    private:
        TrainingReceipt profile_ {};
        std::vector<TrainingClaim> claims_;
    };

    TrainingState& trainingState();

} // namespace nixboy
} // namespace fallout

#endif // FALLOUT_NIXBOY_TRAINING_STATE_H_
