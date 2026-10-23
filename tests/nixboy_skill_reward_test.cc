#include "nixboy_training_state.h"
#include "object.h"
#include "proto.h"
#include "settings.h"
#include "skill.h"

#include <cstdlib>
#include <iostream>
#include <stdexcept>

// Link the real skillGetValue implementation, isolating only its existing
// character/prototype/stat dependencies. Unused skill-use code is discarded.
namespace fallout {
static Object player {};
static Proto playerProto {};
Object* gDude = &player;
Settings settings;
int protoGetProto(int, Proto** proto)
{
    *proto = &playerProto;
    return 0;
}
int critterGetStat(Object*, int) { return 5; }
int perkGetRank(Object*, int) { return 0; }
int traitGetSkillModifier(int) { return 0; }
int perkGetSkillModifier(Object*, int) { return 0; }
} // namespace fallout

namespace {
void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}
} // namespace

int main()
{
    using namespace fallout;
    try {
        auto& training = nixboy::trainingState();
        nixboy::TrainingReceipt receipt {};
        receipt[0] = 1;
        for (bool tagged : { false, true }) {
            training.reset();
            require(training.ensureProfile(), "profile generation");
            int taggedSkills[NUM_TAGGED_SKILLS] { tagged ? SKILL_SCIENCE : -1, -1, -1, -1 };
            skillsSetTagged(taggedSkills, NUM_TAGGED_SKILLS);
            playerProto.critter.data.skills[SKILL_SCIENCE] = 10;
            int before = skillGetValue(gDude, SKILL_SCIENCE);
            require(training.accept(SKILL_SCIENCE, 1, receipt), "accept learning level");
            require(skillGetValue(gDude, SKILL_SCIENCE) == before + 5, "real skill value gets exactly five points whether tagged or untagged");
            require(skillGetBaseValue(gDude, SKILL_SCIENCE) == 10, "raw trained rank unchanged");
            require(training.accept(SKILL_SCIENCE, 1, receipt)
                    && skillGetValue(gDude, SKILL_SCIENCE) == before + 5,
                "duplicate reward never stacks in actual skill value");
            Object other {};
            require(skillGetValue(&other, SKILL_SCIENCE) == 30, "player training does not change other critters");
        }
        training.reset();
        int noTags[NUM_TAGGED_SKILLS] { -1, -1, -1, -1 };
        skillsSetTagged(noTags, NUM_TAGGED_SKILLS);
        playerProto.critter.data.skills[SKILL_SCIENCE] = 277;
        require(skillGetValue(gDude, SKILL_SCIENCE) == 297, "near-cap setup");
        require(training.ensureProfile() && training.accept(SKILL_SCIENCE, 1, receipt), "near-cap reward");
        require(skillGetValue(gDude, SKILL_SCIENCE) == 300, "existing 300-percent cap retained");
        training.reset();
        require(skillGetValue(gDude, SKILL_SCIENCE) == 297, "new-game reset removes old character bonus");
        std::cout << "Nix-Boy real skill reward: all tests passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << "Nix-Boy real skill reward test failure: " << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
