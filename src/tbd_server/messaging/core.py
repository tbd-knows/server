"""Messaging operations use their caller's cursor and commit boundary.

Callers acquire the canonical User row lock before creating messages. Provider
wire fields are translated by the adapter before constructing ReceivedMessage.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from tbd_server.user.store import Problem

WELCOME = "Welcome to TBD. You can reply to this number."


def utf8_size(value):
    try:
        if "\x00" in value:
            raise ValueError
        return len(value.encode("utf-8"))
    except (UnicodeError, ValueError):
        raise Problem(422, "Invalid UTF-8 content") from None


@dataclass(frozen=True)
class ReceivedMessage:
    native_id: str
    account: str
    peer: str
    text: str | None
    unsupported: bool
    source_at: datetime
    channel: str = "imessage"

    def __post_init__(self):
        if not isinstance(self.native_id, str) or not 0 < utf8_size(self.native_id) <= 256:
            raise Problem(422, "Invalid native message ID")
        if self.channel != "imessage" or not self.account or not self.peer:
            raise Problem(422, "Invalid message route")
        if not isinstance(self.unsupported, bool):
            raise Problem(422, "Invalid content classification")
        if self.text is not None and not isinstance(self.text, str):
            raise Problem(422, "Invalid message text")
        if self.text is None or self.text == "":
            if not self.unsupported:
                raise Problem(422, "Text message must be nonempty")
        if self.text is not None and utf8_size(self.text) > 64 * 1024:
            raise Problem(413, "Message text too large")
        if not isinstance(self.source_at, datetime) or self.source_at.utcoffset() is None:
            raise Problem(422, "Original message timestamp required")


def queue_message(cur, user, route, text, dedupe_key, reply_to=None):
    """Caller holds User lock. A producer key cannot change an existing command."""
    account, peer = route
    existing = cur.execute(
        "SELECT * FROM messaging.messages WHERE channel='imessage' AND direction='outbound' AND dedupe_key=%s",
        (dedupe_key,),
    ).fetchone()
    if existing:
        expected = (user["id"], account, peer, text, reply_to)
        actual = tuple(existing[k] for k in ("user_id", "account_ref", "peer_ref", "text", "reply_to_message_id"))
        if actual != expected:
            raise Problem(409, "Outbound command conflicts")
        return existing
    if not isinstance(text, str) or not text or len(text) > 5000:
        raise Problem(422, "Invalid outbound text")
    if reply_to is not None:
        parent = cur.execute("SELECT * FROM messaging.messages WHERE id=%s", (reply_to,)).fetchone()
        if parent is None or (
            parent["direction"],
            parent["user_id"],
            parent["channel"],
            parent["account_ref"],
            parent["peer_ref"],
        ) != ("inbound", user["id"], "imessage", account, peer):
            raise Problem(409, "Reply route conflicts")
    inserted = cur.execute(
        """INSERT INTO messaging.messages
        (user_id,channel,direction,dedupe_key,account_ref,peer_ref,text,state,reply_to_message_id)
        VALUES (%s,'imessage','outbound',%s,%s,%s,%s,'pending',%s)
        ON CONFLICT (channel,direction,dedupe_key) DO NOTHING RETURNING *""",
        (user["id"], dedupe_key, account, peer, text, reply_to),
    ).fetchone()
    if inserted is None:
        return queue_message(cur, user, route, text, dedupe_key, reply_to)
    return inserted


def ensure_welcome(cur, user, account):
    # Welcome identity is independent of current copy and contact configuration.
    key = "welcome:" + str(user["id"])
    existing = cur.execute(
        "SELECT * FROM messaging.messages WHERE channel='imessage' AND direction='outbound' AND dedupe_key=%s",
        (key,),
    ).fetchone()
    if existing:
        return existing
    return queue_message(cur, user, (account, user["phone"]), WELCOME, key)


def receive_message(cur, event: ReceivedMessage, activated_at: datetime):
    """Atomic admission, welcome, inbound and reply; caller commits before ACK."""
    # Serialize native identity even if a conflicting replay changes its peer.
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("inbound:" + event.native_id,))
    existing = cur.execute(
        "SELECT * FROM messaging.messages WHERE channel=%s AND direction='inbound' AND dedupe_key=%s",
        (event.channel, "inbound:" + event.native_id),
    ).fetchone()
    if existing:
        expected = (event.account, event.peer, event.text, event.unsupported, event.source_at)
        actual = tuple(existing[k] for k in ("account_ref", "peer_ref", "text", "has_unsupported_content", "source_at"))
        if actual != expected:
            raise Problem(409, "Inbound replay conflicts")
        return existing
    user = cur.execute(
        """SELECT u.* FROM user_service.users u WHERE u.phone=%s
        AND EXISTS (SELECT 1 FROM user_service.invitations i WHERE i.joined_user_id=u.id)
        FOR UPDATE OF u""",
        (event.peer,),
    ).fetchone()
    if user is None or event.source_at < max(activated_at, user["created_at"]):
        return None
    ensure_welcome(cur, user, event.account)
    inbound = cur.execute(
        """INSERT INTO messaging.messages
        (user_id,channel,direction,dedupe_key,account_ref,peer_ref,text,has_unsupported_content,
         provider_message_id,source_at,state)
        VALUES (%s,%s,'inbound',%s,%s,%s,%s,%s,%s,%s,'received') RETURNING *""",
        (
            user["id"],
            event.channel,
            "inbound:" + event.native_id,
            event.account,
            event.peer,
            event.text,
            event.unsupported,
            event.native_id,
            event.source_at,
        ),
    ).fetchone()
    queue_message(
        cur,
        user,
        (event.account, event.peer),
        "Please send text for now." if event.unsupported else "Received your text.",
        "reply:" + str(inbound["id"]),
        inbound["id"],
    )
    return inbound


def pilot_welcomes(cur, user_ids: list[UUID], account: str):
    """Explicit selected joined Users only; take User locks in stable order."""
    results = []
    for user_id in sorted(set(user_ids)):
        user = cur.execute(
            """SELECT u.* FROM user_service.users u WHERE u.id=%s
            AND EXISTS (SELECT 1 FROM user_service.invitations i WHERE i.joined_user_id=u.id)
            FOR UPDATE OF u""",
            (user_id,),
        ).fetchone()
        if user is None:
            raise Problem(422, "Pilot User must have completed Join")
        results.append(ensure_welcome(cur, user, account))
    return results
