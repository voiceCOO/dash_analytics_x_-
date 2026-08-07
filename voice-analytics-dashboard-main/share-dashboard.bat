@echo off
setlocal
cd /d "%~dp0"
title VOICE Dashboard - SHARE
echo ============================================================
echo   VOICE Dashboard  -  SHARE A LIVE LINK WITH THE TEAM
echo ============================================================
echo.
echo Starting local server on port 8765 ...
start "VOICE server" /min python collector\server.py 8765
timeout /t 3 >nul

where cloudflared >nul 2>&1
if errorlevel 1 (
  echo.
  echo   cloudflared is NOT installed on this machine.
  echo   Install it once, then run this file again:
  echo.
  echo       winget install Cloudflare.cloudflared
  echo.
  pause
  exit /b 1
)

echo.
echo ------------------------------------------------------------
echo  A public link will appear below, like:
echo      https://something-random.trycloudflare.com
echo.
echo  COPY that link and send it to the team.
echo  Keep THIS window open and the PC awake while they view it.
echo  Close this window to stop sharing.
echo ------------------------------------------------------------
echo.
echo  (using http2 transport for best compatibility on restrictive networks;
echo   if the link is unreliable, your network may be throttling the tunnel)
echo.
cloudflared tunnel --protocol http2 --url http://localhost:8765
