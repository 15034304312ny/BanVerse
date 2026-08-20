[CmdletBinding()]
param(
    [string]$Subject = (
        "CN=BanVerse Self-Signed Publisher, " +
        "O=BanVerse Open Source Project, C=CN"
    ),
    [ValidateRange(1, 10)]
    [int]$ValidityYears = 5,
    [string]$BackupDirectory = (
        Join-Path $env:USERPROFILE ".banverse-signing"
    )
)

$ErrorActionPreference = "Stop"

function Set-PrivateSigningAcl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [switch]$Directory
    )

    $CurrentUser = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $System = [Security.Principal.SecurityIdentifier]::new(
        [Security.Principal.WellKnownSidType]::LocalSystemSid,
        $null
    )
    if ($Directory) {
        $Acl = [Security.AccessControl.DirectorySecurity]::new()
        $Inheritance = (
            [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
            [Security.AccessControl.InheritanceFlags]::ObjectInherit
        )
    }
    else {
        $Acl = [Security.AccessControl.FileSecurity]::new()
        $Inheritance = [Security.AccessControl.InheritanceFlags]::None
    }
    $Acl.SetAccessRuleProtection($true, $false)
    foreach ($Identity in @($CurrentUser, $System)) {
        $Rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $Identity,
            [Security.AccessControl.FileSystemRights]::FullControl,
            $Inheritance,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        )
        $Acl.AddAccessRule($Rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $Acl
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SigningDirectory = Join-Path $ProjectRoot "signing"
$PublicCertificatePath = Join-Path `
    $SigningDirectory `
    "banverse-windows-publisher.cer"
$FingerprintPath = Join-Path `
    $SigningDirectory `
    "banverse-windows-selfsigned-fingerprints.txt"
$ResolvedBackupDirectory = [System.IO.Path]::GetFullPath($BackupDirectory)
$ExpectedBackupRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $env:USERPROFILE ".banverse-signing")
)
if ($ResolvedBackupDirectory -ne $ExpectedBackupRoot) {
    throw "The private-key backup must remain in the dedicated user signing directory."
}

New-Item -ItemType Directory -Path $SigningDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $ResolvedBackupDirectory -Force | Out-Null
Set-PrivateSigningAcl -Path $ResolvedBackupDirectory -Directory

$Certificate = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
    Where-Object {
        $_.Subject -eq $Subject -and
        $_.Issuer -eq $Subject -and
        $_.HasPrivateKey -and
        $_.NotAfter -gt (Get-Date).AddMonths(6)
    } |
    Sort-Object NotAfter -Descending |
    Select-Object -First 1

$Created = $false
if (-not $Certificate) {
    $Certificate = New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject $Subject `
        -FriendlyName "BanVerse self-signed release publisher" `
        -KeyAlgorithm RSA `
        -KeyLength 3072 `
        -HashAlgorithm SHA256 `
        -KeyExportPolicy Exportable `
        -KeySpec Signature `
        -CertStoreLocation Cert:\CurrentUser\My `
        -NotAfter (Get-Date).AddYears($ValidityYears)
    $Created = $true
}

$Thumbprint = ($Certificate.Thumbprint -replace "\s", "").ToUpperInvariant()
$PfxPath = Join-Path `
    $ResolvedBackupDirectory `
    "banverse-windows-selfsigned-$Thumbprint.pfx"
$PasswordBackupPath = Join-Path `
    $ResolvedBackupDirectory `
    "banverse-windows-selfsigned-$Thumbprint-password.clixml"

if ($Created -or -not (Test-Path -LiteralPath $PfxPath -PathType Leaf)) {
    $RandomBytes = New-Object byte[] 48
    $Random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $Random.GetBytes($RandomBytes)
    }
    finally {
        $Random.Dispose()
    }
    $PlainPassword = [Convert]::ToBase64String($RandomBytes)
    $SecurePassword = ConvertTo-SecureString $PlainPassword -AsPlainText -Force
    Export-PfxCertificate `
        -Cert $Certificate `
        -FilePath $PfxPath `
        -Password $SecurePassword | Out-Null
    [PSCredential]::new(
        "BanVerse self-signed PFX backup",
        $SecurePassword
    ) | Export-Clixml -LiteralPath $PasswordBackupPath
    $PlainPassword = $null
}

Get-ChildItem -LiteralPath $ResolvedBackupDirectory -File |
    ForEach-Object {
        Set-PrivateSigningAcl -Path $_.FullName
    }

Export-Certificate `
    -Cert $Certificate `
    -FilePath $PublicCertificatePath `
    -Type CERT `
    -Force | Out-Null

$Sha256 = $Certificate.GetCertHashString(
    [Security.Cryptography.HashAlgorithmName]::SHA256
).ToUpperInvariant()
@(
    "Trust mode: self-signed Authenticode (not publicly trusted)"
    "Subject: $($Certificate.Subject)"
    "Issuer: $($Certificate.Issuer)"
    "Valid from: $($Certificate.NotBefore.ToUniversalTime().ToString('o'))"
    "Valid until: $($Certificate.NotAfter.ToUniversalTime().ToString('o'))"
    "SHA-256: $Sha256"
    "SHA-1: $Thumbprint"
) | Set-Content -LiteralPath $FingerprintPath -Encoding utf8

Write-Host "BanVerse self-signed publisher identity is ready."
Write-Host "Public certificate: $PublicCertificatePath"
Write-Host "Public fingerprints: $FingerprintPath"
Write-Host "Private backup: $PfxPath"
Write-Host "SHA-256: $Sha256"
Write-Host "SHA-1: $Thumbprint"
