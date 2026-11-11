param([string]$RuntimeRoot = (Split-Path -Parent $PSScriptRoot), [switch]$SkipBuild)

$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path -Parent $PSScriptRoot
$secretDir = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) '.managed-updater'
New-Item -ItemType Directory -Path $secretDir -Force | Out-Null
# Credentials stay on this computer, outside Git and Docker build contexts.
if ($IsWindows -or $env:OS -eq 'Windows_NT') {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls $secretDir /inheritance:r /grant:r "${identity}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot restrict credential directory access.' }
}
$utf8 = New-Object Text.UTF8Encoding($false)
$updaterFile = Join-Path $secretDir 'updater-token'
if (-not (Test-Path -LiteralPath $updaterFile)) {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    [IO.File]::WriteAllText($updaterFile, [Convert]::ToBase64String($bytes), $utf8)
}
$githubFile = Join-Path $secretDir 'github-token'
if (-not (Test-Path -LiteralPath $githubFile)) {
    $credential = & gh auth token
    if ($LASTEXITCODE -ne 0 -or -not $credential) { throw 'GitHub CLI authentication is required.' }
    [IO.File]::WriteAllText($githubFile, $credential.Trim(), $utf8)
    $credential = $null
}
$override = @{
    services = @{ sub2api = @{
        environment = @{ MANAGED_UPDATER_URL = 'http://sub2api-updater:8099'; MANAGED_UPDATER_TOKEN_FILE = '/run/secrets/updater_token' }
        secrets = @('updater_token')
    } }
    secrets = @{ updater_token = @{ file = $updaterFile } }
}
[IO.File]::WriteAllText((Join-Path $secretDir 'app.override.json'), ($override | ConvertTo-Json -Depth 8), $utf8)
if (-not $SkipBuild) {
    & docker build -t sub2api-managed-updater:local -f (Join-Path $sourceRoot 'deploy/managed-updater/Dockerfile') $sourceRoot
    if ($LASTEXITCODE -ne 0) { throw 'Updater build failed; current application is unchanged.' }
}
$env:UPDATER_SECRETS_DIR = $secretDir
& docker compose -p sub2api-managed -f (Join-Path $sourceRoot 'deploy/managed-updater/compose.yaml') up -d --no-build --wait
if ($LASTEXITCODE -ne 0) { throw 'Updater did not start; current application is unchanged.' }
Write-Host 'Private updater is running. Credentials were not uploaded. Submit an update from the administrator panel after the initial application deployment.'
