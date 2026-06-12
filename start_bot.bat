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
echo [%DATE% %TIME%] AlgoRich bot exited (code %ERRORLEVEL%). >> C:\Algorich\start_bot.log
exit /b 0

:already
echo [%DATE% %TIME%] AlgoRich bot already running. Skipping. >> C:\Algorich\start_bot.log
exit /b 0
