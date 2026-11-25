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
        db.execute(Path("supabase/migrations/20261101103939_user_service.sql").read_text())
        # Rehearse the additive release over existing User data, not empty tables.
        prior_user, prior_auth, prior_invite, prior_address = (uuid4() for _ in range(4))
        db.execute("INSERT INTO auth.users VALUES (%s)", (prior_auth,))
        db.execute(
            """INSERT INTO user_service.users (id,auth_user_id,name,type,phone,email)
            VALUES (%s,%s,'Release Rehearsal','individual','+12125550881','release@example.test')""",
            (prior_user, prior_auth),
        )
        db.execute(
            """INSERT INTO user_service.invitations
            (id,name,type,phone,email,auth_user_id,joined_user_id)
            VALUES (%s,'Release Rehearsal','individual','+12125550881','release@example.test',%s,%s)""",
            (prior_invite, prior_auth, prior_user),
        )
        db.execute(
            """INSERT INTO user_service.addresses
            (id,user_id,recipient,line1,city,region,postal_code,country_code,is_default)
            VALUES (%s,%s,'Release Rehearsal','1 Example St','Example','NY','10001','US',true)""",
            (prior_address, prior_user),
        )
        snapshot_queries = [
            "SELECT * FROM user_service.users ORDER BY id",
            "SELECT * FROM user_service.invitations ORDER BY id",
            "SELECT * FROM user_service.addresses ORDER BY id",
            "SELECT * FROM auth.users ORDER BY id",
            "SELECT table_name,grantee,privilege_type FROM information_schema.role_table_grants WHERE table_schema='user_service' ORDER BY 1,2,3",
            "SELECT nspname,nspacl::text FROM pg_namespace WHERE nspname='user_service'",
            "SELECT tablename,policyname,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname='user_service' ORDER BY 1,2",
        ]
        before = [db.execute(query).fetchall() for query in snapshot_queries]
        for migration in sorted(Path("supabase/migrations").glob("*.sql")):
            if not migration.name.endswith("_user_service.sql"):
                db.execute(migration.read_text())
        assert [db.execute(query).fetchall() for query in snapshot_queries] == before
        # Remove only these known fake rehearsal records, retaining the new schema.
        db.execute("DELETE FROM user_service.addresses WHERE id=%s", (prior_address,))
        db.execute("DELETE FROM user_service.invitations WHERE id=%s", (prior_invite,))
        db.execute("DELETE FROM user_service.users WHERE id=%s", (prior_user,))
        db.execute("DELETE FROM auth.users WHERE id=%s", (prior_auth,))
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


def test_release_rehearsal_uvicorn_starts_with_real_role_and_healthy_owned_loop(store):
    """Real HTTP/process/lifespan and local DB; every provider call is intercepted."""
    import json
    import socket
    import subprocess
    import sys
    import time
    import httpx

    script = """
import httpx
import uvicorn
from tbd_server.messaging.adapter import Comms

def fake_provider(self, method, path, **kwargs):
    assert method == "GET" and path == "messages", "No live or outbound provider calls in rehearsal"
    request = httpx.Request(method, "https://provider.example.test/messages")
    return httpx.Response(200, json={"messages": []}, request=request)
Comms.request = fake_provider
uvicorn.run("tbd_server.main:app", host="127.0.0.1", port=PORT, workers=1, access_log=False)
"""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": "src",
        "DATABASE_URL": store.database_url,
        "SUPABASE_URL": "https://auth.example.test",
        "SUPABASE_SECRET_KEY": "fake-auth-key",
        "JOIN_SITE_URL": "https://join.example.test",
        "PROVISIONING_TOKEN": "fake-provision",
        "INTERNAL_SERVICE_TOKEN": "fake-internal",
        "ALLOWED_WEB_ORIGINS": "https://join.example.test",
        "COMMS_API_KEY": "fake-runtime-key",
        "COMMS_WEBHOOK_SECRET": "fake-webhook-secret",
        "COMMS_LINE_E164": "+12125550880",
        "MESSAGING_ACTIVATED_AT": "2026-11-08T00:00:00Z",
        "COMMS_PILOT_CONVERSATIONS": "{}",
    }
    process = subprocess.Popen(
        [sys.executable, "-c", script.replace("PORT", str(port))],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            if process.poll() is not None:
                pytest.fail("Uvicorn exited before readiness")
            try:
                response = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=0.5)
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                pytest.fail("Uvicorn readiness deadline")
            time.sleep(0.05)
        assert response.json() == {"status": "ok"}
        # Require actual worker ownership and a successful fake history call, not
        # merely a thread alive while waiting for ownership.
        while True:
            with store.transaction() as cur:
                owned = cur.execute(
                    "SELECT count(*) AS n FROM pg_locks WHERE locktype='advisory' AND objid=843219017 AND granted"
                ).fetchone()["n"]
            if owned:
                break
            assert time.monotonic() < deadline
            time.sleep(0.05)
        time.sleep(0.7)
        assert httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
    # Uvicorn re-raises SIGTERM after its graceful lifespan shutdown.
    assert process.returncode in (0, -15)
    assert "Application shutdown complete" in stderr
    with store.transaction() as cur:
        assert (
            cur.execute(
                "SELECT count(*) AS n FROM pg_locks WHERE locktype='advisory' AND objid=843219017 AND granted"
            ).fetchone()["n"]
            == 0
        )
    connection = json.loads(stdout.splitlines()[0])["database_connection_verified"]
    assert connection["role"] == "tbd_user_backend"
    assert '"history_sweep_complete"' in stderr and '"loop_failed"' not in stderr


# Card rollouts use the same local cluster/released-schema rehearsal above.
@pytest.fixture
def card_thread(store):
    from datetime import datetime, timezone
    from tbd_server.messaging.core import ReceivedMessage, receive_message

    entry, auth_id = approved(store)
    user = store.join(auth_id, entry["email"])
    account, conversation = "+12125550188", "fake-card-" + str(uuid4())
    event = ReceivedMessage(
        str(uuid4()), account, user["phone"], "fake card eligibility", False, datetime.now(timezone.utc), conversation
    )
    with store.transaction() as cur:
        inbound = receive_message(cur, event, user["created_at"])
        reply = cur.execute(
            "SELECT * FROM messaging.messages WHERE reply_to_message_id=%s", (inbound["id"],)
        ).fetchone()
        cur.execute(
            "UPDATE messaging.messages SET state='sent',attempt_id=%s,provider_message_id=%s,sent_observed_at=now() WHERE id=%s",
            (uuid4(), "fake-reply-" + str(uuid4()), reply["id"]),
        )
    context = {
        "store": store,
        "user": user,
        "account": account,
        "pairs": {conversation: user["phone"]},
        "conversation": conversation,
        "reply_id": reply["id"],
    }
    yield context
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("DELETE FROM messaging.contact_cards WHERE user_id=%s", (user["id"],))
        db.execute("DELETE FROM messaging.messages WHERE user_id=%s AND direction='outbound'", (user["id"],))
        db.execute("DELETE FROM messaging.messages WHERE user_id=%s AND direction='inbound'", (user["id"],))


def test_cards_existing_new_threads_and_replay(card_thread):
    from tbd_server.messaging.cards import enqueue

    c = card_thread
    with c["store"].transaction() as cur:
        cur.execute("UPDATE messaging.messages SET state='pending' WHERE id=%s", (c["reply_id"],))
        enqueue(cur, c["account"], c["pairs"])
        assert (
            cur.execute(
                "SELECT count(*) AS n FROM messaging.contact_cards WHERE user_id=%s", (c["user"]["id"],)
            ).fetchone()["n"]
            == 0
        )
        cur.execute("UPDATE messaging.messages SET state='sent' WHERE id=%s", (c["reply_id"],))
        enqueue(cur, c["account"], c["pairs"])
        enqueue(cur, c["account"], c["pairs"])
        assert (
            cur.execute(
                "SELECT count(*) AS n FROM messaging.contact_cards WHERE user_id=%s", (c["user"]["id"],)
            ).fetchone()["n"]
            == 1
        )
        # Wrong configured number/pair/conversation cannot add another card.
        enqueue(cur, "+12125550189", c["pairs"])
        enqueue(cur, c["account"], {c["conversation"]: "+12125550190", "unapproved-thread": c["user"]["phone"]})
        assert (
            cur.execute(
                "SELECT count(*) AS n FROM messaging.contact_cards WHERE user_id=%s", (c["user"]["id"],)
            ).fetchone()["n"]
            == 1
        )


def test_cards_concurrent_claim_restart_and_expired_uncertainty(card_thread):
    from tbd_server.messaging.cards import enqueue, claim_card

    c = card_thread

    def do_claim(_):
        with c["store"].transaction() as cur:
            enqueue(cur, c["account"], c["pairs"])
            return claim_card(cur, c["account"], c["pairs"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(do_claim, range(2)))
    job = next(r for r in rows if r is not None)
    assert len([r for r in rows if r is not None]) == 1
    with c["store"].transaction() as cur:
        # Lost process: wait out the persisted lease before reclaiming the same key.
        assert claim_card(cur, c["account"], c["pairs"]) is None
        cur.execute(
            "UPDATE messaging.contact_cards SET next_action_at=now()-interval '1 second' WHERE id=%s", (job["id"],)
        )
        recovered = claim_card(cur, c["account"], c["pairs"])
        assert recovered["idempotency_key"] == job["idempotency_key"] and recovered["attempt_count"] == 2
        cur.execute(
            "UPDATE messaging.contact_cards SET first_attempt_at=now()-interval '25 hours',next_action_at=now() WHERE id=%s",
            (job["id"],),
        )
        assert claim_card(cur, c["account"], c["pairs"]) is None
        assert (
            cur.execute("SELECT error_code FROM messaging.contact_cards WHERE id=%s", (job["id"],)).fetchone()[
                "error_code"
            ]
            == "idempotency_expired"
        )


def test_cards_import_pilot_a_then_never_claim_or_repeat(card_thread):
    from tbd_server.messaging.cards import identity, PHOTO_SHA, import_verified, enqueue, claim_card
    from tbd_server.messaging.adapter import SendResult

    c = card_thread
    digest, key = identity(c["account"], c["conversation"])
    evidence = {
        "conversation_id": c["conversation"],
        "attempt_state": "accepted",
        "payload_sha256": digest,
        "idempotency_key": key,
        "photo_sha256": PHOTO_SHA,
        "provider_message_id": "fake-existing-card",
    }
    summary = {
        "card_received_in_original_thread_confirmed": True,
        "saved_contact_logo_visible_in_messages_confirmed": True,
    }
    verified = SendResult("sent", "fake-existing-card", observed_status="read", response_body="")
    with c["store"].transaction() as cur:
        imported = import_verified(cur, c["account"], c["pairs"], evidence, summary, verified)
        assert import_verified(cur, c["account"], c["pairs"], evidence, summary, verified) == imported
        enqueue(cur, c["account"], c["pairs"])
        assert claim_card(cur, c["account"], c["pairs"]) is None
        row = cur.execute("SELECT * FROM messaging.contact_cards WHERE id=%s", (imported,)).fetchone()
        assert (
            row["state"] == "sent" and row["attempt_count"] == 0 and row["provider_message_id"] == "fake-existing-card"
        )
    with pytest.raises(ValueError):
        with c["store"].transaction() as cur:
            import_verified(
                cur, c["account"], c["pairs"], {**evidence, "provider_message_id": "wrong"}, summary, verified
            )


def test_cards_permissions_canonical_join_recheck_and_isolated_failure(card_thread):
    from tbd_server.messaging.cards import enqueue, claim_card, record_card_result
    from tbd_server.messaging.adapter import SendResult

    c = card_thread
    with c["store"].transaction() as cur:
        enqueue(cur, c["account"], c["pairs"])
        before = cur.execute(
            "SELECT * FROM messaging.messages WHERE user_id=%s ORDER BY id", (c["user"]["id"],)
        ).fetchall()
        cur.execute(
            "UPDATE user_service.invitations SET joined_user_id=NULL WHERE joined_user_id=%s", (c["user"]["id"],)
        )
        assert claim_card(cur, c["account"], c["pairs"]) is None
        cur.execute(
            "UPDATE user_service.invitations SET joined_user_id=%s WHERE auth_user_id=%s",
            (c["user"]["id"], c["user"]["auth_user_id"]),
        )
        job = claim_card(cur, c["account"], c["pairs"])
        assert record_card_result(cur, job, SendResult("failed", error_code="request_rejected"))
        assert (
            cur.execute("SELECT * FROM messaging.messages WHERE user_id=%s ORDER BY id", (c["user"]["id"],)).fetchall()
            == before
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with c["store"].transaction() as cur:
            cur.execute("UPDATE messaging.contact_cards SET peer_ref='+12125550191'")
    for role in ("anon", "authenticated"):
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
            db.execute(psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(role)))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM messaging.contact_cards")


def test_cards_background_io_cannot_block_reply_and_one_in_flight(card_thread):
    from threading import Event
    from unittest.mock import Mock
    from tbd_server.messaging.runtime import DeliveryLoop
    from tbd_server.messaging.adapter import SendResult

    c = card_thread
    started, release = Event(), Event()
    adapter = Mock()

    def slow_card(job):
        started.set()
        assert release.wait(5)
        return SendResult("failed", error_code="request_rejected")

    adapter.send_card.side_effect = slow_card
    loop = DeliveryLoop(c["store"].database_url, adapter, c["account"], lambda *_: False, card_pairs=c["pairs"])
    loop.last_send = 0
    try:
        with psycopg.connect(c["store"].database_url, autocommit=True, row_factory=psycopg.rows.dict_row) as db:
            loop.card_turn(db)
            assert started.wait(2)
            loop.card_turn(db)
            assert adapter.send_card.call_count == 1
            # A newly due normal reply progresses while the card HTTP is blocked.
            db.execute(
                "UPDATE messaging.messages SET state='pending',attempt_id=NULL,provider_message_id=NULL WHERE id=%s",
                (c["reply_id"],),
            )
            loop.category = 1
            loop.last_send = 0
            adapter.send.return_value = SendResult("sent", "fake-new-normal", observed_status="sent")
            loop.turn(db)
            assert adapter.send.call_count == 1
            assert (
                db.execute("SELECT state FROM messaging.messages WHERE id=%s", (c["reply_id"],)).fetchone()["state"]
                == "sent"
            )
            release.set()
            loop.card_operation[1].result(timeout=2)
            loop.finish_card(db)
            assert (
                db.execute("SELECT state FROM messaging.messages WHERE id=%s", (c["reply_id"],)).fetchone()["state"]
                == "sent"
            )
            assert (
                db.execute("SELECT state FROM messaging.contact_cards WHERE user_id=%s", (c["user"]["id"],)).fetchone()[
                    "state"
                ]
                == "failed"
            )
    finally:
        release.set()
        if loop.card_executor:
            loop.card_executor.shutdown(wait=True)


def test_card_known_id_restart_uses_get_and_stale_attempt_cannot_rewrite(card_thread):
    from tbd_server.messaging.cards import enqueue, claim_card, record_card_result
    from tbd_server.messaging.adapter import SendResult

    c = card_thread
    with c["store"].transaction() as cur:
        enqueue(cur, c["account"], c["pairs"])
        first = claim_card(cur, c["account"], c["pairs"])
        assert record_card_result(
            cur, first, SendResult("uncertain", "fake-known-card", observed_status="unknown", response_body="")
        )
        cur.execute("UPDATE messaging.contact_cards SET next_action_at=now() WHERE id=%s", (first["id"],))
        read_job = claim_card(cur, c["account"], c["pairs"])
        assert read_job["provider_message_id"] == "fake-known-card" and read_job["response_body"] == ""
        assert read_job["attempt_count"] == 1 and read_job["idempotency_key"] == first["idempotency_key"]
        assert not record_card_result(cur, first, SendResult("failed", error_code="stale"))
        assert record_card_result(
            cur, read_job, SendResult("sent", "fake-known-card", observed_status="read", response_body="")
        )
        assert not record_card_result(cur, read_job, SendResult("uncertain", "fake-known-card", response_body=""))
        assert claim_card(cur, c["account"], c["pairs"]) is None


def test_card_global_lease_prevents_second_conversation_claim(card_thread):
    from datetime import datetime, timezone
    from tbd_server.messaging.core import ReceivedMessage, receive_message
    from tbd_server.messaging.cards import enqueue, claim_card

    c = card_thread
    other = "fake-second-card-" + str(uuid4())
    pairs = {**c["pairs"], other: c["user"]["phone"]}
    with c["store"].transaction() as cur:
        received = receive_message(
            cur,
            ReceivedMessage(
                str(uuid4()),
                c["account"],
                c["user"]["phone"],
                "another thread",
                False,
                datetime.now(timezone.utc),
                other,
            ),
            c["user"]["created_at"],
        )
        cur.execute(
            "UPDATE messaging.messages SET state='sent',provider_message_id=%s,attempt_id=%s,sent_observed_at=now() WHERE reply_to_message_id=%s",
            ("fake-other-reply-" + str(uuid4()), uuid4(), received["id"]),
        )
        enqueue(cur, c["account"], pairs)
        assert (
            cur.execute(
                "SELECT count(*) AS n FROM messaging.contact_cards WHERE user_id=%s", (c["user"]["id"],)
            ).fetchone()["n"]
            == 2
        )
        assert claim_card(cur, c["account"], pairs) is not None
        assert claim_card(cur, c["account"], pairs) is None


def test_card_reply_priority_and_pacing_before_launch(card_thread):
    from unittest.mock import Mock
    from time import monotonic
    from tbd_server.messaging.runtime import DeliveryLoop

    c = card_thread
    adapter = Mock()
    loop = DeliveryLoop(c["store"].database_url, adapter, c["account"], lambda *_: False, card_pairs=c["pairs"])
    with psycopg.connect(c["store"].database_url, autocommit=True, row_factory=psycopg.rows.dict_row) as db:
        loop.last_send = monotonic()
        loop.card_turn(db)
        assert loop.card_operation is None  # same pacing budget as replies
        loop.last_send = 0
        db.execute("UPDATE messaging.messages SET state='pending' WHERE id=%s", (c["reply_id"],))
        loop.card_turn(db)
        assert loop.card_operation is None and not adapter.send_card.called
        # With rollout disabled, no ledger or provider work is needed.
        disabled = DeliveryLoop(c["store"].database_url, adapter, c["account"], lambda *_: False)
        disabled.card_turn(db)
        assert not adapter.send_card.called


def test_cards_enabled_startup_requires_imported_pilot_record(card_thread):
    from tbd_server.messaging.service import Messaging
    from tbd_server.messaging.cards import identity, PHOTO_SHA, import_verified
    from tbd_server.messaging.adapter import SendResult

    c = card_thread
    service = Messaging(
        c["store"],
        "fake",
        "fake-secret",
        c["account"],
        "2026-11-08T00:00:00Z",
        pilot_conversations=c["pairs"],
        cards_enabled=True,
    )
    try:
        service.verify_schema()
        assert service.loop.card_pairs is None
        digest, key = identity(c["account"], c["conversation"])
        evidence = {
            "conversation_id": c["conversation"],
            "attempt_state": "accepted",
            "payload_sha256": digest,
            "idempotency_key": key,
            "photo_sha256": PHOTO_SHA,
            "provider_message_id": "fake-startup-import-" + str(uuid4()),
        }
        with c["store"].transaction() as cur:
            import_verified(
                cur,
                c["account"],
                c["pairs"],
                evidence,
                {
                    "card_received_in_original_thread_confirmed": True,
                    "saved_contact_logo_visible_in_messages_confirmed": True,
                },
                SendResult("sent", evidence["provider_message_id"], observed_status="sent", response_body=""),
            )
        service.verify_schema()
        assert service.loop.card_pairs == c["pairs"]
    finally:
        service.adapter.close()


def test_card_import_cli_only_gets_exact_history(card_thread, tmp_path, monkeypatch, capsys):
    import json
    import httpx
    from tbd_server.messaging import cards
    from tbd_server.messaging.adapter import Comms

    c = card_thread
    digest, key = cards.identity(c["account"], c["conversation"])
    message = {
        "id": "fake-cli-card-" + str(uuid4()),
        "conversation_id": c["conversation"],
        "line_e164": c["account"],
        "direction": "outbound",
        "channel": "imessage",
        "body": "",
        "status": "read",
    }
    evidence = {
        "conversation_id": c["conversation"],
        "attempt_state": "accepted",
        "payload_sha256": digest,
        "idempotency_key": key,
        "photo_sha256": cards.PHOTO_SHA,
        "provider_message_id": message["id"],
        "response": {"message": message, "contact_card": {"name": "TBD", "phone": c["account"]}},
    }
    summary = {
        "card_received_in_original_thread_confirmed": True,
        "saved_contact_logo_visible_in_messages_confirmed": True,
    }
    first, second = tmp_path / "evidence.json", tmp_path / "summary.json"
    first.write_text(json.dumps(evidence))
    second.write_text(json.dumps(summary))
    requests = []

    def provider(request):
        requests.append(request)
        assert request.method == "GET"
        return httpx.Response(200, json={"messages": [message]})

    monkeypatch.setattr("sys.argv", ["cards", str(first), str(second)])
    for k, v in {
        "DATABASE_URL": c["store"].database_url,
        "COMMS_LINE_E164": c["account"],
        "COMMS_PILOT_CONVERSATIONS": json.dumps(c["pairs"]),
        "COMMS_API_KEY": "fake",
    }.items():
        monkeypatch.setenv(k, v)
    # Patch the imported adapter factory, preserving the real card classifier.
    import tbd_server.messaging.adapter as module

    monkeypatch.setattr(module, "Comms", lambda _: Comms("fake", transport=httpx.MockTransport(provider)))
    cards.main()
    cards.main()
    assert len(requests) == 2 and '"card_sent": false' in capsys.readouterr().out
    with c["store"].transaction() as cur:
        row = cur.execute("SELECT * FROM messaging.contact_cards WHERE user_id=%s", (c["user"]["id"],)).fetchone()
        assert row["state"] == "sent" and row["provider_message_id"] == message["id"] and row["attempt_count"] == 0


def test_card_result_commit_retry_retains_known_id_without_repost(card_thread, monkeypatch):
    from concurrent.futures import Future
    from unittest.mock import Mock
    from tbd_server.messaging import cards
    from tbd_server.messaging.runtime import DeliveryLoop
    from tbd_server.messaging.adapter import SendResult

    c = card_thread
    with c["store"].transaction() as cur:
        cards.enqueue(cur, c["account"], c["pairs"])
        job = cards.claim_card(cur, c["account"], c["pairs"])
    future = Future()
    future.set_result(SendResult("sent", "fake-commit-card", observed_status="sent", response_body=""))
    loop = DeliveryLoop(c["store"].database_url, Mock(), c["account"], lambda *_: False, card_pairs=c["pairs"])
    loop.card_operation = (job, future)
    with psycopg.connect(c["store"].database_url, autocommit=True, row_factory=psycopg.rows.dict_row) as db:
        with monkeypatch.context() as m:
            m.setattr(cards, "record_card_result", Mock(side_effect=psycopg.OperationalError("fake commit loss")))
            loop.finish_card(db)
            assert loop.card_operation is not None
        loop.finish_card(db)
        assert loop.card_operation is None
        row = db.execute("SELECT * FROM messaging.contact_cards WHERE id=%s", (job["id"],)).fetchone()
        assert row["state"] == "sent" and row["provider_message_id"] == "fake-commit-card"
        assert not loop.adapter.send_card.called


def test_card_quota_only_records_ledger_and_honors_shared_account_pause(card_thread):
    from unittest.mock import Mock
    from tbd_server.messaging.cards import enqueue, claim_card, record_card_result
    from tbd_server.messaging.adapter import SendResult
    from tbd_server.messaging.runtime import DeliveryLoop

    c = card_thread
    with c["store"].transaction() as cur:
        before = cur.execute("SELECT * FROM messaging.messages WHERE id=%s", (c["reply_id"],)).fetchone()
        enqueue(cur, c["account"], c["pairs"])
        job = claim_card(cur, c["account"], c["pairs"])
        record_card_result(cur, job, SendResult("pending", error_code="card_account_quota", retry_after=3600))
        assert cur.execute("SELECT * FROM messaging.messages WHERE id=%s", (c["reply_id"],)).fetchone() == before
    adapter = Mock()
    loop = DeliveryLoop(c["store"].database_url, adapter, c["account"], lambda *_: False, card_pairs=c["pairs"])
    with psycopg.connect(c["store"].database_url, autocommit=True, row_factory=psycopg.rows.dict_row) as db:
        loop.turn(db)
    assert not adapter.send.called and not adapter.send_card.called


def test_card_orderly_stop_flushes_completed_outcome(card_thread):
    from concurrent.futures import Future
    from unittest.mock import Mock
    from tbd_server.messaging.cards import enqueue, claim_card
    from tbd_server.messaging.runtime import DeliveryLoop
    from tbd_server.messaging.adapter import SendResult

    c = card_thread
    with c["store"].transaction() as cur:
        enqueue(cur, c["account"], c["pairs"])
        job = claim_card(cur, c["account"], c["pairs"])
    future = Future()
    future.set_result(SendResult("sent", "fake-shutdown-card", observed_status="sent", response_body=""))
    adapter = Mock()
    loop = DeliveryLoop(c["store"].database_url, adapter, c["account"], lambda *_: False, card_pairs=c["pairs"])
    loop.card_operation = (job, future)
    loop.card_executor = Mock()
    loop.stop()
    assert loop.card_operation is None
    adapter.close.assert_called_once()
    with c["store"].transaction() as cur:
        row = cur.execute("SELECT * FROM messaging.contact_cards WHERE id=%s", (job["id"],)).fetchone()
        assert row["state"] == "sent" and row["provider_message_id"] == "fake-shutdown-card"
        assert (
            cur.execute("SELECT state FROM messaging.messages WHERE id=%s", (c["reply_id"],)).fetchone()["state"]
            == "sent"
        )


@pytest.mark.parametrize("status,code", [(402, "dedicated_line_required"), (403, "feature_disabled")])
def test_card_only_rejection_does_not_pause_due_reply_or_history(card_thread, status, code):
    import json
    import httpx
    from unittest.mock import Mock
    from tbd_server.messaging.cards import enqueue, claim_card, record_card_result
    from tbd_server.messaging.adapter import Comms
    from tbd_server.messaging.runtime import DeliveryLoop

    c = card_thread
    requests = []

    def provider(request):
        requests.append(request)
        if request.url.path.endswith("/contact-cards"):
            return httpx.Response(status, json={"code": code})
        assert request.url.path.endswith("/messages") and request.method == "POST"
        data = json.loads(request.content)
        return httpx.Response(
            202,
            json={
                "message": {
                    "id": "fake-after-restriction-" + str(uuid4()),
                    "conversation_id": data["conversation_id"],
                    "line_e164": c["account"],
                    "direction": "outbound",
                    "channel": "imessage",
                    "body": data["body"],
                    "status": "sent",
                }
            },
        )

    adapter = Comms("fake", transport=httpx.MockTransport(provider))
    try:
        with c["store"].transaction() as cur:
            enqueue(cur, c["account"], c["pairs"])
            card = claim_card(cur, c["account"], c["pairs"])
        result = adapter.send_card(card)
        assert result.error_code == "card_restricted"
        with c["store"].transaction() as cur:
            record_card_result(cur, card, result)
            cur.execute(
                "UPDATE messaging.messages SET state='pending',attempt_id=NULL,provider_message_id=NULL,next_action_at=now() WHERE id=%s",
                (c["reply_id"],),
            )
        history = Mock(return_value=False)
        loop = DeliveryLoop(c["store"].database_url, adapter, c["account"], history, card_pairs=c["pairs"])
        loop.category = 1
        loop.last_send = 0
        with psycopg.connect(c["store"].database_url, autocommit=True, row_factory=psycopg.rows.dict_row) as db:
            loop.turn(db)
            assert (
                db.execute("SELECT state FROM messaging.messages WHERE id=%s", (c["reply_id"],)).fetchone()["state"]
                == "sent"
            )
            loop.turn(db)
            history.assert_called_once()
            assert (
                db.execute("SELECT state FROM messaging.contact_cards WHERE id=%s", (card["id"],)).fetchone()["state"]
                == "failed"
            )
        assert len(requests) == 2
    finally:
        adapter.close()


@pytest.mark.parametrize("code", ["idempotency_in_progress", "idempotency_key_reused"])
def test_card_conflict_recovery_persists_original_key_and_backoff(card_thread, code):
    import httpx
    from tbd_server.messaging.cards import enqueue, claim_card, record_card_result
    from tbd_server.messaging.adapter import Comms

    c = card_thread
    requests = []

    def provider(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(409, json={"code": code})
        return httpx.Response(
            200,
            json={
                "contact_card": {"name": "TBD", "phone": c["account"]},
                "message": {
                    "id": "fake-conflict-card-" + str(uuid4()),
                    "conversation_id": c["conversation"],
                    "line_e164": c["account"],
                    "direction": "outbound",
                    "channel": "imessage",
                    "body": "",
                    "status": "sent",
                },
            },
        )

    adapter = Comms("fake", transport=httpx.MockTransport(provider))
    try:
        with c["store"].transaction() as cur:
            enqueue(cur, c["account"], c["pairs"])
            first = claim_card(cur, c["account"], c["pairs"])
        result = adapter.send_card(first)
        with c["store"].transaction() as cur:
            record_card_result(cur, first, result)
            row = cur.execute("SELECT * FROM messaging.contact_cards WHERE id=%s", (first["id"],)).fetchone()
            assert claim_card(cur, c["account"], c["pairs"]) is None
            if code == "idempotency_key_reused":
                assert row["state"] == "failed" and row["error_code"] == code
                return
            assert row["state"] == "pending" and row["error_code"] == code and row["next_action_at"] > row["updated_at"]
            cur.execute("UPDATE messaging.contact_cards SET next_action_at=now() WHERE id=%s", (first["id"],))
            retry = claim_card(cur, c["account"], c["pairs"])
            assert retry["first_attempt_at"] == first["first_attempt_at"]
            assert (
                retry["idempotency_key"] == first["idempotency_key"]
                and retry["payload_sha256"] == first["payload_sha256"]
            )
        observed = adapter.send_card(retry)
        with c["store"].transaction() as cur:
            assert record_card_result(cur, retry, observed)
        assert requests[0].headers["Idempotency-Key"] == requests[1].headers["Idempotency-Key"]
        assert requests[0].content == requests[1].content
        assert observed.state == "sent"
    finally:
        adapter.close()


@pytest.mark.parametrize("import_a", [False, True])
def test_card_prerequisites_never_prevent_app_start_or_b_replies(card_thread, import_a):
    import hashlib
    import hmac
    import json
    import time
    from datetime import datetime, timezone
    from unittest.mock import Mock
    import httpx
    from fastapi.testclient import TestClient
    from tbd_server.messaging.cards import identity, PHOTO_SHA, import_verified
    from tbd_server.messaging.adapter import Comms, SendResult
    from tbd_server.messaging.service import Messaging
    from tbd_server.user.api import create_app
    from test_messaging_adapter import inbound

    c = card_thread
    if import_a:
        digest, key = identity(c["account"], c["conversation"])
        native = "fake-historical-import-" + str(uuid4())
        evidence = {
            "conversation_id": c["conversation"],
            "attempt_state": "accepted",
            "payload_sha256": digest,
            "idempotency_key": key,
            "photo_sha256": PHOTO_SHA,
            "provider_message_id": native,
        }
        with c["store"].transaction() as cur:
            import_verified(
                cur,
                c["account"],
                c["pairs"],
                evidence,
                {
                    "card_received_in_original_thread_confirmed": True,
                    "saved_contact_logo_visible_in_messages_confirmed": True,
                },
                SendResult("sent", native, observed_status="sent", response_body=""),
            )
    entry, auth_id = approved(c["store"])
    user = c["store"].join(auth_id, entry["email"])
    b_conversation = "fake-approved-b-" + str(uuid4())
    b_pair = {b_conversation: user["phone"]}
    service = Messaging(
        c["store"],
        "fake",
        "fake-secret",
        c["account"],
        "2026-11-08T00:00:00Z",
        pilot_conversations=b_pair,
        cards_enabled=True,
    )

    def provider(request):
        if request.method == "GET":
            return httpx.Response(200, json={"messages": []})
        if request.url.path.endswith("/contact-cards"):
            return httpx.Response(403, json={"code": "feature_disabled"})
        data = json.loads(request.content)
        return httpx.Response(
            202,
            json={
                "message": {
                    "id": "fake-live-b-reply-" + str(uuid4()),
                    "conversation_id": data["conversation_id"],
                    "line_e164": c["account"],
                    "direction": "outbound",
                    "channel": "imessage",
                    "body": data["body"],
                    "status": "sent",
                }
            },
        )

    service.adapter.close()
    service.adapter = Comms("fake", transport=httpx.MockTransport(provider))
    service.loop.adapter = service.adapter
    auth = Mock()
    auth.verified_identity.return_value = (auth_id, entry["email"])
    app = create_app(c["store"], auth, "fake-provision", "fake-internal", [], messaging=service)
    try:
        with TestClient(app) as client:
            assert client.get("/healthz").status_code == 200
            assert service.loop.card_pairs == (b_pair if import_a else None)
            assert client.post("/v1/join", headers={"Authorization": "Bearer fake"}).json()["id"] == str(user["id"])
            record = inbound(
                id="fake-prerequisite-b-" + str(uuid4()),
                conversation_id=b_conversation,
                line_e164=c["account"],
                author={"external_id": user["phone"]},
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            raw = json.dumps({"event": "comms.message.received", "data": {"message": record}}).encode()
            sig = "sha256=" + hmac.new(b"fake-secret", raw, hashlib.sha256).hexdigest()
            assert client.post("/webhooks/comms", content=raw, headers={"X-Osis-Signature": sig}).status_code == 200
            deadline = time.monotonic() + 8
            while True:
                with c["store"].transaction() as cur:
                    reply = cur.execute(
                        "SELECT * FROM messaging.messages WHERE user_id=%s AND direction='outbound'", (user["id"],)
                    ).fetchone()
                if reply and reply["state"] == "sent":
                    break
                assert time.monotonic() < deadline, "normal B reply stalled"
                time.sleep(0.02)
            assert reply["attempt_count"] == 1
            assert client.get("/healthz").status_code == 200
    finally:
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
            db.execute("DELETE FROM messaging.contact_cards WHERE user_id=%s", (user["id"],))


agent_pilot_numbers = count(0)


@pytest.fixture
def agent_pilot(store):
    from datetime import datetime, timedelta, timezone

    from tbd_server.agent.runner import AgentRunner, execution_config
    from tbd_server.agent.store import transition
    from tbd_server.messaging.core import ReceivedMessage, receive_message

    index = next(agent_pilot_numbers)
    account = f"+12125556{index:03d}"
    config = execution_config("synthetic-model")
    users = []
    for suffix in (index * 2, index * 2 + 1):
        phone = f"+12125557{suffix:03d}"
        identity, email = messaging_invitation(store, phone)
        users.append(store.join(identity, email))
    # Use a fixture-specific account so unrelated Messaging tests cannot claim
    # these delivery jobs even when the tests are shuffled.
    pairs = {"agent_a": users[0]["phone"], "agent_b": users[1]["phone"], "fixed_a": users[0]["phone"]}
    with store.transaction() as cur:
        for user, conversation in zip(users, ("agent_a", "agent_b")):
            transition(cur, user["id"], "agent", account, pairs, conversation)

    def event(text, conversation="agent_a", at=None):
        return ReceivedMessage(
            str(uuid4()), account, pairs[conversation], text, False, at or datetime.now(timezone.utc), conversation,
        )

    def intake(inbound):
        with store.transaction() as cur:
            return receive_message(cur, inbound, datetime.now(timezone.utc) - timedelta(days=1), agent_config=config)

    def result(inbound):
        with store.transaction() as cur:
            turn = cur.execute("SELECT * FROM agent_service.turns WHERE inbound_id=%s", (inbound["id"],)).fetchone()
            replies = cur.execute(
                "SELECT * FROM messaging.messages WHERE reply_to_message_id=%s", (inbound["id"],)
            ).fetchall()
            return turn, replies

    return store, account, users, pairs, AgentRunner(store.database_url, account, pairs, config), event, intake, result


def test_agent_db_duplicate_scope_isolation_and_local_controls(agent_pilot):
    from datetime import datetime, timedelta, timezone
    import json

    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.store import current_state
    from test_agent_harness import generated

    store, _, users, _, runner, event, intake, result = agent_pilot
    calls = []

    async def model(request, _timeout):
        prompt = request.context
        calls.append(json.loads(prompt))
        return Generation(generated(), {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})

    runner.adapter = model
    secret = intake(event("Password: synthetic-secret"))
    saved = intake(event("Remember size for shoes for me is 8 wide"))
    fixed = intake(event("Compare shoes", "fixed_a"))
    comparison = event("Compare these shoes for me")
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.map(intake, [comparison, comparison])
    comparison_b = intake(event("Compare these shoes for me", "agent_b"))
    assert a["id"] == b["id"]
    assert result(a)[1] == []
    assert result(fixed)[0] is None and len(result(fixed)[1]) == 1
    for _ in range(4):
        assert runner.step()
    assert [result(row)[0]["outcome"] for row in (secret, saved, a, comparison_b)] == [
        "blocked", "control", "model", "model",
    ]
    assert len(result(a)[1]) == 1
    assert len(calls) == 2
    assert calls[0]["preferences"][0]["value"] == "8 wide" and calls[1]["preferences"] == []
    assert "synthetic-secret" not in json.dumps(calls)
    late = intake(event("Stale comparison", at=a["source_at"] - timedelta(microseconds=1)))
    assert runner.step()
    assert result(late)[0]["outcome"] == "stale"
    forget = intake(event("Forget brand for coats for me; compare these coats", at=datetime.now(timezone.utc)))
    with store.transaction() as cur:
        cur.execute(
            "UPDATE agent_service.turns SET inference_deadline=clock_timestamp()-interval '1s' WHERE inbound_id=%s",
            (forget["id"],),
        )
    assert runner.step()
    assert result(forget)[0]["outcome"] == "control"
    assert "resend" in result(forget)[1][0]["text"]
    with store.transaction() as cur:
        active = current_state(cur, users[0]["id"])
        assert active["reset_sequence"] == forget["queue_sequence"]
        assert active["reset_source_at"] == forget["source_at"]
        assert active["brief"] is None


def test_agent_db_lock_wait_reads_new_mode_and_replays_original_producer(agent_pilot):
    from threading import Event
    from time import monotonic, sleep

    from tbd_server.agent.store import lock_user, transition

    store, account, users, pairs, runner, event, intake, result = agent_pilot
    previous_event = event("Compare shoes")
    previous = intake(previous_event)
    assert runner.step()
    started = Event()

    def wait_for_user():
        started.set()
        return intake(new_event)

    new_event = event("Compare jackets")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.transaction() as cur:
            lock_user(cur, users[0]["id"])
            pending = pool.submit(wait_for_user)
            assert started.wait(1)
            deadline = monotonic() + 3
            while True:
                cur.execute("SELECT pg_stat_clear_snapshot()")
                if cur.execute(
                    """SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock'
                    AND query LIKE 'SELECT u.* FROM user_service.users u WHERE u.phone=%%'"""
                ).fetchone():
                    break
                assert monotonic() < deadline, "Intake did not wait on the canonical User lock"
                sleep(0.01)
            transition(cur, users[0]["id"], "fallback", account, pairs)
            transition(cur, users[0]["id"], "fixed", account, pairs)
        fixed = pending.result(timeout=3)
    assert result(fixed)[0] is None
    assert result(fixed)[1][0]["text"] == "Received your text."
    assert intake(previous_event)["replayed"]
    assert result(previous)[0]["outcome"] == "fallback"
    assert len(result(previous)[1]) == 1


def test_agent_db_expired_grant_and_uncertain_commit(agent_pilot):
    from contextlib import contextmanager

    from tbd_server.agent.adapter import Generation
    from test_agent_harness import generated

    store, _, _, _, runner, event, intake, result = agent_pilot
    inbound = intake(event("Compare these shoes"))
    calls = []

    async def expire(*_):
        calls.append("attempt")
        with store.transaction() as cur:
            cur.execute(
                """UPDATE agent_service.turns SET inference_deadline=clock_timestamp()-interval '1s',
                lease_until=clock_timestamp()-interval '1s' WHERE inbound_id=%s""",
                (inbound["id"],),
            )
        return Generation(generated())

    runner.adapter = expire
    assert runner.step()
    assert result(inbound)[1] == []
    assert runner.step()
    assert result(inbound)[0]["outcome"] == "fallback"
    assert calls == ["attempt"]

    second = intake(event("Compare these jackets"))
    transaction = runner.transaction
    committed = False

    @contextmanager
    def uncertain():
        nonlocal committed
        with transaction() as cur:
            yield cur
        if not committed and result(second)[0]["outcome"] == "model":
            committed = True
            raise psycopg.OperationalError("synthetic lost commit response")

    async def model(*_):
        calls.append("second")
        runner.transaction = uncertain
        return Generation(generated())

    runner.adapter = model
    assert runner.step()
    assert committed and result(second)[0]["outcome"] == "model"
    assert len(result(second)[1]) == 1
    assert not runner.step()
    assert calls == ["attempt", "second"]


def test_agent_db_delivery_revokes_during_backoff_and_preserves_late_evidence(agent_pilot):
    from tbd_server.agent.delivery import authorize_post, suppress_revoked
    from tbd_server.messaging.adapter import SendResult
    from tbd_server.messaging.runtime import claim, record_send_result

    store, account, _, pairs, runner, event, intake, result = agent_pilot
    first = intake(event("Compare these shoes"))
    delayed = intake(event("Compare these jackets", "agent_b"))
    assert runner.step() and runner.step()
    with store.transaction() as cur:
        sending = claim(cur, account)
    assert sending["reply_to_message_id"] == first["id"]
    with store.transaction() as cur:
        assert authorize_post(cur, sending, account, pairs)
        cur.execute(
            """UPDATE messaging.messages SET next_action_at=clock_timestamp()+interval '1h',error_code='quota'
            WHERE reply_to_message_id=%s""",
            (delayed["id"],),
        )
    with store.transaction() as cur:
        suppress_revoked(cur, account, {})
    assert result(first)[1][0]["state"] == "uncertain"
    assert result(delayed)[1][0]["state"] == "failed"
    assert result(delayed)[1][0]["error_code"] == "quota"
    with store.transaction() as cur:
        assert authorize_post(cur, sending, account, pairs) is None
        assert record_send_result(
            cur, sending["id"], sending["attempt_id"], SendResult("sent", "synthetic-exact-id", observed_status="sent"),
            agent_guard=True,
        )
    assert result(first)[1][0]["state"] == "sent"


def test_agent_db_claim_expiry_and_guarded_drain(agent_pilot):
    from tbd_server.agent.delivery import authorize_post
    from tbd_server.agent.store import transition
    from tbd_server.messaging.runtime import claim

    store, account, users, pairs, runner, event, intake, result = agent_pilot
    inbound = intake(event("Compare these shoes"))
    with pytest.raises(Problem, match="backlog"):
        with store.transaction() as cur:
            transition(cur, users[0]["id"], "fallback", account, pairs)
            transition(cur, users[0]["id"], "fixed", account, pairs)
    with store.transaction() as cur:
        transition(cur, users[0]["id"], "fallback", account, pairs)
    assert runner.step()
    with store.transaction() as cur:
        transition(cur, users[0]["id"], "fixed", account, pairs)
        sending = claim(cur, account)
        cur.execute(
            "UPDATE messaging.messages SET first_attempt_at=clock_timestamp()-interval '25h' WHERE id=%s",
            (sending["id"],),
        )
    with store.transaction() as cur:
        assert authorize_post(cur, sending, account, pairs) is None
    assert result(inbound)[1][0]["state"] == "failed"


def test_agent_db_private_schema_permissions(store):
    for role in ("anon", "authenticated"):
        for table in ("agent_service.user_state", "agent_service.turns"):
            with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
                db.execute(psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(role)))
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    db.execute(psycopg.sql.SQL("SELECT * FROM {}").format(psycopg.sql.Identifier(*table.split("."))))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with store.transaction() as cur:
            cur.execute("DELETE FROM agent_service.turns WHERE false")


def test_agent_db_hung_inference_does_not_stall_join_webhook_or_delivery(agent_pilot):
    import asyncio
    from datetime import datetime, timezone
    import hashlib
    import hmac
    import json
    from threading import Event
    from time import monotonic, sleep
    from unittest.mock import Mock

    import httpx
    from fastapi.testclient import TestClient

    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.store import transition
    from tbd_server.messaging.adapter import Comms
    from tbd_server.messaging.service import Messaging
    from tbd_server.user.api import create_app
    from test_agent_harness import generated
    from test_messaging_adapter import inbound

    store, account, users, pairs, _, _, _, _ = agent_pilot
    identity, email = messaging_invitation(store, "+12125557991")
    auth = Mock()
    auth.verified_identity.return_value = (identity, email)
    entered, release, fixed_sent = Event(), Event(), Event()
    service = Messaging(
        store, "fake", "fake-secret", account, "2026-11-08T00:00:00Z", pilot_conversations=pairs,
        agent_model="synthetic-model",
    )

    async def hung(*_):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return Generation(generated(reply="Stale model reply."))

    def provider(request):
        if request.method == "GET":
            return httpx.Response(200, json={"messages": []})
        data = json.loads(request.content)
        if data["conversation_id"] == "fixed_a":
            fixed_sent.set()
        return httpx.Response(202, json={"message": {
            "id": str(uuid4()), "conversation_id": data["conversation_id"], "line_e164": account,
            "direction": "outbound", "channel": "imessage", "body": data["body"], "status": "sent",
        }})

    service.runner.adapter = hung
    service.adapter.close()
    service.adapter = service.loop.adapter = Comms("fake", transport=httpx.MockTransport(provider))

    def webhook(client, conversation):
        message = inbound(
            id=str(uuid4()), conversation_id=conversation, line_e164=account,
            author={"external_id": pairs[conversation]}, created_at=datetime.now(timezone.utc).isoformat(),
        )
        raw = json.dumps({"event": "comms.message.received", "data": {"message": message}}).encode()
        sig = "sha256=" + hmac.new(b"fake-secret", raw, hashlib.sha256).hexdigest()
        assert client.post("/webhooks/comms", content=raw, headers={"X-Osis-Signature": sig}).status_code == 200

    with TestClient(create_app(store, auth, "fake-provision", "fake-internal", [], messaging=service)) as client:
        try:
            webhook(client, "agent_a")
            assert entered.wait(4)
            assert client.post("/v1/join", headers={"Authorization": "Bearer fake"}).status_code == 200
            webhook(client, "fixed_a")
            assert client.get("/healthz").status_code == 200
            assert fixed_sent.wait(4)
            with store.transaction() as cur:
                transition(cur, users[0]["id"], "fallback", account, pairs)
        finally:
            release.set()
        deadline = monotonic() + 4
        while monotonic() < deadline:
            with store.transaction() as cur:
                row = cur.execute(
                    """SELECT t.outcome FROM agent_service.turns t JOIN messaging.messages m
                    ON m.id=t.inbound_id WHERE m.user_id=%s""",
                    (users[0]["id"],),
                ).fetchone()
            if row["outcome"] is not None:
                break
            sleep(0.02)
        assert row["outcome"] == "fallback"
        with store.transaction() as cur:
            transition(cur, users[0]["id"], "fixed", account, pairs)


COMMERCE_V1_CASES = (
    ("saved", "Remember condition for shoes for me is new"),
    ("recommendation", "Compare shoes for me: Aspen is new for $80, Birch is used for $70. My budget is $90."),
    ("preference_reuse", "Between the same two shoes above, which fits my saved condition preference?"),
    ("no_fit", "My new maximum budget is $50. Does either of those same shoes fit?"),
    ("follow_up", "Switch to a gift of shoes for my sister. Maple is size 8. Oak is size 10. Which fits?"),
    ("blocked", "CVV: 123. Remember condition for shoes for me is used"),
)


def commerce_quality(label, reply, question):
    import re

    if label == "no_fit":
        return {"no_fit": bool(re.search(r"\b(?:neither|none|both.{0,40}(?:over|exceed))\b", reply, re.I))}
    if label == "follow_up":
        asks_size = re.search(
            r"\b(?:what|which|do you know|can you share|could you tell me)\b[^?.!]*\bsize\b[^?.!]*\?",
            reply, re.I,
        )
        return {
            "gift_clarification": bool(asks_size),
            "gift_stateful_question": bool(question and question["field"] == "size"),
        }
    selects_aspen = re.search(
        r"(?:choose|recommend|pick|go with|select)\b.{0,36}\bAspen\b|"
        r"\bAspen\b.{0,50}\b(?:fits|best|recommend|better|choice|pick)\b", reply, re.I,
    )
    return {label: bool(selects_aspen) and (label != "preference_reuse" or "new" in reply.lower())}


def test_commerce_eval_scores_unbound_gift_question():
    reply = "What size should I use for this request? Please include the item and who it’s for."
    assert commerce_quality("follow_up", reply, None) == {
        "gift_clarification": True, "gift_stateful_question": False,
    }
    assert commerce_quality("follow_up", reply, {"field": "size"}) == {
        "gift_clarification": True, "gift_stateful_question": True,
    }
    assert not commerce_quality("follow_up", "Maple is size 8.", None)["gift_clarification"]


def commerce_output_structure(output):
    from pydantic import ValidationError

    from tbd_server.agent.harness import Result

    if output is None:
        return None
    try:
        result = Result.model_validate_json(output)
    except ValidationError:
        return None
    return {
        "brief": result.brief,
        "edit_fields": [edit.field for edit in result.edits],
        "question_is_null": result.question is None,
    }


def test_commerce_no_fit_budget_only_update_and_reply_only():
    from tbd_server.agent import harness
    from test_agent_harness import generated, turn

    inbound = turn(dict(COMMERCE_V1_CASES)["no_fit"])
    reply = "Neither pair fits your $50 budget."
    budget_only = generated(reply=reply, brief="update", edits=[{"field": "budget", "value": "$50"}])
    assert commerce_output_structure(budget_only) == {
        "brief": "update", "edit_fields": ["budget"], "question_is_null": True,
    }
    with pytest.raises(harness.BriefValidationError, match="Incomplete"):
        harness.validate(budget_only, inbound, None, None)

    reply_only = generated(reply=reply)
    assert commerce_output_structure(reply_only) == {
        "brief": "unchanged", "edit_fields": [], "question_is_null": True,
    }
    assert harness.validate(reply_only, inbound, None, None) == (reply, None, None)
    assert commerce_quality("no_fit", reply, None)["no_fit"]
    assert commerce_output_structure(generated(reply=reply, question={"field": "budget", "value": None})) == {
        "brief": "unchanged", "edit_fields": [], "question_is_null": False,
    }


def test_commerce_gift_recipient_preserves_authorized_wording_and_scope():
    from tbd_server.agent import harness
    from test_agent_harness import generated, turn

    inbound = turn(dict(COMMERCE_V1_CASES)["follow_up"])
    own = {"id": "personal", "fields": {"item": {"value": "shoes"}, "size": {"value": "8"}}, "options": []}
    question = {"field": "size", "value": None}

    def replacement(recipient):
        return generated(brief="replace", edits=[
            {"field": "item", "value": "shoes"}, {"field": "recipient", "value": recipient},
        ], question=question)

    with pytest.raises(harness.BriefEvidenceError) as error:
        harness.validate(replacement("sister"), inbound, own, None)
    assert error.value.field == "recipient"
    _, gift, target = harness.validate(replacement("my sister"), inbound, own, None)
    assert gift["fields"]["recipient"]["value"] == "my sister"
    assert "size" not in gift["fields"] and target["brief_id"] != own["id"]

    reply, unchanged, target = harness.validate(generated(question=question), inbound, own, None)
    assert commerce_quality("follow_up", reply, target)["gift_clarification"]
    assert unchanged == own and own["fields"]["size"]["value"] == "8"
    assert target is None


def test_agent_db_repeated_model_eval(agent_pilot, monkeypatch):
    """Opt-in paid eval on the same real runner and versioned synthetic turns."""
    from collections import Counter
    from decimal import Decimal
    import json
    from statistics import mean
    import subprocess
    from time import monotonic

    from tbd_server.agent import runner as agent_runtime
    from tbd_server.agent.adapter import OpenAI
    from tbd_server.agent.harness import SECRET
    from tbd_server.agent.store import current_state, transition
    from tbd_server.messaging.core import receive_message

    model = os.getenv("TBD_AGENT_MODEL")
    key = os.getenv("TBD_AGENT_OPENAI_API_KEY")
    if os.getenv("TBD_RUN_AGENT_EVAL") != "1" or not model or not key:
        pytest.skip("TBD_RUN_AGENT_EVAL=1 and TBD-approved model and credentials required")

    store, account, users, pairs, runner, event, _, result = agent_pilot
    runner.config = agent_runtime.execution_config(model)
    provider = OpenAI(key)
    records = []
    no_fit_outputs = []
    label = None

    async def observe(*args):
        generation = await provider(*args)
        if label == "no_fit":
            no_fit_outputs.append(commerce_output_structure(generation.output))
        return generation

    runner.adapter = observe
    emit = agent_runtime.emit

    def collect(name, **fields):
        if name in ("agent_inference", "agent_completion", "agent_local_completion"):
            records.append({
                "event": name,
                **{k: v for k, v in fields.items() if k.endswith("_ms") or k in ("reason", "usage", "brief_field")},
            })
        emit(name, **fields)

    monkeypatch.setattr(agent_runtime, "emit", collect)

    metrics = dict(recommendation=0, preference_reuse=0, no_fit=0, gift_clarification=0,
                   gift_stateful_question=0, privacy_or_state_failures=0)
    cases = []
    repeats = 3
    for index in range(repeats):
        inbounds = []
        for label, body in COMMERCE_V1_CASES:
            records.clear()
            no_fit_outputs.clear()
            arrived = monotonic()
            with store.transaction() as cur:
                inbound = receive_message(
                    cur, event(body), current_state(cur, users[0]["id"])["enrolled_at"], agent_config=runner.config,
                )
            start = monotonic()
            while result(inbound)[0]["outcome"] is None:
                runner.step()
                assert monotonic() - start < 30
            finished = monotonic()
            turn, replies = result(inbound)
            assert len(replies) == 1
            outcome = turn["outcome"]
            reply = replies[0]["text"]
            attempts = [r for r in records if r["event"] == "agent_inference"]
            completion = next((r for r in reversed(records) if r["event"] != "agent_inference"), {})
            quality = {}
            if label in ("saved", "blocked"):
                expected = "control" if label == "saved" else "blocked"
                if outcome != expected or attempts or SECRET.search(reply):
                    metrics["privacy_or_state_failures"] += 1
            elif outcome == "model":
                quality = commerce_quality(label, reply, turn["question"])
                for metric, passed in quality.items():
                    metrics[metric] += passed
            case = {
                "repeat": index + 1, "case": label, "outcome": outcome, "quality": quality,
                "fallback_reason": (completion.get("reason") or "unobserved") if outcome == "fallback" else None,
                "intake_ms": round((start - arrived) * 1000, 2),
                "turn_ms": round((finished - start) * 1000, 2),
                "completion": completion, "attempts": attempts,
            }
            if label == "no_fit":
                case["output_structure"] = list(no_fit_outputs)
            if label == "follow_up":
                case["rendered_reply"] = reply
            cases.append(case)
            inbounds.append(inbound)
        if index < repeats - 1:
            # Settle the replies before re-enrollment. Keeping delivery at the
            # repeat boundary preserves the original within-repeat context.
            for inbound in inbounds:
                observe_agent_reply_sent(store, account, pairs, inbound, result)
        with store.transaction() as cur:
            saved = current_state(cur, users[0]["id"])["preferences"]
            metrics["privacy_or_state_failures"] += int(saved["shoes|me|condition"]["value"] != "new")
            if index < repeats - 1:
                transition(cur, users[0]["id"], "fallback", account, pairs)
                transition(cur, users[0]["id"], "fixed", account, pairs)
                transition(cur, users[0]["id"], "agent", account, pairs, "agent_a")
    attempts = [a for case in cases for a in case["attempts"]]
    usage = [a["usage"] for a in attempts if a["usage"] is not None]
    useful_recommendations = metrics["recommendation"] + metrics["preference_reuse"]
    useful_replies = useful_recommendations + metrics["no_fit"] + metrics["gift_clarification"]
    spend = None
    if model == "gpt-5.4-mini-2026-03-17":
        # Standard list rates for the approved snapshot. Actual billed spend
        # depends on the account. Incomplete usage leaves spend unreconciled.
        priced = [
            u for u in usage if {"input_tokens", "cached_tokens", "output_tokens"} <= u.keys()
            and u["cached_tokens"] <= u["input_tokens"]
        ]
        known_cost = sum((
            Decimal("0.75") * (u["input_tokens"] - u["cached_tokens"])
            + Decimal("0.075") * u["cached_tokens"] + Decimal("4.50") * u["output_tokens"]
            for u in priced
        ), Decimal(0)) / 1_000_000
        complete = len(priced) == len(attempts)
        spend = {
            "known_estimate_usd": float(known_cost) if priced else None,
            "unpriced_attempts": len(attempts) - len(priced),
            "estimate_per_useful_reply_usd": float(known_cost / useful_replies) if complete and useful_replies else None,
        }
    print(json.dumps({
        "suite": "commerce-v1", "model": model, "repeats": repeats,
        "sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "scores": metrics, "cases": cases, "useful_replies": useful_replies,
        "useful_recommendation_rate": round(useful_recommendations / (2 * repeats), 3),
        "avg_turn_ms": round(mean(case["turn_ms"] for case in cases), 2),
        "fallback_reasons": dict(Counter(case["fallback_reason"] for case in cases if case["fallback_reason"])),
        "attempt_count": len(attempts), "missing_usage_attempts": len(attempts) - len(usage),
        "usage": {
            key: sum(reported) if (reported := [u[key] for u in usage if key in u]) else None
            for key in ("input_tokens", "cached_tokens", "output_tokens", "total_tokens")
        },
        "spend": spend,
    }))
    assert not metrics["privacy_or_state_failures"]


def test_agent_db_unseen_question_cannot_edit_brief(agent_pilot):
    import json

    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.store import current_state
    from test_agent_harness import generated

    store, _, users, _, runner, event, intake, result = agent_pilot
    first = intake(event("Compare these shoes"))
    heard = []

    async def model(request, _timeout):
        prompt = request.context
        heard.append(json.loads(prompt))
        return Generation(generated(
            brief="update", edits=[{"field": "item", "value": "shoes"}],
            question={"field": "size", "value": None},
        ))

    runner.adapter = model
    assert runner.step()
    question, replies = result(first)
    assert question["question"]["field"] == "size"
    assert replies[0]["sent_observed_at"] is None
    answer = intake(event("9"))
    assert runner.step()
    assert len(heard) == 1
    assert result(answer)[0]["outcome"] == "fallback"
    with store.transaction() as cur:
        brief = current_state(cur, users[0]["id"])["brief"]
        assert "size" not in brief["fields"]


def test_agent_db_unsupported_edits_and_false_confirmations_keep_existing_brief(agent_pilot):
    from tbd_server.agent import harness
    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.store import current_state
    from test_agent_harness import generated

    store, _, users, _, runner, event, intake, result = agent_pilot
    outputs = iter([
        generated(brief="update", edits=[
            {"field": "item", "value": "shoes"},
            {"field": "size", "value": "9"},
            {"field": "budget", "value": "$72"},
        ]),
        generated(brief="update", edits=[{"field": "budget", "value": "$80"}]),
        generated(brief="update", edits=[{"field": "size", "value": "8"}]),
        generated(reply="I placed the order for you."),
    ])

    async def model(*_):
        return Generation(next(outputs))

    def brief():
        with store.transaction() as cur:
            return current_state(cur, users[0]["id"])["brief"]

    runner.adapter = model
    opening = intake(event("Compare shoes. My size is 9. My budget is $72."))
    assert runner.step() and result(opening)[0]["outcome"] == "model"
    before = brief()
    cases = (
        ("My budget is $65, but the seller asks $80.", "fallback", harness.CLARIFY_BRIEF),
        ("The listing is size 8 but I need size 10.", "fallback", harness.CLARIFY_BRIEF),
        ("Which option fits?", "fallback", harness.FALLBACK),
        ("Place the order for me.", "transaction", harness.TRANSACTION),
    )
    for text, outcome, reply in cases:
        inbound = intake(event(text))
        assert runner.step()
        turn, replies = result(inbound)
        assert turn["outcome"] == outcome
        assert len(replies) == 1 and replies[0]["text"] == reply
        assert brief() == before


def test_agent_db_retracted_forget_preserves_memory_and_gift_scope(agent_pilot):
    import json

    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.store import current_state
    from test_agent_harness import generated

    store, _, users, _, runner, event, intake, result = agent_pilot
    prompts = []

    async def model(request, _timeout):
        prompt = request.context
        prompts.append(json.loads(prompt))
        if len(prompts) == 1:
            return Generation(generated(brief="update", edits=[{"field": "item", "value": "shoes"}]))
        return Generation(generated())

    def get_state():
        with store.transaction() as cur:
            return current_state(cur, users[0]["id"])

    runner.adapter = model
    for text in (
        "Forget brand for shoes for me",
        "Remember size for shoes for me is 8",
        "Compare these shoes",
    ):
        intake(event(text))
        assert runner.step()
    assert prompts[0]["preferences"][0]["value"] == "8"
    before = get_state()
    retracted = intake(event("Forget size for shoes for me; actually, keep that preference"))
    assert runner.step()
    turn, replies = result(retracted)
    assert turn["outcome"] == "control"
    assert len(replies) == 1 and "confirm" in replies[0]["text"]
    after = get_state()
    for field in ("preferences", "brief", "reset_source_at", "reset_sequence"):
        assert after[field] == before[field]
    assert len(prompts) == 1

    gift = intake(event("Compare these shoes for a friend"))
    assert runner.step()
    assert result(gift)[0]["outcome"] == "model"
    assert prompts[1]["preferences"] == [] and prompts[1]["brief"] is None

    clear = intake(event("Forget size for shoes for me; compare these shoes"))
    assert runner.step()
    assert "resend" in result(clear)[1][0]["text"]
    after = get_state()
    assert after["preferences"] == {} and after["brief"] is None
    assert after["reset_sequence"] == clear["queue_sequence"]
    assert after["reset_source_at"] == clear["source_at"]
    assert len(prompts) == 2


@pytest.mark.parametrize("legacy", [False, True])
def test_agent_db_local_watermark_cannot_revive_expired_brief(agent_pilot, legacy):
    from datetime import timedelta
    import json

    from psycopg.types.json import Jsonb

    from tbd_server.agent import harness
    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.store import current_state
    from test_agent_harness import generated

    store, _, users, _, runner, event, intake, result = agent_pilot
    prompts = []

    async def model(request, _timeout):
        prompt = request.context
        prompts.append(json.loads(prompt))
        if len(prompts) == 1:
            return Generation(generated(brief="update", edits=[{"field": "item", "value": "shoes"}]))
        return Generation(generated())

    runner.adapter = model
    opening = intake(event("Compare shoes"))
    assert runner.step()
    expired_at = opening["source_at"] - harness.BRIEF_AGE - timedelta(seconds=1)
    with store.transaction() as cur:
        old_brief = current_state(cur, users[0]["id"])["brief"]
        if legacy:
            del old_brief["activity_at"]
        else:
            old_brief["activity_at"] = expired_at.isoformat()
        cur.execute("UPDATE agent_service.user_state SET brief=%s WHERE user_id=%s", (Jsonb(old_brief), users[0]["id"]))
    if legacy:
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
            db.execute("UPDATE messaging.messages SET source_at=%s WHERE id=%s", (expired_at, opening["id"]))

    inspect = intake(event("Show my preferences"))
    assert runner.step() and result(inspect)[0]["outcome"] == "control"
    with store.transaction() as cur:
        after = current_state(cur, users[0]["id"])
        assert after["watermark"] == inspect["source_at"] and after["brief"] == old_brief
    follow_up = intake(event("Can you compare shoes?"))
    assert runner.step() and result(follow_up)[0]["outcome"] == "model"
    assert prompts[1]["brief"] is None


def observe_agent_reply_sent(store, account, pairs, inbound, result):
    from tbd_server.agent.delivery import authorize_post
    from tbd_server.messaging.adapter import SendResult
    from tbd_server.messaging.runtime import claim, record_send_result

    with store.transaction() as cur:
        job = claim(cur, account)
        assert job["reply_to_message_id"] == inbound["id"]
    with store.transaction() as cur:
        assert authorize_post(cur, job, account, pairs) is not None
    with store.transaction() as cur:
        assert record_send_result(
            cur, job["id"], job["attempt_id"],
            SendResult("sent", str(uuid4()), observed_status="sent"), agent_guard=True,
        )
    return result(inbound)[1][0]

def test_agent_db_observed_gift_question_cannot_change_personal_brief(agent_pilot):
    from datetime import datetime, timezone
    import json
    from time import sleep

    from tbd_server.agent import harness
    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.store import current_state
    from test_agent_harness import generated

    store, account, users, pairs, runner, event, intake, result = agent_pilot
    prompts = []
    outputs = iter([
        generated(brief="update", edits=[{"field": "item", "value": "shoes"}, {"field": "size", "value": "8"}]),
        generated(question={"field": "size", "value": None}),
        generated(brief="update", edits=[{"field": "size", "value": "10"}]),
    ])

    async def model(request, _timeout):
        prompt = request.context
        prompts.append(json.loads(prompt))
        return Generation(next(outputs))

    runner.adapter = model
    own = intake(event("Compare shoes for me. I need size 8."))
    assert runner.step() and result(own)[0]["outcome"] == "model"
    observe_agent_reply_sent(store, account, pairs, own, result)
    with store.transaction() as cur:
        before = current_state(cur, users[0]["id"])["brief"]
    assert before["fields"]["size"]["value"] == "8"

    gift = intake(event("Compare these shoes for a friend"))
    assert runner.step()
    assert prompts[1]["brief"] is None
    observed = observe_agent_reply_sent(store, account, pairs, gift, result)
    assert "What size" in observed["text"]
    assert observed["sent_observed_at"] is not None
    sleep(harness.SKEW.total_seconds())
    answer = intake(event("10", at=datetime.now(timezone.utc)))
    assert harness.visible(observed, answer)
    assert runner.step()

    with store.transaction() as cur:
        assert current_state(cur, users[0]["id"])["brief"] is None
    assert result(gift)[0]["question"] is None
    assert result(answer)[1][0]["text"] == harness.RESTATEMENT
    assert len(prompts) == 2


@pytest.mark.parametrize("bind_question", [False, True])
def test_agent_db_gift_size_required_until_recipient_size_is_known(agent_pilot, bind_question):
    import json
    from time import sleep

    from tbd_server.agent import harness
    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.store import current_state
    from test_agent_harness import generated

    store, account, users, pairs, runner, event, intake, result = agent_pilot
    prompts = []
    gift_edits = [
        {"field": "item", "value": "shoes"}, {"field": "recipient", "value": "my sister"},
        {"field": "option", "value": "Maple is size 8"}, {"field": "option", "value": "Oak is size 10"},
    ]
    outputs = iter([
        generated(brief="update", edits=[{"field": "item", "value": "shoes"}, {"field": "size", "value": "8"}]),
        generated(
            reply="I would pick Maple because it fits your size.",
            brief="replace" if bind_question else "unchanged", edits=gift_edits if bind_question else [],
        ),
        generated(
            reply="I would pick Oak because it matches her size 10.",
            brief="update" if bind_question else "unchanged",
            edits=[{"field": "size", "value": "10"}] if bind_question else [],
        ),
    ])

    async def model(request, _timeout):
        prompt = request.context
        prompts.append(json.loads(prompt))
        return Generation(next(outputs))

    runner.adapter = model
    own = intake(event("Compare shoes for me. I need size 8."))
    assert runner.step() and result(own)[0]["outcome"] == "model"
    if bind_question:
        observe_agent_reply_sent(store, account, pairs, own, result)
    with store.transaction() as cur:
        personal = current_state(cur, users[0]["id"])["brief"]

    gift = intake(event(dict(COMMERCE_V1_CASES)["follow_up"]))
    assert runner.step()
    assert prompts[1]["brief"] is None and prompts[1]["preferences"] == []
    assert prompts[1]["required_question"] == {"field": "size", "value": None}
    turn, replies = result(gift)
    assert turn["outcome"] == "model" and len(replies) == 1
    assert replies[0]["text"].startswith("What size does the recipient wear?")
    assert "Maple" not in replies[0]["text"] and "Oak" not in replies[0]["text"]
    with store.transaction() as cur:
        current_brief = current_state(cur, users[0]["id"])["brief"]
    if bind_question:
        assert turn["question"] == {"brief_id": current_brief["id"], "field": "size", "value": None}
        assert current_brief["id"] != personal["id"]
        assert current_brief["fields"]["recipient"]["value"] == "my sister"
        observe_agent_reply_sent(store, account, pairs, gift, result)
        sleep(harness.SKEW.total_seconds())
        supplied = "10"
    else:
        assert turn["question"] is None and current_brief is None
        supplied = "Compare shoes for my sister. My sister wears size 10. Maple is size 8. Oak is size 10. Which fits?"

    answer = intake(event(supplied))
    assert runner.step()
    assert prompts[2]["required_question"] is None
    if bind_question:
        assert prompts[2]["typed_answer"] == {"size": "10"}
        assert prompts[2]["brief"]["options"] == [
            {"value": edit["value"], "source": str(gift["id"])} for edit in gift_edits if edit["field"] == "option"
        ]
    else:
        assert prompts[2]["current_constraints"]["size"] == "10"
        assert prompts[2]["brief"] is None
        assert prompts[2]["recent"] == []
    turn, replies = result(answer)
    assert turn["outcome"] == "model" and turn["question"] is None
    assert replies[0]["text"] == "I would pick Oak because it matches her size 10."
    with store.transaction() as cur:
        after = current_state(cur, users[0]["id"])["brief"]
    if bind_question:
        assert after["fields"]["size"]["value"] == "10"
    else:
        assert after is None


@pytest.mark.parametrize("text,reply,recipient_size", [
    (
        "Switch to a gift of shoes for my sister. She wears a size 10. "
        "Maple is size 8. Oak is size 10. Which fits?",
        "Oak matches your sister’s size 10.",
        "10",
    ),
    (
        "Switch to a gift of shoes for my sister. Maple is size 8 with a relaxed fit and costs $80. "
        "Oak is size 10 and costs $65. Which is cheaper?",
        "Oak is cheaper at $65.",
        None,
    ),
])
def test_agent_db_gift_comparison_answers_requested_criterion(agent_pilot, text, reply, recipient_size):
    import json

    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.store import current_state
    from test_agent_harness import generated

    store, _, users, _, runner, event, intake, result = agent_pilot
    prompts = []

    async def model(request, _timeout):
        prompt = request.context
        prompts.append(json.loads(prompt))
        return Generation(generated(reply=reply))

    runner.adapter = model
    inbound = intake(event(text))
    assert runner.step()
    assert len(prompts) == 1
    assert prompts[0]["required_question"] is None
    assert prompts[0]["recipient_size_for_reply"] == recipient_size
    assert "size" not in prompts[0]["current_constraints"]
    turn, replies = result(inbound)
    assert turn["outcome"] == "model" and turn["question"] is None
    assert len(replies) == 1 and replies[0]["text"] == reply
    with store.transaction() as cur:
        assert current_state(cur, users[0]["id"])["brief"] is None


@pytest.mark.parametrize("response,expected", [
    ({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, "provider_incomplete_max_output_tokens"),
    ({"status": "completed", "output": None}, "provider_unreadable"),
    ("{", "output_invalid_json"),
    ('{"reply": "I would go with Aspen."}', "schema_validation"),
    ("unsubstantiated_budget", "brief_evidence"),
    ("unexpected_edit", "brief_validation"),
    ("derived_condition", "brief_evidence"),
    ("rewritten_option", "brief_evidence"),
    ("reply_only", None),
    ("valid", None),
])
def test_agent_db_recommendation_diagnostics(agent_pilot, monkeypatch, response, expected):
    import json

    import httpx

    from tbd_server.agent import runner as agent_runtime
    from tbd_server.agent.adapter import OpenAI
    from tbd_server.agent.store import current_state
    from test_agent_harness import generated

    store, _, users, _, runner, event, intake, result = agent_pilot
    records = {}
    payloads = []
    real_client = httpx.AsyncClient
    usage = {
        "input_tokens": 500, "output_tokens": 82, "total_tokens": 582,
        "input_tokens_details": {"cached_tokens": 0},
    }
    reply = "I would go with Aspen. It’s new and within your budget."
    model_results = {
        "derived_condition": generated(reply=reply, brief="update", edits=[{"field": "condition", "value": "new"}]),
        "rewritten_option": generated(reply=reply, brief="update", edits=[{"field": "option", "value": "Aspen: $80, new"}]),
        "reply_only": generated(reply=reply),
        "unsubstantiated_budget": generated(brief="update", edits=[{"field": "budget", "value": "$80"}]),
        "unexpected_edit": generated(brief="unchanged", edits=[{"field": "budget", "value": "$90"}]),
        "valid": generated(
            reply=reply, brief="update",
            edits=[
                {"field": "item", "value": "shoes"},
                {"field": "budget", "value": "$90"},
                {"field": "option", "value": "Aspen is new for $80"},
            ],
        ),
    }

    def provider(request):
        payloads.append(json.loads(json.loads(request.content)["input"]))
        if isinstance(response, dict):
            envelope = {**response, "usage": usage}
        else:
            envelope = {
                "status": "completed", "usage": usage,
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": model_results.get(response, response)},
                ]}],
            }
        return httpx.Response(200, json=envelope)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: real_client(transport=httpx.MockTransport(provider)))
    monkeypatch.setattr(agent_runtime, "emit", lambda name, **fields: records.update({name: fields}))
    runner.adapter = OpenAI("synthetic")

    saved = intake(event(COMMERCE_V1_CASES[0][1]))
    assert runner.step() and result(saved)[0]["outcome"] == "control"
    recommendation = intake(event(COMMERCE_V1_CASES[1][1]))
    assert runner.step()
    turn, replies = result(recommendation)
    assert records["agent_completion"]["reason"] == expected
    rejected_field = {"unsubstantiated_budget": "budget", "derived_condition": "condition", "rewritten_option": "option"}
    assert records["agent_completion"]["brief_field"] == (rejected_field.get(response) if isinstance(response, str) else None)
    assert records["agent_inference"]["usage"] == {
        "input_tokens": 500, "output_tokens": 82, "total_tokens": 582, "cached_tokens": 0,
    }
    assert len(payloads) == len(replies) == 1
    assert payloads[0]["current_constraints"] == {"item": "shoes", "recipient": "me", "budget": "$90"}
    assert payloads[0]["preferences"] == [
        {"item": "shoes", "recipient": "me", "key": "condition", "value": "new"},
    ]
    with store.transaction() as cur:
        brief = current_state(cur, users[0]["id"])["brief"]
    if expected is None:
        assert turn["outcome"] == "model"
        assert commerce_quality("recommendation", replies[0]["text"], None)["recommendation"]
        if response == "reply_only":
            assert brief is None
        else:
            assert brief["fields"]["budget"]["value"] == "$90"
            assert brief["options"][0]["value"] == "Aspen is new for $80"
    else:
        assert turn["outcome"] == "fallback" and brief is None


def test_agent_db_labeled_bearer_credential_stays_out_of_inference_and_history(agent_pilot):
    import json

    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.harness import SENSITIVE
    from test_agent_harness import generated

    _, _, _, _, runner, event, intake, result = agent_pilot
    projected = []
    credential = "Authorization: Bearer tbd-review-fake-credential-0123456789"

    async def provider(request, _timeout):
        prompt = request.context
        projected.append(json.loads(prompt))
        return Generation(generated(reply="I can help compare options."))

    runner.adapter = provider
    secret = intake(event(credential))
    assert runner.step()
    turn, replies = result(secret)
    assert turn["outcome"] == "blocked" and projected == []
    assert len(replies) == 1 and replies[0]["text"] == SENSITIVE

    ordinary = intake(event("Compare shoes"))
    assert runner.step() and result(ordinary)[0]["outcome"] == "model"
    assert len(projected) == 1
    assert projected[0]["recent"] == []
    assert credential not in json.dumps(projected)


@pytest.mark.parametrize("comparison", ["Compare backpacks.", "Could you compare backpacks?"])
def test_agent_db_item_switch_requires_replacement_and_cannot_bind_old_brief(agent_pilot, comparison):
    import json

    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.store import current_state
    from test_agent_harness import generated

    store, _, users, _, runner, event, intake, result = agent_pilot
    projected = []
    outputs = iter(
        [
            generated(
                brief="update",
                edits=[
                    {"field": "item", "value": "shoes"},
                    {"field": "budget", "value": "$90"},
                    {"field": "option", "value": "Aspen costs $80"},
                ],
            ),
            generated(brief="update", edits=[{"field": "budget", "value": "$50"}]),
            generated(question={"field": "size", "value": None}),
            generated(
                brief="replace",
                edits=[
                    {"field": "item", "value": "backpacks"},
                    {"field": "budget", "value": "$50"},
                ],
                question={"field": "size", "value": None},
            ),
        ]
    )

    async def provider(request, _timeout):
        prompt = request.context
        projected.append(json.loads(prompt))
        return Generation(next(outputs))

    def brief():
        with store.transaction() as cur:
            return current_state(cur, users[0]["id"])["brief"]

    runner.adapter = provider
    initial = intake(event("Compare shoes. Aspen costs $80. My budget is $90."))
    assert runner.step() and result(initial)[0]["outcome"] == "model"
    shoes = brief()
    assert shoes["fields"]["budget"]["value"] == "$90"

    request = comparison + " My budget is $50."
    invalid_update = intake(event(request))
    assert runner.step()
    assert projected[1]["current_constraints"]["item"] == "backpacks" and projected[1]["brief"] is None
    assert result(invalid_update)[0]["outcome"] == "fallback" and brief() is None

    unbound = intake(event(request))
    assert runner.step()
    turn, replies = result(unbound)
    assert turn["outcome"] == "model" and turn["question"] is None
    assert "What size" in replies[0]["text"] and brief() is None

    replacement = intake(event(request))
    assert runner.step()
    turn, _ = result(replacement)
    assert turn["outcome"] == "model"
    backpacks = brief()
    assert backpacks["id"] == str(replacement["id"])
    assert backpacks["fields"]["item"]["value"] == "backpacks"
    assert backpacks["fields"]["budget"]["value"] == "$50"
    assert backpacks["options"] == []
    assert turn["question"] == {"brief_id": backpacks["id"], "field": "size", "value": None}

    runner.adapter = None
    ambiguous = intake(event("Compare backpacks or jackets."))
    assert runner.step() and result(ambiguous)[1][0]["text"] == "Which item should I focus on?"
    assert brief() == backpacks
    offline_switch = intake(event("Switch to jackets."))
    assert runner.step() and result(offline_switch)[0]["outcome"] == "fallback"
    assert len(result(offline_switch)[1]) == 1 and brief() is None


def test_agent_db_orderly_shutdown_saves_provider_result_before_route_revocation(agent_pilot):
    from threading import Event
    from unittest.mock import Mock

    from psycopg.rows import dict_row

    from tbd_server.messaging.adapter import SendResult
    from tbd_server.messaging.runtime import LOCK_ID, DeliveryLoop

    store, account, _, pairs, runner, event, intake, result = agent_pilot
    inbound = intake(event("Compare shoes"))
    assert runner.step()
    adapter = Mock()
    loop = DeliveryLoop(store.database_url, adapter, account, lambda *_: False, agent_pairs=pairs)
    posting = Event()

    def send(_):
        posting.set()
        assert loop.stop_event.wait(5)
        return SendResult("sent", "synthetic-shutdown-id", observed_status="sent")

    adapter.send.side_effect = send
    loop.start()
    try:
        assert posting.wait(6)
    finally:
        loop.stop()

    assert adapter.send.call_count == 1 and loop.pending_result is None
    observed = result(inbound)[1][0]
    assert observed["state"] == "sent" and observed["provider_message_id"] == "synthetic-shutdown-id"
    assert observed["sent_observed_at"] is not None
    assert observed["attempt_id"] == adapter.send.call_args.args[0]["attempt_id"]

    replacement_adapter = Mock()
    replacement = DeliveryLoop(store.database_url, replacement_adapter, account, lambda *_: False, agent_pairs={})
    with psycopg.connect(store.database_url, autocommit=True, row_factory=dict_row) as db:
        assert db.execute("SELECT pg_try_advisory_lock(%s) AS owned", (LOCK_ID,)).fetchone()["owned"]
        replacement.turn(db)
    replacement_adapter.send.assert_not_called()
    assert result(inbound)[1][0] == observed


def test_agent_db_expired_delivery_keeps_account_quota_pause(agent_pilot):
    from time import monotonic
    from unittest.mock import Mock

    from psycopg.rows import dict_row

    from tbd_server.agent.delivery import authorize_post
    from tbd_server.messaging.adapter import SendResult
    from tbd_server.messaging.runtime import DeliveryLoop, claim, record_send_result

    store, account, _, pairs, runner, event, intake, result = agent_pilot
    first = intake(event("Compare shoes"))
    other = intake(event("Compare socks", "agent_b"))
    assert runner.step() and runner.step()
    with store.transaction() as cur:
        job = claim(cur, account)
        assert job["reply_to_message_id"] == first["id"]
        assert authorize_post(cur, job, account, pairs)
        assert record_send_result(
            cur,
            job["id"],
            job["attempt_id"],
            SendResult("pending", error_code="quota", retry_after=3600),
            agent_guard=True,
        )
        cur.execute(
            "UPDATE messaging.messages SET first_attempt_at=clock_timestamp()-interval '24 hours 1 second' WHERE id=%s",
            (job["id"],),
        )
    paused_until = result(first)[1][0]["next_action_at"]

    adapter = Mock()
    adapter.send.return_value = SendResult("sent", "synthetic-second-route-id", observed_status="sent")
    loop = DeliveryLoop(store.database_url, adapter, account, lambda *_: False, agent_pairs=pairs)
    loop.last_send = monotonic() - 2
    loop.category = 1
    with psycopg.connect(store.database_url, autocommit=True, row_factory=dict_row) as db:
        loop.turn(db)
        expired = result(first)[1][0]
        assert expired["state"] == "uncertain"
        assert expired["error_code"] == "quota" and expired["next_action_at"] == paused_until
        assert result(other)[1][0]["state"] == "pending"
        adapter.send.assert_not_called()

        with loop.transaction(db) as cur:
            cur.execute("UPDATE messaging.messages SET next_action_at=clock_timestamp() WHERE id=%s", (job["id"],))
        loop.category = 1
        loop.turn(db)
    assert adapter.send.call_count == 1
    assert adapter.send.call_args.args[0]["reply_to_message_id"] == other["id"]
    assert result(other)[1][0]["state"] == "sent"
    expired = result(first)[1][0]
    assert expired["state"] == "uncertain" and expired["attempt_count"] == 1


@pytest.mark.parametrize(
    "field,value",
    [("Authorization", "Bearer synthetic-bearer-value"), ("password", "synthetic-password-value")],
)
def test_agent_db_quoted_credentials_stay_out_of_inference_and_history(agent_pilot, field, value):
    import json

    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.harness import SENSITIVE
    from test_agent_harness import generated

    _, _, _, _, runner, event, intake, result = agent_pilot
    prompts = []
    credential = json.dumps({field: value})

    async def provider(request, _timeout):
        prompt = request.context
        prompts.append(json.loads(prompt))
        return Generation(generated(reply="I can help compare options."))

    runner.adapter = provider
    secret = intake(event(credential))
    assert runner.step()
    turn, replies = result(secret)
    assert turn["outcome"] == "blocked" and prompts == []
    assert len(replies) == 1 and replies[0]["text"] == SENSITIVE

    later = intake(event("Compare shoes"))
    assert runner.step() and result(later)[0]["outcome"] == "model"
    assert len(prompts) == 1 and prompts[0]["recent"] == []
    assert value not in json.dumps(prompts)


@pytest.mark.parametrize(
    "ambiguous,clarification",
    [
        ("Compare backpacks. Compare jackets. My budget is $50.", "Which item should I focus on?"),
        ("Compare shoes for Alice. Compare shoes for Beth. My budget is $50.", "Who are you shopping for?"),
        ("Compare shoes for my sister and my mom.", "Who are you shopping for?"),
    ],
)
def test_agent_db_conflicting_brief_scope_prompts_locally(agent_pilot, ambiguous, clarification):
    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.store import current_state
    from test_agent_harness import generated

    store, _, users, _, runner, event, intake, result = agent_pilot
    prompts = []
    outputs = iter(
        [
            generated(
                brief="update",
                edits=[
                    {"field": "item", "value": "shoes"},
                    {"field": "budget", "value": "$90"},
                    {"field": "option", "value": "Aspen costs $80"},
                ],
            ),
            generated(
                brief="update", edits=[{"field": "budget", "value": "$50"}], question={"field": "size", "value": None}
            ),
        ]
    )

    async def provider(request, _timeout):
        prompt = request.context
        prompts.append(prompt)
        return Generation(next(outputs))

    runner.adapter = provider
    initial = intake(event("Compare shoes. Aspen costs $80. My budget is $90."))
    assert runner.step() and result(initial)[0]["outcome"] == "model"
    with store.transaction() as cur:
        shoes = current_state(cur, users[0]["id"])["brief"]

    conflict = intake(event(ambiguous))
    assert runner.step()
    turn, replies = result(conflict)
    assert len(prompts) == 1 and len(replies) == 1
    assert replies[0]["text"] == clarification and turn["question"] is None
    with store.transaction() as cur:
        brief = current_state(cur, users[0]["id"])["brief"]
    assert brief == shoes and brief["fields"]["budget"]["value"] == "$90"


@pytest.mark.parametrize("request_text", [
    "Can you please order these for me?",
    "Please go ahead and order these for me.",
])
def test_agent_db_order_request_stays_local(agent_pilot, request_text):
    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.harness import TRANSACTION
    from test_agent_harness import generated

    _, _, _, _, runner, event, intake, result = agent_pilot
    prompts = []

    async def provider(request, _timeout):
        prompt = request.context
        prompts.append(prompt)
        return Generation(generated())

    runner.adapter = provider
    order = intake(event(request_text))
    assert runner.step()
    turn, replies = result(order)
    assert turn["outcome"] == "transaction" and prompts == []
    assert len(replies) == 1 and replies[0]["text"] == TRANSACTION


@pytest.mark.parametrize("reply", ["I have already placed the order.", "I’ve submitted your order."])
def test_agent_db_rejects_false_order_confirmation(agent_pilot, reply):
    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.harness import FALLBACK
    from test_agent_harness import generated

    _, _, _, _, runner, event, intake, result = agent_pilot
    prompts = []

    async def provider(request, _timeout):
        prompt = request.context
        prompts.append(prompt)
        return Generation(generated(reply=reply))

    runner.adapter = provider
    comparison = intake(event("Compare these shoes"))
    assert runner.step()
    turn, replies = result(comparison)
    assert len(prompts) == 1 and turn["outcome"] == "fallback"
    assert len(replies) == 1 and replies[0]["text"] == FALLBACK


def test_agent_db_reapproval_checks_older_unresolved_sends_by_exact_route(agent_pilot):
    from tbd_server.agent.delivery import authorize_post, suppress_revoked
    from tbd_server.agent.store import current_state, transition
    from tbd_server.messaging.adapter import SendResult
    from tbd_server.messaging.runtime import claim, record_send_result

    store, account, users, pairs, runner, event, intake, result = agent_pilot
    user_id = users[0]["id"]
    first = intake(event("Compare shoes"))
    assert runner.step()
    first_enrollment = result(first)[0]["enrollment_id"]
    with store.transaction() as cur:
        # Simulate a re-enrollment from the earlier release, which permitted
        # reopening the route before its pending reply had been attempted.
        cur.execute(
            "UPDATE agent_service.user_state SET enrollment_id=%s WHERE user_id=%s",
            (uuid4(), user_id),
        )
        assert current_state(cur, user_id)["enrollment_id"] != first_enrollment
        job = claim(cur, account)
        assert job["reply_to_message_id"] == first["id"]
        assert authorize_post(cur, job, account, pairs)
        assert record_send_result(
            cur,
            job["id"],
            job["attempt_id"],
            SendResult("pending", error_code="transport_unknown"),
            agent_guard=True,
        )

    pending = intake(event("Compare socks"))
    with store.transaction() as cur:
        transition(cur, user_id, "fallback", account, {})
    with pytest.raises(Problem, match="backlog has not drained"):
        with store.transaction() as cur:
            transition(cur, user_id, "fixed", account, {})
    runner.pairs = {}
    assert runner.step() and result(pending)[0]["outcome"] == "revoked"
    with store.transaction() as cur:
        transition(cur, user_id, "fixed", account, {})
        suppress_revoked(cur, account, {})
    turn, replies = result(first)
    assert turn["delivery_suppressed"]
    assert replies[0]["state"] == "uncertain" and replies[0]["provider_message_id"] is None

    with pytest.raises(Problem, match="Retire the previous route"):
        with store.transaction() as cur:
            transition(cur, user_id, "agent", account, pairs, "fixed_a")
    with pytest.raises(Problem, match="unresolved delivery"):
        with store.transaction() as cur:
            transition(cur, user_id, "agent", account, pairs, "agent_a")

    with store.transaction() as cur:
        transition(cur, user_id, "agent", account, {"fixed_a": pairs["fixed_a"]}, "fixed_a")
        assert current_state(cur, user_id)["conversation_id"] == "fixed_a"


def test_agent_db_paid_brief_scope_transition(agent_pilot):
    import json
    import subprocess
    from time import monotonic, sleep

    from tbd_server.agent import harness
    from tbd_server.agent.adapter import Generation, OpenAI
    from tbd_server.agent.runner import execution_config
    from tbd_server.agent.store import current_state
    from tbd_server.messaging.core import receive_message
    from test_agent_harness import generated

    model = os.getenv("TBD_AGENT_MODEL")
    key = os.getenv("TBD_AGENT_OPENAI_API_KEY")
    if os.getenv("TBD_RUN_AGENT_EVAL") != "1" or not model or not key:
        pytest.skip("TBD_RUN_AGENT_EVAL=1 and TBD-approved model and credentials required")

    store, account, users, pairs, runner, event, _, result = agent_pilot
    user_id = users[0]["id"]
    runner.config = execution_config(model)
    provider = OpenAI(key)
    prompts = []
    records = []

    async def paid(request, timeout):
        prompts.append(json.loads(request.context))
        started = monotonic()
        generation = await provider(request, timeout)
        records.append(
            {
                "inference_ms": round((monotonic() - started) * 1000, 2),
                "usage": generation.usage,
                "provider_reason": generation.reason,
            }
        )
        return generation

    async def seed(*_):
        return Generation(
            generated(
                reply="Aspen costs $80 and Birch costs $105.",
                brief="update",
                edits=[
                    {"field": "item", "value": "shoes"},
                    {"field": "budget", "value": "$90"},
                    {"field": "option", "value": "Aspen costs $80"},
                    {"field": "option", "value": "Birch costs $105"},
                ],
            )
        )

    async def ask_budget(*_):
        return Generation(
            generated(
                reply="I can use a new budget for these shoes.",
                question={"field": "budget", "value": None},
            )
        )

    def run(body, adapter):
        runner.adapter = adapter
        with store.transaction() as cur:
            inbound = receive_message(
                cur,
                event(body),
                current_state(cur, user_id)["enrolled_at"],
                agent_config=runner.config,
            )
        start = monotonic()
        turn, replies = result(inbound)
        while turn["outcome"] is None:
            runner.step()
            assert monotonic() - start < 30
            turn, replies = result(inbound)
        assert len(replies) == 1
        return inbound, turn, replies[0]["text"]

    def active_brief():
        with store.transaction() as cur:
            return current_state(cur, user_id)["brief"]

    initial, seed_turn, _ = run("Compare shoes. Aspen costs $80. Birch costs $105. My budget is $90.", seed)
    assert seed_turn["outcome"] == "model"
    shoes = active_brief()
    observe_agent_reply_sent(store, account, pairs, initial, result)

    carried, carry_turn, carry_reply = run("Which of the saved options is within my budget? Include the price.", paid)
    assert prompts[0]["brief"] == {k: v for k, v in shoes.items() if k != "task_cutoff"}
    assert prompts[0]["current_constraints"] == {}
    observe_agent_reply_sent(store, account, pairs, carried, result)

    question_inbound, question_turn, _ = run("Compare shoes.", ask_budget)
    assert question_turn["question"] == {"brief_id": shoes["id"], "field": "budget", "value": None}
    observed = observe_agent_reply_sent(store, account, pairs, question_inbound, result)
    sleep(harness.SKEW.total_seconds())
    switched, switch_turn, switch_reply = run(
        "Compare backpacks. Rowan costs $45. Cedar costs $75. My budget is $60. Which is within budget? Include the price.",
        paid,
    )
    assert harness.visible(observed, switched)
    carry_used = carry_turn["outcome"] == "model" and "aspen" in carry_reply.casefold() and "$80" in carry_reply
    switch_used = switch_turn["outcome"] == "model" and "rowan" in switch_reply.casefold() and "$45" in switch_reply
    old_brief_excluded = prompts[-1]["brief"] is None
    old_question_excluded = prompts[-1]["typed_answer"] is None and (
        switch_turn["question"] is None or switch_turn["question"]["brief_id"] != shoes["id"]
    )
    after_switch = active_brief()
    old_brief_retired = after_switch is None or (
        after_switch is not None and after_switch["id"] == str(switched["id"]) and after_switch["fields"]["item"]["value"] == "backpacks"
    )
    print(
        json.dumps(
            {
                "suite": "commerce-v1-brief-scope",
                "sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "model": model,
                "carried_brief_used": carry_used,
                "item_switch_answered": switch_used,
                "old_brief_excluded": old_brief_excluded,
                "old_question_excluded": old_question_excluded,
                "old_brief_retired_or_validly_replaced": old_brief_retired,
                "turns": [
                    {"case": "carried_brief", "outcome": carry_turn["outcome"], "rendered_reply": carry_reply},
                    {"case": "item_switch", "outcome": switch_turn["outcome"], "rendered_reply": switch_reply},
                ],
                "attempts": records,
            }
        )
    )
    assert carry_used and switch_used
    assert old_brief_excluded and old_question_excluded and old_brief_retired


def test_agent_db_reapproval_waits_for_unsuppressed_pending_reply(agent_pilot):
    import json
    from time import monotonic, sleep
    from unittest.mock import Mock

    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.runner import AgentRunner
    from tbd_server.agent.store import current_state, transition
    from tbd_server.messaging.adapter import SendResult
    from tbd_server.messaging.runtime import DeliveryLoop
    from test_agent_harness import generated

    store, account, users, pairs, runner, event, intake, result = agent_pilot
    user_id = users[0]["id"]
    prompts = []

    async def seed_brief(request, _timeout):
        prompts.append(json.loads(request.context))
        return Generation(generated(brief="update", edits=[{"field": "item", "value": "shoes"}]))

    runner.adapter = seed_brief
    saved = intake(event("Save material for shoes for me is linen"))
    assert runner.step() and result(saved)[0]["outcome"] == "control"
    pause = intake(event("Pause memory"))
    assert runner.step() and result(pause)[0]["outcome"] == "control"
    old = intake(event("Compare shoes"))
    assert runner.step() and result(old)[0]["outcome"] == "model"
    assert result(old)[1][0]["state"] == "pending"

    retired = {key: value for key, value in pairs.items() if key != "agent_a"}
    with store.transaction() as cur:
        previous = current_state(cur, user_id)
        assert previous["paused"] and previous["brief"]["id"] == str(old["id"])
        transition(cur, user_id, "fallback", account, pairs)
    in_drain = intake(event("Resume memory"))
    runner.start()
    try:
        deadline = monotonic() + 5
        while result(in_drain)[0]["outcome"] is None:
            assert monotonic() < deadline
            sleep(0.02)
    finally:
        runner.stop()
    assert all(not worker.is_alive() for worker in runner.threads)
    assert "temporarily unavailable" in result(in_drain)[1][0]["text"]
    with store.transaction() as cur:
        transition(cur, user_id, "fixed", account, retired)

    with pytest.raises(Problem, match="unresolved delivery"):
        with store.transaction() as cur:
            transition(cur, user_id, "agent", account, pairs, "agent_a")

    provider = Mock()
    provider.send.return_value = SendResult("sent", "synthetic-after-reapproval", observed_status="sent")
    loop = DeliveryLoop(store.database_url, provider, account, lambda *_: False, agent_pairs=retired)
    with psycopg.connect(store.database_url, autocommit=True, row_factory=psycopg.rows.dict_row) as db:
        loop.turn(db)
        provider.send.assert_not_called()
        old_turn, old_replies = result(old)
        assert old_turn["delivery_suppressed"]
        assert old_replies[0]["state"] == "failed" and old_replies[0]["attempt_count"] == 0

        with store.transaction() as cur:
            assert current_state(cur, user_id)["mode"] == "fixed"
            assert cur.execute(
                """SELECT count(*) AS count FROM messaging.messages m JOIN agent_service.turns t
                    ON t.inbound_id=m.reply_to_message_id
                    WHERE m.user_id=%s AND m.state IN ('pending','sending')""",
                (user_id,),
            ).fetchone()["count"] == 0
            transition(cur, user_id, "agent", account, pairs, "agent_a")
            state = current_state(cur, user_id)
            assert state["enrollment_id"] != previous["enrollment_id"]
            assert state["brief"] is None and state["watermark"] is None
            for key in ("preferences", "paused", "reset_sequence", "reset_source_at"):
                assert state[key] == previous[key]

        async def after_reopen(request, _timeout):
            prompts.append(json.loads(request.context))
            return Generation(generated())

        new_runner = AgentRunner(store.database_url, account, pairs, runner.config, after_reopen)
        reopened = intake(event("Compare shoes"))
        assert new_runner.step() and result(reopened)[0]["outcome"] == "model"
        assert len(prompts) == 2 and prompts[-1]["brief"] is None
        assert prompts[-1]["recent"] == [] and prompts[-1]["preferences"] == []
        loop.agent_pairs = pairs
        loop.category = 1
        loop.last_send = monotonic() - 2
        loop.turn(db)

    provider.send.assert_called_once()
    assert provider.send.call_args.args[0]["reply_to_message_id"] == reopened["id"]
    assert result(reopened)[1][0]["state"] == "sent"
    assert result(old)[1][0]["attempt_count"] == 0


@pytest.mark.parametrize("proposal", ["unseen", "unchanged", "update", "replace_with_size", "replace"])
def test_agent_db_visible_recipient_switch_needs_clean_replacement(agent_pilot, proposal):
    import json
    from time import sleep

    from tbd_server.agent import harness
    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.store import current_state
    from test_agent_harness import generated

    store, account, users, pairs, runner, event, intake, result = agent_pilot
    prompts = []
    seed = generated(
        brief="update",
        edits=[
            {"field": "item", "value": "shoes"},
            {"field": "size", "value": "8"},
            {"field": "budget", "value": "$90"},
            {"field": "option", "value": "Aspen costs $80"},
        ],
        question={"field": "recipient", "value": None},
    )
    recipient = {"field": "recipient", "value": "my sister"}
    replacement = [{"field": "item", "value": "shoes"}, recipient]
    mode, edits = {
        "unseen": ("unchanged", []),
        "unchanged": ("unchanged", []),
        "update": ("update", [recipient]),
        "replace_with_size": ("replace", [*replacement, {"field": "size", "value": "8"}]),
        "replace": ("replace", replacement),
    }[proposal]
    response = generated(
        brief=mode,
        edits=edits,
        question={"field": "size", "value": None},
    )
    outputs = iter([seed, response])

    async def provider(request, _timeout):
        prompt = request.context
        prompts.append(json.loads(prompt))
        return Generation(next(outputs))

    runner.adapter = provider
    original = intake(event("Compare shoes. My size is 8. My budget is $90. Aspen costs $80."))
    assert runner.step()
    with store.transaction() as cur:
        personal = current_state(cur, users[0]["id"])["brief"]
    assert result(original)[0]["question"]["field"] == "recipient"
    if proposal != "unseen":
        observed = observe_agent_reply_sent(store, account, pairs, original, result)
        sleep(harness.SKEW.total_seconds())

    answer = intake(event("my sister"))
    if proposal != "unseen":
        assert harness.visible(observed, answer)
    assert runner.step()
    turn, replies = result(answer)
    assert len(replies) == 1
    with store.transaction() as cur:
        brief = current_state(cur, users[0]["id"])["brief"]

    if proposal == "unseen":
        assert len(prompts) == 1 and replies[0]["text"] == harness.RESTATEMENT
    else:
        assert len(prompts) == 2
        assert prompts[1]["brief"] is None and prompts[1]["recent"] == []
        assert prompts[1]["typed_answer"] == {"recipient": "my sister", "item": "shoes"}

    if proposal == "replace":
        assert turn["outcome"] == "model" and brief["id"] == str(answer["id"])
        assert {field: entry["value"] for field, entry in brief["fields"].items()} == {
            "item": "shoes", "recipient": "my sister",
        }
        assert brief["options"] == []
        assert turn["question"] == {"brief_id": brief["id"], "field": "size", "value": None}
    else:
        assert brief == (personal if proposal == "unseen" else None)
        assert turn["question"] is None
        assert turn["outcome"] == ("model" if proposal == "unchanged" else "fallback")


def test_agentification_policy_with_controlled_alternate_adapter(agent_pilot):
    import json
    from time import sleep

    from tbd_server.agent import harness
    from tbd_server.agent.adapter import Generation, InferenceRequest
    from tbd_server.agent.store import current_state
    from test_agent_harness import generated

    store, account, users, pairs, runner, event, intake, result = agent_pilot
    runner.config["adapter"] = "controlled-alternate-v1"
    calls = []
    outputs = iter([
        generated(reply="Hey! What are you shopping for?"),
        generated(
            reply="I can compare those. Your budget rules out Birch.",
            brief="update",
            edits=[
                {"field": "item", "value": "running shoes"},
                {"field": "budget", "value": "$90"},
                {"field": "option", "value": "Aspen costs $80"},
                {"field": "option", "value": "Birch costs $105"},
                {"field": "option", "value": "Aspen is size 8"},
                {"field": "option", "value": "Birch is size 10"},
            ],
            question={"field": "size", "value": None},
        ),
        generated(reply="Aspen is the option within your $90 budget at $80. Check whether it comes in size 9.",
                  brief="update", edits=[{"field": "size", "value": "9"}]),
        generated(reply="I contacted the seller and verified Aspen is in stock."),
        generated(reply="Aspen is $35."),
        generated(reply="Aspen costs $105."),
        generated(reply="I’m ChatGPT."),
        generated(reply="Aspen comes in size 10."),
        generated(reply="Aspen comes in size 8 and Birch comes in size 10."),
    ])

    async def alternate(request: InferenceRequest, timeout: float) -> Generation:
        assert request.instructions == harness.INSTRUCTIONS and "You are TBD" in request.instructions
        assert request.model == "synthetic-model"
        assert request.schema == harness.Result.model_json_schema()
        assert request.max_output == 1200 and 0 < timeout <= 10
        calls.append(json.loads(request.context))
        return Generation(next(outputs), {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})

    def state():
        with store.transaction() as cur:
            return current_state(cur, users[0]["id"])

    def settle(body, outcome):
        incoming = intake(event(body))
        assert result(incoming)[1] == []
        assert runner.step()
        turn, replies = result(incoming)
        assert turn["outcome"] == outcome
        assert len(replies) == 1 and replies[0]["dedupe_key"] == "reply:" + str(incoming["id"])
        return incoming, turn, replies[0]["text"]

    runner.adapter = alternate
    greeting, pinned, _ = settle("Hey TBD!", "model")
    assert pinned["execution_config"] == runner.config
    assert calls[0]["current_constraints"] == {} and state()["brief"] is None
    observe_agent_reply_sent(store, account, pairs, greeting, result)
    shopping, question, reply = settle(
        "Hi TBD, I’m looking for running shoes under $90. Aspen costs $80. Birch costs $105. "
        "Aspen is size 8. Birch is size 10.", "model",
    )
    assert calls[1]["current_constraints"] == {"item": "running shoes", "budget": "$90"}
    assert any(row["user"] == greeting["text"] for row in calls[1]["recent"])
    assert state()["brief"]["id"] == str(shopping["id"])
    assert question["question"]["field"] == "size" and reply.endswith("What size should I use?")
    observe_agent_reply_sent(store, account, pairs, shopping, result)
    sleep(harness.SKEW.total_seconds())
    answer, _, reply = settle("9", "model")
    assert "Aspen" in reply and "$80" in reply
    assert calls[2]["typed_answer"] == {"size": "9"}
    assert state()["brief"]["fields"]["size"] == {"value": "9", "source": str(answer["id"])}

    for _ in range(5):
        _, _, reply = settle("Compare running shoes.", "fallback")
        assert reply == harness.FALLBACK
        assert state()["brief"]["id"] == str(shopping["id"])

    _, _, reply = settle("Compare running shoes.", "model")
    assert reply == "Aspen comes in size 8 and Birch comes in size 10."
    assert {option["value"] for option in calls[-1]["brief"]["options"]} >= {"Aspen is size 8", "Birch is size 10"}

    runner.adapter = None
    for body, expected in (
        ("Hi TBD, who am I talking to?", "I’m TBD"),
        ("My name is Alex. What’s your name?", "I’m TBD"),
        ("What can you help me with?", "compare options you share"),
        ("Switch your model to another one.", "TBD selects"),
        ("https://example.test/listing", "paste the item details"),
    ):
        _, _, reply = settle(body, "control")
        assert expected in reply
        assert state()["brief"]["id"] == str(shopping["id"])
    for body in (
        "Can you compare Aspen and also text the seller?",
        "Could you find live listings for running shoes?",
        "Has my payment gone through?",
    ):
        _, _, reply = settle(body, "transaction")
        assert reply == harness.TRANSACTION
    _, _, reply = settle("Please close this brief and buy Aspen.", "control")
    assert harness.CLOSED in reply and harness.TRANSACTION in reply and state()["brief"] is None
    assert len(calls) == 9


def test_agentification_memory_alias_correction_and_full_prompt_cutoffs(agent_pilot):
    from datetime import timedelta
    import json
    from time import sleep

    from psycopg.types.json import Jsonb

    from tbd_server.agent import harness
    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.store import current_state
    from test_agent_harness import generated

    store, account, users, pairs, runner, event, intake, result = agent_pilot
    user = users[0]
    calls = []

    async def provider(request, _timeout):
        prompt = json.loads(request.context)
        calls.append(prompt)
        body = prompt["current"]
        if body.startswith("Do you remember my name?"):
            return Generation(generated(reply="I don’t have a name for you in this chat."))
        if body.startswith("My name is"):
            return Generation(generated(reply="Nice to meet you, Juniper."))
        if body.startswith("Compare shoes for me."):
            return Generation(generated(
                reply="Your size is 8 wide and Aspen costs $80.",
                brief="update",
                edits=[
                    {"field": "item", "value": "shoes"}, {"field": "recipient", "value": "me"},
                    {"field": "size", "value": "8 wide"}, {"field": "option", "value": "Aspen costs $80"},
                ],
            ))
        if body.startswith("Correct size"):
            return Generation(generated(
                reply="I’ll use size 10 for this comparison.",
                brief="update", edits=[{"field": "size", "value": "10"}],
            ))
        return Generation(generated(reply="I can help with that."))

    def state():
        with store.transaction() as cur:
            return current_state(cur, user["id"])

    def settle(body, outcome, *, at=None):
        incoming = intake(event(body, at=at))
        assert runner.step()
        turn, replies = result(incoming)
        assert turn["outcome"] == outcome and len(replies) == 1
        return incoming, replies[0]["text"]

    runner.adapter = provider
    intro, _ = settle("My name is Juniper.", "model")
    old, _ = settle("Compare shoes for me. My size is 8 wide. Aspen costs $80.", "model")
    assert any(row["user"] == intro["text"] for row in calls[-1]["recent"])
    original = state()
    first, first_reply = settle("Remember size for shoes for myself is 8 wide", "control")
    assert "saved" in first_reply
    assert state()["preferences"]["shoes|me|size"] == {"value": "8 wide", "source": str(first["id"])}
    _, same_reply = settle("Save size for shoes for me is 8 wide", "control")
    assert "already saved" in same_reply
    _, _ = settle("Save material for shoes for me is linen", "control")
    assert state()["brief"] == original["brief"] and state()["reset_sequence"] is None

    before_withdrawal = state()
    dispatches = len(calls)
    withdrawn, reply = settle("Correct size for shoes for me to 10 for this purchase. Actually, keep size 8.", "control")
    assert reply == harness.KEEP_BRIEF and len(calls) == dispatches
    assert result(withdrawn)[0]["granted_at"] is None
    after_withdrawal = state()
    for field in ("brief", "preferences", "reset_sequence", "reset_source_at"):
        assert after_withdrawal[field] == before_withdrawal[field]

    task, _ = settle("Correct size for shoes for me to 10 for this purchase.", "model")
    assert "8 wide" not in json.dumps(calls[-1])
    assert state()["brief"]["fields"]["size"] == {"value": "10", "source": str(task["id"])}
    assert state()["preferences"]["shoes|me|size"]["value"] == "8 wide"
    settle("Compare shoes for myself", "model")
    assert "8 wide" not in json.dumps(calls[-1])

    legacy = {**state()["preferences"], "shoes|myself|size": {"value": "11", "source": str(first["id"])}}
    with store.transaction() as cur:
        cur.execute("UPDATE agent_service.user_state SET preferences=%s WHERE user_id=%s", (Jsonb(legacy), user["id"]))
    _, inspected = settle("Show my preferences", "control")
    assert inspected.count("size for shoes for me") == 1 and "conflicting values" in inspected

    prior = state()
    for body in (
        "“Forget size for shoes for me”",
        "Forget size for shoes for me; actually, keep it.",
        "Save size for shoes for me is 8 wide and 11",
        "Forget size for shoes for me and for my sister",
    ):
        _, reply = settle(body, "control")
        assert "confirm" in reply
        after = state()
        for key in ("preferences", "brief", "reset_sequence", "reset_source_at"):
            assert after[key] == prior[key]

    runner.adapter = None
    control_event = event("Compare shoes; Save size for shoes for myself is 8 wide; can you contact the seller?")
    correction = intake(control_event)
    with store.transaction() as cur:
        cur.execute(
            "UPDATE agent_service.turns SET inference_deadline=clock_timestamp()-interval '1s' WHERE inbound_id=%s",
            (correction["id"],),
        )
    assert runner.step()
    terminal, replies = result(correction)
    assert terminal["outcome"] == "control" and len(replies) == 1
    assert "updated" in replies[0]["text"] and "resend" in replies[0]["text"] and harness.TRANSACTION in replies[0]["text"]
    reset = state()
    assert reset["brief"] is None
    assert reset["preferences"] == {
        "shoes|me|size": {"value": "8 wide", "source": str(correction["id"])},
        "shoes|me|material": legacy["shoes|me|material"],
    }
    assert reset["reset_sequence"] == correction["queue_sequence"] and reset["reset_source_at"] == correction["source_at"]
    assert intake(control_event)["replayed"] and not runner.step()
    assert result(correction) == (terminal, replies)
    for offset in (0, 1):
        late, reply = settle("Compare shoes. The obsolete budget is $13.", "stale",
                             at=correction["source_at"] - timedelta(microseconds=offset))
        assert late["queue_sequence"] > correction["queue_sequence"] and reply == harness.RESTATEMENT

    changed, reply = settle("Correct size for shoes for me to 9 wide", "control")
    assert "updated" in reply and state()["reset_sequence"] == changed["queue_sequence"]
    assert state()["reset_source_at"] == changed["source_at"]
    _, reply = settle("Forget my name", "control")
    assert "cleared" in reply
    _, reply = settle("What name is on my TBD account?", "control")
    assert "User Service" in reply and user["name"] in reply
    for earlier in (intro, old):
        sent = observe_agent_reply_sent(store, account, pairs, earlier, result)
        assert sent["sent_observed_at"] > correction["source_at"]
    sleep(harness.SKEW.total_seconds())
    runner.adapter = provider
    _, reply = settle("Do you remember my name? My TBD account is active.", "model")
    assert reply == "I don’t have a name for you in this chat."
    assert calls[-1]["recent"] == [] and "Juniper" not in json.dumps(calls[-1])
    assert user["name"] not in json.dumps(calls[-1])
    settle("Compare shoes for myself", "model")
    assert calls[-1]["brief"] is None
    assert {p["key"]: p["value"] for p in calls[-1]["preferences"]} == {"material": "linen", "size": "9 wide"}
    assert all(stale not in json.dumps(calls[-1]) for stale in (old["text"], "8 wide", "obsolete", "Juniper"))


def test_agentification_repair_pause_and_resume_controls(agent_pilot):
    import json

    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.store import current_state
    from test_agent_harness import generated

    store, _, users, _, runner, event, intake, result = agent_pilot
    calls = []

    async def provider(request, _timeout):
        calls.append(json.loads(request.context))
        return Generation(generated())

    def state():
        with store.transaction() as cur:
            return current_state(cur, users[0]["id"])

    def settle(body, outcome="control"):
        incoming = intake(event(body))
        assert runner.step()
        turn, replies = result(incoming)
        assert turn["outcome"] == outcome and len(replies) == 1
        return incoming, replies[0]["text"]

    runner.adapter = provider
    settle("Save size for shoes for me is 8.5")
    settle("Remember material for shoes for me is linen")
    settle("Compare shoes. The seller copied an old size 11.", "model")
    reset_only, reply = settle("Repair size for shoes for myself to 8.5")
    assert "cleared" in reply and state()["reset_sequence"] == reset_only["queue_sequence"]
    assert state()["preferences"]["shoes|me|size"]["value"] == "8.5"
    _, reply = settle("Repair size for shoes for me to 9")
    assert "explicitly save" in reply and state()["reset_sequence"] == reset_only["queue_sequence"]

    pause, _ = settle("Pause memory")
    assert state()["paused"] and state()["reset_sequence"] == pause["queue_sequence"]
    _, reply = settle("What do you remember?")
    assert "8.5" in reply and "linen" in reply
    settle("Compare shoes for me", "model")
    assert calls[-1]["preferences"] == [] and calls[-1]["recent"] == []
    _, reply = settle("Repair size for shoes for me to 9 for future purchases")
    assert "resume" in reply and state()["reset_sequence"] == pause["queue_sequence"]
    repair, reply = settle("Repair brand for shoes for me")
    assert "cleared" in reply
    assert state()["reset_sequence"] == repair["queue_sequence"]
    _, reply = settle("Forget size for shoes for myself")
    assert "forgot" in reply and "shoes|me|size" not in state()["preferences"]
    before = state()
    settle("Resume memory")
    assert not state()["paused"] and state()["reset_sequence"] == before["reset_sequence"]
    settle("Compare shoes for me", "model")
    assert [(p["key"], p["value"]) for p in calls[-1]["preferences"]] == [("material", "linen")]
    assert "Saved preferences" not in json.dumps(calls[-1]) and "8.5" not in json.dumps(calls[-1])



def test_agentification_option_references_keep_eligible_brief_on_fallback(agent_pilot):
    import json

    from tbd_server.agent import harness
    from tbd_server.agent.adapter import Generation
    from tbd_server.agent.store import current_state
    from test_agent_harness import generated

    store, _, users, _, runner, event, intake, result = agent_pilot
    calls = []

    async def provider(request, _timeout):
        calls.append(json.loads(request.context))
        if len(calls) == 1:
            return Generation(generated(brief="update", edits=[
                {"field": "item", "value": "shoes"},
                {"field": "option", "value": "Aspen costs $80"},
                {"field": "option", "value": "Birch costs $90"},
            ]))
        return Generation(reason="synthetic_unavailable")

    def brief():
        with store.transaction() as cur:
            return current_state(cur, users[0]["id"])["brief"]

    def settle(body, outcome):
        incoming = intake(event(body))
        assert runner.step()
        turn, replies = result(incoming)
        assert turn["outcome"] == outcome and len(replies) == 1
        return incoming, replies[0]["text"]

    runner.adapter = provider
    opening, _ = settle("Compare shoes. Aspen costs $80. Birch costs $90.", "model")
    original = brief()
    assert original["id"] == str(opening["id"])

    for text in ("Compare both options", "Compare the two"):
        _, reply = settle(text, "fallback")
        assert reply == harness.FALLBACK
        assert calls[-1]["current_constraints"] == {}
        assert calls[-1]["brief"]["id"] == str(opening["id"])
        assert calls[-1]["brief"]["options"] == original["options"]
        assert brief() == original

    runner.adapter = None
    settle("Compare both options", "fallback")
    assert brief() == original
    settle("Compare backpacks.", "fallback")
    assert brief() is None
    _, reply = settle("Compare the two", "fallback")
    assert reply == harness.BRIEF_DETAILS
    assert len(calls) == 3
