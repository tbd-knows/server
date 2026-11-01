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


def test_openapi_documents_bearer_and_response_contract(client):
    schema = client.get("/openapi.json").json()
    join = schema["paths"]["/v1/join"]["post"]
    assert join["security"] == [{"HTTPBearer": []}]
    assert join["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/UserResponse")
    assert "auth_user_id" not in schema["components"]["schemas"]["UserResponse"]["properties"]
    assert schema["components"]["schemas"]["AddressWrite"]["properties"]["operation"]["default"] == "save"


def test_cors_exact_allowlist(client):
    allowed = client.options(
        "/v1/join",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert allowed.status_code == 200 and allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    refused = client.options(
        "/v1/join", headers={"Origin": "https://unknown-preview.example.test", "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in refused.headers


def test_invalid_fields_are_not_echoed(client):
    response = client.post(
        "/internal/users/seed-business",
        headers={"Authorization": "Bearer provisioning-test"},
        json={"seed_key": "fake", "name": "", "secret": "must-not-echo"},
    )
    assert response.status_code == 422 and "must-not-echo" not in response.text


@pytest.mark.parametrize("origins", [["*"], ["https://*.example.test"], ["https://example.test/join"]])
def test_wildcard_or_non_origin_configuration_refused(origins):
    with pytest.raises(ValueError):
        create_app(None, None, "p", "i", origins)
