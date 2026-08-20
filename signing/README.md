# Public signing identities

This directory contains public certificates and fingerprints used to verify
BanVerse GitHub Release artifacts. It must never contain a private key,
keystore, PFX, password, token, or recovery secret.

- `banverse-android-release.cer` is the public certificate for Android release
  APKs. Its SHA-256 fingerprint is recorded in
  `banverse-android-release-sha256.txt`.
- `banverse-windows-publisher.cer` is the public certificate for the fixed
  BanVerse self-signed Authenticode publisher used by v1.2.1. It does not chain
  to a Windows public CA and must not be represented as publicly trusted.
- `banverse-windows-selfsigned-fingerprints.txt` records its SHA-256 and SHA-1
  fingerprints. Compare them with both the repository and Release notes before
  choosing to trust a downloaded build.

Always compare these files with the certificate information printed by
`apksigner verify --print-certs` or `signtool verify /pa /all /v`.
