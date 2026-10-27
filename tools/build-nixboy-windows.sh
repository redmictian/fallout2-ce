#!/usr/bin/env bash
# Run in WSL. Dependencies contain SDL2-2.26.1/ and zlib-1.3.1/ source trees.
# Usage: bash tools/build-nixboy-windows.sh DEPS_DIR BUILD_DIR [terminal ON|OFF] [perk training ON|OFF]
set -euo pipefail
if [[ $# -lt 2 || $# -gt 4 ]]; then
    echo 'Usage: build-nixboy-windows.sh DEPS_DIR BUILD_DIR [terminal ON|OFF] [perk training ON|OFF]' >&2
    exit 2
fi
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
deps_dir=$(CDPATH= cd -- "$1" && pwd)
build_dir=$(realpath -m -- "$2")
feature=${3:-ON}
perk_training=${4:-OFF}
case "$build_dir/" in
    "$source_dir/"*) echo 'Build output must be outside source.' >&2; exit 2 ;;
esac
case "$feature" in ON|OFF) ;; *) echo 'Feature must be ON or OFF.' >&2; exit 2 ;; esac
case "$perk_training" in ON|OFF) ;; *) echo 'Perk training must be ON or OFF.' >&2; exit 2 ;; esac
mkdir -p "$build_dir"
cmake -S "$source_dir" -B "$build_dir" -G Ninja \
    -DCMAKE_SYSTEM_NAME=Windows \
    -DCMAKE_C_COMPILER=x86_64-w64-mingw32-gcc \
    -DCMAKE_CXX_COMPILER=x86_64-w64-mingw32-g++ \
    -DCMAKE_RC_COMPILER=x86_64-w64-mingw32-windres \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_EXE_LINKER_FLAGS=-static \
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
    -DFALLOUT_NIXBOY="$feature" \
    -DFALLOUT_NIXBOY_PERK_TRAINING="$perk_training" \
    -DFETCHCONTENT_SOURCE_DIR_SDL2="$deps_dir/SDL2-2.26.1" \
    -DFETCHCONTENT_SOURCE_DIR_ZLIB="$deps_dir/zlib-1.3.1" \
    2>&1 | tee "$build_dir/configure.log"
cmake --build "$build_dir" --parallel 6 2>&1 | tee "$build_dir/build.log"
