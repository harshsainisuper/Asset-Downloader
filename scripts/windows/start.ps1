# Installs what YT Media Toolkit needs on Windows (first run only), then starts it.
# Run via start-windows.bat (double-click). Arguments are passed on to run_local.py,
# e.g. --tunnel for a public URL. Compatible with Windows PowerShell 5.1.

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Rest = @($args)
$WantTunnel = $Rest -contains '--tunnel'

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Install-Package([string]$Id, [string]$What, [string]$ManualUrl) {
    if (-not (Test-Command 'winget')) {
        throw "$What is not installed and 'winget' is unavailable. Install it from $ManualUrl, then run this again."
    }
    Write-Host "`n[setup] Installing $What (one time) ..." -ForegroundColor Cyan
    # --source winget: all packages used here live in the community catalogue. Without it,
    # a failing Microsoft Store source (common behind proxies) makes winget refuse to pick one.
    winget install --id $Id --exact --source winget --silent --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget could not install $What (exit code $LASTEXITCODE). Install it from $ManualUrl, then run this again."
    }
    Update-SessionPath
}

# Returns @{ Exe = ...; Args = @(...) } for a Python 3.11+ interpreter, or $null.
function Find-Python {
    # Windows PowerShell 5.1 turns a native program's redirected stderr into a terminating
    # error under 'Stop'; probing is expected to fail, so relax it inside this function.
    $ErrorActionPreference = 'Continue'
    $candidates = @(
        @{ Exe = 'py'; Args = @('-3') },
        @{ Exe = 'python'; Args = @() }
    )
    foreach ($candidate in $candidates) {
        if (-not (Test-Command $candidate.Exe)) { continue }
        $pyArgs = $candidate.Args
        # The Microsoft Store "python" alias prints a message and fails; that counts as not found.
        & $candidate.Exe @pyArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return $candidate }
    }
    return $null
}

try {
    Update-SessionPath

    $python = Find-Python
    if (-not $python) {
        Install-Package 'Python.Python.3.13' 'Python 3.13' 'https://www.python.org/downloads/windows/'
        $python = Find-Python
        if (-not $python) { throw 'Python was installed but cannot be found yet. Close this window and run start-windows.bat again.' }
    }

    if (-not ((Test-Command 'ffmpeg') -and (Test-Command 'ffprobe'))) {
        Install-Package 'Gyan.FFmpeg' 'FFmpeg' 'https://www.gyan.dev/ffmpeg/builds/'
        if (-not (Test-Command 'ffmpeg')) { throw 'FFmpeg was installed but cannot be found yet. Close this window and run start-windows.bat again.' }
    }

    # Node.js is only needed to build the web interface the first time.
    $uiBuilt = Test-Path (Join-Path $Root 'frontend\dist\index.html')
    if (-not $uiBuilt -and -not (Test-Command 'npm')) {
        Install-Package 'OpenJS.NodeJS.LTS' 'Node.js' 'https://nodejs.org/'
    }

    if ($WantTunnel -and -not (Test-Command 'cloudflared')) {
        Install-Package 'Cloudflare.cloudflared' 'Cloudflare Tunnel (cloudflared)' 'https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/'
    }

    $pyArgs = $python.Args
    $launcher = Join-Path $Root 'scripts\run_local.py'
    Write-Host "`n[setup] Starting YT Media Toolkit ..." -ForegroundColor Cyan
    & $python.Exe @pyArgs $launcher @Rest
    exit $LASTEXITCODE
}
catch {
    Write-Host "`n[setup] ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
