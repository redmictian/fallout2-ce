# libvterm dependency

Upstream: <https://www.leonerd.org.uk/code/libvterm/>. Vendored version **0.3.3**,
from upstream's GitHub mirror <https://github.com/neovim/libvterm>, tag `v0.3.3`,
commit `9d6d2112335080312ef8c36667fa717ded4f7daf`.

Downloaded archive:
`https://codeload.github.com/neovim/libvterm/tar.gz/9d6d2112335080312ef8c36667fa717ded4f7daf`
SHA-256: `08827df2eec047797aa8807c5213636aa76b75a0822f1286b876d30a917d043a`.
The official release endpoint failed TLS negotiation during retrieval.

License: MIT; the unmodified upstream notice is in [LICENSE](LICENSE). Headers
and `src/` are copied unchanged except three trailing spaces removed from
`vterm_keycodes.h`, `parser.c` and `unicode.c`, and two required generated
encoding `.inc` files, which are added. Upstream parser behavior is unchanged.

`generate_encoding_tables.py` generates the encoding includes from the upstream
`.tbl` files as dense positional arrays, equivalent to upstream `tbl2inc_c.pl`
but compatible with MSVC (which lacks C99 designated *array* initializers).
The upstream generator is retained for comparison. Regenerate using Python 3:

```sh
python third_party/libvterm/generate_encoding_tables.py
```

The small CMake wrapper adds static target `nixboy-vterm`. libvterm handles VT
parsing, UTF-8, cursor/edit modes, scroll regions, alternate screen, colors and
keyboard escape encoding. Nix-Boy only renders the cells using Fallout's font
and palette. The renderer currently displays ASCII, maps box-drawing to ASCII,
and substitutes `?` for other Unicode glyphs; the terminal state still retains
their original Unicode codepoints and widths. No terminal title, OSC clipboard,
or host command callback is exposed.
