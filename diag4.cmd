@echo off
set MU=D:\MuMuPlayer\nx_main
set LOG=E:\soul\diag4.txt
echo [%TIME%] locate shared host path >> %LOG%
for /f "delims=" %%i in ('dir /s /b E:\MuMuPlayer\vms\private_shared\soul0.png 2^>nul') do echo HOSTPATH=%%i >> %LOG%
for /f "delims=" %%i in ('dir /s /b E:\MuMuPlayer\vms\private_shared\ 2^>nul') do echo HOSTDIR_LINE=%%i >> %LOG%
echo [%TIME%] ls vms top >> %LOG%
dir E:\MuMuPlayer\vms >> %LOG% 2>&1
echo [%TIME%] retry screencap -d 2 >> %LOG%
"%MU%\mumu-cli.exe" sh -v 0 -c "rm -f /mnt/shared/private_shared/soul2.png ; screencap -d 2 -p /mnt/shared/private_shared/soul2.png >/dev/null 2>&1 ; echo RC=$?" >> %LOG% 2>&1
echo [%TIME%] retry screencap --display 2 >> %LOG%
"%MU%\mumu-cli.exe" sh -v 0 -c "rm -f /mnt/shared/private_shared/soul2b.png ; screencap --display 2 -p /mnt/shared/private_shared/soul2b.png >/dev/null 2>&1 ; echo RC=$?" >> %LOG% 2>&1
echo [%TIME%] ls shared soul2 >> %LOG%
"%MU%\mumu-cli.exe" sh -v 0 -c "ls -l /mnt/shared/private_shared/soul2*.png" >> %LOG% 2>&1
echo [%TIME%] === DONE === >> %LOG%
