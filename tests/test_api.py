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
    assert "已生效" in response.text


def test_support_workspace_loads_assigned_case_context() -> None:
    headers = {
        "X-Tenant-ID": "demo-company",
        "X-User-ID": "agent-chenyu",
        "X-Department-ID": "customer-service",
        "X-Roles": "support_agent,knowledge_admin",
    }
    client = TestClient(app)

    cases = client.get("/api/v1/support/cases", headers=headers)
    context = client.get("/api/v1/support/cases/CASE-1001", headers=headers)

    assert cases.status_code == 200
    assert cases.json()[0]["id"] == "CASE-1001"
    assert context.status_code == 200
    assert context.json()["customer"]["name"] == "王晨"
    assert context.json()["order"]["id"] == "ORD-1001"
    assert "memory" in context.json()
    assert "recent_messages" in context.json()["memory"]
    assert context.json()["ticket_history"][0]["id"] == "TKT-HIST-1001"
    assert context.json()["similar_tickets"][0]["id"] == "TKT-HIST-1001"
    assert "fts_rank" not in context.json()["similar_tickets"][0]


def test_employee_cannot_open_support_queue() -> None:
    response = TestClient(app).get("/api/v1/support/cases")
    assert response.status_code == 403
