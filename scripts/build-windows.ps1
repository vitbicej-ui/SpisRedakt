$ErrorActionPreference = 'Stop'
python -m PyInstaller --noconfirm --windowed --name SpisRedakt start.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Copy-Item -Recurse -Force tessdata dist/SpisRedakt/
Copy-Item README.md, LICENSE, THIRD-PARTY.md dist/SpisRedakt/
$env:QT_QPA_PLATFORM = 'offscreen'
$test = Start-Process dist/SpisRedakt/SpisRedakt.exe -ArgumentList '--smoke-test' -PassThru -Wait -WindowStyle Hidden
if ($test.ExitCode -ne 0) { exit $test.ExitCode }
