# -*- coding: utf-8 -*-
"""恢复正常：拉起 Soul 并截图确认"""
import subprocess, io, os, time

MUMU_CLI = r'D:\Program Files\Netease\MuMu\nx_main\mumu-cli.exe'
ADB = r'D:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe'
SHOTS = r'C:\Users\JIAN\Documents\MuMu共享文件夹\Screenshots'
LOG = io.open(r'E:\soul\_restore.txt', 'w', encoding='utf-8')

def p(s):
    LOG.write(s + '\n')
    LOG.flush()

def run(args, t=40):
    try:
        r = subprocess.run(args, capture_output=True, timeout=t, text=True, encoding='utf-8', errors='replace')
        return r.stdout
    except Exception as e:
        return 'ERR:%r' % e

def shot(tag):
    before = max((os.path.getmtime(os.path.join(SHOTS, f)) for f in os.listdir(SHOTS) if f.endswith('.png')), default=0)
    run([MUMU_CLI, 'control', '--vmindex', '0', 'tool', 'func', '--name', 'screenshot'], 30)
    for _ in range(8):
        time.sleep(1)
        fs = [(os.path.getmtime(os.path.join(SHOTS, f)), os.path.join(SHOTS, f))
              for f in os.listdir(SHOTS) if f.endswith('.png')]
        if fs:
            m, f = max(fs)
            if m > before:
                p('%s -> %s' % (tag, f))
                return f
    return None

# 1) am start 拉起 Soul
p('am1: ' + run([ADB, '-s', '127.0.0.1:16384', 'shell', 'am start -n cn.soulapp.android/.component.startup.main.MainActivity'], 30).strip()[:100])
time.sleep(8)
s1 = shot('after_am1')

# 2) 焦点检查
foc = run([ADB, '-s', '127.0.0.1:16384', 'shell', 'dumpsys window | grep mCurrentFocus'], 30)
p('focus: ' + foc.strip()[:150])

# 3) 若焦点不是 Soul 再拉一次
if 'soulapp' not in foc:
    p('am2: ' + run([ADB, '-s', '127.0.0.1:16384', 'shell', 'am start -n cn.soulapp.android/.component.startup.main.MainActivity'], 30).strip()[:100])
    time.sleep(8)
    s1 = shot('after_am2')
    foc2 = run([ADB, '-s', '127.0.0.1:16384', 'shell', 'dumpsys window | grep mCurrentFocus'], 30)
    p('focus2: ' + foc2.strip()[:150])
LOG.close()
print('OK')
