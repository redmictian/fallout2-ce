#ifndef FALLOUT_NIXBOY_TERMINAL_INPUT_H_
#define FALLOUT_NIXBOY_TERMINAL_INPUT_H_

#include <cstdint>
#include <string_view>

namespace fallout {
namespace nixboy {

    // A forwarded key may also produce SDL text (notably keypad and Alt chords).
    // Suppress one matching, single-character duplicate. Printable Ctrl/Alt chords
    // may translate to different ASCII under Shift/the keyboard layout, so their
    // next single ASCII event is also a duplicate until that key is released.
    // Independent text/IME commits need no preceding keydown and must never inherit
    // a blanket suppression flag from an earlier Return or control key.
    class TerminalTextInput {
    public:
        void keyDown(uint32_t scanCode, char duplicate = '\0', char alternate = '\0', bool printableChord = false)
        {
            scanCode_ = scanCode;
            duplicate_ = duplicate;
            alternate_ = alternate;
            printableChord_ = printableChord;
        }

        void keyUp(uint32_t scanCode)
        {
            if (scanCode == scanCode_) reset();
        }

        bool acceptText(std::string_view text)
        {
            const bool duplicate = text.size() == 1
                && ((duplicate_ != '\0' && text.front() == duplicate_)
                    || (alternate_ != '\0' && text.front() == alternate_));
            const bool chordText = printableChord_ && text.size() == 1
                && text.front() >= ' ' && text.front() <= '~';
            reset();
            return !duplicate && !chordText;
        }

        void reset()
        {
            scanCode_ = 0;
            duplicate_ = '\0';
            alternate_ = '\0';
            printableChord_ = false;
        }

    private:
        uint32_t scanCode_ = 0;
        char duplicate_ = '\0';
        char alternate_ = '\0';
        bool printableChord_ = false;
    };

} // namespace nixboy
} // namespace fallout

#endif // FALLOUT_NIXBOY_TERMINAL_INPUT_H_
