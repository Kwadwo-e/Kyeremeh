param(
  [switch]$SkipInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Using project folder: $Root"
python --version
node --version
npm --version

if (-not $SkipInstall) {
  python -m pip install --upgrade pip
  python -m pip install -r requirements-build.txt

  if (Test-Path package-lock.json) {
    npm ci
  } else {
    npm install
  }
}

python -m PyInstaller --version
npm run desktop:validate
npm run desktop:build:win

Write-Host ""
Write-Host "Windows installer output:"
$ReleasePath = Join-Path $Root "release"
$Installers = Get-ChildItem -Path $ReleasePath -Filter "*.exe" -Recurse -ErrorAction SilentlyContinue
if (-not $Installers) {
  throw "No Windows .exe installer was created in $ReleasePath."
}
$Installers | Select-Object -ExpandProperty FullName
