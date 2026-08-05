# iQOO Neo8 / OriginOS 6 USB 实机调试指南

本指南用于在 Windows 上通过 USB 安装、启动伴界 BanVerse，并自动抓取白屏或闪退日志。

## 当前电脑已经完成的配置

- Android Platform Tools 37.0.1 已放在纯英文稳定目录：
  `C:\Users\90321\AppData\Local\Android\platform-tools\`
- 上述目录已持久加入当前 Windows 用户的 `PATH`。新打开的 PowerShell 可以直接执行 `adb`；项目内的 `build/android/windows-platform-tools/platform-tools/` 保留为备用副本。
- vivo 官方 ADB 驱动已经安装：`vivo, Inc. / android_winusb.inf / 10.0.0.0 / 2025-01-22`。
- Windows 已通过 USB 识别到 `iQOO Neo8`（USB VID `2D95`）。
- 当前检测到的是 USB 网络共享接口（RNDIS），还没有检测到 ADB 接口。必须在手机端完成下面的 USB 调试和 RSA 授权。

## 一、在手机上开启开发者选项

1. 打开“设置”。
2. 进入“系统管理”或“系统管理与升级”。
3. 打开“关于手机”。
4. 连续点击“软件版本号”7 次。
5. 根据提示输入锁屏密码，直到看到“您已处于开发者模式”。
6. 返回“系统管理”或“系统管理与升级”，打开“开发者选项”。
7. 开启“USB 调试”。

不同 OriginOS 6 小版本的栏目名称可能略有差异。若找不到入口，直接在设置顶部搜索“开发者选项”或“USB 调试”。

## 二、把 USB 连接切换为调试模式

1. 关闭手机上的“USB 网络共享”。当前电脑检测到的正是该模式，它不会暴露 ADB 调试接口。
2. 使用支持数据传输的 USB 数据线，直接连接电脑 USB 接口，不要先接扩展坞。
3. 保持手机解锁，下拉通知栏，点击“正在通过 USB…”。
4. 将“USB 用途”改为“文件传输 / Android Auto”。不要选择“仅充电”或“USB 网络共享”。
5. 手机弹出“允许 USB 调试吗？”时：
   - 核对并接受 RSA 指纹；
   - 勾选“始终允许使用这台计算机进行调试”；
   - 点击“允许”。
6. 如果没有 RSA 弹窗，在开发者选项中点击“撤销 USB 调试授权”，关闭再开启 USB 调试，然后重新插拔数据线。

## 三、在电脑上确认连接

关闭 vivo 办公套件等可能占用旧版 ADB 的程序，然后打开一个**新的 PowerShell**：

```powershell
adb kill-server
adb start-server
adb devices -l
```

正常结果应包含类似内容：

```text
10AE191F8S0057G    device product:... model:V2301A transport_id:1
```

状态含义：

- `device`：连接和授权完成，可以继续。
- `unauthorized`：手机尚未确认 RSA 弹窗。
- `offline`：拔插数据线，并重新执行 `adb kill-server`、`adb start-server`。
- 列表为空：确认已关闭 USB 网络共享、USB 调试已开启，并换一根确定支持数据传输的数据线。

## 四、一键安装、启动和抓取日志

在项目根目录执行：

```powershell
Set-Location "D:\MyCode\AI Agent\04_机器学习代码开发\DeepSeek对话CLI"
powershell -ExecutionPolicy Bypass -File .\packaging\android\run_on_device.ps1
```

脚本会自动执行：

1. 识别唯一一台已授权的 Android 设备；
2. 读取型号、Android 版本、API、ABI 和内存页大小；
3. 使用对 OriginOS 更稳定的非流式模式安装 `BanVerse-0.1.12-android16-arm64-v8a-debug.apk`；
4. 清空旧 logcat，强制启动应用；
5. 等待 15 秒并检查应用进程、前台窗口；
6. 导出完整 logcat、关键异常、ApplicationExitInfo 和 Python 启动日志。

每次诊断会生成独立目录：

```text
build/android/device-logs/<时间>-<设备序列号>/
```

最重要的文件是：

- `logcat-important.txt`：筛选后的 Java、Python、Qt、Shiboken 和 native 崩溃信息；
- `logcat-full.txt`：完整系统日志；
- `exit-info.txt`：Android 记录的应用退出原因；
- `bootstrap.log` / `startup.log`：如果 Python 已经启动，会包含应用自己的启动日志；
- `install.txt` / `launch.txt`：安装与 Activity 启动结果。

如果 APK 已经安装，只想重新启动并抓日志：

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\android\run_on_device.ps1 -SkipInstall
```

如果同时连接了多台设备：

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\android\run_on_device.ps1 -Serial 10AE191F8S0057G
```

## 五、常见安装错误

- `INSTALL_FAILED_USER_RESTRICTED`：保持手机解锁并确认安装提示；如果开发者选项中存在“USB 安装”，将其开启。
- `INSTALL_FAILED_UPDATE_INCOMPATIBLE`：手机上旧 APK 与新 APK 的签名不同。卸载旧应用会清除其本地聊天和配置数据，确认可以清除后再执行：

  ```powershell
  adb uninstall app.deepseekchat.deepseekchat
  ```

- 安装成功但立刻闪退：不要反复猜测原因，保留本次 `device-logs` 目录，以 `logcat-important.txt` 和 `exit-info.txt` 为准继续修复。

## 六、可选的无线调试备用方案

Android 11 及以上支持无线调试。如果 USB 数据模式持续受系统限制，可在开发者选项中开启“无线调试”，让手机与电脑连接同一 Wi-Fi，然后使用“使用配对码配对设备”显示的地址执行：

```powershell
adb pair <手机显示的IP:配对端口>
adb connect <手机显示的IP:调试端口>
adb devices -l
```

配对端口和调试端口通常不同，必须以手机当次显示的值为准。
