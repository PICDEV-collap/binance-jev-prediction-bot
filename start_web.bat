@echo off
title Binance Prediction Dashboard (Port 3888)
color 0B

echo ======================================================================
echo    STARTING WEB MONITORING DASHBOARD (NEXT.JS) ON PORT 3888
echo ======================================================================
echo.
echo  [+] Local Dashboard URL: http://localhost:3888
echo  [+] Safe Isolated Port: 3888 (No collision with 3000)
echo.

cd web
start "" http://localhost:3888
npm run dev
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Web server exited with code %ERRORLEVEL%.
    pause
)
