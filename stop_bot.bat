@echo off
title Stop Binance Prediction Markets Bot (Port 8899)
color 0C

echo ======================================================================
echo    STOPPING BINANCE PREDICTION MARKETS BOT (PORT 8899)
echo ======================================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$conns = Get-NetTCPConnection -LocalPort 8899 -ErrorAction SilentlyContinue; if ($conns) { $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique; foreach ($p in $pids) { if ($p -gt 0) { Write-Host ' [+] Found Bot Process PID:' $p -ForegroundColor Yellow; Stop-Process -Id $p -Force -ErrorAction SilentlyContinue; Write-Host ' [OK] Successfully stopped process PID:' $p -ForegroundColor Green; } } } else { Write-Host ' [-] No running bot process found on Port 8899.' -ForegroundColor Cyan; }; Get-Process cmd -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like '*Binance Prediction Markets Bot*' } | Stop-Process -Force -ErrorAction SilentlyContinue;"

echo.
echo ======================================================================
echo    BOT SHUTDOWN COMPLETED
echo ======================================================================
echo.
pause
