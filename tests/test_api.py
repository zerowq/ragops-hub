import os
import tempfile

from fastapi.testclient import TestClient

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="ragops-api-tests-")

from app.main import app


def test_health_endpoint() -> None:
    response = TestClient(app).get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_order_sse_endpoint() -> None:
    response = TestClient(app).post(
        "/api/v1/chat/stream",
        json={"message": "查询订单 ORD-1001", "conversation_id": "api-test"},
    )
    assert response.status_code == 200
    assert "event: tool_finished" in response.text
    assert "已发货" in response.text
