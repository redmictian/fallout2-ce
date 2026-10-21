#ifndef CHARACTER_SELECTOR_H
#define CHARACTER_SELECTOR_H

namespace fallout {

int characterSelectorOpen();
// Load one of the original premades without opening the selector. Test launches
// deliberately ignore configurable replacements in the selector list.
bool characterSelectorLoadPremade(const char* name);

void premadeCharactersInit();
void premadeCharactersExit();

} // namespace fallout

#endif /* CHARACTER_SELECTOR_H */
