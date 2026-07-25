# Auto-start wrapper for the TradingAI gold bot (run by Task Scheduler at logon + 5-min watchdog).
# GUARD 1 (double-launch): if run_bots/mt5_bot is already running AND healthy, exit quietly.
# GUARD 2 (wedge watchdog, 2026-07-10): the bot once sat WEDGED >2h in an LLM 429 retry storm —
#   process alive but no main-loop ticks (no trade management/reconcile). mt5_bot.py stamps
#   storage\tick_<SYMBOL>.txt every tick; if every bot process is older than the stale window AND the
#   tick file is stale, the process is wedged -> kill and relaunch. A fresh boot (young process) is
#   never killed even if an old tick file is lying around.
$ErrorActionPreference = 'SilentlyContinue'
$root      = "c:\Users\Setup Game\OneDrive\Desktop\tradingaI"
$py        = "C:\Users\Setup Game\AppData\Local\Programs\Python\Python313\python.exe"
$staleMin  = 10

$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'run_bots|mt5_bot' }

if ($procs) {
    # Youngest bot process age (a fresh boot must never be judged by an old tick file).
    $now = Get-Date
    $youngestAgeMin = ($procs | ForEach-Object { ($now - $_.CreationDate).TotalMinutes } |
        Measure-Object -Minimum).Minimum

    $tick = Get-ChildItem (Join-Path $root 'storage\tick_*.txt') |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $tickAgeMin = if ($tick) { ($now - $tick.LastWriteTime).TotalMinutes } else { [double]::MaxValue }

    if ($youngestAgeMin -gt $staleMin -and $tickAgeMin -gt $staleMin) {
        # Wedged: long-running process, no ticks. Kill everything and fall through to relaunch.
        $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
        Start-Sleep -Seconds 3
    } else {
        exit 0   # healthy (or still booting) — do nothing
    }
}

Start-Process -FilePath $py -ArgumentList "run_bots.py" -WorkingDirectory $root `
    -RedirectStandardOutput "$root\storage\run_bots.out.log" `
    -RedirectStandardError  "$root\storage\run_bots.launcher.err.log" `
    -WindowStyle Hidden
