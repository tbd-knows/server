"""Run only against a newly created, disposable local database, never Supabase."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4
from itertools import count

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from tbd_server.user.models import AddressDelete, AddressWrite, DefaultWrite, Seed
from tbd_server.user.store import Problem, Store


@pytest.fixture(scope="module")
def store():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required for disposable real-Postgres proof")
    info = conninfo_to_dict(url)
    assert info.get("host") in ("localhost", "127.0.0.1", "/private/tmp"), "Local disposable database only"
    assert info.get("dbname", "").startswith("tbd_user_test_"), "Dedicated disposable database required"
    with psycopg.connect(url) as db:
        # Intentionally fail if a previous schema exists. Never reset an existing database.
        db.execute("CREATE SCHEMA auth")
        db.execute("CREATE TABLE auth.users (id uuid PRIMARY KEY)")
        for role in ("anon", "authenticated"):
            if not db.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                db.execute(psycopg.sql.SQL("CREATE ROLE {}").format(psycopg.sql.Identifier(role)))
        for migration in sorted(Path("supabase/migrations").glob("*.sql")):
            db.execute(migration.read_text())
    return Store(make_conninfo(url, user="tbd_user_backend"))


def seeded(store):
    return store.seed(Seed(seed_key=str(uuid4()), name="Example Shop"))["id"]


def address(revision=0, **kwargs):
    return AddressWrite(
        revision=revision,
        recipient="Example",
        line1="1 Example St",
        city="Example",
        region="NY",
        postal_code="10001",
        **kwargs,
    )


def test_seed_concurrency(store):
    data = Seed(seed_key=str(uuid4()), name="Example Shop")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: store.seed(data), range(2)))
    assert results[0]["id"] == results[1]["id"]


def test_join_concurrency(store):
    identity, entry = uuid4(), uuid4()
    email = f"{entry}@example.test"
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("INSERT INTO auth.users VALUES (%s)", (identity,))
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO invitations (id,name,phone,email,type,auth_user_id) VALUES (%s,'Example','+12125550101',%s,'individual',%s)",
            (entry, email, identity),
        )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: store.join(identity, email), range(2)))
    assert results[0]["id"] == results[1]["id"]


def test_atomic_defaults_and_delete(store):
    user, first, second = seeded(store), uuid4(), uuid4()
    assert store.save_address(user, first, address())["revision"] == 1
    assert store.save_address(user, first, address())["revision"] == 1
    store.save_address(user, second, address(1))
    with pytest.raises(Problem) as error:
        store.delete_address(user, first, AddressDelete(revision=2))
    assert error.value.status == 422
    assert store.read(user)["revision"] == 2
    result = store.delete_address(user, first, AddressDelete(revision=2, replacement_address_id=second))
    assert len(result["addresses"]) == 1 and result["addresses"][0]["is_default"]


def test_cross_user_and_stale_revision(store):
    user, other, first = seeded(store), seeded(store), uuid4()
    store.save_address(user, first, address())
    with pytest.raises(Problem) as error:
        store.set_default(other, DefaultWrite(revision=0, address_id=first))
    assert error.value.status == 404
    with pytest.raises(Problem) as error:
        store.save_address(user, uuid4(), address())
    assert error.value.status == 409


def test_browser_privileges_denied(store):
    with store.transaction() as cur:
        for role in ("anon", "authenticated"):
            assert not cur.execute("SELECT has_schema_privilege(%s,'user_service','USAGE')", (role,)).fetchone()[
                "has_schema_privilege"
            ]
            assert not cur.execute("SELECT has_table_privilege(%s,'user_service.users','SELECT')", (role,)).fetchone()[
                "has_table_privilege"
            ]


_fixture_phones = count(110)


def approved(store, *, type="individual", phone=None, bind=True):
    from tbd_server.user.models import Invitation

    identity = uuid4()
    email = f"{identity}@example.test"
    # Fixed 555-01xx fixture range; no live contact records.
    phone = phone or f"+1212555{next(_fixture_phones):04d}"
    entry = store.approve(Invitation(name="Approved Example", phone=phone, email=email, type=type))
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("INSERT INTO auth.users VALUES (%s)", (identity,))
    if bind:
        store.bind_auth(entry["id"], identity)
    return entry, identity


@pytest.mark.parametrize("kind", ["individual", "business"])
def test_join_uses_approved_details_and_no_user_before_join(store, kind):
    entry, identity = approved(store, type=kind)
    with store.transaction() as cur:
        assert cur.execute("SELECT 1 FROM users WHERE email=%s", (entry["email"],)).fetchone() is None
    result = store.join(identity, entry["email"])
    assert result["name"] == entry["name"] and result["type"] == kind and result["phone"] == entry["phone"]
    assert store.join(identity, entry["email"])["id"] == result["id"]
    assert store.invitation(entry["id"])["joined_user_id"] == result["id"]
    assert store.resolve(entry["phone"])["id"] == result["id"]


def test_unapproved_and_mismatched_identity(store):
    with pytest.raises(Problem) as error:
        store.join(uuid4(), "unapproved@example.test")
    assert error.value.status == 403
    entry, identity = approved(store)
    with pytest.raises(Problem) as error:
        store.join(uuid4(), entry["email"])
    assert error.value.status == 403
    assert store.invitation(entry["id"])["joined_user_id"] is None


def test_profile_concurrent_revision_one_wins(store):
    from tbd_server.user.models import ProfilePatch

    user = seeded(store)

    def update(name):
        try:
            return store.patch(user, ProfilePatch(revision=0, name=name))["revision"]
        except Problem as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update, ["First", "Second"]))
    assert sorted(results) == [1, 409]
    assert store.read(user)["revision"] == 1


def test_address_edit_requires_current_revision_and_save_id_cannot_overwrite(store):
    user, address_id = seeded(store), uuid4()
    original = address()
    store.save_address(user, address_id, original)
    changed = original.model_copy(update={"line1": "2 Example St", "revision": 1})
    with pytest.raises(Problem) as error:
        store.save_address(user, address_id, changed)
    assert error.value.status == 409
    before_missing_edit = store.read(user)
    with pytest.raises(Problem) as error:
        store.save_address(user, uuid4(), address(1, operation="edit"))
    assert error.value.status == 404
    assert store.read(user) == before_missing_edit
    changed.operation = "edit"
    result = store.save_address(user, address_id, changed)
    assert result["revision"] == 2 and result["addresses"][0]["line1"] == "2 Example St"
    with pytest.raises(Problem) as error:
        store.save_address(user, address_id, changed)
    assert error.value.status == 409
    assert store.read(user)["revision"] == 2


def test_edit_clear_delete_and_last_default(store):
    user, address_id = seeded(store), uuid4()
    store.save_address(user, address_id, address(label="Home", line2="Unit 2"))
    result = store.save_address(user, address_id, address(1, operation="edit"))
    assert result["addresses"][0]["label"] is None and result["addresses"][0]["line2"] is None
    result = store.delete_address(user, address_id, AddressDelete(revision=2))
    assert result["revision"] == 3 and result["addresses"] == []
    assert store.read(user)["addresses"] == []


def test_default_concurrency_and_rollback(store, monkeypatch):
    user, first, second = seeded(store), uuid4(), uuid4()
    store.save_address(user, first, address())
    store.save_address(user, second, address(1))

    def switch(target):
        try:
            return store.set_default(user, DefaultWrite(revision=2, address_id=target))["revision"]
        except Problem as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(switch, [first, second])) == [3, 409]
    before = store.read(user)
    assert sum(a["is_default"] for a in before["addresses"]) == 1

    def fail(*args):
        raise psycopg.OperationalError("simulated connection failure after UPDATE")

    with monkeypatch.context() as context:
        context.setattr(store, "bump", fail)
        with pytest.raises(Problem) as error:
            store.set_default(user, DefaultWrite(revision=3, address_id=second))
        assert error.value.status == 503
    assert store.read(user) == before


def test_invite_seed_contact_conflicts_in_both_directions(store):
    from tbd_server.user.models import Invitation

    entry, _ = approved(store)
    for contact in ({"email": entry["email"]}, {"phone": entry["phone"]}):
        with pytest.raises(Problem) as error:
            store.seed(Seed(seed_key=str(uuid4()), name="Conflict", **contact))
        assert error.value.status == 409
    unique = str(uuid4())
    seed = Seed(seed_key=unique, name="Shop", email=f"{unique}@example.test", phone="+12125550198")
    store.seed(seed)
    with pytest.raises(Problem) as error:
        store.approve(Invitation(name="Conflict", email=seed.email, phone=seed.phone, type="business"))
    assert error.value.status == 409
    assert store.seed(seed)["auth_user_id"] is None
    with pytest.raises(Problem) as error:
        store.seed(seed.model_copy(update={"name": "Changed"}))
    assert error.value.status == 409


def test_invitation_retries_and_conflicts(store):
    from tbd_server.user.models import Invitation

    entry, _ = approved(store)
    data = Invitation(**{key: entry[key] for key in ("name", "email", "phone", "type")})
    assert store.approve(data)["id"] == entry["id"]
    with pytest.raises(Problem) as error:
        store.approve(data.model_copy(update={"name": "Changed"}))
    assert error.value.status == 409


def test_seed_invite_race_reserves_contact(store):
    from tbd_server.user.models import Invitation

    email = f"{uuid4()}@example.test"

    def create(kind):
        try:
            if kind == "seed":
                store.seed(Seed(seed_key=str(uuid4()), name="Seed", email=email))
            else:
                store.approve(Invitation(name="Invite", email=email, phone="+12125550197", type="business"))
            return 200
        except Problem as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(create, ["seed", "invite"])) == [200, 409]


def test_database_constraints_and_actual_browser_denial(store):
    url = os.environ["TEST_DATABASE_URL"]
    for role in ("anon", "authenticated"):
        with psycopg.connect(url) as db:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with db.transaction():
                    db.execute(psycopg.sql.SQL("SET LOCAL ROLE {}").format(psycopg.sql.Identifier(role)))
                    db.execute("SELECT * FROM user_service.users")
    user, first = seeded(store), uuid4()
    store.save_address(user, first, address())
    with pytest.raises(psycopg.errors.UniqueViolation):
        with psycopg.connect(url) as db:
            db.execute(
                "INSERT INTO user_service.addresses SELECT %s,user_id,label,recipient,line1,line2,city,region,postal_code,country_code,true FROM user_service.addresses WHERE id=%s",
                (uuid4(), first),
            )
    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(url) as db:
            db.execute("UPDATE user_service.users SET type='individual' WHERE id=%s", (user,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with store.transaction() as cur:
            cur.execute("SELECT * FROM auth.users")


def test_http_invite_join_retry_failure_recovery_and_profile_contract(store, monkeypatch):
    import httpx
    from fastapi.testclient import TestClient
    from tbd_server.user.api import create_app
    from tbd_server.user.auth import Auth
    from test_auth import auth_user

    user = auth_user(confirmed=False, email=f"{uuid4()}@example.test")
    identity = UUID(user["id"])
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("INSERT INTO auth.users VALUES (%s)", (identity,))
    state = {"exists": False, "lost": False, "sends": 0}

    def handler(request):
        if request.url.path == "/auth/v1/admin/users":
            return httpx.Response(200, json={"users": [user] if state["exists"] else []})
        if request.url.path == "/auth/v1/invite":
            state["exists"] = True
            state["sends"] += 1
            if not state["lost"]:
                state["lost"] = True
                raise httpx.ReadTimeout("lost response", request=request)
            return httpx.Response(200, json=user)
        if request.url.path == "/auth/v1/user":
            assert request.headers["authorization"] == "Bearer fake-verified-session"
            return httpx.Response(200, json=user)
        pytest.fail(f"Unexpected Auth operation: {request.url.path}")

    auth = Auth(
        "https://auth.example.test",
        "sb_secret_fake",
        "https://join.example.test",
        transport=httpx.MockTransport(handler),
    )
    client = TestClient(create_app(store, auth, "provisioning-test", "internal-test", ["http://localhost:5173"]))
    # A valid, confirmed Auth response does not establish application admission.
    user["email_confirmed_at"] = "2026-01-01T00:00:00Z"
    assert client.post("/v1/join", headers={"Authorization": "Bearer fake-verified-session"}).status_code == 403
    with store.transaction() as cur:
        assert cur.execute("SELECT count(*) AS n FROM users WHERE auth_user_id=%s", (identity,)).fetchone()["n"] == 0
    user["email_confirmed_at"] = None
    provisioning = {"Authorization": "Bearer provisioning-test"}
    approved_data = {"name": "Stored Example", "email": user["email"], "phone": "+12125550196", "type": "business"}
    response = client.post("/internal/invitations", json=approved_data, headers=provisioning)
    assert response.status_code == 503
    entry_id = UUID(response.json()["id"])
    assert store.invitation(entry_id)["joined_user_id"] is None
    response = client.post(f"/internal/invitations/{entry_id}/send", headers=provisioning)
    assert response.status_code == 200 and state["sends"] == 2
    session = {"Authorization": "Bearer fake-verified-session"}
    # GET cannot consume or create; an unconfirmed email cannot join.
    assert client.get("/v1/join", headers=session).status_code == 405
    assert client.post("/v1/join", headers=session).status_code == 403
    user["email_confirmed_at"] = "2026-01-01T00:00:00Z"
    original = store.join
    committed = []

    def uncertain(*args):
        result = original(*args)
        committed.append(result["id"])
        raise Problem(503, "Simulated response lost after commit")

    with monkeypatch.context() as context:
        context.setattr(store, "join", uncertain)
        assert client.post("/v1/join", headers=session).status_code == 503
    response = client.post(
        "/v1/join",
        headers=session,
        json={"name": "Tampered", "type": "individual", "phone": "+12125550195", "id": str(uuid4())},
    )
    assert response.status_code == 200
    joined = response.json()
    assert joined["id"] == str(committed[0]) and joined["name"] == "Stored Example" and joined["type"] == "business"
    assert "auth_user_id" not in joined and "seed_key" not in joined
    assert (
        client.post(f"/internal/invitations/{entry_id}/send", headers=provisioning).json()["outcome"]
        == "already joined"
    )
    internal = {"Authorization": "Bearer internal-test"}
    assert (
        client.post("/internal/users/resolve", json={"phone": approved_data["phone"]}, headers=internal).json()["id"]
        == joined["id"]
    )
    assert client.post("/internal/users/resolve", json={"phone": "+12125550194"}, headers=internal).status_code == 404
    own = {**internal, "X-TBD-User-ID": joined["id"]}
    path = f"/internal/users/{joined['id']}"
    assert client.get(path + "/profile", headers=own).json()["addresses"] == []
    address_id = uuid4()
    response = client.put(path + f"/addresses/{address_id}", headers=own, json=address().model_dump())
    assert response.status_code == 200 and response.json()["revision"] == 1
    assert client.patch(path + "/profile", headers=own, json={"revision": 0, "name": "Stale"}).status_code == 409
    response = client.patch(path + "/profile", headers=own, json={"revision": 1, "name": "Corrected"})
    assert response.status_code == 200 and response.json()["name"] == "Corrected"
    assert client.get(path + "/profile", headers=session).status_code == 401
    with store.transaction() as cur:
        assert cur.execute("SELECT count(*) AS n FROM users WHERE auth_user_id=%s", (identity,)).fetchone()["n"] == 1


@pytest.mark.parametrize("bind", [True, False])
def test_failed_join_rolls_back_user_insert(store, monkeypatch, bind):
    entry, identity = approved(store, bind=bind)
    from contextlib import contextmanager

    original = store.transaction

    @contextmanager
    def fail_before_commit():
        with original() as cur:
            yield cur
            raise psycopg.OperationalError("simulated commit failure")

    with monkeypatch.context() as context:
        context.setattr(store, "transaction", fail_before_commit)
        with pytest.raises(Problem) as error:
            store.join(identity, entry["email"])
        assert error.value.status == 503
    assert store.invitation(entry["id"])["joined_user_id"] is None
    assert store.invitation(entry["id"])["auth_user_id"] == (identity if bind else None)
    with store.transaction() as cur:
        assert cur.execute("SELECT 1 FROM users WHERE auth_user_id=%s", (identity,)).fetchone() is None
    assert store.join(identity, entry["email"])["email"] == entry["email"]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE user_service.users SET phone='2125550100' WHERE id=%s",
        "UPDATE user_service.users SET email=' BAD@example.test ' WHERE id=%s",
        "UPDATE user_service.users SET name='' WHERE id=%s",
        "UPDATE user_service.users SET revision=-1 WHERE id=%s",
    ],
)
def test_database_rejects_invalid_user_fields(store, statement):
    user = seeded(store)
    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
            db.execute(statement, (user,))


def test_cross_user_cannot_edit_delete_or_replace_default(store):
    user, other, first, foreign = seeded(store), seeded(store), uuid4(), uuid4()
    store.save_address(user, first, address())
    store.save_address(other, foreign, address())
    operations = [
        lambda: store.save_address(user, foreign, address(1, operation="edit")),
        lambda: store.delete_address(user, foreign, AddressDelete(revision=1)),
        lambda: store.delete_address(user, first, AddressDelete(revision=1, replacement_address_id=foreign)),
    ]
    before = store.read(user)
    for operation in operations:
        with pytest.raises(Problem) as error:
            operation()
        assert error.value.status == 404
        assert store.read(user) == before


def test_duplicate_user_contacts_and_auth_foreign_key(store):
    data = Seed(seed_key=str(uuid4()), name="Example", phone="+12125550193", email="constraint@example.test")
    store.seed(data)
    for contact in ({"phone": data.phone}, {"email": data.email}):
        with pytest.raises(Problem) as error:
            store.seed(Seed(seed_key=str(uuid4()), name="Other", **contact))
        assert error.value.status == 409
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
            db.execute(
                "INSERT INTO user_service.invitations (name,phone,email,type,auth_user_id) VALUES ('Example','+12125550192','missing-auth@example.test','individual',%s)",
                (uuid4(),),
            )


def test_connection_check_queries_actual_backend_role(store):
    assert store.verify_connection() == {"role": "tbd_user_backend", "tls": False, "session_pooler": False}


def test_connection_check_rejects_owner_credential(store):
    with pytest.raises(Problem) as error:
        Store(os.environ["TEST_DATABASE_URL"]).verify_connection()
    assert error.value.status == 503


@pytest.mark.parametrize("kind", ["individual", "business"])
def test_join_binds_delivered_unbound_invitation(store, kind):
    entry, identity = approved(store, type=kind, bind=False)
    joined = store.join(identity, entry["email"])
    assert joined["auth_user_id"] == identity
    assert all(joined[key] == entry[key] for key in ("name", "email", "phone", "type"))
    assert store.invitation(entry["id"])["auth_user_id"] == identity
    # A delayed delivery response and another Join cannot replace the identity/User.
    store.bind_auth(entry["id"], identity)
    assert store.join(identity, entry["email"])["id"] == joined["id"]
    with pytest.raises(Problem) as error:
        store.join(uuid4(), entry["email"])
    assert error.value.status == 403
    with store.transaction() as cur:
        assert cur.execute("SELECT count(*) AS n FROM users WHERE auth_user_id=%s", (identity,)).fetchone()["n"] == 1


def test_concurrent_join_and_delivery_binding(store):
    from threading import Barrier

    entry, identity = approved(store, bind=False)
    start = Barrier(3)

    def join():
        start.wait(timeout=5)
        return store.join(identity, entry["email"])

    def bind():
        start.wait(timeout=5)
        store.bind_auth(entry["id"], identity)

    with ThreadPoolExecutor(max_workers=3) as executor:
        first, second, binding = executor.submit(join), executor.submit(join), executor.submit(bind)
        first, second = first.result(timeout=15), second.result(timeout=15)
        binding.result(timeout=15)
    assert first["id"] == second["id"]
    saved = store.invitation(entry["id"])
    assert saved["auth_user_id"] == identity and saved["joined_user_id"] == first["id"]
    with store.transaction() as cur:
        assert cur.execute("SELECT count(*) AS n FROM users WHERE auth_user_id=%s", (identity,)).fetchone()["n"] == 1


def test_unbound_join_identity_uniqueness_rolls_back(store):
    original, identity = approved(store)
    entry, _ = approved(store, bind=False)
    with pytest.raises(Problem) as error:
        store.join(identity, entry["email"])
    assert error.value.status == 409
    assert store.invitation(entry["id"])["auth_user_id"] is None
    assert store.invitation(entry["id"])["joined_user_id"] is None
    assert store.invitation(original["id"])["auth_user_id"] == identity
    with store.transaction() as cur:
        assert cur.execute("SELECT 1 FROM users WHERE auth_user_id=%s", (identity,)).fetchone() is None


@pytest.mark.parametrize("lost_response", [False, True])
def test_http_join_during_delivery_without_founder_resend(store, lost_response):
    from threading import Event

    import httpx
    from fastapi.testclient import TestClient
    from test_auth import auth_user
    from tbd_server.user.api import create_app
    from tbd_server.user.auth import Auth

    user = auth_user(confirmed=False, email=f"{uuid4()}@example.test")
    identity = UUID(user["id"])
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("INSERT INTO auth.users VALUES (%s)", (identity,))
    delivered, finish_delivery = Event(), Event()
    sends = []

    def handler(request):
        if request.url.path == "/auth/v1/admin/users":
            return httpx.Response(200, json={"users": []})
        if request.url.path == "/auth/v1/invite":
            sends.append(request.url.path)
            delivered.set()
            assert finish_delivery.wait(timeout=10)
            if lost_response:
                raise httpx.ReadTimeout("response lost after delivery", request=request)
            return httpx.Response(200, json=user)
        if request.url.path == "/auth/v1/user":
            assert request.headers["authorization"] == "Bearer fake-verified-session"
            return httpx.Response(200, json=user)
        pytest.fail(f"Unexpected Auth operation: {request.url.path}")

    auth = Auth(
        "https://auth.example.test",
        "sb_secret_fake",
        "https://join.example.test",
        transport=httpx.MockTransport(handler),
    )
    data = {
        "name": "Approved Delivered Example",
        "email": user["email"],
        "phone": f"+1212555{next(_fixture_phones):04d}",
        "type": "individual",
    }
    with TestClient(create_app(store, auth, "provisioning-test", "internal-test", [])) as client:
        with ThreadPoolExecutor(max_workers=1) as executor:
            sending = executor.submit(
                client.post, "/internal/invitations", json=data, headers={"Authorization": "Bearer provisioning-test"}
            )
            try:
                assert delivered.wait(timeout=5)
                with store.transaction() as cur:
                    entry = cur.execute("SELECT * FROM invitations WHERE email=%s", (user["email"],)).fetchone()
                assert entry["auth_user_id"] is None and entry["joined_user_id"] is None
                headers = {"Authorization": "Bearer fake-verified-session"}
                assert client.post("/v1/join", headers=headers).status_code == 403
                user["email_confirmed_at"] = "2026-01-01T00:00:00Z"
                response = client.post(
                    "/v1/join",
                    headers=headers,
                    json={"id": str(uuid4()), "email": "other@example.test", "type": "business"},
                )
                assert response.status_code == 200
                joined = response.json()
                assert all(joined[key] == data[key] for key in ("name", "phone", "email", "type"))
                assert client.post("/v1/join", headers=headers).json()["id"] == joined["id"]
            finally:
                finish_delivery.set()
            assert sending.result(timeout=5).status_code == (503 if lost_response else 200)
        assert sends == ["/auth/v1/invite"]  # No founder resend or extra email.
    saved = store.invitation(entry["id"])
    assert saved["auth_user_id"] == identity and str(saved["joined_user_id"]) == joined["id"]
    with store.transaction() as cur:
        assert cur.execute("SELECT count(*) AS n FROM users WHERE auth_user_id=%s", (identity,)).fetchone()["n"] == 1


def messaging_invitation(store, phone):
    identity, invitation = uuid4(), uuid4()
    email = f"{invitation}@example.test"
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("INSERT INTO auth.users VALUES (%s)", (identity,))
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO invitations (id,name,phone,email,type,auth_user_id) VALUES (%s,'Messaging Example',%s,%s,'individual',%s)",
            (invitation, phone, email, identity),
        )
    return identity, email


def test_messaging_join_creates_zero_outbound(store):
    identity, email = messaging_invitation(store, "+12125550991")
    with ThreadPoolExecutor(max_workers=2) as executor:
        users = list(executor.map(lambda _: store.join(identity, email), range(2)))
    assert users[0]["id"] == users[1]["id"]
    with store.transaction() as cur:
        assert (
            cur.execute("SELECT count(*) AS n FROM messaging.messages WHERE user_id=%s", (users[0]["id"],)).fetchone()[
                "n"
            ]
            == 0
        )


def test_messaging_private_schema_and_immutable_columns(store):
    for role in ("anon", "authenticated"):
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
            db.execute(psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(role)))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM messaging.messages")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with store.transaction() as cur:
            cur.execute("UPDATE messaging.messages SET text='rewritten'")


def test_messaging_inbound_replay_and_atomic_reply(store):
    from dataclasses import replace
    from datetime import datetime, timedelta, timezone
    from tbd_server.messaging.core import ReceivedMessage, receive_message

    identity, email = messaging_invitation(store, "+12125550992")
    user = store.join(identity, email)
    now = datetime.now(timezone.utc)
    event = ReceivedMessage(
        "fake-inbound-" + str(uuid4()), "+12125550000", user["phone"], " \n🙂\n ", False, now, "conversation_fake"
    )

    def receive():
        with store.transaction() as cur:
            return receive_message(cur, event, now - timedelta(days=1))

    with ThreadPoolExecutor(max_workers=2) as executor:
        rows = list(executor.map(lambda _: receive(), range(2)))
    assert rows[0]["id"] == rows[1]["id"]
    assert rows[0]["text"] == event.text
    with store.transaction() as cur:
        assert (
            cur.execute(
                "SELECT count(*) AS n FROM messaging.messages WHERE reply_to_message_id=%s", (rows[0]["id"],)
            ).fetchone()["n"]
            == 1
        )
    with pytest.raises(Problem) as error:
        with store.transaction() as cur:
            receive_message(cur, replace(event, text="changed"), now)
    assert error.value.status == 409


def test_messaging_delivery_route_order_stale_results_and_known_recovery(store):
    from tbd_server.messaging.adapter import SendResult
    from tbd_server.messaging.core import queue_message
    from tbd_server.messaging.runtime import claim, record_send_result

    account = "+12125550007"
    identity, email = messaging_invitation(store, "+12125550993")
    user = store.join(identity, email)
    identity2, email2 = messaging_invitation(store, "+12125550994")
    other = store.join(identity2, email2)
    with store.transaction() as cur:
        Store.user(cur, user["id"])
        first = queue_message(
            cur, user, (account, user["phone"], "conversation_a"), "first", "first:" + str(user["id"])
        )
        second = queue_message(
            cur, user, (account, user["phone"], "conversation_a"), "second", "fake-second:" + str(user["id"])
        )
        Store.user(cur, other["id"])
        other_reply = queue_message(
            cur, other, (account, other["phone"], "conversation_b"), "first", "first:" + str(other["id"])
        )
    with store.transaction() as cur:
        claimed = claim(cur, account)
    assert claimed["id"] == first["id"]
    with store.transaction() as cur:
        # Another User progresses while the first route is awaiting an outcome.
        assert claim(cur, account)["id"] == other_reply["id"]
        assert claim(cur, account) is None
        assert not record_send_result(cur, first["id"], uuid4(), SendResult("failed"))
        assert record_send_result(cur, first["id"], claimed["attempt_id"], SendResult("provider_queued", "fake-job-1"))
        assert not record_send_result(cur, first["id"], claimed["attempt_id"], SendResult("sent", "wrong-job"))
        assert record_send_result(
            cur, first["id"], claimed["attempt_id"], SendResult("uncertain", "fake-job-1", "unknown_outcome")
        )
        assert claim(cur, account) is None
        assert record_send_result(
            cur, first["id"], claimed["attempt_id"], SendResult("sent", "fake-job-1", observed_status="sent")
        )
        assert not record_send_result(
            cur, first["id"], claimed["attempt_id"], SendResult("provider_queued", "fake-job-1")
        )
        assert claim(cur, account)["id"] == second["id"]


def test_messaging_reply_fk_blocks_cross_user_and_inbound_state_constraints(store):
    account = "+12125550008"
    identity, email = messaging_invitation(store, "+12125550995")
    user = store.join(identity, email)
    with pytest.raises(psycopg.errors.CheckViolation):
        with store.transaction() as cur:
            cur.execute(
                """INSERT INTO messaging.messages
                (user_id,channel,direction,dedupe_key,account_ref,peer_ref,conversation_id,text,state)
                VALUES (%s,'imessage','inbound','fake-invalid',%s,%s,'conversation_fake','text','received')""",
                (user["id"], account, user["phone"]),
            )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with store.transaction() as cur:
            cur.execute(
                """INSERT INTO messaging.messages
                (user_id,channel,direction,dedupe_key,account_ref,peer_ref,conversation_id,text,state,reply_to_message_id)
                VALUES (%s,'imessage','outbound','fake-orphan',%s,%s,'conversation_fake','reply','pending',%s)""",
                (user["id"], account, user["phone"], uuid4()),
            )


def test_comms_retry_window_and_stable_uuid(store):
    from tbd_server.messaging.core import queue_message
    from tbd_server.messaging.runtime import claim, record_send_result
    from tbd_server.messaging.adapter import SendResult

    identity, email = messaging_invitation(store, "+12125550996")
    user = store.join(identity, email)
    account = "+12125550016"
    with store.transaction() as cur:
        Store.user(cur, user["id"])
        original = queue_message(
            cur, user, (account, user["phone"], "conversation_window"), "reply", "window:" + str(user["id"])
        )
        first = claim(cur, account)
        record_send_result(cur, first["id"], first["attempt_id"], SendResult("pending", error_code="transport_unknown"))
        cur.execute("UPDATE messaging.messages SET next_action_at=now() WHERE id=%s", (first["id"],))
        second = claim(cur, account)
        assert second["id"] == original["id"]
        assert second["attempt_id"] != first["attempt_id"]
        assert second["first_attempt_at"] == first["first_attempt_at"]
        record_send_result(
            cur, second["id"], second["attempt_id"], SendResult("pending", error_code="transport_unknown")
        )
        cur.execute(
            "UPDATE messaging.messages SET first_attempt_at=now()-interval '25 hours',next_action_at=now() WHERE id=%s",
            (original["id"],),
        )
        assert claim(cur, account) is None
        held = cur.execute("SELECT * FROM messaging.messages WHERE id=%s", (original["id"],)).fetchone()
        assert held["state"] == "uncertain" and held["error_code"] == "idempotency_expired"


def test_comms_webhook_to_reply_with_actual_database_and_fake_provider(store):
    from datetime import datetime, timezone
    import hashlib
    import hmac
    import json
    from unittest.mock import Mock
    import httpx
    from fastapi.testclient import TestClient
    from tbd_server.user.api import create_app
    from tbd_server.messaging.service import Messaging
    from tbd_server.messaging.adapter import Comms
    from tbd_server.messaging.runtime import claim, record_send_result
    from test_messaging_adapter import inbound, outbound

    identity, email = messaging_invitation(store, "+12125550997")
    user = store.join(identity, email)
    messaging = Messaging(
        store,
        "fake",
        "fake-secret",
        "+12125550100",
        "2026-11-08T00:00:00Z",
        pilot_conversations={"conversation_fake": user["phone"]},
    )
    messaging.loop.start = Mock()
    messaging.loop.stop = Mock()
    record = inbound(
        id="msg_http_database", created_at=datetime.now(timezone.utc).isoformat(), author={"external_id": user["phone"]}
    )
    raw = json.dumps({"event": "comms.message.received", "data": {"message": record}}).encode()
    signature = "sha256=" + hmac.new(b"fake-secret", raw, hashlib.sha256).hexdigest()
    with TestClient(create_app(store, Mock(), "provision", "internal", [], messaging=messaging)) as client:
        for _ in range(2):
            assert (
                client.post("/webhooks/comms", content=raw, headers={"X-Osis-Signature": signature}).status_code == 200
            )
    with store.transaction() as cur:
        rows = cur.execute(
            "SELECT * FROM messaging.messages WHERE user_id=%s ORDER BY queue_sequence", (user["id"],)
        ).fetchall()
        assert len(rows) == 2 and rows[0]["text"] == record["body"]
        assert rows[1]["reply_to_message_id"] == rows[0]["id"]
        j = claim(cur, messaging.account)

    def handler(request):
        assert request.headers["idempotency-key"] == str(j["id"])
        return httpx.Response(202, json={"message": outbound(j, status="submitted")})

    adapter = Comms("fake", transport=httpx.MockTransport(handler))
    result = adapter.send(j)
    with store.transaction() as cur:
        assert record_send_result(cur, j["id"], j["attempt_id"], result)
    adapter.close()
    messaging.adapter.close()


def test_messaging_session_ownership_is_exclusive(store):
    from tbd_server.messaging.runtime import LOCK_ID

    with (
        psycopg.connect(store.database_url, autocommit=True) as first,
        psycopg.connect(store.database_url, autocommit=True) as second,
    ):
        assert first.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]
        assert not second.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]
        first.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
        assert second.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]


def test_comms_unknown_seed_boundary_and_history_edit_preservation(store):
    from dataclasses import replace
    from datetime import datetime, timedelta, timezone
    from tbd_server.messaging.core import ReceivedMessage, receive_message

    identity, email = messaging_invitation(store, "+12125550998")
    user = store.join(identity, email)
    seed = store.seed(Seed(seed_key="comms-ineligible", name="Fake Seed", phone="+12125550999"))
    now = datetime.now(timezone.utc)
    event = ReceivedMessage(
        "msg_boundaries", "+12125550100", user["phone"], "original", False, now, "conversation_boundaries"
    )
    with store.transaction() as cur:
        assert (
            receive_message(cur, replace(event, native_id="unknown", peer="+12125550989"), now - timedelta(days=1))
            is None
        )
        assert (
            receive_message(cur, replace(event, native_id="seed", peer=seed["phone"]), now - timedelta(days=1)) is None
        )
        assert (
            receive_message(
                cur,
                replace(event, native_id="old", source_at=user["created_at"] - timedelta(seconds=1)),
                now - timedelta(days=1),
            )
            is None
        )
        assert receive_message(cur, replace(event, native_id="inactive"), now + timedelta(seconds=1)) is None
        original = receive_message(cur, event, now - timedelta(days=1))
        replay = receive_message(cur, replace(event, text="edited", history=True), now - timedelta(days=1))
        assert replay["id"] == original["id"] and replay["text"] == "original"
        distinct = receive_message(cur, replace(event, native_id="msg_distinct_same_text"), now - timedelta(days=1))
        assert distinct["id"] != original["id"]
        assert (
            cur.execute("SELECT count(*) AS n FROM messaging.messages WHERE user_id=%s", (user["id"],)).fetchone()["n"]
            == 4
        )
    with pytest.raises(RuntimeError):
        with store.transaction() as cur:
            receive_message(cur, replace(event, native_id="msg_rollback"), now - timedelta(days=1))
            raise RuntimeError("injected before commit")
    with store.transaction() as cur:
        assert (
            cur.execute("SELECT 1 FROM messaging.messages WHERE dedupe_key='inbound:msg_rollback'").fetchone() is None
        )


def test_comms_exact_id_unknown_to_read_releases_next_conversation_job(store):
    """Actual sanitized provider shapes, real SQL states, no live HTTP/send."""
    from copy import deepcopy
    import json
    import httpx
    from tbd_server.messaging.adapter import Comms
    from tbd_server.messaging.core import queue_message
    from tbd_server.messaging.runtime import claim, record_send_result

    fixture = json.loads(Path("docs/messaging-service/fixtures/comms-free-pilot-a.json").read_text())
    account = "+12125550025"
    identity, email = messaging_invitation(store, "+12125550988")
    user = store.join(identity, email)
    conversation = "conversation_read_recovery"
    with store.transaction() as cur:
        Store.user(cur, user["id"])
        first = queue_message(
            cur, user, (account, user["phone"], conversation), "Received your text.", "read-first:" + str(user["id"])
        )
        second = queue_message(
            cur, user, (account, user["phone"], conversation), "Next reply", "read-second:" + str(user["id"])
        )
        attempted = claim(cur, account)
    assert attempted["id"] == first["id"]
    accepted = deepcopy(fixture["acceptance"]["message"])
    read = deepcopy(fixture["later_outbound_history"][0])
    for record in (accepted, read):
        record.update(line_e164=account, conversation_id=conversation)
    acceptance = Comms.classify(accepted, attempted)
    assert acceptance.state == "uncertain" and acceptance.observed_status == "unknown"
    with store.transaction() as cur:
        assert record_send_result(cur, first["id"], attempted["attempt_id"], acceptance)
        assert claim(cur, account) is None
        known = cur.execute("SELECT * FROM messaging.messages WHERE id=%s", (first["id"],)).fetchone()
    seen = []

    def handler(request):
        assert request.method == "GET"
        assert request.url.params["conversation_id"] == conversation
        assert request.url.params["direction"] == "outbound"
        seen.append(request.method)
        # A read result for another ID must not release this route.
        records = [{**read, "id": "wrong_id"}]
        if len(seen) > 1:
            records.append(read)
        return httpx.Response(200, json={"messages": records})

    adapter = Comms("fake", transport=httpx.MockTransport(handler))
    try:
        missing = adapter.status(known)
        assert missing.state == "uncertain"
        with store.transaction() as cur:
            assert record_send_result(cur, first["id"], attempted["attempt_id"], missing)
            assert claim(cur, account) is None
        terminal = adapter.status(known)
    finally:
        adapter.close()
    assert seen == ["GET", "GET"]
    assert terminal.state == "sent" and terminal.observed_status == "read"
    with store.transaction() as cur:
        assert record_send_result(cur, first["id"], attempted["attempt_id"], terminal)
        stored = cur.execute("SELECT * FROM messaging.messages WHERE id=%s", (first["id"],)).fetchone()
        assert stored["state"] == "sent" and stored["observed_status"] == "read"
        assert stored["accepted_at"] is not None and stored["sent_observed_at"] is not None
        assert not record_send_result(cur, first["id"], attempted["attempt_id"], acceptance)
        assert claim(cur, account)["id"] == second["id"]


def test_allowlisted_pilot_fixture_webhook_and_history_share_admission(store):
    """Real sanitized shapes, signature boundary and actual atomic SQL; no live send."""
    from copy import deepcopy
    from datetime import datetime, timedelta
    import hashlib
    import hmac
    import json
    from unittest.mock import Mock
    import httpx
    from fastapi.testclient import TestClient
    from tbd_server.messaging.adapter import Comms
    from tbd_server.messaging.service import Messaging
    from tbd_server.user.api import create_app

    fixture = json.loads(Path("docs/messaging-service/fixtures/comms-free-pilot-a.json").read_text())
    event, history = deepcopy(fixture["webhook"]), deepcopy(fixture["history"])
    identity, email = messaging_invitation(store, "+12125550986")
    user = store.join(identity, email)
    other_identity, other_email = messaging_invitation(store, "+12125550985")
    other = store.join(other_identity, other_email)
    # Rebind the fixture's fake phone to an isolated fake User, preserving real shape/time/text.
    event["data"]["message"]["author"]["external_id"] = user["phone"]
    event["data"]["contact"]["phone"] = user["phone"]
    history["author"]["external_id"] = user["phone"]
    source = datetime.fromisoformat(history["created_at"].replace("Z", "+00:00"))
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute(
            "UPDATE user_service.users SET created_at=%s WHERE id IN (%s,%s)",
            (source - timedelta(days=1), user["id"], other["id"]),
        )
    account, conversation = history["line_e164"], history["conversation_id"]
    messaging = Messaging(
        store,
        "fake-key",
        "fake-secret",
        account,
        (source - timedelta(hours=1)).isoformat(),
        pilot_conversations={conversation: user["phone"]},
    )
    messaging.loop.start = Mock()
    messaging.loop.stop = Mock()
    records = [history]

    def provider(request):
        assert request.method == "GET" and request.url.params["direction"] == "inbound"
        assert request.headers["authorization"] == "Bearer fake-key"
        return httpx.Response(200, json={"messages": records})

    messaging.adapter.close()
    messaging.adapter = Comms("fake-key", transport=httpx.MockTransport(provider))
    with TestClient(create_app(store, Mock(), "provision", "internal", [], messaging=messaging)) as client:

        def post(value):
            raw = json.dumps(value).encode()
            signature = "sha256=" + hmac.new(b"fake-secret", raw, hashlib.sha256).hexdigest()
            return client.post("/webhooks/comms", content=raw, headers={"X-Osis-Signature": signature})

        assert post(event).status_code == 200
        with store.transaction() as cur:
            rows = cur.execute(
                "SELECT * FROM messaging.messages WHERE user_id=%s ORDER BY queue_sequence", (user["id"],)
            ).fetchall()
        assert len(rows) == 2
        assert rows[0]["text"] == history["body"] and rows[0]["state"] == "received"
        assert rows[1]["text"] == "Received your text." and rows[1]["state"] == "pending"
        assert rows[1]["reply_to_message_id"] == rows[0]["id"]
        assert rows[0]["conversation_id"] == rows[1]["conversation_id"] == conversation
        assert post(event).status_code == 200
        assert messaging.history_turn(messaging.loop)
        changes = [
            {"author": {"external_id": other["phone"]}},
            {"conversation_id": "conversation_not_approved"},
            {"line_e164": "+12125550984"},
            {"audience": "shared"},
            {"attachments": [{"mediaType": "image"}]},
            {"unsent_at": history["created_at"]},
            {"direction": "outbound"},
            {"channel": "sms"},
            {"event": "comms.message.reaction.added"},
            {"event": "comms.message.edited"},
        ]
        for n, change in enumerate(changes):
            excluded = deepcopy(event)
            excluded["data"]["message"].update(id=f"pilot_excluded_{n}", **change)
            assert post(excluded).status_code == 200
            records.append(excluded["data"]["message"])
        messaging.next_sweep = 0
        assert messaging.history_turn(messaging.loop)
        with store.transaction() as cur:
            rows_after = cur.execute(
                "SELECT * FROM messaging.messages WHERE user_id IN (%s,%s) ORDER BY queue_sequence",
                (user["id"], other["id"]),
            ).fetchall()
        assert [r["id"] for r in rows_after] == [r["id"] for r in rows]
    messaging.adapter.close()
