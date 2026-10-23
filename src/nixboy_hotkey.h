#ifndef FALLOUT_NIXBOY_HOTKEY_H_
#define FALLOUT_NIXBOY_HOTKEY_H_

#include <string_view>

namespace fallout {
namespace nixboy {

    enum class HotkeyKind { None,
        Letter,
        ControlLetter,
        AltLetter,
        Function };

    struct Hotkey {
        HotkeyKind kind = HotkeyKind::None;
        // Uppercase ASCII letter, or function-key number 1..12.
        int key = 0;
    };

    // Case-insensitive, with optional surrounding whitespace. Invalid values
    // return false and clear the binding so they cannot capture another action.
    bool parseHotkey(std::string_view text, Hotkey& hotkey);

} // namespace nixboy
} // namespace fallout

#endif // FALLOUT_NIXBOY_HOTKEY_H_
