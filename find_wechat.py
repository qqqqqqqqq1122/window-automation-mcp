import win32gui, win32process
pids = {2680,11296,12060,18080,19696,19700,23760,31128,32136,32516,33700}
results = []
def enum_cb(hwnd, _):
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid in pids:
            title = win32gui.GetWindowText(hwnd)
            r = win32gui.GetWindowRect(hwnd)
            w, h = r[2]-r[0], r[3]-r[1]
            vis = win32gui.IsWindowVisible(hwnd)
            icon = win32gui.IsIconic(hwnd)
            results.append((hwnd, pid, title, w, h, vis, icon))
    except: pass
    return True
win32gui.EnumWindows(enum_cb, None)
for r in sorted(results, key=lambda x: x[3]*x[4], reverse=True):
    print(f"hwnd={r[0]} pid={r[1]} title='{r[2]}' {r[3]}x{r[4]} visible={r[5]} minimized={r[6]}")
if not results:
    print("NO WECHAT WINDOWS FOUND")
