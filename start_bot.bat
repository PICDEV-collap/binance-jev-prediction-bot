@echo off
title Binance Prediction Markets Bot Launcher (Port 8899)
color 0A
cd /d "%~dp0"

echo ======================================================================
echo    BINANCE PREDICTION MARKETS BOT - BACKGROUND LAUNCHER (PORT 8899)
echo ======================================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$conns = Get-NetTCPConnection -LocalPort 8899 -ErrorAction SilentlyContinue; " ^
    "if ($conns) { " ^
    "    $botPid = ($conns | Select-Object -ExpandProperty OwningProcess -Unique); " ^
    "    Write-Host (' [!] Bot is ALREADY running on Port 8899! (PID: ' + $botPid + ')') -ForegroundColor Yellow; " ^
    "    Write-Host ' [i] To stop the bot, double-click stop_bot.bat' -ForegroundColor Cyan; " ^
    "    exit 2; " ^
    "} " ^
    "Write-Host ' [+] Starting Trading Bot in background (Hidden Window)...' -ForegroundColor Cyan; " ^
    "Start-Process python -ArgumentList 'main.py' -WorkingDirectory '%~dp0' -WindowStyle Hidden; " ^
    "$bound = $false; " ^
    "for ($i = 0; $i -lt 14; $i++) { " ^
    "    Start-Sleep -Milliseconds 500; " ^
    "    $c = Get-NetTCPConnection -LocalPort 8899 -ErrorAction SilentlyContinue; " ^
    "    if ($c) { " ^
    "        $p = ($c | Select-Object -ExpandProperty OwningProcess -Unique); " ^
    "        Write-Host (' [OK] Bot started successfully in BACKGROUND! (PID: ' + $p + ')') -ForegroundColor Green; " ^
    "        Write-Host ' [OK] Telemetry Server: Port 8899' -ForegroundColor Green; " ^
    "        Write-Host ' [i] Web Dashboard: http://localhost:3888' -ForegroundColor Cyan; " ^
    "        Write-Host ' [i] This launcher window will hide/close now...' -ForegroundColor Yellow; " ^
    "        $bound = $true; " ^
    "        break; " ^
    "    } " ^
    "} " ^
    "if (-not $bound) { " ^
    "    Write-Host ' [!] Failed to bind Port 8899 within 7s. Please check bot.log for errors.' -ForegroundColor Red; " ^
    "    exit 1; " ^
    "} "

if %ERRORLEVEL% EQU 2 (
    echo.
    echo Press any key to close this window...
    pause > nul
    exit /b 0
)

if %ERRORLEVEL% EQU 1 (
    echo.
    echo [!] Bot failed to start. Check bot.log for error details.
    pause
    exit /b 1
)

rem Successfully started and verified! Wait 2 seconds and close this launcher window!
ping 127.0.0.1 -n 3 > nul
exit /b 0
