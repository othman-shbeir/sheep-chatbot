import asyncio
import aiohttp
import json
import time
import uuid
import csv
from datetime import datetime
from typing import List, Dict

# --- Configuration ---
# Update BASE_URL if your server is running on a different port or host.
BASE_URL = "http://localhost:5000"
START_ENDPOINT = "/api/v1/chat/stream/start"
EVENTS_ENDPOINT_TEMPLATE = "/api/v1/chat/stream/events/{run_id}"
CSV_PATH = "user_is_for_test.csv"
CONCURRENT_USERS = 30  # Number of users to simulate simultaneously
TIMEOUT = 120  # Total timeout for a single stream across all events

# --- Test Data ---
def load_user_ids(path: str) -> List[str]:
    user_ids = []
    try:
        with open(path, mode='r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader)  # Skip header ("id")
            for row in reader:
                if row:
                    # Remove surrounding quotes if they exist
                    uid = row[0].strip().strip('"')
                    if uid:
                        user_ids.append(uid)
    except FileNotFoundError:
        print(f"Error: {path} not found.")
    return user_ids

USER_IDS = load_user_ids(CSV_PATH)

async def test_user_stream(session: aiohttp.ClientSession, index: int, user_id: str) -> Dict:
    thread_id = f"stress-test-{index}-{uuid.uuid4()}"
    payload = {
        "message": "Give me detailed practical tips to improve lamb growth and flock productivity.",
        "thread_id": thread_id,
        "user_id": user_id
    }
    
    result = {
        "index": index,
        "user_id": user_id,
        "start_status": None,
        "run_id": None,
        "ttfb": None,
        "total_time": None,
        "delta_count": 0,
        "final_event": None,
        "error": None
    }
    
    start_time = time.time()
    
    # 1. Start the stream
    try:
        async with session.post(f"{BASE_URL}{START_ENDPOINT}", json=payload) as resp:
            result["start_status"] = resp.status
            if resp.status != 202:
                result["error"] = f"Failed to start (Status {resp.status}): {await resp.text()}"
                return result
            
            data = await resp.json()
            run_id = data.get("data", {}).get("run_id")
            result["run_id"] = run_id
            if not run_id:
                result["error"] = "No run_id returned in start response"
                return result
    except Exception as e:
        result["error"] = f"Start request error: {str(e)}"
        return result

    # 2. Listen to events via SSE
    events_url = f"{BASE_URL}{EVENTS_ENDPOINT_TEMPLATE.format(run_id=run_id)}"
    try:
        async with session.get(events_url, timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as resp:
            if resp.status != 200:
                result["error"] = f"Failed to connect to events (Status {resp.status})"
                return result
            
            event_type = "message"
            async for line in resp.content:
                line_str = line.decode('utf-8').strip()
                if not line_str:
                    continue
                
                if line_str.startswith("event:"):
                    event_type = line_str[6:].strip()
                elif line_str.startswith("data:"):
                    data_str = line_str[5:].strip()
                    
                    if result["ttfb"] is None and event_type == "delta":
                        result["ttfb"] = time.time() - start_time
                    
                    if event_type == "delta":
                        result["delta_count"] += 1
                    
                    if event_type in ["done", "error", "cancelled"]:
                        result["final_event"] = event_type
                        result["total_time"] = time.time() - start_time
                        return result
                        
    except asyncio.TimeoutError:
        result["error"] = "Stream connection/read timed out"
    except Exception as e:
        result["error"] = f"Streaming error: {str(e)}"
    
    result["total_time"] = time.time() - start_time
    return result

async def main():
    if not USER_IDS:
        print("No user IDs found. Aborting.")
        return

    print("=" * 80)
    print(f"STRESS TEST STARTED AT {datetime.now()}")
    print(f"Target: {BASE_URL}")
    print(f"Users found in CSV: {len(USER_IDS)}")
    print(f"Concurrency: {CONCURRENT_USERS} per batch")
    print("=" * 80)

    results = []
    async with aiohttp.ClientSession() as session:
        # Run in batches
        for i in range(0, len(USER_IDS), CONCURRENT_USERS):
            batch = USER_IDS[i:i+CONCURRENT_USERS]
            tasks = [test_user_stream(session, i + j, user_id) for j, user_id in enumerate(batch)]
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Running batch {i//CONCURRENT_USERS + 1} ({len(tasks)} users)...")
            batch_results = await asyncio.gather(*tasks)
            results.extend(batch_results)

    # --- Summary ---
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    success = [r for r in results if r["final_event"] == "done"]
    errors = [r for r in results if r["error"] or r["final_event"] == "error"]
    timeouts = [r for r in results if r["error"] and "timeout" in r["error"].lower()]
    
    print(f"Total Requests: {len(results)}")
    print(f"Successful (done): {len(success)}")
    print(f"Failed (error event): {sum(1 for r in results if r['final_event'] == 'error')}")
    print(f"Timeouts/Connection Errors: {len(results) - len(success) - sum(1 for r in results if r['final_event'] == 'error')}")
    
    if success:
        valid_ttfbs = [r["ttfb"] for r in success if r["ttfb"] is not None]
        avg_ttfb = sum(valid_ttfbs) / len(valid_ttfbs) if valid_ttfbs else 0
        avg_total = sum(r["total_time"] for r in success if r["total_time"]) / len(success)
        avg_deltas = sum(r["delta_count"] for r in success) / len(success)
        
        print(f"Average TTFB: {avg_ttfb:.2f}s")
        print(f"Average Total Duration: {avg_total:.2f}s")
        print(f"Average Delta Count: {avg_deltas:.1f}")
    
    if errors:
        print("\nFirst 10 Errors/Failures:")
        for r in errors[:10]:
            print(f"  User {r['user_id']}: {r['error'] or r['final_event']}")

    # Save to JSON
    with open("stress_test_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nDetailed results saved to stress_test_results.json")

if __name__ == "__main__":
    asyncio.run(main())
