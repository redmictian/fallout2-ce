# Microsoft WebView2 SDK

nix-boy's Windows wiki uses the official **Microsoft.Web.WebView2 1.0.2903.40**
NuGet SDK. This stable SDK supplies the base browser, virtual-host mapping and
download APIs needed here; the installed Evergreen Runtime provides security
updates independently. No browser engine or .NET runtime is bundled.

- Package: <https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.2903.40>
- Exact archive: <https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/1.0.2903.40/microsoft.web.webview2.1.0.2903.40.nupkg>
- Archive SHA-256: `ef128016dd1e51c59178c827ed5b8aa3322c57afa8675d930f8109505542ad74`
- Unchanged x64 loader SHA-256: `462b36fd1be6ca9f7563466a89e57c41ef4a4def3e0a84fa885d203aea4a3aaf`

CMake fetches the pinned archive and includes its unmodified
`build/native/include/WebView2.h`. It copies the architecture-matched
`WebView2Loader.dll` beside the executable. Runtime loading uses that absolute
application-directory path and Windows system dependencies only. Preserve
`LICENSE.txt` and `NOTICE.txt`, copied byte-for-byte from this SDK, with packages.
The SDK's license permits source and binary redistribution with these notices.

The MinGW cross-build on a case-sensitive WSL host needs the tiny
`mingw/EventToken.h` wrapper because the SDK includes `EventToken.h` while
MinGW ships `eventtoken.h`. GCC `include_next` selects the real system header,
including when the source directory sits on case-insensitive DrvFS. There are
no changed declarations, COM layouts or upstream-header patches. The wrapper is
only added to MinGW's include search. Callbacks use ordinary C++ COM interfaces,
without WIL/WRL or MSVC-specific UUID helpers.

For an already extracted official SDK, configure with
`-DFETCHCONTENT_SOURCE_DIR_NIXBOY_WEBVIEW2=/path/to/extracted/sdk`.
The host registers no native objects or web-message authority. It maps one local
export folder to `https://nixboy-wiki.invalid/`, blocks navigation/resources
outside it, denies permissions/popups/downloads and disables developer tools.
The browser profile/cache stays in `wiki-webview2-profile/` beside the exported
wiki directory, outside the generated pages and their package manifest.
