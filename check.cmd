@echo off
set MU=D:\MuMuPlayer\nx_main
set PY=C:\Users\JIAN\.workbuddy\binaries\python\envs\soulocr\Scripts\python.exe
set LOG=E:\soul\check.txt
echo [%TIME%] focus before > %LOG%
"%MU%\mumu-cli.exe" sh -v 0 -c "dumpsys window | grep mCurrentFocus" >> %LOG% 2>&1
echo [%TIME%] force-stop + start Soul >> %LOG%
"%MU%\mumu-cli.exe" sh -v 0 -c "am force-stop cn.soulapp.android" >> %LOG% 2>&1
"%MU%\mumu-cli.exe" sh -v 0 -c "sleep 2" >> %LOG% 2>&1
"%MU%\mumu-cli.exe" sh -v 0 -c "am start -n cn.soulapp.android/.component.startup.main.MainActivity" >> %LOG% 2>&1
"%MU%\mumu-cli.exe" sh -v 0 -c "sleep 7" >> %LOG% 2>&1
echo [%TIME%] focus after >> %LOG%
"%MU%\mumu-cli.exe" sh -v 0 -c "dumpsys window | grep mCurrentFocus" >> %LOG% 2>&1
echo [%TIME%] winshot w2 >> %LOG%
"%PY%" E:\soul\winshot.py E:\soul\w2.png >> %LOG% 2>&1
echo [%TIME%] OCR w2 >> %LOG%
"%PY%" E:\soul\_ocr1.py w2.png >> %LOG% 2>&1
echo [%TIME%] END >> %LOG%
