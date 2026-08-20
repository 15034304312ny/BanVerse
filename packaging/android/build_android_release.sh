#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export BANVERSE_ANDROID_BUILD_VARIANT=release
exec bash "${SCRIPT_DIR}/build_android.sh" "$@"
