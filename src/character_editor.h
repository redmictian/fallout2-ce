#ifndef CHARACTER_EDITOR_H
#define CHARACTER_EDITOR_H

#include "db.h"

namespace fallout {

extern int gCharacterEditorRemainingCharacterPoints;

// reviewPerk is an explicit debug launch target: focus its card and defer the
// pending level-up picker until a normal editor visit.
int characterEditorShow(bool isCreationMode, int reviewPerk = -1);
void characterEditorInit();
bool _isdoschar(int ch);
char* _strmfe(char* dest, const char* name, const char* ext);
int characterEditorSave(File* stream);
int characterEditorLoad(File* stream);
void characterEditorReset();

} // namespace fallout

#endif /* CHARACTER_EDITOR_H */
