"""Lifecycle and bounded signed intake for the existing FastAPI application."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import re
from time import monotonic
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row
from fastapi import Request

from tbd_server.user.store import Problem
from .adapter import Comms, signed_event
from .core import receive_message
from .runtime import DeliveryLoop, emit


class Messaging:
    def __init__(self, store, api_key, secret, account, activated_at, *, pilot_conversations=None):
        if not api_key or not secret or not re.fullmatch(r"\+[1-9][0-9]{7,14}", account):
            raise ValueError("Messaging credentials and E.164 number required")
        self.activated_at = datetime.fromisoformat(activated_at.replace("Z", "+00:00"))
        if self.activated_at.utcoffset() is None:
            raise ValueError("Messaging activation must include timezone")
        if pilot_conversations is None:
            pilot_conversations = {}
        if not isinstance(pilot_conversations, dict) or any(
            not isinstance(conversation, str)
            or not 0 < len(conversation.encode()) <= 256
            or not isinstance(phone, str)
            or not re.fullmatch(r"\+[1-9][0-9]{7,14}", phone)
            for conversation, phone in pilot_conversations.items()
        ):
            raise ValueError("Pilot conversation/phone allowlist is invalid")
        self.pilot_conversations = dict(pilot_conversations)
        self.store, self.secret, self.account = store, secret, account
        self.adapter = Comms(api_key)
        self.loop = DeliveryLoop(store.database_url, self.adapter, account, self.history_turn)
        self.next_sweep = 0.0
        # httpx INFO URLs include history phone query params. Disable those logs.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    def verify_schema(self):
        with self.store.transaction() as cur:
            cur.execute("SELECT id,attempt_id,next_action_at FROM messaging.messages LIMIT 0")
            if not cur.execute(
                "SELECT has_table_privilege(current_user,'messaging.messages','INSERT') AS ok"
            ).fetchone()["ok"]:
                raise Problem(503, "Messaging schema permissions missing")
            if not cur.execute(
                "SELECT has_column_privilege(current_user,'messaging.messages','state','UPDATE') AS ok"
            ).fetchone()["ok"]:
                raise Problem(503, "Messaging delivery permissions missing")

    def receive(self, event, request_id=None):
        start = monotonic()
        if not self.store.connections.acquire(timeout=0.1):
            raise Problem(503, "Messaging storage busy")
        try:
            with psycopg.connect(self.store.database_url, connect_timeout=1, row_factory=dict_row) as db:
                db.execute("SET LOCAL statement_timeout='500ms'")
                db.execute("SET LOCAL lock_timeout='500ms'")
                row = receive_message(db.cursor(), event, self.activated_at)
                if monotonic() - start > 3:
                    raise Problem(503, "Messaging storage deadline")
            emit(
                "receipt_commit",
                request_id=request_id,
                message_id=row["id"] if row else None,
                replayed=row.get("replayed", False) if row else False,
                duration_ms=round((monotonic() - start) * 1000, 2),
            )
            return row
        except psycopg.Error:
            raise Problem(503, "Messaging storage unavailable or outcome unknown") from None
        finally:
            self.store.connections.release()

    async def webhook(self, request: Request):
        request_id = str(uuid4())
        emit("receipt", request_id=request_id)
        try:
            async with asyncio.timeout(4):
                chunks = bytearray()
                async for chunk in request.stream():
                    chunks.extend(chunk)
                    if len(chunks) > 512 * 1024:
                        raise Problem(413, "Webhook body too large")
                event = signed_event(bytes(chunks), request.headers.get("x-osis-signature", ""), self.secret)
                if event["event"] != "comms.message.received":
                    if event["event"] == "comms.ping":
                        emit("webhook_ping_verified", request_id=request_id)
                    return {"received": True}
                if not isinstance(event.get("data"), dict):
                    raise Problem(422, "Invalid event data")
                normalized = self.adapter.normalize(
                    event["data"].get("message"), self.account, pilot_conversations=self.pilot_conversations
                )
                if normalized is not None:
                    await asyncio.to_thread(self.receive, normalized, request_id)
                return {"received": True}
        except TimeoutError:
            emit("receipt_timeout", request_id=request_id)
            raise Problem(503, "Messaging deadline; retry safely") from None

    def history_turn(self, loop, db=None):
        if monotonic() < self.next_sweep:
            return False
        try:
            records = self.adapter.history(since=self.activated_at.isoformat())
            loop.last_api_at = datetime.now(timezone.utc)
            if len(records) >= 200:
                emit("history_coverage_capped", count=len(records))
            for record in reversed(records):
                try:
                    event = self.adapter.normalize(
                        record, self.account, history=True, pilot_conversations=self.pilot_conversations
                    )
                    if event is not None:
                        self.receive(event)
                except Problem:
                    emit("history_record_rejected")
            self.finish_sweep(True)
        except httpx.HTTPStatusError as exc:
            if db is not None and exc.response.status_code in (401, 403, 429):
                loop.pause_reads(db, exc.response)
            emit("history_unavailable")
            self.finish_sweep(False)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            emit("history_unavailable")
            self.finish_sweep(False)
        return True

    def finish_sweep(self, success):
        self.next_sweep = monotonic() + 60
        if success:
            self.loop.last_history_at = datetime.now(timezone.utc)
            emit("history_sweep_complete")

    @asynccontextmanager
    async def lifespan(self, app):
        self.verify_schema()
        self.loop.start()
        try:
            yield
        finally:
            await asyncio.to_thread(self.loop.stop)
