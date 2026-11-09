"""One durable contact-card obligation per approved line/conversation."""

import base64
import hashlib
import json
from pathlib import Path
from uuid import uuid4

CARD_CLAIM_LOCK = 843219018
PHOTO_SHA = "a23a3e07864b03826a5793cfcd09e880963b34706714a584c0d6a0010a702067"


def payload(account, conversation):
    photo = Path(__file__).with_name("assets").joinpath("tbd-contact-logo-v1.png").read_bytes()
    if len(photo) >= 200000 or hashlib.sha256(photo).hexdigest() != PHOTO_SHA:
        raise ValueError("Contact-card artwork invalid")
    return {
        "conversation_id": conversation,
        "name": "TBD",
        "phone": account,
        "channel": "imessage",
        "line_e164": account,
        "photo_base64": "data:image/png;base64," + base64.b64encode(photo).decode("ascii"),
    }


def identity(account, conversation):
    digest = hashlib.sha256(json.dumps(payload(account, conversation), separators=(",", ":")).encode()).hexdigest()
    key = "tbd-contact-card-v1-" + hashlib.sha256((conversation + "\0" + digest).encode()).hexdigest()
    return digest, key


def eligible_reply(cur, account, conversation, peer):
    return cur.execute(
        """SELECT m.* FROM messaging.messages m JOIN user_service.users u ON u.id=m.user_id
        JOIN user_service.invitations i ON i.joined_user_id=u.id AND i.auth_user_id=u.auth_user_id
        WHERE m.account_ref=%s AND m.conversation_id=%s AND m.peer_ref=%s
        AND u.phone=%s AND u.auth_user_id IS NOT NULL AND m.direction='outbound'
        AND m.channel='imessage' AND m.state='sent' ORDER BY m.queue_sequence LIMIT 1""",
        (account, conversation, peer, peer),
    ).fetchone()


def enqueue(cur, account, pairs):
    # Only server configuration supplies pairs, and a successful canonical reply
    # is required even for existing conversations. Join alone never qualifies.
    for conversation, peer in pairs.items():
        reply = eligible_reply(cur, account, conversation, peer)
        if reply is None:
            continue
        digest, key = identity(account, conversation)
        cur.execute(
            """INSERT INTO messaging.contact_cards
            (account_ref,conversation_id,user_id,peer_ref,successful_reply_id,idempotency_key,payload_sha256)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (account, conversation, reply["user_id"], peer, reply["id"], key, digest),
        )


def claim_card(cur, account, pairs):
    # Serialize claims across processes, including a lost delivery-loop session.
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (CARD_CLAIM_LOCK,))
    if cur.execute(
        "SELECT 1 FROM messaging.contact_cards WHERE state='sending' AND next_action_at>now() LIMIT 1"
    ).fetchone():
        return None
    jobs = cur.execute(
        """SELECT * FROM messaging.contact_cards WHERE account_ref=%s AND state IN ('pending','uncertain','sending')
        AND next_action_at<=now() AND error_code IS DISTINCT FROM 'idempotency_expired'
        ORDER BY created_at FOR UPDATE""",
        (account,),
    ).fetchall()
    for job in jobs:
        if pairs.get(job["conversation_id"]) != job["peer_ref"]:
            continue
        reply = eligible_reply(cur, account, job["conversation_id"], job["peer_ref"])
        if reply is None or reply["user_id"] != job["user_id"]:
            continue
        if job["provider_message_id"] is None and job["first_attempt_at"] is not None:
            if cur.execute(
                "SELECT %s::timestamptz<=now()-interval '24 hours' AS expired", (job["first_attempt_at"],)
            ).fetchone()["expired"]:
                cur.execute(
                    "UPDATE messaging.contact_cards SET state='uncertain',error_code='idempotency_expired' WHERE id=%s",
                    (job["id"],),
                )
                continue
        return cur.execute(
            """UPDATE messaging.contact_cards SET state='sending',attempt_id=%s,
            attempt_count=attempt_count+CASE WHEN provider_message_id IS NULL THEN 1 ELSE 0 END,
            first_attempt_at=CASE WHEN provider_message_id IS NULL THEN coalesce(first_attempt_at,now()) ELSE first_attempt_at END,
            next_action_at=now()+interval '45 seconds',error_code=NULL,updated_at=now() WHERE id=%s RETURNING *""",
            (uuid4(), job["id"]),
        ).fetchone()
    return None


def record_card_result(cur, job, result):
    return (
        cur.execute(
            """UPDATE messaging.contact_cards SET state=%s,
        provider_message_id=coalesce(provider_message_id,%s),response_body=coalesce(response_body,%s),
        observed_status=%s,error_code=%s,next_action_at=now()+make_interval(secs=>%s),updated_at=now()
        WHERE id=%s AND attempt_id=%s AND state NOT IN ('sent','failed')
        AND (provider_message_id IS NULL OR provider_message_id=%s) RETURNING id""",
            (
                "uncertain" if result.state == "provider_queued" else result.state,
                result.provider_id,
                result.response_body,
                result.observed_status,
                result.error_code,
                result.retry_after or 15,
                job["id"],
                job["attempt_id"],
                result.provider_id,
            ),
        ).fetchone()
        is not None
    )


def import_verified(cur, account, pairs, evidence, summary, verified):
    """Owner-operated import; no POST. Verify private proof plus fresh exact-ID GET."""
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (CARD_CLAIM_LOCK,))
    conversation = evidence["conversation_id"]
    peer = pairs.get(conversation)
    reply = eligible_reply(cur, account, conversation, peer)
    digest, key = identity(account, conversation)
    if (
        reply is None
        or evidence.get("attempt_state") != "accepted"
        or evidence.get("payload_sha256") != digest
        or evidence.get("idempotency_key") != key
        or evidence.get("photo_sha256") != PHOTO_SHA
        or not summary.get("card_received_in_original_thread_confirmed")
        or not summary.get("saved_contact_logo_visible_in_messages_confirmed")
        or verified.state != "sent"
        or verified.provider_id != evidence.get("provider_message_id")
    ):
        raise ValueError("Card proof or eligibility mismatch")
    enqueue(cur, account, {conversation: peer})
    row = cur.execute(
        "SELECT * FROM messaging.contact_cards WHERE account_ref=%s AND conversation_id=%s FOR UPDATE",
        (account, conversation),
    ).fetchone()
    if row["idempotency_key"] != key or row["attempt_count"] != 0:
        raise ValueError("Existing card attempt must be reconciled before import")
    if row["state"] == "sent":
        if row["provider_message_id"] != verified.provider_id:
            raise ValueError("Imported card ID conflict")
        return row["id"]
    cur.execute(
        """UPDATE messaging.contact_cards SET state='sent',provider_message_id=%s,response_body=%s,
        observed_status=%s,updated_at=now() WHERE id=%s""",
        (verified.provider_id, verified.response_body, verified.observed_status, row["id"]),
    )
    return row["id"]


def main():
    import argparse
    import os
    from .adapter import Comms
    from tbd_server.user.store import Store

    parser = argparse.ArgumentParser(description="Import a verified existing card before rollout; never sends")
    parser.add_argument("evidence")
    parser.add_argument("summary")
    args = parser.parse_args()
    try:
        evidence = json.loads(Path(args.evidence).read_text())
        summary = json.loads(Path(args.summary).read_text())
        account = os.environ["COMMS_LINE_E164"]
        pairs = json.loads(os.environ["COMMS_PILOT_CONVERSATIONS"])
        store = Store(os.environ["DATABASE_URL"])
        store.verify_connection()
        adapter = Comms(os.environ["COMMS_API_KEY"])
        try:
            message = evidence["response"]["message"]
            job = {
                "account_ref": account,
                "conversation_id": evidence["conversation_id"],
                "provider_message_id": evidence["provider_message_id"],
                "response_body": message["body"],
            }
            card = evidence["response"]["contact_card"]
            if (
                card.get("name") != "TBD"
                or card.get("phone") != account
                or message.get("id") != evidence["provider_message_id"]
                or message.get("conversation_id") != evidence["conversation_id"]
                or message.get("line_e164") != account
                or message.get("channel") != "imessage"
                or message.get("direction") != "outbound"
            ):
                raise ValueError("Private card metadata mismatch")
            verified = adapter.card_status(job)
            with store.transaction() as cur:
                import_verified(cur, account, pairs, evidence, summary, verified)
        finally:
            adapter.close()
        print(json.dumps({"imported": True, "provider_calls_read_only": True, "card_sent": False}))
    except Exception:
        raise SystemExit("Import failed; inspect private proof/configuration. No card POST attempted.") from None


if __name__ == "__main__":
    main()
