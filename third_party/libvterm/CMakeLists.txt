# libvterm 0.3.3, pinned source; see README.nixboy.md and LICENSE.
add_library(nixboy-vterm STATIC
    src/encoding.c src/keyboard.c src/mouse.c src/parser.c src/pen.c
    src/screen.c src/state.c src/unicode.c src/vterm.c)
target_include_directories(nixboy-vterm PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")
set_target_properties(nixboy-vterm PROPERTIES C_STANDARD 99 C_STANDARD_REQUIRED YES)
if(MSVC)
    # libvterm uses C99 designated structure initializers; MSVC supports them
    # in its C11 mode. Generated encoding arrays use positional initializers.
    target_compile_options(nixboy-vterm PRIVATE /std:c11)
    target_compile_definitions(nixboy-vterm PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()
