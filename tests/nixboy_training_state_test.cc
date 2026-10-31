#include "nixboy_training_state.h"
#include "skill_defs.h"

#include <algorithm>
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

TrainingReceipt receipt(uint16_t value)
{
    TrainingReceipt bytes {};
    bytes[14] = static_cast<uint8_t>(value >> 8);
    bytes[15] = static_cast<uint8_t>(value);
    return bytes;
}

void seal(std::vector<uint8_t>& bytes)
{
    uint32_t crc = 0xffffffff;
    for (size_t index = 0; index < bytes.size() - 4; index++) {
        crc ^= bytes[index];
        for (int bit = 0; bit < 8; bit++)
            crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
    crc = ~crc;
    for (size_t index = 0; index < 4; index++)
        bytes[bytes.size() - 4 + index] = static_cast<uint8_t>(crc >> (24 - index * 8));
}

std::vector<uint8_t> legacy(uint8_t version, uint8_t unarmed, uint8_t science)
{
    std::vector<uint8_t> bytes(TrainingState::kLegacyTrailerSize);
    const char magic[] = "NXBTRAIN";
    std::copy(magic, magic + 8, bytes.begin());
    bytes[11] = version;
    bytes[15] = 82;
    bytes[16] = 0xaa;
    bytes[32] = unarmed;
    bytes[33] = science;
    for (int track = 0; track < 2; track++) {
        int mask = bytes[32 + track];
        if (version == 1) mask = (1 << mask) - 1;
        for (int level = 0; level < 2; level++) {
            if (mask & (1 << level)) bytes[34 + (track * 2 + level) * 16 + 15] = static_cast<uint8_t>(track * 2 + level + 1);
        }
    }
    seal(bytes);
    return bytes;
}

void testGenericProgression()
{
    TrainingState state;
    require(!state.accept(fallout::SKILL_REPAIR, 3, receipt(1)), "reward requires identity");
    require(state.ensureProfile(), "generate profile");
    auto initial = state.encode();
    require(state.ensureProfile() && state.encode() == initial, "identity stable");
    require(!state.accept(-1, 1, receipt(1)) && !state.accept(18, 1, receipt(1)), "skill ID bounds");
    require(!state.accept(0, 0, receipt(1)) && !state.accept(0, 65536, receipt(1)), "level ID bounds");
    require(!state.accept(0, 1, {}), "empty receipt rejected");
    for (int skill = 0; skill < fallout::SKILL_COUNT; skill++) {
        require(state.accept(skill, 3, receipt(skill + 1)), "every engine skill supports declarative level three");
        require(state.completed(skill) == 1 && state.bonus(skill) == 5, "independent per-skill bonus");
    }
    require(state.receipt(fallout::SKILL_REPAIR, 3) == receipt(14), "Repair ID thirteen is generic");
    require(state.receipt(fallout::SKILL_REPAIR, 1) == TrainingReceipt {}, "lower level not implicitly earned");
    auto earned = state.encode();
    require(state.accept(fallout::SKILL_REPAIR, 3, receipt(14)) && state.encode() == earned, "exact retry idempotent");
    require(!state.accept(fallout::SKILL_REPAIR, 3, receipt(30)), "conflicting receipt rejected");
    require(!state.accept(fallout::SKILL_REPAIR, 4, receipt(1)), "receipt globally unique");
    require(state.accept(fallout::SKILL_REPAIR, UINT16_MAX, receipt(30)), "sparse maximum level supported");
    require(state.bonus(fallout::SKILL_REPAIR) == 10 && state.bonus(-1) == 0, "bonus counts claims rather than level number");
    auto bytes = state.encode();
    require(bytes.size() == TrainingState::kMinTrailerSize + 21 * 19 && bytes[11] == 4, "variable v4 trailer length");
    TrainingState restored;
    require(restored.decode(bytes.data(), bytes.size()) && restored.encode() == bytes, "ledger round trip");
    require(restored.accept(fallout::SKILL_REPAIR, UINT16_MAX, receipt(30)) && restored.bonus(fallout::SKILL_REPAIR) == 10,
        "sparse receipt retry after reload");
    require(restored.decode(earned.data(), earned.size()) && restored.accept(fallout::SKILL_REPAIR, UINT16_MAX, receipt(30)),
        "earlier timeline can receive trusted replay once");
    restored.reset();
    require(restored.claims().empty() && restored.profileWord(0) == 0, "new game clears progress and identity");
}

void testLegacyMigration()
{
    TrainingState state;
    for (uint8_t version : { 1, 2 }) {
        auto bytes = legacy(version, version == 1 ? 2 : 3, 1);
        require(state.decode(bytes.data(), bytes.size()), "v1 and v2 saves migrate");
        require(state.completed(fallout::SKILL_UNARMED) == 2 && state.bonus(fallout::SKILL_UNARMED) == 10
                && state.completed(fallout::SKILL_SCIENCE) == 1 && state.bonus(fallout::SKILL_SCIENCE) == 5,
            "migration preserves earned bonus");
        require(state.receipt(fallout::SKILL_UNARMED, 2) == receipt(2) && state.receipt(fallout::SKILL_SCIENCE, 1) == receipt(3),
            "migration preserves receipts");
        auto migrated = state.encode();
        require(migrated[11] == 4 && migrated.size() == 101, "legacy next save records fixed five-point historical awards");
        for (const auto& claim : state.claims())
            require(claim.points == 5, "v1/v2 historical points migrate as five");
        require(state.accept(fallout::SKILL_UNARMED, 2, receipt(2)) && state.bonus(fallout::SKILL_UNARMED) == 10,
            "legacy retry remains idempotent");
    }
    auto sparse = legacy(2, 2, 0);
    require(state.decode(sparse.data(), sparse.size()) && state.completed(fallout::SKILL_UNARMED) == 1
            && state.receipt(fallout::SKILL_UNARMED, 1) == TrainingReceipt {}
            && state.receipt(fallout::SKILL_UNARMED, 2) == receipt(2),
        "v2 sparse claim remains sparse");
    for (uint8_t version : { 1, 2 }) {
        auto bytes = legacy(version, 0, 0);
        require(state.decode(bytes.data(), bytes.size()) && state.claims().empty(), "empty legacy training save");
        bytes[32] = version == 1 ? 3 : 4;
        seal(bytes);
        require(!state.decode(bytes.data(), bytes.size()), "invalid legacy count or mask rejected");
    }
    require(state.decode(nullptr, 0) && state.claims().empty() && state.profileWord(0) == 0, "pre-mod save resets previous progress");
}

void testRecordedPoints()
{
    TrainingState state;
    require(state.ensureProfile(), "configurable award identity");
    require(!state.accept(13, 3, receipt(1), 0) && !state.accept(13, 3, receipt(1), 301)
            && !state.accept(13, 3, receipt(1), -1),
        "award amount bounds");
    require(state.accept(13, 3, receipt(1), 7) && state.accept(13, 4, receipt(2), 11)
            && state.accept(12, 300, receipt(3), 300),
        "mixed declarative award amounts");
    require(state.completed(13) == 2 && state.bonus(13) == 18 && state.bonus(12) == 300,
        "skill bonus sums recorded amounts");
    auto bytes = state.encode();
    require(state.accept(13, 3, receipt(1), 7) && state.encode() == bytes, "same receipt and amount retry idempotent");
    require(!state.accept(13, 3, receipt(1), 8) && !state.accept(13, 3, receipt(1)), "changed amount cannot rewrite historical award");
    require(state.decode(bytes.data(), bytes.size()) && state.bonus(13) == 18
            && state.claims()[0].points == 7 && state.claims()[1].points == 11,
        "custom amounts persist across reload");

    // Version three had a 19-byte record and no per-claim amount.
    std::vector<uint8_t> old(38 + 2 * 19);
    std::copy(bytes.begin(), bytes.begin() + 34, old.begin());
    old[11] = 3;
    old[15] = 18 + 2 * 19;
    old[33] = 2;
    old[34] = 13;
    old[36] = 3;
    old[52] = 1;
    old[53] = 12;
    old[54] = 1;
    old[55] = 44;
    old[71] = 2;
    seal(old);
    require(state.decode(old.data(), old.size()) && state.bonus(13) == 5 && state.bonus(12) == 5,
        "v3 receipts migrate as historical five-point awards");
    require(state.claims()[0].points == 5 && state.claims()[1].points == 5
            && state.receipt(13, 3) == receipt(1) && state.receipt(12, 300) == receipt(2),
        "v3 migration retains claim identity");
    require(!state.accept(13, 3, receipt(1), 7) && state.accept(13, 3, receipt(1), 5), "migration never retroactively rebalances old awards");
}

void testCapacityAndCorruption()
{
    TrainingState state;
    require(state.ensureProfile(), "capacity identity");
    for (int level = 1; level <= int(TrainingState::kMaxClaims); level++)
        require(state.accept(fallout::SKILL_REPAIR, level, receipt(level)), "bounded ledger capacity");
    auto full = state.encode();
    require(full.size() == TrainingState::kMaxSkillTrailerSize && state.decode(full.data(), full.size()), "maximum skill ledger round trip");
    require(!state.accept(fallout::SKILL_REPAIR, 2049, receipt(2049)), "overflow claim rejected");
    require(state.accept(fallout::SKILL_REPAIR, 1, receipt(1)), "full ledger still accepts duplicate delivery");
    state.reset();
    auto empty = state.encode();
    require(state.decode(empty.data(), empty.size()), "uninitialized empty v3 save valid");
    require(state.ensureProfile() && state.accept(0, 3, receipt(1)) && state.accept(13, 400, receipt(2)), "corruption fixture");
    auto bytes = state.encode();
    for (size_t size = 1; size < bytes.size(); size++) {
        require(!state.decode(bytes.data(), size) && state.claims().empty(), "truncated trailer fails and clears state");
    }
    for (size_t index = 0; index < bytes.size(); index++) {
        auto damaged = bytes;
        damaged[index] ^= 0x80;
        require(!state.decode(damaged.data(), damaged.size()), "corrupt header payload or checksum rejected");
    }
    for (int mode = 0; mode < 10; mode++) {
        auto damaged = bytes;
        switch (mode) {
        case 0:
            damaged[33] = 3;
            break;
        case 1:
            damaged[34] = 18;
            break;
        case 2:
            damaged[35] = damaged[36] = 0;
            break;
        case 3:
            std::fill(damaged.begin() + 16, damaged.begin() + 32, 0);
            break;
        case 4:
            std::copy(damaged.begin() + 34, damaged.begin() + 55, damaged.begin() + 55);
            break;
        case 5:
            std::copy(damaged.begin() + 39, damaged.begin() + 55, damaged.begin() + 60);
            break;
        case 6:
            damaged[11] = 5;
            break;
        case 7:
            std::fill(damaged.begin() + 39, damaged.begin() + 55, 0);
            break;
        case 8:
            damaged[37] = damaged[38] = 0;
            break;
        case 9:
            damaged[37] = 1;
            damaged[38] = 45;
            break;
        }
        seal(damaged);
        require(!state.decode(damaged.data(), damaged.size()), "invalid semantic ledger rejected despite valid CRC");
    }
    bytes.push_back(0);
    require(!state.decode(bytes.data(), bytes.size()), "extra trailing bytes rejected");
    require(!state.decode(nullptr, 38), "null nonempty buffer rejected");
    std::vector<uint8_t> oversized(TrainingState::kMaxTrailerSize + 1);
    require(!state.decode(oversized.data(), oversized.size()), "oversized trailer bounded before allocation");
}

void testTypedPerkClaims()
{
    using fallout::nixboy::PerkClaimResult;
    TrainingState state;
    require(state.ensureProfile(), "perk profile");
    auto profile = state.profile();
    auto wrongProfile = profile;
    wrongProfile[0] ^= 1;
    require(state.acceptPerk(wrongProfile, 12, 2, receipt(90), 1, 4) == PerkClaimResult::Rejected, "perk profile binding");
    require(state.acceptPerk(profile, 12, 2, receipt(90), 0, 4) == PerkClaimResult::Rejected, "missing ownership");
    require(state.acceptPerk(profile, 12, 2, receipt(90), 4, 4) == PerkClaimResult::Rejected, "max rank no grant");
    require(state.acceptPerk(profile, 12, 2, receipt(90), 1, 1) == PerkClaimResult::Rejected, "maximum below target");
    require(state.acceptPerk(profile, 12, 3, receipt(90), 2, 4) == PerkClaimResult::Rejected, "pilot target allowlist");
    require(state.acceptPerk(profile, 13, 2, receipt(90), 1, 4) == PerkClaimResult::Rejected, "pilot perk allowlist");
    require(state.accept(3, 1, receipt(1)), "old skill receipt");
    require(state.acceptPerk(profile, 12, 2, receipt(1), 1, 4) == PerkClaimResult::Rejected, "skill receipt cannot fund perk");
    require(state.acceptPerk(profile, 12, 2, receipt(90), 1, 4) == PerkClaimResult::New, "new perk grant distinct from ACK");
    require(state.acceptPerk(profile, 12, 2, receipt(90), 2, 4) == PerkClaimResult::Duplicate, "exact duplicate acknowledgment");
    require(state.acceptPerk(profile, 12, 2, receipt(91), 2, 4) == PerkClaimResult::Rejected, "conflicting perk claim");
    require(state.acceptPerk(profile, 12, 2, receipt(90), 1, 4) == PerkClaimResult::Rejected, "claim/rank inconsistency");
    require(!state.accept(12, 60000, receipt(90)), "perk receipt cannot fund skill");
    auto bytes = state.encode();
    require(bytes[11] == 5 && bytes.size() == 80, "typed v5 trailer exact length");
    TrainingState loaded;
    require(loaded.decode(bytes.data(), bytes.size()) && loaded.perkClaims().size() == 1
        && loaded.claims().size() == 1 && loaded.bonus(3) == 5, "v5 load preserves independent skill and perk claims");
    require(loaded.encode() == bytes, "v5 deterministic saved ledger");
    auto corrupt = bytes;
    corrupt[56] = 0; // Perk count must not be zero in v5.
    seal(corrupt);
    require(!loaded.decode(corrupt.data(), corrupt.size()), "v5 bad count rejected despite CRC");
    corrupt = bytes;
    auto reused = receipt(1);
    std::copy(reused.begin(), reused.end(), corrupt.begin() + 60);
    seal(corrupt);
    require(!loaded.decode(corrupt.data(), corrupt.size()), "v5 cross-kind duplicate rejected despite CRC");
}
} // namespace

int main()
{
    try {
        testGenericProgression();
        testLegacyMigration();
        testRecordedPoints();
        testCapacityAndCorruption();
        testTypedPerkClaims();
        std::cout << "nix-boy training state: all tests passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << "nix-boy training state test failure: " << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
