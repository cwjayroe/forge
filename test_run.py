#!/usr/bin/env python3
"""
End-to-end test script for the Forge backend.

Usage:
  1. Start the server: uvicorn backend.main:app --reload
  2. Run this script:  python test_run.py

The script creates a simple task, triggers a run, streams the WebSocket
output, then prints the final run summary.
"""
import asyncio
import json
import sys

try:
    import httpx
except ImportError:
    print("httpx not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("websockets not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

BASE_HTTP = "http://localhost:8000"
BASE_WS = "ws://localhost:8000"


async def main():
    async with httpx.AsyncClient(base_url=BASE_HTTP, timeout=30.0) as client:
        # ------------------------------------------------------------------
        # 1. Health check
        # ------------------------------------------------------------------
        try:
            resp = await client.get("/tasks")
            resp.raise_for_status()
            print(f"[OK] Server is up. Existing tasks: {len(resp.json())}")
        except httpx.ConnectError:
            print("[ERROR] Cannot connect to server at http://localhost:8000")
            print("        Start it with: uvicorn backend.main:app --reload")
            sys.exit(1)

        # ------------------------------------------------------------------
        # 2. Create a task
        # ------------------------------------------------------------------
        import os
        workspace = os.getcwd()

        task_payload = {
            "title": "Hello World Test",
            "description": (
                "List the files in the current workspace directory and say hello. "
                "Use list_files to see what's here, then respond with a brief summary."
            ),
            "workspace": workspace,
            "mode": "autonomous",
            "model": "ollama/qwen2.5-coder:latest",
        }

        resp = await client.post("/tasks", json=task_payload)
        if resp.status_code != 201:
            print(f"[ERROR] Failed to create task: {resp.status_code} {resp.text}")
            sys.exit(1)

        task = resp.json()
        task_id = task["id"]
        print(f"[OK] Created task: {task_id} — {task['title']}")

        # ------------------------------------------------------------------
        # 3. Start a run
        # ------------------------------------------------------------------
        resp = await client.post(f"/tasks/{task_id}/run")
        if resp.status_code != 201:
            print(f"[ERROR] Failed to start run: {resp.status_code} {resp.text}")
            sys.exit(1)

        run = resp.json()
        run_id = run["id"]
        print(f"[OK] Started run: {run_id}")

        # ------------------------------------------------------------------
        # 4. Stream WebSocket events
        # ------------------------------------------------------------------
        print("\n--- Agent Output Stream ---")
        ws_url = f"{BASE_WS}/runs/{run_id}/stream"

        try:
            async with websockets.connect(ws_url, ping_interval=None) as ws:
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=120.0)
                    except asyncio.TimeoutError:
                        print("[TIMEOUT] No event received in 120s")
                        break

                    event = json.loads(raw)
                    etype = event.get("type")

                    if etype == "ping":
                        continue
                    elif etype == "text":
                        print(f"[text] {event.get('content', '')[:200]}")
                    elif etype == "tool_call":
                        args_preview = json.dumps(event.get("input", {}))[:100]
                        print(f"[tool_call] {event.get('name')}({args_preview})")
                    elif etype == "tool_result":
                        result_preview = str(event.get("result", ""))[:150]
                        print(f"[tool_result] {event.get('name')} → {result_preview}")
                    elif etype == "error":
                        print(f"[ERROR] {event.get('content')}")
                    elif etype == "done":
                        status = event.get("status", "unknown")
                        print(f"\n[done] Run finished with status: {status}")
                        break
                    else:
                        print(f"[{etype}] {json.dumps(event)[:150]}")
        except Exception as e:
            print(f"[WebSocket error] {e}")

        # ------------------------------------------------------------------
        # 5. Fetch final run details
        # ------------------------------------------------------------------
        print("\n--- Final Run Details ---")
        resp = await client.get(f"/runs/{run_id}")
        if resp.status_code == 200:
            run_detail = resp.json()
            print(f"Status:   {run_detail['status']}")
            print(f"Events:   {len(run_detail.get('events', []))}")
            print(f"Summary:  {run_detail.get('summary') or '(none)'}")
            if run_detail.get("error"):
                print(f"Error:    {run_detail['error']}")
        else:
            print(f"[ERROR] Could not fetch run details: {resp.status_code}")

        # ------------------------------------------------------------------
        # 6. Check memory
        # ------------------------------------------------------------------
        resp = await client.get("/memory/list")
        memories = resp.json()
        print(f"\nMemory entries after run: {len(memories)}")

        print("\n[DONE] Test complete.")


if __name__ == "__main__":
    asyncio.run(main())
