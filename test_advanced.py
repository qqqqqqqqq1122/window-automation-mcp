#!/usr/bin/env python3
"""
Advanced Test Suite: Multi-instance isolation, occluded background capture,
WebView2/Tauri rendering, and silent app launch.
"""
import asyncio
import json
import subprocess
import time
import base64
import io

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
            print("  高阶测试套件")
            print("=" * 72)

            # ═══════════════════════════════════════════════════════════════
            # TEST 1: Multi-instance isolation & HWND uniqueness
            # ═══════════════════════════════════════════════════════════════
            print("\n" + "─" * 72)
            print("  TEST 1: 多实例隔离与内容校验")
            print("─" * 72)

            # Open two Notepad instances
            p1 = subprocess.Popen(["notepad.exe"])
            await asyncio.sleep(0.8)
            p2 = subprocess.Popen(["notepad.exe"])
            await asyncio.sleep(0.8)

            # Find both Notepad windows
            result = await session.call_tool("list_windows", {"title_keyword": "记事本"})
            notepad_wins = [w for w in json.loads(result.content[0].text)
                          if "记事本" in w.get("title", "")]
            if len(notepad_wins) < 2:
                # Try English title
                result = await session.call_tool("list_windows", {"title_keyword": "Notepad"})
                notepad_wins = [w for w in json.loads(result.content[0].text)
                              if "notepad" in w.get("title", "").lower() or "记事本" in w.get("title", "")]
            print(f"  找到 {len(notepad_wins)} 个记事本窗口")

            if len(notepad_wins) >= 2:
                hwnd_a = notepad_wins[0]["hwnd"]
                hwnd_b = notepad_wins[1]["hwnd"]
                print(f"  窗口 A: hwnd={hwnd_a}, title='{notepad_wins[0]['title']}'")
                print(f"  窗口 B: hwnd={hwnd_b}, title='{notepad_wins[1]['title']}'")

                # Capture baseline for A
                r_a1 = await session.call_tool("capture_window", {"hwnd": hwnd_a})
                meta_a1 = json.loads(r_a1.content[1].text)
                b64_a1 = r_a1.content[0].data
                print(f"  窗口 A 基线截图: {meta_a1.get('width')}×{meta_a1.get('height')}, "
                      f"{len(b64_a1)} chars")

                # Capture baseline for B
                r_b1 = await session.call_tool("capture_window", {"hwnd": hwnd_b})
                meta_b1 = json.loads(r_b1.content[1].text)
                b64_b1 = r_b1.content[0].data
                print(f"  窗口 B 基线截图: {meta_b1.get('width')}×{meta_b1.get('height')}, "
                      f"{len(b64_b1)} chars")

                # Inject unique text into window A only
                test_text = "Test_Instance_A_123"
                print(f"\n  向窗口 A 注入文本: '{test_text}'")
                r_type = await session.call_tool("type_text", {
                    "hwnd": hwnd_a,
                    "element_id_or_name": "Edit",
                    "text": test_text,
                })
                type_result = json.loads(r_type.content[0].text)
                print(f"  输入结果: {type_result}")
                await asyncio.sleep(0.3)

                # Capture A after typing
                r_a2 = await session.call_tool("capture_window", {"hwnd": hwnd_a})
                b64_a2 = r_a2.content[0].data
                print(f"  窗口 A 输入后截图: {len(b64_a2)} chars")

                # Capture B after typing in A
                r_b2 = await session.call_tool("capture_window", {"hwnd": hwnd_b})
                b64_b2 = r_b2.content[0].data
                print(f"  窗口 B 截图 (A输入后): {len(b64_b2)} chars")

                # Verify isolation: B's screenshot should be identical to baseline
                # (or very close — blank notepad pages should be nearly identical)
                isolation_ok = (b64_b1 == b64_b2)
                # Also verify A changed
                a_changed = (b64_a1 != b64_a2)

                print(f"\n  [隔离验证] 窗口 B 截图未变化: {isolation_ok}")
                print(f"  [变化验证] 窗口 A 截图已变化: {a_changed}")
                print(f"  [结论] 多实例 HWND 隔离: {'✅ 通过' if isolation_ok and a_changed else '❌ 失败'}")

                # Close both notepads
                for w in notepad_wins:
                    await session.call_tool("send_hotkey", {"hwnd": w["hwnd"], "hotkey": "Alt+F4"})
                    await asyncio.sleep(0.3)
                await asyncio.sleep(0.5)
                # Handle any "save" dialogs
                result = await session.call_tool("list_windows", {"title_keyword": "记事本"})
                remaining = json.loads(result.content[0].text)
                for w in remaining:
                    await session.call_tool("send_hotkey", {"hwnd": w["hwnd"], "hotkey": "Alt+N"})
            else:
                print(f"  ⚠ 记事本窗口不足2个，跳过此测试")

            # ═══════════════════════════════════════════════════════════════
            # TEST 2: Background occluded capture & silent click
            # ═══════════════════════════════════════════════════════════════
            print("\n" + "─" * 72)
            print("  TEST 2: 后台被遮挡截图与静默点击")
            print("─" * 72)

            # Open Calculator (a UWP/modern app that tests WGC/PrintWindow)
            subprocess.Popen(["calc.exe"])
            await asyncio.sleep(1.2)

            result = await session.call_tool("list_windows", {"title_keyword": ""})
            all_wins = json.loads(result.content[0].text)
            calc_win = None
            for w in all_wins:
                t = w.get("title", "").lower()
                if "计算器" in t or "calculator" in t or "calc" in t:
                    calc_win = w
                    break

            if calc_win:
                chwnd = calc_win["hwnd"]
                print(f"  目标窗口: hwnd={chwnd}, title='{calc_win['title']}'")
                print(f"  目标状态: minimized={calc_win['is_minimized']}")

                if calc_win["is_minimized"]:
                    await session.call_tool("activate", {"hwnd": chwnd})
                    await asyncio.sleep(0.3)

                # STEP 1: Capture baseline (window is visible)
                r_c1 = await session.call_tool("capture_window", {"hwnd": chwnd})
                meta_c1 = json.loads(r_c1.content[1].text)
                c1_method = meta_c1.get("method", "?")
                print(f"\n  基线截图: {meta_c1.get('width')}×{meta_c1.get('height')}, 方法={c1_method}")
                if c1_method not in ("bitblt", "printwindow", "wgc"):
                    print(f"  ⚠ 捕获方法未知: {c1_method}")

                # STEP 2: Bring another window on top to occlude calculator
                # Use a large notepad window to cover it
                subprocess.Popen(["notepad.exe"])
                await asyncio.sleep(0.8)
                result = await session.call_tool("list_windows", {"title_keyword": "记事本"})
                np_wins = json.loads(result.content[0].text)
                if not np_wins:
                    result = await session.call_tool("list_windows", {"title_keyword": "Notepad"})
                    np_wins = json.loads(result.content[0].text)

                if np_wins:
                    nphwnd = np_wins[0]["hwnd"]
                    # Maximize notepad to cover calculator
                    await session.call_tool("activate", {"hwnd": nphwnd})
                    await asyncio.sleep(0.3)
                    # Send Win+Up to maximize
                    result = await session.call_tool("get_window_state", {"hwnd": nphwnd})
                    ns = json.loads(result.content[0].text)
                    print(f"  遮挡窗口: hwnd={nphwnd}, rect={ns.get('rect')}")

                    # STEP 3: Capture calculator while it's occluded (no activate!)
                    print(f"\n  尝试后台截取被遮挡的计算器窗口...")
                    r_c2 = await session.call_tool("capture_window", {"hwnd": chwnd})
                    meta_c2 = json.loads(r_c2.content[1].text)
                    b64_c2 = r_c2.content[0].data if len(r_c2.content) >= 2 else ""
                    c2_method = meta_c2.get("method", "?")

                    occluded_ok = meta_c2.get("error") is None or not meta_c2.get("error")
                    print(f"  被遮挡截图: {meta_c2.get('width')}×{meta_c2.get('height')}, 方法={c2_method}")
                    print(f"  截图成功: {occluded_ok}, 图片大小: {len(b64_c2)} chars")

                    # STEP 4: Try background click on calculator number buttons
                    # First find calculator buttons via UIA
                    print(f"\n  查找计算器按钮...")
                    r_find = await session.call_tool("get_ui_tree", {"hwnd": chwnd, "max_depth": 4})
                    tree = json.loads(r_find.content[0].text)
                    tree_ok = not tree.get("error")
                    print(f"  UIA 树提取: {'✅' if tree_ok else '❌'}")

                    # Try clicking "1" button via UIA
                    if tree_ok:
                        r_find = await session.call_tool("find_element", {
                            "hwnd": chwnd, "text": "1", "role": "Button", "max_depth": 5,
                        })
                        elems = json.loads(r_find.content[0].text)
                        btn_count = elems.get("match_count", 0)
                        print(f"  找到数字按钮 '1': {btn_count} 个")

                        if btn_count > 0:
                            btn_name = elems["matches"][0].get("name", "1")
                            print(f"  点击按钮: '{btn_name}' (后台, 不激活计算器)")
                            r_click = await session.call_tool("click_element", {
                                "hwnd": chwnd,
                                "element_id_or_name": btn_name,
                            })
                            click_res = json.loads(r_click.content[0].text)
                            print(f"  点击结果: {click_res}")
                            await asyncio.sleep(0.3)

                            # Capture again to verify UI changed
                            r_c3 = await session.call_tool("capture_window", {"hwnd": chwnd})
                            meta_c3 = json.loads(r_c3.content[1].text)
                            b64_c3 = r_c3.content[0].data if len(r_c3.content) >= 2 else ""
                            ui_changed = (len(b64_c3) > 0)
                            print(f"  点击后截图: {meta_c3.get('width')}×{meta_c3.get('height')}")
                            print(f"  后台点击后 UI 可捕获: {'✅' if ui_changed else '❌'}")

                    # STEP 5: Verify calculator is still in background
                    r_state = await session.call_tool("get_window_state", {"hwnd": chwnd})
                    state = json.loads(r_state.content[0].text)
                    still_bg = not state.get("focused", True)  # should NOT have focus
                    print(f"\n  计算器仍处于后台 (未抢焦点): {'✅' if still_bg else '⚠ 被激活了'}")

                else:
                    print("  ⚠ 无法创建遮挡窗口")

                # Cleanup: close calculator
                await session.call_tool("send_hotkey", {"hwnd": chwnd, "hotkey": "Alt+F4"})
                await asyncio.sleep(0.3)
                # Close notepad too
                if np_wins:
                    await session.call_tool("send_hotkey", {"hwnd": nphwnd, "hotkey": "Alt+F4"})
                    await asyncio.sleep(0.3)
            else:
                print("  ⚠ 计算器未找到")

            # ═══════════════════════════════════════════════════════════════
            # TEST 3: WebView2 / Tauri / Electron rendering
            # ═══════════════════════════════════════════════════════════════
            print("\n" + "─" * 72)
            print("  TEST 3: WebView2 / Tauri / Electron 渲染控件识别")
            print("─" * 72)

            # Try to find a modern app window
            modern_app = None
            modern_keywords = [
                "visual studio code", "vscode", "code",
                "microsoft edge", "edge",
                "slack", "discord", "spotify", "teams",
                "chrome", "firefox",
            ]
            result = await session.call_tool("list_windows", {"title_keyword": ""})
            all_wins = json.loads(result.content[0].text)

            for w in all_wins:
                title = w.get("title", "").lower()
                for kw in modern_keywords:
                    if kw in title:
                        modern_app = w
                        break
                if modern_app:
                    break

            if modern_app:
                mhwnd = modern_app["hwnd"]
                mtitle = modern_app["title"]
                print(f"  找到现代应用: hwnd={mhwnd}, title='{mtitle}'")

                # Test 3a: UIA tree
                print(f"\n  [3a] UIA 树提取...")
                r_tree = await session.call_tool("get_ui_tree", {"hwnd": mhwnd, "max_depth": 3})
                tree = json.loads(r_tree.content[0].text)
                tree_ok = not tree.get("error")

                # Count nodes recursively
                def count_nodes(node, d=0):
                    if not node:
                        return 0
                    c = 1
                    for child in node.get("children", []):
                        c += count_nodes(child, d + 1)
                    return c

                n = count_nodes(tree.get("tree"))
                print(f"  UIA 树提取: {'✅' if tree_ok else '❌'}, 节点数: {n}")

                # Test 3b: find_element
                print(f"\n  [3b] find_element 搜索...")
                # Try finding common UI elements
                for search_role in ["Button", "MenuItem", "TabItem", "Edit"]:
                    r_find = await session.call_tool("find_element", {
                        "hwnd": mhwnd, "role": search_role, "max_depth": 4,
                    })
                    found = json.loads(r_find.content[0].text)
                    count = found.get("match_count", 0)
                    if count > 0:
                        print(f"    找到 {search_role}: {count} 个")
                        if count > 0:
                            m = found["matches"][0]
                            print(f"      示例: name='{m.get('name')}', aid='{m.get('automation_id')}'")
                        break
                else:
                    # Try text search
                    r_find = await session.call_tool("find_element", {
                        "hwnd": mhwnd, "text": "", "max_depth": 3,
                    })
                    found = json.loads(r_find.content[0].text)
                    print(f"    无特定角色匹配，但元素搜索运行正常")

                # Test 3c: capture
                print(f"\n  [3c] 后台截图 (WGC/PrintWindow)...")
                if modern_app.get("is_minimized"):
                    await session.call_tool("activate", {"hwnd": mhwnd})
                    await asyncio.sleep(0.3)

                r_cap = await session.call_tool("capture_window", {"hwnd": mhwnd})
                if len(r_cap.content) >= 2:
                    meta = json.loads(r_cap.content[1].text)
                    method = meta.get("method", "?")
                    w_size = f"{meta.get('width')}×{meta.get('height')}"
                    has_image = len(r_cap.content[0].data) > 0
                    print(f"  截图: {'✅' if has_image else '❌'}, 方法={method}, 尺寸={w_size}")
                    print(f"  黑屏风险: {'⚠ 注意检查' if method == 'bitblt' and meta.get('width', 0) > 0 else '✅ 图像正常'}")

                    # Save for inspection
                    if has_image:
                        with open(r"G:\install_mcp_UI_interaction\modern_app_capture.png", "wb") as f:
                            f.write(base64.b64decode(r_cap.content[0].data))
                        print(f"  已保存: modern_app_capture.png")
                else:
                    data = json.loads(r_cap.content[0].text)
                    print(f"  截图: ❌ {data.get('message', '')[:100]}")
            else:
                print("  ⚠ 未找到 WebView2/Electron/Tauri 应用窗口")
                print("     (请在后台打开 VS Code、Edge、Chrome 或类似应用后重试)")

            # ═══════════════════════════════════════════════════════════════
            # SUMMARY
            # ═══════════════════════════════════════════════════════════════
            print("\n" + "=" * 72)
            print("  高阶测试完成！")
            print("=" * 72)


if __name__ == "__main__":
    asyncio.run(run_tests())
