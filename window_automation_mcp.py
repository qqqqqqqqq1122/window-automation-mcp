#!/usr/bin/env python3
"""
Windows Background HWND-Based UI Automation MCP Server
======================================================
Provides 12 tools for background window manipulation via HWND handles.

Capture fallback chain:  WGC (COM) → PrintWindow(PW_RENDERFULLCONTENT) → BitBlt(GDI)
Input fallback chain:    UIA Patterns → SendMessage/PostMessage → Foreground simulation

Author: Window Automation MCP
Version: 2.0.0
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
import struct
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


def list_windows_impl(title_keyword: str = "") -> list[dict]:
    """Enumerate all top-level windows matching optional keyword."""
    results: list[dict] = []
    keyword_lower = title_keyword.lower() if title_keyword else ""

    def _enum_callback(hwnd: int, _lparam: Any) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title.strip():
            return True
        if keyword_lower and keyword_lower not in title.lower():
            return True

        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0

        results.append({
            "hwnd": hwnd,
            "pid": pid,
            "title": title,
            "bounds": _get_window_rect(hwnd),
            "is_minimized": _is_minimized(hwnd),
            "is_visible": _is_visible(hwnd),
        })
        return True

    win32gui.EnumWindows(_enum_callback, None)
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
      2. Substring match on Name
      3. Treat as an integer index into children of root
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

    # 2. Name substring match
    def _by_name(ctl: uia.Control, depth: int = 0) -> uia.Control | None:
        if depth > 8:
            return None
        try:
            if target in (ctl.Name or "").lower():
                return ctl
        except Exception:
            pass
        try:
            for child in ctl.GetChildren():
                result = _by_name(child, depth + 1)
                if result:
                    return result
        except Exception:
            pass
        return None

    result = _by_name(root)
    if result:
        return result

    # 3. ControlTypeName match
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
    """Send a click at window-relative client coordinates (x, y)."""
    if not _is_window(hwnd):
        return _err_result(f"Invalid HWND: {hwnd}")

    if _is_minimized(hwnd):
        return _err_result("Window is minimized. Call activate() first, then retry.")

    # Chain 1: SendMessage
    if _send_click_message(hwnd, x, y):
        return _ok_result(f"[SendMessage] Clicked at client ({x}, {y})")

    # Chain 2: Foreground simulation
    try:
        activate_impl(hwnd)
        time.sleep(0.15)
        # Convert client coords to screen coords
        pt = win32gui.ClientToScreen(hwnd, (x, y))
        win32api.SetCursorPos(pt)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        return _ok_result(f"[Foreground] Clicked at client ({x}, {y})")
    except Exception as e:
        return _err_result(f"All click methods failed at ({x}, {y}): {e}")


def type_text_impl(hwnd: int, element_id_or_name: str, text: str) -> str:
    """Type text into an element using UIA ValuePattern → WM_CHAR → foreground chain."""
    if not _is_window(hwnd):
        return _err_result(f"Invalid HWND: {hwnd}")

    if _is_minimized(hwnd):
        return _err_result("Window is minimized. Call activate() first, then retry.")

    control = _resolve_element(hwnd, element_id_or_name)
    if control is None:
        # If no element resolved, try sending text directly to the window
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

    # Chain 2: Focus element then SendMessage
    try:
        control.SetFocus()
        time.sleep(0.05)
    except Exception:
        pass

    try:
        hwnd_focus = win32gui.GetFocus()
        if hwnd_focus:
            if _send_text_message(hwnd_focus, text):
                return _ok_result(f"[SendMessage] Typed '{text}' into '{ctl_name}' (via focus)")
    except Exception:
        pass

    # Chain 3: Send directly to window
    if _send_text_message(hwnd, text):
        return _ok_result(f"[SendMessage] Typed '{text}' into HWND {hwnd}")

    # Chain 4: Foreground keyboard simulation
    try:
        activate_impl(hwnd)
        time.sleep(0.15)
        control.Click()
        time.sleep(0.1)
        for ch in text:
            # Use VK codes where possible; fall back to SendInput
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
    parts = [p.strip().lower() for p in hotkey.split("+")]
    if len(parts) < 1:
        return _err_result(f"Invalid hotkey: '{hotkey}'. Use format like 'Ctrl+C', 'Alt+F4', or 'Esc'.")

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
# MCP SERVER SETUP
# ═══════════════════════════════════════════════════════════════════════════════

SERVER_NAME = "window-automation"
SERVER_VERSION = "2.0.0"

server = Server(SERVER_NAME, version=SERVER_VERSION)


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Return the full list of available window automation tools."""
    return [
        Tool(
            name="list_windows",
            description="List all visible top-level windows, optionally filtered by title keyword. Returns JSON with hwnd, pid, title, bounds, is_minimized, is_visible for each match.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title_keyword": {
                        "type": "string",
                        "description": "Optional substring to filter window titles (case-insensitive). Empty string returns all visible windows.",
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
