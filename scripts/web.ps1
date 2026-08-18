param(
  [string]$HostName = "127.0.0.1",
  [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$Python = $env:PYTHON_EXE

if (-not $Python) {
  $ProjectPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
  $LegacyPython = "D:\hundsun\software\QwenPaw\python.exe"
  if (Test-Path $ProjectPython) {
    $Python = $ProjectPython
  } elseif (Test-Path $LegacyPython) {
    $Python = $LegacyPython
  } else {
    $Python = "python"
  }
}

& $Python -B -m universal_entry_agent web --host $HostName --port $Port
