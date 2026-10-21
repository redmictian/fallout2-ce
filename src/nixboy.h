#ifndef FALLOUT_NIXBOY_H_
#define FALLOUT_NIXBOY_H_

namespace fallout {

// Runtime opt-in, independent of save data. Configuration is reloaded on entry.
bool nixboyIsEnabled();
void nixboyOpen();

} // namespace fallout

#endif
