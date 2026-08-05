#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 6 )); then
    echo "usage: $0 SOURCE_ROOT TARGET_PYTHON_ROOT NDK_PATH HOST_PYTHON BUILD_DIR OUTPUT" >&2
    exit 2
fi

SOURCE_ROOT="$(cd -- "$1" && pwd)"
TARGET_PYTHON_ROOT="$(cd -- "$2" && pwd)"
NDK_PATH="$(cd -- "$3" && pwd)"
HOST_PYTHON="$4"
BUILD_DIR="$5"
OUTPUT="$6"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TOOLCHAIN="${NDK_PATH}/build/cmake/android.toolchain.cmake"
LLVM_BIN="${NDK_PATH}/toolchains/llvm/prebuilt/linux-x86_64/bin"

if [[ ! -f "${TOOLCHAIN}" || ! -x "${HOST_PYTHON}" ]]; then
    echo "Shiboken 16 KB build toolchain is incomplete." >&2
    exit 2
fi
if [[ ! -f "${TARGET_PYTHON_ROOT}/Include/Python.h" \
    || ! -f "${TARGET_PYTHON_ROOT}/android-build/libpython3.11.so" ]]; then
    echo "The python-for-android target Python build is incomplete." >&2
    exit 2
fi

cmake -S "${SCRIPT_DIR}/shiboken_16k" -B "${BUILD_DIR}" --fresh \
    -G "Unix Makefiles" \
    -DCMAKE_TOOLCHAIN_FILE="${TOOLCHAIN}" \
    -DANDROID_ABI=arm64-v8a \
    -DANDROID_PLATFORM=android-28 \
    -DANDROID_STL=c++_shared \
    -DCMAKE_BUILD_TYPE=Release \
    -DSHIBOKEN_SOURCE_ROOT="${SOURCE_ROOT}" \
    -DTARGET_PYTHON_ROOT="${TARGET_PYTHON_ROOT}" \
    -DHOST_PYTHON="${HOST_PYTHON}"
cmake --build "${BUILD_DIR}" --parallel "${ANDROID_BUILD_JOBS:-8}"

LIBRARY="${BUILD_DIR}/libshiboken6.abi3.so"
"${LLVM_BIN}/llvm-strip" --strip-unneeded "${LIBRARY}"
if "${LLVM_BIN}/llvm-readelf" -lW "${LIBRARY}" \
    | awk '$1 == "LOAD" && $NF != "0x4000" { bad = 1 } END { exit bad }'; then
    mkdir -p "$(dirname -- "${OUTPUT}")"
    cp "${LIBRARY}" "${OUTPUT}"
else
    echo "Rebuilt libshiboken is not 16 KB aligned." >&2
    exit 1
fi
