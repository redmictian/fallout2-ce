#include "nixboy_wiki.h"

#if defined(FALLOUT_NIXBOY) && defined(_WIN32)
#include <SDL.h>
#include <SDL_syswm.h>
#include <WebView2.h>
#include <windows.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <filesystem>
#include <functional>
#include <memory>
#include <sstream>
#include <utility>
#include <vector>

#include "art.h"
#include "color.h"
#include "game.h"
#include "input.h"
#include "kb.h"
#include "mouse.h"
#include "nixboy_ui_art.h"
#include "svga.h"
#include "text_font.h"

namespace fallout {
namespace {

    constexpr wchar_t kWikiHost[] = L"nixboy-wiki.invalid";
    constexpr wchar_t kHomeUri[] = L"https://nixboy-wiki.invalid/index.html";
    constexpr wchar_t kPanelClass[] = L"NixboyWikiPanel";
    constexpr int kBack = 1;
    constexpr int kHome = 2;
    constexpr int kClose = 3;

    // Cache only our composed pixels. All source art and lettering come from
    // the installed game; no game assets are included with the mod.
    struct Image {
        int width = 0;
        int height = 0;
        std::vector<uint32_t> pixels;

        void assign(const unsigned char* indexed, int w, int h)
        {
            width = w;
            height = h;
            pixels.resize(w * h);
            for (int i = 0; i < w * h; ++i) {
                const int color = indexed[i] * 3;
                pixels[i] = (static_cast<uint32_t>(_cmap[color]) * 4 << 16)
                    | (static_cast<uint32_t>(_cmap[color + 1]) * 4 << 8)
                    | static_cast<uint32_t>(_cmap[color + 2]) * 4;
            }
        }

        void draw(HDC dc, const RECT& destination) const
        {
            if (pixels.empty()) return;
            BITMAPINFO info {};
            info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
            info.bmiHeader.biWidth = width;
            info.bmiHeader.biHeight = -height;
            info.bmiHeader.biPlanes = 1;
            info.bmiHeader.biBitCount = 32;
            info.bmiHeader.biCompression = BI_RGB;
            SetStretchBltMode(dc, COLORONCOLOR);
            StretchDIBits(dc, destination.left, destination.top,
                destination.right - destination.left, destination.bottom - destination.top,
                0, 0, width, height, pixels.data(), &info, DIB_RGB_COLORS, SRCCOPY);
        }
    };

    IID interfaceId(const wchar_t* text)
    {
        IID result {};
        CLSIDFromString(text, &result);
        return result;
    }

    template <typename Interface, typename... Args>
    class Callback final : public Interface {
    public:
        Callback(const wchar_t* id, std::function<HRESULT(Args...)> function)
            : id_(interfaceId(id))
            , function_(std::move(function))
        {
        }
        HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id, void** object) override
        {
            if (object == nullptr) return E_POINTER;
            *object = nullptr;
            if (!IsEqualIID(id, IID_IUnknown) && !IsEqualIID(id, id_)) return E_NOINTERFACE;
            *object = static_cast<Interface*>(this);
            AddRef();
            return S_OK;
        }
        ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }
        ULONG STDMETHODCALLTYPE Release() override
        {
            ULONG remaining = --references_;
            if (remaining == 0) delete this;
            return remaining;
        }
        HRESULT STDMETHODCALLTYPE Invoke(Args... args) override { return function_(args...); }

    private:
        std::atomic<ULONG> references_ { 1 };
        IID id_;
        std::function<HRESULT(Args...)> function_;
    };

    struct Browser {
        HWND parent = nullptr;
        HWND panel = nullptr;
        HWND back = nullptr;
        HWND home = nullptr;
        HWND close = nullptr;
        int x = 0;
        int y = 0;
        int width = 0;
        int height = 0;
        Image frame;
        std::array<std::array<Image, 2>, 3> buttons;
        ICoreWebView2Environment* environment = nullptr;
        ICoreWebView2Controller* controller = nullptr;
        ICoreWebView2* view = nullptr;
        ICoreWebView2_4* view4 = nullptr;
        std::wstring root;
        std::atomic<bool> closing { false };
        std::atomic<bool> quit { false };
        bool closed = false;
        bool ready = false;
        std::string error;

        ~Browser()
        {
            if (!closed && controller != nullptr) controller->Close();
            closed = true;
            if (panel != nullptr) DestroyWindow(panel);
            if (view4 != nullptr) view4->Release();
            if (view != nullptr) view->Release();
            if (controller != nullptr) controller->Release();
            if (environment != nullptr) environment->Release();
        }

        bool check(HRESULT result, const char* operation)
        {
            if (SUCCEEDED(result)) return true;
            std::ostringstream message;
            message << operation << " (0x" << std::hex << static_cast<unsigned long>(result) << ").";
            error = message.str();
            closing = true;
            return false;
        }

        bool compose(bool fullscreen)
        {
            const int screenWidth = screenGetWidth();
            const int screenHeight = screenGetHeight();
            width = fullscreen ? screenWidth : std::min(1280, screenWidth - std::max(32, screenWidth / 8));
            height = fullscreen ? screenHeight : std::min(900, screenHeight - std::max(32, screenHeight / 7));
            x = (screenWidth - width) / 2;
            y = (screenHeight - height) / 2;
            if (width < 480 || height < 240) {
                error = "The wiki needs a game screen of at least 640 by 480.";
                return false;
            }
            std::vector<unsigned char> indexed(width * height, _colorTable[0]);
            if (!nixboyDrawFrame(indexed.data(), width, height, 18, 56, width - 36, height - 74)) {
                error = "Cannot load the installed game's automap frame for the wiki.";
                return false;
            }
            frame.assign(indexed.data(), width, height);

            FrmImage up;
            FrmImage down;
            if (!up.lock(buildFid(OBJ_TYPE_INTERFACE, 221, 0, 0, 0))
                || !down.lock(buildFid(OBJ_TYPE_INTERFACE, 222, 0, 0, 0))
                || up.getWidth() < 100 || up.getHeight() < 30
                || down.getWidth() < 100 || down.getHeight() < 30) {
                error = "Cannot load the installed game's wiki navigation buttons.";
                return false;
            }
            const int oldFont = fontGetCurrent();
            fontSetCurrent(103);
            if (fontGetCurrent() != 103) {
                fontSetCurrent(oldFont);
                error = "Cannot load the installed game's wiki control font.";
                return false;
            }
            const char* labels[] = { "BACK", "HOME", "CLOSE" };
            for (int button = 0; button < 3; ++button) {
                for (int pressed = 0; pressed < 2; ++pressed) {
                    const auto& source = pressed ? down : up;
                    std::vector<unsigned char> pixels(source.getData(), source.getData() + source.getWidth() * source.getHeight());
                    const int textWidth = fontGetStringWidth(labels[button]);
                    const int textX = (source.getWidth() - textWidth) / 2 + pressed;
                    const int textY = (source.getHeight() - fontGetLineHeight()) / 2 + pressed;
                    fontDrawText(pixels.data() + textY * source.getWidth() + textX, labels[button],
                        textWidth, source.getWidth(), _colorTable[992] | FONT_SHADOW);
                    buttons[button][pressed].assign(pixels.data(), source.getWidth(), source.getHeight());
                }
            }
            fontSetCurrent(oldFont);
            return true;
        }

        RECT scaled(int left, int top, int right, int bottom) const
        {
            RECT client;
            GetClientRect(panel, &client);
            return { MulDiv(left, client.right, width), MulDiv(top, client.bottom, height),
                MulDiv(right, client.right, width), MulDiv(bottom, client.bottom, height) };
        }

        void resize()
        {
            if (panel == nullptr) return;
            const auto move = [&](HWND button, int left, int right) {
                const RECT rectangle = scaled(left, 8, right, 48);
                if (button) MoveWindow(button, rectangle.left, rectangle.top,
                    rectangle.right - rectangle.left, rectangle.bottom - rectangle.top, TRUE);
            };
            move(back, 22, 170);
            move(home, 180, 328);
            move(close, width - 170, width - 22);
            if (controller != nullptr) controller->put_Bounds(scaled(18, 56, width - 18, height - 18));
            InvalidateRect(panel, nullptr, FALSE);
        }

        RECT placement() const
        {
            int left = 0, top = 0, right = 0, bottom = 0;
            SDL_RenderLogicalToWindow(gSdlRenderer, static_cast<float>(x), static_cast<float>(y), &left, &top);
            SDL_RenderLogicalToWindow(gSdlRenderer, static_cast<float>(x + width), static_cast<float>(y + height), &right, &bottom);
            return { left, top, right, bottom };
        }

        void reposition()
        {
            if (gSdlRenderer == nullptr) {
                error = "The game renderer stopped. Close and reopen the wiki.";
                closing = true;
                return;
            }
            const RECT wanted = placement();
            RECT current;
            GetWindowRect(panel, &current);
            MapWindowPoints(nullptr, parent, reinterpret_cast<POINT*>(&current), 2);
            if (EqualRect(&wanted, &current)) return;
            MoveWindow(panel, wanted.left, wanted.top, wanted.right - wanted.left, wanted.bottom - wanted.top, TRUE);
            if (controller) controller->NotifyParentWindowPositionChanged();
        }

        void goBack()
        {
            if (view == nullptr) return;
            BOOL possible = FALSE;
            view->get_CanGoBack(&possible);
            if (possible)
                view->GoBack();
            else
                view->Navigate(kHomeUri);
        }
    };

    LRESULT CALLBACK panelProcedure(HWND window, UINT message, WPARAM wParam, LPARAM lParam)
    {
        Browser* browser = reinterpret_cast<Browser*>(GetWindowLongPtrW(window, GWLP_USERDATA));
        if (message == WM_NCCREATE) {
            browser = static_cast<Browser*>(reinterpret_cast<CREATESTRUCTW*>(lParam)->lpCreateParams);
            SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(browser));
        }
        if (browser != nullptr) {
            if (message == WM_COMMAND) {
                switch (LOWORD(wParam)) {
                case kBack:
                    browser->goBack();
                    return 0;
                case kHome:
                    if (browser->view != nullptr) browser->view->Navigate(kHomeUri);
                    return 0;
                case kClose:
                    browser->closing = true;
                    return 0;
                }
            } else if (message == WM_DRAWITEM) {
                auto* item = reinterpret_cast<DRAWITEMSTRUCT*>(lParam);
                if (item->CtlID >= kBack && item->CtlID <= kClose) {
                    const bool pressed = (item->itemState & ODS_SELECTED) != 0;
                    browser->buttons[item->CtlID - kBack][pressed].draw(item->hDC, item->rcItem);
                    if (item->itemState & ODS_FOCUS) {
                        RECT focus = item->rcItem;
                        InflateRect(&focus, -5, -5);
                        DrawFocusRect(item->hDC, &focus);
                    }
                    return TRUE;
                }
            } else if (message == WM_PAINT) {
                PAINTSTRUCT paint;
                HDC dc = BeginPaint(window, &paint);
                RECT client;
                GetClientRect(window, &client);
                browser->frame.draw(dc, client);
                EndPaint(window, &paint);
                return 0;
            } else if (message == WM_ERASEBKGND) {
                return 1;
            } else if (message == WM_SIZE) {
                browser->resize();
                return 0;
            } else if (message == WM_CLOSE) {
                browser->closing = true;
                return 0;
            } else if (message == WM_SETFOCUS && browser->controller != nullptr) {
                browser->controller->MoveFocus(COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC);
                return 0;
            }
        }
        return DefWindowProcW(window, message, wParam, lParam);
    }

    bool localUri(const wchar_t* uri)
    {
        constexpr wchar_t prefix[] = L"https://nixboy-wiki.invalid/";
        return uri != nullptr && std::wstring(uri).compare(0, std::size(prefix) - 1, prefix) == 0;
    }

    // Callback objects retain only weak state. A delayed controller callback
    // closes its returned controller after cancellation, without touching UI.
    HRESULT configure(const std::shared_ptr<Browser>& browser)
    {
        std::weak_ptr<Browser> weak = browser;
        const IID id4 = interfaceId(L"{20d02d59-6df2-42dc-bd06-f98a694b1302}");
        if (!browser->check(browser->controller->get_CoreWebView2(&browser->view), "Cannot access wiki browser")
            || !browser->check(browser->view->QueryInterface(id4, reinterpret_cast<void**>(&browser->view4)), "WebView2 Runtime lacks required wiki APIs")) return E_FAIL;
        if (!browser->check(browser->view4->SetVirtualHostNameToFolderMapping(kWikiHost, browser->root.c_str(),
                                COREWEBVIEW2_HOST_RESOURCE_ACCESS_KIND_DENY_CORS),
                "Cannot map local wiki files")) return E_FAIL;
        ICoreWebView2Settings* settings = nullptr;
        if (!browser->check(browser->view->get_Settings(&settings), "Cannot configure wiki browser")) return E_FAIL;
        HRESULT result = settings->put_IsScriptEnabled(TRUE);
        if (SUCCEEDED(result)) result = settings->put_IsWebMessageEnabled(FALSE);
        if (SUCCEEDED(result)) result = settings->put_AreHostObjectsAllowed(FALSE);
        if (SUCCEEDED(result)) result = settings->put_AreDevToolsEnabled(FALSE);
        if (SUCCEEDED(result)) result = settings->put_AreDefaultContextMenusEnabled(FALSE);
        if (SUCCEEDED(result)) result = settings->put_AreDefaultScriptDialogsEnabled(FALSE);
        if (SUCCEEDED(result)) result = settings->put_IsStatusBarEnabled(FALSE);
        settings->Release();
        if (!browser->check(result, "Cannot restrict wiki browser settings")) return E_FAIL;

        EventRegistrationToken token;
        auto navigation = new Callback<ICoreWebView2NavigationStartingEventHandler, ICoreWebView2*, ICoreWebView2NavigationStartingEventArgs*>(
            L"{9adbe429-f36d-432b-9ddc-f8881fbd76e3}", [](ICoreWebView2*, ICoreWebView2NavigationStartingEventArgs* args) {
                LPWSTR uri = nullptr;
                HRESULT result = args->get_Uri(&uri);
                if (FAILED(result) || !localUri(uri)) args->put_Cancel(TRUE);
                CoTaskMemFree(uri);
                return S_OK;
            });
        result = browser->view->add_NavigationStarting(navigation, &token);
        navigation->Release();
        if (!browser->check(result, "Cannot constrain wiki navigation")) return E_FAIL;

        auto loaded = new Callback<ICoreWebView2NavigationCompletedEventHandler, ICoreWebView2*, ICoreWebView2NavigationCompletedEventArgs*>(
            L"{d33a35bf-1c49-4f98-93ab-006e0533fe1c}", [weak](ICoreWebView2*, ICoreWebView2NavigationCompletedEventArgs* args) {
                auto state = weak.lock();
                if (!state || state->closed || state->closing) return S_OK;
                BOOL success = FALSE;
                COREWEBVIEW2_WEB_ERROR_STATUS status = COREWEBVIEW2_WEB_ERROR_STATUS_UNKNOWN;
                args->get_IsSuccess(&success);
                if (success)
                    state->ready = true;
                else if (SUCCEEDED(args->get_WebErrorStatus(&status)) && status != COREWEBVIEW2_WEB_ERROR_STATUS_OPERATION_CANCELED) {
                    state->error = "Cannot load a local wiki page. Check the exported wiki files and reopen.";
                    state->closing = true;
                }
                return S_OK;
            });
        result = browser->view->add_NavigationCompleted(loaded, &token);
        loaded->Release();
        if (!browser->check(result, "Cannot monitor wiki page loading")) return E_FAIL;

        auto popup = new Callback<ICoreWebView2NewWindowRequestedEventHandler, ICoreWebView2*, ICoreWebView2NewWindowRequestedEventArgs*>(
            L"{d4c185fe-c81c-4989-97af-2d3fa7ab5651}", [](ICoreWebView2*, ICoreWebView2NewWindowRequestedEventArgs* args) { return args->put_Handled(TRUE); });
        result = browser->view->add_NewWindowRequested(popup, &token);
        popup->Release();
        if (!browser->check(result, "Cannot disable wiki popups")) return E_FAIL;

        auto permission = new Callback<ICoreWebView2PermissionRequestedEventHandler, ICoreWebView2*, ICoreWebView2PermissionRequestedEventArgs*>(
            L"{15e1c6a3-c72a-4df3-91d7-d097fbec6bfd}", [](ICoreWebView2*, ICoreWebView2PermissionRequestedEventArgs* args) { return args->put_State(COREWEBVIEW2_PERMISSION_STATE_DENY); });
        result = browser->view->add_PermissionRequested(permission, &token);
        permission->Release();
        if (!browser->check(result, "Cannot deny wiki permissions")) return E_FAIL;

        auto download = new Callback<ICoreWebView2DownloadStartingEventHandler, ICoreWebView2*, ICoreWebView2DownloadStartingEventArgs*>(
            L"{efedc989-c396-41ca-83f7-07f845a55724}", [](ICoreWebView2*, ICoreWebView2DownloadStartingEventArgs* args) { return args->put_Cancel(TRUE); });
        result = browser->view4->add_DownloadStarting(download, &token);
        download->Release();
        if (!browser->check(result, "Cannot disable wiki downloads")) return E_FAIL;

        auto resource = new Callback<ICoreWebView2WebResourceRequestedEventHandler, ICoreWebView2*, ICoreWebView2WebResourceRequestedEventArgs*>(
            L"{ab00b74c-15f1-4646-80e8-e76341d25d71}", [weak](ICoreWebView2*, ICoreWebView2WebResourceRequestedEventArgs* args) {
                auto state = weak.lock();
                if (!state || state->closed) return S_OK;
                ICoreWebView2WebResourceRequest* request = nullptr;
                LPWSTR uri = nullptr;
                if (SUCCEEDED(args->get_Request(&request))) {
                    request->get_Uri(&uri);
                    request->Release();
                }
                if (!localUri(uri)) {
                    ICoreWebView2WebResourceResponse* response = nullptr;
                    HRESULT blocked = state->environment->CreateWebResourceResponse(nullptr, 403, L"Blocked", L"Content-Type: text/plain", &response);
                    if (SUCCEEDED(blocked) && response != nullptr) {
                        blocked = args->put_Response(response);
                        response->Release();
                    } else if (SUCCEEDED(blocked)) {
                        blocked = E_UNEXPECTED;
                    }
                    if (!state->check(blocked, "Cannot block a non-wiki browser request")) state->view->Stop();
                }
                CoTaskMemFree(uri);
                return S_OK;
            });
        result = browser->view->add_WebResourceRequested(resource, &token);
        resource->Release();
        if (!browser->check(result, "Cannot restrict wiki resources")
            || !browser->check(browser->view->AddWebResourceRequestedFilter(L"*", COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL), "Cannot filter wiki resources")) return E_FAIL;

        auto accelerator = new Callback<ICoreWebView2AcceleratorKeyPressedEventHandler, ICoreWebView2Controller*, ICoreWebView2AcceleratorKeyPressedEventArgs*>(
            L"{b29c7e28-fa79-41a8-8e44-65811c76dcb2}", [weak](ICoreWebView2Controller*, ICoreWebView2AcceleratorKeyPressedEventArgs* args) {
                auto state = weak.lock();
                UINT key = 0;
                COREWEBVIEW2_KEY_EVENT_KIND kind;
                if (state && SUCCEEDED(args->get_KeyEventKind(&kind)) && SUCCEEDED(args->get_VirtualKey(&key))
                    && (kind == COREWEBVIEW2_KEY_EVENT_KIND_KEY_DOWN || kind == COREWEBVIEW2_KEY_EVENT_KIND_SYSTEM_KEY_DOWN)
                    && (key == VK_F10 || key == VK_ESCAPE)) {
                    state->closing = true;
                    args->put_Handled(TRUE);
                }
                return S_OK;
            });
        result = browser->controller->add_AcceleratorKeyPressed(accelerator, &token);
        accelerator->Release();
        if (!browser->check(result, "Cannot install wiki close keys")) return E_FAIL;

        auto failed = new Callback<ICoreWebView2ProcessFailedEventHandler, ICoreWebView2*, ICoreWebView2ProcessFailedEventArgs*>(
            L"{79e0aea4-990b-42d9-aa1d-0fcc2e5bc7f1}", [weak](ICoreWebView2*, ICoreWebView2ProcessFailedEventArgs*) {
                auto state = weak.lock();
                if (state && !state->closed) {
                    state->error = "The wiki browser process stopped. Close and reopen the wiki.";
                    state->closing = true;
                }
                return S_OK;
            });
        result = browser->view->add_ProcessFailed(failed, &token);
        failed->Release();
        if (!browser->check(result, "Cannot monitor wiki browser")) return E_FAIL;
        browser->resize();
        if (!browser->check(browser->controller->put_IsVisible(TRUE), "Cannot show wiki browser")
            || !browser->check(browser->view->Navigate(kHomeUri), "Cannot open wiki home")) return E_FAIL;
        browser->controller->MoveFocus(COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC);
        return S_OK;
    }

    class InputGuard {
    public:
        explicit InputGuard(Browser& browser)
            : browser_(browser)
            , keyboardDisabled_(keyboardIsDisabled() != 0)
            , text_(SDL_IsTextInputActive())
            , relative_(SDL_GetRelativeMouseMode())
            , grab_(SDL_GetWindowGrab(gSdlWindow))
            , nativeCursor_(SDL_ShowCursor(SDL_QUERY))
            , hidden_(cursorIsHidden())
            , focus_(GetFocus())
        {
            SDL_GetEventFilter(&previous_, &previousData_);
            keyboardReset();
            inputEventQueueReset();
            keyboardDisable();
            SDL_StopTextInput();
            SDL_SetRelativeMouseMode(SDL_FALSE);
            SDL_SetWindowGrab(gSdlWindow, SDL_FALSE);
            mouseHideCursor();
            SDL_ShowCursor(SDL_ENABLE);
            SDL_SetEventFilter(filter, this);
        }
        ~InputGuard()
        {
            SDL_SetEventFilter(previous_, previousData_);
            if (IsWindow(focus_))
                SetFocus(focus_);
            else
                SetFocus(browser_.parent);
            SDL_SetWindowGrab(gSdlWindow, grab_);
            SDL_SetRelativeMouseMode(relative_);
            SDL_ShowCursor(nativeCursor_);
            if (!hidden_) mouseShowCursor();
            if (text_) SDL_StartTextInput();
            keyboardReset();
            inputEventQueueReset();
            SDL_FlushEvents(SDL_MOUSEMOTION, SDL_MOUSEWHEEL);
            SDL_GetRelativeMouseState(nullptr, nullptr);
            if (!keyboardDisabled_) keyboardEnable();
            if (browser_.quit) {
                SDL_Event event {};
                event.type = SDL_QUIT;
                SDL_PushEvent(&event);
            }
        }

    private:
        static int SDLCALL filter(void* data, SDL_Event* event)
        {
            auto* self = static_cast<InputGuard*>(data);
            if (event->type == SDL_QUIT) {
                self->browser_.quit = true;
                self->browser_.closing = true;
                return 0;
            }
            if (event->type == SDL_KEYDOWN && (event->key.keysym.sym == SDLK_F10 || event->key.keysym.sym == SDLK_ESCAPE)) self->browser_.closing = true;
            if (event->type == SDL_KEYDOWN || event->type == SDL_KEYUP || event->type == SDL_TEXTINPUT
                || event->type == SDL_MOUSEMOTION || event->type == SDL_MOUSEBUTTONDOWN
                || event->type == SDL_MOUSEBUTTONUP || event->type == SDL_MOUSEWHEEL) return 0;
            return self->previous_ ? self->previous_(self->previousData_, event) : 1;
        }
        Browser& browser_;
        bool keyboardDisabled_;
        SDL_bool text_, relative_, grab_;
        int nativeCursor_;
        bool hidden_;
        HWND focus_;
        SDL_EventFilter previous_ = nullptr;
        void* previousData_ = nullptr;
    };

    bool contentRoot(const std::string& homePath, std::wstring& root, std::string& error)
    {
        int length = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, homePath.data(), static_cast<int>(homePath.size()), nullptr, 0);
        if (length <= 0) {
            error = "Wiki home path is not valid UTF-8.";
            return false;
        }
        std::wstring wide(length, L'\0');
        MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, homePath.data(), static_cast<int>(homePath.size()), wide.data(), length);
        try {
            auto home = std::filesystem::canonical(std::filesystem::path(wide));
            if (!std::filesystem::is_regular_file(home) || home.filename() != L"index.html") {
                error = "Wiki home must point to an exported index.html.";
                return false;
            }
            root = home.parent_path().wstring();
            return true;
        } catch (const std::filesystem::filesystem_error&) {
            error = "Cannot read wiki home. Export the wiki and check [wiki] home in nixboy.ini.";
            return false;
        }
    }

} // namespace

bool nixboyWikiWindowOpen(const std::string& homePath, bool fullscreen, std::string& error)
{
    std::wstring root;
    if (!contentRoot(homePath, root, error)) return false;
    SDL_SysWMinfo info {};
    SDL_VERSION(&info.version);
    if (gSdlWindow == nullptr || gSdlRenderer == nullptr || !SDL_GetWindowWMInfo(gSdlWindow, &info) || info.subsystem != SDL_SYSWM_WINDOWS) {
        error = "Cannot find the game's native Windows window.";
        return false;
    }
    // Keep the loader loaded for process lifetime: asynchronous COM callbacks can
    // finish after a cancelled startup and must never call an unloaded module.
    static HMODULE loader = nullptr;
    if (loader == nullptr) loader = []() {
        std::vector<wchar_t> path(32768);
        DWORD size = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
        if (size == 0 || size >= path.size()) return static_cast<HMODULE>(nullptr);
        auto file = std::filesystem::path(path.data()).parent_path() / L"WebView2Loader.dll";
        return LoadLibraryExW(file.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    }();
    using CreateEnvironment = HRESULT(STDAPICALLTYPE*)(PCWSTR, PCWSTR, ICoreWebView2EnvironmentOptions*, ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler*);
    auto createEnvironment = loader ? reinterpret_cast<CreateEnvironment>(GetProcAddress(loader, "CreateCoreWebView2EnvironmentWithOptions")) : nullptr;
    if (createEnvironment == nullptr) {
        error = "Cannot load the packaged WebView2Loader.dll beside the game executable.";
        return false;
    }
    HRESULT com = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (FAILED(com)) {
        error = "Cannot initialize the Windows wiki browser apartment.";
        return false;
    }
    struct ComGuard {
        ~ComGuard() { CoUninitialize(); }
    } comGuard;
    // Browser and input objects must release before the apartment is uninitialized.
    auto browser = std::make_shared<Browser>();
    browser->root = std::move(root);
    browser->parent = info.info.win.window;
    if (!browser->compose(fullscreen)) {
        error = browser->error;
        return false;
    }
    std::wstring profile;
    try {
        auto directory = std::filesystem::path(browser->root).parent_path() / L"wiki-webview2-profile";
        std::filesystem::create_directories(directory);
        profile = directory.wstring();
    } catch (const std::filesystem::filesystem_error&) {
        error = "Cannot create the local wiki browser profile beside the exported wiki.";
        return false;
    }

    WNDCLASSW windowClass {};
    windowClass.lpfnWndProc = panelProcedure;
    windowClass.hInstance = GetModuleHandleW(nullptr);
    windowClass.hCursor = LoadCursorW(nullptr, MAKEINTRESOURCEW(32512));
    windowClass.lpszClassName = kPanelClass;
    if (!RegisterClassW(&windowClass) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
        error = "Cannot register the wiki panel.";
        return false;
    }
    InputGuard input(*browser);
    // Centered panels leave the game visible. Present the cursor-free indexed
    // background once before the native child takes ownership of its rectangle.
    renderPresent();
    const RECT rectangle = browser->placement();
    browser->panel = CreateWindowExW(0, kPanelClass, L"nix-boy wiki", WS_CHILD | WS_VISIBLE | WS_CLIPCHILDREN | WS_CLIPSIBLINGS,
        rectangle.left, rectangle.top, rectangle.right - rectangle.left, rectangle.bottom - rectangle.top,
        browser->parent, nullptr, windowClass.hInstance, browser.get());
    if (browser->panel == nullptr) {
        error = "Cannot create the in-game wiki panel.";
        return false;
    }
    auto button = [&](int id, const wchar_t* label) {
        HWND control = CreateWindowExW(0, L"BUTTON", label, WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_OWNERDRAW,
            0, 0, 1, 1, browser->panel, reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)), windowClass.hInstance, nullptr);
        return control;
    };
    browser->back = button(kBack, L"Back");
    browser->home = button(kHome, L"Home");
    browser->close = button(kClose, L"Close (F10)");
    if (!browser->back || !browser->home || !browser->close) {
        error = "Cannot create wiki navigation controls.";
        return false;
    }
    browser->resize();
    SetFocus(browser->close);
    std::weak_ptr<Browser> weak = browser;
    auto environmentReady = new Callback<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler, HRESULT, ICoreWebView2Environment*>(
        L"{4e8a3389-c9d8-4bd2-b6b5-124fee6cc14d}", [weak](HRESULT result, ICoreWebView2Environment* environment) {
            auto state = weak.lock();
            if (!state || state->closed || state->closing) return S_OK;
            if (!state->check(result, "Cannot initialize WebView2. Install or repair the Microsoft Edge WebView2 Runtime") || environment == nullptr) return S_OK;
            environment->AddRef();
            state->environment = environment;
            auto controllerReady = new Callback<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler, HRESULT, ICoreWebView2Controller*>(
                L"{6c4819f3-c9b7-4260-8127-c9f5bde7f68c}", [weak](HRESULT result, ICoreWebView2Controller* controller) {
                    auto state = weak.lock();
                    if (!state || state->closed || state->closing) {
                        if (controller) controller->Close();
                        return S_OK;
                    }
                    if (!state->check(result, "Cannot create the in-game WebView2 controller") || controller == nullptr) return S_OK;
                    controller->AddRef();
                    state->controller = controller;
                    return configure(state);
                });
            HRESULT created = environment->CreateCoreWebView2Controller(state->panel, controllerReady);
            controllerReady->Release();
            state->check(created, "Cannot start the in-game wiki browser");
            return S_OK;
        });
    HRESULT created = createEnvironment(nullptr, profile.c_str(), nullptr, environmentReady);
    environmentReady->Release();
    browser->check(created, "Cannot start WebView2. Install or repair the Microsoft Edge WebView2 Runtime");
    const DWORD start = GetTickCount();
    while (!browser->closing && _game_user_wants_to_quit == 0) {
        MSG message;
        while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
            if (message.message == WM_QUIT || (message.hwnd == browser->parent && message.message == WM_CLOSE)) {
                browser->quit = true;
                browser->closing = true;
                continue;
            }
            if ((message.message == WM_KEYDOWN || message.message == WM_SYSKEYDOWN)
                && (message.wParam == VK_F10 || message.wParam == VK_ESCAPE)
                && (message.hwnd == browser->parent || IsChild(browser->parent, message.hwnd))) {
                browser->closing = true;
                continue;
            }
            if (!IsDialogMessageW(browser->panel, &message)) {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }
        _GNW95_process_message();
        tickersExecute();
        browser->reposition();
        if (!browser->ready && GetTickCount() - start > 15000) {
            browser->error = "Wiki browser startup timed out. Close and reopen the wiki.";
            browser->closing = true;
        }
        MsgWaitForMultipleObjectsEx(0, nullptr, 16, QS_ALLINPUT, MWMO_INPUTAVAILABLE);
    }
    browser->closed = true;
    if (browser->controller) browser->controller->Close();
    DestroyWindow(browser->panel);
    browser->panel = nullptr;
    error = browser->error;
    return error.empty();
}

} // namespace fallout

#else

namespace fallout {

bool nixboyWikiWindowOpen(const std::string&, bool, std::string& error)
{
    error = "The in-game HTML wiki requires a Windows nix-boy build.";
    return false;
}

} // namespace fallout

#endif
