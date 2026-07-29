#!/usr/bin/env python3
"""
Round 2: Edge-case and robustness testing for window_automation_mcp.py.
Tests minimized windows, rapid sequential calls, non-existent elements, etc.
"""
import asyncio
import json
import subprocess

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
            print("Round 2: Edge-case & Robustness Tests")
            print("=" * 70)

            # Get all windows
            result = await session.call_tool("list_windows", {"title_keyword": ""})
            windows = json.loads(result.content[0].text)
            print(f"\nBaseline: {len(windows)} windows visible")

            # ── Test A: Rapid sequential calls (stress test) ──
            print("\n─── Test A: Rapid sequential get_window_state × 10 ───")
            errors = 0
            for i in range(10):
                try:
                    r = await asyncio.wait_for(
                        session.call_tool("get_window_state", {"hwnd": windows[0]["hwnd"]}),
                        timeout=5.0,
                    )
                    data = json.loads(r.content[0].text)
                    if data.get("error"):
                        errors += 1
                except Exception as e:
                    errors += 1
            print(f"  Errors: {errors}/10")

            # ── Test B: Capture multiple different windows ──
            print("\n─── Test B: Capture multiple windows ───")
            tested = 0
            for w in windows[:5]:
                hwnd = w["hwnd"]
                title = w["title"]
                if w.get("is_minimized"):
                    continue
                try:
                    r = await asyncio.wait_for(
                        session.call_tool("capture_window", {"hwnd": hwnd}),
                        timeout=10.0,
                    )
                    if len(r.content) >= 2:
                        meta = json.loads(r.content[1].text)
                        method = meta.get("method", "?")
                        w_size = f"{meta.get('width', '?')}×{meta.get('height', '?')}"
                        print(f"  ✓ '{title[:50]}' → {method} {w_size}")
                    else:
                        data = json.loads(r.content[0].text)
                        if data.get("error"):
                            print(f"  ⚠ '{title[:50]}' → error: {data.get('message', '')[:80]}")
                except Exception as e:
                    print(f"  ✗ '{title[:50]}' → exception: {e}")
                tested += 1
            print(f"  Captured {tested} windows")

            # ── Test C: UIA tree depth variations ──
            print("\n─── Test C: UI tree with different depths ───")
            hwnd = windows[0]["hwnd"]
            for depth in [1, 2, 5]:
                r = await session.call_tool("get_ui_tree", {"hwnd": hwnd, "max_depth": depth})
                data = json.loads(r.content[0].text)
                if not data.get("error"):
                    # Count total nodes
                    def count_nodes(node, d=0):
                        c = 1 if node else 0
                        for child in node.get("children", []) if node else []:
                            c += count_nodes(child, d + 1)
                        return c

                    node_count = count_nodes(data.get("tree"))
                    print(f"  depth={depth} → {node_count} nodes")
                else:
                    print(f"  depth={depth} → error: {data.get('message', '')[:60]}")

            # ── Test D: find_element with various filters ──
            print("\n─── Test D: find_element filter combinations ──")
            filters = [
                {"text": "OK"},
                {"role": "Window"},
                {"automation_id": "test"},
                {"text": "OK", "role": "Button"},
            ]
            for f in filters:
                args = {"hwnd": hwnd, "max_depth": 4}
                args.update(f)
                r = await session.call_tool("find_element", args)
                data = json.loads(r.content[0].text)
                count = data.get("match_count", 0)
                desc = ", ".join(f"{k}={v}" for k, v in f.items())
                print(f"  [{desc}] → {count} matches")

            # ── Test E: Concurrent calls (parallel tool invocations) ──
            print("\n─── Test E: Concurrent tool calls (parallel) ───")
            tasks = [
                session.call_tool("get_window_state", {"hwnd": hwnd}),
                session.call_tool("get_ui_tree", {"hwnd": hwnd, "max_depth": 2}),
                session.call_tool("find_element", {"hwnd": hwnd, "text": "OK"}),
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            ok = sum(1 for r in results if not isinstance(r, Exception))
            print(f"  Concurrent calls: {ok}/{len(tasks)} succeeded")

            # ── Test F: type_text empty string ──
            print("\n─── Test F: type_text edge cases ──")
            r = await session.call_tool("type_text", {
                "hwnd": hwnd, "element_id_or_name": "", "text": "",
            })
            print(f"  Empty text: {r.content[0].text[:100]}")

            r = await session.call_tool("type_text", {
                "hwnd": hwnd, "element_id_or_name": "", "text": "Hello 世界 🌍",
            })
            print(f"  Unicode text: {r.content[0].text[:100]}")

            # ── Test G: scroll directions ──
            print("\n─── Test G: scroll directions ──")
            for d in ["up", "down"]:
                r = await session.call_tool("scroll", {
                    "hwnd": hwnd, "direction": d, "distance": 3,
                })
                resp = r.content[0].text
                if "success" in resp.lower():
                    print(f"  scroll {d}: ✓")
                else:
                    print(f"  scroll {d}: {resp[:80]}")

            # ── Test H: click_coordinate boundary values ──
            print("\n─── Test H: click_coordinate edge cases ──")
            for coords in [(0, 0), (-1, -1), (99999, 99999)]:
                r = await session.call_tool("click_coordinate", {
                    "hwnd": hwnd, "x": coords[0], "y": coords[1],
                })
                print(f"  click({coords[0]}, {coords[1]}): {r.content[0].text[:100]}")

            # ── Test I: UIA search depth boundary ──
            print("\n─── Test I: UIA tree with max_depth=0 ───")
            r = await session.call_tool("get_ui_tree", {"hwnd": hwnd, "max_depth": 0})
            data = json.loads(r.content[0].text)
            print(f"  depth=0: error={data.get('error')}, root={'present' if data.get('tree') else 'none'}")

            print("\n" + "=" * 70)
            print("Edge-case tests complete!")
            print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_tests())
