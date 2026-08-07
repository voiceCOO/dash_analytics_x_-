@echo off
REM VOICE Analytics — daily Sorsa collection (metrics, engagers, media, scores).
REM Geo is NOT updated here (it needs a logged-in browser) — run apply_geo.py for that.
cd /d "%~dp0collector"
py collect.py >> "%~dp0collect-daily.log" 2>&1
