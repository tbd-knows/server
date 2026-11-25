"""TBD policy, context projection, and proposal validation."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from tbd_server.messaging.core import utf8_size
from tbd_server.user.store import Problem

from .memory import TASK_CORRECTION, TASK_ONLY, is_memory_input, normalize, recipient_scope, resolved_preferences
from .policy import (
    ACTION, CONTRADICTORY_IDENTITY, FALSE_CONFIRMATION, IDENTITY, INSTRUCTIONS, QUOTATIONS, SECRET, SHOPPING_REQUEST,
    action_clauses, profile_request, request_clauses, unsupported_action, without_greeting, without_quotes,
)

SKEW = timedelta(seconds=2)
BRIEF_AGE = timedelta(days=7)
PROMPT_BYTES = 16000
FALLBACK = "I couldn’t help with that right now. Please try again."
RESTATEMENT = "Please restate what you're shopping for, who it’s for, and the key details in a short message."
CLARIFY_BRIEF = "Please confirm which shopping detail you want me to use and give its value."
KEEP_BRIEF = "I’ll leave the shopping details as they are."
TRANSACTION = (
    "I can’t find live listings, contact sellers, buy, pay, or verify order or payment status. "
    "Share the options you’re considering and I can help you compare."
)
CLOSED = "I closed that shopping brief. Tell me what you’d like to look for next."
NO_BRIEF = "There’s no open shopping brief. Tell me what you’d like to look for next."
LINK_DETAILS = "Please paste the item details, price, and anything you want me to compare. I can work from what you share here."
BRIEF_DETAILS = "What are you shopping for, who is it for, and what detail should I use?"
SENSITIVE = "Please don’t share payment credentials or authentication secrets in this chat."
OPTION_REFERENCE = (
    r"(?:(?:both|the two|these two|those two|these|those|the other)(?: (?:options?|choices?|ones?|listings?))?"
    r"|(?:the |two |these |those )?(?:options|choices|listings)|them|it|this|that)"
)
RECIPIENT = re.compile(
    r"\b(?i:for) ((?i:me|myself|someone(?: else)?|(?:my|a|an|our|the) [\w-]+(?: [\w-]+)*?)"
    r"|[A-Z][\w-]*(?: [A-Z][\w-]*)*)(?=$|[,:.!?]|\s+(?i:with|under|by|and|in size)\b)"
)
MULTIPLE_RECIPIENTS = re.compile(
    r"\b(?i:for) (?:[\w-]+ ){0,4}(?i:and|or) (?:(?i:for )?(?i:me|myself|my (?:partner|friend|"
    r"child|mom|mother|dad|father|sister|brother))|(?:[A-Z][a-z]+)(?=$|[,;.!?]))\b"
)
UNKNOWN_RECIPIENTS = {"someone", "someone else", "them"}
RECIPIENT_ANSWER = (
    r"(?:for )?(?:me|myself|my (?:partner|friend|child|mom|mother|dad|father|sister|brother))|"
    r"for (?-i:[A-Z][a-z]+(?: [A-Z][a-z]+)*)"
)
PRICE_AMOUNT = r"\$\s?\d[\d,]*(?:\.\d{2})?"
PRICE = f"({PRICE_AMOUNT})"
SIZE_VALUE = r"(?:\d{1,3}(?:\.\d{1,2})?(?:\s*(?:us|uk|eu|wide))?|xxxs|xxs|xs|s|m|l|xl|xxl|xxxl)"
SIZE = f"({SIZE_VALUE})"
RECIPIENT_SIZE = re.compile(
    r"(my [\w-]+)(?: (?:wears|takes|needs) (?:a )?size|[’']s (?:shoe )?size(?: is|:))\s+" + SIZE,
    re.I,
)
PRONOUN_SIZE = re.compile(
    r"(?:(?:she|he|they) (?:wears?|takes?|needs?) (?:a )?size|(?:her|his|their) (?:shoe )?size(?: is|:))\s+" + SIZE,
    re.I,
)
SIZE_MENTION = re.compile(r"\bsize\s+" + SIZE + r"\b", re.I)
FIT_CHOICE = re.compile(
    r"(?:(?:which(?: (?:one|pair|option|of these|of the two))?|what) "
    r"(?:(?:(?:would|will|could|might) )?fits?(?: (?:her|him|them|my [\w-]+))?(?: (?:better|best|well))?"
    r"|(?:is|would be) (?:a|the) (?:better|best|right) fit(?: for (?:her|him|them|my [\w-]+))?)"
    r"|(?:will|would|does|do|could|can) (?:either|both|it|they|this|that|these|those)"
    r"(?: (?:one|option|pair|of them|of these|of those))? fit(?: (?:her|him|them|my [\w-]+))?"
    r"|(?:compare (?:how they fit|their fit)|help me (?:choose|pick) (?:by|based on) fit))(?: please)?",
    re.I,
)
CONSTRAINTS = {
    "budget": r"(?:my (?:new )?(?:(?:max(?:imum)? )?budget|max(?:imum)?)(?: is| of|:)?|"
    r"i (?:can spend|want to spend|won[’']t spend more than))\s+" + PRICE,
    "size": r"(?:my size(?: is|:)?|i (?:need|want|wear|take) (?:a )?(?:size|us size))\s+" + SIZE,
    "deadline": r"(?:my deadline(?: is|:)|i need (?:it|them) by) (.+)",
    "fit": r"(?:my fit(?: is|:)|i (?:need|want|prefer)) (slim|regular|relaxed|wide|narrow|loose|oversized)(?: fit)?",
    "condition": r"(?:my condition(?: is|:)|i (?:need|want|prefer)) (new|used|like new|refurbished|secondhand)(?: only)?",
    "brand": r"(?:my brand(?: is|:)|i (?:need|want|prefer) (?:the )?brand) (.+)",
    "material": r"(?:my material(?: is|:)|i (?:need|want|prefer)) (cotton|linen|wool|leather|silk|polyester|nylon|denim|suede)",
}
CONTEXT_VERSION = 7
SCHEMA_VERSION = 1


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Edit(StrictModel):
    field: Literal["item", "recipient", "budget", "deadline", "size", "fit", "condition", "brand", "material", "option"]
    value: str


class Question(StrictModel):
    field: Literal["recipient", "budget", "deadline", "size", "fit", "condition", "brand", "material"]
    value: str | None


class Result(StrictModel):
    reply: str
    brief: Literal["unchanged", "update", "replace", "close"]
    edits: list[Edit]
    question: Question | None


def fresh(turn, state):
    source = turn["source_at"]
    return (
        source <= turn["created_at"] + SKEW
        and source >= state["enrolled_at"]
        and (state["watermark"] is None or source >= state["watermark"])
        and after_reset(turn, state)
    )


def after_reset(turn, state):
    return (state["reset_sequence"] is None or turn["queue_sequence"] > state["reset_sequence"]) and (
        state["reset_source_at"] is None or turn["source_at"] > state["reset_source_at"]
    )


def visible(parent, turn):
    observed = parent["sent_observed_at"]
    return observed is not None and observed < min(turn["created_at"], turn["source_at"] - SKEW)


TYPED_VALUES = {
    "size": r"(?:size )?" + SIZE,
    "budget": r"(?:up to |under )?\$\s?[\d,.]{1,12}",
    "deadline": r"(?:by )?(?:today|tomorrow|(?:next )?(?:mon|tues|wednes|thurs|fri|satur|sun)day|\d{4}-\d{2}-\d{2})",
    "condition": r"new|used|like new|refurbished|secondhand",
    "fit": r"slim|regular|relaxed|wide|narrow|loose|oversized",
    "material": r"cotton|linen|wool|leather|silk|polyester|nylon|denim|suede",
    "recipient": RECIPIENT_ANSWER,
    "brand": r"brand[: ]+[\w -]{1,48}",
}
CONFIRMATION = re.compile(r"yes(?: please)?|correct", re.I)
ACKNOWLEDGMENT = re.compile(r"no(?: thanks)?|sure", re.I)
EXPLICIT_SIZE_ANSWER = re.compile(r"size(?:[ \t]+|:[ \t]*)[\w.-]+(?:[ \t]+[\w.-]+)*", re.I)


def answer_text(text):
    return text.strip().rstrip(".!")


def short_answer(text):
    answer = answer_text(text).rstrip("?")
    return (
        CONFIRMATION.fullmatch(answer) is not None or ACKNOWLEDGMENT.fullmatch(answer) is not None
        or EXPLICIT_SIZE_ANSWER.fullmatch(answer) is not None
        or any(re.fullmatch(pattern, answer, re.I) for pattern in TYPED_VALUES.values())
    )


def typed_answer(text, question, brief):
    if not question or not brief or question["brief_id"] != brief["id"]:
        return None
    field, value = question["field"], question["value"]
    answer = answer_text(text)
    if value is not None:
        return {field: value} if CONFIRMATION.fullmatch(answer) else None
    if pattern := TYPED_VALUES.get(field):
        if re.fullmatch(pattern, answer, re.I):
            if field == "recipient" and answer.casefold().startswith("for "):
                answer = answer[4:]
            if field == "brand":
                answer = re.sub(r"^brand[: ]+", "", answer, flags=re.I)
            return {field: answer}
    return None


MESSAGE_SEPARATORS = re.compile(
    r"[;\n]|[.!?](?:\s+|$)|,?\s+(?:and|but)\s+(?=(?:i|my|(?:the )?(?:seller|listing|option)|(?:also )?(?:buy|contact|pay|order|text|message|email|check|verify))\b)"
    r"|,\s+(?=(?:i|my|(?:the )?(?:seller|listing|option)|(?:also )?(?:buy|contact|pay|order|text|message|email|check|verify))\b)",
    re.I,
)


def message_clauses(text):
    return [clause.strip().strip(",").strip() for clause in MESSAGE_SEPARATORS.split(without_quotes(text))]


SCOPE_CLARIFICATIONS = {
    ("item",): "Which item should I focus on?",
    ("recipient",): "Who are you shopping for?",
    ("item", "recipient"): "Which item should I focus on, and who is it for?",
}


def current_constraints(text, brief=None):
    return request_constraints(text, brief)[0]


WITHDRAWAL = re.compile(
    r"(?:^|[,;]|\bbut\b|\band\b)\s*(?:(?:actually|instead)\b[, ]*)?"
    r"(?:(?P<retract>take (?:that|it) back)|keep|leave|never ?mind|"
    r"(?:do not|don[’']t) (?:change|correct|update))\b", re.I,
)
RETAINED_FIELD = re.compile(r"(?:(?:the|my|current|existing|original|old|previous|same)\s+)*(\w+)", re.I)
CORRECTION_REFERENCE = re.compile(
    r"(?:about )?(?:it|that(?: change| correction)?|the (?:change|correction)|everything)"
    r"(?: alone| as (?:it )?is| unchanged| the same)?",
    re.I,
)


def task_correction(clause):
    follow_up = WITHDRAWAL.search(clause)
    if follow_up:
        clause = clause[:follow_up.start()].rstrip(" ,")
    if TASK_ONLY.search(clause):
        return TASK_CORRECTION.fullmatch(TASK_ONLY.sub("", clause))
    return None


def withdrawn_task_correction(text):
    if not re.search(r"\bcorrect\b", text, re.I):
        return False
    corrected_fields = set()
    for clause in message_clauses(text):
        clause = without_greeting(clause)
        if correction := task_correction(clause):
            corrected_fields.add(correction[1].casefold())
        if not corrected_fields:
            continue
        for withdrawal in WITHDRAWAL.finditer(clause):
            target = clause[withdrawal.end():].strip(" ,")
            field = RETAINED_FIELD.match(target)
            if (
                withdrawal["retract"] or not target or CORRECTION_REFERENCE.fullmatch(target)
                or (field and field[1].casefold() in corrected_fields)
            ):
                return True
    return False


REPEAT_REQUEST = r"(?:again|one more time|once more|side by side)"
REPEAT_SUFFIX = re.compile(r"(?:^|\s+)" + REPEAT_REQUEST + r"(?: please)?$", re.I)
OPTION_REFERENCE_ONLY = re.compile(
    OPTION_REFERENCE + r"|(?:both|(?:the|these|those) two) of (?:them|these|those)|"
    r"the same(?: (?:options?|choices?|ones?|listings?))?|the pair|"
    r"(?:their|the) (?:prices?|sizes?|conditions?|differences)|"
    r"(?:what|the ones) (?:we discussed|you showed me)",
    re.I,
)
ACTIVE_OPTIONS_PREFIX = re.compile(
    r"^(?:them|it|(?:both|the(?: two)?|these(?: two)?|those(?: two)?) (?:options?|choices?|ones?|listings?))\b",
    re.I,
)
COMPARISON_QUALIFIER = re.compile(
    r"\s+(?:but|and)\s+(?:please\s+)?(?:just\s+|only\s+)?"
    r"(?:focus|prioritize|emphasize|pay attention to|look at|only compare|compare by)\b",
    re.I,
)
NON_ITEM_START = re.compile(r"^(?:not|by|on|based on|using|for|between|according to|once more)\b", re.I)


def shopping_item(details):
    if ACTIVE_OPTIONS_PREFIX.match(details.lstrip()):
        return ""
    details = COMPARISON_QUALIFIER.split(details, maxsplit=1)[0].strip()
    details = REPEAT_SUFFIX.sub("", details)
    item = re.split(r"\s+(?:for|with|under|up to|at most|by|on (?:price|fit|condition|size)|in size)\b|,", details, maxsplit=1, flags=re.I)[0].strip()
    item = REPEAT_SUFFIX.sub("", item)
    reference = re.split(r"\s+(?:on|based on|according to)\b", item, maxsplit=1, flags=re.I)[0]
    if OPTION_REFERENCE_ONLY.fullmatch(reference) or NON_ITEM_START.match(item):
        return ""
    return re.sub(r"^(?:both |these |those |some |a pair of |a gift of |a |an )", "", item, flags=re.I)


def request_constraints(text, brief=None):
    """Read explicit constraints and retain conflicts in the request's scope."""
    if withdrawn_task_correction(text):
        return {}, ()
    values = {}
    recipient_sizes = {}
    multiple_recipients = any(
        SHOPPING_REQUEST.match(without_greeting(sentence)) and MULTIPLE_RECIPIENTS.search(sentence)
        for sentence in re.split(r"[;\n]|[.!?](?:\s+|$)", text)
    )
    for clause in message_clauses(text):
        clause = without_greeting(clause)
        if correction := task_correction(clause):
            key, item, person, value = correction.groups()
            for field, entry in (("item", item), ("recipient", person), (key.casefold(), value)):
                identity = recipient_scope(entry) if field == "recipient" else normalize(entry)
                values.setdefault(field, {})[identity] = entry
            if key.casefold() == "size":
                recipient_sizes.setdefault(recipient_scope(person), {})[normalize(value)] = value
            continue
        if named_size := RECIPIENT_SIZE.fullmatch(clause):
            recipient, size = named_size.groups()
            values.setdefault("recipient", {})[recipient_scope(recipient)] = recipient
            recipient_sizes.setdefault(recipient_scope(recipient), {})[size.casefold()] = size
            continue
        matched = False
        for field, pattern in CONSTRAINTS.items():
            if match := re.fullmatch(pattern, clause, re.I):
                value = match[1].strip()
                values.setdefault(field, {})[value.casefold()] = value
                matched = True
        if matched or ACTION.match(clause):
            continue
        request = SHOPPING_REQUEST.match(clause)
        if request:
            details = clause[request.end() :].split(":", 1)[0]
            for recipient in RECIPIENT.finditer(details):
                values.setdefault("recipient", {})[recipient_scope(recipient[1])] = recipient[1]
            if cap := re.search(r"\b(?:under|up to|at most)\s+" + PRICE, details, re.I):
                values.setdefault("budget", {})[normalize(cap[1])] = cap[1]
            item = shopping_item(details)
            if not item:
                continue
            if re.fullmatch(r"[A-Z][\w-]+(?: (?:and|or) [A-Z][\w-]+)+", item):
                # Comparisons between supplied labels carry no item evidence.
                continue
            if brief and any(
                re.match(re.escape(item) + r"(?=\s|[:(]|$)", option["value"], re.I) for option in brief["options"]
            ):
                continue
            for candidate in re.split(r"\s+(?:and|or)\s+", item, flags=re.I):
                if (
                    re.fullmatch(r"[\w-]+(?: [\w-]+){0,5}", candidate)
                    and candidate.casefold() not in ("these", "those", "them", "it", "this", "the options")
                ):
                    values.setdefault("item", {})[normalize(candidate)] = candidate
    constraints = {field: next(iter(choices.values())) for field, choices in values.items() if len(choices) == 1}
    conflicts = tuple(
        field for field in ("item", "recipient")
        if len(values.get(field, {})) > 1 or (field == "recipient" and multiple_recipients)
    )
    if conflicts:
        return constraints, conflicts
    recipient = constraints.get("recipient")
    if recipient and (scope := recipient_scope(recipient)) != "me":
        # First-person sizes belong to the shopper. A gift needs its own size evidence.
        constraints.pop("size", None)
        sizes = recipient_sizes.get(scope, {})
        if len(sizes) == 1:
            constraints["size"] = next(iter(sizes.values()))
    elif not recipient and "size" in constraints and brief_matches_request(brief, text, constraints):
        stored_recipient = brief_recipient(brief)
        if recipient_scope(stored_recipient) != "me":
            constraints.pop("size")
    return constraints, conflicts




def unclear_recipient(text, constraints):
    if "recipient" in constraints:
        return recipient_scope(constraints["recipient"]) in UNKNOWN_RECIPIENTS
    return bool(
        re.search(r"\b(?:gifts?|for (?:my|a|an|our|the|someone))\b", text, re.I)
    )


def brief_item_matches(brief, constraints):
    item = constraints.get("item")
    stored = brief["fields"].get("item", {}).get("value", "")
    return item is None or normalize(item) == normalize(stored)


def brief_recipient(brief):
    return brief["fields"].get("recipient", {}).get("value", "me")


def scoped_answer(brief, answer):
    recipient = (answer or {}).get("recipient")
    if brief and recipient and recipient_scope(recipient) != recipient_scope(brief_recipient(brief)):
        # A verified recipient answer can reuse the subject of its question.
        # The different recipient makes all other brief fields ineligible.
        return {**answer, "item": brief["fields"]["item"]["value"]}
    return answer


def brief_matches_request(brief, text, constraints):
    if brief is None or unclear_recipient(text, constraints):
        return False
    recipient = constraints.get("recipient")
    return brief_item_matches(brief, constraints) and (
        recipient is None or recipient_scope(recipient) == recipient_scope(brief_recipient(brief))
    )


def gift_size_for_reply(text, constraints):
    recipient = constraints.get("recipient")
    if not recipient or recipient_scope(recipient) == "me" or "size" in constraints:
        return None
    clauses = message_clauses(text)
    sizes = {match[1].casefold(): match[1] for clause in clauses if (match := PRONOUN_SIZE.fullmatch(clause))}
    if len(sizes) != 1:
        return None
    # A pronoun can inform the reply when it immediately follows the shopping
    # request naming its recipient. This is separate from editable constraints.
    if any(
        SHOPPING_REQUEST.match(prior)
        and prior.casefold().endswith(" for " + recipient.casefold())
        and PRONOUN_SIZE.fullmatch(clause)
        for prior, clause in zip(clauses, clauses[1:])
    ):
        return next(iter(sizes.values()))
    return None


def needs_gift_size(text, constraints, projected_brief, answer, preferences, *, reply_size=None):
    fields = projected_brief["fields"] if projected_brief else {}
    recipient = constraints.get("recipient") or fields.get("recipient", {}).get("value")
    if not recipient or recipient_scope(recipient) == "me":
        return False
    if reply_size is not None or "size" in constraints or "size" in (answer or {}) or "size" in fields:
        return False
    if any(pref["key"] == "size" and pref["recipient"] == recipient_scope(recipient) for pref in preferences):
        return False
    options = projected_brief["options"] if projected_brief else ()
    sizes = {
        size.casefold()
        for claim in (text, *(option["value"] for option in options))
        for size in SIZE_MENTION.findall(claim)
    }
    return len(sizes) > 1 and any(FIT_CHOICE.fullmatch(clause) for clause in message_clauses(text))


@dataclass(frozen=True)
class ActiveBrief:
    value: dict
    task_cutoff: int


def brief_in_use(cur, turn, state):
    brief = state["brief"]
    if not brief or "item" not in brief.get("fields", {}):
        return None
    entries = (*brief["fields"].values(), *brief["options"])
    if any(SECRET.search(entry["value"]) for entry in entries):
        return None
    try:
        opening = UUID(brief["id"])
        sources = {opening, *(UUID(entry["source"]) for entry in entries)}
    except (ValueError, KeyError, TypeError):
        return None
    rows = cur.execute(
        """SELECT m.id,m.source_at,m.queue_sequence,m.text FROM messaging.messages m
        JOIN agent_service.turns t ON t.inbound_id=m.id
        WHERE m.id=ANY(%s) AND m.user_id=%s AND m.account_ref=%s AND m.peer_ref=%s AND m.conversation_id=%s
          AND t.enrollment_id=%s AND m.queue_sequence<%s AND t.outcome='model'
          AND m.source_at >= %s AND m.source_at <= %s
          AND m.source_at <= m.created_at + %s""",
        (
            list(sources), turn["user_id"], turn["account_ref"], turn["peer_ref"],
            turn["conversation_id"], turn["enrollment_id"], turn["queue_sequence"],
            state["enrolled_at"], turn["source_at"], SKEW,
        ),
    ).fetchall()
    eligible = {
        row["id"]: row for row in rows
        if after_reset(row, state) and not SECRET.search(row["text"])
        and not profile_request(row["text"]) and not is_memory_input(row["text"])
    }
    if eligible.keys() != sources:
        return None
    at = max(row["source_at"] for row in eligible.values())
    if brief.get("activity_at"):
        try:
            at = min(at, datetime.fromisoformat(brief["activity_at"]))
        except (ValueError, TypeError):
            return None
    if turn["source_at"] - at > BRIEF_AGE:
        return None
    opening_sequence = eligible[opening]["queue_sequence"]
    legacy_cutoff = max(
        (eligible[UUID(entry["source"])]["queue_sequence"]
         for key, entry in brief["fields"].items() if key not in ("item", "recipient")),
        default=opening_sequence,
    )
    cutoff = brief.get("task_cutoff", legacy_cutoff)
    options = [
        entry for entry in brief["options"]
        if is_supplied_option(entry["value"], eligible[UUID(entry["source"])]["text"])
    ]
    return ActiveBrief({**brief, "activity_at": at.isoformat(), "options": options}, max(opening_sequence, cutoff))


def previous_turns(cur, turn):
    return cur.execute(
        """SELECT i.id,i.text,i.queue_sequence,i.source_at,i.created_at,t.outcome,t.question,
            o.sent_observed_at,o.text AS reply
        FROM messaging.messages i JOIN agent_service.turns t ON t.inbound_id=i.id
        LEFT JOIN messaging.messages o ON o.reply_to_message_id=i.id
          AND o.dedupe_key='reply:'||i.id::text AND o.direction='outbound'
          AND (o.user_id,o.channel,o.account_ref,o.peer_ref,o.conversation_id)
              =(i.user_id,i.channel,i.account_ref,i.peer_ref,i.conversation_id)
        WHERE i.user_id=%s AND i.account_ref=%s AND i.peer_ref=%s AND i.conversation_id=%s
          AND t.enrollment_id=%s AND i.queue_sequence<%s AND t.outcome IS NOT NULL
        ORDER BY i.queue_sequence DESC LIMIT 12""",
        (
            turn["user_id"], turn["account_ref"], turn["peer_ref"],
            turn["conversation_id"], turn["enrollment_id"], turn["queue_sequence"],
        ),
    ).fetchall()


def app_identity(parent):
    return parent["outcome"] == "control" and parent["reply"] == IDENTITY


def eligible_parent(parent, turn, state):
    return (
        (parent["outcome"] in ("model", "fallback") or app_identity(parent))
        and after_reset(parent, state)
        and state["enrolled_at"] <= parent["source_at"] <= turn["source_at"]
        and parent["source_at"] <= parent["created_at"] + SKEW
        and not SECRET.search(parent["text"])
        and not profile_request(parent["text"])
        and not is_memory_input(parent["text"])
        and not unsupported_action(parent["text"])
    )


def question_text(field, value):
    return f"Is {value} the right {field}?" if value is not None else f"What {field} should I use?"


def bound_answer(text, parent, brief, turn, state):
    target = parent.get("question")
    if (
        not brief or not target or parent["outcome"] != "model" or not parent["reply"]
        or not eligible_parent(parent, turn, state) or not visible(parent, turn)
        or turn["source_at"] <= parent["source_at"]
    ):
        return None
    field, value = target["field"], target["value"]
    reply = parent["reply"]
    if (
        reply.rsplit("\n\n", 1)[-1] != question_text(field, value)
        and not (field == "size" and value is None and reply == "What size does the recipient wear?")
    ):
        return None
    return scoped_answer(brief, typed_answer(text, target, brief))


VAGUE_TASK = re.compile(
    r"^(?:(?:(?:can|could|would) you )?(?:please )?(?:compare|pick|choose) (?:"
    + OPTION_REFERENCE + "|" + REPEAT_REQUEST + r")\b|"
    r"which (?:option|one|fits|works|is better|of (?:the|these|those))|"
    r"(?:continue|resume|pick up|back to) (?:where|my|the|that|this)|what about (?:that|those|the other))", re.I,
)
NAME = r"[^\W\d_][\w’'-]*(?: [^\W\d_][\w’'-]*){0,4}"
INTRODUCTION = (
    r"(?:my name is|i go by|call me) " + NAME +
    r"|(?:i am|i[’']m) (?-i:[A-Z][a-z]+(?: [A-Z][a-z]+)*)"
)


def identity_introduction(text):
    clauses = (part.split(",", 1)[0].strip() for part in request_clauses(text))
    names = [
        clause for clause in clauses
        if re.fullmatch(INTRODUCTION, clause, re.I) and not SHOPPING_REQUEST.match(clause)
    ]
    return names[0] if len(names) == 1 else None


SOCIAL = re.compile(
    r"(?:[\W]*|thanks(?: for your help)?|thank you|how are you(?: doing)?|"
    r"nice to meet you|good to meet you|what(?: is|'s|’s) my name|do you remember my name|"
    + INTRODUCTION + r")", re.I,
)


def social_text(text):
    return all(SOCIAL.fullmatch(without_greeting(clause)) for clause in message_clauses(text))


@dataclass(frozen=True)
class TurnScope:
    active: ActiveBrief | None
    constraints: dict
    answer: dict | None = None
    retire: bool = False
    clarification: str | None = None

    @property
    def brief(self):
        return self.active.value if self.active else None


def resolve_scope(turn, state, active, previous):
    text = turn["text"]
    brief = active.value if active else None
    constraints, conflicts = request_constraints(text, brief)
    if conflicts:
        return TurnScope(active, constraints, clarification=SCOPE_CLARIFICATIONS[conflicts])
    if unclear_recipient(text, constraints):
        if any(SHOPPING_REQUEST.match(part) for part in action_clauses(text)):
            return TurnScope(active, constraints, clarification=SCOPE_CLARIFICATIONS[("recipient",)])
    answer = bound_answer(text, previous[0], brief, turn, state) if previous else None
    scope = {**(answer or {}), **constraints}
    if without_greeting(text) and short_answer(text) and not answer:
        return TurnScope(active, constraints, clarification=RESTATEMENT)
    if brief is None and not scope.get("item") and any(
        VAGUE_TASK.search(without_greeting(clause)) for clause in message_clauses(text)
    ):
        # A current, self-contained list can still be compared.
        if not SIZE_MENTION.search(text) and len(re.findall(PRICE, text)) < 2:
            return TurnScope(active, constraints, clarification=BRIEF_DETAILS)
    if re.match(r"^switch\b", without_greeting(text), re.I) and not any(key in scope for key in ("item", "recipient")):
        return TurnScope(active, constraints, clarification=BRIEF_DETAILS)
    retire = (
        brief is not None and not unclear_recipient(text, scope)
        and (
            not brief_item_matches(brief, scope)
            or ("recipient" in scope and recipient_scope(scope["recipient"]) != recipient_scope(brief_recipient(brief)))
        )
    )
    return TurnScope(active, constraints, answer, retire)


def task_history_matches(parent, projected, scope):
    text = parent["text"]
    constraints, conflicts = request_constraints(text)
    if not conflicts and not constraints and social_text(text) and not unclear_recipient(text, {}):
        return True
    if projected is None or parent["queue_sequence"] < scope.active.task_cutoff:
        return False
    if not constraints.get("item") or conflicts:
        return False
    # An omitted recipient cannot establish a gift scope in earlier chat.
    return (
        brief_matches_request(projected, text, constraints)
        and recipient_scope(constraints.get("recipient", "me")) == recipient_scope(brief_recipient(projected))
    )


def matching_preferences(state, turn, scope, projected):
    if state["paused"] or unclear_recipient(turn["text"], scope.constraints):
        return []
    fields = {key: entry["value"] for key, entry in projected["fields"].items()} if projected else {}
    current = {**fields, **(scope.answer or {}), **scope.constraints}
    item = normalize(current.get("item", ""))
    person = recipient_scope(current.get("recipient", "me"))
    result = []
    for (pref_item, pref_person, key), aliases in sorted(resolved_preferences(state["preferences"]).items()):
        if (item, person) != (pref_item, pref_person):
            continue
        values = {normalize(entry["value"]): entry["value"] for entry in aliases.values()}
        if len(values) != 1:
            continue
        value = next(iter(values.values()))
        if not SECRET.search(value) and (key not in current or normalize(current[key]) == normalize(value)):
            result.append({"item": item, "recipient": person, "key": key, "value": value})
    return result


@dataclass(frozen=True)
class Projection:
    scope: TurnScope
    prompt: str | None = None
    require_gift_size: bool = False


def context(cur, turn, state):
    previous = previous_turns(cur, turn)
    scope = resolve_scope(turn, state, brief_in_use(cur, turn, state), previous)
    if scope.clarification:
        return Projection(scope)
    text = turn["text"]
    explicit = {**(scope.answer or {}), **scope.constraints}
    projected = (
        scope.brief if (explicit or not social_text(text))
        and brief_matches_request(scope.brief, text, explicit)
        else None
    )
    preferences = matching_preferences(state, turn, scope, projected)
    corrected = False
    if projected:
        fields = projected["fields"]
        corrected = any(key in fields and normalize(fields[key]["value"]) != normalize(value)
                        for key, value in explicit.items() if key not in ("recipient", "item"))
        if corrected:
            projected = {**projected, "fields": {key: entry for key, entry in fields.items() if key not in explicit}}
    recent = []
    if not scope.retire:
        for parent in reversed(previous):
            if not eligible_parent(parent, turn, state):
                continue
            prior_text = parent["text"]
            if app_identity(parent):
                prior_text = identity_introduction(prior_text)
                if not prior_text:
                    continue
            elif not task_history_matches(parent, None if corrected else projected, scope):
                continue
            reply = parent["reply"]
            visible_reply = (
                (parent["outcome"] == "model" or app_identity(parent)) and reply and visible(parent, turn)
                and not SECRET.search(reply) and not FALSE_CONFIRMATION.search(reply) and not CONTRADICTORY_IDENTITY.search(reply)
            )
            recent.append({"source": str(parent["id"]), "user": prior_text, "reply": reply if visible_reply else None})
    reply_size = gift_size_for_reply(text, scope.constraints)
    if projected:
        projected = {
            "id": projected["id"], "activity_at": projected["activity_at"],
            "fields": projected["fields"], "options": projected["options"],
        }
    payload = {
        "current": text,
        "current_constraints": scope.constraints,
        "typed_answer": scope.answer,
        "recipient_size_for_reply": reply_size,
        "brief": projected,
        "preferences": preferences,
        "recent": recent,
    }
    while True:
        require_gift_size = needs_gift_size(text, explicit, projected, scope.answer, preferences, reply_size=reply_size)
        payload["required_question"] = {"field": "size", "value": None} if require_gift_size else None
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if utf8_size(encoded) + utf8_size(INSTRUCTIONS) + utf8_size(json.dumps(Result.model_json_schema())) <= PROMPT_BYTES:
            return Projection(scope, encoded, require_gift_size)
        if recent:
            recent.pop(0)
        elif preferences:
            preferences.pop()
        else:
            return Projection(scope)


def scope_mentioned(scope, text):
    return re.search(r"(?<!\w)" + re.escape(scope) + r"(?!\w)", text) is not None


class BriefValidationError(ValueError):
    """A proposed change or question violates the brief contract."""


class BriefEvidenceError(BriefValidationError):
    """A proposed brief edit lacks authority from the current User turn."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


OPTION_LABEL = (
    r"\b((?i:(?:the )?(?:seller|listing|option)(?: [a-z0-9]+)?)|[A-Z][\w-]*(?: [A-Z0-9][\w-]*){0,3})"
)
CONDITION_VALUE = r"(?:brand new|like new|new|used|refurbished|secondhand)"
PRICE_PREFIX = (
    r"(?i:\s*:\s*|\s+(?:costs?|is|runs?|asks?|for|at)\s+|"
    rf"\s+is (?:{CONDITION_VALUE}|size {SIZE_VALUE}) (?:and costs?|for|at)\s+)"
)
PRICE_CLAIM = PRICE_PREFIX + PRICE
SIZE_VERB = r"(?:is(?: available in)?|comes? in|available in|in|at)"
SIZE_CLAIM = (
    rf"(?i:(?:{PRICE_PREFIX}{PRICE_AMOUNT}(?:\s*,\s*|\s+and {SIZE_VERB}\s+)|"
    rf"\s+(?:{SIZE_VERB}\s+(?:a )?)?|\s*[:(–—]\s*)size\s+"
    r"|[’']s\s+(?:shoe )?size(?: is|:)\s*)" + SIZE + r"\b"
)
CONDITION_VERB = r"(?:is(?: listed as)?|in|describes it as)"
DETAIL_SEPARATOR = r"(?:\s*,\s*(?:and\s+)?|\s+(?:and|but)\s+)"
CONDITION_CLAIM = (
    rf"(?i:(?:\s+{CONDITION_VERB}\s+|\s*[:,–—]\s*(?:condition\s*:?\s*)?"
    rf"|{PRICE_PREFIX}{PRICE_AMOUNT}(?:{DETAIL_SEPARATOR}(?:{SIZE_VERB}\s+)?size\s+{SIZE_VALUE})?"
    rf"{DETAIL_SEPARATOR}(?:{CONDITION_VERB}\s+)?"
    rf")({CONDITION_VALUE})\b)"
)
OPTION_DESCRIPTION = re.compile(
    OPTION_LABEL + r"(?i:\s+(?:has|includes|weighs|measures|is made of)\s+\S"
    r"|\s+(?:is|comes in)\s+(?:waterproof|cotton|linen|wool|leather|silk|polyester|nylon|denim|suede)\b)"
)
LISTING_ATTRIBUTION = re.compile(
    r"\b(?:listing|seller|option)(?: [A-Z0-9]+)? (?:says|reads|lists|describes it as)\s*:?\s*$", re.I,
)
PARENTHETICAL_OPTION = r"\s*\(([^()\r\n]+)\)"
RECOMMENDATION_LEAD = re.compile(r"^(?:pick|choose|recommend|go with)\s+(?:the\s+)?", re.I)
NON_OPTION_LABELS = {"i", "we", "you", "your", "my", "our", "it", "this", "that", "both", "which", "what"}


def amount(value):
    match = re.search(PRICE, value)
    return re.sub(r"[,\s$]", "", match[1] if match else value).removesuffix(".00")


def size_token(value):
    return normalize(value).removeprefix("size ").replace(" ", "")


def condition_token(value):
    condition = normalize(value)
    return {"brand new": "new", "secondhand": "used"}.get(condition, condition)


LISTING_CLAIMS = {
    "price": (PRICE_CLAIM, amount),
    "size": (SIZE_CLAIM, size_token),
    "condition": (CONDITION_CLAIM, condition_token),
}


PARENTHETICAL_FIELDS = {
    "price": (re.compile(PRICE), amount),
    "size": (SIZE_MENTION, size_token),
    "condition": (
        re.compile(rf"(?:^|[,;])\s*(?:condition\s*:?\s*)?({CONDITION_VALUE})\s*(?=$|[,;])", re.I),
        condition_token,
    ),
}


def parenthetical_facts(details):
    return {
        field: {canonical(value) for value in pattern.findall(details)}
        for field, (pattern, canonical) in PARENTHETICAL_FIELDS.items()
        if pattern.search(details)
    }


def option_name(label):
    name = normalize(label).removeprefix("the ")
    if name.split()[0] not in NON_OPTION_LABELS:
        return name
    return None


def listing_facts(sources):
    facts = {}
    for field, (claim, canonical) in LISTING_CLAIMS.items():
        for source in sources:
            for label, value in re.findall(OPTION_LABEL + claim, source):
                if name := option_name(label):
                    facts.setdefault(name, {}).setdefault(field, set()).add(canonical(value))
    for source in sources:
        for label, details in re.findall(OPTION_LABEL + PARENTHETICAL_OPTION, source):
            if name := option_name(label):
                for field, values in parenthetical_facts(details).items():
                    facts.setdefault(name, {}).setdefault(field, set()).update(values)
    return facts


SHOPPER_CONSTRAINT = re.compile(
    "(?:" + "|".join(CONSTRAINTS.values()) + r")(?=$|[,;:]|\s+(?:and|but)\b)", re.I,
)


def shopper_constraint_spans(source):
    # Keep the inbound offsets while hiding quoted seller text from personal-constraint parsing.
    unquoted = QUOTATIONS.sub(lambda quote: " " * len(quote[0]), source)
    boundaries = [(separator.start(), separator.end()) for separator in MESSAGE_SEPARATORS.finditer(unquoted)]
    boundaries.append((len(unquoted), len(unquoted)))
    start = 0
    for end, next_start in boundaries:
        raw = unquoted[start:end]
        clause = without_greeting(raw.strip(" ,"))
        if clause:
            offset = start + raw.index(clause)
            if task_correction(clause) or RECIPIENT_SIZE.fullmatch(clause) or PRONOUN_SIZE.fullmatch(clause):
                yield offset, offset + len(clause)
            elif constraint := SHOPPER_CONSTRAINT.match(clause):
                yield offset, offset + constraint.end()
        start = next_start


def is_supplied_option(value, text):
    personal = tuple(shopper_constraint_spans(text))
    occurrences = [
        match for match in re.finditer(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text, re.I)
        if all(match.end() <= start or match.start() >= end for start, end in personal)
    ]
    if not occurrences:
        return False
    clauses = [clause for clause in message_clauses(value) if clause]
    if clauses and all(
        listing_facts([clause]) or any(option_name(label) for label in OPTION_DESCRIPTION.findall(clause))
        for clause in clauses
    ):
        return True
    # Standalone listing details need an explicit seller/listing attribution.
    if not re.fullmatch(rf"(?:size {SIZE_VALUE}|{PRICE_AMOUNT}|{CONDITION_VALUE})", value, re.I):
        return False
    return any(
        LISTING_ATTRIBUTION.search(text[:quote.start()])
        and any(quote.start() < match.start() and match.end() < quote.end() for match in occurrences)
        for quote in QUOTATIONS.finditer(text)
    )


def facts_supported(claims, evidence):
    return all(values <= evidence.get(field, set()) for field, values in claims.items())


def listing_claims_match(reply, sources):
    supplied = listing_facts(sources)
    for name, claims in listing_facts([reply]).items():
        option = name if name in supplied else RECOMMENDATION_LEAD.sub("", name)
        if not facts_supported(claims, supplied.get(option, {})):
            return False
    # Also match case-insensitive references to labels identified in the supplied listings.
    for name, evidence in supplied.items():
        label = r"\b" + re.escape(name)
        for field, (claim, canonical) in LISTING_CLAIMS.items():
            if any(canonical(match) not in evidence.get(field, ()) for match in re.findall(label + claim, reply, re.I)):
                return False
        for details in re.findall(label + PARENTHETICAL_OPTION, reply, re.I):
            if not facts_supported(parenthetical_facts(details), evidence):
                return False
    return True


def grounded_numbers(reply, prompt):
    """Reject invented prices and sizes against authorized User evidence."""
    context = json.loads(prompt)
    sources = [context["current"], *(parent["user"] for parent in context["recent"])]
    if brief := context["brief"]:
        sources.extend(entry["value"] for entry in brief["options"])
    if not listing_claims_match(reply, sources):
        return False
    for key, pattern, format_value in (("budget", PRICE, amount), ("size", SIZE_MENTION, size_token)):
        values = {
            format_value(value)
            for source in sources for value in re.findall(pattern, source)
        }
        if key == "size":
            values.update(
                size_token(entry["value"]) for entry in context["preferences"] if entry["key"] == key
            )
        explicit = (context["typed_answer"] or {}).get(key) or context["current_constraints"].get(key)
        stored = context["brief"]["fields"].get(key, {}).get("value") if context["brief"] else None
        if explicit or stored:
            values.add(format_value(explicit or stored))
        claims = {format_value(value) for value in re.findall(pattern, reply)}
        if not claims <= values:
            return False
    return True


def validate(raw, turn, brief, answer, *, require_gift_size=False, prompt=None):
    result = Result.model_validate_json(raw)
    text = turn["text"]
    if withdrawn_task_correction(text):
        raise BriefEvidenceError("The correction was withdrawn")
    constraints, conflicts = request_constraints(text, brief)
    if conflicts:
        raise BriefEvidenceError("Conflicting request scope")
    answer = scoped_answer(brief, answer)
    scope = {**(answer or {}), **constraints}
    existing_brief_eligible = brief_matches_request(brief, text, scope)
    reply = result.reply.strip()
    if (
        not reply
        or len(reply) > 4800
        or reply.count("?") > 1
        or SECRET.search(reply)
        or FALSE_CONFIRMATION.search(reply)
        or CONTRADICTORY_IDENTITY.search(reply)
        or re.search(r"\b(?:send|share|provide|enter|tell me)\b.{0,60}\b(?:card|password|cvv|pin|code)\b", reply, re.I)
    ):
        raise ValueError("Invalid reply")
    utf8_size(reply)
    if social_text(text) and not scope and (result.brief != "unchanged" or result.question):
        raise BriefValidationError("A social turn cannot edit the brief or attach a question")
    if prompt is not None and not require_gift_size and not grounded_numbers(reply, prompt):
        raise ValueError("A listing claim conflicts with supplied evidence")
    if len(result.edits) > 12:
        raise BriefValidationError("Too many edits")
    if result.brief in ("unchanged", "close") and result.edits:
        raise BriefValidationError("Unexpected edits")
    if result.brief == "close":
        if not re.fullmatch(
            r"(?:close|clear|finish|cancel)(?: (?:this|the|my))? (?:shopping )?brief[.!]?", text.strip(), re.I
        ):
            raise BriefValidationError("Missing close evidence")
        brief = None
    if result.brief in ("update", "replace"):
        if (
            result.brief == "replace"
            and brief
            and brief_item_matches(brief, scope)
            and "recipient" not in scope
            and not re.search(
                r"\b(?:instead|switch|new|different|now (?:I|look|find)|for my|for me|gift"
                r"|(?:I need|I want|I’m looking for|I'm looking for|shopping for))\b",
                text,
                re.I,
            )
        ):
            raise BriefValidationError("Missing switch evidence")
        recipient = scope.get("recipient")
        if brief and result.brief == "update" and not existing_brief_eligible:
            raise BriefEvidenceError("The current request cannot edit this brief")
        brief = (
            json.loads(json.dumps(brief))
            if brief and result.brief == "update"
            else {
                "id": str(turn["id"]),
                "task_cutoff": turn["queue_sequence"],
                "fields": {},
                "options": [],
            }
        )
        seen = set()
        for edit in result.edits:
            value = edit.value.strip()
            if not 0 < len(value) <= 160 or SECRET.search(value):
                raise BriefValidationError("Invalid edit value")
            if edit.field == "option":
                allowed = is_supplied_option(value, text)
            else:
                allowed = value.casefold() in {
                    candidate.casefold()
                    for candidate in (constraints.get(edit.field), (answer or {}).get(edit.field))
                    if candidate is not None
                }
            if not allowed:
                raise BriefEvidenceError("Missing edit evidence", edit.field)
            utf8_size(value)
            evidence = {"value": value, "source": str(turn["id"])}
            if edit.field == "option":
                if value.casefold() not in {row["value"].casefold() for row in brief["options"]}:
                    brief["options"].append(evidence)
            else:
                if edit.field in seen:
                    raise BriefValidationError("Duplicate field")
                if (
                    edit.field in ("item", "recipient")
                    and edit.field in brief["fields"]
                    and brief["fields"][edit.field]["value"].casefold() != value.casefold()
                ):
                    raise BriefValidationError("Use a replacement for a different item or recipient")
                previous = brief["fields"].get(edit.field)
                if previous and edit.field not in ("item", "recipient") and normalize(previous["value"]) != normalize(value):
                    brief["task_cutoff"] = turn["queue_sequence"]
                brief["fields"][edit.field] = evidence
                seen.add(edit.field)
        if recipient and recipient_scope(brief_recipient(brief)) != recipient_scope(recipient):
            raise BriefEvidenceError("Missing recipient evidence in brief", "recipient")
        if result.edits:
            brief["activity_at"] = turn["source_at"].isoformat()
        if "item" not in brief["fields"] or len(brief["options"]) > 8:
            raise BriefValidationError("Incomplete or oversized brief")
    target = None
    question = Question(field="size", value=None) if require_gift_size else result.question
    if question:
        if not require_gift_size and "?" in reply:
            raise BriefValidationError("Ambiguous question")
        field, value = question.field, question.value
        if require_gift_size:
            reply = "What size does the recipient wear?"
        if result.brief == "unchanged" and not existing_brief_eligible:
            # The app can ask for a detail without making a short answer
            # eligible to edit a brief from a different request.
            clarification = reply if require_gift_size else f"What {field} should I use for this request?"
            return clarification + " Please include the item and who it’s for.", brief, None
        if not brief:
            raise BriefValidationError("A question target requires a brief")
        if value is not None and value != brief["fields"].get(field, {}).get("value"):
            raise BriefValidationError("Unsupported question target")
        target = {"brief_id": brief["id"], "field": field, "value": value}
        if not require_gift_size:
            reply += "\n\n" + question_text(field, value)
    if len(reply) > 5000:
        raise ValueError("Unsafe reply")
    utf8_size(reply)
    return reply, brief, target


INVALID_RESULT = (ValidationError, ValueError, Problem, UnicodeError)
