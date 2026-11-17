#!/usr/bin/env bash
# Download the Czech OCR data used by tests and verify its checksum (Linux/macOS).
set -euo pipefail
mkdir -p tessdata
base='https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0'
curl -sSfL -o tessdata/ces.traineddata "$base/ces.traineddata"
echo '934bcaf97ef3348413263331131c9fa7f55f30db333c711929c124fb635f7e1b  tessdata/ces.traineddata' | sha256sum -c -
curl -sSfL -o tessdata/LICENSE "$base/LICENSE"
