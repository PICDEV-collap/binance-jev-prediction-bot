@echo off
chcp 65001 > nul
title Binance Prediction Markets Bot - Live Log Monitor
color 0B
cd /d "%~dp0"

echo ======================================================================
echo    BINANCE PREDICTION BOT - LIVE STREAMING LOG MONITOR
echo    (Close this window anytime - it will NOT stop the bot)
echo ======================================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; if (Test-Path 'bot.log') { Write-Host ' [i] Streaming live bot.log (Press Ctrl+C to stop viewing)...' -ForegroundColor Cyan; Get-Content 'bot.log' -Wait -Tail 30 -Encoding UTF8 } else { Write-Host ' [!] bot.log not found yet. Waiting for bot to generate logs...' -ForegroundColor Yellow; while (-not (Test-Path 'bot.log')) { Start-Sleep -Seconds 1 }; Get-Content 'bot.log' -Wait -Tail 30 -Encoding UTF8 }"
