# Read-only preflight. Does not install, enable, initialize or start anything.
$ErrorActionPreference = 'Continue'
$os = Get-CimInstance Win32_OperatingSystem
$pc = Get-CimInstance Win32_ComputerSystem
$cpu = Get-CimInstance Win32_Processor
$drive = Get-PSDrive -Name ($env:SystemDrive.TrimEnd(':'))
[pscustomobject]@{
    Windows = $os.Caption
    Build = $os.BuildNumber
    Architecture = $os.OSArchitecture
    MemoryGiB = [math]::Round($pc.TotalPhysicalMemory / 1GB, 1)
    SystemDriveFreeGiB = [math]::Round($drive.Free / 1GB, 1)
    HypervisorPresent = $pc.HypervisorPresent
    VirtualizationFirmwareEnabled = ($cpu | Select-Object -First 1).VirtualizationFirmwareEnabled
} | Format-List
if (Get-Command wsl.exe -ErrorAction SilentlyContinue) { wsl.exe --status; wsl.exe --list --verbose }
else { Write-Output 'WSL executable unavailable' }
if (Get-Command docker.exe -ErrorAction SilentlyContinue) {
    docker.exe version --format '{{.Server.Os}}/{{.Server.Arch}}'
    docker.exe compose version
} else { Write-Output 'Docker executable unavailable' }
