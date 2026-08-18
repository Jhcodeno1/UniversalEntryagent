param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$Args
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

if (-not $Args -or $Args.Count -eq 0) {
  $Args = @("chat")
}

& $Python -m universal_entry_agent @Args
