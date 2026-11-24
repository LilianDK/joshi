# shellSniffer installer: sets up capturing in ALL shells found on this machine:
#   - Windows PowerShell 5.1 and PowerShell 7 (console and VS Code)
#   - Git Bash
#   - all WSL distributions
# Safe to run repeatedly: previous entries are replaced.
#   .\install.ps1              install
#   .\install.ps1 -Uninstall   remove all entries again (Atuin itself stays installed)
#   .\install.ps1 -NoAtuin     without Atuin (shellSniffer then captures commands itself)
#
# With Atuin (default), Atuin records commands in PowerShell and WSL; shellSniffer reads them and its
# hook only adds the output. Git Bash is not supported by Atuin and keeps using the shellSniffer hook.
param([switch]$Uninstall, [switch]$NoAtuin)
$ErrorActionPreference = "Stop"
$root    = $PSScriptRoot
$marker  = '#\s*(shelldoc|docuduck|joshi|atuin-joshi|shellsniffer|atuin-shellsniffer)\s*$'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$done = @()

function Update-LineFile([string]$path, [string[]]$newLines, [bool]$bom) {
    $lines = @()
    if (Test-Path $path) { $lines = @(Get-Content -LiteralPath $path -Encoding UTF8) }
    $kept = @($lines | Where-Object { $_ -notmatch $marker })
    if (-not $Uninstall) { $kept += $newLines }
    $dir = Split-Path $path
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $enc = if ($bom) { New-Object System.Text.UTF8Encoding($true) } else { $utf8NoBom }
    $nl  = if ($bom) { "`r`n" } else { "`n" }
    [System.IO.File]::WriteAllText($path, (($kept -join $nl) + $nl), $enc)
}

# ---------------------------------------------------------------- Atuin (Windows)
function Update-Path { $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") }
$useAtuin = -not $NoAtuin -and -not $Uninstall
if ($useAtuin -and -not (Get-Command atuin -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Installing Atuin (winget) ..."
        winget install -e --id Atuinsh.Atuin --accept-source-agreements --accept-package-agreements
        Update-Path
    }
    if (-not (Get-Command atuin -ErrorAction SilentlyContinue)) {
        Write-Host "Atuin could not be installed - shellSniffer will capture PowerShell commands itself." -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------- PowerShell
$docs = [Environment]::GetFolderPath("MyDocuments")
$psHook = Join-Path $root "hooks\shellsniffer.ps1"
$psLine = ". `"$psHook`"  # shellsniffer"
$atuinPsLine = 'if (Get-Command atuin -ErrorAction SilentlyContinue) { try { atuin init powershell | Out-String | Invoke-Expression } catch {} }  # atuin-shellsniffer'
$profiles = @()
$profiles += Join-Path $docs "WindowsPowerShell\Microsoft.PowerShell_profile.ps1"
$profiles += Join-Path $docs "WindowsPowerShell\Microsoft.VSCode_profile.ps1"
if (Get-Command pwsh -ErrorAction SilentlyContinue) {
    $profiles += Join-Path $docs "PowerShell\Microsoft.PowerShell_profile.ps1"
    $profiles += Join-Path $docs "PowerShell\Microsoft.VSCode_profile.ps1"
}
foreach ($p in $profiles) {
    # Only touch VS Code profiles if they exist or VS Code is installed
    if ($p -like "*VSCode*" -and -not (Test-Path $p) -and -not (Get-Command code -ErrorAction SilentlyContinue)) { continue }
    $ownAtuin = (Test-Path $p) -and (Select-String -LiteralPath $p -Pattern 'atuin init' -Quiet) -and
                -not (Select-String -LiteralPath $p -Pattern 'atuin-(joshi|shellsniffer)' -Quiet)
    $add = @()
    if ($useAtuin -and -not $ownAtuin) { $add += $atuinPsLine }   # load Atuin before shellSniffer
    $add += $psLine
    Update-LineFile $p $add $true
    $done += "PowerShell: $p"
}

# ---------------------------------------------------------------- Git Bash
$gitBash = @("$env:ProgramFiles\Git\bin\bash.exe", "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($gitBash) {
    $drive = $root.Substring(0, 1).ToLower()
    $gbPath = "/$drive" + $root.Substring(2).Replace('\', '/') + "/hooks/shellsniffer.bash"
    $bashrc = Join-Path $HOME ".bashrc"
    Update-LineFile $bashrc @("source `"$gbPath`"  # shellsniffer") $false
    # Git Bash reads ~/.bash_profile on start – make sure it loads .bashrc
    $bp = Join-Path $HOME ".bash_profile"
    if (-not $Uninstall -and -not (Test-Path $bp)) {
        [System.IO.File]::WriteAllText($bp, "test -f ~/.bashrc && . ~/.bashrc`n", $utf8NoBom)
    }
    $done += "Git Bash: $bashrc"
}

# ---------------------------------------------------------------- WSL
$wslDistros = @()
if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
    $env:WSL_UTF8 = "1"
    $raw = (& wsl.exe -l -q 2>$null) -join "`n"
    $wslDistros = @($raw -replace "`0", "" -split "\r?\n" | ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and $_ -notmatch '\s' -and $_ -notmatch '^docker-desktop' })
}
foreach ($d in $wslDistros) {
    $drive = $root.Substring(0, 1).ToLower()
    $wPath = "/mnt/$drive" + $root.Substring(2).Replace('\', '/') + "/hooks/shellsniffer.bash"
    $atuinSetup = ""
    if ($useAtuin) {
        # Install Atuin (if missing) and add it with bash-preexec to .bashrc
        $atuinSetup = @'
if ! command -v atuin >/dev/null 2>&1 && [ ! -x "$HOME/.atuin/bin/atuin" ]; then
  echo "Installing Atuin ..."; curl --proto '=https' --tlsv1.2 -LsSf https://setup.atuin.sh | sh
fi
if ! grep -q 'atuin init bash' ~/.bashrc; then
  [ -f ~/.bash-preexec.sh ] || curl -fsSL https://raw.githubusercontent.com/rcaloras/bash-preexec/master/bash-preexec.sh -o ~/.bash-preexec.sh
  printf '%s\n' '[ -f "$HOME/.atuin/bin/env" ] && . "$HOME/.atuin/bin/env"  # atuin-shellsniffer' '[ -f ~/.bash-preexec.sh ] && source ~/.bash-preexec.sh  # atuin-shellsniffer' 'command -v atuin >/dev/null && eval "$(atuin init bash)"  # atuin-shellsniffer' >> ~/.bashrc
fi
command -v python3 >/dev/null || echo "WARNING: python3 is missing in this distribution - shellSniffer cannot read Atuin here (sudo apt install python3)"
'@
    }
    $add = if ($Uninstall) { "" } else { "printf '%s\n' 'source `"$wPath`"  # shellsniffer' >> ~/.bashrc" }
    $clean = if ($Uninstall) { "(shelldoc|docuduck|joshi|atuin-joshi|shellsniffer|atuin-shellsniffer)" } else { "(shelldoc|docuduck|joshi|shellsniffer)" }
    $script = "touch ~/.bashrc; sed -i -E '/#[[:space:]]*$clean[[:space:]]*`$/d' ~/.bashrc`n$atuinSetup`n$add"
    $b64 = [Convert]::ToBase64String($utf8NoBom.GetBytes($script))
    & wsl.exe -d $d -- bash -c "echo $b64 | base64 -d | bash"
    if ($LASTEXITCODE -eq 0) { $done += "WSL ($d): ~/.bashrc" } else { Write-Host "WSL ($d): could not update ~/.bashrc" -ForegroundColor Yellow }
}

# ---------------------------------------------------------------- Firewall for WSL
if ($wslDistros.Count -gt 0 -and -not $Uninstall) {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $rule = Get-NetFirewallRule -DisplayName "shellSniffer (WSL)", "joshi (WSL)" -ErrorAction SilentlyContinue
    if (-not $rule) {
        $cmd = 'New-NetFirewallRule -DisplayName "shellSniffer (WSL)" -Direction Inbound -Protocol TCP -LocalPort 8765 -RemoteAddress 172.16.0.0/12 -Action Allow -Profile Any'
        if ($isAdmin) {
            Invoke-Expression $cmd | Out-Null
            $done += "Firewall: rule 'shellSniffer (WSL)' for port 8765 from the WSL network"
        } else {
            Write-Host ""
            Write-Host "For WSL to reach the shellSniffer server, a firewall rule is needed." -ForegroundColor Yellow
            Write-Host "Either run this script once as Administrator, or run this in an admin PowerShell:"
            Write-Host "    $cmd" -ForegroundColor Yellow
        }
    }
}

Write-Host ""
if ($Uninstall) { Write-Host "shellSniffer entries removed from:" -ForegroundColor Green } else { Write-Host "shellSniffer is now watching:" -ForegroundColor Green }
$done | ForEach-Object { Write-Host "  - $_" }
if (-not $Uninstall) {
    Write-Host ""
    Write-Host "Open new shell windows (windows that are already open are not watched)."
    if ($wslDistros.Count -gt 0) { Write-Host "For WSL, start the server with .\start.ps1 (it is then reachable from WSL automatically)." }
}
