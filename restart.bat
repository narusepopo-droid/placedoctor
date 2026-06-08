@echo off
REM 플레이스닥터 서버 재시작 (더블클릭용). 포트 8000 좀비 정리 + 단일 프로세스 기동.
chcp 65001 >nul
cd /d "%~dp0"
python restart.py
echo.
pause
