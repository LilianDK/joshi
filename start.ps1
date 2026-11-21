# Starts the joshi server (creates a virtual environment on first run).
#   .\start.ps1              -> start the server. If WSL is installed it is also reachable from WSL
#                              (requests not from localhost need the token from ~/.joshi/token)
#   .\start.ps1 -LocalOnly   -> reachable from localhost only
param([switch]$LocalOnly, [int]$Port = 8765)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Test-Python([string]$exe, [string[]]$pre) {
    # Checks whether $exe is a real Python >= 3.10 (not the Microsoft Store placeholder)
    if (-not $pre) { $pre = @() }
    try {
        $out = & $exe @pre -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq "True")
    } catch { return $false }
}

function Find-Python {
    $candidates = New-Object System.Collections.ArrayList
    if (Get-Command py -ErrorAction SilentlyContinue) {
        [void]$candidates.Add([pscustomobject]@{ Exe = "py"; Pre = @("-3") })
    }
    foreach ($name in "python", "python3") {
        foreach ($c in @(Get-Command $name -All -CommandType Application -ErrorAction SilentlyContinue)) {
            if ($c.Source -notlike "*\WindowsApps\*") {
                [void]$candidates.Add([pscustomobject]@{ Exe = $c.Source; Pre = @() })
            }
        }
    }
    # Freshly installed via winget, but PATH in this window is still stale:
    foreach ($r in @("$env:LOCALAPPDATA\Programs\Python", $env:ProgramFiles)) {
        if ($r -and (Test-Path $r)) {
            Get-ChildItem $r -Directory -Filter "Python3*" -ErrorAction SilentlyContinue |
                Sort-Object Name -Descending | ForEach-Object {
                    $p = Join-Path $_.FullName "python.exe"
                    if (Test-Path $p) { [void]$candidates.Add([pscustomobject]@{ Exe = $p; Pre = @() }) }
                }
        }
    }
    foreach ($c in $candidates) {
        if (Test-Python $c.Exe $c.Pre) { return $c }
    }
    return $null
}

$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

# Remove a half-created venv from a failed attempt
if ((Test-Path ".venv") -and -not (Test-Path $venvPy)) {
    Remove-Item ".venv" -Recurse -Force
}

if (-not (Test-Path $venvPy)) {
    $py = Find-Python
    if (-not $py) {
        Write-Host ""
        Write-Host "No Python (3.10 or newer) found." -ForegroundColor Red
        Write-Host "'python' on this machine is only the Microsoft Store placeholder."
        Write-Host "Install Python, e.g. with:"
        Write-Host "    winget install -e --id Python.Python.3.12" -ForegroundColor Yellow
        Write-Host "Then open a NEW PowerShell window and run .\start.ps1 again."
        exit 1
    }
    $pre = $py.Pre
    Write-Host "Creating Python virtual environment (using $($py.Exe) $($pre -join ' ')) ..."
    & $py.Exe @pre -m venv .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) {
        Write-Host "Creating the virtual environment failed (see above)." -ForegroundColor Red
        if (Test-Path ".venv") { Remove-Item ".venv" -Recurse -Force }
        exit 1
    }
    Write-Host "Installing dependencies ..."
    & $venvPy -m pip install --quiet --upgrade pip
    & $venvPy -m pip install --quiet -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing dependencies failed (see above)." -ForegroundColor Red
        Remove-Item ".venv" -Recurse -Force
        exit 1
    }
}

$hasWsl = (Test-Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss") -and
          @(Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss" -ErrorAction SilentlyContinue).Count -gt 0
$bind = if ($hasWsl -and -not $LocalOnly) { "0.0.0.0" } else { "127.0.0.1" }
Write-Host ""
Write-Host "joshi is running at http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "Keep this window open (Ctrl+C stops the server). Nothing else will appear here."
if ($bind -eq "0.0.0.0") { Write-Host "WSL found: the server is also reachable from WSL." }
Write-Host "To capture commands: run .\install.ps1 once in a SECOND window,"
Write-Host "then open NEW shell windows (PowerShell, Git Bash, WSL) and work there."
Write-Host ""
Start-Process "http://127.0.0.1:$Port"
& $venvPy -m uvicorn joshi.app:app --host $bind --port $Port --log-level warning
