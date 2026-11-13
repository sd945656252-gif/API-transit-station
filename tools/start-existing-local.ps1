param([switch]$InstallAutoStart, [ValidateRange(15, 600)][int]$TimeoutSeconds = 180)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$stateDirectory = Join-Path $root '.managed-updater'
[IO.Directory]::CreateDirectory($stateDirectory) | Out-Null
$utf8 = New-Object Text.UTF8Encoding($false)
$logPath = Join-Path $stateDirectory 'startup.log'

function Write-Status([string]$Message) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    [IO.File]::AppendAllText($logPath, $line + [Environment]::NewLine, $utf8)
    Write-Host $line
}

if ($InstallAutoStart) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) 'Sub2API.lnk'))
    $shortcut.TargetPath = Join-Path $PSHOME 'powershell.exe'
    $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ' + [char]34 + $PSCommandPath + [char]34
    $shortcut.WorkingDirectory = $root
    $shortcut.WindowStyle = 7
    $shortcut.Save()
    Write-Status 'Installed independent Windows login startup.'
}

$mutex = New-Object Threading.Mutex($false, 'Sub2APIOrderedStartup')
$locked = $false
try {
    $locked = $mutex.WaitOne(0)
    if (-not $locked) { Write-Status 'Another startup is already running.'; return }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        $env:Path += ';' + (Join-Path $env:ProgramFiles 'Docker/Docker/resources/bin')
    }
    $engineDeadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $desktopRequested = $false
    function Test-EngineReady {
        $ErrorActionPreference = 'Continue'
        & docker info --format '{{.ServerVersion}}' *> $null
        return ($LASTEXITCODE -eq 0)
    }
    while (-not (Test-EngineReady)) {
        if (-not $desktopRequested -and -not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) {
            $desktop = Join-Path $env:ProgramFiles 'Docker/Docker/Docker Desktop.exe'
            if (-not (Test-Path -LiteralPath $desktop)) { throw 'Docker Desktop is not installed.' }
            Start-Process -FilePath $desktop -WindowStyle Hidden
            $desktopRequested = $true
        }
        if ((Get-Date) -ge $engineDeadline) { throw 'Docker engine did not become ready before the startup deadline.' }
        Start-Sleep -Seconds 3
    }
    Write-Status 'Docker engine is ready.'

    function Read-State([string]$Name) {
        $json = & docker inspect --format '{{json .State}}' $Name 2>$null
        if ($LASTEXITCODE -ne 0) { throw ('Required existing container is missing: ' + $Name) }
        return ($json | ConvertFrom-Json)
    }
    function Start-Healthy([string]$Name) {
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        $attempts = 0
        while ($true) {
            $status = Read-State $Name
            if ($status.Running -and $status.Health.Status -eq 'healthy') {
                Write-Status ($Name + ' is healthy.'); return
            }
            if (-not $status.Running -and $attempts -lt 3) {
                & docker start $Name | Out-Null
                if ($LASTEXITCODE -ne 0) { throw ('Container startup failed: ' + $Name) }
                $attempts++
            }
            if ((Get-Date) -ge $deadline) { throw ('Health check timed out: ' + $Name) }
            Start-Sleep -Seconds 3
        }
    }
    # Exact names only: never Compose-group start or start previous containers.
    Start-Healthy 'sub2api-postgres'
    Start-Healthy 'sub2api-redis'
    foreach ($name in @('sub2api-storage', 'sub2api-updater')) {
        $exists = & docker container ls -a --filter ('name=^/' + $name + '$') --format '{{.Names}}'
        if ($exists -eq $name) {
            $status = Read-State $name
            if (-not $status.Running) {
                & docker start $name | Out-Null
                if ($LASTEXITCODE -ne 0) { throw ('Container startup failed: ' + $name) }
            }
        }
    }
    Start-Healthy 'sub2api'
    $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8080/health' -TimeoutSec 10
    if ($response.StatusCode -ne 200) { throw 'API health endpoint did not return HTTP 200.' }
    Write-Status 'API is ready at http://127.0.0.1:8080/home'
} catch {
    Write-Status ('FAILED: ' + $_.Exception.Message)
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
