[CmdletBinding()]
param(
    [string]$PackageVersion = "10.0.28000.2526"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ToolsRoot = Join-Path $ProjectRoot "build\tools\windows-sdk"
$PackageDirectory = Join-Path $ToolsRoot $PackageVersion
$PackagePath = Join-Path `
    $ToolsRoot `
    "microsoft.windows.sdk.buildtools.$PackageVersion.nupkg"
$PackageUrl = (
    "https://api.nuget.org/v3-flatcontainer/" +
    "microsoft.windows.sdk.buildtools/$PackageVersion/" +
    "microsoft.windows.sdk.buildtools.$PackageVersion.nupkg"
)
$NuGetDirectory = Join-Path $ToolsRoot "nuget"
$NuGetPath = Join-Path $NuGetDirectory "nuget.exe"
$NuGetUrl = "https://dist.nuget.org/win-x86-commandline/latest/nuget.exe"

$Existing = Get-ChildItem `
    -LiteralPath $PackageDirectory `
    -Filter signtool.exe `
    -File `
    -Recurse `
    -ErrorAction SilentlyContinue |
    Where-Object { $_.DirectoryName -match "[\\/]x64$" } |
    Select-Object -First 1
if ($Existing) {
    Write-Host "SignTool is already available: $($Existing.FullName)"
    exit 0
}
if (Test-Path -LiteralPath $PackageDirectory) {
    throw "The portable Windows SDK directory is incomplete; inspect it manually."
}

New-Item -ItemType Directory -Path $ToolsRoot -Force | Out-Null
New-Item -ItemType Directory -Path $NuGetDirectory -Force | Out-Null
if (-not (Test-Path -LiteralPath $PackagePath -PathType Leaf)) {
    Invoke-WebRequest -Uri $PackageUrl -OutFile $PackagePath
}
if (-not (Test-Path -LiteralPath $NuGetPath -PathType Leaf)) {
    Invoke-WebRequest -Uri $NuGetUrl -OutFile $NuGetPath
}

$NuGetSignature = Get-AuthenticodeSignature -LiteralPath $NuGetPath
if (
    $NuGetSignature.Status -ne
        [System.Management.Automation.SignatureStatus]::Valid -or
    $NuGetSignature.SignerCertificate.Subject -notmatch
        "(^|, )O=Microsoft Corporation(,|$)"
) {
    throw "The downloaded NuGet CLI is not validly signed by Microsoft."
}

& $NuGetPath verify -All $PackagePath -NonInteractive -ForceEnglishOutput
if ($LASTEXITCODE -ne 0) {
    throw "The Microsoft Windows SDK BuildTools NuGet signature is invalid."
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::ExtractToDirectory($PackagePath, $PackageDirectory)
$SignTool = Get-ChildItem `
    -LiteralPath $PackageDirectory `
    -Filter signtool.exe `
    -File `
    -Recurse |
    Where-Object { $_.DirectoryName -match "[\\/]x64$" } |
    Select-Object -First 1
if (-not $SignTool) {
    throw "The verified SDK package did not contain an x64 SignTool."
}

Write-Host "Verified Microsoft SignTool installed: $($SignTool.FullName)"
