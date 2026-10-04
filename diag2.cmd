@echo off
set MU=D:\MuMuPlayer\nx_main
set LOG=E:\soul\diag2.txt
echo [%TIME%] === DIAG2 (adb bridge) === > %LOG%
echo [%TIME%] adb -c connect >> %LOG%
"%MU%\mumu-cli.exe" adb -v 0 -c connect >> %LOG% 2>&1
echo [%TIME%] dumpsys display >> %LOG%
"%MU%\mumu-cli.exe" adb -v 0 shell "dumpsys display" >> %LOG% 2>&1
echo [%TIME%] wm size (no -d) >> %LOG%
"%MU%\mumu-cli.exe" adb -v 0 shell "wm size" >> %LOG% 2>&1
echo [%TIME%] wm size -d 0 >> %LOG%
"%MU%\mumu-cli.exe" adb -v 0 shell "wm size -d 0" >> %LOG% 2>&1
echo [%TIME%] wm size -d 2 >> %LOG%
"%MU%\mumu-cli.exe" adb -v 0 shell "wm size -d 2" >> %LOG% 2>&1
echo [%TIME%] window displayIds >> %LOG%
"%MU%\mumu-cli.exe" adb -v 0 shell "dumpsys window windows | grep mDisplayId" >> %LOG% 2>&1
echo [%TIME%] screenshots via bridge >> %LOG%
"%MU%\mumu-cli.exe" adb -v 0 shell "screencap -p /mnt/shared/private_shared/b_default.png" >> %LOG% 2>&1
"%MU%\mumu-cli.exe" adb -v 0 shell "screencap -d 2 -p /mnt/shared/private_shared/b_d2.png" >> %LOG% 2>&1
echo [%TIME%] ls shared >> %LOG%
"%MU%\mumu-cli.exe" adb -v 0 shell "ls -l /mnt/shared/private_shared/" >> %LOG% 2>&1
echo [%TIME%] === DIAG2 DONE === >> %LOG%
