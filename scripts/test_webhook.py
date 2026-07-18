import requests

url = "http://127.0.0.1:8000/webhook/whop"
payload = {
    "action": "membership.going_active",
    "data": {
        "user": {
            "id": "whop_test_user_123"
        }
    }
}

try:
    response = requests.post(url, json=payload)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.json()}")
except Exception as e:
    print(f"Connection failed: {e}\nEnsure the FastAPI server is running.")
