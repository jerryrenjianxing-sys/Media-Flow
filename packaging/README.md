# MediaFlow Windows distribution

Build prerequisites are kept under `packaging/tools/` and generated artifacts under
`packaging/out/`; both are ignored by Git.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\packaging\bootstrap-velopack.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\packaging\build-windows-release.ps1 -Version 0.1.1
```

The release directory is a self-contained local build. The build emits the branded
`MediaFlow-Installer.exe`, a fast `MediaFlow-Setup.exe`, `MediaFlow-x64.msi`, a
portable zip, full package, and release metadata. The branded installer lets the
user choose the program location and delegates installation to Velopack.
Runtime data is stored outside the version directory at
`%LocalAppData%\MediaFlow\data` and is not included in release artifacts.
New installations configure their own OpenRouter key and device profiles. The
internal build is unsigned; add Authenticode signing before public distribution.
