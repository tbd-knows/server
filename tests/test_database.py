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
        db.execute(next(Path("supabase/migrations").glob("*_user_service.sql")).read_text())
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


def approved(store, *, type="individual", phone=None):
    from tbd_server.user.models import Invitation

    identity = uuid4()
    email = f"{identity}@example.test"
    # Fixed 555-01xx fixture range; no live contact records.
    phone = phone or f"+1212555{next(_fixture_phones):04d}"
    entry = store.approve(Invitation(name="Approved Example", phone=phone, email=email, type=type))
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("INSERT INTO auth.users VALUES (%s)", (identity,))
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


def test_failed_join_rolls_back_user_insert(store, monkeypatch):
    entry, identity = approved(store)
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
