<p align="center">
  <img src="src/deepseek_cli/desktop/resources/app_icon.png" width="112" alt="伴界 BanVerse 图标">
</p>

<h1 align="center">伴界 BanVerse</h1>

<p align="center">
  一个面向沉浸式中文角色扮演的本地聊天应用。<br>
  让角色记住关系、理解时间、自然拆分消息，并在合适时机说话、发图和发起话题。
</p>

<p align="center">
  <a href="https://github.com/15034304312ny/BanVerse/releases/latest"><img src="https://img.shields.io/github/v/release/15034304312ny/BanVerse?display_name=tag&sort=semver" alt="最新版本"></a>
  <a href="https://github.com/15034304312ny/BanVerse/actions/workflows/ci.yml"><img src="https://github.com/15034304312ny/BanVerse/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache--2.0-D22128.svg" alt="Apache-2.0 License"></a>
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/Windows-x64-0078D4?logo=windows" alt="Windows x64">
  <img src="https://img.shields.io/badge/Android-9%2B-3DDC84?logo=android&logoColor=white" alt="Android 9+">
</p>

## 下载

前往 [GitHub Releases](https://github.com/15034304312ny/BanVerse/releases/latest) 获取最新版本：

| 平台 | 文件 | 说明 |
| --- | --- | --- |
| Windows x64 | `BanVerse-<版本>-Setup.exe` | 安装版，推荐普通用户使用 |
| Windows x64 | `BanVerse-<版本>.exe` | 免安装单文件版 |
| Android arm64-v8a | `BanVerse-<版本>-android16-arm64-v8a-release.apk` | v1.2.1 起的正式签名包，Android 9（API 28）及以上 |

> [!IMPORTANT]
> v1.2.1 Windows 产物使用项目固定的 Authenticode **自签名证书**，用于校验官方构建的完整性和发布者连续性，但不受 Windows 公共 CA 默认信任，因此 SmartScreen 仍可能显示警告。Android 从 v1.2.1 起使用固定 release key。请只从本仓库 Release 页面下载，并使用 Release 中的 SHA-256 清单和 `signing/` 公开指纹交叉校验。

BanVerse 不内置任何第三方平台的 API Key。首次运行后，至少需要配置一个文本 AI 平台才能开始对话；图片和 TTS 均为可选能力，相关调用可能产生平台费用。

## 它有什么不同

- **更深的角色扮演**：角色卡、场景、性格、示例对话、世界书、关系阶段、共同记忆和未完事件共同参与提示构建。
- **像聊天，而不是长文生成**：AI 完整回复先在本地分类，再拆成对白、旁白和发图事件，以多个短气泡延时投递。
- **角色主动联系你**：角色可读取本地日期、星期、时区和时段，在午间、傍晚或深夜发起符合情境的话题。
- **角色自主发图**：关键词、本地事件和 AI 语义判断并行决定是否发图；生成提示会结合角色设定、上下文和当前时段。
- **只朗读真正的台词**：动作、旁白和思考过程不进入 TTS；角色音色可跟随角色卡独立配置。
- **本地优先的数据管理**：会话、角色、摘要、图片和设置保存在设备本地，凭据不写入聊天数据库或源码。

## 核心功能

### 角色与记忆

- 兼容 **Character Card V2 JSON**，支持导入、导出、编辑、复制和删除。
- 支持 `{{char}}`、`{{user}}`、备用开场白、系统提示、历史后指令和 Character Book。
- 新建角色会话时，由当前文本模型结合角色设定和本地时间生成动态开场白；失败时回退到角色卡预设。
- 维护结构化连续性状态，并用最近完整轮次与长期摘要共同保持人物关系和剧情一致性。
- 可随机发现由 AI 生成的新联系人，支持每日上限、随机间隔和女性/男性生成比例。
- 自动生成角色可调用当前图片平台生成头像；失败不会阻塞角色创建。

### 消息与图片

- 消息列表显示角色姓名和 AI 生成的简短摘要，聊天详情保留完整回复。
- 长回复按语义拆成短消息，并根据内容长度、标点和消息类型加入自然等待时间。
- 支持发送 PNG、JPEG、WebP 图片和 24 个内置表情包。
- 多模态模型可理解用户图片；识别失败时仍保存原图并继续对话。
- 用户明确索图、角色发图动作和 AI 自主判断按 OR 逻辑合并，每轮最多生成一张，避免重复计费。
- 自主图片和自动头像提示会结合清晨、上午、午间、下午、傍晚、晚间、深夜或凌晨场景，不会凭时间臆造天气。

### 语音与主动消息

- AI 回复默认可自动朗读，也可以停止、重播或全局关闭。
- 只提取角色说出口的台词；全角括号中的动作、场景、旁白和思考过程保持为文字。
- 支持免费 Edge TTS / Android 系统 TTS、硅基流动 TTS、科大讯飞超拟人 TTS，以及 Windows 本地 IndexTTS2 声音克隆。
- 角色卡可以保存独立音色、IndexTTS2 预设和情感基调。
- 主动消息和随机新联系人均默认关闭，启用后只在应用运行时触发，并会产生相应 API 调用。

## AI 平台配置

设置页按能力分成三类，并只显示当前选中平台的配置项。不同能力的 Key 独立保存，互不混用。

| 能力 | 可选平台 | 用途 |
| --- | --- | --- |
| 文本 AI | DeepSeek Platform、GRS AI | 对话、摘要、动态开场白、主动消息、连续性状态和发图语义判断 |
| 图片 AI | 硅基流动、GRS AI | 图片理解、角色自主生图、自动角色头像 |
| TTS | Edge TTS / Android 系统 TTS、硅基流动、科大讯飞、本地 IndexTTS2 | 角色对白语音合成 |

GRS AI 与硅基流动支持从接口拉取模型列表，并按文本、多模态识图、生图或 TTS 能力过滤下拉选项。模型和账户权限可能随平台调整，请以各平台控制台实际返回结果为准。

### 首次使用

1. 打开“设置 → 文本 AI”，选择 DeepSeek Platform 或 GRS AI。
2. 保存对应 API Key，并选择当前账号可用的文本模型。
3. 如需识图或角色发图，在“图片 AI”中选择平台并保存独立图片 Key。
4. 如需语音，在“TTS”中选择引擎；Edge TTS 和 Android 系统 TTS 无需 Key。
5. 回到“角色”，选择一位内置角色并开始聊天。

### 本地 IndexTTS2

IndexTTS2 仅在 Windows 桌面版提供，默认连接：

```text
http://127.0.0.1:7861
```

先部署并启动 IndexTTS2 服务，再到“设置 → TTS → 本地 IndexTTS2”检测服务并刷新预设。客户端只接受 localhost 或回环 IP 地址，服务端也拒绝绑定非回环网卡。内置角色可绑定独立克隆预设，普通角色未指定预设时使用全局默认值。

## 内置角色

首次启动会写入六位原创中文角色卡，共五位女性和一位男性：

| 角色 | 定位 |
| --- | --- |
| 谢昭宁 | 冷静敏锐的宫廷星案调查者 |
| 白荼 | 温柔神秘的无昼茶馆主人 |
| 阮星遥 | 直率坚韧的废土机械师 |
| 洛弥莎 | 疏离梦幻的深海记忆歌者 |
| 周既明 | 稳重可靠的应急通信负责人 |
| 林小满 | 活泼、讨喜、会主动分享都市生活的妹妹系设计师 |

内置角色及头像可以像普通角色一样编辑、复制和删除。删除后不会在下一次启动时自动恢复；“恢复内置角色”只补回缺失项，不覆盖用户对现存角色的修改。

## 数据与隐私

- Windows API Key 优先写入系统凭据管理器；Android 写入应用私有设置目录。
- 聊天 SQLite、角色、媒体和缓存位于 Qt `QStandardPaths.AppDataLocation` 对应目录，不位于源码目录。
- 用户发送的文本、图片描述和所选角色台词会按功能发送到当前配置的第三方 AI/TTS 平台。
- 思考过程、动作和旁白不会发送给 TTS；本地 IndexTTS2 请求不会离开回环地址。
- 清空或卸载应用前请自行备份重要会话与角色卡。Windows 卸载程序默认保留用户数据。

请阅读并遵守 DeepSeek、GRS AI、硅基流动和科大讯飞各自的服务条款与隐私政策。不要提交真实 API Key、聊天数据库或个人媒体到 Issue、Pull Request 或公开仓库。

## 从源码运行

### 环境要求

- Windows 10/11
- Python 3.10 或更高版本
- Git

### 安装

```powershell
git clone https://github.com/15034304312ny/BanVerse.git
cd BanVerse
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[desktop,dev]"
```

启动桌面应用：

```powershell
.\.venv\Scripts\python.exe -m deepseek_cli.desktop.main
```

启动 CLI：

```powershell
$env:DEEPSEEK_API_KEY = "你的 API Key"
.\.venv\Scripts\deepseek-cli.exe
```

CLI 支持 `/help`、`/clear`、`/model`、`/model chat`、`/model reasoner` 和 `/exit`。

## 测试

测试使用假网关、假图片/语音服务和临时数据库，不访问真实第三方 API：

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests packaging
.\.venv\Scripts\python.exe packaging/check_version_consistency.py
.\.venv\Scripts\python.exe -m pytest
```

GitHub Actions 会在每次 push 和 pull request 上执行相同的静态检查、版本一致性校验与测试。

## 构建

### Windows

安装开发依赖和 Inno Setup 6 后执行：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -Sign
```

构建脚本会检查版本、Git 标签和干净工作区，并依次执行 Ruff、完整测试、PyInstaller 冒烟测试和 Inno Setup 构建。输出：

```text
dist/BanVerse-<版本>.exe
dist/BanVerse-<版本>-Setup.exe
```

### Android

Qt 官方 `pyside6-android-deploy` 需要 Linux 或 macOS。Windows 开发机可在 WSL2 Ubuntu 中运行：

调试 APK：

```bash
bash packaging/android/build_android.sh
```

GitHub 正式签名 APK：

```bash
bash packaging/android/build_android_release.sh
```

当前工具链固定为 CPython 3.11、JDK 21+、PySide6 6.11.1、SDK 36、NDK 28c 和 arm64-v8a，并检查 Android 15/16 所需的 16 KB ELF 页面对齐。正式版在对齐后使用固定 app signing key 签名，并核验预登记的证书 SHA-256 指纹。完整策略见 [GitHub 分发签名文档](packaging/SIGNING.md)，系统依赖和真机流程见 [Android 构建文档](packaging/android/README.md) 与 [USB 真机指南](packaging/android/USB_REAL_DEVICE_GUIDE.md)。

## 项目结构

```text
src/deepseek_cli/             CLI、模型网关与桌面/Android 应用
  desktop/                    UI、数据库、角色、图片、TTS 与后台任务
  desktop/resources/          图标、提示音、内置角色卡和头像
tests/                        单元测试与 UI/工作流测试
packaging/                    Windows、PyInstaller、Inno Setup 构建脚本
packaging/android/            Android 构建、Java 桥接与真机测试工具
.github/workflows/ci.yml      GitHub Actions 持续集成
```

## 1.2.1 更新

- 为 Windows 便携版、安装器和卸载器加入固定 Authenticode 自签名身份与可信时间戳验证；公开证书和指纹随源码发布。
- Android 正式包切换为固定 release key，构建时强制核对证书 SHA-256 指纹。
- 为项目源代码引入 Apache License 2.0，并补充第三方许可声明。
- 发布清单只接受正式签名 release APK，拒绝调试包进入 GitHub Release。

## 1.2.0 更新

- 随机角色发现新增女性/男性生成比例设置。
- 角色自主图片和自动头像提示根据设备当前时间生成合理场景。
- 修复 OriginOS/Android 文档选择器桥接，恢复聊天图片上传。
- 优化 Android 单指滑动、底部锚定和消息列表触控体验。
- Windows x64 与 iQOO Neo8 / OriginOS 6（Android 16）完成安装和冷启动验证。

## 兼容性与限制

- Android APK 只提供 `arm64-v8a`，最低 API 28，目标 API 36；v1.2.1 起使用固定 release 签名。
- v1.2.1 Windows 单文件版、安装器与卸载器使用项目自签名 Authenticode 证书和 RFC 3161 时间戳；该证书不是 Windows 公共 CA 证书，用户应通过官方仓库指纹和 Release 清单验证。
- AI、图片与云端 TTS 的可用性、模型列表、速率限制和费用由对应第三方平台决定。
- 本项目不是 SillyTavern、DeepSeek、GRS AI、硅基流动或科大讯飞的官方客户端，也不隶属于这些服务商。

## 开源许可

BanVerse 自有源代码使用 [Apache License 2.0](LICENSE)，允许使用、修改、
商用和再分发，但必须保留许可证与归属声明。打包产物中的 PySide6、
Qt、edge-tts 等第三方组件继续适用其各自许可证，详见
[NOTICE](NOTICE) 和 [第三方许可声明](THIRD_PARTY_NOTICES.md)。

## 参考规范

- [SillyTavern Character Design](https://docs.sillytavern.app/usage/core-concepts/characterdesign/)
- [Character Card V2 Spec](https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md)
