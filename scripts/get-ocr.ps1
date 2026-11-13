$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force tessdata | Out-Null
$base = 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0'
Invoke-WebRequest -Uri "$base/ces.traineddata" -OutFile tessdata/ces.traineddata
$hash = (Get-FileHash tessdata/ces.traineddata -Algorithm SHA256).Hash
if ($hash -ne '934BCAF97EF3348413263331131C9FA7F55F30DB333C711929C124FB635F7E1B') {
    Remove-Item -LiteralPath tessdata/ces.traineddata
    throw 'Unexpected SHA256 for Czech OCR data.'
}
Invoke-WebRequest -Uri "$base/LICENSE" -OutFile tessdata/LICENSE
