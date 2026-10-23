#include "nixboy_hotkey.h"

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

using fallout::nixboy::Hotkey;
using fallout::nixboy::HotkeyKind;
using fallout::nixboy::parseHotkey;

namespace {
void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

void expect(const std::string& text, HotkeyKind kind, int key)
{
    Hotkey parsed;
    require(parseHotkey(text, parsed), "accepted shortcut syntax");
    require(parsed.kind == kind && parsed.key == key, "normalized shortcut value");
}
} // namespace

int main()
{
    try {
        expect("Ctrl+N", HotkeyKind::ControlLetter, 'N');
        expect(" cTrL+n\t", HotkeyKind::ControlLetter, 'N');
        expect("NoNe", HotkeyKind::None, 0);
        for (char letter = 'A'; letter <= 'Z'; letter++) {
            expect(std::string(1, letter), HotkeyKind::Letter, letter);
            expect(std::string(1, static_cast<char>(letter - 'A' + 'a')), HotkeyKind::Letter, letter);
            if (letter != 'H' && letter != 'I' && letter != 'M')
                expect(std::string("Ctrl+") + letter, HotkeyKind::ControlLetter, letter);
            expect(std::string("Alt+") + letter, HotkeyKind::AltLetter, letter);
        }
        for (int key = 1; key <= 12; key++) {
            expect("F" + std::to_string(key), HotkeyKind::Function, key);
            expect("f" + std::to_string(key), HotkeyKind::Function, key);
        }
        for (const char* invalid : { "", " ", "14", "0", "Ctrl+", "Alt+", "Ctrl+Alt+N", "Shift+N",
                 "Ctrl+F1", "Alt+F12", "Ctrl+ N", "N+Ctrl", "F0", "F13", "F01", "NN", "disabled",
                 "Ctrl+H", "Ctrl+I", "Ctrl+M", "ctrl+h", "ctrl+i", "ctrl+m" }) {
            Hotkey parsed { HotkeyKind::ControlLetter, 'N' };
            require(!parseHotkey(invalid, parsed) && parsed.kind == HotkeyKind::None && parsed.key == 0,
                "invalid shortcuts fail closed and clear earlier bindings");
        }
        std::cout << "nix-boy hotkey parser: all tests passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << "nix-boy hotkey test failure: " << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
