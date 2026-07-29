#!/usr/bin/env python3
"""
Comprehensive test suite for window_automation_mcp.py.
Tests all 12 tools against real windows on the system.
"""
import asyncio
import json
import subprocess
import sys
import os
import time

# Add venv to path if needed
VENV_PYTHON = r"G:\install_mcp_UI_interaction\.venv\Scripts\python.exe"
SERVER_SCRIPT = r"G:\install_mcp_UI_interaction\window_automation_mcp.py"

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run_tests():
    """Run all tests against the MCP server."""
    print("=" * 70)
    print("Window Automation MCP Server — Comprehensive Test Suite")
    print("=" * 70)

    server_params = StdioServerParameters(
        command=VENV_PYTHON,
        args=[SERVER_SCRIPT],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize
            await session.initialize()
            print("\n✓ Server initialized successfully")

            # List tools
            tools_result = await session.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            print(f"\n✓ Server reports {len(tool_names)} tools: {tool_names}")

            # ── Test 1: list_windows ──
            print("\n─── Test 1: list_windows ───")
            result = await session.call_tool("list_windows", {"title_keyword": ""})
            windows = json.loads(result.content[0].text)
            print(f"  Found {len(windows)} visible windows")
            if windows:
                w = windows[0]
                print(f"  Sample: hwnd={w.get('hwnd')}, title='{w.get('title')}', "
                      f"pid={w.get('pid')}, minimized={w.get('is_minimized')}")

            # ── Test 2: list_windows with filter ──
            print("\n─── Test 2: list_windows (filtered) ───")
            result = await session.call_tool("list_windows", {"title_keyword": "Program Manager"})
            filtered = json.loads(result.content[0].text)
            print(f"  Filtered to {len(filtered)} window(s)")
            if filtered:
                print(f"  Title: '{filtered[0].get('title')}'")

            # Find a test window — use any visible window
            test_hwnd = None
            test_title = ""

            # Try to find a good test window
            for w in windows:
                title = w.get("title", "")
                hwnd = w.get("hwnd", 0)
                if hwnd > 0 and not w.get("is_minimized", True):
                    test_hwnd = hwnd
                    test_title = title
                    break

            if test_hwnd is None:
                print("\n⚠ No test windows available — skipping window-specific tests")
                return

            print(f"\n  Using test window: hwnd={test_hwnd}, title='{test_title}'")

            # ── Test 3: get_window_state ──
            print("\n─── Test 3: get_window_state ───")
            result = await session.call_tool("get_window_state", {"hwnd": test_hwnd})
            state = json.loads(result.content[0].text)
            print(f"  State: minimized={state.get('minimized')}, visible={state.get('visible')}, "
                  f"focused={state.get('focused')}, dpi={state.get('dpi')}, "
                  f"scale={state.get('scale')}")
            print(f"  Rect: {state.get('rect')}")

            # ── Test 4: capture_window ──
            print("\n─── Test 4: capture_window ───")
            result = await session.call_tool("capture_window", {"hwnd": test_hwnd})
            if len(result.content) >= 2 and hasattr(result.content[0], 'data'):
                img_data = result.content[0].data
                meta = json.loads(result.content[1].text)
                print(f"  ✓ Captured! Method: {meta.get('method')}, "
                      f"Size: {meta.get('width')}×{meta.get('height')}, "
                      f"Base64 length: {len(img_data)} chars")
            elif len(result.content) == 1:
                # May be error text
                text = result.content[0].text
                data = json.loads(text)
                if data.get("error"):
                    print(f"  ⚠ Capture returned error: {data.get('message')}")
                else:
                    print(f"  Response: {text[:200]}")
            else:
                print(f"  Response has {len(result.content)} content block(s)")

            # ── Test 5: get_ui_tree ──
            print("\n─── Test 5: get_ui_tree ───")
            result = await session.call_tool("get_ui_tree", {"hwnd": test_hwnd, "max_depth": 2})
            tree = json.loads(result.content[0].text)
            if tree.get("error"):
                print(f"  ⚠ Error: {tree.get('message')}")
            else:
                print(f"  ✓ Tree extracted (max_depth={tree.get('max_depth')})")
                root = tree.get("tree", {})
                print(f"  Root: control_type='{root.get('control_type')}', name='{root.get('name')}'")
                kids = root.get("children", [])
                print(f"  Children count: {len(kids)}")

            # ── Test 6: find_element ──
            print("\n─── Test 6: find_element ───")
            result = await session.call_tool("find_element", {
                "hwnd": test_hwnd,
                "text": "",
                "role": "",
                "automation_id": "",
                "max_depth": 4,
            })
            elems = json.loads(result.content[0].text)
            if elems.get("error"):
                print(f"  ⚠ Error: {elems.get('message')}")
            else:
                print(f"  ✓ Found {elems.get('match_count')} elements (capped at 50)")
                matches = elems.get("matches", [])
                if matches:
                    print(f"  First match: '{matches[0].get('name')}' "
                          f"({matches[0].get('control_type')})")

            # ── Test 7: activate ──
            print("\n─── Test 7: activate ───")
            result = await session.call_tool("activate", {"hwnd": test_hwnd})
            text = result.content[0].text
            print(f"  {text}")

            # ── Test 8: click_element ──
            print("\n─── Test 8: click_element ──")
            # Try to find a clickable element first
            find_result = await session.call_tool("find_element", {
                "hwnd": test_hwnd,
                "role": "Button",
                "max_depth": 4,
            })
            find_data = json.loads(find_result.content[0].text)
            matches = find_data.get("matches", [])
            if matches:
                target = matches[0]
                name = target.get("name") or target.get("automation_id") or "Button"
                print(f"  Targeting: '{name}'")
                result = await session.call_tool("click_element", {
                    "hwnd": test_hwnd,
                    "element_id_or_name": name,
                })
                print(f"  {result.content[0].text}")
            else:
                print("  ⚠ No Button found in window — skipping click_element test")

            # ── Test 9: click_coordinate ──
            print("\n─── Test 9: click_coordinate ──")
            result = await session.call_tool("click_coordinate", {
                "hwnd": test_hwnd,
                "x": 50,
                "y": 50,
            })
            print(f"  {result.content[0].text}")

            # ── Test 10: type_text ──
            print("\n─── Test 10: type_text ──")
            find_edit = await session.call_tool("find_element", {
                "hwnd": test_hwnd,
                "role": "Edit",
                "max_depth": 4,
            })
            edit_data = json.loads(find_edit.content[0].text)
            edit_matches = edit_data.get("matches", [])
            if edit_matches:
                e = edit_matches[0]
                name = e.get("name") or e.get("automation_id") or "Edit"
                print(f"  Targeting edit: '{name}'")
                result = await session.call_tool("type_text", {
                    "hwnd": test_hwnd,
                    "element_id_or_name": name,
                    "text": "test123",
                })
                print(f"  {result.content[0].text}")
            else:
                print("  ⚠ No Edit control found — sending text to window directly")
                result = await session.call_tool("type_text", {
                    "hwnd": test_hwnd,
                    "element_id_or_name": "",
                    "text": "test",
                })
                print(f"  {result.content[0].text}")

            # ── Test 11: scroll ──
            print("\n─── Test 11: scroll ──")
            result = await session.call_tool("scroll", {
                "hwnd": test_hwnd,
                "element_id_or_name": "",
                "direction": "down",
                "distance": 3,
            })
            print(f"  {result.content[0].text}")

            # ── Test 12: send_hotkey ──
            print("\n─── Test 12: send_hotkey ──")
            result = await session.call_tool("send_hotkey", {
                "hwnd": test_hwnd,
                "hotkey": "Ctrl+C",
            })
            print(f"  {result.content[0].text}")

            # ── Test 13: wait_for_ui_change ──
            print("\n─── Test 13: wait_for_ui_change ──")
            result = await session.call_tool("wait_for_ui_change", {
                "hwnd": test_hwnd,
                "timeout_seconds": 1.0,
            })
            wdata = json.loads(result.content[0].text)
            print(f"  Changed: {wdata.get('changed')}, iterations: {wdata.get('iterations')}, "
                  f"elapsed: {wdata.get('elapsed')}s")

            # ── Test 14: Error handling — invalid HWND ──
            print("\n─── Test 14: Error handling (invalid HWND) ───")
            result = await session.call_tool("get_window_state", {"hwnd": 99999999})
            data = json.loads(result.content[0].text)
            print(f"  Invalid HWND → error={data.get('error')}: {data.get('message', '')[:100]}")

            # ── Test 15: Error handling — unknown tool ──
            print("\n─── Test 15: Error handling (unknown tool) ──")
            try:
                result = await session.call_tool("nonexistent_tool", {})
                print(f"  Response: {result.content[0].text[:200]}")
            except Exception as e:
                print(f"  Expected exception: {e}")

            print("\n" + "=" * 70)
            print("All tests completed!")
            print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_tests())
