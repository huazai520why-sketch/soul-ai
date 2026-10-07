"""vm0（主号 账号1）开关机一键脚本 —— 完整版
用法:
    python -X utf8 E:\\soul\\start_vm0.py on            # 启动设备 + Soul 自动到前台
    python -X utf8 E:\\soul\\start_vm0.py on --shot     # 启动后自动截图到 _vm0_start.png
    python -X utf8 E:\\soul\\start_vm0.py off           # 关闭设备 + 自动检查进程（用户铁律）
    python -X utf8 E:\\soul\\start_vm0.py status        # 当前状态一览
"""
import subprocess, sys, time, json, os, re, ctypes

CLI = r"D:\Program Files\Netease\MuMu\nx_main\mumu-cli.exe"
ADB = r"D:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe"
PKG = "cn.soulapp.android"
MAIN = "cn.soulapp.android/.component.startup.main.MainActivity"
ADB_ADDR = "127.0.0.1:16384"
ENUM_PY = r"E:\soul\_enum_win.py"
WINSHOT = r"E:\soul\winshot.py"
SHOT = r"E:\soul\_vm0_start.png"


def run(args, timeout=30):
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout,
                           creationflags=0x08000000)
        return (p.stdout or b"").decode("utf-8", "ignore")
    except Exception as e:
        return "ERR:%r" % (e,)


def info_vm(v):
    s = run([CLI, "info", "-v", v])
    try:
        j = json.loads(s)
        return j.get("is_android_started"), j.get("is_process_started")
    except Exception:
        return None, None


def find_render_wnd():
    """动态找 MuMu 渲染窗口（Qt5156QWindowToolSaveBits），句柄每次启动都可能变"""
    try:
        out = run([sys.executable, "-X", "utf8", ENUM_PY], 30)
        for line in out.splitlines():
            if "Qt5156QWindowToolSaveBits" in line:
                m = re.match(r"(0x[0-9a-f]+)", line.strip())
                if m:
                    return int(m.group(1), 16)
    except Exception:
        pass
    return None


def take_shot():
    """截 Soul 渲染窗口 -> SHOT；失败则尝试用上次句柄"""
    h = find_render_wnd()
    if h is None:
        print("    ⚠️ 未找到渲染窗口，跳过截图", flush=True)
        return None
    run([sys.executable, "-X", "utf8", WINSHOT, hex(h), SHOT], 60)
    if os.path.exists(SHOT):
        sz = os.path.getsize(SHOT)
        print("    截图: %s（%d 字节）" % (SHOT, sz), flush=True)
        return sz
    return None


def wait_soul_front(max_s=90):
    """等 Soul 前台（先 adb connect，自动 am start 兜底）"""
    # ⭐ 设备刚 launch 时 adb 可能未注册（实测 launch 后直接 am start 全失败），
    #   必须先 connect 确认 device 在线
    run([ADB, "connect", ADB_ADDR], 15)
    time.sleep(3)
    t0 = time.time()
    while time.time() - t0 < max_s:
        top = run([ADB, "-s", ADB_ADDR, "shell", "dumpsys activity activities"])
        m = re.search(r"topResumedActivity=ActivityRecord\{[^}]*\s+(.+?)\s+t\d+\}", top)
        cur = m.group(1) if m else ""
        if "cn.soulapp" in cur:
            print("    Soul 已在前台: %s（%.0fs）" % (cur.split("/")[-1], time.time() - t0), flush=True)
            return True
        run([ADB, "-s", ADB_ADDR, "shell", "am start -n %s --activity-clear-top" % MAIN])
        time.sleep(10)
    print("    ⚠️ Soul 未到前台（超时）", flush=True)
    return False


def check_procs():
    """进程检查：MuMuNxDevice / Soul 守护链（python 仅统计命令行含 soul 的项目脚本，
    避免把 agent 运行时自身 python 误报为残留；用 PowerShell CIM 拿命令行最准）"""
    ps = (
        "$p = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { "
        "$_.Name -match 'MuMuNxDevice' -or "
        "($_.Name -match '^python' -and $_.CommandLine -match 'soul_(daemon|guard|watcher|watchdog|monitor|round)\\.py') }; "
        "if ($p) { $p | ForEach-Object { '{0}|{1}' -f $_.ProcessId, $_.Name } }"
    )
    out = run(["powershell", "-NoProfile", "-Command", ps], 30)
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    dev_n = sum(1 for ln in lines if "MuMuNxDevice" in ln)
    py_lines = [ln for ln in lines if "python" in ln and "MuMuNxDevice" not in ln]
    if dev_n == 0 and not py_lines:
        print("    ✅ 进程检查干净：MuMuNxDevice=0 / 守护链=0", flush=True)
    else:
        print("    ⚠️ 进程残留：MuMuNxDevice=%d / 守护链=%d" % (dev_n, len(py_lines)), flush=True)
        for ln in lines:
            print("      %s" % ln, flush=True)
    return dev_n == 0 and not py_lines
    if dev_n == 0 and py_n == 0:
        print("    ✅ 进程检查干净：MuMuNxDevice=0 / python=0", flush=True)
    else:
        print("    ⚠️ 进程残留：MuMuNxDevice=%d / python=%d" % (dev_n, py_n), flush=True)
        # 输出残留明细，便于排查
        snap2 = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        pe2 = PROCESSENTRY32W(); pe2.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if k32.Process32FirstW(snap2, ctypes.byref(pe2)):
            while True:
                n2 = pe2.szExeFile
                if pe2.th32ProcessID != my_pid and (
                        n2.lower().startswith("python") or re.match(r"MuMuNxDevice", n2, re.I)):
                    print("      PID=%d %s" % (pe2.th32ProcessID, n2), flush=True)
                if not k32.Process32NextW(snap2, ctypes.byref(pe2)):
                    break
        k32.CloseHandle(snap2)
    return dev_n == 0 and py_n == 0


def cmd_on(shot):
    t = time.time()
    print("[on] 启动 vm0 + Soul ...", flush=True)
    run([CLI, "control", "-v", "0", "launch", "--package", PKG], 120)
    t0 = time.time()
    while time.time() - t0 < 150:
        a, p = info_vm("0")
        if a:
            print("  安卓已启动（%.0fs）" % (time.time() - t0), flush=True)
            break
        time.sleep(8)
    else:
        print("  ⚠️ 150s 内安卓未就绪", flush=True)
    ok = wait_soul_front()
    if shot:
        take_shot()
    print("[on] 完成，总耗时 %.0fs" % (time.time() - t), flush=True)
    return ok


def cmd_off():
    t = time.time()
    print("[off] 关闭 vm0 ...", flush=True)
    run([CLI, "control", "-v", "0", "shutdown"], 120)
    time.sleep(10)
    for _ in range(12):  # 最长 2 分钟等关闭
        a, p = info_vm("0")
        if a is False and p is False:
            break
        time.sleep(10)
    a, p = info_vm("0")
    print("  安卓:%s / 进程:%s（%.0fs）" % (a, p, time.time() - t), flush=True)
    clean = check_procs()
    print("[off] 完成", flush=True)
    return clean


def cmd_status():
    a, p = info_vm("0")
    print("== vm0 状态 ==", flush=True)
    print("  安卓已启动: %s" % a, flush=True)
    print("  进程已启动: %s" % p, flush=True)
    try:
        dev = run([ADB, "devices"])
        print("  adb devices:\n%s" % dev.strip(), flush=True)
    except Exception:
        pass
    if a:
        top = run([ADB, "-s", ADB_ADDR, "shell", "dumpsys activity activities"])
        m = re.search(r"topResumedActivity=ActivityRecord\{[^}]*\s+(.+?)\s+t\d+\}", top)
        print("  前台: %s" % (m.group(1) if m else "?"), flush=True)


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "on"
    shot = "--shot" in args
    if cmd == "off":
        ok = cmd_off()
        sys.exit(0 if ok else 2)
    elif cmd == "status":
        cmd_status()
    else:  # on
        ok = cmd_on(shot)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
