import requests
import json
import os

key = "AQ.Ab8RN6K-MeBbTTjmqZNBi42sl0kRZTGxiRIvrOllUZbxlXqSjA"
url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
headers = {'Content-Type': 'application/json'}
data = {
    "contents": [{"parts": [{"text": "Hello"}]}]
}
try:
    response = requests.post(url, headers=headers, json=data)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")
