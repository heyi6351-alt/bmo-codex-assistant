# Fast flash on COM5: try high baud first, fall back to reliable 115200.
$exe = "D:\TuyaOpen-master1\tools\tyutool\tyutool_cli.exe"
$bin = "D:\TuyaOpen-master1\apps\tuya.ai\your_chat_bot\.build\bin\your_chat_bot_QIO_1.0.1.bin"
if (-not (Test-Path $bin)) { Write-Output "BIN NOT FOUND"; exit 2 }
Write-Output "=== FAST FLASH COM5  $(Get-Date -Format o) ==="
$ok = $false
foreach ($baud in @(921600, 921600, 460800, 115200, 115200)) {
    Write-Output "----- try COM5 @ $baud -----"
    & $exe write -d t5 -f $bin -p COM5 -b $baud --plain 2>&1 | ForEach-Object { $_ }
    if ($LASTEXITCODE -eq 0) { $ok = $true; Write-Output "=== FLASH SUCCESS @ $baud ==="; break }
    Start-Sleep -Milliseconds 500
}
if (-not $ok) { Write-Output "=== ALL BAUDS FAILED ===" }