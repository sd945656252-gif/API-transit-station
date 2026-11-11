param([switch]$EnableAdapter)

$ErrorActionPreference = "Stop"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $dockerBin = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin'
    if (Test-Path (Join-Path $dockerBin 'docker.exe')) {
        $env:Path += ";$dockerBin"
    } else {
        throw "Docker CLI is not installed."
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Review passwords and keys before continuing." -ForegroundColor Yellow
}

$localConfig = @{}
foreach ($line in Get-Content '.env') {
    if ($line -match '^\s*([A-Z][A-Z0-9_]*)=(.*)$') {
        $localConfig[$matches[1]] = $matches[2].Trim().Trim('"').Trim("'")
    }
}

# Prevent a local-only configuration from being reused on a public host with
# an unauthenticated Redis instance or placeholder cryptographic secrets.
$bindHost = if ($env:BIND_HOST) { $env:BIND_HOST } else { $localConfig['BIND_HOST'] }
$isLoopback = $bindHost -in @('127.0.0.1', 'localhost', '::1')
if (-not $isLoopback) {
    foreach ($key in @('POSTGRES_PASSWORD', 'REDIS_PASSWORD', 'JWT_SECRET', 'TOTP_ENCRYPTION_KEY')) {
        $value = [Environment]::GetEnvironmentVariable($key)
        if (-not $value) { $value = $localConfig[$key] }
        if (-not $value -or $value -match '^change[_-]?this|^your[_-]?') {
            throw "Configure a non-placeholder $key before binding the service to a non-loopback host."
        }
    }
}

if ($EnableAdapter) {
    $provider = if ($env:ADAPTER_PROVIDER) { $env:ADAPTER_PROVIDER } else { $localConfig['ADAPTER_PROVIDER'] }
    if (-not $provider -or $provider -eq 'mock') {
        throw 'Configure a real adapter provider before enabling the runtime gateway. Mock is for tests only.'
    }
    $required = switch ($provider) {
        'runninghub' { @('ADAPTER_GATEWAY_API_KEY', 'RUNNINGHUB_API_KEY') }
        'openai-compatible' { @('ADAPTER_GATEWAY_API_KEY', 'UPSTREAM_BASE_URL', 'UPSTREAM_API_KEY') }
        default { throw "Unsupported adapter provider: $provider" }
    }
    foreach ($key in $required) {
        $value = [Environment]::GetEnvironmentVariable($key)
        if (-not $value) { $value = $localConfig[$key] }
        if (-not $value -or $value -match '^change-this') {
            throw "Configure $key before enabling the adapter."
        }
    }
}

# Explicit files prevent accidentally deploying the stock image or another override.
$composeFiles = @('-f', 'docker-compose.yml', '-f', 'compose.override.yaml')
if (Test-Path '.managed-updater/app.override.json') { $composeFiles += @('-f', '.managed-updater/app.override.json') }
docker compose @composeFiles build sub2api
if ($LASTEXITCODE -ne 0) { throw 'Local application build failed; existing service was preserved.' }
docker compose @composeFiles up -d --no-deps --no-build --wait sub2api
if ($LASTEXITCODE -ne 0) { throw 'Application startup failed.' }

if ($EnableAdapter) {
    docker compose -f docker-compose.yml -f compose.override.yaml --profile adapter up -d --build --wait adapter-gateway
    if ($LASTEXITCODE -ne 0) { throw 'Adapter startup failed.' }
} else {
    docker compose -f docker-compose.yml -f compose.override.yaml --profile adapter stop adapter-gateway
    if ($LASTEXITCODE -ne 0) { throw 'Failed to stop the optional adapter.' }
}

$localPort = if ($env:SERVER_PORT) { $env:SERVER_PORT } elseif ($localConfig['SERVER_PORT']) { $localConfig['SERVER_PORT'] } else { "8080" }
try {
    $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$localPort/health" -TimeoutSec 10
    Write-Host "Sub2API health: $($health.StatusCode)" -ForegroundColor Green
} catch {
    throw "Sub2API health check failed: $($_.Exception.Message)"
}

docker compose ps
Write-Host "Local URL: http://127.0.0.1:$localPort" -ForegroundColor Cyan
if ($EnableAdapter) {
    Write-Host "Adapter: http://adapter-gateway:8090 (internal only)" -ForegroundColor Cyan
}
