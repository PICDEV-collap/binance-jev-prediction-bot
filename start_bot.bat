@echo off
title Binance Prediction Markets Bot + Jev AI Core (Port 8899)
color 0A

echo ======================================================================
echo    BINANCE PREDICTION MARKETS BOT + JEV AI DECISION ENGINE
echo ======================================================================
echo.
echo  [+] Dedicated API / WS Port: 8899
echo  [+] Web Dashboard Port: 3888 (Run `npm run dev` in /web)
echo  [+] Safe Isolated Ports: No collision with 3000, 5173, 8000, 8080
echo.
echo Starting Trading Bot Core...
echo Press CTRL+C to terminate cleanly.
echo.

python main.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Bot terminated with an error code %ERRORLEVEL%.
    pause
)
