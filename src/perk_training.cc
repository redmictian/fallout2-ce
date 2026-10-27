#include "perk_training.h"

namespace fallout {
namespace {

#define TRAINING "Rank 1: Training unlocked. No bonus. "
#define SINGLE TRAINING "Complete a nix-boy challenge to reach rank 2 and "
#define MULTI TRAINING "Complete a nix-boy challenge to gain each additional rank. Each rank above 1 grants "

struct TrainingDescription {
    int perk;
    char text[640];
};

// Authored descriptions; no original game messages or assets are redistributed.
// Preserve existing benefit ceilings: one unlock plus the original benefit ranks.
TrainingDescription descriptions[] = {
    { PERK_AWARENESS, SINGLE "see exact HP and equipped weapon details when examining living critters." },
    { PERK_BONUS_HTH_ATTACKS, SINGLE "reduce melee and unarmed attack costs by 1 AP, subject to the minimum attack cost." },
    { PERK_BONUS_HTH_DAMAGE, MULTI "+2 damage with melee and unarmed attacks." },
    { PERK_BONUS_MOVE, MULTI "+2 movement-only AP at the start of each combat turn." },
    { PERK_BONUS_RANGED_DAMAGE, MULTI "+2 damage with ranged attacks." },
    { PERK_BONUS_RATE_OF_FIRE, SINGLE "reduce ranged attack costs by 1 AP, subject to the minimum attack cost." },
    { PERK_EARLIER_SEQUENCE, MULTI "+2 Sequence." },
    { PERK_FASTER_HEALING, MULTI "+2 Healing Rate." },
    { PERK_MORE_CRITICALS, MULTI "+5 percentage points of critical chance." },
    { PERK_NIGHT_VISION, SINGLE "increase effective ambient light by approximately 20 percentage points of maximum light." },
    { PERK_PRESENCE, MULTI "a 10% improvement to initial reactions where supported by dialogue scripts." },
    { PERK_RAD_RESISTANCE, MULTI "+15 percentage points of radiation resistance." },
    { PERK_TOUGHNESS, MULTI "+10 percentage points of normal damage resistance." },
    { PERK_STRONG_BACK, MULTI "+50 pounds of carrying capacity." },
    { PERK_SHARPSHOOTER, SINGLE "gain +2 effective Perception for ranged distance penalties." },
    { PERK_SILENT_RUNNING, SINGLE "run while remaining in Sneak mode." },
    { PERK_SURVIVALIST, SINGLE "gain +25 percentage points to Outdoorsman." },
    { PERK_MASTER_TRADER, SINGLE "gain a 25-point purchasing modifier in barter. Other pricing factors still apply." },
    { PERK_EDUCATED, MULTI "+2 skill points at future level-ups and +2 current unspent skill points on acquisition." },
    { PERK_HEALER, MULTI "4 to 10 additional HP healed when using First Aid or Doctor." },
    { PERK_FORTUNE_FINDER, SINGLE "double money quantities generated in random encounters." },
    { PERK_BETTER_CRITICALS, SINGLE "gain +20 on the critical effect table. Critical chance is unchanged." },
    { PERK_EMPATHY, SINGLE "see dialogue reaction categories through option colors." },
    { PERK_SLAYER, SINGLE "turn successful melee and unarmed attacks into critical hits." },
    { PERK_SNIPER, SINGLE "gain a Luck check to turn successful ranged hits into critical hits." },
    { PERK_SILENT_DEATH, SINGLE "multiply qualifying rear melee or unarmed damage while sneaking: x4 on the noncritical path, an extra x2 on the critical path." },
    { PERK_ACTION_BOY, MULTI "+1 maximum AP." },
    { PERK_LIFEGIVER, MULTI "+4 HP at future level-ups and +4 maximum and current HP on acquisition." },
    { PERK_DODGER, SINGLE "gain +5 Armor Class." },
    { PERK_SNAKEATER, MULTI "+25 percentage points of poison resistance." },
    { PERK_MR_FIXIT, SINGLE "gain +10 percentage points each to Science and Repair." },
    { PERK_MEDIC, SINGLE "gain +10 percentage points each to First Aid and Doctor." },
    { PERK_MASTER_THIEF, SINGLE "gain +15 percentage points each to Lockpick and Steal." },
    { PERK_SPEAKER, SINGLE "gain +20 percentage points to Speech." },
    { PERK_HEAVE_HO, MULTI "+2 effective Strength for throwing range, capped at Strength 10 and the weapon's maximum range." },
    { PERK_PICKPOCKET, SINGLE "ignore item-size and target-facing penalties when stealing." },
    { PERK_GHOST, SINGLE "gain +20 percentage points to Sneak when the game's light condition passes." },
    { PERK_CULT_OF_PERSONALITY, SINGLE "have favorable reputation treatment where supported by dialogue scripts." },
    { PERK_EXPLORER, SINGLE "gain +2 to the worldmap special-encounter selection roll." },
    { PERK_PATHFINDER, MULTI "a 25% reduction in worldmap travel time." },
    { PERK_SCOUT, SINGLE "explore one extra subtile in each direction and gain +1 to the special-encounter roll." },
    { PERK_MYSTERIOUS_STRANGER, SINGLE "unlock help from the Mysterious Stranger through encounter scripts. A lost ally is not replaced." },
    { PERK_RANGER, SINGLE "gain +15 percentage points to Outdoorsman and +1 to the special-encounter roll." },
    { PERK_QUICK_POCKETS, SINGLE "open inventory in combat for 2 AP instead of 4 AP." },
    { PERK_SMOOTH_TALKER, MULTI "+1 effective Intelligence for dialogue eligibility checks." },
    { PERK_SWIFT_LEARNER, MULTI "+5% of the base XP award whenever experience is earned." },
    { PERK_TAG, SINGLE "choose a fourth tagged skill in the perk-selection dialog. Tagged skills improve twice as fast." },
    { PERK_MUTATE, SINGLE "replace one selected character trait in the perk-selection dialog." },
    { PERK_ADRENALINE_RUSH, SINGLE "gain +1 Strength while current HP is below half of maximum HP." },
    { PERK_CAUTIOUS_NATURE, SINGLE "gain +3 effective Perception for placement in random encounters." },
    { PERK_COMPREHENSION, SINGLE "gain 50% more skill points from books, with integer rounding." },
    { PERK_DEMOLITION_EXPERT, SINGLE "succeed at explosive placement rolls and add +10 to minimum and maximum explosion damage." },
    { PERK_GAMBLER, SINGLE "gain +20 percentage points to Gambling." },
    { PERK_GAIN_STRENGTH, SINGLE "gain +1 Strength." },
    { PERK_GAIN_PERCEPTION, SINGLE "gain +1 Perception." },
    { PERK_GAIN_ENDURANCE, SINGLE "gain +1 Endurance." },
    { PERK_GAIN_CHARISMA, SINGLE "gain +1 Charisma." },
    { PERK_GAIN_INTELLIGENCE, SINGLE "gain +1 Intelligence." },
    { PERK_GAIN_AGILITY, SINGLE "gain +1 Agility." },
    { PERK_GAIN_LUCK, SINGLE "gain +1 Luck." },
    { PERK_HARMLESS, SINGLE "gain +20 percentage points to Steal." },
    { PERK_HERE_AND_NOW, SINGLE "immediately gain the XP needed to reach the next experience level." },
    { PERK_HTH_EVADE, SINGLE "double unused-AP Armor Class and add Unarmed/12 to Armor Class when unarmed and defending in combat." },
    { PERK_KAMA_SUTRA_MASTER, SINGLE "unlock improved sexual outcomes where supported by dialogue scripts." },
    { PERK_KARMA_BEACON, SINGLE "double karma's effect in reactions where supported by scripts. Saved karma itself is unchanged." },
    { PERK_LIGHT_STEP, SINGLE "unlock trap avoidance where supported by trap scripts." },
    { PERK_LIVING_ANATOMY, SINGLE "gain +10 percentage points to Doctor and +5 damage against targets other than robots and aliens." },
    { PERK_MAGNETIC_PERSONALITY, SINGLE "gain one extra companion slot where supported by companion scripts." },
    { PERK_NEGOTIATOR, SINGLE "gain +10 percentage points each to Speech and Barter." },
    { PERK_PACK_RAT, SINGLE "gain +50 pounds of carrying capacity." },
    { PERK_PYROMANIAC, SINGLE "gain +5 fire-weapon damage and easier fiery death animations." },
    { PERK_QUICK_RECOVERY, SINGLE "stand up after knockdown for 1 AP instead of 3 AP." },
    { PERK_SALESMAN, SINGLE "gain +20 percentage points to Barter." },
    { PERK_STONEWALL, SINGLE "gain knockdown protection and a 50% chance to prevent knockback in qualifying combat checks." },
    { PERK_THIEF, SINGLE "gain +10 percentage points each to Sneak, Lockpick, Steal and Traps." },
    { PERK_WEAPON_HANDLING, SINGLE "treat weapon Strength requirements as 3 lower. General Strength is unchanged." },
};

#undef MULTI
#undef SINGLE
#undef TRAINING

} // namespace

char* perkTrainingDescription(int perk)
{
    for (auto& entry : descriptions) {
        if (entry.perk == perk) {
            return entry.text;
        }
    }
    return nullptr;
}

} // namespace fallout
