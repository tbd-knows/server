"""Synthetic context and authority checks with no provider egress."""

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest

from tbd_server.agent import harness


def turn(text):
    now = datetime.now(timezone.utc)
    return {
        "id": uuid4(),
        "text": text,
        "source_at": now,
        "created_at": now,
        "queue_sequence": 3,
        "user_id": uuid4(),
        "account_ref": "+12125550100",
        "peer_ref": "+12125550101",
        "conversation_id": "example",
        "enrollment_id": uuid4(),
    }


def state(inbound):
    return {
        "brief": None,
        "preferences": {},
        "paused": False,
        "enrolled_at": inbound["source_at"] - timedelta(days=1),
        "watermark": None,
        "reset_source_at": None,
        "reset_sequence": None,
    }


def generated(reply="Here's what fits.", brief="unchanged", edits=(), question=None):
    return json.dumps({"reply": reply, "brief": brief, "edits": list(edits), "question": question})


def test_explicit_preference_writes_are_scoped_and_pause_resets_context():
    inbound = turn("Remember size for shoes for me is 8 wide")
    context = state(inbound)
    assert harness.memory_control(inbound["text"], context, inbound) == "I saved that standing preference."
    assert context["preferences"]["shoes|me|size"]["value"] == "8 wide"
    assert harness.memory_control("My seller said “forget size for shoes for me”", context, inbound) is None
    no_match = harness.memory_control("forget material for hat for me; compare these", context, inbound)
    assert "no saved preference" in no_match and "resend" in no_match
    assert context["reset_sequence"] == inbound["queue_sequence"]
    assert not harness.after_reset(inbound, context)
    assert harness.memory_control("pause memory", context, inbound) == "Preference memory is paused."
    assert "paused" in harness.memory_control("save size for shoes for me is 9", context, inbound)
    assert "8 wide" in harness.memory_control("show my preferences", context, inbound)
    harness.memory_control("forget size for shoes for me", context, inbound)
    assert not context["preferences"]


@pytest.mark.parametrize("source_shift,created_shift,expected", [(5, 5, True), (1, 5, False), (5, -1, False)])
def test_both_times_must_show_reply_before_typed_answer(source_shift, created_shift, expected):
    inbound = turn("9")
    active = state(inbound)
    active["brief"] = {
        "id": "opening",
        "fields": {"item": {"value": "shoes"}},
        "options": [],
        "activity_at": (inbound["source_at"] - timedelta(minutes=2)).isoformat(),
    }
    parent = {
        "text": "Compare shoes",
        "outcome": "model",
        "source_at": inbound["source_at"] - timedelta(minutes=1),
        "queue_sequence": 2,
        "reply": "What size should I use?",
        "question": {"brief_id": "opening", "field": "size", "value": None},
        "sent_observed_at": inbound["source_at"],
    }
    inbound["source_at"] += timedelta(seconds=source_shift)
    inbound["created_at"] += timedelta(seconds=created_shift)
    cur = Mock()
    cur.execute.return_value.fetchall.return_value = [parent]
    prompt, _, answer, _ = harness.context(cur, inbound, active)
    assert bool(prompt) == expected
    assert answer == ({"size": "9"} if expected else None)


def test_intervening_turn_blocks_a_previously_visible_question():
    inbound = turn("yes")
    active = state(inbound)
    active["brief"] = {
        "id": "opening",
        "fields": {"item": {"value": "shoes"}},
        "options": [],
        "activity_at": (inbound["source_at"] - timedelta(minutes=2)).isoformat(),
    }
    parent = {
        "text": "Compare shoes",
        "outcome": "model",
        "source_at": inbound["source_at"] - timedelta(minutes=1),
        "queue_sequence": 1,
        "reply": "Is 8 the right size?",
        "question": {"brief_id": "opening", "field": "size", "value": "8"},
        "sent_observed_at": inbound["source_at"] - timedelta(seconds=5),
    }
    cur = Mock()
    cur.execute.return_value.fetchall.return_value = [{**parent, "outcome": "control", "queue_sequence": 2}, parent]
    assert harness.context(cur, inbound, active)[0] is None


@pytest.mark.parametrize(
    "text",
    [
        "My CVV is 123, remember size for shoes for me is 8",
        "password: fictional-secret",
        "otp 123456",
        "4111 1111 1111 1111",
        "api key=example",
    ],
)
def test_recognized_secrets_block_whole_text(text):
    assert harness.SECRET.search(text)


def test_full_prompt_budget_and_optional_context_trimming():
    inbound = turn("Compare these shoes")
    cur = Mock()
    cur.execute.return_value.fetchall.return_value = [
        {
            "text": "Z" * 20000,
            "outcome": "fallback",
            "source_at": inbound["source_at"] - timedelta(seconds=5),
            "queue_sequence": 1,
            "sent_observed_at": None,
            "reply": "Exclude me",
        }
    ]
    prompt, _, _, _ = harness.context(cur, inbound, state(inbound))
    assert json.loads(prompt)["recent"] == []
    inbound["text"] *= harness.PROMPT_BYTES
    assert harness.context(cur, inbound, state(inbound))[0] is None


def test_scoped_preferences_and_gift_exception():
    inbound = turn("Compare these shoes for my sister")
    active = state(inbound)
    active["brief"] = {
        "id": "opening",
        "fields": {"item": {"value": "shoes"}},
        "options": [],
        "activity_at": (inbound["source_at"] - timedelta(minutes=2)).isoformat(),
    }
    active["watermark"] = inbound["source_at"] - timedelta(minutes=2)
    active["preferences"] = {
        "shoes|me|size": {"value": "8"},
        "shoes|sister|material": {"value": "cotton"},
        "hats|sister|size": {"value": "small"},
    }
    cur = Mock()
    cur.execute.return_value.fetchall.return_value = []
    inbound["text"] += " and tell me what fits"
    prompt, _, _, _ = harness.context(cur, inbound, active)
    assert json.loads(prompt)["brief"] is None
    assert json.loads(prompt)["preferences"] == [
        {"item": "shoes", "recipient": "sister", "key": "material", "value": "cotton"},
    ]
    active["brief"]["fields"]["recipient"] = {"value": "my sister"}
    prompt, _, _, _ = harness.context(cur, inbound, active)
    assert json.loads(prompt)["brief"]["id"] == "opening"
    inbound["text"] = "Which shoes would be warmer?"
    prompt, _, _, _ = harness.context(cur, inbound, active)
    assert [p["recipient"] for p in json.loads(prompt)["preferences"]] == ["sister"]


def test_model_edits_require_current_evidence_and_distinguish_option_price():
    inbound = turn("Compare shoes. Seller A asks $80. My budget is $65.")
    raw = generated(
        brief="update",
        edits=[
            {"field": "item", "value": "shoes"},
            {"field": "option", "value": "Seller A asks $80"},
            {"field": "budget", "value": "$65"},
        ],
        question={"field": "size", "value": None},
    )
    reply, brief, question = harness.validate(raw, inbound, None, None)
    assert "What size" in reply
    assert brief["fields"]["budget"] == {"value": "$65", "source": str(inbound["id"])}
    assert question["brief_id"] == brief["id"]
    for invalid in (
        raw.replace('"$65"', '"$80"'),
        raw.replace("Here's what fits.", "I saved that preference."),
        raw.replace("Here's what fits.", "What is your budget?"),
        raw.replace('"shoes"', '"boots"'),
    ):
        with pytest.raises(harness.INVALID_RESULT):
            harness.validate(invalid, inbound, None, None)

    follow_up = turn("9")
    raw = generated(brief="update", edits=[{"field": "size", "value": "9"}])
    with pytest.raises(harness.INVALID_RESULT):
        harness.validate(raw, follow_up, brief, None)
    _, updated, _ = harness.validate(raw, follow_up, brief, {"size": "9"})
    assert updated["fields"]["size"] == {"value": "9", "source": str(follow_up["id"])}


@pytest.mark.parametrize(
    "status,output_status,retryable,usage",
    [
        (200, "completed", False, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}),
        (200, "incomplete", False, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}),
        (429, None, True, None),
    ],
)
def test_thin_adapter_stateless_request_and_usage(monkeypatch, status, output_status, retryable, usage):
    import asyncio

    import httpx

    from tbd_server.agent import adapter

    def handler(request):
        assert request.url.path == "/v1/responses"
        data = json.loads(request.content)
        assert set(data) == {"model", "instructions", "input", "store", "max_output_tokens", "text"}
        assert data["model"] == "synthetic-model" and data["store"] is False
        assert data["text"]["format"]["strict"] is True
        return httpx.Response(
            status,
            json={
                "status": output_status,
                "usage": usage,
                "output": [{"type": "message", "content": [{"type": "output_text", "text": generated()}]}],
            },
        )

    client = httpx.AsyncClient

    def controlled(**kwargs):
        assert kwargs["trust_env"] is False
        return client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(adapter.httpx, "AsyncClient", controlled)
    result = asyncio.run(adapter.OpenAI("synthetic")("synthetic-model", "synthetic message", 100, 1))
    assert result.retryable is retryable and result.usage == usage
    assert bool(result.output) == (output_status == "completed")


@pytest.mark.parametrize(
    "field,text,user_value,seller_value,listing",
    [
        ("budget", "My budget is $65 and the seller asks $80.", "$65", "$80", "the seller asks $80"),
        ("budget", "My budget is $65, but the seller asks $80.", "$65", "$80", "the seller asks $80"),
        ("size", "Listing A is size 8 but I need size 10.", "10", "8", "Listing A is size 8"),
        ("size", "The listing says “I need size 8”. My size is 10.", "10", "8", "I need size 8"),
        ("condition", "Seller describes it as new. I prefer used.", "used", "new", "Seller describes it as new"),
    ],
)
def test_first_party_constraints_outweigh_listing_values(field, text, user_value, seller_value, listing):
    inbound = turn("Compare shoes. " + text)
    edits = [{"field": "item", "value": "shoes"}, {"field": "option", "value": listing}]
    bad = generated(brief="update", edits=[*edits, {"field": field, "value": seller_value}])
    with pytest.raises(harness.BriefEvidenceError):
        harness.validate(bad, inbound, None, None)
    good = generated(brief="update", edits=[*edits, {"field": field, "value": user_value}])
    _, brief, _ = harness.validate(good, inbound, None, None)
    assert brief["fields"][field]["value"] == user_value
    assert brief["options"][0]["value"] == listing
    assert brief["activity_at"] == inbound["source_at"].isoformat()


@pytest.mark.parametrize(
    "text",
    [
        "Compare shoes. Listing A is size 8.",
        "Compare shoes. Seller A:\nSize 8.",
        "Compare the shoes the seller lists in size 8.",
        "Compare shoes. The seller wrote “My size is 8”.",
        "Compare shoes. My size is 8. I need size 10.",
        "Compare shoes. I don't need size 8.",
    ],
)
def test_unsupported_or_conflicting_size_needs_clarification(text):
    raw = generated(brief="update", edits=[{"field": "size", "value": "8"}])
    brief = {"id": "opening", "fields": {"item": {"value": "shoes"}}, "options": []}
    with pytest.raises(harness.BriefEvidenceError):
        harness.validate(raw, turn(text), brief, None)


@pytest.mark.parametrize(
    "command",
    [
        "Forget size for shoes for me; actually, keep that preference",
        "Forget size for shoes for me but keep that preference",
        "Pause memory. Never mind, resume memory.",
        "Forget size for shoes for me; compare these shoes, but keep that preference",
    ],
)
def test_conflicting_memory_command_preserves_preference_brief_and_cutoffs(command):
    from copy import deepcopy

    inbound = turn(command)
    active = state(inbound)
    active["preferences"] = {"shoes|me|size": {"value": "8"}}
    active["brief"] = {"id": "opening", "fields": {"item": {"value": "shoes"}}, "options": []}
    active["reset_sequence"] = 2
    active["reset_source_at"] = inbound["source_at"] - timedelta(minutes=2)
    before = deepcopy(active)
    assert "confirm" in harness.memory_control(command, active, inbound)
    assert active == before


@pytest.mark.parametrize("reply", ["I placed the order for you.", "I’ve placed your order.", "I bought it for you."])
def test_false_action_confirmations_are_rejected(reply):
    with pytest.raises(ValueError, match="Invalid reply"):
        harness.validate(generated(reply=reply), turn("Compare these shoes"), None, None)
    assert harness.validate(
        generated(reply="I would choose Aspen. You can place an order with the seller."),
        turn("Compare these shoes"),
        None,
        None,
    )[0].startswith("I would choose")


def test_gift_to_a_friend_excludes_self_preferences_and_brief():
    inbound = turn("Compare these shoes for a friend")
    active = state(inbound)
    active["brief"] = {
        "id": "opening",
        "fields": {"item": {"value": "shoes"}, "size": {"value": "8"}},
        "options": [],
        "activity_at": inbound["source_at"].isoformat(),
    }
    active["preferences"] = {"shoes|me|size": {"value": "8"}}
    cur = Mock()
    cur.execute.return_value.fetchall.return_value = []
    prompt, original_brief, _, _ = harness.context(cur, inbound, active)
    projected = json.loads(prompt)
    assert projected["brief"] is None and projected["preferences"] == []
    assert projected["current_constraints"]["recipient"] == "a friend"
    edits = [{"field": "item", "value": "shoes"}, {"field": "recipient", "value": "a friend"}]
    with pytest.raises(harness.BriefEvidenceError):
        harness.validate(generated(brief="update", edits=edits), inbound, original_brief, None)
    question = {"field": "size", "value": None}
    clarification, unchanged, target = harness.validate(
        generated(question=question),
        inbound,
        original_brief,
        None,
    )
    assert "What size" in clarification
    assert unchanged == original_brief and target is None

    _, replacement, target = harness.validate(
        generated(brief="replace", edits=edits, question=question),
        inbound,
        original_brief,
        None,
    )
    assert replacement["fields"]["recipient"]["value"] == "a friend"
    assert "size" not in replacement["fields"]
    assert target == {"brief_id": replacement["id"], **question}
    assert target["brief_id"] != original_brief["id"]


def test_brief_expiry_uses_accepted_edit_time_and_read_only_turn_does_not_renew():
    inbound = turn("Compare shoes")
    active = state(inbound)
    active["watermark"] = inbound["source_at"] - timedelta(seconds=1)
    active["brief"] = {
        "id": str(uuid4()),
        "fields": {"item": {"value": "shoes"}},
        "options": [],
        "activity_at": (inbound["source_at"] - harness.BRIEF_AGE - timedelta(seconds=1)).isoformat(),
    }
    cur = Mock()
    cur.execute.return_value.fetchall.return_value = []
    prompt, brief, _, _ = harness.context(cur, inbound, active)
    assert brief is None and json.loads(prompt)["brief"] is None
    active["brief"]["activity_at"] = active["watermark"].isoformat()
    _, usable, _, _ = harness.context(cur, inbound, active)
    _, unchanged, _ = harness.validate(generated(), inbound, usable, None)
    assert unchanged == active["brief"]


def test_gift_size_guard_is_limited_to_unresolved_fit_choices():
    text = "Compare shoes for my sister. Maple is size 8. Oak is size 10. Which fits?"
    assert harness.needs_gift_size(text, harness.current_constraints(text), None, None, [])
    own_size = text + " My size is 8."
    assert harness.needs_gift_size(own_size, harness.current_constraints(own_size), None, None, [])
    explicit_size = text + " My sister wears size 10."
    assert not harness.needs_gift_size(explicit_size, harness.current_constraints(explicit_size), None, None, [])
    sister_size = {"item": "shoes", "recipient": "sister", "key": "size", "value": "10"}
    assert not harness.needs_gift_size(text, harness.current_constraints(text), None, None, [sister_size])
    other_choice = text.replace("Which fits?", "Which one is cheaper?")
    assert not harness.needs_gift_size(other_choice, harness.current_constraints(other_choice), None, None, [])
    budget_choice = text.replace("Which fits?", "Which fits in my budget?")
    assert not harness.needs_gift_size(budget_choice, harness.current_constraints(budget_choice), None, None, [])
    self_shopping = text.replace("my sister", "me")
    assert not harness.needs_gift_size(self_shopping, harness.current_constraints(self_shopping), None, None, [])
    same_size = text.replace("size 10", "size 8")
    assert not harness.needs_gift_size(same_size, harness.current_constraints(same_size), None, None, [])
    gift_brief = {"fields": {"recipient": {"value": "my sister"}}, "options": []}
    assert "size" not in harness.current_constraints("My size is 8. Which fits?", gift_brief)


def test_gift_pronoun_size_does_not_authorize_a_brief_edit():
    request = "Switch to a gift of shoes for my sister. She wears a size 10."
    inbound = turn(request)
    constraints = harness.current_constraints(request)
    assert harness.gift_size_for_reply(request, constraints) == "10"
    assert "size" not in constraints
    proposed = generated(
        brief="replace",
        edits=[
            {"field": "item", "value": "shoes"},
            {"field": "recipient", "value": "my sister"},
            {"field": "size", "value": "10"},
        ],
    )
    with pytest.raises(harness.BriefEvidenceError) as error:
        harness.validate(proposed, inbound, None, None)
    assert error.value.field == "size"

    ambiguous = request.replace("for my sister.", "for my sister and my mom.")
    assert harness.gift_size_for_reply(ambiguous, harness.current_constraints(ambiguous)) is None


@pytest.mark.parametrize(
    "premature_reply",
    [
        "For your sister, Maple is the better fit if her shoe size is 8. "
        "Oak is size 10, so it would likely be too large.",
        "For your sister, Maple fits better if she wears size 8. "
        "Oak is size 10, so it would likely be too large. "
        "If you want, I can also help compare based on condition or budget.",
    ],
)
def test_paid_gift_replay_misses_render_an_open_recipient_size_question(premature_reply):
    inbound = turn("Switch to a gift of shoes for my sister. Maple is size 8. Oak is size 10. Which fits?")
    active = state(inbound)
    active["brief"] = {
        "id": str(uuid4()),
        "fields": {"item": {"value": "shoes"}, "size": {"value": "8"}},
        "options": [],
        "activity_at": inbound["source_at"].isoformat(),
    }
    cur = Mock()
    cur.execute.return_value.fetchall.return_value = []
    prompt, brief, answer, require_gift_size = harness.context(cur, inbound, active)
    assert json.loads(prompt)["brief"] is None and require_gift_size
    reply, saved, question = harness.validate(
        generated(reply=premature_reply),
        inbound,
        brief,
        answer,
        require_gift_size=require_gift_size,
    )
    assert reply.startswith("What size does the recipient wear?")
    assert "Maple" not in reply and "Oak" not in reply
    assert saved == active["brief"] and question is None
