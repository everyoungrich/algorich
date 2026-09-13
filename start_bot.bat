@echo off
rem AlgoRich NH-Sniper bot launcher
rem Task Scheduler runs this Mon-Fri 08:00 (ASCII + CRLF only - do not save as UTF-8/LF)

cd /d C:\Algorich
title AlgoRich_Bot

rem Skip if trader_final.py already running in an AlgoRich_Bot console
tasklist /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq AlgoRich_Bot*" 2>NUL | find /I "python.exe" >NUL
if not errorlevel 1 goto already

echo [%DATE% %TIME%] AlgoRich bot starting... >> C:\Algorich\start_bot.log
python trader_final.py >> C:\Algorich\start_bot_stdout.log 2>&1
set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% neq 0 (
    echo [%DATE% %TIME%] AlgoRich bot exited (code %EXIT_CODE%). >> C:\Algorich\start_bot.log
    echo [%DATE% %TIME%] ERROR: exit code %EXIT_CODE% - check start_bot_stdout.log >> C:\Algorich\start_bot.log
    python -c "import os,sys,dotenv; dotenv.load_dotenv(override=True); missing=[k for k in ['KIS_REAL_APP_KEY','KIS_REAL_APP_SECRET','KIS_MOCK_APP_KEY','KIS_MOCK_APP_SECRET'] if not os.getenv(k)]; print('missing_vars='+str(missing))" >> C:\Algorich\start_bot.log 2>&1
) else (
    echo [%DATE% %TIME%] AlgoRich bot exited (code %EXIT_CODE%). >> C:\Algorich\start_bot.log
)
exit /b 0

:already
echo [%DATE% %TIME%] AlgoRich bot already running. Skipping. >> C:\Algorich\start_bot.log
exit /b 0
