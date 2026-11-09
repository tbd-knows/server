"""One session-owned delivery loop; no database transaction spans provider I/O."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import logging
from threading import Event, Thread
from time import monotonic
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .adapter import SendResult, retry_after

log = logging.getLogger("tbd.messaging")
LOCK_ID = 843219017


def configure_logging():
    """Own only the Messaging logger; keep provider/root logging unchanged."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.handlers = [handler]
    log.setLevel(logging.INFO)
    log.disabled = False
    log.propagate = False


def emit(event, **fields):
    log.info(json.dumps({"event": event, **fields}, default=str))


def claim(cur, account):
    cur.execute(
        """UPDATE messaging.messages SET state='uncertain',error_code='idempotency_expired',updated_at=now()
        WHERE direction='outbound' AND account_ref=%s AND state='pending'
        AND provider_message_id IS NULL AND first_attempt_at<=now()-interval '24 hours'""",
        (account,),
    )
    # Prior unresolved jobs block only their own route, including uncertain jobs.
    row = cur.execute(
        """SELECT m.* FROM messaging.messages m WHERE m.direction='outbound'
        AND m.account_ref=%s AND m.state='pending' AND m.next_action_at<=now()
        AND NOT EXISTS (SELECT 1 FROM messaging.messages prior
          WHERE prior.direction='outbound' AND prior.user_id=m.user_id
          AND prior.account_ref=m.account_ref AND prior.channel=m.channel AND prior.conversation_id=m.conversation_id
          AND prior.queue_sequence<m.queue_sequence AND prior.state NOT IN ('sent','failed'))
        AND NOT EXISTS (SELECT 1 FROM messaging.messages paused
          WHERE paused.account_ref=m.account_ref AND paused.error_code IN ('quota','account_configuration','rate_limited')
          AND paused.next_action_at>now())
        ORDER BY m.queue_sequence LIMIT 1 FOR UPDATE OF m""",
        (account,),
    ).fetchone()
    if row is None:
        return None
    return cur.execute(
        """UPDATE messaging.messages SET state='sending',attempt_id=%s,attempt_count=attempt_count+1,
        attempt_at=now(),first_attempt_at=coalesce(first_attempt_at,now()),updated_at=now(),error_code=NULL WHERE id=%s RETURNING *""",
        (uuid4(), row["id"]),
    ).fetchone()


def record_send_result(cur, message_id, attempt_id, result: SendResult):
    row = cur.execute("SELECT * FROM messaging.messages WHERE id=%s FOR UPDATE", (message_id,)).fetchone()
    if row is None or row["direction"] != "outbound" or row["attempt_id"] != attempt_id:
        return False
    if row["state"] in ("sent", "failed"):
        return False
    if row["provider_message_id"] and result.provider_id != row["provider_message_id"]:
        return False
    if result.state not in ("pending", "provider_queued", "sent", "failed", "uncertain"):
        return False
    if row["provider_message_id"] and result.state == "pending":
        return False
    cur.execute(
        """UPDATE messaging.messages SET state=%s,provider_message_id=coalesce(%s,provider_message_id),
        error_code=%s,observed_status=%s,updated_at=now(),
        accepted_at=CASE WHEN %s::text IS NOT NULL THEN coalesce(accepted_at,now()) ELSE accepted_at END,
        sent_observed_at=CASE WHEN %s='sent' THEN now() ELSE sent_observed_at END,
        next_action_at=now() + make_interval(secs => %s) WHERE id=%s""",
        (
            result.state,
            result.provider_id,
            result.error_code,
            result.observed_status,
            result.provider_id,
            result.state,
            result.retry_after or (15 if result.state != "pending" else min(30, 2 ** min(row["attempt_count"], 5))),
            message_id,
        ),
    )
    return True


class DeliveryLoop:
    def __init__(self, database_url, adapter, account, history_turn, *, card_pairs=None):
        self.database_url, self.adapter, self.account = database_url, adapter, account
        self.history_turn = history_turn
        self.stop_event = Event()
        self.thread = None
        self.last_tick = monotonic()
        self.last_api_at = None
        self.last_history_at = None
        self.last_api = 0.0
        self.last_send = monotonic()
        self.category = 0
        self.pending_result = None
        self.read_pause_until = 0.0
        self.card_pairs = None if card_pairs is None else dict(card_pairs)
        self.card_executor = None
        self.card_operation = None
        self.next_card_scan = 0.0

    def start(self):
        self.thread = Thread(target=self.run, name="messaging-delivery", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=35)
        if self.thread and self.thread.is_alive():
            raise RuntimeError("Messaging loop did not stop")
        if self.card_executor:
            self.card_executor.shutdown(wait=True)
            if self.card_operation is not None:
                try:
                    with psycopg.connect(
                        self.database_url, autocommit=True, row_factory=dict_row, connect_timeout=2
                    ) as db:
                        self.finish_card(db)
                except psycopg.Error:
                    emit("card_shutdown_commit_unavailable")
        self.adapter.close()

    def healthy(self):
        return self.thread is not None and self.thread.is_alive() and monotonic() - self.last_tick < 45

    @contextmanager
    def transaction(self, db):
        with db.transaction():
            with db.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout='2s'")
                cur.execute("SET LOCAL lock_timeout='1s'")
                yield cur

    def run(self):
        while not self.stop_event.is_set():
            try:
                with psycopg.connect(self.database_url, autocommit=True, row_factory=dict_row, connect_timeout=2) as db:
                    while not self.stop_event.is_set():
                        self.last_tick = monotonic()
                        owned = db.execute("SELECT pg_try_advisory_lock(%s) AS owned", (LOCK_ID,)).fetchone()["owned"]
                        if owned:
                            break
                        self.stop_event.wait(1)
                    if self.stop_event.is_set():
                        return
                    self.last_send = monotonic()
                    with self.transaction(db) as cur:
                        cur.execute("""UPDATE messaging.messages SET state=CASE WHEN provider_message_id IS NULL AND first_attempt_at>now()-interval '24 hours' THEN 'pending' ELSE 'uncertain' END,error_code='restart_unknown',
                          updated_at=now() WHERE direction='outbound' AND state='sending'""")
                    if self.pending_result is not None:
                        self.save_result(db)
                    while not self.stop_event.is_set():
                        self.last_tick = monotonic()
                        if monotonic() - self.last_api < 1.1:
                            self.stop_event.wait(0.2)
                            continue
                        self.turn(db)
                        self.stop_event.wait(0.2)
            except (psycopg.Error, OSError):
                emit("loop_database_unavailable")
                self.last_tick = monotonic()
                self.stop_event.wait(1)
            except Exception:
                # No raw exception: provider errors may contain contacts/URLs.
                emit("loop_failed")
                return

    def pause_reads(self, db, response):
        code = "rate_limited" if response.status_code == 429 else "account_configuration"
        delay = retry_after(response) if response.status_code == 429 else 60
        self.read_pause_until = monotonic() + delay
        with self.transaction(db) as cur:
            cur.execute(
                """UPDATE messaging.messages SET error_code=%s,
                next_action_at=greatest(next_action_at,now()+make_interval(secs => %s))
                WHERE id=(SELECT id FROM messaging.messages WHERE account_ref=%s AND direction='outbound'
                ORDER BY queue_sequence DESC LIMIT 1)""",
                (code, delay, self.account),
            )

    def turn(self, db):
        # Result storage is independent of reply state and survives reconnects.
        if self.card_operation is not None and self.card_operation[1].done():
            self.finish_card(db)
        if monotonic() < self.read_pause_until:
            return
        with self.transaction(db) as cur:
            paused = cur.execute(
                """SELECT 1 FROM messaging.messages WHERE account_ref=%s
                AND error_code IN ('account_configuration','rate_limited') AND next_action_at>now() LIMIT 1""",
                (self.account,),
            ).fetchone()
        if paused:
            return
        if self.card_pairs is not None:
            try:
                with self.transaction(db) as cur:
                    card_pause = cur.execute(
                        """SELECT 1 FROM messaging.contact_cards WHERE account_ref=%s
                        AND error_code IN ('account_configuration','rate_limited','quota')
                        AND next_action_at>now() LIMIT 1""",
                        (self.account,),
                    ).fetchone()
                if card_pause:
                    return
            except psycopg.Error:
                emit("card_pause_unavailable")
        self.category = (self.category + 1) % 3
        if self.category == 0:
            if self.history_turn(self, db):
                self.last_api = monotonic()
                with self.transaction(db) as cur:
                    counts = cur.execute(
                        "SELECT state,count(*) AS count FROM messaging.messages WHERE direction='outbound' GROUP BY state"
                    ).fetchall()
                emit(
                    "queue_snapshot",
                    counts={r["state"]: r["count"] for r in counts},
                    last_api_at=self.last_api_at,
                    last_history_at=self.last_history_at,
                )
            return
        if self.category == 1:
            with self.transaction(db) as cur:
                job = cur.execute(
                    """SELECT * FROM messaging.messages WHERE direction='outbound'
                    AND account_ref=%s AND provider_message_id IS NOT NULL
                    AND state IN ('provider_queued','uncertain') AND next_action_at<=now()
                    ORDER BY next_action_at,queue_sequence LIMIT 1""",
                    (self.account,),
                ).fetchone()
            if job is None:
                self.card_turn(db)
                return
            api_started = monotonic()
            result = self.adapter.status(job)
        else:
            if monotonic() - self.last_send < 1.1:
                return
            with self.transaction(db) as cur:
                job = claim(cur, self.account)
            if job is None:
                self.card_turn(db)
                return
            emit(
                "claim",
                message_id=job["id"],
                attempt_id=job["attempt_id"],
                queue_age_seconds=(datetime.now(timezone.utc) - job["created_at"]).total_seconds(),
            )
            api_started = monotonic()
            result = self.adapter.send(job)
            self.last_send = monotonic()
        self.last_api = monotonic()
        if result.error_code not in ("transport_unknown", "read_unavailable"):
            self.last_api_at = datetime.now(timezone.utc)
        emit(
            "provider_request_complete",
            message_id=job["id"],
            attempt_id=job["attempt_id"],
            duration_ms=round((monotonic() - api_started) * 1000, 2),
            state=result.state,
        )
        if result.state == "uncertain" and result.provider_id is None:
            result = SendResult("pending", error_code=result.error_code, retry_after=result.retry_after)
        self.pending_result = (job, result)
        self.save_result(db)

    def card_turn(self, db):
        if self.card_pairs is None or self.card_operation is not None:
            return
        from .cards import enqueue, claim_card

        try:
            with self.transaction(db) as cur:
                # Normal sends/status work, including per-route blocked work,
                # takes priority. Cards reserve the same API/send pacing slot.
                if cur.execute(
                    """SELECT 1 FROM messaging.messages m WHERE account_ref=%s AND direction='outbound'
                    AND next_action_at<=now() AND (
                      (provider_message_id IS NOT NULL AND state IN ('uncertain','provider_queued'))
                      OR (state='pending' AND NOT EXISTS (
                        SELECT 1 FROM messaging.messages p WHERE p.user_id=m.user_id AND p.account_ref=m.account_ref
                        AND p.conversation_id=m.conversation_id AND p.queue_sequence<m.queue_sequence
                        AND p.direction='outbound' AND p.state NOT IN ('sent','failed')))) LIMIT 1""",
                    (self.account,),
                ).fetchone():
                    return
                if cur.execute(
                    """SELECT 1 FROM messaging.messages WHERE account_ref=%s AND direction='outbound'
                    AND error_code IN ('quota','account_configuration','rate_limited') AND next_action_at>now() LIMIT 1""",
                    (self.account,),
                ).fetchone():
                    return
                if monotonic() - self.last_send < 1.1:
                    return
                if monotonic() >= self.next_card_scan:
                    enqueue(cur, self.account, self.card_pairs)
                    self.next_card_scan = monotonic() + 60
                job = claim_card(cur, self.account, self.card_pairs)
            if job is None:
                return
            if self.card_executor is None:
                self.card_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="messaging-card")
            operation = self.adapter.card_status if job["provider_message_id"] else self.adapter.send_card
            future = self.card_executor.submit(operation, job)
            self.card_operation = (job, future)
            self.last_api = self.last_send = monotonic()
            emit("card_claim", card_id=job["id"], attempt_id=job["attempt_id"])
        except Exception:
            emit("card_work_unavailable")

    def finish_card(self, db):
        from .cards import record_card_result

        job, future = self.card_operation
        try:
            result = future.result()
        except Exception:
            result = SendResult(
                "uncertain", job["provider_message_id"], "card_operation_unknown", response_body=job["response_body"]
            )
        # No-ID unknown may only retry the original persisted payload/key.
        if result.state == "uncertain" and result.provider_id is None:
            result = SendResult("pending", error_code=result.error_code)
        try:
            with self.transaction(db) as cur:
                applied = record_card_result(cur, job, result)
            emit("card_result", card_id=job["id"], state=result.state, error_code=result.error_code, applied=applied)
            self.card_operation = None
        except psycopg.Error:
            # Retain this completed future; don't turn a known outcome into POST.
            emit("card_result_commit_retry", card_id=job["id"])

    def save_result(self, db):
        # Keep known outcomes across database reconnections; never re-POST them.
        job, result = self.pending_result
        while not self.stop_event.is_set():
            try:
                with self.transaction(db) as cur:
                    applied = record_send_result(cur, job["id"], job["attempt_id"], result)
                emit(
                    "send_result",
                    message_id=job["id"],
                    attempt_id=job["attempt_id"],
                    state=result.state,
                    error_code=result.error_code,
                    applied=applied,
                )
                self.pending_result = None
                if result.state == "sent":
                    self.next_card_scan = 0.0
                return
            except psycopg.Error:
                emit("result_commit_retry", message_id=job["id"], attempt_id=job["attempt_id"])
                if db.closed or db.broken:
                    raise
                self.last_tick = monotonic()
                self.stop_event.wait(1)
