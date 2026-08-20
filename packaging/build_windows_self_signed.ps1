[CmdletBinding()]
param(
    [switch]$SkipChecks,
    [switch]$SkipSmoke,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PublicCertificatePath = Join-Path `
    $ProjectRoot `
    "signing\banverse-windows-publisher.cer"
if (-not (Test-Path -LiteralPath $PublicCertificatePath -PathType Leaf)) {
    throw (
        "The self-signed publisher certificate is missing. Run " +
        "packaging\create_self_signed_windows_certificate.ps1 first."
    )
}

$Certificate = Get-PfxCertificate -FilePath $PublicCertificatePath
$InstalledCertificate = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
    Where-Object {
        $_.Thumbprint -eq $Certificate.Thumbprint -and $_.HasPrivateKey
    } |
    Select-Object -First 1
if (-not $InstalledCertificate) {
    throw "The self-signed publisher private key is unavailable."
}

$Arguments = @{
    Sign = $true
    CertificateThumbprint = $Certificate.Thumbprint
    CertificateSubject = $Certificate.Subject
    TimestampUrl = "http://ts.ssl.com"
    TrustMode = "SelfSigned"
    PublicCertificatePath = $PublicCertificatePath
    SkipChecks = $SkipChecks
    SkipSmoke = $SkipSmoke
    SkipInstaller = $SkipInstaller
}
& (Join-Path $PSScriptRoot "build_windows.ps1") @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "The self-signed Windows release build failed."
}
