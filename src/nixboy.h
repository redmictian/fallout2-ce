#ifndef FALLOUT_NIXBOY_H_
#define FALLOUT_NIXBOY_H_

namespace fallout {

// Runtime opt-in, independent of save data. Configuration is reloaded on entry.
bool nixboyIsEnabled();
// The configured shortcut is cached at first key event; restart to reload it.
bool nixboyIsHotkey(int eventCode);
void nixboyOpen();
// Capture save-associated bridge progression before the checked SAVE.DAT write.
// Failure leaves the existing slot untouched and reports a useful local error.
bool nixboyPrepareSave();

// Independent local reading UI; no terminal token, Docker or training identity.
bool nixboyIsWikiEnabled();
bool nixboyIsWikiHotkey(int eventCode);
void nixboyOpenWiki();

} // namespace fallout

#endif
