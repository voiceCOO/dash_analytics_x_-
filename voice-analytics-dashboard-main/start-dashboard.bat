@echo off
echo Starting VOICE Analytics Dashboard...
cd /d "%~dp0"
start "" "http://localhost:8765/"
python collector\server.py 8765
