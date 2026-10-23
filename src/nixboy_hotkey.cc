#include "nixboy_hotkey.h"

#include <string>

namespace fallout {
namespace nixboy {

    bool parseHotkey(std::string_view text, Hotkey& hotkey)
    {
        hotkey = {};
        constexpr std::string_view whitespace = " \t\r\n";
        size_t begin = text.find_first_not_of(whitespace);
        if (begin == std::string_view::npos) return false;
        size_t end = text.find_last_not_of(whitespace);
        std::string value(text.substr(begin, end - begin + 1));
        for (char& ch : value) {
            if (ch >= 'a' && ch <= 'z') ch = static_cast<char>(ch - 'a' + 'A');
        }
        if (value == "NONE") return true;
        HotkeyKind kind = HotkeyKind::Letter;
        if (value.compare(0, 5, "CTRL+") == 0) {
            kind = HotkeyKind::ControlLetter;
            value.erase(0, 5);
        } else if (value.compare(0, 4, "ALT+") == 0) {
            kind = HotkeyKind::AltLetter;
            value.erase(0, 4);
        }
        if (value.size() == 1 && value[0] >= 'A' && value[0] <= 'Z') {
            // The engine maps these to the same logical codes as Backspace,
            // Tab and Enter, so they cannot be used as distinct shortcuts.
            if (kind == HotkeyKind::ControlLetter && (value[0] == 'H' || value[0] == 'I' || value[0] == 'M')) return false;
            hotkey = { kind, value[0] };
            return true;
        }
        if (kind != HotkeyKind::Letter) return false;
        if (value.size() == 2 && value[0] == 'F' && value[1] >= '1' && value[1] <= '9') {
            hotkey = { HotkeyKind::Function, value[1] - '0' };
            return true;
        }
        if (value == "F10" || value == "F11" || value == "F12") {
            hotkey = { HotkeyKind::Function, 10 + value[2] - '0' };
            return true;
        }
        return false;
    }

} // namespace nixboy
} // namespace fallout
