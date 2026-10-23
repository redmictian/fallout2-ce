#include "nixboy_training_state.h"

#include <algorithm>
#include <cstring>
#include <random>

#include "skill_defs.h"

namespace fallout {
namespace nixboy {
    namespace {
        constexpr uint8_t kMagic[] = { 'N', 'X', 'B', 'T', 'R', 'A', 'I', 'N' };
        constexpr uint32_t kVersion = 1;
        constexpr uint32_t kPayloadSize = 82;

        int trackIndex(int skill)
        {
            return skill == SKILL_UNARMED ? 0 : (skill == SKILL_SCIENCE ? 1 : -1);
        }

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
        int track = trackIndex(skill);
        return track < 0 ? 0 : completed_[track];
    }

    int TrainingState::bonus(int skill) const
    {
        return completed(skill) * 5;
    }

    TrainingReceipt TrainingState::receipt(int skill, int level) const
    {
        int track = trackIndex(skill);
        return track >= 0 && level >= 1 && level <= completed_[track] ? receipts_[track][level - 1] : TrainingReceipt {};
    }

    bool TrainingState::accept(int skill, int level, const TrainingReceipt& receipt)
    {
        int track = trackIndex(skill);
        if (track < 0 || level < 1 || level > 2 || empty(profile_) || empty(receipt)) return false;
        if (level <= completed_[track]) return receipts_[track][level - 1] == receipt;
        if (level != completed_[track] + 1) return false;
        for (const auto& trackReceipts : receipts_) {
            for (const auto& previous : trackReceipts) {
                if (previous == receipt) return false;
            }
        }
        receipts_[track][level - 1] = receipt;
        completed_[track] = static_cast<uint8_t>(level);
        return true;
    }

    std::vector<uint8_t> TrainingState::encode() const
    {
        std::vector<uint8_t> bytes(std::begin(kMagic), std::end(kMagic));
        append32(bytes, kVersion);
        append32(bytes, kPayloadSize);
        bytes.insert(bytes.end(), profile_.begin(), profile_.end());
        bytes.insert(bytes.end(), completed_.begin(), completed_.end());
        for (const auto& track : receipts_) {
            for (const auto& receipt : track)
                bytes.insert(bytes.end(), receipt.begin(), receipt.end());
        }
        append32(bytes, checksum(bytes.data(), bytes.size()));
        return bytes;
    }

    bool TrainingState::decode(const uint8_t* bytes, size_t size)
    {
        reset();
        if (size == 0) return true;
        if (bytes == nullptr || size != kTrailerSize
            || !std::equal(std::begin(kMagic), std::end(kMagic), bytes)
            || read32(bytes + 8) != kVersion || read32(bytes + 12) != kPayloadSize
            || read32(bytes + size - 4) != checksum(bytes, size - 4)) return false;
        TrainingState candidate;
        std::copy(bytes + 16, bytes + 32, candidate.profile_.begin());
        size_t offset = 34;
        for (int track = 0; track < 2; track++) {
            candidate.completed_[track] = bytes[32 + track];
            if (candidate.completed_[track] > 2) return false;
            for (int level = 0; level < 2; level++) {
                auto& receipt = candidate.receipts_[track][level];
                std::copy(bytes + offset, bytes + offset + receipt.size(), receipt.begin());
                offset += receipt.size();
                if (empty(receipt) != (level >= candidate.completed_[track])) return false;
            }
        }
        if (empty(candidate.profile_) && (candidate.completed_[0] != 0 || candidate.completed_[1] != 0)) return false;
        std::vector<TrainingReceipt> seen;
        for (const auto& track : candidate.receipts_) {
            for (const auto& receipt : track) {
                if (empty(receipt)) continue;
                if (std::find(seen.begin(), seen.end(), receipt) != seen.end()) return false;
                seen.push_back(receipt);
            }
        }
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
