@echo off
:: AlgoRich NH-Sniper 자동매매 봇 런처
:: Windows Task Scheduler가 평일 08:00에 이 파일을 실행합니다

cd /d C:\Algorich

:: 이미 실행 중이면 중복 실행 방지
tasklist /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq AlgoRich*" 2>NUL | find /I "python.exe" >NUL
if %ERRORLEVEL% EQU 0 (
    echo [%DATE% %TIME%] AlgoRich bot already running. Skipping. >> C:\Algorich\start_bot.log
    exit /b 0
)

echo [%DATE% %TIME%] AlgoRich bot starting... >> C:\Algorich\start_bot.log

:: Python 실행 (출력을 trade_log.txt에 추가 기록)
python trader_final.py >> C:\Algorich\trade_log.txt 2>&1

echo [%DATE% %TIME%] AlgoRich bot exited. >> C:\Algorich\start_bot.log
