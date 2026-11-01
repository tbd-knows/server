"""Run only against a newly created, disposable local database, never Supabase."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

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
        db.execute(Path("tests/schema.sql").read_text())
    return Store(url)


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
    with store.transaction() as cur:
        cur.execute("INSERT INTO auth.users VALUES (%s)", (identity,))
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
