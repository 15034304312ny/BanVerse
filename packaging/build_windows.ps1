[CmdletBinding()]
param(
    [switch]$SkipChecks,
    [switch]$SkipSmoke,
    [switch]$SkipInstaller,
    [string]$IsccPath = "",
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = if ($PythonPath) {
    $PythonPath
} else {
    Join-Path $ProjectRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project virtual environment not found: $Python"
}

$VersionReader = Join-Path $PSScriptRoot "read_project_version.py"
$Version = (& $Python $VersionReader).Trim()
if ($LASTEXITCODE -ne 0 -or -not $Version) {
    throw "Unable to read the BanVerse release version."
}
Write-Host "Building BanVerse $Version from $ProjectRoot"

Push-Location $ProjectRoot
try {
    & $Python "packaging\check_release_source.py"
    if ($LASTEXITCODE -ne 0) { throw "Release source check failed." }
    if (-not $SkipChecks) {
        & $Python "packaging\check_version_consistency.py"
        if ($LASTEXITCODE -ne 0) { throw "Version consistency check failed." }
        & $Python -m ruff check src tests packaging
        if ($LASTEXITCODE -ne 0) { throw "Ruff check failed." }
        & $Python -m pytest
        if ($LASTEXITCODE -ne 0) { throw "Test suite failed." }
    }

    & $Python -m PyInstaller --clean --noconfirm "packaging\deepseek_app.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    $Exe = Join-Path $ProjectRoot "dist\BanVerse-$Version.exe"
    if (-not (Test-Path -LiteralPath $Exe)) {
        throw "Expected executable was not created: $Exe"
    }
    if (-not $SkipSmoke) {
        & (Join-Path $PSScriptRoot "verify_smoke.ps1")
        if ($LASTEXITCODE -ne 0) { throw "Desktop smoke test failed." }
    }

    if (-not $SkipInstaller) {
        if (-not $IsccPath) {
            $Candidates = @(
                "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                "C:\Program Files\Inno Setup 6\ISCC.exe"
            )
            $IsccPath = $Candidates |
                Where-Object { Test-Path -LiteralPath $_ } |
                Select-Object -First 1
        }
        if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath)) {
            throw "Inno Setup 6 ISCC.exe was not found."
        }
        & $IsccPath "/DMyAppVersion=$Version" "packaging\installer.iss"
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed." }
        $Setup = Join-Path $ProjectRoot "dist\BanVerse-$Version-Setup.exe"
        if (-not (Test-Path -LiteralPath $Setup)) {
            throw "Expected installer was not created: $Setup"
        }
    }
}
finally {
    Pop-Location
}

Write-Host "Windows release artifacts created for BanVerse $Version."
