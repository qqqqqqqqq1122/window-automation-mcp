#!/usr/bin/env python3
"""
Test: start_app_silent — silent background app launch without focus stealing.
"""
import asyncio
import json

VENV_PYTHON = r"G:\install_mcp_UI_interaction\.venv\Scripts\python.exe"
SERVER_SCRIPT = r"G:\install_mcp_UI_interaction\window_automation_mcp.py"

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run_tests():
    server_params = StdioServerParameters(command=VENV_PYTHON, args=[SERVER_SCRIPT])
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("=" * 72)
            print("  start_app_silent 静默启动测试")
            print("=" * 72)

            # First check tool count
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            print(f"\n工具数量: {len(tool_names)}")
            assert "start_app_silent" in tool_names, "start_app_silent not registered!"
            print(f"✓ start_app_silent 已注册")

            # ── Test 1: Launch Notepad silently ──
            print("\n─── Test 1: 静默启动记事本 ───")
            # Record current foreground window before launch
            fg_before = None
            try:
                import win32gui
                fg_before = win32gui.GetForegroundWindow()
                fg_title = win32gui.GetWindowText(fg_before)
                print(f"  启动前前台窗口: '{fg_title}'")
            except Exception:
                pass

            result = await session.call_tool("start_app_silent", {
                "app_path": "notepad.exe",
                "args": "",
                "work_dir": "",
            })
            launch_data = json.loads(result.content[0].text)
            print(f"  启动结果: {json.dumps(launch_data, indent=2, ensure_ascii=False)}")

            assert launch_data.get("success"), f"Launch failed: {launch_data}"
            pid = launch_data["pid"]
            hwnd = launch_data.get("hwnd", 0)
            print(f"  PID={pid}, HWND={hwnd}")

            await asyncio.sleep(0.5)

            # ── Test 2: Verify focus was NOT stolen ──
            print("\n─── Test 2: 验证焦点未被抢占 ───")
            try:
                import win32gui
                fg_after = win32gui.GetForegroundWindow()
                fg_title_after = win32gui.GetWindowText(fg_after)
                focus_stolen = (fg_after == hwnd)
                print(f"  启动后前台窗口: '{fg_title_after}'")
                print(f"  焦点被抢占: {focus_stolen}")
                print(f"  静默启动: {'✅ 成功 (焦点未动)' if not focus_stolen else '⚠ 焦点被抢了'}")
            except Exception as e:
                print(f"  Focus check error: {e}")

            # ── Test 3: Verify window is visible ──
            print("\n─── Test 3: 验证窗口可见 ───")
            if hwnd:
                result = await session.call_tool("get_window_state", {"hwnd": hwnd})
                state = json.loads(result.content[0].text)
                print(f"  可见: {state.get('visible')}")
                print(f"  最小化: {state.get('minimized')}")
                print(f"  标题: {state.get('title')}")
                print(f"  获取焦点: {state.get('focused')}")
                assert state.get("visible"), "Window should be visible!"
                print(f"  ✅ 窗口可见 (且最小化={state.get('minimized')}, 焦点={state.get('focused')})")

                # ── Test 4: Capture the silently-launched window ──
                print("\n─── Test 4: 截取静默启动的窗口 ──")
                result = await session.call_tool("capture_window", {"hwnd": hwnd})
                if len(result.content) >= 2:
                    meta = json.loads(result.content[1].text)
                    print(f"  截图: {meta.get('width')}×{meta.get('height')}, 方法={meta.get('method')}")
                    print(f"  ✅ 静默启动的窗口可正常截图")
                else:
                    print(f"  ⚠ {result.content[0].text[:100]}")

                # ── Test 5: Interact with silently-launched window ──
                print("\n─── Test 5: 与静默启动窗口交互 ──")
                result = await session.call_tool("type_text", {
                    "hwnd": hwnd,
                    "element_id_or_name": "Edit",
                    "text": "Silently launched!",
                })
                print(f"  输入: {json.loads(result.content[0].text)}")

                # ── Test 6: Launch Calculator silently ──
                print("\n─── Test 6: 静默启动计算器 ──")
                result = await session.call_tool("start_app_silent", {
                    "app_path": "calc.exe",
                })
                calc_data = json.loads(result.content[0].text)
                print(f"  启动结果: success={calc_data.get('success')}, pid={calc_data.get('pid')}")
                if calc_data.get("success"):
                    calc_hwnd = calc_data.get("hwnd", 0)
                    if calc_hwnd:
                        await asyncio.sleep(0.8)
                        result = await session.call_tool("capture_window", {"hwnd": calc_hwnd})
                        if len(result.content) >= 2:
                            meta = json.loads(result.content[1].text)
                            print(f"  计算器截图: {meta.get('width')}×{meta.get('height')}")
                        await session.call_tool("send_hotkey", {"hwnd": calc_hwnd, "hotkey": "Alt+F4"})
                        print(f"  ✅ 计算器已关闭")
                else:
                    print(f"  ⚠ 计算器启动: {calc_data.get('message')}")

                # Cleanup notepad
                await session.call_tool("send_hotkey", {"hwnd": hwnd, "hotkey": "Alt+F4"})
                await asyncio.sleep(0.3)
                print(f"\n  ✅ 记事本已关闭")
            else:
                print(f"  ⚠ HWND=0, 窗口可能尚未渲染")

            print("\n" + "=" * 72)
            print("  start_app_silent 测试完成！")
            print("=" * 72)


if __name__ == "__main__":
    asyncio.run(run_tests())
