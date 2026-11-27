param(
    [Parameter(Mandatory=$true)][string]$LanIp,
    [string]$App,
    [string]$QaHome = (Join-Path $env:LOCALAPPDATA 'RastiSi-Integrated-QA-f15c453f-loopback'),
    [string]$Caddy = 'caddy.exe',
    [switch]$TemporaryFirewall
)
$ErrorActionPreference = 'Stop'
$sha = '993b879e3915b7fa2dd6ad9e805f6924f2c3d073'
if ($LanIp -notmatch '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)') { throw 'Use a private LAN IPv4 address.' }
$nic = Get-NetIPAddress -IPAddress $LanIp -AddressFamily IPv4
if (-not (Get-NetConnectionProfile -InterfaceIndex $nic.InterfaceIndex | Where-Object NetworkCategory -eq 'Private')) {
    throw 'Use a trusted Windows Private-profile network.'
}
if (-not $App) {
    $runs = Join-Path $env:LOCALAPPDATA 'RastiSi-Acceptance'
    foreach ($dir in (Get-ChildItem -LiteralPath $runs -Directory | Sort-Object LastWriteTime -Descending)) {
        $candidate = Join-Path $dir.FullName 'source'
        if (-not (Test-Path -LiteralPath (Join-Path $candidate '.git'))) { continue }
        $head = git -C $candidate rev-parse HEAD
        if ($LASTEXITCODE -eq 0 -and $head -ceq $sha) { $App = $candidate; break }
    }
}
if (-not $App) { throw 'Pass the approved acceptance checkout with -App.' }
$App = (Resolve-Path -LiteralPath $App).Path
$head = git -C $App rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $head -cne $sha) { throw 'Unexpected RastiSi SHA.' }
$changes = git -C $App status --porcelain --untracked-files=all
if ($LASTEXITCODE -ne 0 -or $changes) { throw 'Use the clean acceptance checkout.' }
$python = Join-Path $QaHome 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Existing QA Python not found.' }
$caddyExe = (Get-Command $Caddy -CommandType Application).Source
foreach ($port in 8772,8443) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use."
    }
}
$root = Join-Path $env:LOCALAPPDATA ('RastiSi-PhonePush-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$evidence = Join-Path $root 'result.jsonl'
$rule = 'RastiSi QA Push ' + [guid]::NewGuid().ToString('N')
$vars = @{}
$pattern = '^(DJANGO_|RASTISI_|PAYMENTS?_|REDIS_|AWS_|DATABASE_URL$|PYTHONPATH$|PYTHONHOME$)'
Get-ChildItem Env: | Where-Object Name -match $pattern | ForEach-Object { $vars[$_.Name] = $_.Value }
$web = $tls = $null
$firewallAdded = $false

function Run {
    param([string[]]$Arguments)
    & $python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "QA process failed with exit $LASTEXITCODE." }
}

Push-Location $App
try {
    foreach ($name in $vars.Keys) { Remove-Item -LiteralPath "Env:$name" }
    $env:PYTHONPATH = $root + [IO.Path]::PathSeparator + $App
    $env:DJANGO_DEBUG = '1'
    $env:DJANGO_SECRET_KEY = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    $env:DJANGO_SETTINGS_MODULE = 'phone_push_qa'
    $env:DJANGO_SECURE_PROXY_SSL_HEADER = 'X-Forwarded-Proto:https'
    $env:DJANGO_TRUSTED_PROXY_CIDRS = '127.0.0.1/32'
    $env:RASTISI_QA_MODE = '1'
    $env:RASTISI_EMAIL_PROVIDER = 'fake'
    $env:RASTISI_OWNER_SMS_BACKEND = 'console'
    $env:RASTISI_OWNER_SMS_ALLOW_CONSOLE_OTP = '1'
    $env:RASTISI_DEV_OTP_CODE = '123456'
    $env:RASTISI_QA_LAN_IP = $LanIp
    $env:RASTISI_QA_CADDY_DATA = Join-Path $root 'ca'
    $env:RASTISI_VAPID_SUBJECT = 'mailto:qa@rastisi.test'

    @'
from pathlib import Path
from tools.qa.mobile_lan_settings import *
root = Path(__file__).resolve().parent
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': root / 'qa.sqlite3'}}
MEDIA_ROOT = root / 'media'
PRIVATE_MEDIA_ROOT = root / 'private'
'@ | Set-Content -LiteralPath (Join-Path $root 'phone_push_qa.py') -Encoding ascii

    Run @('manage.py','migrate','--noinput','--verbosity','0')
    $qaPass = [guid]::NewGuid().ToString('N') + '!aA7'
    $fixture = Join-Path $root 'fixture.json'
    Run @('manage.py','seed_qa_notifications','--password',$qaPass,'--port','8443','--json',$fixture)
    Remove-Item -LiteralPath $fixture
    $keys = Run @('manage.py','generate_vapid_keys')
    $key = $keys | Where-Object { $_ -like 'RASTISI_VAPID_PRIVATE_KEY=*' } | Select-Object -First 1
    if (-not $key) { throw 'VAPID key generation failed.' }
    $env:RASTISI_VAPID_PRIVATE_KEY = $key.Substring('RASTISI_VAPID_PRIVATE_KEY='.Length)
    $env:PAYMENT_CREDENTIAL_KEY = Run @('-c','from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
    $env:RASTISI_NOTIF_WEBPUSH_ENABLED = '1'

    $config = Join-Path $root 'Caddyfile'
    (Get-Content -Raw -LiteralPath 'tools\qa\Caddyfile.mobile-qa').Replace('127.0.0.1:8771','127.0.0.1:8772') |
        Set-Content -LiteralPath $config -Encoding ascii
    & $caddyExe validate --config $config --adapter caddyfile
    if ($LASTEXITCODE -ne 0) { throw 'Caddy configuration failed.' }

    if ($TemporaryFirewall) {
        $fw = @{DisplayName=$rule; Direction='Inbound'; Action='Allow'; Profile='Private'; LocalAddress=$LanIp;
                LocalPort=8443; RemoteAddress='LocalSubnet'; Protocol='TCP'; Program=$caddyExe}
        New-NetFirewallRule @fw | Out-Null
        $firewallAdded = $true
    }
    $webArgs = @{FilePath=$python; ArgumentList='manage.py runserver 127.0.0.1:8772 --noreload'; WorkingDirectory=$App;
                 PassThru=$true; RedirectStandardOutput=(Join-Path $root 'django.out');
                 RedirectStandardError=(Join-Path $root 'django.err')}
    $web = Start-Process @webArgs
    $tlsArgs = @{FilePath=$caddyExe; WorkingDirectory=$root; ArgumentList='run --config Caddyfile --adapter caddyfile';
                 PassThru=$true; RedirectStandardOutput=(Join-Path $root 'caddy.out');
                 RedirectStandardError=(Join-Path $root 'caddy.err')}
    $tls = Start-Process @tlsArgs

    $ca = Join-Path $root 'ca\pki\authorities\local\root.crt'
    $ready = $false
    for ($i=0; $i -lt 30; $i++) {
        if ($web.HasExited -or $tls.HasExited) { throw "QA server stopped. See $root." }
        if (Test-Path -LiteralPath $ca) {
            try {
                $check = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8772/login/' -Headers @{ Host = 'rastisi-qa.test' } -TimeoutSec 2
                if ($check.StatusCode -eq 200) { $ready = $true; break }
            } catch { }
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw "QA startup check failed. See $root." }
    $publicCa = Join-Path $root 'RastiSi-QA-root.crt'
    Copy-Item -LiteralPath $ca -Destination $publicCa
    Write-Host "Temporary QA CA (public certificate only): $publicCa"
    Write-Host "Private DNS: rastisi-qa.test and qa-bike.rastisi-qa.test -> $LanIp"
    Write-Host 'Panel: https://qa-bike.rastisi-qa.test:8443/admin-portal/inbox/preferences/'
    Write-Host "Login: 09121110003    password: $qaPass    local OTP: 123456"
    Write-Host 'Enable browser delivery in preferences and register each phone. Close the panel.'
    [void](Read-Host 'Press Enter to send the first order')

    $helper = Join-Path $root 'send_order.py'
    @'
import django
django.setup()
import json
import sys
from datetime import datetime, timezone
from django.contrib.sessions.models import Session
from django.core.management import call_command
from apps.notifications.models import NotificationDelivery, NotificationEvent, WebPushSubscription
from apps.notifications.services.scope import key_for
from apps.stores.models import Store

phase = sys.argv[1]
store = Store.objects.get(slug='qa-bike')
devices = WebPushSubscription.objects.filter(user__username='09121110003', scope_key=key_for(store), revoked_at__isnull=True)
if devices.count() < 1:
    raise SystemExit('No active registered phone. Enable Web Push and register a device first.')
if phase == 'expired':
    Session.objects.all().delete()  # Only the disposable QA database.
before = NotificationEvent.objects.filter(store=store, type_key='order.created').order_by('-pk').values_list('pk', flat=True).first() or 0
call_command('qa_place_order', store='qa-bike')
event = NotificationEvent.objects.get(store=store, type_key='order.created', pk__gt=before)
deliveries = NotificationDelivery.objects.filter(inbox_item__event=event, channel='web_push')
call_command('process_inbox_deliveries', limit=200)
statuses = list(deliveries.values_list('status', flat=True))
result = {'at': datetime.now(timezone.utc).isoformat(), 'phase': phase,
          'order_event_created': True, 'registered_devices': devices.count(), 'queued': len(statuses),
          'provider_accepted_2xx': statuses.count('delivered'),
          'delivery_statuses': statuses}
print(json.dumps(result))
with open(sys.argv[2], 'a', encoding='utf-8') as report:
    report.write(json.dumps(result) + '\n')
'@ | Set-Content -LiteralPath $helper -Encoding ascii

    foreach ($phase in 'panel_closed','expired') {
        Run @($helper,$phase,$evidence)
        $android = Read-Host 'Android: received / missed / not-tested'
        $iphone = Read-Host 'iPhone: received / missed / not-tested'
        $opened = Read-Host 'Opened correct order (after login for expired): yes / no / not-tested'
        if ($android -notin @('received','missed','not-tested') -or $iphone -notin @('received','missed','not-tested') -or
            $opened -notin @('yes','no','not-tested')) { throw 'Use the indicated observation values.' }
        @{phase=$phase; android=$android; iphone=$iphone; correct_order_opened=$opened; source='human_observation'} |
            ConvertTo-Json -Compress | Add-Content -LiteralPath $evidence -Encoding ascii
        if ($phase -eq 'panel_closed') { [void](Read-Host 'Close the panel again, then press Enter for the expired-session order') }
    }
} finally {
    foreach ($process in @($tls,$web)) {
        if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue }
    }
    if ($firewallAdded) { Remove-NetFirewallRule -DisplayName $rule -ErrorAction Continue }
    Get-ChildItem Env: | Where-Object Name -match $pattern | ForEach-Object { Remove-Item -LiteralPath "Env:$($_.Name)" }
    foreach ($name in $vars.Keys) { Set-Item -LiteralPath "Env:$name" -Value $vars[$name] }
    Pop-Location
    Write-Host "QA evidence and local files: $root"
}
