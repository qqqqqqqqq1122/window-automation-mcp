#!/usr/bin/env python3
"""
Use window-automation MCP to send "ok" to WeChat 文件传输助手.
"""
import asyncio
import json
import time

VENV_PYTHON = r"G:\install_mcp_UI_interaction\.venv\Scripts\python.exe"
SERVER_SCRIPT = r"G:\install_mcp_UI_interaction\window_automation_mcp.py"

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    server_params = StdioServerParameters(command=VENV_PYTHON, args=[SERVER_SCRIPT])
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Step 1: Find WeChat window
            print("🔍 查找微信窗口...")
            result = await session.call_tool("list_windows", {"title_keyword": "微信"})
            windows = json.loads(result.content[0].text)
            wechat_wins = [w for w in windows if "微信" in w.get("title", "")]
            print(f"  找到 {len(wechat_wins)} 个微信窗口")
            for w in wechat_wins:
                print(f"    hwnd={w['hwnd']}, title='{w['title']}'")

            if not wechat_wins:
                print("❌ 未找到微信窗口！请确保微信已打开。")
                return

            hwnd = wechat_wins[0]["hwnd"]
            print(f"\n✅ 使用窗口: hwnd={hwnd}")

            # Step 2: Activate WeChat (need foreground for hotkey-based navigation)
            print("\n📌 激活微信窗口...")
            result = await session.call_tool("activate", {"hwnd": hwnd})
            print(f"  {json.loads(result.content[0].text)['data']}")
            await asyncio.sleep(0.5)

            # Step 3: Use Ctrl+F to search for 文件传输助手
            print("\n🔍 搜索 '文件传输助手'...")
            result = await session.call_tool("send_hotkey", {
                "hwnd": hwnd, "hotkey": "Ctrl+F",
            })
            print(f"  Ctrl+F: {json.loads(result.content[0].text)}")
            await asyncio.sleep(0.5)

            # Step 4: Get UIA tree to find the search box
            print("\n📋 获取 UIA 树...")
            result = await session.call_tool("get_ui_tree", {"hwnd": hwnd, "max_depth": 4})
            tree = json.loads(result.content[0].text)
            if not tree.get("error"):
                # Print tree to find the search input
                def print_tree(node, indent=0):
                    if node is None:
                        return
                    prefix = "  " * indent
                    ctl = node.get("control_type", "?")
                    name = node.get("name", "")
                    aid = node.get("automation_id", "")
                    if name or aid:
                        print(f"{prefix}[{ctl}] name='{name[:50]}' aid='{aid}'")
                    for child in node.get("children", []):
                        print_tree(child, indent + 1)

                print_tree(tree.get("tree"))

            # Step 5: Find and type into search box
            print("\n🔍 查找搜索输入框...")
            result = await session.call_tool("find_element", {
                "hwnd": hwnd, "role": "Edit", "max_depth": 4,
            })
            edits = json.loads(result.content[0].text)
            matches = edits.get("matches", [])
            print(f"  找到 {len(matches)} 个 Edit 控件")

            # Type the search text into the first Edit control
            if matches:
                # Type into WeChat main window first to ensure focus
                for i, m in enumerate(matches[:3]):
                    name = m.get("name", "")
                    aid = m.get("automation_id", "")
                    print(f"  [{i}] name='{name}', aid='{aid}'")

            # Step 6: Type the search text
            print("\n⌨️ 输入搜索文本 '文件传输助手'...")
            # First try UIA - find any Edit and type
            if matches:
                best_edit = matches[0].get("name") or matches[0].get("automation_id")
                if not best_edit:
                    best_edit = "Edit"
                result = await session.call_tool("type_text", {
                    "hwnd": hwnd,
                    "element_id_or_name": best_edit,
                    "text": "文件传输助手",
                })
                print(f"  UIA输入: {json.loads(result.content[0].text)}")
            else:
                # Send Ctrl+F first then type to the window
                result = await session.call_tool("send_hotkey", {
                    "hwnd": hwnd, "hotkey": "Ctrl+F",
                })
                await asyncio.sleep(0.3)
                result = await session.call_tool("type_text", {
                    "hwnd": hwnd,
                    "element_id_or_name": "",
                    "text": "文件传输助手",
                })
                print(f"  直接输入: {json.loads(result.content[0].text)}")

            await asyncio.sleep(1.0)

            # Step 7: Press Enter to open the chat
            print("\n⏎ 按 Enter 打开聊天...")
            result = await session.call_tool("send_hotkey", {
                "hwnd": hwnd, "hotkey": "Enter",
            })
            print(f"  Enter: {json.loads(result.content[0].text)}")
            await asyncio.sleep(1.0)

            # Step 8: Find the message input box and type "ok"
            print("\n⌨️ 输入消息 'ok'...")
            result = await session.call_tool("get_ui_tree", {"hwnd": hwnd, "max_depth": 5})
            tree2 = json.loads(result.content[0].text)

            # Find all Edit controls
            result = await session.call_tool("find_element", {
                "hwnd": hwnd, "role": "Edit", "max_depth": 5,
            })
            edits2 = json.loads(result.content[0].text)
            matches2 = edits2.get("matches", [])
            print(f"  当前找到 {len(matches2)} 个 Edit 控件")

            # The message input is typically the largest Edit control at the bottom
            msg_edit = None
            max_area = 0
            for m in matches2:
                r = m.get("rect")
                if r:
                    area = r.get("width", 0) * r.get("height", 0)
                    if area > max_area:
                        max_area = area
                        msg_edit = m

            if msg_edit:
                name = msg_edit.get("name") or msg_edit.get("automation_id") or "Edit"
                print(f"  目标输入框: name='{name}', rect={msg_edit.get('rect')}")
                result = await session.call_tool("type_text", {
                    "hwnd": hwnd,
                    "element_id_or_name": name,
                    "text": "ok",
                })
                print(f"  输入结果: {json.loads(result.content[0].text)}")
            elif matches2:
                # Try first Edit
                name = matches2[0].get("name") or matches2[0].get("automation_id") or "Edit"
                result = await session.call_tool("type_text", {
                    "hwnd": hwnd,
                    "element_id_or_name": name,
                    "text": "ok",
                })
                print(f"  输入结果 (fallback): {json.loads(result.content[0].text)}")
            else:
                # Last resort: send to window directly
                result = await session.call_tool("type_text", {
                    "hwnd": hwnd,
                    "element_id_or_name": "",
                    "text": "ok",
                })
                print(f"  直接输入: {json.loads(result.content[0].text)}")

            await asyncio.sleep(0.3)

            # Step 9: Press Enter to send
            print("\n📤 按 Enter 发送...")
            result = await session.call_tool("send_hotkey", {
                "hwnd": hwnd, "hotkey": "Enter",
            })
            print(f"  发送: {json.loads(result.content[0].text)}")

            print("\n✅ 完成！已向微信文件传输助手发送 'ok'")


if __name__ == "__main__":
    asyncio.run(main())
