import pytest
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../bot')))

from main import app
from database import Base, engine, SessionLocal, User

# Configure test database
TestingSessionLocal = SessionLocal

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_webhook_activation():
    # Simplistic test for now without mocking database
    payload = {
        "action": "membership.going_active",
        "data": {
            "user": {
                "id": "whop_test_1"
            }
        }
    }
    response = client.post("/webhook/whop", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["message"] == "User activated"
