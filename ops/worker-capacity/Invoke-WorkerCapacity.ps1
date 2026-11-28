param(
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*$')][string]$Destination,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{40}$')][string]$Commit,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$CapacitySha256,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$ShellSha256,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$SqlSha256,
    [ValidatePattern('^[A-Za-z0-9_.-]+$')][string]$PgService
)
$ErrorActionPreference = 'Stop'
$expected = @{
    'capacity.py' = $CapacitySha256
    'postgres-capacity.sh' = $ShellSha256
    'postgres-capacity.sql' = $SqlSha256
}
$temp = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $temp | Out-Null
try {
    $archive = Join-Path $temp 'source.tar'
    $paths = @($expected.Keys | ForEach-Object { "ops/worker-capacity/$_" })
    git archive --format=tar "--output=$archive" $Commit -- @paths
    if ($LASTEXITCODE -ne 0) { throw 'Git archive failed' }
    tar -xf $archive -C $temp
    if ($LASTEXITCODE -ne 0) { throw 'Archive extraction failed' }
    $payload = @{}
    foreach ($name in $expected.Keys) {
        $path = Join-Path $temp "ops/worker-capacity/$name"
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $expected[$name]) {
            throw "SHA-256 mismatch for $name"
        }
        $payload[$name] = [Convert]::ToBase64String([IO.File]::ReadAllBytes($path))
    }
    $remote = @"
import base64
import os
import subprocess
import sys

capacity = base64.b64decode('$($payload['capacity.py'])')
shell = base64.b64decode('$($payload['postgres-capacity.sh'])').decode('ascii')
sql = base64.b64decode('$($payload['postgres-capacity.sql'])')
status = subprocess.run([sys.executable, '-I', '-'], input=capacity).returncode
if status:
    raise SystemExit(status)
service = '$PgService'
if service:
    env = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
    env['PGSERVICE'] = service
    raise SystemExit(subprocess.run(['/bin/sh', '-c', shell], input=sql, env=env).returncode)
"@
    $remote | ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes $Destination 'python3 -I -'
    if ($LASTEXITCODE -ne 0) { throw 'Remote probe failed' }
}
finally {
    Remove-Item -LiteralPath $temp -Recurse -Force
}
