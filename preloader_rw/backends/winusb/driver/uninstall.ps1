# MTK EDL WinUSB 驱动卸载脚本。
# 用法：以管理员身份运行 powershell -ExecutionPolicy Bypass -File uninstall.ps1
# 卸载对象：发布名对应与本脚本同目录的 mtk_edl.inf 的驱动包。

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

function Get-PublishedNames {
    $names = @()

    # 优先用 DISM 模块取发布名（oemXX.inf）
    if (Get-Command Get-WindowsDriver -ErrorAction SilentlyContinue) {
        try {
            $names = @(Get-WindowsDriver -Online -All -ErrorAction Stop |
                Where-Object { $_.OriginalFileName -like '*mtk_edl.inf' } |
                ForEach-Object { $_.Driver })
        }
        catch {
            $names = @()
        }
    }

    # 回退：直接解析 pnputil /enum-drivers 的驱动包段落
    if ($names.Count -eq 0) {
        $raw = @(& pnputil.exe /enum-drivers 2>&1)
        $blocks = ($raw -join "`n") -split "(\r?\n){2,}"
        foreach ($block in $blocks) {
            if ($block -match 'mtk_edl\.inf') {
                $hit = [regex]::Match($block, 'oem\d+\.inf', 'IgnoreCase')
                if ($hit.Success) {
                    $names += $hit.Value
                }
            }
        }
    }

    return @($names | Select-Object -Unique)
}

Assert-Administrator

Show-DeviceInstances '卸载前'

$published = @(Get-PublishedNames)
if ($published.Count -eq 0) {
    Write-Host "未找到 mtk_edl.inf 对应的驱动包，无需卸载。"
    exit 0
}

$failed = @()
foreach ($name in $published) {
    Write-Host "--- 卸载 $name ---"
    $output = & pnputil.exe /delete-driver $name /uninstall /force 2>&1
    $code = $LASTEXITCODE
    foreach ($line in $output) {
        Write-Host $line
    }
    if ($code -ne 0) {
        $failed += $name
    }
}

Show-DeviceInstances '卸载后'

if ($failed.Count -gt 0) {
    Write-Host ("[错误] 以下驱动包卸载失败：{0}" -f ($failed -join ', ')) -ForegroundColor Red
    exit 1
}

Write-Host "[完成] 驱动包已卸载。重新运行 install.ps1 即可恢复。" -ForegroundColor Green
exit 0
