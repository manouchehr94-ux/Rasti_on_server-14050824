param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{40}$')][string]$Commit,
    [string]$QaHome = (Join-Path $env:LOCALAPPDATA 'RastiSi-Integrated-QA-f15c453f-loopback'),
    [string]$Ref = 'feat/rastisi-stability-industry-mobile',
    [string]$Chromium,
    [string]$PreviousRun,
    [string[]]$Group
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $QaHome 'venv\Scripts\python.exe'
$existing = Join-Path $QaHome 'app'
$repo = 'https://github.com/manouchehr94-ux/Rasti_on_server-14050824.git'
if ($PreviousRun) {
    $existing = Join-Path $PreviousRun 'source'
    $previousSummary = Join-Path $PreviousRun 'report\summary.json'
    if (-not (Test-Path -LiteralPath (Join-Path $existing '.git')) -or
        -not (Test-Path -LiteralPath $previousSummary -PathType Leaf)) {
        throw 'The previous source checkout or summary is missing.'
    }
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "QA Python not found. Pass the existing QA environment with -QaHome."
}

$run = Join-Path $env:LOCALAPPDATA ("RastiSi-Acceptance\" + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$app = Join-Path $run 'source'
$output = Join-Path $run 'report'
New-Item -ItemType Directory -Path $run | Out-Null

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program exited with code $LASTEXITCODE." }
}

$local = $false
try {
    $kind = git -C $existing cat-file -t $Commit 2>$null
    $local = $LASTEXITCODE -eq 0 -and $kind -eq 'commit'
} catch { $local = $false }
if ($local -or $PreviousRun) {
    Invoke-Checked git @('clone','--local','--no-hardlinks','--no-checkout','--',$existing,$app)
    if (-not $local) {
        Invoke-Checked git @('-C',$app,'fetch','--no-tags',$repo,$Commit)
    }
} else {
    Invoke-Checked git @('clone','--no-checkout','--single-branch','--branch',$Ref,$repo,$app)
}
Invoke-Checked git @('-C',$app,'checkout','--detach',$Commit)
$head = git -C $app rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $head -cne $Commit) { throw 'Unexpected acceptance SHA.' }

if (-not $Chromium) {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    )
    $Chromium = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $Chromium) { throw 'Pass the existing Edge or Chrome executable with -Chromium.' }

$oldPythonPath = $env:PYTHONPATH
$oldPythonHome = $env:PYTHONHOME
try {
    Remove-Item Env:PYTHONPATH,Env:PYTHONHOME -ErrorAction SilentlyContinue
    if ($PreviousRun) {
        $previousDriver = Join-Path $PreviousRun 'browser-driver'
        if (Test-Path -LiteralPath (Join-Path $previousDriver 'playwright') -PathType Container) {
            $env:PYTHONPATH = $previousDriver
        }
    }
    $hasDriver = $false
    try {
        & $python -c 'import playwright.sync_api' 2>$null
        $hasDriver = $LASTEXITCODE -eq 0
    } catch { $hasDriver = $false }
    if (-not $hasDriver) {
        $driver = Join-Path $run 'browser-driver'
        Invoke-Checked $python @('-m','pip','install','--disable-pip-version-check','--target',$driver,'-r',
            (Join-Path $app 'tools\qa\requirements-management-browser.txt'))
        $env:PYTHONPATH = $driver
    }
    Write-Host "Acceptance report: $output"
    $argsForRunner = @((Join-Path $app 'tools\qa\run_acceptance.py'),'--output',$output,'--chromium',$Chromium)
    foreach ($name in $Group) { $argsForRunner += @('--group',$name) }
    if ($PreviousRun) { $argsForRunner += @('--previous-summary',$previousSummary) }
    Invoke-Checked $python $argsForRunner
} finally {
    $env:PYTHONPATH = $oldPythonPath
    $env:PYTHONHOME = $oldPythonHome
}
