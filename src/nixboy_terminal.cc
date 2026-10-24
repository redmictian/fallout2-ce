#include "nixboy_terminal.h"

#include <SDL.h>
#include <vterm.h>

#include <algorithm>
#include <array>
#include <deque>
#include <limits>
#include <memory>
#include <mutex>
#include <utility>
#include <vector>

#include "art.h"
#include "color.h"
#include "game.h"
#include "game_mouse.h"
#include "game_sound.h"
#include "input.h"
#include "kb.h"
#include "mouse.h"
#include "nixboy_terminal_font.h"
#include "nixboy_terminal_input.h"
#include "nixboy_terminal_view.h"
#include "nixboy_ui_art.h"
#include "svga.h"
#include "text_font.h"
#include "window_manager.h"

namespace fallout {
namespace {

    constexpr int kCloseEvent = 19901;
    constexpr unsigned int kStartupTimeoutMs = 15000;
    constexpr size_t kMaxInputEvents = 256;
    constexpr int kMargin = 22;

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
        FrmImage closeUp;
        FrmImage closeDown;

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
        std::string printedStatus;
        std::string notice;
        uint32_t noticeStarted = 0;
        bool hasOutput = false;
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
        // Readline word editing is more portable as Esc-b/f than modified
        // cursor CSI sequences. Keep AltGr and plain application-mode keys intact.
        if (!(mod & (KMOD_RALT | KMOD_MODE))) {
            nixboy::TerminalEditingKey editing = nixboy::TerminalEditingKey::None;
            switch (key) {
            case SDLK_LEFT:
                editing = nixboy::TerminalEditingKey::Left;
                break;
            case SDLK_RIGHT:
                editing = nixboy::TerminalEditingKey::Right;
                break;
            case SDLK_BACKSPACE:
                editing = nixboy::TerminalEditingKey::Backspace;
                break;
            case SDLK_DELETE:
                editing = nixboy::TerminalEditingKey::Delete;
                break;
            }
            const auto sequence = nixboy::terminalEditingSequence(editing,
                (mod & KMOD_CTRL) != 0, (mod & KMOD_LALT) != 0, (mod & KMOD_GUI) != 0);
            if (!sequence.empty()) {
                if (editing == nixboy::TerminalEditingKey::Backspace) {
                    state.textInput.keyDown(event.keysym.scancode, '\b', '\x7f');
                }
                if (!state.client.sendInput(std::string(sequence))) {
                    state.status = "Terminal input queue full or disconnected. Wait, or close with F10.";
                    state.dirty = true;
                }
                return;
            }
        }
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

    void publishStatus(VTerm* term, TerminalState& state)
    {
        if (state.status == state.printedStatus) return;
        state.printedStatus = state.status;
        // Live PTY frames may split UTF-8 and escape sequences. Display rare
        // asynchronous notices inside the glass without changing its parser,
        // cursor or application screen. Restore the covered cells on expiry.
        if (state.hasOutput && state.ready) {
            state.notice = "[nix-boy] " + plainStatus(state.status);
            state.noticeStarted = getTicks();
            state.dirty = true;
            return;
        }
        state.notice.clear();
        // Startup and ended-session messages can be printed into the terminal.
        // Cancel an unfinished sequence only after the live stream has ended.
        const std::string message = "\x18\x1b[0m\r\n[nix-boy] " + plainStatus(state.status) + "\r\n";
        vterm_input_write(term, message.data(), message.size());
        state.dirty = true;
    }

    struct TerminalPresentation {
        nixboy::TerminalViewLayout layout;
        nixboy::TerminalCrt crt;
        std::vector<unsigned char> source;
        std::vector<uint32_t> rgb;
        std::array<unsigned char, 256> luminance;
        SDL_Texture* texture = nullptr;
        uint32_t generation = std::numeric_limits<uint32_t>::max();
        uint32_t previousFrame = 0;
        bool retro = false;
        bool textureFailed = false;
        bool sourceChanged = true;
        bool hasFrame = false;

        ~TerminalPresentation()
        {
            if (texture != nullptr && generation == renderGetGeneration()) SDL_DestroyTexture(texture);
        }

        bool initialize(int cellWidth, int cellHeight, int chromeHeight, const NixboyTerminalOptions& options)
        {
            if (!nixboy::terminalViewLayout(screenGetWidth(), screenGetHeight(),
                    cellWidth, cellHeight, options.fullscreen, layout, chromeHeight)) {
                return false;
            }
            source.resize(static_cast<size_t>(layout.glassWidth) * layout.glassHeight);
            retro = options.retro && crt.initialize(layout.glassWidth, layout.glassHeight, options.crt);
            if (retro) rgb.resize(source.size());
            for (int index = 0; index < 256; index++) {
                const int red = _cmap[index * 3];
                const int green = _cmap[index * 3 + 1];
                const int blue = _cmap[index * 3 + 2];
                // ANSI hues retain useful brightness when converted to green;
                // saturated blue/red warnings must not disappear into the glass.
                const int peak = std::max({ red, green, blue });
                const int weighted = (red * 77 + green * 150 + blue * 29) / 256;
                luminance[index] = std::min(255, (peak * 3 + weighted) * 9 / 8);
            }
            return true;
        }

        void drawCursor(const SDL_Rect& glass)
        {
            int width;
            int height;
            int pitch;
            unsigned char transparent;
            const unsigned char* shape = mouseGetShape(&width, &height, &pitch, &transparent);
            if (shape == nullptr || gSdlSurface == nullptr || gSdlSurface->format->palette == nullptr) return;
            Rect mouse;
            mouseGetRect(&mouse);
            const int left = std::max(0, glass.x - mouse.left);
            const int right = std::min(width, glass.x + glass.w - mouse.left);
            const int top = std::max(0, glass.y - mouse.top);
            const int bottom = std::min(height, glass.y + glass.h - mouse.top);
            if (left >= right || top >= bottom) return;
            Uint8 red;
            Uint8 green;
            Uint8 blue;
            Uint8 alpha;
            SDL_BlendMode blend;
            SDL_GetRenderDrawColor(gSdlRenderer, &red, &green, &blue, &alpha);
            SDL_GetRenderDrawBlendMode(gSdlRenderer, &blend);
            SDL_SetRenderDrawBlendMode(gSdlRenderer, SDL_BLENDMODE_NONE);
            const SDL_Color* colors = gSdlSurface->format->palette->colors;
            for (int y = top; y < bottom; y++) {
                for (int x = left; x < right;) {
                    const unsigned char color = shape[y * pitch + x];
                    const int start = x++;
                    while (x < right && shape[y * pitch + x] == color)
                        x++;
                    if (color == transparent) continue;
                    SDL_SetRenderDrawColor(gSdlRenderer, colors[color].r, colors[color].g, colors[color].b, 255);
                    SDL_RenderDrawLine(gSdlRenderer, mouse.left + start, mouse.top + y,
                        mouse.left + x - 1, mouse.top + y);
                }
            }
            SDL_SetRenderDrawColor(gSdlRenderer, red, green, blue, alpha);
            SDL_SetRenderDrawBlendMode(gSdlRenderer, blend);
        }

        static void overlay(void* context)
        {
            auto& self = *static_cast<TerminalPresentation*>(context);
            if (!self.retro || gSdlRenderer == nullptr) return;
            if (self.generation != renderGetGeneration()) {
                // SDL destroyed the old texture with its renderer. Never call
                // DestroyTexture on that stale pointer, even if addresses repeat.
                self.texture = nullptr;
                self.generation = renderGetGeneration();
                self.textureFailed = false;
                self.hasFrame = false;
            }
            if (self.textureFailed) return; // The indexed grid is the fallback.
            const auto& layout = self.layout;
            if (self.texture == nullptr) {
                self.texture = SDL_CreateTexture(gSdlRenderer, SDL_PIXELFORMAT_RGB888,
                    SDL_TEXTUREACCESS_STREAMING, layout.glassWidth, layout.glassHeight);
                if (self.texture == nullptr) {
                    self.textureFailed = true;
                    return;
                }
                SDL_SetTextureBlendMode(self.texture, SDL_BLENDMODE_NONE);
            }
            const uint32_t now = getTicks();
            if (!self.hasFrame || self.sourceChanged || now - self.previousFrame >= 33) {
                self.crt.render(self.rgb.data(), layout.glassWidth, now);
                if (SDL_UpdateTexture(self.texture, nullptr, self.rgb.data(), layout.glassWidth * sizeof(uint32_t)) != 0) {
                    SDL_DestroyTexture(self.texture);
                    self.texture = nullptr;
                    self.textureFailed = true;
                    return;
                }
                self.previousFrame = now;
                self.hasFrame = true;
                self.sourceChanged = false;
            }
            const SDL_Rect destination = { layout.x + layout.glassX, layout.y + layout.glassY,
                layout.glassWidth, layout.glassHeight };
            if (SDL_RenderCopy(gSdlRenderer, self.texture, nullptr, &destination) != 0) {
                self.textureFailed = true;
                return;
            }
            // Only the cursor's overlap with the glass was covered. Its other
            // pixels remain in the normal game canvas, with no double cursor.
            self.drawCursor(destination);
        }
    };

    void renderTerminal(int window, TerminalPresentation& presentation, int cellWidth, int cellHeight,
        VTerm* term, VTermScreen* screen, const TerminalState& state)
    {
        const int previousFont = fontGetCurrent();
        fontSetCurrent(101);
        const auto& layout = presentation.layout;
        const int pitch = layout.glassWidth;
        unsigned char* source = presentation.source.data();
        std::fill(presentation.source.begin(), presentation.source.end(), _colorTable[0]);
        VTermPos cursor;
        vterm_state_get_cursorpos(vterm_obtain_state(term), &cursor);
        for (int row = 0; row < layout.rows; row++) {
            for (int col = 0; col < layout.cols; col++) {
                VTermScreenCell cell = {};
                vterm_screen_get_cell(screen, { row, col }, &cell);
                unsigned char foreground = paletteColor(screen, cell.fg);
                unsigned char background = paletteColor(screen, cell.bg);
                bool reverse = cell.attrs.reverse != 0;
                if (state.ready && state.cursorVisible && cursor.row == row && cursor.col == col) {
                    reverse = !reverse;
                }
                if (reverse) std::swap(foreground, background);
                const int x = layout.textX + col * cellWidth;
                const int y = layout.textY + row * cellHeight;
                for (int offset = 0; offset < cellHeight; offset++) {
                    std::fill_n(source + (y + offset) * pitch + x, cellWidth, background);
                }
                if (cell.chars[0] == UINT32_MAX || cell.attrs.conceal) continue;
                nixboy::drawTerminalGlyph(source, pitch, x, y, legacyGlyph(cell.chars[0]), foreground, cellWidth, cellHeight);
                if (cell.attrs.underline) {
                    std::fill_n(source + (y + cellHeight - 2) * pitch + x, cellWidth, foreground);
                }
            }
        }
        if (!state.notice.empty()) {
            const int noticeRows = std::min(3, static_cast<int>((state.notice.size() + layout.cols - 1) / layout.cols));
            const int top = layout.textY + (layout.rows - noticeRows) * cellHeight;
            for (int y = 0; y < noticeRows * cellHeight; y++) {
                std::fill_n(source + (top + y) * pitch + layout.textX, layout.cols * cellWidth, _colorTable[0]);
            }
            const size_t count = std::min(state.notice.size(), static_cast<size_t>(noticeRows * layout.cols));
            for (size_t index = 0; index < count; index++) {
                nixboy::drawTerminalGlyph(source, pitch,
                    layout.textX + (index % layout.cols) * cellWidth,
                    top + (index / layout.cols) * cellHeight, state.notice[index], _colorTable[992], cellWidth, cellHeight);
            }
        }
        unsigned char* buffer = windowGetBuffer(window);
        for (int y = 0; y < layout.glassHeight; y++) {
            std::copy_n(source + y * pitch, pitch,
                buffer + (layout.glassY + y) * layout.width + layout.glassX);
        }
        if (presentation.retro) {
            presentation.crt.update(source, presentation.luminance);
            presentation.sourceChanged = true;
        }
        fontSetCurrent(previousFont);
        windowRefresh(window);
    }

} // namespace

bool nixboyTerminalOpen(uint16_t port, const std::string& token,
    const nixboy::Context& context,
    const std::function<void(const std::string&, int32_t)>& onResult,
    const NixboyTrainingCallbacks& training,
    const NixboyTerminalOptions& options)
{
    ModalState modal;
    fontSetCurrent(101);
    if (fontGetCurrent() != 101) return false;
    gameMouseSetCursor(MOUSE_CURSOR_ARROW);
    mouseShowCursor();

    const int cellWidth = fontGetMonospacedCharacterWidth();
    const int cellHeight = fontGetLineHeight();
    const int chromeHeight = fontGetLineHeight() + 1;
    if (chromeHeight <= 1) return false;
    TerminalPresentation presentation;
    if (!presentation.initialize(cellWidth, cellHeight, chromeHeight, options)) return false;
    const auto& layout = presentation.layout;
    const int rows = layout.rows;
    const int cols = layout.cols;
    modal.window = windowCreate(layout.x, layout.y, layout.width, layout.height,
        _colorTable[0], WINDOW_MODAL | WINDOW_MOVE_ON_TOP);
    if (modal.window == -1) return false;
    nixboyDrawTerminalFrame(modal.window, layout.width, layout.height,
        layout.glassX, layout.glassY, layout.glassWidth, layout.glassHeight, layout.statusY);
    unsigned char* windowBuffer = windowGetBuffer(modal.window);
    const char* closeTitle = "CLOSE";
    const bool closeArt = modal.closeUp.lock(buildFid(OBJ_TYPE_INTERFACE, 8, 0, 0, 0))
        && modal.closeDown.lock(buildFid(OBJ_TYPE_INTERFACE, 9, 0, 0, 0));
    const int closeWidth = closeArt ? modal.closeUp.getWidth() : 16;
    const int closeHeight = closeArt ? modal.closeUp.getHeight() : 16;
    const int closeX = layout.width - kMargin - fontGetStringWidth(closeTitle) - closeWidth - 6;
    const int closeY = (layout.glassY - closeHeight) / 2;
    const int closeLabelY = (layout.glassY - fontGetLineHeight()) / 2;
    const int closeButton = buttonCreate(modal.window, closeX, closeY, closeWidth, closeHeight,
        -1, -1, -1, kCloseEvent, closeArt ? modal.closeUp.getData() : nullptr,
        closeArt ? modal.closeDown.getData() : nullptr, nullptr, BUTTON_FLAG_TRANSPARENT);
    if (closeButton == -1) return false;
    buttonSetCallbacks(closeButton, _gsound_red_butt_press, _gsound_red_butt_release);
    fontDrawText(windowBuffer + closeLabelY * layout.width
            + closeX + closeWidth + 6,
        closeTitle, fontGetStringWidth(closeTitle), layout.width, _colorTable[992]);

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
    publishStatus(term, state);
    const char* hints = state.training ? "F5 Check | F6 Hub | F7 Reset | F8 Abandon | F10 Close\r\n"
                                       : "Esc / Ctrl+C: Linux | F10 Close\r\n";
    vterm_input_write(term, hints, std::char_traits<char>::length(hints));
    TerminalInput input;
    renderTerminal(modal.window, presentation, cellWidth, cellHeight, term, screen, state);
    renderPresent(TerminalPresentation::overlay, &presentation);

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
    publishStatus(term, state);
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
                publishStatus(term, state);
                break;
            case nixboy::EventType::Output:
                state.hasOutput = true;
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
        publishStatus(term, state);
        if (!state.notice.empty() && getTicks() - state.noticeStarted >= 5000) {
            state.notice.clear();
            state.dirty = true;
        }
        vterm_screen_flush_damage(screen);
        if (state.dirty) {
            renderTerminal(modal.window, presentation, cellWidth, cellHeight, term, screen, state);
            state.dirty = false;
        }
        renderPresent(TerminalPresentation::overlay, &presentation);
        sharedFpsLimiter.throttle();
    }
    state.client.close();
    return true;
}

} // namespace fallout
