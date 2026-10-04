@echo off
set MU=D:\MuMuPlayer\nx_main
set PY=C:\Users\JIAN\.workbuddy\binaries\python\envs\soulocr\Scripts\python.exe
set LOG=E:\soul\shot_test.txt
echo [%TIME%] === dir E:\soul === > %LOG%
dir E:\soul >> %LOG% 2>&1
echo [%TIME%] === bring Soul front === >> %LOG%
"%MU%\mumu-cli.exe" sh -v 0 -c "am start -n cn.soulapp.android/.component.startup.main.MainActivity" >> %LOG% 2>&1
"%MU%\mumu-cli.exe" sh -v 0 -c "sleep 2" >> %LOG% 2>&1
echo [%TIME%] === run winshot === >> %LOG%
"%PY%" E:\soul\winshot.py E:\soul\wshot.png >> %LOG% 2>&1
echo [%TIME%] rc=%ERRORLEVEL% >> %LOG%
if exist E:\soul\wshot.png (for %%F in (E:\soul\wshot.png) do echo wshot_size=%%~zF >> %LOG%) else echo wshot_MISSING >> %LOG%
echo [%TIME%] === DONE === >> %LOG%
