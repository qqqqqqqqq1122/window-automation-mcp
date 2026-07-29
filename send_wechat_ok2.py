import asyncio, json, time
VENV_PYTHON = r"G:\install_mcp_UI_interaction\.venv\Scripts\python.exe"
SERVER_SCRIPT = r"G:\install_mcp_UI_interaction\window_automation_mcp.py"
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    sp = StdioServerParameters(command=VENV_PYTHON, args=[SERVER_SCRIPT])
    async with stdio_client(sp) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            # Find WeChat by process name
            print("1. Finding WeChat...")
            res = await s.call_tool("list_windows", {"title_keyword": "WeChatAppEx"})
            wins = json.loads(res.content[0].text)
            wechat = [w for w in wins if w['is_visible'] and w['bounds']['width'] > 100]
            if not wechat:
                print("NO WECHAT FOUND")
                return
            hwnd = wechat[0]['hwnd']
            print(f"   HWND={hwnd}, process={wechat[0]['process_name']}, webview={wechat[0]['is_webview']}({wechat[0]['webview_engine']})")

            # Activate
            print("\n2. Activating...")
            res = await s.call_tool("activate", {"hwnd": hwnd})
            print(f"   {json.loads(res.content[0].text)}")
            await asyncio.sleep(0.5)

            # Click search area at top-left of WeChat (left sidebar)
            print("\n3. Clicking search box...")
            res = await s.call_tool("click_coordinate", {"hwnd": hwnd, "x": 130, "y": 35})
            print(f"   {json.loads(res.content[0].text)}")
            await asyncio.sleep(0.5)

            # Type search text via clipboard paste
            print("\n4. Typing '文件传输助手'...")
            res = await s.call_tool("type_text", {
                "hwnd": hwnd, "element_id_or_name": "", "text": "文件传输助手",
            })
            print(f"   {json.loads(res.content[0].text)}")
            await asyncio.sleep(1.0)

            # Press Enter to open chat
            print("\n5. Opening chat (Enter)...")
            res = await s.call_tool("send_hotkey", {"hwnd": hwnd, "hotkey": "Enter"})
            print(f"   {json.loads(res.content[0].text)}")
            await asyncio.sleep(1.0)

            # Type "ok" via clipboard paste
            print("\n6. Typing 'ok'...")
            res = await s.call_tool("type_text", {
                "hwnd": hwnd, "element_id_or_name": "", "text": "ok",
            })
            print(f"   {json.loads(res.content[0].text)}")
            await asyncio.sleep(0.5)

            # Press Enter to send
            print("\n7. Sending (Enter)...")
            res = await s.call_tool("send_hotkey", {"hwnd": hwnd, "hotkey": "Enter"})
            print(f"   {json.loads(res.content[0].text)}")

            print("\n✅ Done! Check WeChat.")

asyncio.run(main())
