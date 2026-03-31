"""Ad-hoc load test script for the one-shot chat task flow."""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BASE_URL = "http://localhost:5000"

# Load test settings
TOTAL_REQUESTS = 20
MAX_SUBMIT_WORKERS = 10
POLL_INTERVAL_SECONDS = 2
MAX_POLL_ATTEMPTS = 120
REQUEST_TIMEOUT = 30

user_ids =["a029009f-2f74-42f1-a477-95ef9dcc1eeb", "e6d88955-7324-4406-9387-92d93eb6114e","2c59d54e-983a-47aa-839e-a504459a47f6",
 "f66cec65-76d3-4ea3-baa3-4bd213e95525", "0d464513-8c45-442c-9e9e-ab1956ce357a", "91592ab9-5001-44a1-85c0-0ade3c26306e"]

def build_payload(index: int) -> dict:
    user_id = user_ids[index % len(user_ids)]
    return {
        "message": f"Request #{index}: Give me 5 practical tips to improve lamb growth.",
        "thread_id": f"load-thread-{index}-{uuid.uuid4()}",
        "user_id": user_id,
    }


def submit_chat_request(index: int) -> dict:
    payload = build_payload(index)
    started_at = time.time()

    try:
        response = requests.post(
            f"{BASE_URL}/api/v1/chat/one-shot",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        elapsed = time.time() - started_at

        result = {
            "index": index,
            "submit_time_seconds": elapsed,
            "status_code": response.status_code,
            "payload": payload,
        }

        try:
            result["response_json"] = response.json()
        except Exception:
            result["response_json"] = {"raw_text": response.text}

        if response.status_code == 202:
            result["accepted"] = True
            result["task_id"] = result["response_json"]["data"]["task_id"]
        else:
            result["accepted"] = False
            result["task_id"] = None

        return result

    except Exception as e:
        elapsed = time.time() - started_at
        return {
            "index": index,
            "submit_time_seconds": elapsed,
            "status_code": None,
            "payload": payload,
            "response_json": None,
            "accepted": False,
            "task_id": None,
            "error": str(e),
        }


def poll_task(task_id: str, index: int) -> dict:
    started_at = time.time()

    for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
        try:
            response = requests.get(
                f"{BASE_URL}/api/v1/chat/tasks/{task_id}",
                timeout=REQUEST_TIMEOUT,
            )
            data = response.json()
            state = data.get("data", {}).get("state")

            if state == "SUCCESS":
                return {
                    "index": index,
                    "task_id": task_id,
                    "final_state": "SUCCESS",
                    "attempts": attempt,
                    "elapsed_seconds": time.time() - started_at,
                    "result": data.get("data", {}).get("result"),
                }

            if state == "FAILURE":
                return {
                    "index": index,
                    "task_id": task_id,
                    "final_state": "FAILURE",
                    "attempts": attempt,
                    "elapsed_seconds": time.time() - started_at,
                    "error": data.get("data", {}).get("error"),
                    "raw": data,
                }

        except Exception as e:
            # keep trying unless we hit max attempts
            if attempt == MAX_POLL_ATTEMPTS:
                return {
                    "index": index,
                    "task_id": task_id,
                    "final_state": "POLL_ERROR",
                    "attempts": attempt,
                    "elapsed_seconds": time.time() - started_at,
                    "error": str(e),
                }

        time.sleep(POLL_INTERVAL_SECONDS)

    return {
        "index": index,
        "task_id": task_id,
        "final_state": "TIMEOUT",
        "attempts": MAX_POLL_ATTEMPTS,
        "elapsed_seconds": time.time() - started_at,
        "error": "Polling timed out.",
    }


def main():
    print("=" * 80)
    print("LOAD TEST STARTED")
    print(f"Target URL: {BASE_URL}")
    print(f"Total requests: {TOTAL_REQUESTS}")
    print(f"Parallel submit workers: {MAX_SUBMIT_WORKERS}")
    print("=" * 80)

    submit_results = []

    submit_started_at = time.time()

    with ThreadPoolExecutor(max_workers=MAX_SUBMIT_WORKERS) as executor:
        futures = {
            executor.submit(submit_chat_request, i): i
            for i in range(1, TOTAL_REQUESTS + 1)
        }

        for future in as_completed(futures):
            result = future.result()
            submit_results.append(result)

            idx = result["index"]
            status_code = result.get("status_code")
            accepted = result.get("accepted")
            task_id = result.get("task_id")

            if accepted:
                print(
                    f"[SUBMIT OK] request={idx} "
                    f"status={status_code} "
                    f"task_id={task_id} "
                    f"time={result['submit_time_seconds']:.2f}s"
                )
            else:
                print(
                    f"[SUBMIT FAIL] request={idx} "
                    f"status={status_code} "
                    f"time={result['submit_time_seconds']:.2f}s "
                    f"error={result.get('error') or result.get('response_json')}"
                )

    submit_total_time = time.time() - submit_started_at

    accepted_results = [r for r in submit_results if r["accepted"]]
    rejected_results = [r for r in submit_results if not r["accepted"]]

    print("\n" + "=" * 80)
    print("SUBMISSION SUMMARY")
    print("=" * 80)
    print(f"Submitted: {len(submit_results)}")
    print(f"Accepted : {len(accepted_results)}")
    print(f"Rejected : {len(rejected_results)}")
    print(f"Submit phase total time: {submit_total_time:.2f}s")

    if accepted_results:
        avg_submit = sum(r["submit_time_seconds"] for r in accepted_results) / len(accepted_results)
        print(f"Average accepted submit time: {avg_submit:.2f}s")

    print("\nPolling accepted tasks...\n")

    poll_results = []
    poll_started_at = time.time()

    with ThreadPoolExecutor(max_workers=min(MAX_SUBMIT_WORKERS, len(accepted_results) or 1)) as executor:
        futures = {
            executor.submit(poll_task, r["task_id"], r["index"]): r
            for r in accepted_results
        }

        for future in as_completed(futures):
            result = future.result()
            poll_results.append(result)

            state = result["final_state"]
            idx = result["index"]
            task_id = result["task_id"]

            if state == "SUCCESS":
                assistant_message = result.get("result", {}).get("assistant_message", "")
                preview = assistant_message[:80].replace("\n", " ")
                print(
                    f"[DONE] request={idx} task_id={task_id} "
                    f"time={result['elapsed_seconds']:.2f}s "
                    f"preview={preview!r}"
                )
            else:
                print(
                    f"[{state}] request={idx} task_id={task_id} "
                    f"time={result['elapsed_seconds']:.2f}s "
                    f"error={result.get('error')}"
                )

    poll_total_time = time.time() - poll_started_at

    success_count = sum(1 for r in poll_results if r["final_state"] == "SUCCESS")
    failure_count = sum(1 for r in poll_results if r["final_state"] == "FAILURE")
    timeout_count = sum(1 for r in poll_results if r["final_state"] == "TIMEOUT")
    poll_error_count = sum(1 for r in poll_results if r["final_state"] == "POLL_ERROR")

    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    print(f"Total requests           : {TOTAL_REQUESTS}")
    print(f"Accepted submissions     : {len(accepted_results)}")
    print(f"Rejected submissions     : {len(rejected_results)}")
    print(f"Successful tasks         : {success_count}")
    print(f"Failed tasks             : {failure_count}")
    print(f"Timed out tasks          : {timeout_count}")
    print(f"Polling errors           : {poll_error_count}")
    print(f"Polling phase total time : {poll_total_time:.2f}s")

    completed = [r for r in poll_results if r["final_state"] == "SUCCESS"]
    if completed:
        avg_completion = sum(r["elapsed_seconds"] for r in completed) / len(completed)
        max_completion = max(r["elapsed_seconds"] for r in completed)
        min_completion = min(r["elapsed_seconds"] for r in completed)
        print(f"Average completion time  : {avg_completion:.2f}s")
        print(f"Fastest completion       : {min_completion:.2f}s")
        print(f"Slowest completion       : {max_completion:.2f}s")

    if rejected_results:
        print("\nRejected requests details:")
        for r in rejected_results:
            print(
                f"  - request={r['index']} status={r.get('status_code')} "
                f"error={r.get('error') or r.get('response_json')}"
            )

    failed_or_timed_out = [
        r for r in poll_results
        if r["final_state"] in {"FAILURE", "TIMEOUT", "POLL_ERROR"}
    ]
    if failed_or_timed_out:
        print("\nFailed/timed out task details:")
        for r in failed_or_timed_out:
            print(
                f"  - request={r['index']} task_id={r['task_id']} "
                f"state={r['final_state']} error={r.get('error')}"
            )


if __name__ == "__main__":
    main()
