[CmdletBinding()]
param(
    [switch]$SkipChecks,
    [switch]$SkipSmoke,
    [switch]$SkipInstaller,
    [switch]$Sign,
    [string]$IsccPath = "",
    [string]$PythonPath = "",
    [string]$SignToolPath = "",
    [string]$CertificateThumbprint = "",
    [string]$CertificateSubject = "",
    [string]$TimestampUrl = "",
    [ValidateSet("PublicCa", "SelfSigned")]
    [string]$TrustMode = "PublicCa",
    [string]$PublicCertificatePath = ""
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

$Signer = Join-Path $PSScriptRoot "sign_windows.ps1"
if ($Sign) {
    if ($SignToolPath) {
        $env:BANVERSE_WINDOWS_SIGNTOOL = $SignToolPath
    }
    if ($CertificateThumbprint) {
        $env:BANVERSE_WINDOWS_CERT_THUMBPRINT = $CertificateThumbprint
    }
    if ($CertificateSubject) {
        $env:BANVERSE_WINDOWS_CERT_SUBJECT = $CertificateSubject
    }
    if ($TimestampUrl) {
        $env:BANVERSE_WINDOWS_TIMESTAMP_URL = $TimestampUrl
    }
    $env:BANVERSE_WINDOWS_TRUST_MODE = $TrustMode
    if ($PublicCertificatePath) {
        $env:BANVERSE_WINDOWS_PUBLIC_CERT = $PublicCertificatePath
    }
    if (-not (Test-Path -LiteralPath $Signer -PathType Leaf)) {
        throw "Windows signing helper was not found: $Signer"
    }
}

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

    # PyInstaller resolves native dependencies through PATH. Codex and other
    # developer shells may prepend private Poppler/ICU or API-set DLL folders;
    # collecting those host-only binaries makes QtCore fail on clean machines
    # (and can even shadow Windows' own ICU in the local smoke test). Keep the
    # native dependency scan deterministic and restore the caller's PATH as
    # soon as collection finishes.
    $OriginalPath = $env:PATH
    $System32 = Join-Path $env:SystemRoot "System32"
    $PythonDirectory = Split-Path -Parent $Python
    $env:PATH = @($PythonDirectory, $System32, $env:SystemRoot) -join ";"
    try {
        & $Python -m PyInstaller --clean --noconfirm "packaging\deepseek_app.spec"
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
    }
    finally {
        $env:PATH = $OriginalPath
    }

    $AnalysisToc = Join-Path $ProjectRoot "build\deepseek_app\Analysis-00.toc"
    if (-not (Test-Path -LiteralPath $AnalysisToc -PathType Leaf)) {
        throw "PyInstaller dependency manifest was not created: $AnalysisToc"
    }
    if (Select-String -LiteralPath $AnalysisToc -SimpleMatch "\codex-runtimes\") {
        throw "Host Codex runtime DLLs leaked into the Windows artifact."
    }

    $Exe = Join-Path $ProjectRoot "dist\BanVerse-$Version.exe"
    if (-not (Test-Path -LiteralPath $Exe)) {
        throw "Expected executable was not created: $Exe"
    }
    if ($Sign) {
        & $Signer -Path $Exe
        if ($LASTEXITCODE -ne 0) { throw "Executable signing failed." }
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
        $IsccArguments = @("/DMyAppVersion=$Version")
        if ($Sign) {
            $PowerShell = (Get-Process -Id $PID).Path
            $InnoFilePlaceholder = [char]36 + "f"
            $InnoQuotePlaceholder = [char]36 + "q"
            $InnoSignCommand = (
                '{0}{1}{0} -NoLogo -NoProfile -ExecutionPolicy Bypass ' +
                '-File {0}{2}{0} -Path {3}'
            ) -f (
                $InnoQuotePlaceholder,
                $PowerShell,
                $Signer,
                $InnoFilePlaceholder
            )
            $IsccArguments += "/DBanVerseSignedBuild=1"
            $IsccArguments += "/Sbanverse=$InnoSignCommand"
        }
        $IsccArguments += "packaging\installer.iss"
        & $IsccPath @IsccArguments
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed." }
        $Setup = Join-Path $ProjectRoot "dist\BanVerse-$Version-Setup.exe"
        if (-not (Test-Path -LiteralPath $Setup)) {
            throw "Expected installer was not created: $Setup"
        }
        if ($Sign) {
            & $Signer -Path $Setup -VerifyOnly
            if ($LASTEXITCODE -ne 0) {
                throw "Installer signature verification failed."
            }
        }
    }
}
finally {
    Pop-Location
}

Write-Host "Windows release artifacts created for BanVerse $Version."
