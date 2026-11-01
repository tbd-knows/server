"""Transactional persistence. Every mutation commits before its caller sees success."""

from contextlib import contextmanager
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .models import AddressDelete, AddressWrite, DefaultWrite, ProfilePatch, Seed


class Problem(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail
        super().__init__(detail)


class Store:
    def __init__(self, database_url: str):
        self.database_url = database_url

    @contextmanager
    def transaction(self):
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=5) as db:
                with db.cursor() as cur:
                    cur.execute("SET LOCAL statement_timeout = '10s'")
                    cur.execute("SET LOCAL search_path = user_service, pg_catalog")
                    yield cur
        except psycopg.errors.UniqueViolation:
            raise Problem(409, "Identity or address conflict") from None

    @staticmethod
    def user(cur, user_id, revision=None):
        row = cur.execute("SELECT * FROM users WHERE id=%s FOR UPDATE", (user_id,)).fetchone()
        if row is None:
            raise Problem(404, "User not found")
        if revision is not None and row["revision"] != revision:
            raise Problem(409, "Stale revision; reread before changing")
        return row

    @staticmethod
    def profile(cur, user_id):
        # The caller holds the User lock so profile/revision and addresses agree.
        row = cur.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
        row["addresses"] = cur.execute("SELECT * FROM addresses WHERE user_id=%s ORDER BY id", (user_id,)).fetchall()
        return row

    @staticmethod
    def bump(cur, user_id):
        cur.execute("UPDATE users SET revision=revision+1 WHERE id=%s", (user_id,))

    def read(self, user_id):
        with self.transaction() as cur:
            self.user(cur, user_id)
            result = self.profile(cur, user_id)
        return result

    def resolve(self, phone):
        with self.transaction() as cur:
            row = cur.execute("SELECT id FROM users WHERE phone=%s", (phone,)).fetchone()
            if row is None:
                raise Problem(404, "Sender not found")
        return row

    def patch(self, user_id, data: ProfilePatch):
        with self.transaction() as cur:
            self.user(cur, user_id, data.revision)
            cur.execute("UPDATE users SET name=%s WHERE id=%s", (data.name, user_id))
            self.bump(cur, user_id)
            result = self.profile(cur, user_id)
        return result

    def seed(self, data: Seed):
        with self.transaction() as cur:
            # Serializes retries even when the key does not yet exist.
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (data.seed_key,))
            row = cur.execute("SELECT * FROM users WHERE seed_key=%s", (data.seed_key,)).fetchone()
            if row:
                if any(row[key] != value for key, value in data.model_dump().items()):
                    raise Problem(409, "Seed details conflict")
            else:
                row = cur.execute(
                    "INSERT INTO users (seed_key,name,phone,email,type) VALUES (%s,%s,%s,%s,'business') RETURNING *",
                    (data.seed_key, data.name, data.phone, data.email),
                ).fetchone()
        return row

    def join(self, auth_id: UUID, email: str):
        with self.transaction() as cur:
            entry = cur.execute("SELECT * FROM invitations WHERE email=%s FOR UPDATE", (email,)).fetchone()
            if entry is None:
                raise Problem(403, "No approved invitation")
            if entry["auth_user_id"] != auth_id:
                raise Problem(403, "Invitation identity mismatch")
            if entry["joined_user_id"]:
                row = cur.execute("SELECT * FROM users WHERE id=%s", (entry["joined_user_id"],)).fetchone()
            else:
                row = cur.execute(
                    "INSERT INTO users (auth_user_id,name,phone,email,type) VALUES (%s,%s,%s,%s,%s) RETURNING *",
                    (auth_id, entry["name"], entry["phone"], entry["email"], entry["type"]),
                ).fetchone()
                cur.execute("UPDATE invitations SET joined_user_id=%s WHERE id=%s", (row["id"], entry["id"]))
        return row

    def save_address(self, user_id, address_id, data: AddressWrite):
        values = data.model_dump(exclude={"revision"})
        with self.transaction() as cur:
            user = self.user(cur, user_id)
            old = cur.execute("SELECT * FROM addresses WHERE id=%s", (address_id,)).fetchone()
            if old and old["user_id"] != user_id:
                raise Problem(404, "Address not found")
            identical = old and all(old[key] == value for key, value in values.items())
            # An identical retry is a no-op even when its revision predates the save.
            if not identical:
                if user["revision"] != data.revision:
                    raise Problem(409, "Stale revision; reread before changing")
                params = tuple(values.values())
                if old:
                    cur.execute(
                        "UPDATE addresses SET label=%s,recipient=%s,line1=%s,line2=%s,city=%s,region=%s,postal_code=%s,country_code=%s WHERE id=%s AND user_id=%s",
                        (*params, address_id, user_id),
                    )
                else:
                    cur.execute(
                        "INSERT INTO addresses (label,recipient,line1,line2,city,region,postal_code,country_code,id,user_id,is_default) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOT EXISTS (SELECT 1 FROM addresses WHERE user_id=%s))",
                        (*params, address_id, user_id, user_id),
                    )
                self.bump(cur, user_id)
            result = self.profile(cur, user_id)
        return result

    @staticmethod
    def own_address(cur, user_id, address_id):
        row = cur.execute("SELECT * FROM addresses WHERE id=%s AND user_id=%s", (address_id, user_id)).fetchone()
        if row is None:
            raise Problem(404, "Address not found")
        return row

    def set_default(self, user_id, data: DefaultWrite):
        with self.transaction() as cur:
            self.user(cur, user_id, data.revision)
            self.own_address(cur, user_id, data.address_id)
            cur.execute("UPDATE addresses SET is_default=false WHERE user_id=%s AND is_default", (user_id,))
            cur.execute("UPDATE addresses SET is_default=true WHERE id=%s", (data.address_id,))
            self.bump(cur, user_id)
            result = self.profile(cur, user_id)
        return result

    def delete_address(self, user_id, address_id, data: AddressDelete):
        with self.transaction() as cur:
            self.user(cur, user_id, data.revision)
            old = self.own_address(cur, user_id, address_id)
            others = cur.execute(
                "SELECT id FROM addresses WHERE user_id=%s AND id<>%s", (user_id, address_id)
            ).fetchall()
            replacement = data.replacement_address_id
            if replacement is not None and replacement not in [r["id"] for r in others]:
                raise Problem(404, "Replacement address not found")
            if old["is_default"] and others and replacement is None:
                raise Problem(422, "Replacement default required")
            if replacement is not None and not old["is_default"]:
                raise Problem(422, "Replacement only applies when removing the default")
            cur.execute("DELETE FROM addresses WHERE id=%s AND user_id=%s", (address_id, user_id))
            if replacement:
                cur.execute("UPDATE addresses SET is_default=true WHERE id=%s AND user_id=%s", (replacement, user_id))
            self.bump(cur, user_id)
            result = self.profile(cur, user_id)
        return result

    def approve(self, data):
        with self.transaction() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (data.email,))
            row = cur.execute("SELECT * FROM invitations WHERE email=%s", (data.email,)).fetchone()
            if row:
                if any(row[key] != value for key, value in data.model_dump().items()):
                    raise Problem(409, "Approved details conflict")
            else:
                if cur.execute("SELECT 1 FROM users WHERE email=%s OR phone=%s", (data.email, data.phone)).fetchone():
                    raise Problem(409, "Contact already belongs to a User")
                row = cur.execute(
                    "INSERT INTO invitations (name,phone,email,type) VALUES (%s,%s,%s,%s) RETURNING *",
                    (data.name, data.phone, data.email, data.type),
                ).fetchone()
        return row

    def invitation(self, invitation_id):
        with self.transaction() as cur:
            row = cur.execute("SELECT * FROM invitations WHERE id=%s", (invitation_id,)).fetchone()
            if row is None:
                raise Problem(404, "Invitation not found")
        return row

    def bind_auth(self, invitation_id, auth_id):
        with self.transaction() as cur:
            row = cur.execute("SELECT * FROM invitations WHERE id=%s FOR UPDATE", (invitation_id,)).fetchone()
            if row is None:
                raise Problem(404, "Invitation not found")
            if row["auth_user_id"] is not None and row["auth_user_id"] != auth_id:
                raise Problem(409, "Auth identity conflict")
            cur.execute("UPDATE invitations SET auth_user_id=%s WHERE id=%s", (auth_id, invitation_id))
