// Inspect the real descriptor table and execute the production acquisition,
// removal, eligibility, skill-modifier and persistence code with isolated world IO.
#include "../src/perk.cc"
#include "perk_training.h"
#include "debug_perks.h"
#include "nixboy_perk_reward.h"

#include <array>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace fallout {
Object player {};
Object companion {};
Object* gDude = &player;
int gPartyMemberDescriptionsLength = 2;
int pids[] = { 0x1000000, 0x1000001 };
int* gPartyMemberPids = pids;
std::array<std::array<int, STAT_COUNT>, 2> bonus {};
std::array<int, PC_STAT_COUNT> pc {};
int currentHp = 20;
std::vector<int> savedRanks;
size_t readIndex = 0;
int slot(Object* obj) { return obj == gDude ? 0 : 1; }
int debugPrint(const char*, ...) { return 0; }
int critterGetStat(Object*, int) { return 10; }
int critterGetBonusStat(Object* obj, int stat) { return bonus[slot(obj)][stat]; }
int critterSetBonusStat(Object* obj, int stat, int value) { bonus[slot(obj)][stat] = value; return 0; }
int critterAdjustHitPoints(Object*, int value) { currentHp += value; return 0; }
int pcGetStat(int stat) { return pc[stat]; }
int pcSetStat(int stat, int value) { pc[stat] = value; return 0; }
int pcGetExperienceForLevel(int level) { return level * 1000; }
int pcAddExperienceWithOptions(int xp, bool, int*) { pc[PC_STAT_EXPERIENCE] += xp; return 0; }
int skillGetValue(Object*, int) { return 100; }
int gameGetGlobalVar(int) { return 100; }
int objectGetLightIntensity(Object*) { return 65536; }
void skillsGetTagged(int* tags, int count) { for (int i = 0; i < count; ++i) tags[i] = -1; }
void traitsGetSelected(int* first, int* second) { *first = -1; *second = -1; }
int fileWriteInt32(File*, int value) { savedRanks.push_back(value); return 0; }
int fileReadInt32(File*, int* value) { if (readIndex == savedRanks.size()) return -1; *value = savedRanks[readIndex++]; return 0; }
} // namespace fallout

static void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

int main()
{
    using namespace fallout;
    try {
        PerkRankData ranks[2] {};
        gPartyMemberPerkRanks = ranks;
        player.pid = pids[0];
        companion.pid = pids[1];
        int affected = 0;
        for (int perk = 0; perk < PERK_COUNT; ++perk) {
            const auto& definition = gPerkDescriptions[perk];
#if defined(FALLOUT_NIXBOY_PERK_TRAINING)
            bool expected = definition.maxRank > 0 && definition.minLevel <= PC_LEVEL_MAX;
            require(perkUsesTraining(perk) == expected, "training set must equal all and only 76 selectable descriptors");
#else
            require(!perkUsesTraining(perk), "disabled add-on keeps vanilla policy");
#endif
            if (!perkUsesTraining(perk)) continue;
            ++affected;
            require(perkTrainingMaxRank(perk, definition.maxRank) == definition.maxRank + 1, "every selectable maximum increased exactly once");
            char* text = perkGetDescription(perk);
            require(text != nullptr && std::strstr(text, "Rank 1: Training unlocked. No bonus.") == text, "every affected perk has the unlock description");
            require(std::strstr(text, "nix-boy challenge") != nullptr, "every affected description names challenge progression");
            require(std::strstr(text, definition.maxRank == 1 ? "reach rank 2" : "Each rank above 1") != nullptr, "correct single or multirank description");
            // Legacy word wrapping requires writable buffers.
            char first = text[0]; text[0] = '\0'; text[0] = first;
            perksReset(); bonus = {}; pc = {}; currentHp = 20;
            pc[PC_STAT_LEVEL] = 99;
            auto beforeBonus = bonus;
            auto beforePc = pc;
            // Some original upper-bound SPECIAL/global requirements are not
            // satisfied by this test character. Forced grants still test every ID.
            require(perkAddForce(gDude, perk) == 0, "first ownership can be granted");
            require(perkGetRank(gDude, perk) == 1 && perkGetBenefitRank(gDude, perk) == 0, "owned rank one has zero benefits");
            require(bonus == beforeBonus && pc == beforePc && currentHp == 20, "rank one changes no stored stats, XP, points or HP");
            for (int level = 1; level <= 99; ++level) {
                pc[PC_STAT_LEVEL] = level;
                require(!perkCanSelect(gDude, perk) && perkAdd(gDude, perk) == -1, "all reachable player levels reject rank two");
            }
            pc[PC_STAT_LEVEL] = 99;
            for (int raw = 2; raw <= definition.maxRank + 1; ++raw) {
                require(perkAddForce(gDude, perk) == 0, "forced upgrade remains available");
                require(perkGetBenefitRank(gDude, perk) == raw - 1, "benefits shift by exactly one rank");
                if (definition.stat != -1) require(bonus[0][definition.stat] == definition.statModifier * (raw - 1), "stored stat matches original benefit rank");
            }
            require(perkAddForce(gDude, perk) == -1, "forced upgrade respects raised maximum");
            if (perk == PERK_LIFEGIVER) require(currentHp == 20 + definition.maxRank * 4 && bonus[0][STAT_MAXIMUM_HIT_POINTS] == definition.maxRank * 4, "immediate Lifegiver HP starts at rank two");
            if (perk == PERK_EDUCATED) require(pc[PC_STAT_UNSPENT_SKILL_POINTS] == definition.maxRank * 2, "immediate Educated points start at rank two");
            for (int raw = definition.maxRank + 1; raw > 0; --raw) {
                require(perkRemove(gDude, perk) == 0, "rank removal succeeds");
                if (definition.stat != -1) require(bonus[0][definition.stat] == definition.statModifier * (raw > 1 ? raw - 2 : 0), "stored delta removal is symmetric including 2 to 1 and 1 to 0");
            }
            require(perkGetRank(gDude, perk) == 0, "removal reaches unowned");
        }
#if defined(FALLOUT_NIXBOY_PERK_TRAINING)
        require(affected == 76 && perkTrainingSaveRelease() == 'T', "76 perks and distinct save identity");
#else
        require(affected == 0 && perkTrainingSaveRelease() == 'R', "vanilla save identity when disabled");
#endif
        perksReset(); bonus = {}; pc = {};
        nixboy::TrainingState unownedLearning;
        require(unownedLearning.ensureProfile(), "unowned test profile");
        nixboy::TrainingReceipt unownedReceipt {}; unownedReceipt.back() = 18;
        require(nixboy::applyPerkTrainingReward(unownedLearning, unownedLearning.profile(), PERK_TOUGHNESS, 2, unownedReceipt)
            == nixboy::PerkClaimResult::Rejected && perkGetRank(gDude, PERK_TOUGHNESS) == 0
            && bonus[0][STAT_DAMAGE_RESISTANCE] == 0, "unowned production perk does not acquire reward ownership");
        pc[PC_STAT_LEVEL] = 2;
        require(perkAdd(gDude, PERK_TOUGHNESS) == -1, "first rank retains original minimum level");
        pc[PC_STAT_LEVEL] = 3;
        require(perkCanSelect(gDude, PERK_TOUGHNESS) && perkAdd(gDude, PERK_TOUGHNESS) == 0, "eligible first rank is selectable");
        int firstRank = perkBenefitRank(PERK_TOUGHNESS, 1);
        require(bonus[0][STAT_DAMAGE_RESISTANCE] == firstRank * 10, "real Toughness acquisition effect");
        nixboy::TrainingState learning;
        require(learning.ensureProfile(), "reward profile");
        nixboy::TrainingReceipt perkReceipt {}; perkReceipt.back() = 19;
        auto olderLearning = learning;
        auto olderSave = learning.encode();
        if (perkUsesTraining(PERK_TOUGHNESS)) {
            auto wrongProfile = learning.profile(); wrongProfile[0] ^= 1;
            require(nixboy::applyPerkTrainingReward(learning, wrongProfile, PERK_TOUGHNESS, 2, perkReceipt)
                == nixboy::PerkClaimResult::Rejected && perkGetRank(gDude, PERK_TOUGHNESS) == 1, "wrong profile leaves production perk unchanged");
            require(nixboy::applyPerkTrainingReward(learning, learning.profile(), PERK_TOUGHNESS, 2, perkReceipt)
                == nixboy::PerkClaimResult::New, "earned typed passive reward uses production grant");
            require(nixboy::applyPerkTrainingReward(learning, learning.profile(), PERK_TOUGHNESS, 2, perkReceipt)
                == nixboy::PerkClaimResult::Duplicate && bonus[0][STAT_DAMAGE_RESISTANCE] == 10
                && perkGetRank(gDude, PERK_TOUGHNESS) == 2, "duplicate ACK adds no production rank or effect");
            require(nixboy::applyPerkTrainingReward(olderLearning, olderLearning.profile(), PERK_TOUGHNESS, 2, perkReceipt)
                == nixboy::PerkClaimResult::Rejected, "missing ledger at already upgraded rank does not add another rank");
            require(perkRemove(gDude, PERK_TOUGHNESS) == 0 && perkGetRank(gDude, PERK_TOUGHNESS) == 1
                && bonus[0][STAT_DAMAGE_RESISTANCE] == 0, "older-save native rank/effect state restored");
            auto oldProfile = olderLearning.profile();
            require(olderLearning.decode(olderSave.data(), olderSave.size()) && olderLearning.ensureProfile()
                && olderLearning.profile() != oldProfile, "older save opens a fresh external assessment scope");
            require(nixboy::applyPerkTrainingReward(olderLearning, oldProfile, PERK_TOUGHNESS, 2, perkReceipt)
                == nixboy::PerkClaimResult::Rejected && perkGetRank(gDude, PERK_TOUGHNESS) == 1,
                "old scope cannot restore later perk reward");
            nixboy::TrainingReceipt freshCompletion {}; freshCompletion.back() = 20;
            require(nixboy::applyPerkTrainingReward(olderLearning, olderLearning.profile(), PERK_TOUGHNESS, 2, freshCompletion)
                == nixboy::PerkClaimResult::New && perkGetRank(gDude, PERK_TOUGHNESS) == 2
                && bonus[0][STAT_DAMAGE_RESISTANCE] == 10, "a fresh completion can earn the rolled-back perk once");
        } else {
            require(nixboy::applyPerkTrainingReward(learning, learning.profile(), PERK_TOUGHNESS, 2, perkReceipt)
                == nixboy::PerkClaimResult::Rejected, "feature disabled rejects perk rewards");
            require(perkAddForce(gDude, PERK_TOUGHNESS) == 0, "ordinary Toughness upgrade when feature disabled");
        }
        require(bonus[0][STAT_DAMAGE_RESISTANCE] == perkBenefitRank(PERK_TOUGHNESS, 2) * 10, "real upgraded Toughness effect");
        perksReset(); bonus = {};
        require(perkAddForce(gDude, PERK_SURVIVALIST) == 0, "Survivalist unlock");
        require(perkGetSkillModifier(gDude, SKILL_OUTDOORSMAN) == firstRank * 25, "real Survivalist unlock modifier");
#if defined(FALLOUT_NIXBOY_PERK_TRAINING)
        require(perkAddForce(gDude, PERK_SURVIVALIST) == 0 && perkGetSkillModifier(gDude, SKILL_OUTDOORSMAN) == 25, "Survivalist rank two grants 25 points");
#endif
        // Equipment and withdrawal callbacks do not acquire ranks.
        perkAddEffect(gDude, PERK_POWERED_ARMOR);
        require(bonus[0][STAT_STRENGTH] == 3, "armor effect unchanged without ownership");
        perkRemoveEffect(gDude, PERK_POWERED_ARMOR);
        require(bonus[0][STAT_STRENGTH] == 0, "armor removal unchanged");
        perkAddEffect(gDude, PERK_BUFFOUT_ADDICTION);
        require(bonus[0][STAT_STRENGTH] == -2, "withdrawal effect unchanged");
        perkRemoveEffect(gDude, PERK_BUFFOUT_ADDICTION);
        require(bonus[0][STAT_STRENGTH] == 0, "withdrawal removal unchanged");
        require(perkAddForce(&companion, PERK_TOUGHNESS) == 0 && perkGetBenefitRank(&companion, PERK_TOUGHNESS) == 1 && bonus[1][STAT_DAMAGE_RESISTANCE] == 10, "companion ranks and effects retain vanilla semantics");
        require(perkAddForce(gDude, PERK_VAULT_CITY_INOCULATIONS) == 0 && perkGetBenefitRank(gDude, PERK_VAULT_CITY_INOCULATIONS) == 1, "world reward ownership stays active at rank one");
        savedRanks.clear(); readIndex = 0;
        require(perksSave(nullptr) == 0, "serialize ranks");
        auto savedBonus = bonus;
        perksReset();
        require(perksLoad(nullptr) == 0 && perkGetRank(gDude, PERK_SURVIVALIST) == (perkUsesTraining(PERK_SURVIVALIST) ? 2 : 1), "raw ranks survive round trip");
        require(bonus == savedBonus, "load does not replay stored stat effects");
        // No options when all choices are already unlocked; opportunity retention
        // and the UI early return are additionally checked in the source audit.
        if (perkUsesTraining(PERK_AWARENESS)) {
            perksReset(); pc[PC_STAT_LEVEL] = 99;
            for (int perk = 0; perk < PERK_COUNT; ++perk) if (perkUsesTraining(perk)) require(perkAddForce(gDude, perk) == 0, "unlock all");
            int options[PERK_COUNT];
            require(perkGetAvailablePerks(gDude, options) == 0, "no stale candidate once every perk is unlocked");
            perksReset(); bonus = {}; pc = {};
            DebugLaunchOptions gallery;
            gallery.reviewAllPerks = true;
            require(debugGrantReviewPerks(gallery), "debug gallery grants through real production code");
            for (int perk = 0; perk < PERK_COUNT; ++perk) {
                require(perkGetRank(gDude, perk) == (perkUsesTraining(perk) ? 1 : 0), "gallery grants exactly the 76 level-up perks");
            }
            perksReset(); bonus = {}; pc = {};
            DebugLaunchOptions single;
            single.reviewPerk = PERK_TOUGHNESS; single.reviewRank = 4;
            require(debugGrantReviewPerks(single) && bonus[0][STAT_DAMAGE_RESISTANCE] == 30, "debug specific rank preserves old benefit ceiling");
            single.reviewPerk = PERK_SURVIVALIST; single.reviewRank = 3;
            require(!debugGrantReviewPerks(single) && perkGetRank(gDude, PERK_SURVIVALIST) == 0, "invalid debug rank rejected before acquisition");
        }
        std::cout << "Perk training: production transition/eligibility/effect/save tests passed (" << affected << " affected perks)\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
