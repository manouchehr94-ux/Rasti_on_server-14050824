param(
    [string]$AdminTarget = 'root@87.107.9.4',
    [ValidateRange(1,65535)][int]$AdminPort = 22,
    [string]$Baseline = '86742072855eb0d937a63be0c7662919d0dbef87',
    [string]$KeyDirectory = '',
    [switch]$Apply
)

# Run locally on Windows. Default is a read-only production plan.
# Private keys never leave the laptop through this script.
$ErrorActionPreference = 'Stop'
if ($AdminTarget -notmatch '^root@[A-Za-z0-9.-]+$') { throw 'Invalid administrator SSH target.' }
if ($Baseline -notmatch '^[0-9a-f]{40}$') { throw 'A full baseline commit is required.' }
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$backupHome = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'RastiSi-Server-Backup-PRIVATE'
if (!$KeyDirectory) { $KeyDirectory = Join-Path $backupHome 'deployment-keys' }
$KeyDirectory = [IO.Path]::GetFullPath($KeyDirectory)
if ($KeyDirectory.TrimEnd('\') -eq $repo.TrimEnd('\') -or
    $KeyDirectory.StartsWith($repo.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Private key directory must be outside the source repository.'
}
New-Item -ItemType Directory -Force -Path $KeyDirectory | Out-Null
$deployKey = Join-Path $KeyDirectory 'github-actions-ed25519'
$backupKey = Join-Path $KeyDirectory 'laptop-backup-signing-ed25519'

foreach ($tool in @('git','ssh','scp','ssh-keygen','tar')) {
    if (!(Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Required command is missing: $tool" }
}
git -C $repo cat-file -e "${Baseline}^{commit}"
if ($LASTEXITCODE -ne 0) { throw 'The original production import commit is missing.' }

if (!(Test-Path -LiteralPath $deployKey)) {
    # ProcessStartInfo preserves the empty passphrase argument on Windows PowerShell 5.
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = (Get-Command ssh-keygen).Source
    $start.Arguments = '-q -t ed25519 -N "" -f "' + $deployKey + '" -C rastisi-github-actions'
    $start.UseShellExecute = $false
    $process = [Diagnostics.Process]::Start($start)
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw 'Generating deployment key failed.' }
    $process.Dispose()
}
if (!(Test-Path -LiteralPath $backupKey)) {
    Write-Host 'Creating the separate laptop signing key; choose its passphrase in the local prompt.'
    ssh-keygen -q -t ed25519 -f $backupKey -C rastisi-laptop-backup
    if ($LASTEXITCODE -ne 0) { throw 'Generating backup signing key failed.' }
}
foreach ($key in @($deployKey,$backupKey)) {
    if (!(Test-Path -LiteralPath ($key + '.pub'))) { throw "Public key is missing: $key.pub" }
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$localSetup = Join-Path $backupHome ("cicd-bootstrap-" + $stamp)
New-Item -ItemType Directory -Path $localSetup | Out-Null
$baselineZip = Join-Path $localSetup 'baseline.zip'
$manifestPath = Join-Path $localSetup 'bootstrap-source.json'
$toolArchive = Join-Path $localSetup 'tooling.tar.gz'
git -C $repo archive --format=zip "--output=$baselineZip" $Baseline -- apps deploy shop_core static templates manage.py requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Exporting baseline failed.' }

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($baselineZip)
$files = [ordered]@{}
try {
    foreach ($entry in $zip.Entries) {
        if ($entry.FullName.EndsWith('/')) { continue }
        $stream = $entry.Open()
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { $files[$entry.FullName] = ([BitConverter]::ToString($hasher.ComputeHash($stream))).Replace('-','').ToLowerInvariant() }
        finally { $stream.Dispose(); $hasher.Dispose() }
    }
} finally { $zip.Dispose() }
if ($files.Count -eq 0) { throw 'Baseline manifest is empty.' }
$manifest = [ordered]@{schema=1; sha=$Baseline; files=$files}
$utf8 = New-Object Text.UTF8Encoding($false)
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 4), $utf8)
Remove-Item -LiteralPath $baselineZip

tar -czf $toolArchive --exclude=__pycache__ --exclude=*.pyc -C $repo deploy/release deploy/backup
if ($LASTEXITCODE -ne 0) { throw 'Packaging tooling failed.' }
$expectedHash = (Get-FileHash -LiteralPath $toolArchive -Algorithm SHA256).Hash.ToLowerInvariant()
$remoteSetup = '/tmp/rastisi-cicd-setup-' + $stamp
ssh -T -p $AdminPort $AdminTarget "install -d -m 700 $remoteSetup"
if ($LASTEXITCODE -ne 0) { throw 'Creating temporary setup directory failed.' }
scp -P $AdminPort $toolArchive $manifestPath "${AdminTarget}:$remoteSetup/"
if ($LASTEXITCODE -ne 0) { throw 'Uploading tooling failed.' }
scp -P $AdminPort ($deployKey + '.pub') "${AdminTarget}:$remoteSetup/github-deploy.pub"
if ($LASTEXITCODE -ne 0) { throw 'Uploading deploy public key failed.' }
scp -P $AdminPort ($backupKey + '.pub') "${AdminTarget}:$remoteSetup/laptop-backup.pub"
if ($LASTEXITCODE -ne 0) { throw 'Uploading backup public key failed.' }
$hashLine = ssh -T -p $AdminPort $AdminTarget "sha256sum $remoteSetup/tooling.tar.gz"
if ($LASTEXITCODE -ne 0 -or (($hashLine -split '\s+')[0] -ne $expectedHash)) { throw 'Tooling transfer hash mismatch.' }

$command = "cd $remoteSetup && tar -xzf tooling.tar.gz && python3 deploy/release/bootstrap_existing.py --manifest bootstrap-source.json --backup-public-key laptop-backup.pub --deploy-public-key github-deploy.pub"
if ($Apply) { $command += ' --apply' }
ssh -T -p $AdminPort $AdminTarget $command
if ($LASTEXITCODE -ne 0) { throw 'Bootstrap stopped; send its non-secret output and inspect before retrying.' }
if ($Apply) { Write-Host 'PRODUCTION_BOOTSTRAP_APPLIED' }
else { Write-Host 'PRODUCTION_BOOTSTRAP_PLAN_COMPLETE: no service paths were changed.' }
Write-Host "Laptop signing key location: $backupKey"
Write-Host "Deployment key location (for GitHub environment secret, never for chat): $deployKey"

# Obtain the public host key over the already trusted administrator connection.
# Never replace this with an unauthenticated ssh-keyscan bootstrap.
$hostKey = ssh -T -p $AdminPort $AdminTarget 'cat /etc/ssh/ssh_host_ed25519_key.pub'
if ($LASTEXITCODE -ne 0) { throw 'Reading public host key failed.' }
$hostKeyParts = ($hostKey -join ' ').Trim() -split '\s+'
if ($hostKeyParts.Count -lt 2 -or $hostKeyParts[0] -ne 'ssh-ed25519') {
    throw 'Unexpected SSH host public key format.'
}
$sshServerName = $AdminTarget.Substring(5)
$knownHostName = $sshServerName
if ($AdminPort -ne 22) { $knownHostName = '[' + $sshServerName + ']:' + $AdminPort }
$variables = [ordered]@{
    DEPLOY_HOST = $sshServerName
    DEPLOY_USER = 'rastisi-deploy'
    DEPLOY_PORT = [string]$AdminPort
    DEPLOY_KNOWN_HOSTS = $knownHostName + ' ' + $hostKeyParts[0] + ' ' + $hostKeyParts[1]
    PRODUCTION_URL = 'https://rastisi.ir'
    RASTISI_PYTHON_VERSION = '3.12'
}
$variablesFile = Join-Path $localSetup 'github-production-variables.json'
[IO.File]::WriteAllText($variablesFile, ($variables | ConvertTo-Json), $utf8)
Write-Host "Non-secret GitHub production variables: $variablesFile"
