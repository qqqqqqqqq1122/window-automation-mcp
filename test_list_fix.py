import asyncio, json
VENV_PYTHON = r"G:\install_mcp_UI_interaction\.venv\Scripts\python.exe"
SERVER_SCRIPT = r"G:\install_mcp_UI_interaction\window_automation_mcp.py"
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    sp = StdioServerParameters(command=VENV_PYTHON, args=[SERVER_SCRIPT])
    async with stdio_client(sp) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            # Test 1: Search by process name
            print("=== Test 1: keyword='WeChatAppEx' ===")
            res = await s.call_tool("list_windows", {"title_keyword": "WeChatAppEx"})
            wins = json.loads(res.content[0].text)
            print(f"Found {len(wins)} windows")
            for w in wins:
                print(f"  hwnd={w['hwnd']} pid={w['pid']} process={w.get('process_name','?')} "
                      f"title='{w['title']}' visible={w['is_visible']} "
                      f"webview={w.get('is_webview',False)}({w.get('webview_engine','')}) "
                      f"bounds={w['bounds']}")

            # Test 2: Search by keyword "微信" on title
            print("\n=== Test 2: keyword='微信' ===")
            res = await s.call_tool("list_windows", {"title_keyword": "微信"})
            wins = json.loads(res.content[0].text)
            print(f"Found {len(wins)} windows")
            for w in wins:
                print(f"  hwnd={w['hwnd']} title='{w['title']}' visible={w['is_visible']}")

            # Test 3: Empty search (all windows)
            print(f"\n=== Test 3: keyword='' (all windows) ===")
            res = await s.call_tool("list_windows", {"title_keyword": ""})
            wins = json.loads(res.content[0].text)
            print(f"Found {len(wins)} windows total")
            # Show WeChat-related
            for w in wins:
                pn = w.get('process_name','')
                if 'wechat' in pn.lower() or '微信' in w.get('title',''):
                    print(f"  WECHAT: hwnd={w['hwnd']} process={pn} title='{w['title']}' "
                          f"visible={w['is_visible']} bounds={w['bounds']}")

asyncio.run(main())
