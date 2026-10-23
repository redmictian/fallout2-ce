#include "nixboy_terminal.h"

#include <SDL.h>
#include <vterm.h>

#include <algorithm>
#include <deque>
#include <memory>
#include <mutex>
#include <utility>
#include <vector>

#include "color.h"
#include "game.h"
#include "game_mouse.h"
#include "input.h"
#include "kb.h"
#include "mouse.h"
#include "nixboy_terminal_input.h"
#include "svga.h"
#include "text_font.h"
#include "window_manager.h"

namespace fallout {
namespace {

    constexpr int kCloseEvent = 19901;
    constexpr unsigned int kStartupTimeoutMs = 15000;
    constexpr size_t kMaxInputEvents = 256;
    constexpr int kMargin = 12;

    // SDL may call filters from threads which push events. Only queue event copies
    // here; libvterm, the socket and all game state stay on the modal's UI thread.
    class TerminalInput {
    public:
        TerminalInput()
            : wasTextInput_(SDL_IsTextInputActive() == SDL_TRUE)
            , wasKeyboardDisabled_(keyboardIsDisabled() != 0)
        {
            SDL_GetEventFilter(&previousFilter_, &previousData_);
            keyboardReset();
            inputEventQueueReset();
            keyboardDisable();
            SDL_SetEventFilter(filter, this);
            SDL_StartTextInput();
        }

        ~TerminalInput()
        {
            SDL_SetEventFilter(previousFilter_, previousData_);
            if (!wasTextInput_) {
                SDL_StopTextInput();
            }
            keyboardReset();
            inputEventQueueReset();
            if (!wasKeyboardDisabled_) {
                keyboardEnable();
            }
            // The modal intercepts SDL_QUIT to close its Docker session first.
            // Redeliver it after restoring the engine's event handler: value 2
            // only ends gameplay and is cleared by gameReset.
            bool quit;
            {
                std::lock_guard<std::mutex> lock(mutex_);
                quit = quit_;
            }
            if (quit) {
                SDL_Event event = {};
                event.type = SDL_QUIT;
                SDL_PushEvent(&event);
            }
        }

        void take(std::deque<SDL_Event>& events, bool& quit, bool& overflow)
        {
            std::lock_guard<std::mutex> lock(mutex_);
            events.swap(events_);
            quit = quit_;
            overflow = overflow_;
            overflow_ = false;
        }

    private:
        static int SDLCALL filter(void* data, SDL_Event* event)
        {
            auto* self = static_cast<TerminalInput*>(data);
            if (self->previousFilter_ != nullptr
                && !self->previousFilter_(self->previousData_, event)) {
                return 0;
            }
            if (event->type == SDL_QUIT) {
                std::lock_guard<std::mutex> lock(self->mutex_);
                self->quit_ = true;
                return 0;
            }
            const bool focusLost = event->type == SDL_WINDOWEVENT
                && event->window.event == SDL_WINDOWEVENT_FOCUS_LOST;
            if (event->type == SDL_KEYDOWN || event->type == SDL_KEYUP
                || event->type == SDL_TEXTINPUT || focusLost) {
                std::lock_guard<std::mutex> lock(self->mutex_);
                // Reserve local close even if the bounded input queue is full.
                if (event->type == SDL_KEYDOWN && event->key.keysym.sym == SDLK_F10) {
                    self->events_.clear();
                    self->events_.push_back(*event);
                } else if (self->events_.size() < kMaxInputEvents) {
                    self->events_.push_back(*event);
                } else {
                    self->overflow_ = true;
                }
                // Keep focus changes in order with terminal keys, while still
                // allowing the engine to pause audio/update its focus state.
                return focusLost ? 1 : 0;
            }
            return 1;
        }

        bool wasTextInput_;
        bool wasKeyboardDisabled_;
        SDL_EventFilter previousFilter_ = nullptr;
        void* previousData_ = nullptr;
        std::mutex mutex_;
        std::deque<SDL_Event> events_;
        bool quit_ = false;
        bool overflow_ = false;
    };

    struct ModalState {
        int font = fontGetCurrent();
        int cursor = gameMouseGetCursor();
        bool hidden = cursorIsHidden();
        int window = -1;

        ~ModalState()
        {
            if (window != -1) {
                windowDestroy(window);
            }
            fontSetCurrent(font);
            gameMouseSetCursor(cursor);
            if (hidden) {
                mouseHideCursor();
            } else {
                mouseShowCursor();
            }
        }
    };

    struct TerminalState {
        nixboy::Client client;
        std::string status = "Connecting to local nix-boy service...";
        bool ready = false;
        bool exited = false;
        bool cursorVisible = true;
        bool dirty = true;
        bool training = false;
        nixboy::TerminalTextInput textInput;

        static void output(const char* bytes, size_t length, void* user)
        {
            auto* self = static_cast<TerminalState*>(user);
            if (self->ready && !self->client.sendInput(std::string(bytes, length))) {
                self->status = "Terminal input queue full or disconnected. Wait, or close with F10.";
                self->dirty = true;
            }
        }

        static int damage(VTermRect, void* user)
        {
            static_cast<TerminalState*>(user)->dirty = true;
            return 1;
        }

        static int cursor(VTermPos, VTermPos, int visible, void* user)
        {
            auto* self = static_cast<TerminalState*>(user);
            self->cursorVisible = visible != 0;
            self->dirty = true;
            return 1;
        }

        static int property(VTermProp prop, VTermValue* value, void* user)
        {
            auto* self = static_cast<TerminalState*>(user);
            if (prop == VTERM_PROP_CURSORVISIBLE) {
                self->cursorVisible = value->boolean != 0;
            }
            self->dirty = true;
            return 1;
        }
    };

    VTermModifier modifiers(SDL_Keymod mod)
    {
        int result = VTERM_MOD_NONE;
        if (mod & KMOD_SHIFT) result |= VTERM_MOD_SHIFT;
        if (mod & KMOD_ALT) result |= VTERM_MOD_ALT;
        if (mod & KMOD_CTRL) result |= VTERM_MOD_CTRL;
        return static_cast<VTermModifier>(result);
    }

    VTermKey terminalKey(SDL_Keycode key)
    {
        switch (key) {
        case SDLK_RETURN:
            return VTERM_KEY_ENTER;
        case SDLK_TAB:
            return VTERM_KEY_TAB;
        case SDLK_BACKSPACE:
            return VTERM_KEY_BACKSPACE;
        case SDLK_ESCAPE:
            return VTERM_KEY_ESCAPE;
        case SDLK_UP:
            return VTERM_KEY_UP;
        case SDLK_DOWN:
            return VTERM_KEY_DOWN;
        case SDLK_LEFT:
            return VTERM_KEY_LEFT;
        case SDLK_RIGHT:
            return VTERM_KEY_RIGHT;
        case SDLK_INSERT:
            return VTERM_KEY_INS;
        case SDLK_DELETE:
            return VTERM_KEY_DEL;
        case SDLK_HOME:
            return VTERM_KEY_HOME;
        case SDLK_END:
            return VTERM_KEY_END;
        case SDLK_PAGEUP:
            return VTERM_KEY_PAGEUP;
        case SDLK_PAGEDOWN:
            return VTERM_KEY_PAGEDOWN;
        case SDLK_KP_ENTER:
            return VTERM_KEY_KP_ENTER;
        case SDLK_KP_0:
            return VTERM_KEY_KP_0;
        case SDLK_KP_1:
            return VTERM_KEY_KP_1;
        case SDLK_KP_2:
            return VTERM_KEY_KP_2;
        case SDLK_KP_3:
            return VTERM_KEY_KP_3;
        case SDLK_KP_4:
            return VTERM_KEY_KP_4;
        case SDLK_KP_5:
            return VTERM_KEY_KP_5;
        case SDLK_KP_6:
            return VTERM_KEY_KP_6;
        case SDLK_KP_7:
            return VTERM_KEY_KP_7;
        case SDLK_KP_8:
            return VTERM_KEY_KP_8;
        case SDLK_KP_9:
            return VTERM_KEY_KP_9;
        case SDLK_KP_MULTIPLY:
            return VTERM_KEY_KP_MULT;
        case SDLK_KP_PLUS:
            return VTERM_KEY_KP_PLUS;
        case SDLK_KP_COMMA:
            return VTERM_KEY_KP_COMMA;
        case SDLK_KP_MINUS:
            return VTERM_KEY_KP_MINUS;
        case SDLK_KP_PERIOD:
            return VTERM_KEY_KP_PERIOD;
        case SDLK_KP_DIVIDE:
            return VTERM_KEY_KP_DIVIDE;
        case SDLK_KP_EQUALS:
            return VTERM_KEY_KP_EQUAL;
        default:
            if (key >= SDLK_F1 && key <= SDLK_F12) {
                return static_cast<VTermKey>(VTERM_KEY_FUNCTION(key - SDLK_F1 + 1));
            }
            return VTERM_KEY_NONE;
        }
    }

    void handleKey(VTerm* term, TerminalState& state, const SDL_KeyboardEvent& event)
    {
        const SDL_Keycode key = event.keysym.sym;
        const auto mod = static_cast<SDL_Keymod>(event.keysym.mod);
        VTermKey special = terminalKey(key);
        state.textInput.keyDown(event.keysym.scancode);
        if (special != VTERM_KEY_NONE) {
            char duplicate = '\0';
            char alternate = '\0';
            switch (key) {
            case SDLK_RETURN:
            case SDLK_KP_ENTER:
                duplicate = '\r';
                alternate = '\n';
                break;
            case SDLK_TAB:
                duplicate = '\t';
                break;
            case SDLK_BACKSPACE:
                duplicate = '\b';
                alternate = '\x7f';
                break;
            case SDLK_ESCAPE:
                duplicate = '\x1b';
                break;
            case SDLK_KP_0:
                duplicate = '0';
                break;
            case SDLK_KP_1:
                duplicate = '1';
                break;
            case SDLK_KP_2:
                duplicate = '2';
                break;
            case SDLK_KP_3:
                duplicate = '3';
                break;
            case SDLK_KP_4:
                duplicate = '4';
                break;
            case SDLK_KP_5:
                duplicate = '5';
                break;
            case SDLK_KP_6:
                duplicate = '6';
                break;
            case SDLK_KP_7:
                duplicate = '7';
                break;
            case SDLK_KP_8:
                duplicate = '8';
                break;
            case SDLK_KP_9:
                duplicate = '9';
                break;
            case SDLK_KP_MULTIPLY:
                duplicate = '*';
                break;
            case SDLK_KP_PLUS:
                duplicate = '+';
                break;
            case SDLK_KP_COMMA:
                duplicate = ',';
                break;
            case SDLK_KP_MINUS:
                duplicate = '-';
                break;
            case SDLK_KP_PERIOD:
                duplicate = '.';
                alternate = ',';
                break;
            case SDLK_KP_DIVIDE:
                duplicate = '/';
                break;
            case SDLK_KP_EQUALS:
                duplicate = '=';
                break;
            default:
                break;
            }
            state.textInput.keyDown(event.keysym.scancode, duplicate, alternate);
            vterm_keyboard_key(term, special, modifiers(mod));
            return;
        }
        // SDL_TEXTINPUT supplies text in the user's keyboard layout, including
        // non-ASCII/IME text. Ctrl and left Alt chords arrive as keydown instead.
        // Right Alt / MODE is left to SDL's AltGr text composition.
        if ((mod & (KMOD_CTRL | KMOD_LALT)) && !(mod & (KMOD_RALT | KMOD_MODE))
            && key >= 32 && key <= 126) {
            uint32_t ch = static_cast<uint32_t>(key);
            char duplicate = static_cast<char>(key);
            if (key >= SDLK_a && key <= SDLK_z && ((mod & KMOD_SHIFT) != 0) != ((mod & KMOD_CAPS) != 0)) {
                duplicate -= 'a' - 'A';
            }
            int terminalMods = modifiers(mod);
            // Preserve conventional shell control bytes for Ctrl+I/J/M/[ as well
            // as the other letters. libvterm deliberately encodes these distinct
            // chords with CSI-u by default; ordinary Linux shells expect C0 here.
            if ((mod & KMOD_CTRL) && ((key >= SDLK_a && key <= SDLK_z) || key == SDLK_LEFTBRACKET || key == SDLK_BACKSLASH || key == SDLK_RIGHTBRACKET || key == SDLK_CARET || key == SDLK_UNDERSCORE || key == SDLK_SPACE)) {
                ch &= 0x1F;
                terminalMods &= ~(VTERM_MOD_CTRL | VTERM_MOD_SHIFT);
            } else if ((mod & KMOD_CTRL) && (mod & KMOD_SHIFT) && key == SDLK_6) {
                ch = 30;
                duplicate = '^';
                terminalMods &= ~(VTERM_MOD_CTRL | VTERM_MOD_SHIFT);
            } else if ((mod & KMOD_CTRL) && (mod & KMOD_SHIFT) && key == SDLK_MINUS) {
                ch = 31;
                duplicate = '_';
                terminalMods &= ~(VTERM_MOD_CTRL | VTERM_MOD_SHIFT);
            } else if ((mod & KMOD_SHIFT) && key >= SDLK_a && key <= SDLK_z) {
                ch -= 'a' - 'A';
            }
            state.textInput.keyDown(event.keysym.scancode, duplicate, static_cast<char>(ch), true);
            vterm_keyboard_unichar(term, ch, static_cast<VTermModifier>(terminalMods));
        }
    }

    unsigned char paletteColor(VTermScreen* screen, VTermColor color)
    {
        vterm_screen_convert_color_to_rgb(screen, &color);
        return _colorTable[((color.rgb.red >> 3) << 10)
            | ((color.rgb.green >> 3) << 5) | (color.rgb.blue >> 3)];
    }

    char legacyGlyph(uint32_t codepoint)
    {
        if (codepoint == 0) return ' ';
        if (codepoint >= 32 && codepoint <= 126) return static_cast<char>(codepoint);
        if (codepoint == 0x2500 || (codepoint >= 0x23BA && codepoint <= 0x23BD)) return '-';
        if (codepoint == 0x2502) return '|';
        if (codepoint >= 0x250C && codepoint <= 0x257F) return '+';
        return '?';
    }

    std::string plainStatus(const std::string& text)
    {
        std::string result;
        result.reserve(std::min<size_t>(text.size(), 180));
        for (unsigned char ch : text) {
            if (result.size() == 180) break;
            result.push_back(ch >= 32 && ch <= 126 ? static_cast<char>(ch) : '?');
        }
        return result;
    }

    void renderTerminal(int window, int width, int height, int cellWidth, int cellHeight,
        int rows, int cols, VTerm* term, VTermScreen* screen, const TerminalState& state)
    {
        unsigned char* buffer = windowGetBuffer(window);
        const int top = 3 * cellHeight;
        VTermPos cursor;
        vterm_state_get_cursorpos(vterm_obtain_state(term), &cursor);
        for (int row = 0; row < rows; row++) {
            for (int col = 0; col < cols; col++) {
                VTermScreenCell cell = {};
                vterm_screen_get_cell(screen, { row, col }, &cell);
                unsigned char foreground = paletteColor(screen, cell.fg);
                unsigned char background = paletteColor(screen, cell.bg);
                bool reverse = cell.attrs.reverse != 0;
                if (state.ready && state.cursorVisible && cursor.row == row && cursor.col == col) {
                    reverse = !reverse;
                }
                if (reverse) std::swap(foreground, background);
                const int x = kMargin + col * cellWidth;
                const int y = top + row * cellHeight;
                windowFill(window, x, y, cellWidth, cellHeight, background);
                if (cell.chars[0] == UINT32_MAX || cell.attrs.conceal) continue;
                const char glyph[2] = { legacyGlyph(cell.chars[0]), '\0' };
                const int inset = std::max(0, (cellWidth - fontGetCharacterWidth(glyph[0])) / 2);
                fontDrawText(buffer + y * width + x + inset, glyph,
                    cellWidth - inset, width, foreground);
                if (cell.attrs.underline) {
                    windowDrawLine(window, x, y + cellHeight - 2,
                        x + cellWidth - 1, y + cellHeight - 2, foreground);
                }
            }
        }
        const int textColor = _colorTable[992];
        const int statusY = height - 3 * cellHeight;
        windowFill(window, kMargin, statusY, width - 2 * kMargin, 3 * cellHeight - 4, _colorTable[0]);
        const std::string status = plainStatus(state.status);
        fontDrawText(buffer + statusY * width + kMargin, status.c_str(), width - 2 * kMargin, width, textColor);
        fontDrawText(buffer + (statusY + cellHeight) * width + kMargin,
            state.training ? "F5 Check / F6 Hub / F7 Reset / F8 Abandon / F10 Close (keeps exercise)"
                           : "Esc/Ctrl+C go to Linux. F10 closes. ASCII font: other glyphs appear as ?.",
            width - 2 * kMargin, width, textColor);
        windowRefresh(window);
    }

} // namespace

bool nixboyTerminalOpen(uint16_t port, const std::string& token,
    const nixboy::Context& context,
    const std::function<void(const std::string&, int32_t)>& onResult,
    const NixboyTrainingCallbacks& training)
{
    ModalState modal;
    fontSetCurrent(0);
    gameMouseSetCursor(MOUSE_CURSOR_ARROW);
    mouseShowCursor();

    int cellWidth = 1;
    for (int ch = 32; ch <= 126; ch++) {
        cellWidth = std::max(cellWidth, fontGetCharacterWidth(ch) + fontGetLetterSpacing());
    }
    const int cellHeight = fontGetLineHeight() + 1;
    if (cellHeight <= 1) return false;
    const int width = std::min(screenGetWidth(), 1280);
    const int height = std::min(screenGetHeight(), 900);
    const int cols = std::min(300, (width - 2 * kMargin) / cellWidth);
    const int rows = std::min(200, (height - 7 * cellHeight) / cellHeight);
    if (cols < 20 || rows < 10) return false;

    modal.window = windowCreate((screenGetWidth() - width) / 2,
        (screenGetHeight() - height) / 2, width, height, _colorTable[0], WINDOW_MODAL | WINDOW_MOVE_ON_TOP);
    if (modal.window == -1) return false;
    const char* closeTitle = "Close [F10]";
    if (_win_register_text_button(modal.window, width - fontGetStringWidth(closeTitle) - 2 * kMargin - 16,
            kMargin, -1, -1, -1, kCloseEvent, closeTitle, 0)
        == -1) return false;
    windowDrawText(modal.window, "nix-boy / Linux terminal", width / 2,
        kMargin, kMargin, _colorTable[992]);

    TerminalState state;
    state.training = training.enabled;
    std::unique_ptr<VTerm, decltype(&vterm_free)> terminal(vterm_new(rows, cols), vterm_free);
    if (!terminal) return false;
    VTerm* term = terminal.get();
    vterm_set_utf8(term, 1);
    vterm_output_set_callback(term, TerminalState::output, &state);
    VTermScreen* screen = vterm_obtain_screen(term);
    vterm_state_set_bold_highbright(vterm_obtain_state(term), 1);
    VTermScreenCallbacks callbacks = {};
    callbacks.damage = TerminalState::damage;
    callbacks.movecursor = TerminalState::cursor;
    callbacks.settermprop = TerminalState::property;
    vterm_screen_set_callbacks(screen, &callbacks, &state);
    vterm_screen_enable_altscreen(screen, 1);
    VTermColor foreground;
    VTermColor background;
    vterm_color_rgb(&foreground, 128, 220, 128);
    vterm_color_rgb(&background, 0, 0, 0);
    vterm_screen_set_default_colors(screen, &foreground, &background);
    vterm_screen_reset(screen, 1);
    TerminalInput input;
    renderTerminal(modal.window, width, height, cellWidth, cellHeight, rows, cols, term, screen, state);
    renderPresent();

    std::string error;
    bool opening = state.client.connect(port, token, error)
        && state.client.open(static_cast<uint16_t>(rows), static_cast<uint16_t>(cols), context, error, training.enabled,
            training.progress ? training.progress() : std::vector<nixboy::TrainingClaim> {});
    if (!opening) {
        state.status = "Cannot start nix-boy: " + error;
        state.dirty = true;
    } else {
        state.status = training.enabled ? "Opening training hub... (F10 closes)" : "Starting Linux container... (F10 cancels)";
    }
    const unsigned int start = getTicks();
    bool close = false;
    while (!close && _game_user_wants_to_quit == 0) {
        sharedFpsLimiter.mark();
        // Unlike inputGetInput, this loop does not block while focus is lost.
        // It can cancel startup and clean up the session on a window close.
        _GNW95_process_message();
        tickersExecute();
        _mouse_info();
        if (_win_check_all_buttons() == kCloseEvent) close = true;
        std::deque<SDL_Event> inputEvents;
        bool quit;
        bool overflow;
        input.take(inputEvents, quit, overflow);
        if (quit) {
            _game_user_wants_to_quit = 2;
            close = true;
        }
        if (overflow) {
            state.status = "Keyboard queue full; some input was discarded. F10 closes.";
            state.dirty = true;
        }

        std::vector<nixboy::Event> events;
        state.client.poll(events);
        for (const auto& event : events) {
            switch (event.type) {
            case nixboy::EventType::Ready:
                state.ready = true;
                opening = false;
                state.status = training.enabled ? "Training connected. F5 checks your work; F6 returns to the hub."
                                                : "Linux connected. Results apply through configured game bindings.";
                break;
            case nixboy::EventType::Output:
                vterm_input_write(term, event.text.data(), event.text.size());
                break;
            case nixboy::EventType::Result:
                // Process termination has a dedicated protocol message. A
                // shell helper must never impersonate it with a named result.
                if (!training.enabled && state.ready && !state.exited && event.name != "terminal_exit" && onResult) {
                    onResult(event.name, event.value);
                }
                break;
            case nixboy::EventType::TrainingReward: {
                if (!training.enabled || !state.ready || state.exited) break;
                bool accepted = training.reward && training.reward(event.skill, event.level, event.points, event.receipt);
                bool queued = state.client.acknowledgeTraining(event.skill, event.level, event.points, event.receipt, accepted);
                if (training.snapshot) queued = state.client.sendContext(training.snapshot()) && queued;
                state.status = !queued ? "Reward response could not be sent. Reopen to reconcile training progress."
                    : accepted         ? "Training reward recorded. Save your game to keep this progress."
                                       : "Training reward rejected: invalid, conflicting or progress limit reached.";
                break;
            }
            case nixboy::EventType::Exit:
                if (!state.exited) {
                    state.exited = true;
                    state.ready = false;
                    opening = false;
                    if (!training.enabled && onResult) onResult("terminal_exit", event.value);
                    state.status = "Linux session exited with code " + std::to_string(event.value) + ". F10 closes.";
                }
                break;
            case nixboy::EventType::Error:
                state.ready = false;
                opening = false;
                if (!state.exited) state.status = "nix-boy error: " + event.text;
                break;
            }
            state.dirty = true;
        }
        if (opening && getTicksSince(start) > kStartupTimeoutMs) {
            state.client.close();
            opening = false;
            state.status = "Linux startup timed out. Check the nix-boy service and Docker; F10 closes.";
            state.dirty = true;
        }
        for (const SDL_Event& event : inputEvents) {
            if (event.type == SDL_KEYDOWN && event.key.keysym.sym == SDLK_F10) {
                close = true;
                break;
            }
            if (!state.ready || close) continue;
            if (training.enabled && event.type == SDL_KEYDOWN
                && event.key.keysym.sym >= SDLK_F5 && event.key.keysym.sym <= SDLK_F8) {
                if (event.key.repeat == 0) {
                    auto action = static_cast<nixboy::TrainingAction>(event.key.keysym.sym - SDLK_F5 + 1);
                    if (!state.client.sendTrainingAction(action)) {
                        state.status = "Training action queue full or disconnected. Reopen to resume.";
                        state.dirty = true;
                    }
                }
                continue;
            }
            if (event.type == SDL_KEYDOWN) {
                handleKey(term, state, event.key);
            } else if (event.type == SDL_KEYUP) {
                state.textInput.keyUp(event.key.keysym.scancode);
            } else if (event.type == SDL_WINDOWEVENT) {
                state.textInput.reset();
            } else if (event.type == SDL_TEXTINPUT && state.textInput.acceptText(event.text.text)) {
                if (!state.client.sendInput(event.text.text)) {
                    state.status = "Terminal input queue full or disconnected. Wait, or close with F10.";
                    state.dirty = true;
                }
            }
        }
        // Overflow may have discarded the release for a retained keydown.
        if (overflow) state.textInput.reset();
        vterm_screen_flush_damage(screen);
        if (state.dirty) {
            renderTerminal(modal.window, width, height, cellWidth, cellHeight, rows, cols, term, screen, state);
            state.dirty = false;
        }
        renderPresent();
        sharedFpsLimiter.throttle();
    }
    state.client.close();
    return true;
}

} // namespace fallout
