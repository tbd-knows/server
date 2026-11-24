"""Agent reply guard used only by the existing Messaging delivery owner."""

from time import monotonic

from .store import approved, lock_user, now

BACKOFF = ("quota", "account_configuration", "rate_limited")
DEFINITELY_REJECTED = (*BACKOFF, "request_rejected", "agent_claim_expired")


def marker(cur, job):
    return cur.execute(
        "SELECT * FROM agent_service.turns WHERE inbound_id=%s", (job["reply_to_message_id"],)
    ).fetchone()


def route_allowed(job, user, account, pairs):
    return user and user["joined"] and user["phone"] == job["peer_ref"] and approved(job, account, pairs)


def suppress(cur, job, turn):
    cur.execute("UPDATE agent_service.turns SET delivery_suppressed=true WHERE inbound_id=%s", (turn["inbound_id"],))
    if job["provider_message_id"] is not None or job["state"] in ("sent", "failed"):
        return
    possible = turn["submission_possible"] or turn["delivery_grant"] is not None
    cur.execute(
        """UPDATE messaging.messages SET state=%s,updated_at=clock_timestamp(),
          error_code=CASE WHEN error_code IN ('quota','account_configuration','rate_limited')
            AND next_action_at>clock_timestamp() THEN error_code ELSE 'agent_route_revoked' END
        WHERE id=%s""",
        ("uncertain" if possible else "failed", job["id"]),
    )


def suppress_revoked(cur, account, pairs):
    # Sweep before checking provider pauses, including future-due work. The
    # single delivery owner is also responsible for retry and result handling.
    jobs = cur.execute(
        """SELECT m.*,u.phone AS current_phone,
          EXISTS (SELECT 1 FROM user_service.invitations WHERE joined_user_id=u.id) AS joined
        FROM agent_service.turns t JOIN messaging.messages m ON m.reply_to_message_id=t.inbound_id
        JOIN user_service.users u ON u.id=m.user_id
        WHERE m.channel='imessage' AND m.direction='outbound' AND m.dedupe_key='reply:'||t.inbound_id::text
          AND NOT t.delivery_suppressed AND m.state NOT IN ('sent','failed')
        ORDER BY m.queue_sequence"""
    ).fetchall()
    for job in jobs:
        if approved(job, account, pairs) and job["joined"] and job["current_phone"] == job["peer_ref"]:
            continue
        user = lock_user(cur, job["user_id"])
        if not route_allowed(job, user, account, pairs):
            suppress(cur, job, marker(cur, job))


def authorize_post(cur, job, account, pairs):
    """Return the final monotonic deadline for this POST, or None to skip."""
    turn = marker(cur, job)
    if not turn:
        # Legacy commands keep the existing queue behavior.
        return float("inf")
    user = lock_user(cur, job["user_id"])
    turn = marker(cur, job)
    current = cur.execute("SELECT * FROM messaging.messages WHERE id=%s FOR UPDATE", (job["id"],)).fetchone()
    if current["state"] != "sending" or current["attempt_id"] != job["attempt_id"]:
        return None
    if turn["delivery_suppressed"] or not route_allowed(job, user, account, pairs):
        suppress(cur, current, turn)
        return None
    tick = monotonic()
    at = now(cur)
    window = min(
        86400 - (at - current["first_attempt_at"]).total_seconds(), 15 - (at - current["attempt_at"]).total_seconds()
    )
    if window <= 0:
        possible = turn["submission_possible"] or turn["delivery_grant"] is not None
        expires_key = (at - current["first_attempt_at"]).total_seconds() >= 86400
        cur.execute(
            "UPDATE messaging.messages SET state=%s,error_code=%s WHERE id=%s",
            (
                "uncertain" if possible and expires_key else "failed" if expires_key else "pending",
                "idempotency_expired" if expires_key else "agent_claim_expired",
                job["id"],
            ),
        )
        return None
    if turn["delivery_grant"] == job["attempt_id"]:
        return None
    cur.execute(
        """UPDATE agent_service.turns SET delivery_grant=%s,
        submission_possible=(submission_possible OR delivery_grant IS NOT NULL) WHERE inbound_id=%s""",
        (job["attempt_id"], turn["inbound_id"]),
    )
    return tick + window


def record_evidence(cur, job, result):
    turn = marker(cur, job)
    if not turn:
        return result.state
    if turn["delivery_grant"] == job["attempt_id"]:
        possible = not result.provider_id and result.error_code not in DEFINITELY_REJECTED
        cur.execute(
            """UPDATE agent_service.turns SET delivery_grant=NULL,
            submission_possible=(submission_possible OR %s) WHERE inbound_id=%s""",
            (possible, turn["inbound_id"]),
        )
        turn["submission_possible"] |= possible
    if not result.provider_id and result.state == "failed" and turn["submission_possible"]:
        return "uncertain"
    if turn["delivery_suppressed"] and not result.provider_id:
        return "uncertain" if turn["submission_possible"] else "failed"
    return result.state
