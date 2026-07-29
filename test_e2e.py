#!/usr/bin/env python3
"""
Round 3: End-to-end test with Notepad.
Opens Notepad, captures it, clicks, types text, verifies UIA tree.
"""
import asyncio
import json
import subprocess
import time

VENV_PYTHON = r"G:\install_mcp_UI_interaction\.venv\Scripts\python.exe"
SERVER_SCRIPT = r"G:\install_mcp_UI_interaction\window_automation_mcp.py"

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run_tests():
    server_params = StdioServerParameters(command=VENV_PYTHON, args=[SERVER_SCRIPT])

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("=" * 70)
            print("Round 3: End-to-End Tests with Notepad")
            print("=" * 70)

            # ── Open Notepad ──
            print("\n─── Opening Notepad ───")
            subprocess.Popen(["notepad.exe"])
            await asyncio.sleep(1.5)

            # Find Notepad window
            result = await session.call_tool("list_windows", {"title_keyword": "Notepad"})
            windows = json.loads(result.content[0].text)
            print(f"  Found {len(windows)} Notepad windows")

            if not windows:
                # Try the alternate title
                result = await session.call_tool("list_windows", {"title_keyword": "记事本"})
                windows = json.loads(result.content[0].text)
                print(f"  Found {len(windows)} windows (Chinese title)")

            if not windows:
                # Broader search
                result = await session.call_tool("list_windows", {"title_keyword": ""})
                all_windows = json.loads(result.content[0].text)
                # Look for notepad
                for w in all_windows:
                    if "notepad" in w["title"].lower() or "记事本" in w["title"]:
                        windows.append(w)
                print(f"  Found {len(windows)} Notepad windows (broad search)")

            if windows:
                hwnd = windows[0]["hwnd"]
                print(f"  Using: hwnd={hwnd}, title='{windows[0]['title']}'")

                # ── Capture Notepad ──
                print("\n─── Capture Notepad ───")
                result = await session.call_tool("capture_window", {"hwnd": hwnd})
                if len(result.content) >= 2:
                    meta = json.loads(result.content[1].text)
                    print(f"  ✓ Method: {meta.get('method')}, Size: {meta.get('width')}×{meta.get('height')}")
                    # Save the image
                    import base64
                    img_data = result.content[0].data
                    with open(r"G:\install_mcp_UI_interaction\notepad_capture.png", "wb") as f:
                        f.write(base64.b64decode(img_data))
                    print(f"  ✓ Saved to notepad_capture.png")
                else:
                    data = json.loads(result.content[0].text)
                    print(f"  Result: {data.get('message', data)}")

                # ── UIA tree ──
                print("\n─── Notepad UIA Tree ───")
                result = await session.call_tool("get_ui_tree", {"hwnd": hwnd, "max_depth": 3})
                tree = json.loads(result.content[0].text)
                if tree.get("error"):
                    print(f"  Error: {tree.get('message')}")
                else:
                    def print_tree(node, indent=0):
                        if node is None:
                            return
                        prefix = "  " * indent
                        ctl = node.get("control_type", "?")
                        name = node.get("name", "")[:40]
                        aid = node.get("automation_id", "")[:20]
                        print(f"{prefix}[{ctl}] name='{name}' aid='{aid}'")
                        for child in node.get("children", []):
                            print_tree(child, indent + 1)

                    root = tree.get("tree")
                    print(f"  Root: max_depth={tree.get('max_depth')}")
                    if root:
                        print_tree(root)

                # ── Find Edit control ──
                print("\n─── Find Edit control ──")
                result = await session.call_tool("find_element", {
                    "hwnd": hwnd, "role": "Edit", "max_depth": 4,
                })
                elems = json.loads(result.content[0].text)
                print(f"  Found {elems.get('match_count')} Edit control(s)")
                matches = elems.get("matches", [])
                edit_name = ""
                if matches:
                    e = matches[0]
                    edit_name = e.get("name") or e.get("automation_id") or "Edit"
                    print(f"  Edit: name='{e.get('name')}', automation_id='{e.get('automation_id')}', "
                          f"rect={e.get('rect')}")

                # ── Type text into Edit ──
                print("\n─── Type text into Notepad ──")
                test_text = "Hello from MCP Window Automation!\r\nBackground typing works!"
                result = await session.call_tool("type_text", {
                    "hwnd": hwnd,
                    "element_id_or_name": edit_name if edit_name else "Edit",
                    "text": test_text,
                })
                print(f"  {result.content[0].text}")

                # ── Send Ctrl+S (Save As dialog) ──
                print("\n─── Send Ctrl+S hotkey ───")
                result = await session.call_tool("send_hotkey", {
                    "hwnd": hwnd, "hotkey": "Ctrl+S",
                })
                print(f"  {result.content[0].text}")
                await asyncio.sleep(1.0)

                # Check if Save dialog appeared
                result = await session.call_tool("list_windows", {"title_keyword": "Save As"})
                save_wins = json.loads(result.content[0].text)
                if not save_wins:
                    result = await session.call_tool("list_windows", {"title_keyword": "另存为"})
                    save_wins = json.loads(result.content[0].text)
                print(f"  Save dialog appeared: {len(save_wins) > 0}")

                if save_wins:
                    save_hwnd = save_wins[0]["hwnd"]
                    # Press Escape to close save dialog
                    result = await session.call_tool("send_hotkey", {
                        "hwnd": save_hwnd, "hotkey": "Esc",
                    })
                    print(f"  Closed save dialog: {result.content[0].text[:100]}")

                # ── Close Notepad (don't save) ──
                print("\n─── Close Notepad ───")
                result = await session.call_tool("send_hotkey", {
                    "hwnd": hwnd, "hotkey": "Alt+F4",
                })
                print(f"  Alt+F4: {result.content[0].text}")
                await asyncio.sleep(0.5)

                # Handle "Don't Save" dialog if present
                result = await session.call_tool("list_windows", {"title_keyword": "Notepad"})
                remaining = json.loads(result.content[0].text)
                if remaining:
                    # Send Alt+N (Don't Save) or just close
                    for w in remaining:
                        result = await session.call_tool("send_hotkey", {
                            "hwnd": w["hwnd"], "hotkey": "Alt+N",
                        })
                        print(f"  Don't Save dialog handled")

            else:
                print("  ⚠ Could not find Notepad window — skipping Notepad tests")

            # ── Additional robustness: Test against Task Manager ──
            print("\n─── Bonus: Test with Calculator ───")
            try:
                subprocess.Popen(["calc.exe"])
                await asyncio.sleep(1.0)
                result = await session.call_tool("list_windows", {"title_keyword": "Calculator"})
                calc_wins = json.loads(result.content[0].text)
                if not calc_wins:
                    result = await session.call_tool("list_windows", {"title_keyword": "计算器"})
                    calc_wins = json.loads(result.content[0].text)
                if calc_wins:
                    chwnd = calc_wins[0]["hwnd"]
                    # Capture calculator
                    result = await session.call_tool("capture_window", {"hwnd": chwnd})
                    if len(result.content) >= 2:
                        meta = json.loads(result.content[1].text)
                        print(f"  ✓ Calc captured: {meta.get('method')} {meta.get('width')}×{meta.get('height')}")
                    # Close calculator
                    result = await session.call_tool("send_hotkey", {
                        "hwnd": chwnd, "hotkey": "Alt+F4",
                    })
                    print(f"  ✓ Calc closed")
                else:
                    print("  ⚠ Calculator not found")
            except Exception as e:
                print(f"  Calc test skipped: {e}")

            print("\n" + "=" * 70)
            print("End-to-end tests complete!")
            print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_tests())
