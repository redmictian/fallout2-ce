#include <vterm.h>

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

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

void generatedCharacterTables()
{
    Terminal t;
    t.feed("\x1b(0q\x1b(A#", true);
    require(t.cell(0, 0).chars[0] == 0x2500, "DEC drawing table generation");
    require(t.cell(0, 1).chars[0] == 0xA3, "UK table generation");
}

} // namespace

int main()
{
    try {
        fragmentedTextAndAttributes();
        alternateScreenAndCursor();
        scrollRegion();
        editingAndReports();
        keyboardModes();
        generatedCharacterTables();
        std::cout << "Nix-Boy VT regression tests passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
