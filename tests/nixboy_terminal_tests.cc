#include <vterm.h>

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "../src/nixboy_terminal_input.h"

namespace {

void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

class Terminal {
public:
    VTerm* term = vterm_new(6, 20);
    VTermScreen* screen = vterm_obtain_screen(term);
    std::string output;
    bool visible = true;
    VTermScreenCallbacks callbacks = {};

    Terminal()
    {
        vterm_set_utf8(term, 1);
        vterm_output_set_callback(term, [](const char* data, size_t len, void* user) { static_cast<Terminal*>(user)->output.append(data, len); }, this);
        callbacks.settermprop = [](VTermProp prop, VTermValue* value, void* user) {
            if (prop == VTERM_PROP_CURSORVISIBLE) {
                static_cast<Terminal*>(user)->visible = value->boolean != 0;
            }
            return 1;
        };
        vterm_screen_set_callbacks(screen, &callbacks, this);
        vterm_screen_enable_altscreen(screen, 1);
        vterm_screen_reset(screen, 1);
    }

    ~Terminal() { vterm_free(term); }

    void feed(const std::string& text, bool fragmented = false)
    {
        if (fragmented) {
            for (const char& ch : text)
                vterm_input_write(term, &ch, 1);
        } else {
            vterm_input_write(term, text.data(), text.size());
        }
        vterm_screen_flush_damage(screen);
    }

    VTermScreenCell cell(int row, int col)
    {
        VTermScreenCell result = {};
        require(vterm_screen_get_cell(screen, { row, col }, &result) != 0, "cell unavailable");
        return result;
    }
};

void fragmentedTextAndAttributes()
{
    Terminal t;
    t.feed("\x1b[31;44;7;4mX\x1b[0me\xcc\x81\xe4\xb8\xad", true);
    const auto colored = t.cell(0, 0);
    require(VTERM_COLOR_IS_INDEXED(&colored.fg) && colored.fg.indexed.idx == 1, "SGR foreground");
    require(VTERM_COLOR_IS_INDEXED(&colored.bg) && colored.bg.indexed.idx == 4, "SGR background");
    require(colored.attrs.reverse && colored.attrs.underline, "SGR reverse/underline");
    const auto combining = t.cell(0, 1);
    require(combining.chars[0] == 'e' && combining.chars[1] == 0x301, "fragmented combining UTF-8");
    require(t.cell(0, 2).chars[0] == 0x4E2D && t.cell(0, 2).width == 2, "wide UTF-8 cell width");
}

void alternateScreenAndCursor()
{
    Terminal t;
    t.feed("main\x1b[?1049h\x1b[Hother", true);
    require(t.cell(0, 0).chars[0] == 'o', "alternate screen entered");
    t.feed("\x1b[?25l");
    require(!t.visible, "cursor hide");
    t.feed("\x1b[?1049l\x1b[?25h", true);
    require(t.cell(0, 0).chars[0] == 'm' && t.cell(0, 3).chars[0] == 'n', "primary screen restored");
    VTermPos cursor;
    vterm_state_get_cursorpos(vterm_obtain_state(t.term), &cursor);
    require(cursor.row == 0 && cursor.col == 4, "cursor restored after alternate screen");
    require(t.visible, "cursor show");
}

void scrollRegion()
{
    Terminal t;
    for (int row = 0; row < 6; ++row) {
        t.feed("\x1b[" + std::to_string(row + 1) + ";1H" + std::string(1, 'A' + row));
    }
    t.feed("\x1b[2;5r\x1b[5;1H\n", true);
    require(t.cell(0, 0).chars[0] == 'A', "scroll region preserves header");
    require(t.cell(1, 0).chars[0] == 'C' && t.cell(3, 0).chars[0] == 'E', "scroll region moves rows");
    require(t.cell(4, 0).chars[0] == 0, "scroll region clears last row");
    require(t.cell(5, 0).chars[0] == 'F', "scroll region preserves footer");
}

void editingAndReports()
{
    Terminal t;
    t.feed("abcdef\x1b[1;3H\x1b[2P", true);
    require(t.cell(0, 2).chars[0] == 'e' && t.cell(0, 3).chars[0] == 'f', "delete characters");
    t.feed("\x1b[1;2H\x1b[2@", true);
    require(t.cell(0, 1).chars[0] == 0 && t.cell(0, 3).chars[0] == 'b', "insert characters");
    t.feed("\x1b[3;4H\x1b[6n", true);
    require(t.output == "\x1b[3;4R", "cursor report reaches terminal output callback");
    t.feed("\x1b[2J\x1b[H");
    require(t.cell(0, 0).chars[0] == 0 && t.cell(0, 3).chars[0] == 0, "erase display");
}

void keyboardModes()
{
    Terminal t;
    vterm_keyboard_unichar(t.term, 'c', VTERM_MOD_CTRL);
    vterm_keyboard_key(t.term, VTERM_KEY_TAB, VTERM_MOD_NONE);
    vterm_keyboard_key(t.term, VTERM_KEY_BACKSPACE, VTERM_MOD_NONE);
    vterm_keyboard_key(t.term, VTERM_KEY_ESCAPE, VTERM_MOD_NONE);
    vterm_keyboard_key(t.term, VTERM_KEY_UP, VTERM_MOD_NONE);
    require(t.output == std::string("\x03\t\x7f\x1b\x1b[A"), "control/basic terminal keys");
    t.output.clear();
    t.feed("\x1b[?1h");
    vterm_keyboard_key(t.term, VTERM_KEY_UP, VTERM_MOD_NONE);
    vterm_keyboard_key(t.term, VTERM_KEY_TAB, VTERM_MOD_SHIFT);
    vterm_keyboard_key(t.term, static_cast<VTermKey>(VTERM_KEY_FUNCTION(5)), VTERM_MOD_NONE);
    require(t.output == "\x1bOA\x1b[Z\x1b[15~", "application cursor/shift tab/function key");
}

void terminalWordEditing()
{
    using fallout::nixboy::TerminalEditingKey;
    using fallout::nixboy::terminalEditingSequence;
    for (const auto& wordModifiers : { std::pair<bool, bool> { true, false }, { false, true }, { true, true } }) {
        require(terminalEditingSequence(TerminalEditingKey::Left, wordModifiers.first, wordModifiers.second, false) == "\x1b"
                                                                                                                       "b",
            "modified left uses built-in backward-word");
        require(terminalEditingSequence(TerminalEditingKey::Right, wordModifiers.first, wordModifiers.second, false) == "\x1b"
                                                                                                                        "f",
            "modified right uses built-in forward-word");
    }
    require(terminalEditingSequence(TerminalEditingKey::Left, false, true, true) == "\x1b"
                                                                                    "b",
        "delivered Win+Alt+Left moves a word");
    require(terminalEditingSequence(TerminalEditingKey::Right, false, true, true) == "\x1b"
                                                                                     "f",
        "delivered Win+Alt+Right moves a word");
    require(terminalEditingSequence(TerminalEditingKey::Backspace, true, false, false) == "\x17", "Ctrl+Backspace erases a whitespace-delimited word");
    require(terminalEditingSequence(TerminalEditingKey::Backspace, false, true, false) == "\x1b\x7f", "Alt+Backspace erases a Readline word");
    require(terminalEditingSequence(TerminalEditingKey::Delete, true, false, false) == "\x1b"
                                                                                       "d",
        "Ctrl+Delete erases the next word");
    require(terminalEditingSequence(TerminalEditingKey::Delete, false, true, false) == "\x1b"
                                                                                       "d",
        "Alt+Delete erases the next word");
    for (auto key : { TerminalEditingKey::Left, TerminalEditingKey::Right, TerminalEditingKey::Backspace, TerminalEditingKey::Delete }) {
        require(terminalEditingSequence(key, false, false, false).empty(), "plain navigation retains libvterm mode handling");
        require(terminalEditingSequence(key, false, false, true).empty(), "GUI alone has no terminal editing override");
    }
    require(terminalEditingSequence(TerminalEditingKey::None, true, true, false).empty(), "unmapped keys retain ordinary forwarding");
    require(terminalEditingSequence(TerminalEditingKey::Backspace, true, true, false).empty(), "ambiguous Ctrl+Alt+Backspace remains unassigned");
    require(terminalEditingSequence(TerminalEditingKey::Delete, true, true, false).empty(), "ambiguous Ctrl+Alt+Delete remains unassigned");
    require(terminalEditingSequence(TerminalEditingKey::Backspace, false, true, true).empty(), "GUI override is restricted to word arrows");

    Terminal t;
    // These printable Ctrl chords already use the C0 path in handleKey. Check
    // the actual output callback, including libvterm's special Ctrl-I/J/M cases.
    for (char key : std::string("acdek luwyijm")) {
        if (key != ' ') vterm_keyboard_unichar(t.term, key & 0x1f, VTERM_MOD_NONE);
    }
    require(t.output == "\x01\x03\x04\x05\x0b\x0c\x15\x17\x19\x09\x0a\x0d", "basic Emacs controls, interrupt, EOF, completion and Enter remain C0 bytes");
}

// Print production-emitted bytes for the optional real Bash/PTY smoke probe.
// This avoids maintaining a second byte-encoding implementation in the probe.
void editingProbeData()
{
    using fallout::nixboy::TerminalEditingKey;
    using fallout::nixboy::terminalEditingSequence;
    std::vector<std::pair<std::string, std::string>> sequences;
    const auto addEditing = [&](const char* name, TerminalEditingKey key, bool ctrl, bool alt, bool gui = false) {
        sequences.emplace_back(name, terminalEditingSequence(key, ctrl, alt, gui));
    };
    addEditing("CtrlLeft", TerminalEditingKey::Left, true, false);
    addEditing("CtrlRight", TerminalEditingKey::Right, true, false);
    addEditing("AltLeft", TerminalEditingKey::Left, false, true);
    addEditing("AltRight", TerminalEditingKey::Right, false, true);
    addEditing("WinAltLeft", TerminalEditingKey::Left, false, true, true);
    addEditing("WinAltRight", TerminalEditingKey::Right, false, true, true);
    addEditing("CtrlBackspace", TerminalEditingKey::Backspace, true, false);
    addEditing("AltBackspace", TerminalEditingKey::Backspace, false, true);
    addEditing("CtrlDelete", TerminalEditingKey::Delete, true, false);
    addEditing("AltDelete", TerminalEditingKey::Delete, false, true);
    for (char key : std::string("acdekluwy")) {
        Terminal t;
        vterm_keyboard_unichar(t.term, key & 0x1f, VTERM_MOD_NONE);
        sequences.emplace_back(std::string("Ctrl") + static_cast<char>(key - 'a' + 'A'), t.output);
    }
    for (const auto& key : { std::pair<const char*, VTermKey> { "Left", VTERM_KEY_LEFT }, { "Right", VTERM_KEY_RIGHT },
             { "Home", VTERM_KEY_HOME }, { "End", VTERM_KEY_END }, { "Delete", VTERM_KEY_DEL },
             { "Backspace", VTERM_KEY_BACKSPACE }, { "Tab", VTERM_KEY_TAB } }) {
        Terminal t;
        vterm_keyboard_key(t.term, key.second, VTERM_MOD_NONE);
        sequences.emplace_back(key.first, t.output);
    }
    constexpr char hex[] = "0123456789abcdef";
    for (const auto& sequence : sequences) {
        std::cout << sequence.first << ' ';
        for (unsigned char byte : sequence.second)
            std::cout << hex[byte >> 4] << hex[byte & 15];
        std::cout << '\n';
    }
}

void generatedCharacterTables()
{
    Terminal t;
    t.feed("\x1b(0q\x1b(A#", true);
    require(t.cell(0, 0).chars[0] == 0x2500, "DEC drawing table generation");
    require(t.cell(0, 1).chars[0] == 0xA3, "UK table generation");
}

void textOnlyInputAfterTerminalKeys()
{
    fallout::nixboy::TerminalTextInput input;
    constexpr uint32_t enter = 40;
    constexpr uint32_t escape = 41;
    constexpr uint32_t controlC = 6;
    constexpr uint32_t keypad0 = 98;
    constexpr uint32_t keypadPlus = 87;
    constexpr uint32_t altShift1 = 30;

    // Match the in-game failure: synthetic typing supplies text events only,
    // while Return supplies physical keydown/keyup events between commands.
    std::string sent;
    for (const char* command : { "printf first", "printf second", "printf third" }) {
        for (const char* ch = command; *ch; ++ch) {
            if (input.acceptText(std::string_view(ch, 1))) sent.push_back(*ch);
        }
        input.keyDown(enter, '\r', '\n');
        sent.push_back('\r');
        input.keyUp(enter);
    }
    require(sent == "printf first\rprintf second\rprintf third\r", "text-only commands survive Return releases");

    input.keyDown(enter, '\r', '\n');
    require(!input.acceptText("\r"), "forwarded Return duplicate suppressed");
    require(input.acceptText("\r"), "duplicate suppression consumed once");
    input.keyDown(enter, '\r', '\n');
    require(input.acceptText("next command"), "Return cannot suppress unrelated synthetic text without keyup");

    input.keyDown(controlC, 'c', '\x03');
    require(!input.acceptText("c"), "forwarded control chord duplicate suppressed");
    input.keyDown(controlC, 'c', '\x03');
    require(!input.acceptText("\x03"), "forwarded control byte duplicate suppressed");
    input.keyDown(controlC, 'c', '\x03');
    input.keyUp(escape);
    require(!input.acceptText("c"), "unrelated keyup preserves active chord suppression");
    input.keyDown(controlC, 'c', '\x03');
    input.keyUp(controlC);
    require(input.acceptText("c"), "released control chord permits same synthetic character");

    input.keyDown(escape, '\x1b');
    require(!input.acceptText("\x1b"), "forwarded Escape duplicate suppressed");
    input.keyDown(escape, '\x1b');
    require(input.acceptText("\xe4\xb8\xad"), "held special key cannot suppress UTF-8 IME commit");
    input.keyDown(controlC, 'c', '\x03');
    require(input.acceptText("compose"), "whole IME text cannot match a duplicate prefix");
    input.keyDown(altShift1, '1', '\0', true);
    require(!input.acceptText("!"), "shifted printable chord duplicate suppressed");
    input.keyDown(altShift1, '1', '\0', true);
    input.keyUp(altShift1);
    require(input.acceptText("!"), "released shifted chord permits synthetic punctuation");

    input.keyDown(keypad0, '0');
    require(!input.acceptText("0"), "keypad numeric duplicate suppressed");
    input.keyDown(keypadPlus, '+');
    require(!input.acceptText("+"), "keypad operator duplicate suppressed");
    input.keyDown(keypad0, '.', ',');
    require(!input.acceptText(","), "localized keypad decimal duplicate suppressed");
    input.keyDown(keypadPlus, '+');
    require(!input.acceptText("+"), "key repeat re-arms duplicate suppression");
    input.keyDown(keypadPlus, '+');
    input.keyDown(controlC);
    require(input.acceptText("+"), "subsequent ordinary keydown clears stale duplicate");
    for (const char* reason : { "focus loss", "queue overflow after retained keydown" }) {
        input.keyDown(controlC, 'c', '\x03');
        input.reset();
        require(input.acceptText("c"), reason);
    }
}

} // namespace

int main(int argc, char** argv)
{
    try {
        if (argc == 2 && std::string_view(argv[1]) == "--editing-probe-data") {
            editingProbeData();
            return EXIT_SUCCESS;
        }
        fragmentedTextAndAttributes();
        alternateScreenAndCursor();
        scrollRegion();
        editingAndReports();
        keyboardModes();
        terminalWordEditing();
        generatedCharacterTables();
        textOnlyInputAfterTerminalKeys();
        std::cout << "Nix-Boy VT regression tests passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
