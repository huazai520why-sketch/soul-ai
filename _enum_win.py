import ctypes

u = ctypes.windll.user32
buf = ctypes.create_unicode_buffer(512)
buf2 = ctypes.create_unicode_buffer(512)
out = []

def cb(h, l):
    u.GetWindowTextW(h, buf, 512)
    t = buf.value
    u.GetClassNameW(h, buf2, 512)
    c = buf2.value
    if t and any(k in t for k in ("MuMu", "Android", "soul", "Soul", "模拟器", "emulator", "Emulator")):
        out.append(f"{hex(h)} | {c} | {t}")
    return True

u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(cb), 0)
print("\n".join(out) if out else "NONE_MATCHED")
