#ifndef FALLOUT_NIXBOY_WIKI_H_
#define FALLOUT_NIXBOY_WIKI_H_

#include <string>

namespace fallout {

// The caller pauses gameplay. This independent reader needs no bridge or token.
bool nixboyWikiWindowOpen(const std::string& homePath, bool fullscreen, std::string& error);

} // namespace fallout

#endif
