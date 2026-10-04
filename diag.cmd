@echo off
set MU=D:\MuMuPlayer\nx_main
set ADB=%MU%\adb.exe
set LOG=E:\soul\diag.txt
echo [%TIME%] === DIAG START === > %LOG%
echo [%TIME%] mumu-cli info >> %LOG%
"%MU%\mumu-cli.exe" info -v 0 >> %LOG% 2>&1
echo [%TIME%] mumu-cli top help >> %LOG%
"%MU%\mumu-cli.exe" --help >> %LOG% 2>&1
echo [%TIME%] adb connect 127.0.0.1:16384 >> %LOG%
"%ADB%" connect 127.0.0.1:16384 >> %LOG% 2>&1
echo [%TIME%] adb devices >> %LOG%
"%ADB%" devices >> %LOG% 2>&1
echo [%TIME%] wm size >> %LOG%
"%ADB%" -s 127.0.0.1:16384 shell wm size >> %LOG% 2>&1
echo [%TIME%] screencap default disp >> %LOG%
"%ADB%" -s 127.0.0.1:16384 exec-out screencap -p > E:\soul\adb_default.png 2>>%LOG%
if exist E:\soul\adb_default.png (for %%F in (E:\soul\adb_default.png) do echo default_png_size=%%~zF >> %LOG%) else echo default_png_MISSING >> %LOG%
echo [%TIME%] screencap -d 2 >> %LOG%
"%ADB%" -s 127.0.0.1:16384 exec-out screencap -d 2 -p > E:\soul\adb_d2.png 2>>%LOG%
if exist E:\soul\adb_d2.png (for %%F in (E:\soul\adb_d2.png) do echo d2_png_size=%%~zF >> %LOG%) else echo d2_png_MISSING >> %LOG%
echo [%TIME%] dumpsys soul display >> %LOG%
"%ADB%" -s 127.0.0.1:16384 shell "dumpsys window windows | grep -i soulapp" >> %LOG% 2>&1
echo [%TIME%] current focus >> %LOG%
"%ADB%" -s 127.0.0.1:16384 shell "dumpsys window | grep mCurrentFocus" >> %LOG% 2>&1
echo [%TIME%] === DIAG DONE === >> %LOG%
