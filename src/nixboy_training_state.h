#ifndef FALLOUT_NIXBOY_TRAINING_STATE_H_
#define FALLOUT_NIXBOY_TRAINING_STATE_H_

#include <array>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace fallout {
namespace nixboy {

    using TrainingReceipt = std::array<uint8_t, 16>;

    // Save-owned learning progress. These are engine skill IDs, not menu indexes.
    // A completed level contributes five percentage points after tag modifiers.
    class TrainingState {
    public:
        static constexpr size_t kTrailerSize = 102;
        void reset();
        bool ensureProfile();
        int32_t profileWord(size_t index) const;
        int completed(int skill) const;
        int claimedMask(int skill) const;
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
        std::array<uint8_t, 2> claimed_ {};
        std::array<std::array<TrainingReceipt, 2>, 2> receipts_ {};
    };

    TrainingState& trainingState();

} // namespace nixboy
} // namespace fallout

#endif // FALLOUT_NIXBOY_TRAINING_STATE_H_
