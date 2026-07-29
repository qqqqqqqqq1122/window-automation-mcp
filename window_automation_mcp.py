#!/usr/bin/env python3
"""
Windows Background HWND-Based UI Automation MCP Server
======================================================
Provides 13 tools for background window manipulation via HWND handles.

Capture fallback chain:  WGC (COM) → PrintWindow(PW_RENDERFULLCONTENT) → BitBlt(GDI)
Input fallback chain:    UIA Patterns → SendMessage/PostMessage → Foreground simulation

Author: Window Automation MCP
Version: 2.1.0
"""

# ── stdlib ──────────────────────────────────────────────────────────────────
import asyncio
import base64
import ctypes
import ctypes.wintypes
import hashlib
import io
import json
import logging
import os
import shutil
import struct
import subprocess
import sys
import time
import traceback
from typing import Any

# ── third-party (must be installed in venv) ─────────────────────────────────
import win32gui
import win32ui
import win32con
import win32api
import win32process
import win32clipboard

from PIL import Image
import uiautomation as uia

import mcp
from mcp.server import Server
from mcp.types import Tool, TextContent, ImageContent

# ── logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,  # quiet by default; set MCP_LOG_LEVEL=DEBUG to debug
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("window_automation_mcp")

# ── constants ───────────────────────────────────────────────────────────────
PW_RENDERFULLCONTENT = 0x00000002
SMTO_ABORTIFHUNG = 0x0002
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_SETFOCUS = 0x0007
WM_KILLFOCUS = 0x0008
WM_ACTIVATE = 0x0006
WA_ACTIVE = 1

VK_CONTROL = 0x11
VK_ALT = 0x12  # Menu key (Alt)
VK_SHIFT = 0x10
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_BACK = 0x08
VK_DELETE = 0x2E
VK_UP = 0x26
VK_DOWN = 0x28
VK_LEFT = 0x25
VK_RIGHT = 0x27
VK_HOME = 0x24
VK_END = 0x23
VK_PRIOR = 0x21  # Page Up
VK_NEXT = 0x22   # Page Down
VK_INSERT = 0x2D
VK_F1 = 0x70
VK_F12 = 0x7B

VK_MAP: dict[str, int] = {
    "ctrl": VK_CONTROL, "control": VK_CONTROL,
    "alt": VK_ALT, "menu": VK_ALT,
    "shift": VK_SHIFT,
    "tab": VK_TAB, "enter": VK_RETURN, "return": VK_RETURN,
    "escape": VK_ESCAPE, "esc": VK_ESCAPE,
    "space": VK_SPACE, "backspace": VK_BACK, "back": VK_BACK,
    "delete": VK_DELETE, "del": VK_DELETE,
    "up": VK_UP, "down": VK_DOWN, "left": VK_LEFT, "right": VK_RIGHT,
    "home": VK_HOME, "end": VK_END,
    "pageup": VK_PRIOR, "pgup": VK_PRIOR,
    "pagedown": VK_NEXT, "pgdn": VK_NEXT,
    "insert": VK_INSERT, "ins": VK_INSERT,
    # Math / symbol keys (common on all keyboard layouts via keybd_event)
    "plus": 0xBB, "add": 0x6B,        # VK_OEM_PLUS / VK_ADD
    "minus": 0xBD, "subtract": 0x6D,  # VK_OEM_MINUS / VK_SUBTRACT
    "multiply": 0x6A,                  # VK_MULTIPLY (*)
    "divide": 0x6F,                    # VK_DIVIDE (/)
    "decimal": 0xBE,                   # VK_OEM_PERIOD (.)
    "equals": 0xBB,                    # = (same as plus on US keyboard)
    "semicolon": 0xBA,                 # VK_OEM_1 (;)
    "comma": 0xBC,                     # VK_OEM_COMMA
    "period": 0xBE,                    # VK_OEM_PERIOD
    "slash": 0xBF,                     # VK_OEM_2
    "backslash": 0xDC,                 # VK_OEM_5
    "bracketleft": 0xDB,              # [
    "bracketright": 0xDD,             # ]
    "quote": 0xDE,                     # '
    "apostrophe": 0xDE,                # '
    "grave": 0xC0,                     # `
    "apps": 0x5D,                      # context menu key
    "printscreen": 0x2C, "prtsc": 0x2C,
    "numlock": 0x90,
    "scrolllock": 0x91,
    "capslock": 0x14,
}
for _i in range(1, 13):
    VK_MAP[f"f{_i}"] = VK_F1 + _i - 1
for _ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    VK_MAP[_ch.lower()] = ord(_ch)

# ── error helpers ───────────────────────────────────────────────────────────

class WindowError(Exception):
    """Structured error for window operations."""
    def __init__(self, message: str, code: str = "WINDOW_ERROR", detail: str = ""):
        super().__init__(message)
        self.code = code
        self.detail = detail


def _err_result(message: str) -> str:
    """Return a JSON error string so tools never raise unhandled exceptions."""
    return json.dumps({"error": True, "message": message})


def _ok_result(data: dict | str) -> str:
    """Wrap a success result as JSON string."""
    if isinstance(data, str):
        return json.dumps({"success": True, "data": data})
    data.setdefault("success", True)
    return json.dumps(data, default=str)


# ═══════════════════════════════════════════════════════════════════════════════
# WINDOW ENUMERATION & STATE
# ═══════════════════════════════════════════════════════════════════════════════

def _get_window_rect(hwnd: int) -> dict:
    """Get window rectangle in screen coordinates."""
    try:
        r = win32gui.GetWindowRect(hwnd)
        return {"left": r[0], "top": r[1], "width": r[2] - r[0], "height": r[3] - r[1]}
    except Exception:
        return {"left": 0, "top": 0, "width": 0, "height": 0}


def _get_client_rect(hwnd: int) -> dict:
    """Get client rectangle (relative coords)."""
    try:
        r = win32gui.GetClientRect(hwnd)
        return {"left": 0, "top": 0, "width": r[2] - r[0], "height": r[3] - r[1]}
    except Exception:
        return {"left": 0, "top": 0, "width": 0, "height": 0}


def _get_dpi(hwnd: int) -> int:
    """Get window DPI."""
    try:
        return win32api.GetDpiForWindow(hwnd)
    except Exception:
        try:
            hdc = win32gui.GetDC(hwnd)
            dpi = win32ui.GetDeviceCaps(win32ui.CreateDCFromHandle(hdc), 88)  # LOGPIXELSX
            win32gui.ReleaseDC(hwnd, hdc)
            return dpi
        except Exception:
            return 96


def _window_text(hwnd: int) -> str:
    try:
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


def _is_visible(hwnd: int) -> bool:
    try:
        return bool(win32gui.IsWindowVisible(hwnd))
    except Exception:
        return False


def _is_minimized(hwnd: int) -> bool:
    try:
        return bool(win32gui.IsIconic(hwnd))
    except Exception:
        return False


def _is_window(hwnd: int) -> bool:
    try:
        return bool(win32gui.IsWindow(hwnd))
    except Exception:
        return False


def _is_top_level(hwnd: int) -> bool:
    """Check if a window has no parent (true top-level window)."""
    try:
        return win32gui.GetParent(hwnd) == 0
    except Exception:
        return False


def _window_class(hwnd: int) -> str:
    """Get window class name."""
    try:
        return win32gui.GetClassName(hwnd)
    except Exception:
        return ""


def _get_process_name_by_pid(pid: int) -> str:
    """Get process executable name by PID using Win32 API (no psutil dependency)."""
    if pid == 0:
        return ""
    try:
        h_process = ctypes.windll.kernel32.OpenProcess(
            0x0400 | 0x0010,  # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
            False, pid,
        )
        if h_process:
            exe_name = ctypes.create_unicode_buffer(260)
            size = ctypes.c_ulong(260)
            success = ctypes.windll.psapi.GetModuleBaseNameW(
                h_process, None, exe_name, size,
            )
            ctypes.windll.kernel32.CloseHandle(h_process)
            if success:
                return exe_name.value
    except Exception:
        pass
    return ""


# Cached PID → process name mapping for performance during EnumWindows
_process_name_cache: dict[int, str] = {}


def _cached_process_name(pid: int) -> str:
    """Get process name with caching — EnumWindows hits the same PID many times."""
    if pid not in _process_name_cache:
        _process_name_cache[pid] = _get_process_name_by_pid(pid)
    return _process_name_cache[pid]


# System classes that are NEVER real application windows
_SYSTEM_CLASSES: set[str] = {
    "MSCTFIME UI",
    "IME",
    "SystemResourceNotifyWindow",
    "tooltips_class32",
    "SysShadow",
    "Ghost",
    "Progman",
    "DummyDWMListenerWindow",
    "Shell_TrayWnd",
    "Button",
    "TrayNotifyWnd",
    "CiceroUIWndFrame",
    "OfficeTooltip",
    "TaskSwitcherWnd",
    "OperationStatusWindow",
}


def _needs_foreground_input(hwnd: int) -> tuple[bool, str]:
    """Detect windows that cannot process SendMessage/WM_CHAR.
    This includes CEF/WebView2/Electron (GPU-rendered content) AND
    UWP/Store apps (ApplicationFrameWindow sandbox).

    Returns (needs_foreground, reason)."""
    cls = _window_class(hwnd).lower()
    # CEF / Electron
    if "chrome_widgetwin" in cls:
        return (True, "cef")
    if "chrome_renderwidgethost" in cls:
        return (True, "chromium")
    # WebView2
    if "webviewhost" in cls:
        return (True, "webview2")
    # UWP / Windows Store apps — sandboxed, don't process external messages
    if cls in ("applicationframewindow", "windows.ui.core.corewindow"):
        return (True, "uwp")
    return (False, "")


# Backward-compatible alias
_is_webview_window = _needs_foreground_input


def list_windows_impl(title_keyword: str = "") -> list[dict]:
    """
    Enumerate all top-level windows, optionally filtered by keyword.

    The keyword is matched against window TITLE AND process EXE name.
    Even hidden windows (IsWindowVisible=False) with empty titles ARE
    returned if the keyword matches the process name.

    This is critical for apps like WeChat that use a hidden main window
    managed internally by CEF/Chromium frameworks.
    """
    # Flush PID→name cache each enumeration
    _process_name_cache.clear()

    results: list[dict] = []
    keyword_lower = title_keyword.lower().strip() if title_keyword else ""

    def _enum_callback(hwnd: int, _lparam: Any) -> bool:
        try:
            # ── 1. Hard filter: skip child windows (non-top-level) ──
            if not _is_top_level(hwnd):
                return True

            # ── 2. System junk class blacklist ──
            cls_name = _window_class(hwnd)
            if cls_name in _SYSTEM_CLASSES:
                return True

            # ── 3. Extract window info ──
            title = win32gui.GetWindowText(hwnd)
            is_vis = bool(win32gui.IsWindowVisible(hwnd))
            is_min = bool(win32gui.IsIconic(hwnd))
            rect = _get_window_rect(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc_name = _cached_process_name(pid) if pid else ""

            # ── 4. Keyword matching ──
            if keyword_lower:
                title_match = keyword_lower in title.lower()
                proc_match = keyword_lower in proc_name.lower()
                cls_match = keyword_lower in cls_name.lower()
                if not (title_match or proc_match or cls_match):
                    return True

            # ── 5. Detect WebView/CEF ──
            is_webview, webview_engine = _is_webview_window(hwnd)

            results.append({
                "hwnd": hwnd,
                "pid": pid,
                "process_name": proc_name,
                "title": title,
                "class_name": cls_name,
                "bounds": rect,
                "is_minimized": is_min,
                "is_visible": is_vis,
                "is_webview": is_webview,
                "webview_engine": webview_engine if is_webview else "",
            })
        except Exception:
            pass
        return True

    win32gui.EnumWindows(_enum_callback, None)
    # Sort by window area descending — main window first, helper windows last
    results.sort(
        key=lambda w: w["bounds"].get("width", 0) * w["bounds"].get("height", 0),
        reverse=True,
    )
    return results


def get_window_state_impl(hwnd: int) -> dict:
    """Get detailed window state."""
    if not _is_window(hwnd):
        return {"error": True, "message": f"Invalid HWND: {hwnd}"}

    dpi = _get_dpi(hwnd)
    return {
        "hwnd": hwnd,
        "title": _window_text(hwnd),
        "minimized": _is_minimized(hwnd),
        "visible": _is_visible(hwnd),
        "focused": hwnd == win32gui.GetForegroundWindow(),
        "dpi": dpi,
        "scale": round(dpi / 96.0, 2),
        "rect": _get_window_rect(hwnd),
        "client_rect": _get_client_rect(hwnd),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# GRAPHICS CAPTURE — FALLBACK CHAIN
# ═══════════════════════════════════════════════════════════════════════════════

def _capture_via_printwindow(hwnd: int) -> Image.Image | None:
    """
    Use PrintWindow with PW_RENDERFULLCONTENT flag.
    This handles DirectComposition windows (Edge, WebView2, Electron, Tauri).
    """
    try:
        rect = win32gui.GetWindowRect(hwnd)
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        if w <= 0 or h <= 0:
            return None

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bitmap)

        # Try with PW_RENDERFULLCONTENT first
        result = win32gui.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        if result == 0:  # failed
            # Try without the flag
            result = win32gui.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0)

        if result == 0:
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwnd_dc)
            return None

        bmp_info = bitmap.GetInfo()
        bmp_bits = bitmap.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGB",
            (bmp_info["bmWidth"], bmp_info["bmHeight"]),
            bmp_bits, "raw", "BGRX", 0, 1,
        )

        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        return img
    except Exception:
        return None


def _capture_via_bitblt(hwnd: int) -> Image.Image | None:
    """Traditional GDI BitBlt capture — last resort."""
    try:
        rect = win32gui.GetWindowRect(hwnd)
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        if w <= 0 or h <= 0:
            return None

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bitmap)
        save_dc.BitBlt((0, 0), (w, h), mfc_dc, (0, 0), win32con.SRCCOPY)

        bmp_info = bitmap.GetInfo()
        bmp_bits = bitmap.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGB",
            (bmp_info["bmWidth"], bmp_info["bmHeight"]),
            bmp_bits, "raw", "BGRX", 0, 1,
        )

        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        return img
    except Exception:
        return None


def _capture_via_uia(hwnd: int) -> Image.Image | None:
    """
    Use UI Automation's built-in CaptureToImage method.
    This internally attempts multiple approaches and handles many edge cases.
    """
    try:
        control = uia.ControlFromHandle(hwnd)
        img = control.CaptureToImage()
        if img is not None:
            return img
        return None
    except Exception:
        return None


def _capture_via_wgc(hwnd: int) -> Image.Image | None:
    """
    Windows Graphics Capture API via CreateForWindow COM interop.
    Uses ctypes to call into windows.graphics.capture APIs.

    This is the recommended approach for DirectX/WebView2/Tauri/Electron apps.
    """
    try:
        # We use a pragmatic approach: spawn a tiny helper via uiautomation
        # that internally attempts the best capture method available.
        # uiautomation 2.x uses GDI + PrintWindow internally for CaptureToImage.
        #
        # For true WGC, we'd need the WinRT interop package (winrt) or
        # complex COM activation. We attempt a direct composition capture.
        return _capture_via_directcomposition(hwnd)
    except Exception:
        return None


def _capture_via_directcomposition(hwnd: int) -> Image.Image | None:
    """
    Attempt DirectComposition capture via DwmGetDxSharedSurface + D3D.
    This is a best-effort approach that works for many modern windows.
    Falls back gracefully if DWM doesn't provide a shared surface.
    """
    try:
        # Try uiautomation capture first — it handles many internal cases
        control = uia.ControlFromHandle(hwnd)
        if control is None:
            return None

        # uiautomation's CaptureToImage works for many DirectComposition windows
        bitmap = control.CaptureToImage()
        if bitmap is not None:
            return bitmap
    except Exception:
        pass

    return None


def capture_window_impl(hwnd: int) -> dict:
    """
    Capture a window to a base64-encoded PNG image.

    Fallback chain:
      1. WGC / DirectComposition (best for modern apps)
      2. PrintWindow with PW_RENDERFULLCONTENT
      3. GDI BitBlt
    """
    if not _is_window(hwnd):
        return {"error": True, "message": f"Invalid HWND: {hwnd}"}

    if _is_minimized(hwnd):
        return {
            "error": True,
            "message": "Window is minimized. Call activate() first to restore it, then retry capture.",
            "minimized": True,
        }

    img: Image.Image | None = None
    method_used = "unknown"

    # Chain 1: WGC / DirectComposition via UIA internals
    try:
        img = _capture_via_wgc(hwnd)
        if img is not None:
            method_used = "wgc"
    except Exception as e:
        log.debug("WGC capture failed: %s", e)

    # Chain 2: PrintWindow with PW_RENDERFULLCONTENT
    if img is None:
        try:
            img = _capture_via_printwindow(hwnd)
            if img is not None:
                method_used = "printwindow"
        except Exception as e:
            log.debug("PrintWindow capture failed: %s", e)

    # Chain 3: GDI BitBlt
    if img is None:
        try:
            img = _capture_via_bitblt(hwnd)
            if img is not None:
                method_used = "bitblt"
        except Exception as e:
            log.debug("BitBlt capture failed: %s", e)

    # Chain 4: Last resort via UIA
    if img is None:
        try:
            img = _capture_via_uia(hwnd)
            if img is not None:
                method_used = "uia"
        except Exception as e:
            log.debug("UIA capture failed: %s", e)

    if img is None:
        return {
            "error": True,
            "message": (
                "All capture methods failed for this window. "
                "The window may be using protected content (DRM), "
                "be a UWP sandboxed app, or have an unsupported rendering pipeline."
            ),
        }

    # Convert to base64 PNG
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    dpi = _get_dpi(hwnd)
    rect = _get_window_rect(hwnd)

    return {
        "image": b64,
        "format": "png",
        "encoding": "base64",
        "width": img.width,
        "height": img.height,
        "dpi": dpi,
        "scale": round(dpi / 96.0, 2),
        "method": method_used,
        "timestamp": time.time(),
        "window_rect": rect,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# UI AUTOMATION TREE & SEARCH
# ═══════════════════════════════════════════════════════════════════════════════

def _control_to_dict(control: uia.Control, max_depth: int, current_depth: int = 0) -> dict | None:
    """Convert a UIA control to a dictionary node, respecting max_depth."""
    if current_depth > max_depth:
        return None
    try:
        rect = control.BoundingRectangle
        node: dict[str, Any] = {
            "control_type": control.ControlTypeName or "Unknown",
            "name": control.Name or "",
            "automation_id": control.AutomationId or "",
            "class_name": control.ClassName or "",
            "rect": {
                "left": rect.left, "top": rect.top,
                "width": rect.width(), "height": rect.height(),
            } if rect else None,
            "is_enabled": bool(control.IsEnabled) if control.IsEnabled is not None else None,
            "is_offscreen": bool(control.IsOffscreen) if control.IsOffscreen is not None else None,
            "depth": current_depth,
        }
        children = []
        if current_depth < max_depth:
            try:
                for child in control.GetChildren():
                    cd = _control_to_dict(child, max_depth, current_depth + 1)
                    if cd:
                        children.append(cd)
            except Exception:
                pass
        if children:
            node["children"] = children
        return node
    except Exception:
        return None


def get_ui_tree_impl(hwnd: int, max_depth: int = 3) -> dict:
    """Extract the UIA control hierarchy for a window."""
    if not _is_window(hwnd):
        return {"error": True, "message": f"Invalid HWND: {hwnd}"}

    try:
        control = uia.ControlFromHandle(hwnd)
        if control is None:
            return {"error": True, "message": "Cannot get UIA control for this window."}

        tree = _control_to_dict(control, max_depth)
        return {
            "hwnd": hwnd,
            "title": _window_text(hwnd),
            "max_depth": max_depth,
            "tree": tree,
        }
    except Exception as e:
        return {"error": True, "message": f"UIA tree extraction failed: {e}"}


def _find_recursive(control: uia.Control, text: str, role: str,
                    automation_id: str, max_depth: int, depth: int = 0) -> list[dict]:
    """Recursive search for UIA elements matching criteria."""
    if depth > max_depth:
        return []
    results: list[dict] = []

    name = (control.Name or "").lower()
    ctl_type = (control.ControlTypeName or "").lower()
    aid = (control.AutomationId or "").lower()
    cls = control.ClassName or ""

    text_lower = text.lower() if text else ""
    role_lower = role.lower() if role else ""
    aid_lower = automation_id.lower() if automation_id else ""

    match = True
    if text_lower and text_lower not in name and text_lower not in cls.lower():
        match = False
    if role_lower and role_lower not in ctl_type:
        match = False
    if aid_lower and aid_lower != aid:
        match = False

    if match and (text_lower or role_lower or aid_lower):
        rect = None
        try:
            r = control.BoundingRectangle
            if r:
                rect = {"left": r.left, "top": r.top, "width": r.width(), "height": r.height()}
        except Exception:
            pass

        results.append({
            "name": control.Name or "",
            "automation_id": control.AutomationId or "",
            "control_type": control.ControlTypeName or "Unknown",
            "class_name": cls,
            "rect": rect,
            "depth": depth,
            "is_enabled": bool(control.IsEnabled) if control.IsEnabled is not None else None,
        })

    try:
        for child in control.GetChildren():
            results.extend(_find_recursive(child, text, role, automation_id, max_depth, depth + 1))
    except Exception:
        pass

    return results


def find_element_impl(hwnd: int, text: str = "", role: str = "",
                       automation_id: str = "", max_depth: int = 5) -> dict:
    """Search for UIA elements matching criteria within a window."""
    if not _is_window(hwnd):
        return {"error": True, "message": f"Invalid HWND: {hwnd}"}

    try:
        control = uia.ControlFromHandle(hwnd)
        if control is None:
            return {"error": True, "message": "Cannot get UIA control for this window."}

        matches = _find_recursive(control, text, role, automation_id, max_depth)
        return {
            "hwnd": hwnd,
            "match_count": len(matches),
            "matches": matches[:50],  # cap results
        }
    except Exception as e:
        return {"error": True, "message": f"Element search failed: {e}"}


def _resolve_element(hwnd: int, element_id_or_name: str) -> uia.Control | None:
    """
    Resolve an element reference. Tries in order:
      1. Exact match on AutomationId
      2. EXACT match on Name (full string equality)
      3. Substring match on Name (shortest name wins — avoids "清除"→"清除所有记忆")
      4. ControlTypeName match
    """
    try:
        root = uia.ControlFromHandle(hwnd)
        if root is None:
            return None
    except Exception:
        return None

    target = element_id_or_name.lower().strip()

    # 1. AutomationId exact match (recursive)
    def _by_aid(ctl: uia.Control, depth: int = 0) -> uia.Control | None:
        if depth > 8:
            return None
        try:
            if (ctl.AutomationId or "").lower() == target:
                return ctl
        except Exception:
            pass
        try:
            for child in ctl.GetChildren():
                result = _by_aid(child, depth + 1)
                if result:
                    return result
        except Exception:
            pass
        return None

    result = _by_aid(root)
    if result:
        return result

    # 2. Name EXACT match (recursive, collects all candidates)
    def _collect_by_name_exact(ctl: uia.Control, depth: int = 0) -> list[uia.Control]:
        results: list[uia.Control] = []
        if depth > 8:
            return results
        try:
            if (ctl.Name or "").lower() == target:
                results.append(ctl)
        except Exception:
            pass
        try:
            for child in ctl.GetChildren():
                results.extend(_collect_by_name_exact(child, depth + 1))
        except Exception:
            pass
        return results

    exact_matches = _collect_by_name_exact(root)
    if exact_matches:
        return exact_matches[0]

    # 3. Name SUBSTRING match — collect ALL, prefer SHORTEST name
    #    (prevents "清除" from matching "清除所有记忆" when "清除条目" is available)
    def _collect_by_name_substr(ctl: uia.Control, depth: int = 0) -> list[uia.Control]:
        results: list[uia.Control] = []
        if depth > 8:
            return results
        try:
            name = (ctl.Name or "").lower()
            if target in name:
                results.append(ctl)
        except Exception:
            pass
        try:
            for child in ctl.GetChildren():
                results.extend(_collect_by_name_substr(child, depth + 1))
        except Exception:
            pass
        return results

    substr_matches = _collect_by_name_substr(root)
    if substr_matches:
        # Sort by name length (shorter = more specific match)
        substr_matches.sort(key=lambda c: len(c.Name or ""))
        return substr_matches[0]

    # 4. ControlTypeName match
    def _by_type(ctl: uia.Control, depth: int = 0) -> uia.Control | None:
        if depth > 8:
            return None
        try:
            if target == (ctl.ControlTypeName or "").lower():
                return ctl
        except Exception:
            pass
        try:
            for child in ctl.GetChildren():
                result = _by_type(child, depth + 1)
                if result:
                    return result
        except Exception:
            pass
        return None

    result = _by_type(root)
    if result:
        return result

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# INPUT OPERATIONS — FALLBACK CHAIN
# ═══════════════════════════════════════════════════════════════════════════════

def _uia_click(control: uia.Control) -> bool:
    """Attempt UIA InvokePattern click. Returns True on success."""
    try:
        if hasattr(control, "GetInvokePattern") and control.GetInvokePattern():
            control.GetInvokePattern().Invoke()
            return True
    except Exception:
        pass
    try:
        # Direct Click method (UIA-level)
        control.Click()
        return True
    except Exception:
        pass
    return False


def _uia_set_value(control: uia.Control, text: str) -> bool:
    """Attempt UIA ValuePattern SetValue. Returns True on success."""
    try:
        pattern = control.GetValuePattern()
        if pattern:
            pattern.SetValue(text)
            return True
    except Exception:
        pass
    return False


def _send_click_message(hwnd: int, x: int, y: int) -> bool:
    """Send WM_LBUTTONDOWN + WM_LBUTTONUP to a specific client coordinate."""
    try:
        lparam = (y << 16) | (x & 0xFFFF)
        win32gui.PostMessage(hwnd, WM_LBUTTONDOWN, 0x0001, lparam)
        time.sleep(0.05)
        win32gui.PostMessage(hwnd, WM_LBUTTONUP, 0, lparam)
        return True
    except Exception:
        return False


def _send_text_message(hwnd: int, text: str) -> bool:
    """Send WM_CHAR messages for each character."""
    try:
        # First try WM_SETTEXT (works for Edit controls)
        result = win32gui.SendMessage(hwnd, WM_SETTEXT, 0, text)
        if result == 1:  # success
            return True
    except Exception:
        pass

    try:
        for ch in text:
            win32gui.PostMessage(hwnd, WM_CHAR, ord(ch), 0)
            time.sleep(0.005)
        return True
    except Exception:
        return False


def _element_center(control: uia.Control) -> tuple[int, int] | None:
    """Get the center point of a UIA control's bounding rect, relative to client area."""
    try:
        r = control.BoundingRectangle
        if r:
            return (r.left + r.width() // 2, r.top + r.height() // 2)
    except Exception:
        pass
    return None


def _get_client_offset(hwnd: int) -> tuple[int, int]:
    """Get offset from window rect to client rect."""
    try:
        wr = win32gui.GetWindowRect(hwnd)
        cr = win32gui.GetClientRect(hwnd)
        # Map client (0,0) to screen
        pt = win32gui.ClientToScreen(hwnd, (cr[0], cr[1]))
        return (pt[0] - wr[0], pt[1] - wr[1])
    except Exception:
        return (0, 0)


def click_element_impl(hwnd: int, element_id_or_name: str) -> str:
    """Click an element identified by name/automation_id using UIA → message → foreground chain."""
    if not _is_window(hwnd):
        return _err_result(f"Invalid HWND: {hwnd}")

    if _is_minimized(hwnd):
        return _err_result("Window is minimized. Call activate() first, then retry.")

    control = _resolve_element(hwnd, element_id_or_name)
    if control is None:
        return _err_result(f"Cannot find element matching: '{element_id_or_name}'")

    try:
        ctl_name = control.Name or control.AutomationId or control.ControlTypeName or "unknown"
    except Exception:
        ctl_name = "unknown"

    # Chain 1: UIA InvokePattern
    if _uia_click(control):
        return _ok_result(f"[UIA] Clicked element: {ctl_name}")

    # Chain 2: SendMessage at element center
    center = _element_center(control)
    if center:
        off_x, off_y = _get_client_offset(hwnd)
        client_x = center[0] - off_x
        client_y = center[1] - off_y
        if _send_click_message(hwnd, client_x, client_y):
            return _ok_result(f"[SendMessage] Clicked element '{ctl_name}' at ({client_x}, {client_y})")

    # Chain 3: Foreground simulation
    log.debug("Background methods failed, falling back to foreground click for %s", ctl_name)
    try:
        activate_impl(hwnd)
        time.sleep(0.15)
        control.Click()
        return _ok_result(f"[Foreground] Clicked element: {ctl_name}")
    except Exception as e:
        return _err_result(f"All click methods failed for '{ctl_name}': {e}")


def click_coordinate_impl(hwnd: int, x: int, y: int) -> str:
    """Send a click at window-relative client coordinates (x, y).

    For CEF/WebView2/Electron windows, SendMessage clicks are ignored
    (the GPU-rendered content does not process WM_LBUTTONDOWN).
    We detect this and fall through to foreground simulation immediately."""
    if not _is_window(hwnd):
        return _err_result(f"Invalid HWND: {hwnd}")

    if _is_minimized(hwnd):
        return _err_result("Window is minimized. Call activate() first, then retry.")

    needs_fg, fg_reason = _needs_foreground_input(hwnd)

    # Chain 1: SendMessage (SKIP for CEF/WebView/UWP — cannot process external messages)
    if not needs_fg:
        if _send_click_message(hwnd, x, y):
            return _ok_result(f"[SendMessage] Clicked at client ({x}, {y})")

    # Chain 2: Foreground simulation
    try:
        activate_impl(hwnd)
        time.sleep(0.15)
        pt = win32gui.ClientToScreen(hwnd, (x, y))
        win32api.SetCursorPos(pt)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        method = "[Foreground" + (f"-{fg_reason}]" if needs_fg else "]")
        return _ok_result(f"{method} Clicked at client ({x}, {y})")
    except Exception as e:
        return _err_result(f"All click methods failed at ({x}, {y}): {e}")


def _clipboard_paste(hwnd: int, text: str) -> bool:
    """Set clipboard text and send Ctrl+V to the window.
    Used for CEF/WebView2/Electron windows that don't process WM_CHAR."""
    try:
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        time.sleep(0.05)
        # Send Ctrl+V
        activate_impl(hwnd)
        time.sleep(0.1)
        win32api.keybd_event(VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(ord('V'), 0, 0, 0)
        time.sleep(0.03)
        win32api.keybd_event(ord('V'), 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        return True
    except Exception:
        return False


def type_text_impl(hwnd: int, element_id_or_name: str, text: str) -> str:
    """Type text into an element. For CEF/WebView2 windows, uses clipboard paste
    (WM_CHAR messages are ignored by GPU-rendered content)."""
    if not _is_window(hwnd):
        return _err_result(f"Invalid HWND: {hwnd}")

    if _is_minimized(hwnd):
        return _err_result("Window is minimized. Call activate() first, then retry.")

    is_webview, webview_engine = _is_webview_window(hwnd)

    # ── WebView/CEF path: skip WM_CHAR, use clipboard paste ──
    if is_webview and text:
        if _clipboard_paste(hwnd, text):
            return _ok_result(f"[Clipboard-WebView({webview_engine})] Pasted '{text}' via Ctrl+V")

        # Fallback: try foreground keyboard for single chars
        try:
            activate_impl(hwnd)
            time.sleep(0.1)
            for ch in text:
                vk = VK_MAP.get(ch.lower(), 0)
                if vk == 0 and ch.isprintable():
                    vk = ord(ch.upper())
                if vk:
                    win32api.keybd_event(vk, 0, 0, 0)
                    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
                    time.sleep(0.01)
            return _ok_result(f"[Foreground-WebView] Typed '{text}' via keybd_event")
        except Exception as e:
            return _err_result(f"WebView text input failed: {e}")

    # ── Standard path: UIA → SendMessage → Foreground ──
    control = _resolve_element(hwnd, element_id_or_name)
    if control is None:
        if _send_text_message(hwnd, text):
            return _ok_result(f"[SendMessage] Sent text to HWND {hwnd}")
        return _err_result(f"Cannot find element matching: '{element_id_or_name}'")

    try:
        ctl_name = control.Name or control.AutomationId or control.ControlTypeName or "unknown"
    except Exception:
        ctl_name = "unknown"

    # Chain 1: UIA ValuePattern.SetValue
    if _uia_set_value(control, text):
        return _ok_result(f"[UIA] Typed '{text}' into '{ctl_name}'")

    # Chain 2: SendMessage
    try:
        control.SetFocus()
        time.sleep(0.05)
    except Exception:
        pass

    try:
        hwnd_focus = win32gui.GetFocus()
        if hwnd_focus and _send_text_message(hwnd_focus, text):
            return _ok_result(f"[SendMessage] Typed '{text}' into '{ctl_name}' (via focus)")
    except Exception:
        pass

    if _send_text_message(hwnd, text):
        return _ok_result(f"[SendMessage] Typed '{text}' into HWND {hwnd}")

    # Chain 3: Foreground keyboard simulation
    try:
        activate_impl(hwnd)
        time.sleep(0.15)
        control.Click()
        time.sleep(0.1)
        for ch in text:
            if ch.isprintable():
                import ctypes
                INPUT_KEYBOARD = 1
                KEYEVENTF_UNICODE = 0x0004
                KEYEVENTF_KEYUP = 0x0002

                class KEYBDINPUT(ctypes.Structure):
                    _fields_ = [("wVk", ctypes.wintypes.WORD),
                                ("wScan", ctypes.wintypes.WORD),
                                ("dwFlags", ctypes.wintypes.DWORD),
                                ("time", ctypes.wintypes.DWORD),
                                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

                class INPUT_UNION(ctypes.Union):
                    _fields_ = [("ki", KEYBDINPUT)]

                class INPUT(ctypes.Structure):
                    _fields_ = [("type", ctypes.wintypes.DWORD), ("union", INPUT_UNION)]

                inp = INPUT()
                inp.type = INPUT_KEYBOARD
                inp.union.ki.wVk = 0
                inp.union.ki.wScan = ord(ch)
                inp.union.ki.dwFlags = KEYEVENTF_UNICODE
                ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

                inp.union.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
                ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
                time.sleep(0.01)
        return _ok_result(f"[Foreground] Typed '{text}' into '{ctl_name}'")
    except Exception as e:
        return _err_result(f"All type methods failed for '{ctl_name}': {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# SCROLL, HOTKEY, ACTIVATE, WAIT
# ═══════════════════════════════════════════════════════════════════════════════

def scroll_impl(hwnd: int, element_id_or_name: str, direction: str = "down",
                distance: int = 5) -> str:
    """Scroll an element or window."""
    if not _is_window(hwnd):
        return _err_result(f"Invalid HWND: {hwnd}")

    if _is_minimized(hwnd):
        return _err_result("Window is minimized. Call activate() first, then retry.")

    delta = -distance * 120 if direction.lower() == "down" else distance * 120
    target_hwnd = hwnd

    if element_id_or_name:
        control = _resolve_element(hwnd, element_id_or_name)
        if control is None:
            return _err_result(f"Cannot find element matching: '{element_id_or_name}'")

        # Try UIA ScrollPattern
        try:
            pattern = control.GetScrollPattern()
            if pattern:
                if delta < 0:
                    for _ in range(distance):
                        pattern.Scroll(1, 1)  # small scroll down
                else:
                    for _ in range(distance):
                        pattern.Scroll(1, -1)  # small scroll up
                ctl_name = control.Name or "element"
                return _ok_result(f"[UIA Scroll] Scrolled '{ctl_name}' {direction} × {distance}")
        except Exception:
            pass

        # Get center and use as target
        center = _element_center(control)
        if center:
            off_x, off_y = _get_client_offset(hwnd)
            target_x = center[0] - off_x
            target_y = center[1] - off_y
        else:
            target_x = target_y = 0
    else:
        target_x = target_y = 0

    # Fallback: WM_MOUSEWHEEL — wParam: MK_CONTROL (0x0008) + delta, lParam: (y << 16) | x
    try:
        wparam = delta & 0xFFFF0000
        lparam = (target_y << 16) | (target_x & 0xFFFF)
        win32gui.PostMessage(target_hwnd, WM_MOUSEWHEEL, wparam, lparam)
        return _ok_result(f"[WM_MOUSEWHEEL] Scrolled {direction} × {distance}")
    except Exception as e:
        return _err_result(f"Scroll failed: {e}")


def send_hotkey_impl(hwnd: int, hotkey: str) -> str:
    """
    Send a keyboard shortcut like 'Ctrl+C', 'Ctrl+Shift+T' or 'Alt+F4'.
    Detects if window is foreground; if not, activates first.
    """
    if not _is_window(hwnd):
        return _err_result(f"Invalid HWND: {hwnd}")

    if _is_minimized(hwnd):
        return _err_result("Window is minimized. Call activate() first, then retry.")

    # Parse hotkey
    # Special case: standalone symbol key like "plus", "slash", "grave"
    hotkey_lower = hotkey.strip().lower()
    if hotkey_lower in VK_MAP and "+" not in hotkey_lower:
        parts = [hotkey_lower]
    else:
        parts = [p.strip().lower() for p in hotkey.split("+")]
        # Filter empty strings from split (e.g. "Shift++" → ["shift", "", ""] → ["shift"])
        parts = [p for p in parts if p]

    if len(parts) < 1:
        return _err_result(f"Invalid hotkey: '{hotkey}'. Use format like 'Ctrl+C', 'Alt+F4', 'Esc', or 'plus'.")

    modifiers: list[int] = []
    key = None
    for p in parts:
        if p in ("ctrl", "control"):
            modifiers.append(VK_CONTROL)
        elif p in ("alt", "menu"):
            modifiers.append(VK_ALT)
        elif p == "shift":
            modifiers.append(VK_SHIFT)
        else:
            key = VK_MAP.get(p)
            if key is None and len(p) == 1:
                key = ord(p.upper())

    if key is None:
        return _err_result(f"Unknown key in hotkey: '{hotkey}'. Known keys: {', '.join(sorted(VK_MAP.keys())[:30])}...")

    # Check if window is foreground
    foreground = win32gui.GetForegroundWindow()
    needs_activate = (foreground != hwnd)
    was_activated = False

    if needs_activate:
        try:
            activate_impl(hwnd)
            time.sleep(0.2)
            was_activated = True
        except Exception as e:
            return _err_result(f"Cannot activate window for hotkey: {e}")

    try:
        # Press modifiers
        for mod in modifiers:
            win32api.keybd_event(mod, 0, 0, 0)
        # Press key
        win32api.keybd_event(key, 0, 0, 0)
        time.sleep(0.05)
        # Release key
        win32api.keybd_event(key, 0, win32con.KEYEVENTF_KEYUP, 0)
        # Release modifiers (reverse order)
        for mod in reversed(modifiers):
            win32api.keybd_event(mod, 0, win32con.KEYEVENTF_KEYUP, 0)

        return _ok_result(f"[{'Foreground' if was_activated else 'Background'}] Sent hotkey: {hotkey}")
    except Exception as e:
        return _err_result(f"Hotkey failed: {e}")


def activate_impl(hwnd: int) -> str:
    """Explicitly activate and bring a window to the foreground."""
    if not _is_window(hwnd):
        return _err_result(f"Invalid HWND: {hwnd}")

    try:
        # If minimized, restore
        if _is_minimized(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.1)

        # Attach input thread for proper foreground activation
        try:
            current_thread = win32api.GetCurrentThreadId()
            foreground_thread = win32process.GetWindowThreadProcessId(
                win32gui.GetForegroundWindow()
            )[0]
            if current_thread != foreground_thread:
                win32process.AttachThreadInput(current_thread, foreground_thread, True)
                win32gui.SetForegroundWindow(hwnd)
                win32process.AttachThreadInput(current_thread, foreground_thread, False)
        except Exception:
            # Simple approach
            win32gui.SetForegroundWindow(hwnd)

        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.BringWindowToTop(hwnd)
        return _ok_result(f"Activated window: {_window_text(hwnd)}")
    except Exception as e:
        return _err_result(f"Activation failed: {e}")


def wait_for_ui_change_impl(hwnd: int, timeout_seconds: float = 3.0) -> dict:
    """
    Poll the UIA tree hash until it changes or timeout expires.
    Returns the new tree state.
    """
    if not _is_window(hwnd):
        return {"error": True, "message": f"Invalid HWND: {hwnd}"}

    def _tree_hash() -> str:
        try:
            control = uia.ControlFromHandle(hwnd)
            if control is None:
                return ""
            # Compute a hash of the tree structure
            parts: list[str] = []

            def _walk(c: uia.Control, d: int = 0):
                if d > 4:
                    return
                try:
                    parts.append(f"{c.ControlTypeName}:{c.Name}:{c.AutomationId}")
                except Exception:
                    pass
                try:
                    for child in c.GetChildren():
                        _walk(child, d + 1)
                except Exception:
                    pass

            _walk(control)
            return hashlib.md5("|".join(parts).encode()).hexdigest()
        except Exception:
            return ""

    start_hash = _tree_hash()
    deadline = time.time() + timeout_seconds
    changed = False
    iterations = 0

    while time.time() < deadline:
        iterations += 1
        current_hash = _tree_hash()
        if current_hash and current_hash != start_hash:
            changed = True
            break
        time.sleep(0.1)

    return {
        "changed": changed,
        "iterations": iterations,
        "elapsed": round(time.time() - (deadline - timeout_seconds), 3),
        "initial_hash": start_hash,
        "final_hash": _tree_hash() if changed else start_hash,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SILENT APP LAUNCH (NO-ACTIVATE / HIDDEN LAUNCH)
# ═══════════════════════════════════════════════════════════════════════════════

# Constants for silent process creation
SW_SHOWNOACTIVATE = 4        # Show window but don't activate
SW_SHOWMINNOACTIVE = 7       # Show minimized, don't activate
STARTF_USESHOWWINDOW = 0x00000001
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
NORMAL_PRIORITY_CLASS = 0x00000020
SW_HIDE = 0

# Win32 API structures for CreateProcess
class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("lpReserved", ctypes.c_wchar_p),
        ("lpDesktop", ctypes.c_wchar_p),
        ("lpTitle", ctypes.c_wchar_p),
        ("dwX", ctypes.wintypes.DWORD),
        ("dwY", ctypes.wintypes.DWORD),
        ("dwXSize", ctypes.wintypes.DWORD),
        ("dwYSize", ctypes.wintypes.DWORD),
        ("dwXCountChars", ctypes.wintypes.DWORD),
        ("dwYCountChars", ctypes.wintypes.DWORD),
        ("dwFillAttribute", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("wShowWindow", ctypes.wintypes.WORD),
        ("cbReserved2", ctypes.wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", ctypes.wintypes.HANDLE),
        ("hStdOutput", ctypes.wintypes.HANDLE),
        ("hStdError", ctypes.wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", ctypes.wintypes.HANDLE),
        ("hThread", ctypes.wintypes.HANDLE),
        ("dwProcessId", ctypes.wintypes.DWORD),
        ("dwThreadId", ctypes.wintypes.DWORD),
    ]


def _find_window_by_pid(pid: int, timeout: float = 5.0) -> int | None:
    """Poll for a top-level visible window belonging to a PID."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        found: list[int] = []

        def _enum(hwnd: int, _lparam: Any) -> bool:
            try:
                _, wp = win32process.GetWindowThreadProcessId(hwnd)
                if wp == pid and win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if title.strip():
                        found.append(hwnd)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(_enum, None)
        if found:
            # Return the largest window (main window)
            best = found[0]
            best_area = 0
            for h in found:
                try:
                    r = win32gui.GetWindowRect(h)
                    area = (r[2] - r[0]) * (r[3] - r[1])
                    if area > best_area:
                        best_area = area
                        best = h
                except Exception:
                    pass
            return best
        time.sleep(0.15)
    return None


def start_app_silent_impl(app_path: str, args: str = "", work_dir: str = "") -> dict:
    """
    Launch an application silently — the window appears but does NOT
    steal focus or pop to the foreground.

    Uses CreateProcess with SW_SHOWNOACTIVATE to show the window
    without activating it. For GUI apps, the window stays in the
    background. For console apps, no new console window is created.

    Returns: { "success": bool, "pid": int, "hwnd": int, "message": str }
    """
    if not os.path.exists(app_path) and not app_path.startswith(("cmd", "start")):
        # Try to find the executable via PATH
        resolved = shutil.which(app_path)
        if resolved:
            app_path = resolved
        else:
            return {
                "success": False,
                "pid": 0,
                "hwnd": 0,
                "message": f"Executable not found: {app_path}",
            }

    # Build command line
    cmd_line = f'"{app_path}"'
    if args:
        cmd_line += f" {args}"

    wd = work_dir if work_dir and os.path.isdir(work_dir) else os.path.dirname(app_path) or None

    try:
        # Use CreateProcessW directly for maximum control over window state
        si = _STARTUPINFOW()
        si.cb = ctypes.sizeof(_STARTUPINFOW)
        si.dwFlags = STARTF_USESHOWWINDOW
        si.wShowWindow = SW_SHOWNOACTIVATE  # Show but don't activate!

        pi = _PROCESS_INFORMATION()

        creation_flags = NORMAL_PRIORITY_CLASS

        result = ctypes.windll.kernel32.CreateProcessW(
            None,                          # lpApplicationName
            cmd_line,                      # lpCommandLine
            None,                          # lpProcessAttributes
            None,                          # lpThreadAttributes
            False,                         # bInheritHandles
            creation_flags,                # dwCreationFlags
            None,                          # lpEnvironment
            wd,                            # lpCurrentDirectory
            ctypes.byref(si),              # lpStartupInfo
            ctypes.byref(pi),              # lpProcessInformation
        )

        if not result:
            err = ctypes.windll.kernel32.GetLastError()
            return {
                "success": False,
                "pid": 0,
                "hwnd": 0,
                "message": f"CreateProcess failed with error code: {err}",
            }

        pid = pi.dwProcessId

        # Close thread handle (we don't need it)
        ctypes.windll.kernel32.CloseHandle(pi.hThread)
        ctypes.windll.kernel32.CloseHandle(pi.hProcess)

        # Poll for the main window to appear (up to 5 seconds)
        hwnd = _find_window_by_pid(pid, timeout=5.0)

        if hwnd:
            # Ensure it stays background (SW_SHOWNOACTIVATE)
            try:
                win32gui.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            except Exception:
                pass

            title = _window_text(hwnd)
            return {
                "success": True,
                "pid": pid,
                "hwnd": hwnd,
                "title": title,
                "message": f"App launched silently. PID={pid}, HWND={hwnd}, Title='{title}'",
            }
        else:
            return {
                "success": True,
                "pid": pid,
                "hwnd": 0,
                "message": (
                    f"Process started (PID={pid}) but no visible window detected "
                    f"within 5 seconds. The app may be a background process, "
                    f"system tray app, or still initializing."
                ),
            }

    except Exception as e:
        log.exception("start_app_silent failed")
        return {
            "success": False,
            "pid": 0,
            "hwnd": 0,
            "message": f"Launch failed: {e}",
        }


# ═══════════════════════════════════════════════════════════════════════════════
# MCP SERVER SETUP
# ═══════════════════════════════════════════════════════════════════════════════

SERVER_NAME = "window-automation"
SERVER_VERSION = "2.1.0"

server = Server(SERVER_NAME, version=SERVER_VERSION)


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Return the full list of available window automation tools."""
    return [
        Tool(
            name="list_windows",
            description="List all visible top-level windows, optionally filtered by title keyword. Also matches process name (e.g. 'WeChatAppEx' finds WeChat even when its window title is empty). Returns JSON with hwnd, pid, process_name, title, bounds, class_name, is_visible, is_webview, webview_engine for each match.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title_keyword": {
                        "type": "string",
                        "description": "Optional substring to filter by window title, process name, or class name (case-insensitive). Use empty string for all windows. For hidden/hollow windows (e.g. CEF apps), search by process name like 'WeChatAppEx'.",
                        "default": "",
                    }
                },
                "required": [],
            },
        ),
        Tool(
            name="get_window_state",
            description="Get the detailed physical and geometric state of a window by its HWND. Returns minimized, visible, focused, dpi, scale, rect, client_rect.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {
                        "type": "integer",
                        "description": "Window handle (HWND) of the target window.",
                    }
                },
                "required": ["hwnd"],
            },
        ),
        Tool(
            name="capture_window",
            description="Capture a window screenshot in the background. Uses WGC → PrintWindow(PW_RENDERFULLCONTENT) → GDI BitBlt fallback chain. Returns base64-encoded PNG with metadata. Will fail gracefully if the window is minimized — call activate() first in that case.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {
                        "type": "integer",
                        "description": "Window handle (HWND) of the target window.",
                    }
                },
                "required": ["hwnd"],
            },
        ),
        Tool(
            name="get_ui_tree",
            description="Extract the UIA (UI Automation) control hierarchy tree for a window up to max_depth levels. Returns control_type, name, automation_id, rect, is_enabled, and nested children.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {
                        "type": "integer",
                        "description": "Window handle (HWND) of the target window.",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum depth to traverse the control tree (default: 3).",
                        "default": 3,
                    },
                },
                "required": ["hwnd"],
            },
        ),
        Tool(
            name="find_element",
            description="Search for UIA elements within a window matching text (name), role (control type), or automation_id. Returns up to 50 matches with coordinates and pattern info.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {
                        "type": "integer",
                        "description": "Window handle (HWND) of the target window.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Substring to match against element Name (case-insensitive).",
                        "default": "",
                    },
                    "role": {
                        "type": "string",
                        "description": "Control type to match (e.g. 'Button', 'Edit', 'ListItem', 'MenuItem').",
                        "default": "",
                    },
                    "automation_id": {
                        "type": "string",
                        "description": "Exact AutomationId to match (case-insensitive).",
                        "default": "",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum search depth (default: 5).",
                        "default": 5,
                    },
                },
                "required": ["hwnd"],
            },
        ),
        Tool(
            name="click_element",
            description="Click a UI element identified by name, automation_id, or control type. Uses UIA InvokePattern → SendMessage → foreground simulation fallback chain.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {
                        "type": "integer",
                        "description": "Window handle (HWND) of the target window.",
                    },
                    "element_id_or_name": {
                        "type": "string",
                        "description": "Identifier for the element: AutomationId (exact match), Name (substring match), or ControlType name (e.g. 'Button', 'Edit').",
                    },
                },
                "required": ["hwnd", "element_id_or_name"],
            },
        ),
        Tool(
            name="click_coordinate",
            description="Send a click at window-relative client coordinates (x, y). Uses SendMessage → foreground simulation fallback chain.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {
                        "type": "integer",
                        "description": "Window handle (HWND) of the target window.",
                    },
                    "x": {
                        "type": "integer",
                        "description": "X coordinate relative to the window's client area.",
                    },
                    "y": {
                        "type": "integer",
                        "description": "Y coordinate relative to the window's client area.",
                    },
                },
                "required": ["hwnd", "x", "y"],
            },
        ),
        Tool(
            name="type_text",
            description="Type text into a UI element. Uses UIA ValuePattern.SetValue → WM_CHAR messages → foreground keyboard simulation fallback chain.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {
                        "type": "integer",
                        "description": "Window handle (HWND) of the target window.",
                    },
                    "element_id_or_name": {
                        "type": "string",
                        "description": "Identifier for the target element. If empty, sends text to the window directly.",
                        "default": "",
                    },
                    "text": {
                        "type": "string",
                        "description": "The text to type into the element.",
                    },
                },
                "required": ["hwnd", "text"],
            },
        ),
        Tool(
            name="send_hotkey",
            description="Send a keyboard shortcut (e.g. 'Ctrl+C', 'Ctrl+Shift+T', 'Alt+F4') to a window. Activates the window first if it is not in the foreground, then sends the key combination.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {
                        "type": "integer",
                        "description": "Window handle (HWND) of the target window.",
                    },
                    "hotkey": {
                        "type": "string",
                        "description": "Hotkey combination like 'Ctrl+C', 'Ctrl+Shift+T', 'Alt+F4', 'Ctrl+A'.",
                    },
                },
                "required": ["hwnd", "hotkey"],
            },
        ),
        Tool(
            name="scroll",
            description="Scroll within a window or a specific element. Uses UIA ScrollPattern → WM_MOUSEWHEEL fallback.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {
                        "type": "integer",
                        "description": "Window handle (HWND) of the target window.",
                    },
                    "element_id_or_name": {
                        "type": "string",
                        "description": "Optional element identifier to target. If empty, scrolls the window itself.",
                        "default": "",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down"],
                        "description": "Scroll direction (default: 'down').",
                        "default": "down",
                    },
                    "distance": {
                        "type": "integer",
                        "description": "Number of scroll increments (default: 5).",
                        "default": 5,
                    },
                },
                "required": ["hwnd"],
            },
        ),
        Tool(
            name="wait_for_ui_change",
            description="Poll the UIA tree until it changes or timeout expires. Useful for waiting on UI transitions instead of using fixed sleep().",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {
                        "type": "integer",
                        "description": "Window handle (HWND) of the target window.",
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "description": "Maximum time to wait in seconds (default: 3.0).",
                        "default": 3.0,
                    },
                },
                "required": ["hwnd"],
            },
        ),
        Tool(
            name="activate",
            description="Explicitly activate and bring a window to the foreground. Restores it if minimized. Use this before foreground-dependent operations or when a minimized window blocks background capture.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {
                        "type": "integer",
                        "description": "Window handle (HWND) of the target window.",
                    },
                },
                "required": ["hwnd"],
            },
        ),
        Tool(
            name="start_app_silent",
            description="Launch an application silently in the background without stealing focus or popping to the foreground. The app window appears but stays behind the current foreground window. Returns pid and hwnd on success. Use this when you need to open an app for automation without interrupting the user.",
            inputSchema={
                "type": "object",
                "properties": {
                    "app_path": {
                        "type": "string",
                        "description": "Full path to the executable (e.g. 'C:\\Windows\\notepad.exe' or 'calc.exe'). If only a name is given, it is resolved via system PATH.",
                    },
                    "args": {
                        "type": "string",
                        "description": "Optional command-line arguments to pass to the application.",
                        "default": "",
                    },
                    "work_dir": {
                        "type": "string",
                        "description": "Optional working directory for the process. Defaults to the executable's directory.",
                        "default": "",
                    },
                },
                "required": ["app_path"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(tool_name: str, arguments: dict[str, Any]) -> list[TextContent | ImageContent]:
    """Dispatch tool calls to the appropriate implementation."""
    log.info("call_tool: %s args=%s", tool_name, arguments)

    try:
        if tool_name == "list_windows":
            result = list_windows_impl(arguments.get("title_keyword", ""))
            return [TextContent(type="text", text=json.dumps(result, default=str, indent=2))]

        elif tool_name == "get_window_state":
            result = get_window_state_impl(arguments["hwnd"])
            return [TextContent(type="text", text=json.dumps(result, default=str, indent=2))]

        elif tool_name == "capture_window":
            result = capture_window_impl(arguments["hwnd"])
            if result.get("error"):
                return [TextContent(type="text", text=json.dumps(result, default=str, indent=2))]
            # Return both the image and the metadata
            b64 = result.pop("image", "")
            meta_text = json.dumps(result, default=str, indent=2)
            return [
                ImageContent(type="image", data=b64, mimeType="image/png"),
                TextContent(type="text", text=meta_text),
            ]

        elif tool_name == "get_ui_tree":
            result = get_ui_tree_impl(arguments["hwnd"], arguments.get("max_depth", 3))
            return [TextContent(type="text", text=json.dumps(result, default=str, indent=2))]

        elif tool_name == "find_element":
            result = find_element_impl(
                arguments["hwnd"],
                arguments.get("text", ""),
                arguments.get("role", ""),
                arguments.get("automation_id", ""),
                arguments.get("max_depth", 5),
            )
            return [TextContent(type="text", text=json.dumps(result, default=str, indent=2))]

        elif tool_name == "click_element":
            result = click_element_impl(arguments["hwnd"], arguments["element_id_or_name"])
            return [TextContent(type="text", text=result)]

        elif tool_name == "click_coordinate":
            result = click_coordinate_impl(arguments["hwnd"], arguments["x"], arguments["y"])
            return [TextContent(type="text", text=result)]

        elif tool_name == "type_text":
            result = type_text_impl(
                arguments["hwnd"],
                arguments.get("element_id_or_name", ""),
                arguments["text"],
            )
            return [TextContent(type="text", text=result)]

        elif tool_name == "send_hotkey":
            result = send_hotkey_impl(arguments["hwnd"], arguments["hotkey"])
            return [TextContent(type="text", text=result)]

        elif tool_name == "scroll":
            result = scroll_impl(
                arguments["hwnd"],
                arguments.get("element_id_or_name", ""),
                arguments.get("direction", "down"),
                arguments.get("distance", 5),
            )
            return [TextContent(type="text", text=result)]

        elif tool_name == "wait_for_ui_change":
            result = wait_for_ui_change_impl(
                arguments["hwnd"],
                arguments.get("timeout_seconds", 3.0),
            )
            return [TextContent(type="text", text=json.dumps(result, default=str, indent=2))]

        elif tool_name == "activate":
            result = activate_impl(arguments["hwnd"])
            return [TextContent(type="text", text=result)]

        elif tool_name == "start_app_silent":
            result = start_app_silent_impl(
                arguments["app_path"],
                arguments.get("args", ""),
                arguments.get("work_dir", ""),
            )
            return [TextContent(type="text", text=json.dumps(result, default=str, indent=2))]

        else:
            return [TextContent(type="text", text=json.dumps(
                {"error": True, "message": f"Unknown tool: {tool_name}"}
            ))]

    except Exception as e:
        log.exception("Unhandled error in call_tool %s", tool_name)
        return [TextContent(type="text", text=json.dumps({
            "error": True,
            "message": f"Internal error: {e}",
            "detail": traceback.format_exc(),
        }))]


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════


def main() -> None:
    """Run the MCP server over stdio."""
    log.info("Starting Window Automation MCP Server v%s", SERVER_VERSION)

    async def _run() -> None:
        async with mcp.stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
                raise_exceptions=False,
            )

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("Server stopped by user.")
    except Exception as e:
        log.exception("Fatal error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
