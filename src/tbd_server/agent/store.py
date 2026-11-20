"""Agent state changes share Messaging's canonical User lock."""

from uuid import uuid4

from psycopg.types.json import Jsonb

from tbd_server.messaging.core import queue_message
from tbd_server.user.store import Problem


ROUTE_FIELDS = ("account_ref", "peer_ref", "conversation_id")
HEAD = """
SELECT m.*, t.enrollment_id,t.outcome,t.inference_deadline,t.execution_config,
 t.attempt_id AS agent_attempt,t.lease_until,t.attempts_left,t.granted_at
FROM messaging.messages m JOIN agent_service.turns t ON t.inbound_id=m.id
WHERE m.user_id=%s AND t.outcome IS NULL ORDER BY m.queue_sequence LIMIT 1
"""


def route(row):
    return tuple(row[field] for field in ROUTE_FIELDS)


def approved(row, account, pairs):
    return row["account_ref"] == account and pairs.get(row["conversation_id"]) == row["peer_ref"]


def lock_user(cur, user_id):
    return cur.execute(
        """SELECT u.id,u.phone,EXISTS (
          SELECT 1 FROM user_service.invitations WHERE joined_user_id=u.id
        ) AS joined FROM user_service.users u WHERE id=%s FOR UPDATE OF u""",
        (user_id,),
    ).fetchone()


def current_state(cur, user_id):
    return cur.execute("SELECT * FROM agent_service.user_state WHERE user_id=%s", (user_id,)).fetchone()


def now(cur):
    return cur.execute("SELECT clock_timestamp() AS at").fetchone()["at"]


def admit(cur, inbound, config):
    """Called only for new verified text, in the inbound transaction after locking."""
    state = current_state(cur, inbound["user_id"])
    if not state or state["mode"] == "fixed" or route(state) != route(inbound):
        return False
    cur.execute(
        """INSERT INTO agent_service.turns
        (inbound_id,enrollment_id,inference_deadline,execution_config,attempts_left)
        VALUES (%s,%s,clock_timestamp() + make_interval(secs => %s),%s,2)""",
        (inbound["id"], state["enrollment_id"], config["deadline"], Jsonb(config)),
    )
    return True


def finish(cur, turn, state, outcome, text=None, *, fresh=False, question=None):
    """Publish all effects in one transaction. Every caller holds the User lock."""
    if text is not None:
        queue_message(cur, {"id": turn["user_id"]}, route(turn), text, "reply:" + str(turn["id"]), turn["id"])
    if fresh:
        cur.execute(
            """UPDATE agent_service.user_state SET brief=%s,preferences=%s,paused=%s,
            watermark=%s,reset_source_at=%s,reset_sequence=%s WHERE user_id=%s""",
            (
                Jsonb(state["brief"]) if state["brief"] is not None else None,
                Jsonb(state["preferences"]),
                state["paused"],
                turn["source_at"],
                state["reset_source_at"],
                state["reset_sequence"],
                turn["user_id"],
            ),
        )
    cur.execute(
        "UPDATE agent_service.turns SET outcome=%s,completed_at=clock_timestamp(),question=%s WHERE inbound_id=%s",
        (outcome, Jsonb(question) if question else None, turn["id"]),
    )


def transition(cur, user_id, mode, account, pairs, conversation=None):
    user = lock_user(cur, user_id)
    state = current_state(cur, user_id)
    previous = state["mode"] if state else "fixed"
    if not user or not user["joined"]:
        raise Problem(409, "Joined User required")
    if (previous, mode) not in {("fixed", "agent"), ("agent", "fallback"), ("fallback", "fixed")}:
        raise Problem(409, "Invalid Agent transition")
    if mode == "agent":
        if not conversation or pairs.get(conversation) != user["phone"]:
            raise Problem(409, "Approved iMessage route required")
        target_route = (account, user["phone"], conversation)
        if state:
            replacing = route(state) != target_route
            if replacing and approved(state, account, pairs):
                raise Problem(409, "Retire the previous route before replacement")
            if cur.execute(
                """SELECT 1 FROM agent_service.turns t JOIN messaging.messages m ON m.reply_to_message_id=t.inbound_id
                WHERE m.user_id=%s AND t.enrollment_id=%s AND (
                  (%s AND NOT t.delivery_suppressed AND m.state NOT IN ('sent','failed'))
                  OR (m.state='uncertain' AND m.provider_message_id IS NULL))
                LIMIT 1""",
                (user_id, state["enrollment_id"], replacing),
            ).fetchone():
                raise Problem(409, "Previous route still has unresolved delivery")
        if cur.execute(
            """SELECT 1 FROM agent_service.turns t
            JOIN messaging.messages m ON m.reply_to_message_id=t.inbound_id
            WHERE m.user_id=%s AND (m.account_ref,m.peer_ref,m.conversation_id)=(%s,%s,%s)
              AND m.channel='imessage' AND m.direction='outbound' AND m.dedupe_key='reply:'||t.inbound_id::text
              AND m.provider_message_id IS NULL AND m.state NOT IN ('sent','failed')
              AND (m.state='uncertain' OR t.submission_possible OR t.delivery_grant IS NOT NULL)
            LIMIT 1""",
            (user_id, *target_route),
        ).fetchone():
            raise Problem(409, "This route still has unresolved delivery")
        cur.execute(
            """INSERT INTO agent_service.user_state
            (user_id,mode,enrollment_id,enrolled_at,account_ref,peer_ref,conversation_id)
            VALUES (%s,'agent',%s,clock_timestamp(),%s,%s,%s)
            ON CONFLICT (user_id) DO UPDATE SET mode='agent',enrollment_id=excluded.enrollment_id,
              enrolled_at=excluded.enrolled_at,account_ref=excluded.account_ref,peer_ref=excluded.peer_ref,
              conversation_id=excluded.conversation_id,brief=NULL,watermark=NULL""",
            (user_id, uuid4(), *target_route),
        )
    else:
        if mode == "fixed" and cur.execute(HEAD, (user_id,)).fetchone():
            raise Problem(409, "Agent backlog has not drained")
        cur.execute("UPDATE agent_service.user_state SET mode=%s WHERE user_id=%s", (mode, user_id))
