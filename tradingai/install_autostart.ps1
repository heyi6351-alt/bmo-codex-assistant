# =============================================================================
# install_autostart.ps1  -  keep the TradingAI gold bot alive 24/7 (run ONCE).
#
# Registers a Windows Scheduled Task that runs start_bot.ps1:
#   * at logon (starts the bot when you log in), AND
#   * every 5 minutes as a WATCHDOG (re-launches after a crash, sleep, or reboot).
# start_bot.ps1 is idempotent - if the bot is already running it exits and does
# NOTHING, so the 5-minute watchdog never double-launches. This fixes the bot
# going dark whenever the PC sleeps / you log out (which costs trading tape).
#
# HOW TO RUN (once) - open PowerShell (admin NOT required for a per-user task):
#   powershell -ExecutionPolicy Bypass -File "C:\Users\Setup Game\OneDrive\Desktop\tradingaI\install_autostart.ps1"
# Remove later:
#   Unregister-ScheduledTask -TaskName "TradingAIBot" -Confirm:$false
# =============================================================================

$ErrorActionPreference = 'Stop'
$TaskName = 'TradingAIBot'
$Root     = 'C:\Users\Setup Game\OneDrive\Desktop\tradingaI'
$Launcher = Join-Path $Root 'start_bot.ps1'
$User     = "$env:USERDOMAIN\$env:USERNAME"

if (-not (Test-Path $Launcher)) { throw "start_bot.ps1 not found at $Launcher" }

Write-Host "Installing scheduled task '$TaskName' for user $User ..."

# The action: run the (idempotent) launcher, hidden, no profile, policy-bypassed.
# Build the argument by concatenation to avoid nested-quote escaping headaches.
$arg = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $Launcher + '"'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg

# Trigger 1: at logon. Trigger 2: repeating watchdog every 5 min for ~10 years.
$trigLogon = New-ScheduledTaskTrigger -AtLogOn -User $User
$trigWatch = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration  (New-TimeSpan -Days 3650)

# Interactive user session (MT5 needs the logged-on GUI session); normal (Limited)
# level to match how MT5 runs and to avoid UAC / elevation-isolation problems.
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited

# Robust: survive battery, start when available, no time limit, no piled-up instances.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

# Replace any existing copy, then register.
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger @($trigLogon, $trigWatch) `
    -Principal $principal -Settings $settings `
    -Description 'Keeps the TradingAI gold bot running (logon + 5-min watchdog); launcher is idempotent.' | Out-Null

Write-Host "[OK] Installed. Auto-start at logon + re-check every 5 minutes."
Write-Host "     Starting it now so you do not have to wait..."
Start-ScheduledTask -TaskName $TaskName

Start-Sleep -Seconds 6
$proc = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'run_bots|mt5_bot' }
if ($proc) {
    Write-Host "[OK] Bot is running (PIDs: $($proc.ProcessId -join ', '))."
} else {
    Write-Host "[!]  Bot not detected yet - check storage\run_bots.out.log; it may still be starting."
}
Write-Host ""
Write-Host "Manage:  Task Scheduler -> Task Scheduler Library -> 'TradingAIBot'"
Write-Host "Remove:  Unregister-ScheduledTask -TaskName 'TradingAIBot' -Confirm:`$false"
