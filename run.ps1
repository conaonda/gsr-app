$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m uvicorn gsr.server:app --host 127.0.0.1 --port 8765
