"""Exercise the installed SDK wire contract with fake Auth HTTP responses only."""

import json
from uuid import uuid4

import httpx
import pytest

from tbd_server.user.auth import Auth
from tbd_server.user.store import Problem


def auth_user(identity=None, confirmed=True, email="example@example.test"):
    user = {
        "id": str(identity or uuid4()),
        "email": email,
        "aud": "authenticated",
        "app_metadata": {},
        "user_metadata": {},
        "created_at": "2026-01-01T00:00:00Z",
    }
    if confirmed:
        user["email_confirmed_at"] = "2026-01-01T00:00:00Z"
    return user


def adapter(handler):
    return Auth(
        "https://auth.example.test",
        "sb_secret_fake",
        "https://join.example.test",
        transport=httpx.MockTransport(handler),
    )


class EntryStore:
    def __init__(self, joined=False):
        self.entry = {
            "id": uuid4(),
            "email": "example@example.test",
            "auth_user_id": None,
            "joined_user_id": uuid4() if joined else None,
        }
        self.binds = []

    def invitation(self, invitation_id):
        assert invitation_id == self.entry["id"]
        return self.entry.copy()

    def bind_auth(self, invitation_id, auth_id):
        self.binds.append(auth_id)
        self.entry["auth_user_id"] = auth_id


def test_installed_sdk_get_user_uses_bearer_not_metadata():
    user = auth_user()
    user["user_metadata"] = {"type": "business", "phone": "+12125550199"}

    def handler(request):
        assert request.url.path == "/auth/v1/user"
        assert request.headers["authorization"] == "Bearer verified-session"
        return httpx.Response(200, json=user)

    assert adapter(handler).verified_identity("verified-session") == (uuid4_from(user), user["email"])


def uuid4_from(user):
    from uuid import UUID

    return UUID(user["id"])


@pytest.mark.parametrize("status, expected", [(401, 401), (403, 401), (500, 503), (429, 503)])
def test_auth_errors(status, expected):
    auth = adapter(
        lambda _: httpx.Response(status, json={"msg": "private upstream detail", "code": "unexpected_failure"})
    )
    with pytest.raises(Problem) as error:
        auth.verified_identity("fake")
    assert error.value.status == expected
    assert "private" not in error.value.detail


def test_auth_timeout_retryable():
    def handler(request):
        raise httpx.ConnectError("fake", request=request)

    with pytest.raises(Problem) as error:
        adapter(handler).verified_identity("fake")
    assert error.value.status == 503


def test_unconfirmed_rejected():
    with pytest.raises(Problem) as error:
        adapter(lambda _: httpx.Response(200, json=auth_user(confirmed=False))).verified_identity("fake")
    assert error.value.status == 403


@pytest.mark.parametrize("confirmed", [False, True])
def test_reconciled_resend_sdk_payload(confirmed):
    store, user, requests = EntryStore(), auth_user(confirmed=confirmed), []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            assert request.url.params["page"] == "1"
            return httpx.Response(200, json={"users": [user]})
        if confirmed:
            assert request.url.path == "/auth/v1/otp"
            assert json.loads(request.content)["create_user"] is False
            return httpx.Response(200, json={})
        assert request.url.path == "/auth/v1/invite"
        return httpx.Response(200, json=user)

    assert adapter(handler).send(store, store.entry["id"])["outcome"] == "sent"
    assert store.entry["auth_user_id"] == uuid4_from(user)
    assert requests[-1].url.params["redirect_to"] == "https://join.example.test/join"
    assert requests[-1].headers["authorization"] == "Bearer sb_secret_fake"


def test_lost_invite_response_reconciles_before_resend():
    store, user, state = EntryStore(), auth_user(confirmed=False), {"exists": False, "calls": 0}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"users": [user] if state["exists"] else []})
        assert request.url.path == "/auth/v1/invite"
        state["calls"] += 1
        if not state["exists"]:
            state["exists"] = True
            raise httpx.ReadTimeout("response lost", request=request)
        # Binding must have happened by email reconciliation before resending.
        assert store.entry["auth_user_id"] == uuid4_from(user)
        return httpx.Response(200, json=user)

    auth = adapter(handler)
    with pytest.raises(Problem) as error:
        auth.send(store, store.entry["id"])
    assert error.value.status == 503 and store.entry["auth_user_id"] is None
    assert auth.send(store, store.entry["id"])["outcome"] == "sent"
    assert state["calls"] == 2


def test_joined_sends_nothing():
    store = EntryStore(joined=True)

    def handler(request):
        pytest.fail("Already joined must not call Auth")

    assert adapter(handler).send(store, store.entry["id"])["outcome"] == "already joined"


def test_email_reconciliation_paginates():
    user = auth_user()

    def handler(request):
        page = int(request.url.params["page"])
        return httpx.Response(
            200, json={"users": [auth_user(email="other@example.test") for _ in range(100)] if page == 1 else [user]}
        )

    auth = adapter(handler)
    with auth.client() as client:
        assert auth.find_email(client.admin, user["email"]).id == user["id"]
