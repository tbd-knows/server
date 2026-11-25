"""Bounded Agent workers. No DB connection or lock survives into inference."""

import asyncio
from contextlib import contextmanager
from threading import Event, Thread
from time import monotonic
from uuid import uuid4

import psycopg
from pydantic import ValidationError
from psycopg.rows import dict_row

from tbd_server.messaging.runtime import emit
from tbd_server.user.store import Store

from . import harness, memory, policy
from .adapter import Adapter, Generation, InferenceRequest
from .store import HEAD, approved, current_state, finish, lock_user, now, route


def execution_config(model, *, adapter="openai-responses-v1"):
    return {
        "model": model,
        "adapter": adapter,
        "prompt": policy.POLICY_VERSION,
        "context": harness.CONTEXT_VERSION,
        "schema": harness.SCHEMA_VERSION,
        "store": False,
        "attempts": 2,
        "deadline": 24,
        "lease": 10,
        "max_output": 1200,
        "prompt_bytes": harness.PROMPT_BYTES,
        "skew": harness.SKEW.total_seconds(),
        "brief_age": harness.BRIEF_AGE.total_seconds(),
    }


class AgentRunner:
    def __init__(self, database_url, account, pairs, config, adapter: Adapter | None = None):
        self.database_url, self.account, self.pairs = database_url, account, dict(pairs)
        self.config, self.adapter = config, adapter
        self.stopped = Event()
        self.threads = []

    def start(self):
        for index in range(2):
            thread = Thread(target=self.run, name=f"agent-turn-{index}", daemon=True)
            self.threads.append(thread)
            thread.start()

    def stop(self):
        self.stopped.set()
        for worker in self.threads:
            worker.join(timeout=self.config["lease"] + 4)

    @contextmanager
    def transaction(self):
        with psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=2) as db:
            db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            db.execute("SET LOCAL statement_timeout='2s'")
            db.execute("SET LOCAL lock_timeout='500ms'")
            yield db.cursor()

    def run(self):
        while not self.stopped.is_set():
            try:
                progressed = self.step()
            except (psycopg.Error, OSError):
                emit("agent_storage_unavailable")
                progressed = False
            except Exception:
                emit("agent_worker_error")
                progressed = False
            if not progressed:
                self.stopped.wait(0.5)

    def still_enrolled(self, user, state, turn):
        return (
            user
            and user["joined"]
            and user["phone"] == turn["peer_ref"]
            and state
            and state["mode"] in ("agent", "fallback")
            and state["enrollment_id"] == turn["enrollment_id"]
            and route(state) == route(turn)
            and approved(turn, self.account, self.pairs)
        )

    def step(self):
        started = monotonic()
        with self.transaction() as cur:
            # Inspect only each User's head. The prior-turn predicate is
            # independent of leases, so a live head keeps later work fenced.
            candidate = cur.execute(
                """SELECT m.user_id FROM agent_service.turns t
                JOIN messaging.messages m ON m.id=t.inbound_id
                LEFT JOIN agent_service.user_state s ON s.user_id=m.user_id
                WHERE t.outcome IS NULL AND
                  (t.lease_until IS NULL OR t.lease_until<=clock_timestamp() OR s.mode <> 'agent')
                AND NOT EXISTS (
                  SELECT 1 FROM messaging.messages earlier JOIN agent_service.turns p ON p.inbound_id=earlier.id
                  WHERE earlier.user_id=m.user_id AND earlier.queue_sequence<m.queue_sequence AND p.outcome IS NULL
                )
                ORDER BY m.queue_sequence LIMIT 1"""
            ).fetchone()
            if not candidate:
                return False
            user, state, turn, at = self.read_head(cur, candidate["user_id"])
            if not turn:
                return True
            if not self.still_enrolled(user, state, turn):
                finish(cur, turn, state, "revoked")
                return True
            if turn["lease_until"] and turn["lease_until"] > at and state["mode"] == "agent":
                return False
            if turn["granted_at"] is not None:
                emit("agent_recovered_grant", inbound_id=turn["id"], attempt_id=turn["agent_attempt"])
            settled, reason = self.local(cur, turn, user, state, at)
            if not settled:
                token = uuid4()
                cur.execute(
                    """UPDATE agent_service.turns SET attempt_id=%s,granted_at=NULL,
                    lease_until=least(inference_deadline,clock_timestamp()+make_interval(secs => %s)),
                    attempts_left=attempts_left-1 WHERE inbound_id=%s""",
                    (token, self.config["lease"], turn["id"]),
                )
        if settled:
            emit(
                "agent_local_completion",
                inbound_id=turn["id"],
                outcome=settled,
                reason=reason,
                queue_ms=round((at - turn["created_at"]).total_seconds() * 1000, 2),
                completion_ms=round((monotonic() - started) * 1000, 2),
            )
            return True

        context_started = monotonic()
        # Assemble and grant in separate transactions to recheck all fences
        # after context work. An unconfirmed grant never reaches the provider.
        with self.transaction() as cur:
            projection = harness.context(cur, turn, state)
        if projection.prompt is None:
            self.complete(turn, token, Generation(), projection)
            return True
        granted = monotonic()
        with self.transaction() as cur:
            user, state, head, at = self.read_head(cur, turn["user_id"])
            if not self.valid_attempt(user, state, head, turn, token, at) or head["granted_at"]:
                return False
            cur.execute("UPDATE agent_service.turns SET granted_at=%s WHERE inbound_id=%s", (at, turn["id"]))
            remaining = (min(head["lease_until"], head["inference_deadline"]) - at).total_seconds()

        timeout = remaining - (monotonic() - granted)
        if timeout <= 0 or self.stopped.is_set():
            return True
        emit("agent_dispatch", inbound_id=turn["id"], attempt_id=token, config=self.config)
        inference_started = monotonic()

        request = InferenceRequest(
            self.config["model"], harness.INSTRUCTIONS, projection.prompt,
            harness.Result.model_json_schema(), self.config["max_output"],
        )

        async def bounded():
            async with asyncio.timeout(timeout):
                return await self.adapter(request, timeout)

        try:
            result = asyncio.run(bounded())
        except TimeoutError:
            result = Generation(retryable=True, reason="provider_timeout")
        except Exception:
            result = Generation(retryable=True, reason="adapter_error")
        inferred = monotonic()
        emit(
            "agent_inference",
            inbound_id=turn["id"],
            attempt_id=token,
            config=self.config,
            queue_ms=round((at - turn["created_at"]).total_seconds() * 1000, 2),
            claim_ms=round((context_started - started) * 1000, 2),
            context_ms=round((granted - context_started) * 1000, 2),
            inference_ms=round((inferred - inference_started) * 1000, 2),
            usage=result.usage,
            reason=result.reason,
        )
        self.complete(turn, token, result, projection)
        return True

    @staticmethod
    def read_head(cur, user_id):
        user = lock_user(cur, user_id)
        return user, current_state(cur, user_id), cur.execute(HEAD, (user_id,)).fetchone(), now(cur)

    def valid_attempt(self, user, state, head, turn, token, at):
        return (
            head
            and head["id"] == turn["id"]
            and head["agent_attempt"] == token
            and self.still_enrolled(user, state, head)
            and state["mode"] == "agent"
            and head["lease_until"] > at
            and head["inference_deadline"] > at
            and head["execution_config"] == self.config
        )

    def local(self, cur, turn, user, state, at):
        text = turn["text"]
        fresh = harness.fresh(turn, state)
        reason = None
        if harness.SECRET.search(text):
            outcome, reply = "blocked", harness.SENSITIVE
        elif not fresh:
            outcome, reply = "stale", harness.RESTATEMENT
        elif state["mode"] == "fallback":
            reason = "operator_fallback"
            outcome, reply = (
                ("control", "Preference controls are temporarily unavailable.")
                if memory.is_memory_input(text)
                else ("fallback", harness.FALLBACK)
            )
        else:
            action = policy.unsupported_action(text)
            if harness.withdrawn_task_correction(text):
                outcome, reply = "control", harness.KEEP_BRIEF + (" " + harness.TRANSACTION if action else "")
            elif control := memory.memory_control(text, state, turn):
                outcome, reply = "control", control + (" " + harness.TRANSACTION if action else "")
            elif policy.close_requested(text):
                reply = harness.CLOSED if state["brief"] is not None else harness.NO_BRIEF
                state["brief"] = None
                outcome, reply = "control", reply + (" " + harness.TRANSACTION if action else "")
            else:
                outcome, reply, reason = self.local_reply(cur, turn, user, state, at, action)
                if outcome is None:
                    return None, None
        finish(cur, turn, state, outcome, reply, fresh=fresh)
        return outcome, reason

    def local_reply(self, cur, turn, user, state, at, action):
        text = turn["text"]
        if action:
            outcome, reply, reason = "transaction", harness.TRANSACTION, None
        elif profile := policy.profile_request(text):
            if profile == "edit":
                reply = "Your display name is managed in your TBD account."
            else:
                name = Store.display_name(cur, user["id"])
                reply = f"User Service lists your TBD account display name as {name}."
            outcome, reason = "control", None
        elif reply := policy.direct_reply(text):
            outcome, reason = "control", None
        elif policy.bare_link(text):
            outcome, reply, reason = "control", harness.LINK_DETAILS, None
        else:
            outcome, reply, reason = "fallback", harness.FALLBACK, None
            if at >= turn["inference_deadline"]:
                reason = "inference_deadline"
            elif not turn["attempts_left"]:
                reason = "attempts_exhausted"
            elif not self.adapter or turn["execution_config"] != self.config:
                reason = "inference_unavailable"
            else:
                return None, None, None

        if state["brief"] is not None or outcome == "fallback":
            active = harness.brief_in_use(cur, turn, state)
            prior = (
                harness.previous_turns(cur, turn)
                if active and harness.ISOLATED.fullmatch(text.strip().rstrip(".!?")) else ()
            )
            scope = harness.resolve_scope(turn, state, active, prior)
            if scope.retire:
                state["brief"] = None
            if outcome == "fallback" and scope.clarification:
                reply, reason = scope.clarification, "scope_restatement"
        return outcome, reply, reason

    def complete(self, turn, token, result, projection):
        started = monotonic()
        outcome = "fallback"
        scope = projection.scope
        local = projection.prompt is None
        brief = scope.brief
        reply = (scope.clarification or harness.RESTATEMENT) if local else harness.FALLBACK
        target = None
        brief_field = None
        reason = "context_restatement" if local else result.reason or "provider_unreadable"
        if result.output is not None:
            try:
                reply, brief, target = harness.validate(
                    result.output,
                    turn,
                    brief,
                    scope.answer,
                    require_gift_size=projection.require_gift_size,
                    prompt=projection.prompt,
                )
                outcome, reason = "model", None
            except ValidationError as exc:
                errors = exc.errors(include_input=False, include_context=False, include_url=False)
                reason = "output_invalid_json" if errors[0]["type"] == "json_invalid" else "schema_validation"
            except harness.BriefEvidenceError as exc:
                reason, brief_field = "brief_evidence", exc.field
                reply = harness.CLARIFY_BRIEF
            except harness.BriefValidationError:
                reason = "brief_validation"
            except harness.INVALID_RESULT:
                reason = "reply_validation"
        try:
            with self.transaction() as cur:
                user, state, head, at = self.read_head(cur, turn["user_id"])
                if not self.valid_attempt(user, state, head, turn, token, at) or (not local and not head["granted_at"]):
                    return False
                if result.retryable and head["attempts_left"]:
                    cur.execute(
                        """UPDATE agent_service.turns SET attempt_id=NULL,lease_until=NULL,granted_at=NULL
                        WHERE inbound_id=%s""",
                        (turn["id"],),
                    )
                    return False
                if outcome == "model" and (not scope.retire or brief != scope.brief):
                    state["brief"] = brief
                elif scope.retire:
                    state["brief"] = None
                finish(cur, head, state, outcome, reply, fresh=True, question=target)
        except psycopg.Error:
            # The connection may have been lost after COMMIT. Never repeat an
            # inference to find out. Reread durable state on a new connection.
            with self.transaction() as cur:
                stored = cur.execute("SELECT outcome FROM agent_service.turns WHERE inbound_id=%s", (turn["id"],))
                stored = stored.fetchone()
            emit("agent_commit_reconciled", inbound_id=turn["id"], attempt_id=token, outcome=stored["outcome"])
            return stored["outcome"] is not None
        emit(
            "agent_completion",
            inbound_id=turn["id"],
            attempt_id=token,
            outcome=outcome,
            reason=reason,
            brief_field=brief_field,
            commit_ms=round((monotonic() - started) * 1000, 2),
        )
        return True
