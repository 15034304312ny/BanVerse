# Android 构建

本目录使用 Qt 官方 `pyside6-android-deploy`，输出 64 位 ARM 调试 APK。
该工具目前只能从 Linux 或 macOS 主机运行；Windows 请使用 WSL2 Ubuntu。

## 工具链

- CPython 3.11
- JDK 21 或更高版本
- PySide6 6.11.1
- Android SDK Platform 36、Build-Tools 36.0.0
- Android NDK 28.2.13676358（28c，启用 16 KB 页大小兼容）

`build_android.sh` 会创建独立主机虚拟环境并下载 Qt 官方
`android_aarch64` wheels。Android SDK/NDK 应预先按 Qt 版本准备好；
设置 `ANDROID_SDK_ROOT` 和 `ANDROID_NDK_ROOT` 可复用现有目录。

Ubuntu/WSL2 首次安装常用系统依赖：

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv openjdk-21-jdk \
  git curl zip unzip autoconf libtool pkg-config zlib1g-dev \
  libncurses-dev cmake libffi-dev libssl-dev
```

安装 Android 命令行工具后，执行：

```bash
sdkmanager "platform-tools" "platforms;android-36" \
  "build-tools;36.0.0" "ndk;28.2.13676358"
export ANDROID_SDK_ROOT="$HOME/Android/Sdk"
export ANDROID_NDK_ROOT="$ANDROID_SDK_ROOT/ndk/28.2.13676358"
```

也可以按照 Qt 官方方式创建自动识别的工具链缓存：

```bash
git clone --depth 1 --branch 6.11 \
  https://code.qt.io/pyside/pyside-setup.git
cd pyside-setup
python3.11 tools/cross_compile_android/main.py \
  --download-only --skip-update --auto-accept-license
cd -
```

在项目根目录构建：

```bash
bash packaging/android/build_android.sh
```

成功产物：

```text
dist/android/BanVerse-<version>-android16-arm64-v8a-debug.apk
```

首次构建会下载较大的 Android 工具链。调试 APK 使用测试签名，可用
`adb install -r dist/android/BanVerse-<version>-android16-arm64-v8a-debug.apk` 安装。

构建流程会显式重编译 16 KB 对齐的 Shiboken，移除 Android 上不需要的
FFmpeg 媒体插件，并在签名前逐个检查 APK 中所有 ELF LOAD 段。应用启动时
先显示聊天窗口，再延迟初始化 TTS 和提示音；Python 启动异常会写入应用私有
目录中的 `bootstrap.log` 与 `startup.log`。
正式发布前仍需改为 release/AAB、配置自己的签名，并在真机逐项验证
文件选择、系统 TTS、音频播放、网络请求、后台/前台切换及系统权限。
