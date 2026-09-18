param(
    [ValidateSet('up', 'stop', 'status', 'logs')][string]$Action = 'up',
    [string]$ImportEnv,
    [switch]$Demo,
    [switch]$NoBuild,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot

function Invoke-Docker {
    param([string[]]$DockerArgs)
    & docker @DockerArgs
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed. See the message above; no data volumes were deleted." }
}

Invoke-Docker -DockerArgs @('info', '--format', '{{.OSType}}')
$configDirectory = Join-Path $projectRoot '.data/local'
$runtimeFile = Join-Path $configDirectory 'runtime.env'
$composeArgs = @('compose', '--project-name', 'sec-filing-local', '--env-file', $runtimeFile, '-f', 'infra/local/compose.yaml')
if ($Action -eq 'up') {
    New-Item -ItemType Directory -Path $configDirectory -Force | Out-Null
    if (-not $NoBuild) {
        Invoke-Docker -DockerArgs @('build', '-f', 'apps/backend/Dockerfile', '-t', 'sec-filing-agent-backend:local', '.')
    }
    $bootstrapArgs = @('run', '--rm', '--pull', 'never', '--user', '0:0', '--mount', "type=bind,source=$configDirectory,target=/config")
    if ($ImportEnv) {
        $importFile = (Resolve-Path -LiteralPath $ImportEnv).Path
        $bootstrapArgs += @('--mount', "type=bind,source=$importFile,target=/import.env,readonly")
    }
    $bootstrapArgs += @('--entrypoint', 'python', 'sec-filing-agent-backend:local', 'infra/local/bootstrap.py')
    if ($ImportEnv) { $bootstrapArgs += @('--import-env', '/import.env') }
    if ($Demo) { $bootstrapArgs += '--demo' }
    Invoke-Docker -DockerArgs $bootstrapArgs
    Invoke-Docker -DockerArgs ($composeArgs + @('config', '--quiet'))
    if (-not $NoBuild) { Invoke-Docker -DockerArgs ($composeArgs + @('build', 'web')) }
    Invoke-Docker -DockerArgs ($composeArgs + @('up', '-d', '--no-build', '--wait', '--wait-timeout', '300'))
    Write-Host 'Ready: https://localhost:8443 (local self-signed certificate).'
    Write-Host 'Model/SEC settings: .data/local/provider.env; re-run this script after editing.'
    if (-not $NoBrowser) { Start-Process 'https://localhost:8443' }
} else {
    if (-not (Test-Path -LiteralPath $runtimeFile)) { throw 'Run .\start-local.ps1 first.' }
    switch ($Action) {
        'stop' { Invoke-Docker -DockerArgs ($composeArgs + @('stop')) }
        'status' { Invoke-Docker -DockerArgs ($composeArgs + @('ps', '-a')) }
        'logs' { Invoke-Docker -DockerArgs ($composeArgs + @('logs', '--tail', '100', 'api', 'worker', 'dispatcher', 'reconciler', 'beat', 'web')) }
    }
}
