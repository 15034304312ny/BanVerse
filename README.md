# 伴界 BanVerse：CLI、Windows 桌面应用与 Android 构建

本项目提供两个入口：

- `deepseek-cli`：原有命令行流式聊天客户端。
- `deepseek-app`：PySide6 开发的聊天应用；Windows 使用三栏布局，
  Android 使用单列页面和底部导航。

图形版支持会话列表、本地历史、后台流式生成与真人式分段投递、折叠显示思考过程、停止与重试、内置表情包、发送与预览图片、AI 图片理解、角色自主生成并分享图片、AI 消息提示音、会话级模型切换、AI 会话摘要、角色随机主动消息、随机发现新联系人、新角色会话 AI 动态开场、浅色/深色主题和本地凭据存储。界面仅借鉴常见聊天软件的交互习惯，不使用第三方聊天软件的商标或官方素材。

## 环境要求

- Windows 10/11（运行桌面版）
- Python 3.10 或更高版本（Windows 开发与 CLI）
- 用户自己的 DeepSeek API Key
- 可选的硅基流动 API Key（统一用于图片理解、图片生成和硅基流动 TTS）
- 可选的科大讯飞超拟人 TTS：APPID + API Password，或 APPID + APIKey + APISecret
- 可选的本地 IndexTTS2：已部署的 IndexTTS2 项目、模型和角色克隆预设

> 图片与代理语音能力通过[硅基流动 API](https://docs.siliconflow.cn/)调用，
> 使用硅基流动账号内独立创建的 API Key；模型可用性和费用以
> [硅基流动定价页](https://siliconflow.cn/pricing)为准。Edge TTS 不需要 Key；
> 科大讯飞超拟人 TTS 可在设置页逐项检测当前账号真实开通的发音人，并按
> 场景、性别和名称分类显示；检测结果会缓存在本机。接口当前实际接受的默认
> 兼容音色使用 `x5_*_flow` ID，Pro/Mini 发音人仍需在控制台开通对应权限。

## 安装

在项目目录执行：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[desktop,test]"
```

开发和打包环境可安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[desktop,dev]"
```

## 运行 Windows App

```powershell
.\.venv\Scripts\deepseek-app.exe
```

也可以直接运行模块：

```powershell
.\.venv\Scripts\python.exe -m deepseek_cli.desktop.main
```

首次启动会进入设置页。在“DeepSeek API”中输入自己的 Key 并保存。
Windows 版通过 `keyring` 写入 Windows 凭据管理器；Android 版写入应用
沙箱内的 Qt 私有设置。Key 不会写入聊天 SQLite 或源码。如果本地凭据存储
不可用，应用会明确提示，并且只在当前进程内临时使用该 Key。

发送本地图片不要求硅基流动 Key；图片会复制并规范化到应用数据目录。保存硅基流动 Key 后，同一 Key 可用于图片理解、角色自主发图和可选的硅基流动 TTS。默认生图模型为 `Tongyi-MAI/Z-Image-Turbo`，默认尺寸为 `1024x1024`；模型、尺寸和角色自主发图开关可在设置页配置。Key 独立保存在平台对应的本地凭据存储中，不写入聊天数据库。

### 本地 IndexTTS2

IndexTTS2 使用独立的常驻服务，避免每条消息重复加载模型。可先双击：

```text
D:\MyCode\AI Agent\IndexTTS2\启动_BanVerse_IndexTTS2_API.bat
```

也可在设置页的 TTS 二级配置中选择“本地 IndexTTS2”，填写项目目录后点击“启动本地服务”。默认只监听 `http://127.0.0.1:7861`，提供 `/health`、`/v1/presets` 和 `/v1/audio/speech` 接口。服务就绪后点击“检测服务并刷新预设”即可选择默认音色。六位内置角色已在角色卡中绑定各自的克隆预设；用户角色可在角色编辑器的“语音”页填写 IndexTTS2 预设名，留空则使用全局默认预设。

聊天数据库位于 Qt `QStandardPaths.AppDataLocation` 对应的平台应用数据目录，
不位于源码目录。推理内容可以在界面回看，但不会发送到后续对话上下文；
只有完整成功的用户/助手轮次会成为下一轮上下文。失败、停止和意外中断的
轮次可重试，但不会污染上下文。

### 桌面交互

- 左侧“消息/角色/设置”切换顶层页面。
- 消息列表在头像右侧显示对话角色姓名；回复正文不会直接出现在列表中，而是在回复完成后由 DeepSeek 自动生成一条简短摘要。完整回复仍保存在聊天详情。
- 会话栏可新建和搜索本地会话；双击会话或点击聊天页“编辑”可修改会话名称、头像和绑定角色。
- 会话头像默认继承角色头像，也可设置仅对当前会话生效的覆盖头像；清除覆盖后恢复角色头像。
- Enter 发送，Shift+Enter 换行；生成中“发送”按钮变为“停止”。
- “表情”打开 24 个内置表情的网格选择器，点击后直接发送并以大号表情气泡显示。数据库只保存稳定的表情 ID；模型收到对应中文语义，例如“用户发了一个抱抱表情”，不会把表情当作图片上传到视觉服务。
- “图片”可选择 PNG、JPEG 或 WebP，与文字一同发送；聊天气泡显示缩略图并支持打开原图。图片限制在 20 MB、4000 万像素以内，并在本地压缩到最长边 2048 像素。
- 输入框不提供手动 ImageGen 入口。绑定角色的回复完成后，“发送图片”动作/高置信度索图关键词与 DeepSeek Flash 语义判断会作为并行触发条件，任一路径命中即可调用硅基流动，每轮自动去重为最多一张。用户表达“给我发图片/照片/自拍”“让我看看你现在的样子”等含义时会可靠触发；没有固定关键词的委婉索图交由 AI 结合角色设定、最近对话和当前回复判断。成功生成的图片会附加在同一条角色消息中，临时 URL 会立即下载到本机。普通问答不会强制生图，近期图片冷却仅限制角色自主发图，不拦截用户明确索图。
- 配置硅基流动后，用户图片先由视觉模型生成客观中文描述，再由 DeepSeek 结合角色设定自然回复；未配置或识别失败时仍会保存和显示图片，DeepSeek 会明确按“暂时无法辨认画面”处理。
- 每轮 AI 回复默认自动朗读；Windows 默认使用免费的 Microsoft Edge TTS，Android 使用系统 TTS。设置页可在默认免费引擎、科大讯飞超拟人 TTS、硅基流动 TTS 和本地 IndexTTS2 之间切换。动作与场景放在全角括号中，仅角色真正说出口的台词会朗读。讯飞模式不会调用文本 AI，也不会添加分句、呼吸停顿、多音字、轻重音、语速、音调或音量控制；同一轮对白会按原文合并为一次合成，并固定使用发音人的默认语速、语调和音量（50/50/50）。IndexTTS2 会按角色卡选择克隆预设，并将已检测的台词情绪映射为本地情感向量。每条成功回复支持播放、停止和重播，旁白、动作及思考过程不会朗读。
- 模型的完整回复不会边生成边直接展示。回复完成后先在本机分类为对白、旁白和“发送图片”事件；长对白按语义拆成多个短气泡，显示“对方正在输入…”并按下一段长度、标点和消息类型估算约 0.65～3.2 秒的等待时间后逐条投递。对白进入 TTS，旁白保留为文字，发图事件直接调用硅基流动图片生成接口并作为独立图片消息保存。
- 每条 AI 回复和生成图片成功落库后会播放一段随 EXE 打包的轻快本地提示音；设置页可以关闭或试听。失败、取消和后台摘要不会触发提示音。
- 会话顶部可以切换 Flash/Pro 模型；切换只影响后续消息并保留已有上下文。
- 角色会话默认用 1.3 的创造性参数，并维护场景、角色情绪、关系阶段、共同记忆和未完事件。最近 16 个完整轮次保留原文，更早内容由结构化连续性状态承接；世界书关键词只扫描近期语境，避免旧关键词永久激活。
- 设置页可选择跟随系统、浅色或深色主题，并清空全部本地会话。
- 设置页“角色扮演”可配置用户称呼、人物简介、Flash 回复创造性，以及是否启用连续性记忆。人物简介只作为用户自述背景，当前对话中的最新表达优先。
- 设置页可选择是否允许角色主动消息并设置随机间隔。该功能默认关闭，只在软件运行、当前打开角色会话且 API Key 可用时触发；角色会读取设备当前本地日期、星期、时区和时段，午间可自然询问午饭，傍晚可聊晚饭或回家，深夜才会温和询问是否睡不着。每次主动消息都会产生一次 DeepSeek API 调用。
- 设置页“新角色发现”可独立启用随机新联系人。软件运行期间到达随机时间后，当前文本平台会生成一张受控的原创成年 Character Card V2；通过格式、重名和高优先级指令过滤后写入本机，并用角色的第一条消息创建联系人会话。可配置 15 分钟至 7 天的随机区间和每日 1 至 10 位的成功上限，默认关闭、默认每日最多 1 位。失败或重名不占成功名额，自动角色可照常编辑和删除。

## 角色卡

角色页参考 SillyTavern 的角色工作流，支持新建、编辑、复制、删除、搜索、导入、导出和“从角色开始聊天”。首版兼容 **Character Card V2 JSON**：

- 导入/导出保留 `data.extensions`、未知字段和可选 `character_book`。
- 编辑字段包括名称、描述、性格、场景、首条消息、示例对话、备用开场白、系统提示、历史后指令、作者信息、标签和 Character Book JSON。
- 从角色开始聊天时会由当前文本模型根据角色设定和本地时间生成动态开场白；`first_mes` 与 `alternate_greetings` 仍按已有会话数轮换，但只在未配置 API Key 或开场请求失败时作为本地兜底。
- `description`、`personality`、`scenario`、`system_prompt`、`post_history_instructions`、示例对话和匹配的 character book 条目会参与提示构建。
- `creator_notes`、`creator`、`character_version`、`tags` 等作者元数据不发送给模型。
- 支持 `{{char}}` 和 `{{user}}` 占位符。
- 每个角色可以在“语音”页保留独立基础音色、IndexTTS2 克隆预设、语速、音调、音量和情感基调，也可启用动作感知的自动情绪调整。切换到科大讯飞或硅基流动后，角色音色会自动映射为对应平台的男/女发音人；切换到 IndexTTS2 后优先使用角色卡预设，留空时使用全局默认预设。配置保存在 `data.extensions.deepseek_chat.tts`，随 V2 JSON 导入导出。

首版不支持把角色数据嵌入 PNG 的 `chara` 文本块；PNG/JPEG/WebP 仅作为角色或会话头像。用户选择的头像会复制、裁剪并规范化到平台应用数据目录，原始图片不会被修改。

应用首次启动会写入六位原创中文内置角色：宫廷星案调查者谢昭宁、无昼茶馆主人白荼、废土机械师阮星遥、深海记忆歌者洛弥莎、应急通信负责人周既明，以及会主动分享都市生活的妹妹系设计师林小满，共五位女性和一位男性。内置角色及头像均可像普通角色一样编辑、复制和删除；删除后不会在下次启动时自动出现。角色页的“恢复内置角色”只补回当前缺失的内置角色，不覆盖仍存在角色的用户修改。内置头像会从打包资源复制到稳定的平台应用数据目录。六位角色头像和应用图标采用统一的原创插画语言。

参考规范：

- [SillyTavern Character Design](https://docs.sillytavern.app/usage/core-concepts/characterdesign/)
- [Character Card V2 Spec](https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md)
- [SillyTavern 中文站](https://sillytaverncn.com/)

## 运行 CLI

CLI 仍从环境变量读取 Key：

```powershell
$env:DEEPSEEK_API_KEY = "你的 API Key"
.\.venv\Scripts\deepseek-cli.exe
```

可用命令：

| 命令 | 说明 |
| --- | --- |
| `/help` | 显示帮助 |
| `/clear` | 清空当前内存会话历史 |
| `/model` | 查看当前模型 |
| `/model chat` | 切换到 `deepseek-v4-flash` |
| `/model reasoner` | 切换到 `deepseek-v4-pro` |
| `/exit`、`/quit` | 退出程序 |

旧名称 `deepseek-chat` 和 `deepseek-reasoner` 仍可作为 CLI 兼容别名。App 对外只展示官方 DeepSeek 模型名。provider 边界遵循官方 Anthropic 兼容映射：`claude-opus*` 路由到 `deepseek-v4-pro`，`claude-sonnet*` 和 `claude-haiku*` 路由到 `deepseek-v4-flash`，其他未知模型回落到 Flash。

## 测试

测试默认使用假网关、假图片与语音服务和临时数据库，不访问真实 DeepSeek、科大讯飞或硅基流动：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## 打包 Windows 正式版

安装开发依赖与 Inno Setup 6 后，使用统一构建入口：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

脚本会从 `src/deepseek_cli/_version.py` 读取唯一版本号，并要求工作区干净且对应的 `v<version>` Git 标签指向当前提交；随后依次执行版本配置校验、Ruff、完整测试、PyInstaller 构建、桌面启动冒烟和 Inno Setup 构建。产物为 `dist/BanVerse-<version>.exe` 与 `dist/BanVerse-<version>-Setup.exe`。

单文件版可复制到其他目录直接启动，不显示控制台窗口。API Key 不会被打入产物；用户仍需在首次启动时自行输入。生成图片和用户发送的聊天图片保存在 Qt 应用数据目录下的 `media` 子目录，数据库只记录本地路径。

安装版默认安装到 `Program Files\伴界 BanVerse`，创建开始菜单/桌面快捷方式并支持覆盖升级。卸载程序只移除应用本体，明确保留聊天数据库、角色、API 配置与媒体文件，避免误删用户数据。

## 打包 Android APK

Qt 官方的 `pyside6-android-deploy` 目前需要 Linux 或 macOS。Windows
开发机请先启用 WSL2 Ubuntu；Android 构建固定使用 CPython 3.11、
JDK 21+、PySide6 6.11.1、SDK 36 和 NDK 28c：

```bash
cd "/mnt/d/MyCode/AI Agent/04_机器学习代码开发/DeepSeek对话CLI"
bash packaging/android/build_android.sh
```

Android 正式构建同样要求干净工作区与版本标签；只有本地调试构建可以显式设置 `BANVERSE_DEVELOPMENT_BUILD=1` 跳过该限制。

脚本会创建隔离构建环境、下载 Qt 官方 arm64 Android wheels、校验全部
角色资源，并生成：

```text
dist/android/BanVerse-<version>-android16-arm64-v8a-debug.apk
```

详细系统依赖、真机验证项和发布签名说明见
[`packaging/android/README.md`](packaging/android/README.md)。

## 接口与隐私

应用使用 Python 标准库调用
[DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion)，
以 SSE 接收 `content` 和 `reasoning_content`，不依赖第三方模型 SDK。请求地址固定为：

```text
https://api.deepseek.com/chat/completions
```

CLI 只读取 `DEEPSEEK_API_KEY`，不会读取其他服务的环境变量；图形 App 从平台凭据存储显式取出用户保存的 DeepSeek、硅基流动 Key，以及科大讯飞 API Password/APIKey/APISecret，讯飞 APPID 作为普通设置保存在本机。DeepSeek 继续负责角色对话；硅基流动通过 `/chat/completions` 理解图片、通过 `/images/generations` 生成图片，并在用户选择硅基流动 TTS 时通过 `/audio/speech` 返回音频。TTS 可在平台默认免费引擎、科大讯飞超拟人 WebSocket 服务、硅基流动和本地 IndexTTS2 之间切换；只向所选语音服务发送提取后的角色台词，不发送思考过程、旁白或括号内动作。IndexTTS2 客户端仅接受 localhost 或回环 IP 的 HTTP 地址，服务也拒绝绑定非回环网卡。讯飞模式直接发送合并后的原台词并保持发音人默认参数，不再经过文本模型改写或插入韵律控制标记。流中的 `reasoning_content` 单独保存为思考过程；`content` 会先在后台汇总和分类，再按短消息投递。

桌面 App 在每条成功回复后会把该回复再次发送到当前文本平台生成消息列表摘要。角色会话在同一次后台请求中同时更新结构化连续性状态，因此不会为记忆维护再增加一次请求。对白、旁白和发图事件的分类与拆分完全在本机完成；发图动作和用户明确索图会形成可靠的本地兜底条件，同时当前文本平台独立进行一次语义发图判断，两者按 OR 逻辑合并并在单轮内去重。生图失败会保留已经投递的文字消息。角色提示会注入设备本地时间快照，以便普通回复和主动消息正确理解当前日期、星期及生活时段；启用“角色主动消息”后，最近上下文、连续性状态和时间提示会在随机计时到期时发送到当前文本平台，以生成一条时机合适且不重复近期话题的角色消息。启用“新角色发现”后，称呼、人物简介和已有角色的有限去重资料会在独立随机计时到期时发送到当前文本平台；返回结果只允许落入受控的角色资料字段，模型返回的系统提示和历史后指令不会被采用。两项随机功能均默认关闭。

会话保存在本机，但用户发送的文本和模型回复会经过 DeepSeek 网络服务，因此本应用不是离线模型。应用错误提示会隐藏 SDK 原始异常和凭据内容。
