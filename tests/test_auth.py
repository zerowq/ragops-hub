from datetime import datetime, timedelta, timezone

import jwt

from app.api.dependencies import get_principal
from app.core.config import get_settings


def test_jwt_mode_builds_principal_from_signed_claims(monkeypatch) -> None:
    secret = "test-secret-with-at-least-32-bytes"
    monkeypatch.setenv("AUTH_MODE", "jwt")
    monkeypatch.setenv("JWT_SECRET", secret)
    get_settings.cache_clear()
    token = jwt.encode(
        {
            "sub": "user-1",
            "tenant_id": "tenant-a",
            "department_id": "support",
            "roles": ["knowledge_admin"],
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        secret,
        algorithm="HS256",
    )

    principal = get_principal(
        authorization=f"Bearer {token}",
        x_user_id="ignored-user",
        x_tenant_id="ignored-tenant",
        x_department_id="ignored-department",
        x_roles="employee",
    )

    assert principal.user_id == "user-1"
    assert principal.tenant_id == "tenant-a"
    assert principal.roles == ["knowledge_admin"]
    get_settings.cache_clear()
