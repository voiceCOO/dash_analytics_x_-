@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo   Push the Sorsa API key from config.json into Vercel env
echo   (the key goes straight to Vercel, it is not printed)
echo ============================================================
echo.

for /f "usebackq delims=" %%K in (`python -c "import json,io;print(json.load(io.open(r'..\collector\config.json',encoding='utf-8'))['sorsa']['apiKey'])"`) do set "SK=%%K"
if "%SK%"=="" (
  echo Could not read the key from ..\collector\config.json
  pause
  exit /b 1
)

echo Setting SORSA_API_KEY for production...
call npx vercel env rm SORSA_API_KEY production --yes >nul 2>&1
echo %SK%| call npx vercel env add SORSA_API_KEY production
echo Setting SORSA_API_KEY for preview...
call npx vercel env rm SORSA_API_KEY preview --yes >nul 2>&1
echo %SK%| call npx vercel env add SORSA_API_KEY preview
set "SK="

echo.
echo Key set. Now redeploy so the function picks it up:
echo     npx vercel --prod --yes
echo (or just tell Claude "ключ залил" and it will redeploy)
echo.
pause
