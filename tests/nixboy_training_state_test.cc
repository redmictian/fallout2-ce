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
    require(bytes.size() == TrainingState::kMinTrailerSize + 19 * 19 && bytes[11] == 3, "variable v3 trailer length");
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
        require(migrated[11] == 3 && migrated.size() == 95, "legacy next save uses variable ledger");
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

void testCapacityAndCorruption()
{
    TrainingState state;
    require(state.ensureProfile(), "capacity identity");
    for (int level = 1; level <= int(TrainingState::kMaxClaims); level++)
        require(state.accept(fallout::SKILL_REPAIR, level, receipt(level)), "bounded ledger capacity");
    auto full = state.encode();
    require(full.size() == TrainingState::kMaxTrailerSize && state.decode(full.data(), full.size()), "maximum ledger round trip");
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
    for (int mode = 0; mode < 8; mode++) {
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
            std::copy(damaged.begin() + 34, damaged.begin() + 53, damaged.begin() + 53);
            break;
        case 5:
            std::copy(damaged.begin() + 37, damaged.begin() + 53, damaged.begin() + 56);
            break;
        case 6:
            damaged[11] = 4;
            break;
        case 7:
            std::fill(damaged.begin() + 37, damaged.begin() + 53, 0);
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
} // namespace

int main()
{
    try {
        testGenericProgression();
        testLegacyMigration();
        testCapacityAndCorruption();
        std::cout << "nix-boy training state: all tests passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << "nix-boy training state test failure: " << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
