[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [string]$SignToolPath = $env:BANVERSE_WINDOWS_SIGNTOOL,
    [string]$CertificateThumbprint = $env:BANVERSE_WINDOWS_CERT_THUMBPRINT,
    [string]$ExpectedSubject = $env:BANVERSE_WINDOWS_CERT_SUBJECT,
    [string]$TimestampUrl = $env:BANVERSE_WINDOWS_TIMESTAMP_URL,
    [ValidateSet("PublicCa", "SelfSigned")]
    [string]$TrustMode = $(
        if ($env:BANVERSE_WINDOWS_TRUST_MODE) {
            $env:BANVERSE_WINDOWS_TRUST_MODE
        } else {
            "PublicCa"
        }
    ),
    [string]$PublicCertificatePath = $env:BANVERSE_WINDOWS_PUBLIC_CERT,
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"

function Resolve-SignTool {
    param([string]$ConfiguredPath)

    if ($ConfiguredPath) {
        $resolved = [System.IO.Path]::GetFullPath($ConfiguredPath)
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "SignTool was not found: $resolved"
        }
        return $resolved
    }

    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $programFilesX86 = [Environment]::GetFolderPath(
        [Environment+SpecialFolder]::ProgramFilesX86
    )
    if ($programFilesX86) {
        $kitsRoot = Join-Path $programFilesX86 "Windows Kits\10\bin"
    }
    if ($programFilesX86 -and (Test-Path -LiteralPath $kitsRoot -PathType Container)) {
        $candidate = Get-ChildItem -LiteralPath $kitsRoot -Directory |
            Sort-Object Name -Descending |
            ForEach-Object {
                Join-Path $_.FullName "x64\signtool.exe"
            } |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
            Select-Object -First 1
        if ($candidate) {
            return $candidate
        }
    }

    $PortableSdkRoot = Join-Path `
        (Split-Path -Parent $PSScriptRoot) `
        "build\tools\windows-sdk"
    if (Test-Path -LiteralPath $PortableSdkRoot -PathType Container) {
        $PortableCandidate = Get-ChildItem `
            -LiteralPath $PortableSdkRoot `
            -Filter signtool.exe `
            -File `
            -Recurse `
            -ErrorAction SilentlyContinue |
            Where-Object { $_.DirectoryName -match "[\\/]x64$" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($PortableCandidate) {
            return $PortableCandidate.FullName
        }
    }

    throw "SignTool was not found. Install the Windows SDK signing tools."
}

function Resolve-CodeSigningCertificate {
    param([string]$Thumbprint)

    $Certificates = @(
        Get-ChildItem -Path Cert:\CurrentUser\My, Cert:\LocalMachine\My `
            -CodeSigningCert -ErrorAction SilentlyContinue |
            Where-Object {
                (($_.Thumbprint -replace "\s", "").ToUpperInvariant()) -eq $Thumbprint
            }
    )
    if ($Certificates.Count -eq 0) {
        throw (
            "The configured code-signing certificate is not available in the " +
            "Windows certificate store. Install or unlock the CA hardware/cloud " +
            "signing provider before building."
        )
    }

    $Certificate = $Certificates |
        Where-Object { $_.NotBefore -le (Get-Date) -and $_.NotAfter -gt (Get-Date) } |
        Select-Object -First 1
    if (-not $Certificate) {
        throw "The configured code-signing certificate is not currently valid."
    }
    if (-not $Certificate.HasPrivateKey) {
        throw (
            "The certificate is present, but its hardware token or cloud HSM " +
            "private-key provider is unavailable."
        )
    }
    $CodeSigningOid = "1.3.6.1.5.5.7.3.3"
    $EnhancedKeyUsages = @(
        $Certificate.EnhancedKeyUsageList |
            ForEach-Object { [string]$_.ObjectId }
    )
    if ($CodeSigningOid -notin $EnhancedKeyUsages) {
        throw "The configured certificate is not authorized for code signing."
    }
    return $Certificate
}

$Target = [System.IO.Path]::GetFullPath($Path)
if (-not (Test-Path -LiteralPath $Target -PathType Leaf)) {
    throw "Signing target was not found: $Target"
}
$SignTool = Resolve-SignTool -ConfiguredPath $SignToolPath
$Thumbprint = ([string]$CertificateThumbprint -replace "\s", "").ToUpperInvariant()

if (-not $VerifyOnly) {
    if ($Thumbprint -notmatch "^[0-9A-F]{40}$") {
        throw (
            "BANVERSE_WINDOWS_CERT_THUMBPRINT must contain the 40-character " +
            "SHA-1 thumbprint of a trusted code-signing certificate."
        )
    }
    $SigningCertificate = Resolve-CodeSigningCertificate -Thumbprint $Thumbprint
    if ($ExpectedSubject -and -not $SigningCertificate.Subject.Equals(
        $ExpectedSubject.Trim(),
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "The configured certificate subject does not match the expected publisher."
    }
    $Timestamp = $null
    if (-not [Uri]::TryCreate(
        $TimestampUrl,
        [UriKind]::Absolute,
        [ref]$Timestamp
    ) -or $Timestamp.Scheme -notin @("http", "https")) {
        throw "BANVERSE_WINDOWS_TIMESTAMP_URL must be an RFC 3161 HTTP(S) URL."
    }

    & $SignTool sign `
        /fd SHA256 `
        /tr $Timestamp.AbsoluteUri `
        /td SHA256 `
        /sha1 $Thumbprint `
        /d "伴界 BanVerse" `
        $Target
    if ($LASTEXITCODE -ne 0) {
        throw "Authenticode signing failed: $Target"
    }
}

if ($TrustMode -eq "SelfSigned") {
    if ($Thumbprint -notmatch "^[0-9A-F]{40}$") {
        throw "SelfSigned verification requires BANVERSE_WINDOWS_CERT_THUMBPRINT."
    }
    if (-not $PublicCertificatePath) {
        throw "SelfSigned verification requires BANVERSE_WINDOWS_PUBLIC_CERT."
    }
    $PublicCertificateFile = [System.IO.Path]::GetFullPath(
        $PublicCertificatePath
    )
    if (-not (Test-Path -LiteralPath $PublicCertificateFile -PathType Leaf)) {
        throw "The self-signed public certificate was not found."
    }
    $PublicCertificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new(
        $PublicCertificateFile
    )
    $PublicThumbprint = (
        $PublicCertificate.Thumbprint -replace "\s", ""
    ).ToUpperInvariant()
    if ($PublicThumbprint -ne $Thumbprint) {
        throw "The self-signed public certificate fingerprint does not match."
    }
    if ($PublicCertificate.Subject -ne $PublicCertificate.Issuer) {
        throw "SelfSigned mode only accepts a self-issued publisher certificate."
    }
}
else {
    & $SignTool verify /pa /all /v $Target
    if ($LASTEXITCODE -ne 0) {
        throw "Authenticode verification failed: $Target"
    }
}

$Signature = Get-AuthenticodeSignature -LiteralPath $Target
if ($TrustMode -eq "SelfSigned") {
    $AllowedSelfSignedStatuses = @(
        [System.Management.Automation.SignatureStatus]::Valid,
        [System.Management.Automation.SignatureStatus]::UnknownError,
        [System.Management.Automation.SignatureStatus]::NotTrusted
    )
    if ($Signature.Status -notin $AllowedSelfSignedStatuses) {
        throw (
            "The self-signed Authenticode signature is missing, altered, or " +
            "cryptographically invalid: $($Signature.Status)."
        )
    }
}
else {
    if ($Signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        throw "Windows does not consider the signature valid in the selected trust mode."
    }
}
if (-not $Signature.SignerCertificate) {
    throw "The file does not contain an Authenticode signer certificate."
}
if (-not $Signature.TimeStamperCertificate) {
    throw "The signature has no trusted timestamp: $Target"
}
if ($Thumbprint) {
    $ActualThumbprint = (
        $Signature.SignerCertificate.Thumbprint -replace "\s", ""
    ).ToUpperInvariant()
    if ($ActualThumbprint -ne $Thumbprint) {
        throw "The signing certificate does not match the configured thumbprint."
    }
}
if ($ExpectedSubject -and -not $Signature.SignerCertificate.Subject.Equals(
    $ExpectedSubject.Trim(),
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "The signed file publisher does not match BANVERSE_WINDOWS_CERT_SUBJECT."
}

Write-Host "Authenticode signature verified ($TrustMode): $Target"
