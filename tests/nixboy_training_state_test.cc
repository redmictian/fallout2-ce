#include "nixboy_training_state.h"
#include "skill_defs.h"

#include <cstdlib>
#include <iostream>
#include <stdexcept>

using fallout::nixboy::TrainingReceipt;
using fallout::nixboy::TrainingState;

namespace {
void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

TrainingReceipt receipt(uint8_t value)
{
    TrainingReceipt bytes {};
    bytes.back() = value;
    return bytes;
}

void testProgression()
{
    TrainingState state;
    require(!state.accept(fallout::SKILL_UNARMED, 1, receipt(1)), "reward requires character identity");
    require(state.ensureProfile(), "profile generation");
    auto initial = state.encode();
    require(state.ensureProfile() && state.encode() == initial, "identity stable across reopen");
    require(!state.accept(fallout::SKILL_UNARMED, 1, {}), "zero receipt rejected");
    require(!state.accept(fallout::SKILL_UNARMED, 2, receipt(2)), "cannot skip a level");
    require(!state.accept(fallout::SKILL_REPAIR, 1, receipt(1)), "unsupported skill rejected");
    require(!state.accept(fallout::SKILL_SCIENCE, 0, receipt(1)), "zero level rejected");
    require(!state.accept(fallout::SKILL_SCIENCE, 3, receipt(1)), "unsupported level rejected");
    require(state.accept(fallout::SKILL_UNARMED, 1, receipt(1)), "unarmed level one");
    require(state.completed(fallout::SKILL_UNARMED) == 1 && state.bonus(fallout::SKILL_UNARMED) == 5, "exact five-point additive bonus");
    require(state.completed(fallout::SKILL_SCIENCE) == 0, "other track remains introductory");
    require(state.receipt(fallout::SKILL_UNARMED, 1) == receipt(1), "completed receipt available for bridge recovery");
    require(state.receipt(fallout::SKILL_UNARMED, 2) == TrainingReceipt {}
            && state.receipt(fallout::SKILL_SCIENCE, 1) == TrainingReceipt {}
            && state.receipt(-1, 1) == TrainingReceipt {}
            && state.receipt(fallout::SKILL_UNARMED, 0) == TrainingReceipt {},
        "unearned or invalid receipts unavailable");
    auto earned = state.encode();
    require(state.accept(fallout::SKILL_UNARMED, 1, receipt(1)) && state.encode() == earned, "exact duplicate accepted without stacking");
    require(!state.accept(fallout::SKILL_UNARMED, 1, receipt(2)) && state.encode() == earned, "changed receipt rejected");
    require(!state.accept(fallout::SKILL_SCIENCE, 1, receipt(1)), "receipt cannot reward a second track");
    require(!state.accept(fallout::SKILL_UNARMED, 2, receipt(1)), "receipt cannot reward a second level");
    require(state.accept(fallout::SKILL_SCIENCE, 1, receipt(3)), "independent science level one");
    require(state.accept(fallout::SKILL_UNARMED, 2, receipt(2)), "unarmed level two");
    require(state.bonus(fallout::SKILL_UNARMED) == 10 && state.bonus(fallout::SKILL_SCIENCE) == 5, "independent additive rewards");
    require(state.bonus(-1) == 0 && state.bonus(18) == 0 && state.completed(fallout::SKILL_REPAIR) == 0, "other skills unaffected");

    TrainingState restored;
    auto bytes = state.encode();
    require(bytes.size() == TrainingState::kTrailerSize, "fixed version-one trailer length");
    require(restored.decode(bytes.data(), bytes.size()), "save round trip");
    require(restored.encode() == bytes, "profile levels and receipts round trip");
    require(restored.accept(fallout::SKILL_UNARMED, 1, receipt(1)), "older receipt remains idempotent after later level");
    require(restored.accept(fallout::SKILL_UNARMED, 2, receipt(2)) && restored.bonus(fallout::SKILL_UNARMED) == 10, "duplicate stays idempotent after reload");

    // A restored earlier timeline can accept a previously validated receipt
    // once, without adding again on retries within that loaded state.
    require(restored.decode(earned.data(), earned.size()), "restore older timeline");
    require(restored.accept(fallout::SKILL_UNARMED, 2, receipt(2)), "bridge can redeliver into older timeline");
    require(restored.accept(fallout::SKILL_UNARMED, 2, receipt(2)) && restored.bonus(fallout::SKILL_UNARMED) == 10, "replayed receipt never stacks");
    restored.reset();
    require(restored.bonus(fallout::SKILL_UNARMED) == 0 && restored.profileWord(0) == 0, "new-game reset clears identity and bonuses");
}

void testLegacyAndCorruption()
{
    TrainingState state;
    require(state.ensureProfile() && state.accept(fallout::SKILL_SCIENCE, 1, receipt(9)), "setup earned state");
    require(state.decode(nullptr, 0), "legacy EOF has no optional trailer");
    require(state.bonus(fallout::SKILL_SCIENCE) == 0 && state.profileWord(3) == 0, "legacy load clears previous character training");
    auto empty = state.encode();
    require(state.decode(empty.data(), empty.size()), "uninitialized profile in a new mod save is valid");
    require(state.ensureProfile() && state.accept(fallout::SKILL_UNARMED, 1, receipt(1)), "setup corruption fixture");
    auto bytes = state.encode();
    for (size_t size = 1; size < bytes.size(); size++) {
        require(!state.decode(bytes.data(), size), "any truncated present trailer must fail");
        require(state.bonus(fallout::SKILL_UNARMED) == 0, "failed decode clears state");
    }
    for (size_t index = 0; index < bytes.size(); index++) {
        auto damaged = bytes;
        damaged[index] ^= 0x80;
        require(!state.decode(damaged.data(), damaged.size()), "corrupt header payload or checksum rejected");
    }
    bytes.push_back(0);
    require(!state.decode(bytes.data(), bytes.size()), "unexpected trailing bytes rejected");
    require(!state.decode(nullptr, TrainingState::kTrailerSize), "null nonempty buffer rejected");
}
} // namespace

int main()
{
    try {
        testProgression();
        testLegacyAndCorruption();
        std::cout << "Nix-Boy training state: all tests passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << "Nix-Boy training state test failure: " << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
