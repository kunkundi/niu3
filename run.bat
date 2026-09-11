@echo off
cd /d "%~dp0"
docker compose up -d --build
if errorlevel 1 exit /b 1
docker compose ps
echo NiuNo3: http://127.0.0.1:8789
