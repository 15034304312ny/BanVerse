# BanVerse GitHub 分发签名策略

本项目只面向 GitHub Releases 直接分发，不使用 Google Play App Signing。
Windows 与 Android 使用彼此独立的长期签名身份，任何私钥、keystore、PFX
或密码都不得进入源码仓库、构建日志和 Release 附件。

## 发布不变量

1. Windows 便携 EXE、安装器和卸载器必须使用同一发布者身份签名。
2. Windows 签名必须使用 SHA-256 文件摘要和 RFC 3161 SHA-256 时间戳。
3. Android GitHub APK 必须是 `release` 构建，并永久使用同一 app signing key。
4. Android 构建必须比对预先登记的 SHA-256 证书指纹，拒绝临时或错误密钥。
5. 所有二进制签名、验证完成后才能计算 SHA-256 和生成 Release 清单。
6. 调试包、debug.keystore、无时间戳 EXE 和签名验证失败的产物不得发布。
7. 自签名版必须在 README、清单和 Release 说明中标明不受 Windows 公共 CA 默认信任。

## Windows Authenticode

### 签名身份

BanVerse v1.2.1 采用项目固定的 Authenticode 自签名证书。该签名能够
发现文件在签名后的篡改，并通过仓库中的公开指纹证明后续版本使用
同一发布密钥。它不是公共 CA 证书，不会让 Windows 或 SmartScreen 默认
信任 BanVerse。未来可迁移到 SignPath Foundation 免费开源签名，或受
Windows 信任的 IV/OV CA 证书。

自签名私钥的加密备份仅保存在当前用户的
`%USERPROFILE%\.banverse-signing` 目录，不得上传仓库或 Release。备份
密码使用 Windows DPAPI 保护，只能由当前 Windows 用户在本机解密。

`packaging/sign_windows.ps1` 按 SHA-1 指纹锁定证书，并在调用
SignTool 前检查证书有效期、Code Signing EKU 和私钥是否可用。
验证自签名产物时，脚本不写入 Root 或 TrustedPublisher 信任库。它允许
预期的“自签名链不受信任”状态，但仍必须通过 Authenticode 文件哈希校验、发布者
指纹和时间戳校验；`HashMismatch`、`NotSigned` 等状态会立即终止发布。

### 一次性生成自签名身份

```powershell
powershell -ExecutionPolicy Bypass `
  -File packaging\install_windows_signing_tools.ps1
powershell -ExecutionPolicy Bypass `
  -File packaging\create_self_signed_windows_certificate.ps1
```

第一个脚本下载固定版本的 Microsoft Windows SDK BuildTools NuGet 包，先验证
NuGet CLI 自身的 Microsoft Authenticode 签名，再由 `nuget verify -All` 验证
SDK 包签名，最后只将便携签名工具解包到被 Git 忽略的
`build/tools/windows-sdk` 目录。

脚本会复用未过期的同 Subject 证书，避免每次构建变更发布身份。可以
这样查看公开信息：

```powershell
Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
  Format-List Subject, Thumbprint, NotBefore, NotAfter, HasPrivateKey
```

公开证书保存在 `signing/banverse-windows-publisher.cer`，公开指纹保存在
`signing/banverse-windows-selfsigned-fingerprints.txt`。

### 环境变量

```powershell
$env:BANVERSE_WINDOWS_SIGNTOOL = "C:\Program Files (x86)\Windows Kits\10\bin\<版本>\x64\signtool.exe"
$env:BANVERSE_WINDOWS_CERT_THUMBPRINT = "受信任代码签名证书的40位SHA1指纹"
$env:BANVERSE_WINDOWS_CERT_SUBJECT = "CA证书中的完整Subject，例如CN=<经验证的姓名>, C=CN"
$env:BANVERSE_WINDOWS_TIMESTAMP_URL = "证书服务商提供的RFC3161时间戳地址"
$env:BANVERSE_WINDOWS_TRUST_MODE = "SelfSigned"
$env:BANVERSE_WINDOWS_PUBLIC_CERT = "<项目目录>\signing\banverse-windows-publisher.cer"
```

指纹和 Subject 都是公开信息。密码不应出现在 PowerShell 历史和构建参数中。

### 构建

```powershell
powershell -ExecutionPolicy Bypass `
  -File packaging\build_windows_self_signed.ps1
```

构建顺序为 PyInstaller → 签名便携 EXE → 签名验证 → 冒烟测试 → Inno Setup。
Inno Setup 通过 `SignTool=banverse` 签名安装器和卸载器，并对已签名的源 EXE
使用 `signonce`。任一签名或验证失败都会终止构建。

单独验证已有文件：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\sign_windows.ps1 `
  -Path dist\BanVerse-<版本>.exe -VerifyOnly
```

## Android release APK

### 一次性生成 app signing key

在 WSL/JDK 21 环境中执行。密钥必须放在源码目录之外：

```bash
install -d -m 700 "$HOME/.banverse-signing"
keytool -genkeypair -v \
  -keystore "$HOME/.banverse-signing/banverse-release.jks" \
  -storetype JKS \
  -alias banverse-release \
  -keyalg RSA \
  -keysize 4096 \
  -validity 10000
chmod 600 "$HOME/.banverse-signing/banverse-release.jks"
```

密码应由密码管理器生成并保存。至少制作两份加密离线备份；丢失该密钥后，
GitHub 用户将无法把新版 APK 覆盖安装到旧版之上。

读取并登记证书 SHA-256 指纹：

```bash
keytool -list -v \
  -keystore "$HOME/.banverse-signing/banverse-release.jks" \
  -alias banverse-release
```

复制输出中的 `SHA256` 值。该指纹是公有信息，用于防止构建机误用另一把密钥。
BanVerse 正式 Android 签名公钥和预登记指纹同时保存在
[`signing/`](../signing/README.md)，可与 `apksigner --print-certs` 输出交叉核验。

### 每次 release 构建

```bash
export BANVERSE_ANDROID_KEYSTORE="$HOME/.banverse-signing/banverse-release.jks"
export BANVERSE_ANDROID_KEY_ALIAS="banverse-release"
export BANVERSE_ANDROID_STORE_PASSWORD="从密码管理器注入"
export BANVERSE_ANDROID_KEY_PASSWORD="从密码管理器注入"
export BANVERSE_ANDROID_CERT_SHA256="已登记的64位SHA256证书指纹"

bash packaging/android/build_android_release.sh
```

脚本构建非调试 APK，替换并检查 16 KB 对齐的原生库，然后依次执行
`zipalign -P 16`、`apksigner sign`、`apksigner verify --print-certs` 和证书
指纹比对。签名后不会再修改 APK。输出为：

```text
dist/android/BanVerse-<版本>-android16-arm64-v8a-release.apk
```

普通调试构建仍使用：

```bash
bash packaging/android/build_android.sh
```

### 从 debug 签名迁移

截至 v1.2.0，GitHub APK 使用 Android 默认 debug.keystore。首次切换到正式
app signing key 时，包名仍为 `app.deepseekchat.deepseekchat`，但签名不同，
Android 不允许覆盖安装。必须先导出角色卡并备份应用数据，再卸载旧调试版，
最后安装正式版。完成这一次迁移后，所有后续 APK 都必须继续使用同一密钥。

## GitHub Release 门禁

正式发布前按以下顺序执行：

1. 版本号、干净提交和 `v<版本>` 标签校验通过。
2. 使用 `build_windows.ps1 -Sign` 生成并验证两个 Windows 产物。
3. 使用 `build_android_release.sh` 生成并验证 release APK。
4. 执行 `python packaging/verify_release_artifacts.py` 生成最终清单。
5. 确认清单只包含签名后的 EXE、Setup 和 `release.apk`。
6. 上传产物与清单到 GitHub Release，禁止上传 keystore、证书私钥或密码文件。

## 密钥轮换

- Windows 自签名证书应持续复用当前私钥；更换证书时必须在 Release 公告新旧指纹和迁移原因。
- Android 直发 APK 不应常规轮换 app signing key。发生泄露时应停止发布、公告
  影响并制定迁移方案；不能把新密钥当作普通更新直接替换旧密钥。

参考：

- [Microsoft SignTool](https://learn.microsoft.com/windows/win32/seccrypto/using-signtool-to-sign-a-file)
- [Microsoft SmartScreen reputation](https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation)
- [SignPath Foundation 免费开源签名](https://signpath.org/)
- [Inno Setup SignTool](https://jrsoftware.org/ishelp/topic_setup_signtool.htm)
- [Android app signing](https://developer.android.com/studio/publish/app-signing)
- [Android apksigner](https://developer.android.com/tools/apksigner)
