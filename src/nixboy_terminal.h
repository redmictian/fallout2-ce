#ifndef FALLOUT_NIXBOY_TERMINAL_H_
#define FALLOUT_NIXBOY_TERMINAL_H_

#include <cstdint>
#include <functional>
#include <string>

#include "nixboy_client.h"

namespace fallout {

struct NixboyTrainingCallbacks {
    bool enabled = false;
    std::function<bool(uint8_t, uint16_t, const nixboy::TrainingReceipt&)> reward;
    std::function<nixboy::Context()> snapshot;
    std::function<std::vector<nixboy::TrainingClaim>()> progress;
};

// Opens a modal terminal. Only the UI thread invokes onResult. Returns false
// if a local window could not be created; backend failures remain visible in
// the modal. F10 and the Close button close the session; Esc goes to Linux.
bool nixboyTerminalOpen(uint16_t port, const std::string& token,
    const nixboy::Context& context,
    const std::function<void(const std::string&, int32_t)>& onResult,
    const NixboyTrainingCallbacks& training = {});

} // namespace fallout

#endif // FALLOUT_NIXBOY_TERMINAL_H_
