# Unattended smoke test: launch the current versioned EXE, confirm the app window renders, then exits.
#
# Usage: powershell -ExecutionPolicy Bypass -File packaging\verify_smoke.ps1
#
# Sets BANVERSE_SMOKE_TEST=1 so the app auto-quits 500ms after the window is shown
# (exit code 0). On startup failure the app writes startup.log and returns nonzero.
#
# Note: PyInstaller onefile spawns a child process that owns the real window; the
# parent bootloader process has no MainWindowHandle. So we detect the window by
# polling all versioned BanVerse processes for a non-null MainWindowHandle instead of
# tracking a single Process object.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VersionFile = Join-Path $ProjectRoot "src\deepseek_cli\_version.py"
$VersionSource = Get-Content -LiteralPath $VersionFile -Raw -Encoding UTF8
if ($VersionSource -notmatch '__version__\s*=\s*"([^"]+)"') {
    throw "Unable to read BanVerse version from $VersionFile"
}
$Version = $Matches[1]
$ProcessName = "BanVerse-$Version"
$Exe = Join-Path $ProjectRoot "dist\$ProcessName.exe"
$BrandingFile = Join-Path $ProjectRoot "src\deepseek_cli\branding.py"
$BrandingSource = Get-Content -LiteralPath $BrandingFile -Raw -Encoding UTF8
if ($BrandingSource -notmatch 'PRODUCT_NAME\s*=\s*"([^"]+)"') {
    throw "Unable to read BanVerse product name from $BrandingFile"
}
$ExpectedWindowTitle = $Matches[1]

if (-not (Test-Path $Exe)) {
    Write-Error "Artifact not found: $Exe"
    exit 2
}

Write-Host "Smoke test starting: $Exe"

# Both env vars as a belt-and-suspenders: either triggers smoke mode.
$env:BANVERSE_SMOKE_TEST = "1"
$env:DEEPSEEK_CHAT_SMOKE_TEST = "1"

$Parent = Start-Process -FilePath $Exe -PassThru

# Onefile first launch extracts to a temp dir, so allow up to 120s.
$Deadline = (Get-Date).AddSeconds(120)
$WindowSeen = $false
$AllExited = $false
$FatalWindowTitle = ""
$ReportedHandles = @{}
while ((Get-Date) -lt $Deadline) {
    $Procs = @(Get-Process $ProcessName -ErrorAction SilentlyContinue)
    if ($Procs.Count -eq 0) {
        $AllExited = $true
        break
    }
    foreach ($Proc in $Procs) {
        try {
            $Proc.Refresh()
            $Handle = $Proc.MainWindowHandle.ToInt64()
            $Title = $Proc.MainWindowTitle
        }
        catch {
            # The smoke timer can terminate the child between Get-Process and
            # Refresh/property access. That is an expected successful race.
            continue
        }
        if ($Handle -ne 0) {
            $WindowKey = "$($Proc.Id):$Handle"
            if ($Title -eq $ExpectedWindowTitle) {
                $WindowSeen = $true
                if (-not $ReportedHandles.ContainsKey($WindowKey)) {
                    Write-Host (
                        "BanVerse window rendered " +
                        "(pid=$($Proc.Id) handle=$Handle)"
                    )
                    $ReportedHandles[$WindowKey] = $true
                }
            }
            elseif ($Title -eq "Unhandled exception in script") {
                $FatalWindowTitle = $Title
                Write-Warning "PyInstaller startup exception dialog detected."
                break
            }
            elseif (-not $ReportedHandles.ContainsKey($WindowKey)) {
                Write-Host (
                    "Ignoring non-application window '$Title' " +
                    "(pid=$($Proc.Id) handle=$Handle)"
                )
                $ReportedHandles[$WindowKey] = $true
            }
        }
    }
    if ($FatalWindowTitle) { break }
    if ($WindowSeen -and $AllExited -eq $false) {
        # Once the window rendered, keep waiting for the process to exit.
    }
    Start-Sleep -Milliseconds 200
}

# Refresh exit state one more time.
if ($FatalWindowTitle) {
    Get-Process $ProcessName -ErrorAction SilentlyContinue | Stop-Process -Force
    $ExitCode = -1
}
elseif (-not $AllExited) {
    $Procs = @(Get-Process $ProcessName -ErrorAction SilentlyContinue)
    if ($Procs.Count -eq 0) { $AllExited = $true }
}

if (-not $AllExited -and -not $FatalWindowTitle) {
    Write-Warning "Timed out waiting for exit. Killing all $ProcessName processes."
    Get-Process $ProcessName -ErrorAction SilentlyContinue | Stop-Process -Force
    $ExitCode = -1
} else {
    $ExitCode = $Parent.ExitCode
}

Remove-Item Env:BANVERSE_SMOKE_TEST -ErrorAction SilentlyContinue
Remove-Item Env:DEEPSEEK_CHAT_SMOKE_TEST -ErrorAction SilentlyContinue

Write-Host "Exited=$AllExited ExitCode=$ExitCode WindowRendered=$WindowSeen"

if ($FatalWindowTitle) {
    Write-Error "Smoke FAILED: packaged startup raised '$FatalWindowTitle'."
    exit 1
}
if (-not $AllExited) {
    Write-Error "Smoke FAILED: app did not exit in time."
    exit 1
}
if ($ExitCode -ne 0) {
    Write-Error "Smoke FAILED: exit code $ExitCode (see startup.log)."
    exit 1
}
if (-not $WindowSeen) {
    Write-Error "Smoke FAILED: exit code 0 but no window rendered."
    exit 1
}

Write-Host "Smoke test PASSED."
exit 0
