# MTK EDL WinUSB 驱动安装脚本。
# 用法：以管理员身份运行 powershell -ExecutionPolicy Bypass -File install.ps1
# 安装内容：与本脚本同目录的 mtk_edl.inf（只匹配 USB\VID_0E8D&PID_2000）。

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host "[错误] 需要管理员权限，请以管理员身份运行 PowerShell。" -ForegroundColor Red
        exit 1
    }
}

function Show-DeviceInstances {
    param([string]$Stage)
    Write-Host "--- 设备实例（$Stage）---"
    $devices = @()
    if (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue) {
        $devices = @(Get-PnpDevice -ErrorAction SilentlyContinue |
            Where-Object { $_.InstanceId -like 'USB\VID_0E8D&PID_2000*' })
    }
    if ($devices.Count -eq 0) {
        Write-Host "（未发现 USB\VID_0E8D&PID_2000 设备实例）"
        return
    }
    foreach ($dev in $devices) {
        Write-Host ("InstanceId={0} | Status={1} | Class={2} | Name={3}" -f `
            $dev.InstanceId, $dev.Status, $dev.Class, $dev.FriendlyName)
    }
}

Assert-Administrator

$inf = Join-Path $PSScriptRoot 'mtk_edl.inf'
if (-not (Test-Path -LiteralPath $inf)) {
    Write-Host "[错误] 未找到 INF：$inf" -ForegroundColor Red
    exit 1
}

Show-DeviceInstances '安装前'

Write-Host "--- 安装 $inf ---"
$output = & pnputil.exe /add-driver $inf /install 2>&1
$code = $LASTEXITCODE
foreach ($line in $output) {
    Write-Host $line
}

Show-DeviceInstances '安装后'

if ($code -ne 0) {
    Write-Host "[错误] pnputil 返回 $code。若提示签名问题，需要在测试签名模式下安装。" -ForegroundColor Red
    exit $code
}

Write-Host "[完成] WinUSB 驱动已安装。设备重新插拔后生效。" -ForegroundColor Green
exit 0
