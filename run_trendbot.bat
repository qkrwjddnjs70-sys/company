@echo off
REM 더블클릭 한 번으로: 최신 코드 받기 -> 기존 서버(8766 포트) 종료 -> 웹앱 재실행.
REM 이 파일이 있는 폴더(=저장소 루트)에서 실행되도록 %~dp0 로 이동한다.
cd /d "%~dp0"

echo [1/3] 최신 코드 받는 중...
git pull origin claude/vigilant-brahmagupta-s8d2vw
if errorlevel 1 (
    echo.
    echo [경고] git pull 이 실패했습니다. 위 메시지를 확인하세요.
    echo         충돌(conflict)이면 화면을 캡처해서 알려주세요.
    pause
    exit /b 1
)

echo.
echo [2/3] 기존에 켜져 있던 서버가 있으면 종료합니다...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr :8766 ^| findstr LISTENING') do (
    taskkill /F /PID %%P >nul 2>&1
)

echo.
echo [3/3] 서버 실행 중... (이 창을 닫으면 서버도 꺼집니다)
echo       http://127.0.0.1:8766
echo.
python -m trendbot webapp

pause
