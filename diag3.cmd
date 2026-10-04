@echo off
set MU=D:\MuMuPlayer\nx_main
set LOG=E:\soul\diag3.txt
echo [%TIME%] === DIAG3 (sh, display routing) === > %LOG%
echo [%TIME%] dumpsys display >> %LOG%
"%MU%\mumu-cli.exe" sh -v 0 -c "dumpsys display" >> %LOG% 2>&1
echo [%TIME%] wm size -d 2 >> %LOG%
"%MU%\mumu-cli.exe" sh -v 0 -c "wm size -d 2" >> %LOG% 2>&1
echo [%TIME%] soul window before >> %LOG%
"%MU%\mumu-cli.exe" sh -v 0 -c "dumpsys window windows | grep -i soulapp" >> %LOG% 2>&1
echo [%TIME%] try am start --display 0 >> %LOG%
"%MU%\mumu-cli.exe" sh -v 0 -c "am start --display 0 -n cn.soulapp.android/.component.startup.main.MainActivity" >> %LOG% 2>&1
echo [%TIME%] sleep 3 >> %LOG%
"%MU%\mumu-cli.exe" sh -v 0 -c "sleep 3" >> %LOG% 2>&1
echo [%TIME%] soul window after >> %LOG%
"%MU%\mumu-cli.exe" sh -v 0 -c "dumpsys window windows | grep -i soulapp" >> %LOG% 2>&1
echo [%TIME%] screencap display0 (soul?) >> %LOG%
"%MU%\mumu-cli.exe" sh -v 0 -c "rm -f /mnt/shared/private_shared/soul0.png ; screencap -p /mnt/shared/private_shared/soul0.png && echo CAPOK" >> %LOG% 2>&1
echo [%TIME%] ls shared >> %LOG%
"%MU%\mumu-cli.exe" sh -v 0 -c "ls -l /mnt/shared/private_shared/" >> %LOG% 2>&1
echo [%TIME%] === DIAG3 DONE === >> %LOG%
