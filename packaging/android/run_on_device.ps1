[CmdletBinding()]
param(
    [string]$ApkPath,
    [string]$Serial,
    [ValidateRange(3, 60)]
    [int]$StartupWaitSeconds = 15,
    [switch]$SkipInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$packageName = "app.deepseekchat.deepseekchat"
$activityName = "org.kivy.android.PythonActivity"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$bundledAdb = Join-Path $projectRoot "build\android\windows-platform-tools\platform-tools\adb.exe"
$stableAdb = Join-Path $env:LOCALAPPDATA "Android\platform-tools\adb.exe"

if (-not $ApkPath) {
    $ApkPath = Join-Path $projectRoot "dist\android\BanVerse-0.1.12-android16-arm64-v8a-debug.apk"
}
$ApkPath = [System.IO.Path]::GetFullPath($ApkPath)

if (Test-Path -LiteralPath $stableAdb) {
    $adb = $stableAdb
} else {
    $adbCommand = Get-Command adb -ErrorAction SilentlyContinue
    if ($adbCommand) {
        $adb = $adbCommand.Source
    } elseif (Test-Path -LiteralPath $bundledAdb) {
        $adb = $bundledAdb
    } else {
        throw "adb was not found. Install Android platform-tools or add it to PATH."
    }
}

function Invoke-Adb {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments,
        [switch]$AllowFailure
    )

    # Windows PowerShell 5.1 wraps native stderr as ErrorRecord objects and,
    # with Stop enabled, can throw even when adb exits successfully (notably
    # for the push progress emitted by `adb install --no-streaming`).
    $stderrPath = [System.IO.Path]::GetTempFileName()
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $stdout = & $adb @Arguments 2> $stderrPath | Out-String
        $exitCode = $LASTEXITCODE
        $stdoutText = if ($null -eq $stdout) { "" } else { [string]$stdout }
        $stderrValue = Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue
        $stderrText = if ($null -eq $stderrValue) { "" } else { [string]$stderrValue }
        if ($exitCode -eq 0) {
            # Successful adb commands sometimes use stderr only for progress.
            # PowerShell 5.1 serializes that progress as a NativeCommandError.
            $stderrText = ""
        }
        $parts = @()
        if (-not [string]::IsNullOrWhiteSpace($stdoutText)) {
            $parts += $stdoutText.TrimEnd()
        }
        if (-not [string]::IsNullOrWhiteSpace($stderrText)) {
            $parts += $stderrText.TrimEnd()
        }
        $output = $parts -join [Environment]::NewLine
    } finally {
        $ErrorActionPreference = $previousErrorAction
        Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    }
    if (($exitCode -ne 0) -and (-not $AllowFailure)) {
        throw "adb failed with exit code ${exitCode}: adb $($Arguments -join ' ')`n$output"
    }
    $outputText = if ($null -eq $output) { "" } else { [string]$output }
    return [pscustomobject]@{
        ExitCode = $exitCode
        Text = $outputText.TrimEnd()
    }
}

function Get-AdbText {
    param([Parameter(Mandatory)][string[]]$Arguments)
    return (Invoke-Adb -Arguments $Arguments).Text
}

Write-Host "[1/7] Starting ADB: $adb" -ForegroundColor Cyan
& $adb start-server | Out-Host

$deviceLines = & $adb devices 2>&1 | Select-Object -Skip 1 | Where-Object { $_ -match "\S" }
$devices = @()
foreach ($line in $deviceLines) {
    if ($line -match "^([^\s]+)\s+(device|unauthorized|offline)\b") {
        $devices += [pscustomobject]@{ Serial = $Matches[1]; State = $Matches[2] }
    }
}

if ($Serial) {
    $selected = $devices | Where-Object { $_.Serial -eq $Serial } | Select-Object -First 1
    if (-not $selected) {
        throw "Device $Serial was not found. Run: adb devices -l"
    }
} else {
    $ready = @($devices | Where-Object { $_.State -eq "device" })
    if ($ready.Count -eq 1) {
        $selected = $ready[0]
    } elseif ($ready.Count -gt 1) {
        throw "Multiple devices are connected. Pass -Serial to select one."
    } elseif (@($devices | Where-Object { $_.State -eq "unauthorized" }).Count -gt 0) {
        throw "USB debugging is unauthorized. Unlock the phone, accept the RSA prompt, and retry."
    } elseif (@($devices | Where-Object { $_.State -eq "offline" }).Count -gt 0) {
        throw "The device is offline. Reconnect USB, then restart the ADB server."
    } else {
        throw "No ADB device. Disable USB tethering, enable USB debugging, select File Transfer, and accept the RSA prompt."
    }
}

$Serial = $selected.Serial
$target = @("-s", $Serial)
Write-Host "[2/7] Connected device: $Serial" -ForegroundColor Green

$properties = [ordered]@{
    Manufacturer = Get-AdbText -Arguments ($target + @("shell", "getprop", "ro.product.manufacturer"))
    Model        = Get-AdbText -Arguments ($target + @("shell", "getprop", "ro.product.model"))
    Android      = Get-AdbText -Arguments ($target + @("shell", "getprop", "ro.build.version.release"))
    ApiLevel     = Get-AdbText -Arguments ($target + @("shell", "getprop", "ro.build.version.sdk"))
    Abi          = Get-AdbText -Arguments ($target + @("shell", "getprop", "ro.product.cpu.abi"))
    PageSize     = Get-AdbText -Arguments ($target + @("shell", "getconf", "PAGE_SIZE"))
}
[pscustomobject]$properties | Format-List | Out-Host

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logDir = Join-Path $projectRoot "build\android\device-logs\$stamp-$Serial"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$properties | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logDir "device.json") -Encoding UTF8

if (-not $SkipInstall) {
    Write-Host "[3/7] Installing APK: $ApkPath" -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath $ApkPath -PathType Leaf)) {
        throw "APK does not exist: $ApkPath"
    }

    # OriginOS may leave streamed installs waiting in the package verifier.
    # Push-install is slower but deterministic on vivo/iQOO devices.
    $installResult = Invoke-Adb -Arguments ($target + @("install", "--no-streaming", "-r", "-d", "-g", $ApkPath)) -AllowFailure
    $installResult.Text | Tee-Object -LiteralPath (Join-Path $logDir "install.txt") | Out-Host
    if ($installResult.ExitCode -ne 0 -or $installResult.Text -match "Failure \[") {
        if ($installResult.Text -match "INSTALL_FAILED_UPDATE_INCOMPATIBLE") {
            throw "APK signature conflict. Uninstalling the old app will erase local app data. If acceptable, run: adb uninstall $packageName"
        }
        if ($installResult.Text -match "INSTALL_FAILED_USER_RESTRICTED") {
            throw "The phone denied USB installation. Keep it unlocked, accept the install prompt, and enable USB install if present."
        }
        throw "APK install failed. See $logDir\install.txt"
    }
} else {
    Write-Host "[3/7] Install skipped; testing the existing package." -ForegroundColor DarkGray
}

Write-Host "[4/7] Clearing logs and launching the app" -ForegroundColor Cyan
Get-AdbText -Arguments ($target + @("logcat", "-c")) | Out-Null
Get-AdbText -Arguments ($target + @("shell", "am", "force-stop", $packageName)) | Out-Null
$launchResult = Invoke-Adb -Arguments ($target + @("shell", "am", "start", "-W", "-n", "$packageName/$activityName")) -AllowFailure
$launchResult.Text | Set-Content -LiteralPath (Join-Path $logDir "launch.txt") -Encoding UTF8
$launchResult.Text | Out-Host

Write-Host "[5/7] Waiting $StartupWaitSeconds seconds for startup" -ForegroundColor Cyan
Start-Sleep -Seconds $StartupWaitSeconds

$appPidResult = Invoke-Adb -Arguments ($target + @("shell", "pidof", $packageName)) -AllowFailure
$appPid = $appPidResult.Text
$focusResult = Invoke-Adb -Arguments ($target + @("shell", "dumpsys", "activity", "activities")) -AllowFailure
$focusLine = ($focusResult.Text -split "`r?`n" | Where-Object { $_ -match "topResumedActivity|ResumedActivity|mCurrentFocus|mFocusedApp" }) -join "`n"

Write-Host "[6/7] Exporting logcat, exit info, and private startup logs" -ForegroundColor Cyan
$allLogResult = Invoke-Adb -Arguments ($target + @("logcat", "-d", "-v", "threadtime")) -AllowFailure
$allLog = $allLogResult.Text
$allLogPath = Join-Path $logDir "logcat-full.txt"
$allLog | Set-Content -LiteralPath $allLogPath -Encoding UTF8

$patterns = "FATAL EXCEPTION|AndroidRuntime|Fatal signal|DEBUG\s*:|libc\s*:|PythonActivity|python\s*:|PySide|Qt|shiboken|deepseekchat|UnsatisfiedLinkError|ImportError|Traceback|System\.err"
$important = $allLog -split "`r?`n" | Where-Object { $_ -match $patterns }
$importantPath = Join-Path $logDir "logcat-important.txt"
$important | Set-Content -LiteralPath $importantPath -Encoding UTF8

$exitInfo = Get-AdbText -Arguments ($target + @("shell", "dumpsys", "activity", "exit-info", $packageName))
$exitInfo | Set-Content -LiteralPath (Join-Path $logDir "exit-info.txt") -Encoding UTF8

foreach ($name in @("bootstrap.log", "startup.log")) {
    $privateResult = Invoke-Adb -Arguments ($target + @("exec-out", "run-as", $packageName, "cat", "files/$name")) -AllowFailure
    if ($privateResult.Text -and $privateResult.Text -notmatch "No such file|not debuggable|run-as:") {
        $privateResult.Text | Set-Content -LiteralPath (Join-Path $logDir $name) -Encoding UTF8
    }
}

Write-Host "[7/7] Result" -ForegroundColor Cyan
if ($appPid) {
    Write-Host "PASS: app process is alive, PID=$appPid" -ForegroundColor Green
    if ($focusLine -match [regex]::Escape($packageName)) {
        Write-Host "PASS: app window is in the foreground." -ForegroundColor Green
    } else {
        Write-Warning "The process is alive, but the app is not in the foreground. Check the phone for a permission dialog."
    }
} else {
    Write-Host "FAIL: app process exited. Important log lines:" -ForegroundColor Red
    $important | Select-Object -Last 160 | Out-Host
}

Write-Host "Diagnostic directory: $logDir"
Write-Host "Important log: $importantPath"
if (-not $appPid) {
    exit 2
}
