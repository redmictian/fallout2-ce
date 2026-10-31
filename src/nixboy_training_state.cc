#include "nixboy_training_state.h"

#include <algorithm>
#include <cstring>
#include <random>

#include "skill_defs.h"
#include "perk_defs.h"

namespace fallout {
namespace nixboy {
    namespace {
        constexpr uint8_t kMagic[] = { 'N', 'X', 'B', 'T', 'R', 'A', 'I', 'N' };
        constexpr uint32_t kVersion = 4;
        static_assert(SKILL_COUNT == 18, "Training wire skill IDs require an explicit migration if changed");

        bool empty(const TrainingReceipt& receipt)
        {
            return std::all_of(receipt.begin(), receipt.end(), [](uint8_t byte) { return byte == 0; });
        }

        uint32_t read32(const uint8_t* bytes)
        {
            return (uint32_t(bytes[0]) << 24) | (uint32_t(bytes[1]) << 16)
                | (uint32_t(bytes[2]) << 8) | bytes[3];
        }

        void append32(std::vector<uint8_t>& bytes, uint32_t value)
        {
            for (int shift = 24; shift >= 0; shift -= 8)
                bytes.push_back(static_cast<uint8_t>(value >> shift));
        }

        uint32_t checksum(const uint8_t* bytes, size_t count)
        {
            uint32_t crc = 0xffffffff;
            for (size_t index = 0; index < count; index++) {
                crc ^= bytes[index];
                for (int bit = 0; bit < 8; bit++)
                    crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
            }
            return ~crc;
        }
    } // namespace

    void TrainingState::reset()
    {
        *this = TrainingState();
    }

    bool TrainingState::ensureProfile()
    {
        if (!empty(profile_)) return true;
        try {
            std::random_device random;
            for (auto& byte : profile_)
                byte = static_cast<uint8_t>(random());
        } catch (...) {
            profile_.fill(0);
            return false;
        }
        return !empty(profile_);
    }

    int32_t TrainingState::profileWord(size_t index) const
    {
        if (index >= 4) return 0;
        uint32_t value = read32(profile_.data() + index * 4);
        return value <= uint32_t(INT32_MAX) ? static_cast<int32_t>(value) : -1 - static_cast<int32_t>(~value);
    }

    int TrainingState::completed(int skill) const
    {
        return static_cast<int>(std::count_if(claims_.begin(), claims_.end(),
            [skill](const TrainingClaim& claim) { return claim.skill == skill; }));
    }

    int TrainingState::bonus(int skill) const
    {
        int total = 0;
        for (const auto& claim : claims_) {
            if (claim.skill == skill) total += claim.points;
        }
        return total;
    }

    TrainingReceipt TrainingState::receipt(int skill, int level) const
    {
        for (const auto& claim : claims_) {
            if (claim.skill == skill && claim.level == level) return claim.receipt;
        }
        return {};
    }

    bool TrainingState::accept(int skill, int level, const TrainingReceipt& receipt, int points)
    {
        if (skill < 0 || skill >= SKILL_COUNT || level < 1 || level > UINT16_MAX || points < 1 || points > 300 || empty(profile_) || empty(receipt)) return false;
        for (const auto& claim : claims_) {
            if (claim.skill == skill && claim.level == level) return claim.receipt == receipt && claim.points == points;
            if (claim.receipt == receipt) return false;
        }
        for (const auto& claim : perkClaims_) {
            if (claim.receipt == receipt) return false;
        }
        if (claims_.size() >= kMaxClaims) return false;
        claims_.push_back({ static_cast<uint8_t>(skill), static_cast<uint16_t>(level), receipt, static_cast<uint16_t>(points) });
        return true;
    }

    PerkClaimResult TrainingState::acceptPerk(const TrainingReceipt& profile, int perk, int targetRank,
        const TrainingReceipt& receipt, int currentRank, int maximumRank)
    {
        if (empty(profile_) || profile != profile_ || empty(receipt)
            || perk != PERK_TOUGHNESS || targetRank != 2 || maximumRank < targetRank
            || currentRank < 1 || currentRank > maximumRank) return PerkClaimResult::Rejected;
        for (const auto& claim : claims_) {
            if (claim.receipt == receipt) return PerkClaimResult::Rejected;
        }
        for (const auto& claim : perkClaims_) {
            if (claim.perk == perk && claim.rank == targetRank)
                return claim.receipt == receipt && currentRank >= targetRank
                    ? PerkClaimResult::Duplicate : PerkClaimResult::Rejected;
            if (claim.receipt == receipt) return PerkClaimResult::Rejected;
        }
        if (currentRank != targetRank - 1 || perkClaims_.size() >= kMaxPerkClaims)
            return PerkClaimResult::Rejected;
        perkClaims_.push_back({ static_cast<uint16_t>(perk), static_cast<uint8_t>(targetRank), receipt });
        return PerkClaimResult::New;
    }

    std::vector<uint8_t> TrainingState::encode() const
    {
        std::vector<uint8_t> bytes(std::begin(kMagic), std::end(kMagic));
        bool hasPerks = !perkClaims_.empty();
        append32(bytes, hasPerks ? 5 : kVersion);
        append32(bytes, static_cast<uint32_t>(18 + claims_.size() * 21 + (hasPerks ? 2 + perkClaims_.size() * 19 : 0)));
        bytes.insert(bytes.end(), profile_.begin(), profile_.end());
        bytes.push_back(static_cast<uint8_t>(claims_.size() >> 8));
        bytes.push_back(static_cast<uint8_t>(claims_.size()));
        for (const auto& claim : claims_) {
            bytes.push_back(claim.skill);
            bytes.push_back(static_cast<uint8_t>(claim.level >> 8));
            bytes.push_back(static_cast<uint8_t>(claim.level));
            bytes.push_back(static_cast<uint8_t>(claim.points >> 8));
            bytes.push_back(static_cast<uint8_t>(claim.points));
            bytes.insert(bytes.end(), claim.receipt.begin(), claim.receipt.end());
        }
        if (hasPerks) {
            bytes.push_back(static_cast<uint8_t>(perkClaims_.size() >> 8));
            bytes.push_back(static_cast<uint8_t>(perkClaims_.size()));
            for (const auto& claim : perkClaims_) {
                bytes.push_back(static_cast<uint8_t>(claim.perk >> 8));
                bytes.push_back(static_cast<uint8_t>(claim.perk));
                bytes.push_back(claim.rank);
                bytes.insert(bytes.end(), claim.receipt.begin(), claim.receipt.end());
            }
        }
        append32(bytes, checksum(bytes.data(), bytes.size()));
        return bytes;
    }

    bool TrainingState::decode(const uint8_t* bytes, size_t size)
    {
        reset();
        if (size == 0) return true;
        if (bytes == nullptr || size < kMinTrailerSize || size > kMaxTrailerSize
            || !std::equal(std::begin(kMagic), std::end(kMagic), bytes)
            || read32(bytes + 12) != size - 20
            || read32(bytes + size - 4) != checksum(bytes, size - 4)) return false;
        uint32_t version = read32(bytes + 8);
        TrainingState candidate;
        std::copy(bytes + 16, bytes + 32, candidate.profile_.begin());
        size_t offset = 34;
        if (version == 1 || version == 2) {
            if (size != kLegacyTrailerSize) return false;
            for (int track = 0; track < 2; track++) {
                int mask = bytes[32 + track];
                if (version == 1) {
                    if (mask > 2) return false;
                    mask = (1 << mask) - 1;
                } else if (mask > 3)
                    return false;
                for (int level = 0; level < 2; level++) {
                    TrainingReceipt receipt;
                    std::copy(bytes + offset, bytes + offset + receipt.size(), receipt.begin());
                    offset += receipt.size();
                    bool claimed = (mask & (1 << level)) != 0;
                    if (empty(receipt) == claimed) return false;
                    if (claimed && !candidate.accept(track == 0 ? SKILL_UNARMED : SKILL_SCIENCE, level + 1, receipt)) return false;
                }
            }
        } else if (version == 3 || version == kVersion || version == 5) {
            size_t recordSize = version == 3 ? 19 : 21;
            size_t count = (size_t(bytes[32]) << 8) | bytes[33];
            size_t skillEnd = kMinTrailerSize + count * recordSize;
            if (count > kMaxClaims || (version != 5 && size != skillEnd)
                || (version == 5 && size < skillEnd + 2)) return false;
            for (size_t index = 0; index < count; index++) {
                int skill = bytes[offset];
                int level = (int(bytes[offset + 1]) << 8) | bytes[offset + 2];
                int points = version == 3 ? 5 : (int(bytes[offset + 3]) << 8) | bytes[offset + 4];
                TrainingReceipt receipt;
                std::copy(bytes + offset + recordSize - 16, bytes + offset + recordSize, receipt.begin());
                // Even an exact repeated row is corrupt serialized progress.
                if (!empty(candidate.receipt(skill, level)) || !candidate.accept(skill, level, receipt, points)) return false;
                offset += recordSize;
            }
            if (version == 5) {
                size_t perkCount = (size_t(bytes[offset]) << 8) | bytes[offset + 1];
                offset += 2;
                if (perkCount == 0 || perkCount > kMaxPerkClaims || offset + perkCount * 19 + 4 != size) return false;
                for (size_t index = 0; index < perkCount; index++) {
                    int perk = (int(bytes[offset]) << 8) | bytes[offset + 1];
                    int rank = bytes[offset + 2];
                    TrainingReceipt receipt;
                    std::copy(bytes + offset + 3, bytes + offset + 19, receipt.begin());
                    if (candidate.acceptPerk(candidate.profile(), perk, rank, receipt, rank - 1, rank) != PerkClaimResult::New) return false;
                    offset += 19;
                }
            }
        } else
            return false;
        *this = candidate;
        return true;
    }

    TrainingState& trainingState()
    {
        static TrainingState state;
        return state;
    }

} // namespace nixboy
} // namespace fallout
