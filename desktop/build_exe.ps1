# Builds dist\MultiSync.exe (single file, windowed). Run from the desktop folder:
#   powershell -ExecutionPolicy Bypass -File build_exe.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install --quiet -r requirements.txt pyinstaller pillow
python tools\make_icon.py
python -m pytest -q tests
if ($LASTEXITCODE -ne 0) { throw "tests failed" }

python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name MultiSync `
    --icon assets\icon.ico `
    --add-data "assets;assets" `
    --version-file version_info.txt `
    --collect-data customtkinter `
    --collect-binaries sounddevice `
    --collect-data soundfile `
    --collect-binaries soundfile `
    --hidden-import soundcard.mediafoundation `
    --collect-data soundcard `
    --noupx `
    main.py

Write-Host "Built: $(Resolve-Path dist\MultiSync.exe)"
