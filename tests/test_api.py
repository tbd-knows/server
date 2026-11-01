from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from tbd_server.user.api import create_app


class NoCalls:
    def __getattr__(self, name):
        raise AssertionError(f"Unauthorized request reached {name}")


@pytest.fixture
def client():
    return TestClient(create_app(NoCalls(), NoCalls(), "provisioning-test", "internal-test", ["http://localhost:5173"]))


@pytest.mark.parametrize("token", [None, "invalid", "provisioning-test", "browser-session"])
def test_internal_access_denied(client, token):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.get(f"/internal/users/{uuid4()}/profile", headers=headers)
    assert response.status_code == 401


def test_context_mismatch_denied(client):
    response = client.get(
        f"/internal/users/{uuid4()}/profile",
        headers={"Authorization": "Bearer internal-test", "X-TBD-User-ID": str(uuid4())},
    )
    assert response.status_code == 403


def test_internal_cannot_provision(client):
    response = client.post(
        "/internal/users/seed-business",
        json={"seed_key": "fake", "name": "Example"},
        headers={"Authorization": "Bearer internal-test"},
    )
    assert response.status_code == 401


def test_get_cannot_join(client):
    assert client.get("/v1/join").status_code == 405
    assert client.get("/healthz").headers["cache-control"] == "no-store"


def test_protected_fields_rejected(client):
    user_id = str(uuid4())
    response = client.patch(
        f"/internal/users/{user_id}/profile",
        json={"name": "Example", "revision": 0, "email": "other@example.test"},
        headers={"Authorization": "Bearer internal-test", "X-TBD-User-ID": user_id},
    )
    assert response.status_code == 422
