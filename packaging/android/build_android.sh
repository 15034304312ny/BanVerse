#!/usr/bin/env bash
set -Eeuo pipefail

PYSIDE_VERSION="6.11.1"
WEBSOCKET_VERSION="1.9.0"
CERTIFI_VERSION="2026.7.22"
NDK_VERSION="28.2.13676358"
APP_VERSION="0.1.12"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
BUILD_ROOT="${PROJECT_ROOT}/build/android"
HOST_VENV="${BUILD_ROOT}/host-venv"
WHEEL_DIR="${BUILD_ROOT}/wheels"
EXEC_DIR="${BUILD_ROOT}/output"
DIST_DIR="${PROJECT_ROOT}/dist/android"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"

case "$(uname -s)" in
    Linux|Darwin) ;;
    *)
        echo "pyside6-android-deploy 仅支持 Linux/macOS；Windows 请在 WSL2 Ubuntu 中运行本脚本。" >&2
        exit 2
        ;;
esac

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "找不到 ${PYTHON_BIN}。PySide6 ${PYSIDE_VERSION} Android wheel 需要 CPython 3.11。" >&2
    exit 2
fi
if ! command -v java >/dev/null 2>&1; then
    echo "找不到 Java。请先安装 JDK 21 或更高版本。" >&2
    exit 2
fi
JAVA_VERSION_LINE="$(java -version 2>&1 | head -n 1)"
JAVA_MAJOR="$(printf '%s' "${JAVA_VERSION_LINE}" | sed -E 's/.*version "([0-9]+).*/\1/')"
if [[ ! "${JAVA_MAJOR}" =~ ^[0-9]+$ ]] || (( JAVA_MAJOR < 21 )); then
    echo "当前 Java 不满足要求（${JAVA_VERSION_LINE}）；请使用 JDK 21 或更高版本。" >&2
    exit 2
fi
if ! command -v curl >/dev/null 2>&1; then
    echo "找不到 curl，无法下载 Qt 官方 Android wheel。" >&2
    exit 2
fi
if ! "${PYTHON_BIN}" "${SCRIPT_DIR}/../check_version_consistency.py" >/dev/null 2>&1; then
    echo "版本号不一致：pyproject.toml、branding.py 与 build_android.sh 中的版本号必须保持一致。" >&2
    "${PYTHON_BIN}" "${SCRIPT_DIR}/../check_version_consistency.py" || true
    exit 2
fi
if [[ -n "${ANDROID_BUILD_PROXY_HOST:-}" \
    && -n "${ANDROID_BUILD_PROXY_PORT:-}" ]]; then
    export JAVA_TOOL_OPTIONS="\
-Dhttp.proxyHost=${ANDROID_BUILD_PROXY_HOST} \
-Dhttp.proxyPort=${ANDROID_BUILD_PROXY_PORT} \
-Dhttps.proxyHost=${ANDROID_BUILD_PROXY_HOST} \
-Dhttps.proxyPort=${ANDROID_BUILD_PROXY_PORT}"
fi

SDK_PATH="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}"
NDK_PATH="${ANDROID_NDK_ROOT:-}"
if [[ -n "${SDK_PATH}" && -z "${NDK_PATH}" && -d "${SDK_PATH}/ndk/${NDK_VERSION}" ]]; then
    NDK_PATH="${SDK_PATH}/ndk/${NDK_VERSION}"
fi
if [[ -n "${SDK_PATH}" ]]; then
    if [[ ! -d "${SDK_PATH}/platforms/android-36" || ! -d "${SDK_PATH}/build-tools/36.0.0" ]]; then
        echo "ANDROID_SDK_ROOT 缺少 platforms;android-36 或 build-tools;36.0.0。" >&2
        echo "请执行：sdkmanager \"platform-tools\" \"platforms;android-36\" \"build-tools;36.0.0\" \"ndk;${NDK_VERSION}\"" >&2
        exit 2
    fi
    if [[ ! -d "${NDK_PATH}" ]]; then
        echo "找不到 Android NDK ${NDK_VERSION}（28c）。" >&2
        exit 2
    fi
    if ! grep -q "Pkg.Revision = ${NDK_VERSION}" \
        "${NDK_PATH}/source.properties"; then
        echo "Android 16 构建必须使用 NDK ${NDK_VERSION}（28c）。" >&2
        exit 2
    fi
    if [[ ! -e "${SDK_PATH}/tools" \
        && -x "${SDK_PATH}/cmdline-tools/latest/bin/sdkmanager" ]]; then
        ln -s "cmdline-tools/latest" "${SDK_PATH}/tools"
    fi
elif [[ ! -d "${HOME}/.pyside6_android_deploy" ]]; then
    echo "未配置 Android SDK/NDK，也未找到 Qt 官方工具链缓存。" >&2
    echo "请设置 ANDROID_SDK_ROOT 和 ANDROID_NDK_ROOT，或按 packaging/android/README.md 下载官方工具链。" >&2
    exit 2
fi

mkdir -p "${BUILD_ROOT}" "${WHEEL_DIR}" "${EXEC_DIR}" "${DIST_DIR}"
if [[ ! -x "${HOST_VENV}/bin/python" ]]; then
    "${PYTHON_BIN}" -m venv --system-site-packages "${HOST_VENV}"
elif grep -q '^include-system-site-packages = false$' \
    "${HOST_VENV}/pyvenv.cfg"; then
    sed -i \
        's/^include-system-site-packages = false$/include-system-site-packages = true/' \
        "${HOST_VENV}/pyvenv.cfg"
fi
HOST_PYTHON="${HOST_VENV}/bin/python"
export PATH="${HOST_VENV}/bin:${PATH}"
"${HOST_PYTHON}" -m pip install --upgrade pip
"${HOST_PYTHON}" -m pip install \
    "PySide6==${PYSIDE_VERSION}" \
    "buildozer==1.5.0" \
    "cython==0.29.33" \
    "jinja2" \
    "pkginfo" \
    "tqdm" \
    "packaging==24.1" \
    "websocket-client==${WEBSOCKET_VERSION}" \
    "certifi==${CERTIFI_VERSION}"

PYSIDE_WHEEL="${WHEEL_DIR}/pyside6-${PYSIDE_VERSION}-${PYSIDE_VERSION}-cp311-cp311-android_aarch64.whl"
SHIBOKEN_WHEEL="${WHEEL_DIR}/shiboken6-${PYSIDE_VERSION}-${PYSIDE_VERSION}-cp311-cp311-android_aarch64.whl"
PYSIDE_URL="https://download.qt.io/official_releases/QtForPython/pyside6/${PYSIDE_WHEEL##*/}"
SHIBOKEN_URL="https://download.qt.io/official_releases/QtForPython/shiboken6/${SHIBOKEN_WHEEL##*/}"

if [[ ! -s "${PYSIDE_WHEEL}" ]]; then
    curl --fail --location --retry 3 --output "${PYSIDE_WHEEL}" "${PYSIDE_URL}"
fi
if [[ ! -s "${SHIBOKEN_WHEEL}" ]]; then
    curl --fail --location --retry 3 --output "${SHIBOKEN_WHEEL}" "${SHIBOKEN_URL}"
fi

STAGE_DIR="$(mktemp -d "${BUILD_ROOT}/stage.XXXXXX")"
"${HOST_PYTHON}" "${SCRIPT_DIR}/prepare_android_stage.py" \
    --project-root "${PROJECT_ROOT}" \
    --stage "${STAGE_DIR}" \
    --exec-directory "${EXEC_DIR}" \
    --python-path "${HOST_PYTHON}" \
    --pyside-wheel "${PYSIDE_WHEEL}" \
    --shiboken-wheel "${SHIBOKEN_WHEEL}" \
    --sdk-path "${SDK_PATH}" \
    --ndk-path "${NDK_PATH}"

(
    cd "${STAGE_DIR}"
    printf 'y\n' | "${HOST_VENV}/bin/pyside6-android-deploy" \
        --config-file "${STAGE_DIR}/pysidedeploy.spec" \
        --ndk-path "${NDK_PATH}" \
        --sdk-path "${SDK_PATH}" \
        --init \
        --keep-deployment-files \
        -f
)
P4A_COMMIT="${P4A_COMMIT:-0382d27de2f7315ed98e74884bafb30365decdee}"
P4A_UPSTREAM_SOURCE_DIR="${P4A_SOURCE_DIR:-}"
P4A_SOURCE_DIR="${HOME}/.buildozer/android/platform/python-for-android-deepseek-16k"
if [[ ! -d "${P4A_SOURCE_DIR}/.git" ]]; then
    mkdir -p "$(dirname -- "${P4A_SOURCE_DIR}")"
    if [[ -d "${P4A_UPSTREAM_SOURCE_DIR}/.git" ]]; then
        git clone --no-hardlinks "${P4A_UPSTREAM_SOURCE_DIR}" "${P4A_SOURCE_DIR}"
    else
        git clone --branch develop --single-branch \
            https://github.com/kivy/python-for-android.git \
            "${P4A_SOURCE_DIR}"
    fi
fi
if ! git -C "${P4A_SOURCE_DIR}" cat-file -e "${P4A_COMMIT}^{commit}"; then
    git -C "${P4A_SOURCE_DIR}" fetch origin develop
fi
CURRENT_P4A_COMMIT="$(git -C "${P4A_SOURCE_DIR}" rev-parse HEAD)"
if [[ "${CURRENT_P4A_COMMIT}" != "${P4A_COMMIT}" ]]; then
    if [[ -n "$(git -C "${P4A_SOURCE_DIR}" status --porcelain)" ]]; then
        echo "python-for-android 缓存包含未提交修改，无法安全切换版本：${P4A_SOURCE_DIR}" >&2
        exit 2
    fi
    git -C "${P4A_SOURCE_DIR}" switch --detach "${P4A_COMMIT}"
fi
"${HOST_PYTHON}" "${SCRIPT_DIR}/patch_p4a_16k.py" "${P4A_SOURCE_DIR}"

ANDROID_BUILD_CACHE="${ANDROID_BUILD_CACHE:-${HOME}/.buildozer/deepseek-chat-py311-16k-r28-patched-build}"
mkdir -p "${ANDROID_BUILD_CACHE}"
if [[ "${ANDROID_GRADLE_MIRROR:-0}" == "1" ]]; then
    GRADLE_INIT_DIR="${GRADLE_USER_HOME:-${HOME}/.gradle}/init.d"
    mkdir -p "${GRADLE_INIT_DIR}"
    GRADLE_INIT_SCRIPT="${GRADLE_INIT_DIR}/deepseek-chat-mirror.gradle"
    cp "${SCRIPT_DIR}/gradle_mirror.init.gradle" "${GRADLE_INIT_SCRIPT}"
fi
PATCH_ARGUMENTS=(
    "${STAGE_DIR}/buildozer.spec"
    --build-dir "${ANDROID_BUILD_CACHE}"
    --p4a-commit "${P4A_COMMIT}"
)
PATCH_ARGUMENTS+=(--p4a-source-dir "${P4A_SOURCE_DIR}")
"${HOST_PYTHON}" "${SCRIPT_DIR}/patch_buildozer_spec.py" \
    "${PATCH_ARGUMENTS[@]}"
PAGE_SIZE_LINKER_FLAG="-Wl,-z,max-page-size=16384"
export LDFLAGS="${LDFLAGS:-} ${PAGE_SIZE_LINKER_FLAG}"
export APP_LDFLAGS="${APP_LDFLAGS:-} ${PAGE_SIZE_LINKER_FLAG}"
(
    cd "${STAGE_DIR}"
    "${HOST_PYTHON}" -m buildozer android debug
)

EXPECTED_APK="${EXEC_DIR}/deepseekchat-${APP_VERSION}-arm64-v8a-debug.apk"
if [[ -f "${EXPECTED_APK}" ]]; then
    APK_PATH="${EXPECTED_APK}"
else
    APK_PATH="$(find "${STAGE_DIR}" -type f \
        -name "deepseekchat-${APP_VERSION}-arm64-v8a-debug.apk" -print -quit)"
fi
if [[ -z "${APK_PATH}" ]]; then
    echo "Android 构建命令已结束，但未找到 ${APP_VERSION} APK。部署目录保留在：${STAGE_DIR}" >&2
    exit 1
fi

PYSIDE_SOURCE_VERSION="pyside-setup-everywhere-src-${PYSIDE_VERSION}"
PYSIDE_SOURCE_ARCHIVE="${BUILD_ROOT}/${PYSIDE_SOURCE_VERSION}.zip"
PYSIDE_SOURCE_PARENT="${BUILD_ROOT}/pyside-source-${PYSIDE_VERSION}"
PYSIDE_SOURCE_ROOT="${PYSIDE_SOURCE_PARENT}/${PYSIDE_SOURCE_VERSION}/sources/shiboken6"
PYSIDE_SOURCE_URL="https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-${PYSIDE_VERSION}-src/${PYSIDE_SOURCE_VERSION}.zip"
if [[ ! -s "${PYSIDE_SOURCE_ARCHIVE}" ]]; then
    curl --fail --location --retry 3 \
        --output "${PYSIDE_SOURCE_ARCHIVE}" "${PYSIDE_SOURCE_URL}"
fi
if [[ ! -d "${PYSIDE_SOURCE_ROOT}" ]]; then
    mkdir -p "${PYSIDE_SOURCE_PARENT}"
    unzip -q "${PYSIDE_SOURCE_ARCHIVE}" -d "${PYSIDE_SOURCE_PARENT}"
fi
TARGET_PYTHON_ROOT="${ANDROID_BUILD_CACHE}/android/platform/build-arm64-v8a/build/other_builds/python3/arm64-v8a__ndk_target_28/python3"
SHIBOKEN_16K_LIBRARY="${BUILD_ROOT}/shiboken-16k/libshiboken6.abi3.so"
bash "${SCRIPT_DIR}/build_shiboken_16k.sh" \
    "${PYSIDE_SOURCE_ROOT}" \
    "${TARGET_PYTHON_ROOT}" \
    "${NDK_PATH}" \
    "${HOST_PYTHON}" \
    "${BUILD_ROOT}/shiboken-16k/cmake" \
    "${SHIBOKEN_16K_LIBRARY}"
"${HOST_PYTHON}" "${SCRIPT_DIR}/replace_apk_library.py" \
    "${APK_PATH}" \
    "lib/arm64-v8a/libshiboken6.abi3.so" \
    "${SHIBOKEN_16K_LIBRARY}" \
    --remove "lib/arm64-v8a/libplugins_multimedia_ffmpegmediaplugin_arm64-v8a.so" \
    --remove "lib/arm64-v8a/libavcodec.so" \
    --remove "lib/arm64-v8a/libavformat.so" \
    --remove "lib/arm64-v8a/libavutil.so" \
    --remove "lib/arm64-v8a/libswresample.so" \
    --remove "lib/arm64-v8a/libswscale.so"

BUILD_TOOLS="${SDK_PATH}/build-tools/36.0.0"
ALIGNED_APK="${BUILD_ROOT}/deepseekchat-16k-aligned.apk"
"${BUILD_TOOLS}/zipalign" -P 16 -f 4 "${APK_PATH}" "${ALIGNED_APK}"
"${BUILD_TOOLS}/apksigner" sign \
    --ks "${HOME}/.android/debug.keystore" \
    --ks-pass pass:android \
    --key-pass pass:android \
    "${ALIGNED_APK}"
"${BUILD_TOOLS}/apksigner" verify --verbose "${ALIGNED_APK}"
"${HOST_PYTHON}" "${SCRIPT_DIR}/check_apk_elf_alignment.py" \
    "${ALIGNED_APK}"
APK_PATH="${ALIGNED_APK}"

FINAL_APK="${DIST_DIR}/BanVerse-${APP_VERSION}-android16-arm64-v8a-debug.apk"
cp "${APK_PATH}" "${FINAL_APK}"
echo "Android APK 已生成：${FINAL_APK}"
echo "保留的部署目录：${STAGE_DIR}"
