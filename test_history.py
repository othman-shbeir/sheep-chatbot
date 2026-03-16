import requests
import json
import time

def main():
    try:
        print("Creating a new thread interaction...")
        payload = {
            "message": "When should I shear my sheep?", 
            "thread_id": "test_history_sidebar"
        }
        res = requests.post("http://127.0.0.1:5000/chat", json=payload)
        res.raise_for_status()
        print("Agent responded.")

        print("\nFetching /api/history...")
        history_res = requests.get("http://127.0.0.1:5000/api/history")
        history_res.raise_for_status()
        print(json.dumps(history_res.json(), indent=2))

        print("\nFetching /api/history/test_history_sidebar...")
        detail_res = requests.get("http://127.0.0.1:5000/api/history/test_history_sidebar")
        detail_res.raise_for_status()
        print(json.dumps(detail_res.json(), indent=2))
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    time.sleep(2) # Give Flask time to start
    main()
