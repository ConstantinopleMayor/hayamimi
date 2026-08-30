# start_desktop_subtitle.ps1
# 一键启动 hayamimi --serve 服务器 + 桌面透明字幕窗
# 用法: pwsh -ExecutionPolicy Bypass -File .\start_desktop_subtitle.ps1
$env:PYTHONUTF8 = 1
[Console]::InputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$project = "D:\AGI\hayamimi"
$overlay = "$project\desktop-subtitle"
$log = "$env:TEMP\hayamimi_serve.log"
$err = "$env:TEMP\hayamimi_serve.err.log"

# ---- 1. hayamimi 服务器（未启动时才启动）----
$serverUp = $false
try {
    $null = Invoke-WebRequest -Uri "http://localhost:8833/" -UseBasicParsing -TimeoutSec 3
    $serverUp = $true
} catch { }

if (-not $serverUp) {
    Write-Host "[1/2] 启动 hayamimi 服务器 ..."
    Start-Process -FilePath "$project\.venv\Scripts\python.exe" `
        -ArgumentList "scripts\realtime_transcribe.py", "--serve", "8833" `
        -WorkingDirectory $project `
        -RedirectStandardOutput $log -RedirectStandardError $err -WindowStyle Hidden
    Start-Sleep -Seconds 6
    try {
        $null = Invoke-WebRequest -Uri "http://localhost:8833/" -UseBasicParsing -TimeoutSec 3
        Write-Host "      服务器就绪 -> http://localhost:8833/"
    } catch {
        Write-Host "      服务器启动失败，请查看日志: $err"
    }
} else {
    Write-Host "[1/2] hayamimi 服务器已在运行"
}

# ---- 2. Electron 桌面字幕窗（默认可交互模式）----
Write-Host "[2/2] 启动桌面字幕窗 ..."
Start-Process -FilePath "$overlay\node_modules\electron\dist\electron.exe" `
    -ArgumentList "." -WorkingDirectory $overlay -WindowStyle Hidden
Start-Sleep -Seconds 2

Write-Host ""
Write-Host "========================================"
Write-Host " 桌面字幕窗已启动"
Write-Host " 右键        选择字号 / 开启点击穿透 / 退出"
Write-Host " 左键拖动    移动字幕窗（穿透开启时不可用）"
Write-Host " Ctrl+Alt+D  快速切换穿透模式"
Write-Host " Esc         退出字幕窗"
Write-Host ""
Write-Host " 停止 hayamimi 服务器:"
Write-Host "   Get-CimInstance Win32_Process | Where-Object {`$_.CommandLine -match 'realtime_transcribe.py'} | ForEach-Object { Stop-Process -Id `$_.ProcessId -Force }"
Write-Host "========================================"