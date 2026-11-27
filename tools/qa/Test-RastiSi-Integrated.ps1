param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{40}$')][string]$Commit,
    [string]$SourceRepo = 'D:\Projects\RastiSi\Rasti_on_server-14050824',
    [switch]$EnableWebPush
)
$ErrorActionPreference = 'Stop'
$qa = Join-Path $env:LOCALAPPDATA "RastiSi-Integrated-QA-$($Commit.Substring(0,8))"
$app = Join-Path $qa 'app'
$venv = Join-Path $qa 'venv'

function Invoke-Checked {
    param([string]$Program, [string[]]$NativeArgs)
    & $Program @NativeArgs
    if ($LASTEXITCODE -ne 0) { throw "$Program failed (exit $LASTEXITCODE)." }
}

if (Test-Path -LiteralPath $qa) { throw "QA directory already exists and was preserved: $qa" }
$pattern = '^(DJANGO_|RASTISI_|PAYMENTS?_|REDIS_|AWS_|DATABASE_URL$|PYTHONPATH$|PYTHONHOME$)'
$saved = @{}
Get-ChildItem Env: | Where-Object Name -match $pattern |
    ForEach-Object { $saved[$_.Name] = $_.Value }
$worker = $null
Push-Location
try {
    foreach ($name in $saved.Keys) { Remove-Item -LiteralPath "Env:$name" }
    New-Item -ItemType Directory -Path $qa | Out-Null
    $local = $false
    if (Test-Path -LiteralPath $SourceRepo) {
        try {
            $kind = git -C $SourceRepo cat-file -t $Commit 2>$null
            $local = $LASTEXITCODE -eq 0 -and $kind -eq 'commit'
        } catch { $local = $false }
    }
    if ($local) {
        Invoke-Checked git @('clone','--local','--no-hardlinks','--no-checkout','--',$SourceRepo,$app)
    } else {
        Invoke-Checked git @('clone','--no-checkout','--single-branch','--branch','feat/rastisi-stability-industry-mobile',
                  'https://github.com/manouchehr94-ux/Rasti_on_server-14050824.git',$app)
    }
    Set-Location -LiteralPath $app
    Invoke-Checked git @('checkout','--detach',$Commit)
    $head = git rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $head -cne $Commit) { throw 'Unexpected QA commit.' }
    $env:DJANGO_DEBUG = 'True'
    $env:DJANGO_SETTINGS_MODULE = 'shop_core.settings'
    $env:DJANGO_MEDIA_ROOT = Join-Path $qa 'media'
    $env:DJANGO_PRIVATE_MEDIA_ROOT = Join-Path $qa 'private_media'
    $env:RASTISI_QA_MODE = '1'
    $env:RASTISI_EMAIL_PROVIDER = 'fake'
    $env:RASTISI_OWNER_SMS_BACKEND = 'console'
    $env:RASTISI_OWNER_SMS_ALLOW_CONSOLE_OTP = '1'
    $env:RASTISI_DEV_OTP_CODE = '123456'

    Invoke-Checked py @('-3.12','-m','venv',$venv)
    $python = Join-Path $venv 'Scripts\python.exe'
    Invoke-Checked $python @('-m','pip','install','--disable-pip-version-check','-r','requirements.txt')
    $check = "from pathlib import Path; from django.conf import settings; d=settings.DATABASES; assert set(d)=={'default'} and d['default']['ENGINE']=='django.db.backends.sqlite3' and Path(d['default']['NAME']).resolve()==Path('db.sqlite3').resolve()"
    Invoke-Checked $python @('-c',$check)
    Invoke-Checked $python @('manage.py','migrate','--noinput')
    Invoke-Checked $python @('manage.py','ensure_industry_registry','--apply')
    Invoke-Checked $python @('manage.py','seed_qa_notifications','--password','Synthetic-QA-4827!','--port','8771')

    if ($EnableWebPush) {
        $keys = Invoke-Checked $python @('manage.py','generate_vapid_keys')
        $key = $keys | Where-Object { $_ -like 'RASTISI_VAPID_PRIVATE_KEY=*' }
        if (-not $key) { throw 'Local VAPID key generation failed.' }
        $env:RASTISI_VAPID_PRIVATE_KEY = $key.Substring('RASTISI_VAPID_PRIVATE_KEY='.Length)
        $env:RASTISI_VAPID_SUBJECT = 'mailto:qa@rastisi.test'
        $env:RASTISI_NOTIF_WEBPUSH_ENABLED = '1'
        $env:PAYMENT_CREDENTIAL_KEY = Invoke-Checked $python @('-c','from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
        $worker = Start-Process -FilePath $python -ArgumentList @('manage.py','qa_worker','--interval','5') -WorkingDirectory $app -NoNewWindow -PassThru
    }

    Invoke-Checked $python @('manage.py','runserver','127.0.0.1:8771','--noreload')
} finally {
    if ($worker -and -not $worker.HasExited) { Stop-Process -Id $worker.Id -ErrorAction SilentlyContinue }
    Get-ChildItem Env: | Where-Object Name -match $pattern |
        ForEach-Object { Remove-Item -LiteralPath "Env:$($_.Name)" }
    foreach ($name in $saved.Keys) { Set-Item -LiteralPath "Env:$name" -Value $saved[$name] }
    Pop-Location
}
