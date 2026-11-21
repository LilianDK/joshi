# joshi – hook for PowerShell (Windows PowerShell 5.1 and PowerShell 7)
# Load it at the END of your profile ($PROFILE) – after oh-my-posh/starship etc.:
#   . "C:\path\to\joshi\hooks\joshi.ps1"
# (install.ps1 does this for you.)
#
# Options (environment variables, set before loading):
#   $env:JOSHI_URL            = "http://127.0.0.1:8765"
#   $env:JOSHI_CAPTURE_OUTPUT = "0"   # commands only, no output
#   $env:JOSHI_DISABLE        = "1"   # hook off

if ($env:JOSHI_DISABLE -eq "1" -or $global:__sd_loaded) { return }
$global:__sd_loaded = $true

$global:__sd_url     = if ($env:JOSHI_URL) { $env:JOSHI_URL.TrimEnd('/') } else { "http://127.0.0.1:8765" }
$global:__sd_dir     = Join-Path $HOME ".joshi"
$global:__sd_token   = try { (Get-Content (Join-Path $__sd_dir "token") -ErrorAction Stop -Raw).Trim() } catch { "" }
$global:__sd_shell   = if ($PSVersionTable.PSEdition -eq "Core") { "pwsh" } else { "powershell" }
$global:__sd_session = "$PID-$(Get-Random)"
$global:__sd_lastId  = (Get-History -Count 1).Id
$global:__sd_offset  = 0L
$global:__sd_transcript = $null

Add-Type -AssemblyName System.Net.Http -ErrorAction SilentlyContinue
$global:__sd_http = New-Object System.Net.Http.HttpClient
$global:__sd_http.Timeout = [TimeSpan]::FromSeconds(5)

# --- capture output via a transcript ---------------------------------------
if ($env:JOSHI_CAPTURE_OUTPUT -ne "0") {
    New-Item -ItemType Directory -Force -Path $__sd_dir | Out-Null
    $global:__sd_transcript = Join-Path $__sd_dir "ps-transcript-$PID.txt"
    try {
        Start-Transcript -Path $__sd_transcript -Force -ErrorAction Stop | Out-Null
        $global:__sd_offset = (Get-Item $__sd_transcript).Length
        Register-EngineEvent -SourceIdentifier PowerShell.Exiting -SupportEvent -Action {
            try { Stop-Transcript | Out-Null } catch {}
            Remove-Item $global:__sd_transcript -ErrorAction SilentlyContinue
        } | Out-Null
    } catch { $global:__sd_transcript = $null }
}

function global:__sd_ReadOutput {
    if (-not $global:__sd_transcript) { return $null }
    try {
        $fs = [System.IO.File]::Open($global:__sd_transcript, 'Open', 'Read', 'ReadWrite')
        try {
            $len = $fs.Length
            if ($len -le $global:__sd_offset) { $global:__sd_offset = $len; return $null }
            $n = [Math]::Min($len - $global:__sd_offset, 65536)
            $buf = New-Object byte[] $n
            [void]$fs.Seek($global:__sd_offset, 'Begin')
            [void]$fs.Read($buf, 0, $n)
            $global:__sd_offset = $len
        } finally { $fs.Dispose() }
        $text = [System.Text.Encoding]::UTF8.GetString($buf)
        $lines = $text -split "\r?\n"
        # first line is prompt + command ("PS C:\> command") – drop it
        if ($lines.Count -gt 0 -and $lines[0] -match '^PS .*>') { $lines = $lines[1..($lines.Count - 1)] }
        elseif ($lines.Count -gt 1 -and $lines[0] -eq '' -and $lines[1] -match '^PS .*>') { $lines = $lines[2..($lines.Count - 1)] }
        $out = ($lines | Where-Object { $_ -notmatch '^\*{10,}$' }) -join "`n"
        return $out.Trim()
    } catch { return $null }
}

function global:__sd_Send($h, [int]$exitCode) {
    try {
        $start = [DateTimeOffset]::new($h.StartExecutionTime).ToUnixTimeMilliseconds()
        $dur   = [int]($h.EndExecutionTime - $h.StartExecutionTime).TotalMilliseconds
        $output = __sd_ReadOutput
        # With Atuin, Atuin records the command; joshi adds the output and links both via
        # Atuin's session id. (If no Atuin entry shows up, joshi records the command itself.)
        $path = if ($env:ATUIN_SESSION) { "/api/outputs" } else { "/api/events" }
        $session = if ($env:ATUIN_SESSION) { $env:ATUIN_SESSION } else { $global:__sd_session }
        $body = @{
            shell       = $global:__sd_shell
            host        = $env:COMPUTERNAME
            session     = $session
            cwd         = (Get-Location).Path
            command     = $h.CommandLine
            exit_code   = $exitCode
            duration_ms = $dur
            started_at  = $start
            output      = $output
        } | ConvertTo-Json -Compress
        $content = New-Object System.Net.Http.StringContent($body, [System.Text.Encoding]::UTF8, "application/json")
        $req = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Post, "$($global:__sd_url)$path")
        $req.Content = $content
        if ($global:__sd_token) { [void]$req.Headers.TryAddWithoutValidation("X-Joshi-Token", $global:__sd_token) }
        [void]$global:__sd_http.SendAsync($req)   # do not wait -> prompt stays fast
    } catch {}
}

# --- wrap the prompt function ----------------------------------------------
$global:__sd_origPrompt = $function:prompt
function global:prompt {
    $ok = $?
    $nativeExit = $global:LASTEXITCODE
    try {
        $h = Get-History -Count 1
        if ($h -and $h.Id -ne $global:__sd_lastId) {
            $global:__sd_lastId = $h.Id
            $ec = if ($ok) { 0 } elseif ($nativeExit) { [int]$nativeExit } else { 1 }
            __sd_Send $h $ec
        } elseif ($global:__sd_transcript) {
            [void](__sd_ReadOutput)   # advance the offset, e.g. after an empty input line
        }
    } catch {}
    $global:LASTEXITCODE = $nativeExit
    if ($global:__sd_origPrompt) { & $global:__sd_origPrompt } else { "PS $($executionContext.SessionState.Path.CurrentLocation)$('>' * ($nestedPromptLevel + 1)) " }
}
