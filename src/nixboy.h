#ifndef FALLOUT_NIXBOY_H_
#define FALLOUT_NIXBOY_H_

namespace fallout {

// Runtime opt-in, independent of save data. Configuration is reloaded on entry.
bool nixboyIsEnabled();
// The configured shortcut is cached at first key event; restart to reload it.
bool nixboyIsHotkey(int eventCode);
void nixboyOpen();

} // namespace fallout

#endif
