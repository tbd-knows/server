"""Standing preference authority, canonical scopes, and local controls."""

import re

from .policy import SECRET, SHOPPING_REQUEST, action_clauses, unsupported_action, without_greeting

KEYS = ("size", "fit", "condition", "brand", "material")

# Durable controls name the field, item and recipient.
MEMORY = re.compile(
    r"^(remember|save|correct|repair|forget) (?:my )?(size|fit|condition|brand|material)"
    r" for ([\w -]{1,48}?) for ([\w -]{1,48}?)(?:(?: is | to | = )([^\n;!?]{1,80}))?$",
    re.I,
)
TASK_ONLY = re.compile(r"\s+(?:just )?for (?:this|the current) (?:purchase|brief)$", re.I)
TASK_CORRECTION = re.compile(
    r"^correct (?:my )?(size|fit|condition|brand|material) for ([\w -]{1,48}?) for ([\w -]{1,48}?) to (.{1,80})$",
    re.I,
)

MEMORY_CONFLICT = re.compile(
    r"\b(?:never mind|nevermind|take (?:that|it) back|changed my mind|"
    r"(?:keep|retain|restore|undo|forget|save|remember|resume|pause)\b.{0,40}\b(?:preference|memory|that|it)|"
    r"(?:do not|don[’']t) (?:forget|delete|clear|pause)|(?:pause|resume) memory)\b",
    re.I,
)

CONTROL_CLARIFICATION = "Please confirm what you want me to do with your preference memory."
CONTROL_HEAD = re.compile(
    r"^(?:remember|save|correct|repair|forget|pause memory|resume memory|show my preferences|"
    r"what do you remember|what preferences (?:do you|did you) save)\b", re.I
)
CONTROL_SPLIT = re.compile(
    r";|\n|[.!?]\s+|,?\s+(?:and|then)\s+(?=(?:(?:please|can you)\s+)?"
    r"(?:compare|find|look|help|show|recommend|buy|order|pay|contact|text|message|check|verify|remember|"
    r"save|correct|repair|forget|pause|resume|close)\b)"
    r"|(?:,\s*|\s+(?:and|but)\s+)(?=(?:actually|keep|retain|undo|never mind|resume|don[’']t)\b)",
    re.I,
)

def normalize(value):
    return " ".join(value.casefold().split())


def recipient_scope(value):
    person = re.sub(r"^(?:for )?(?:(?:my|a|an|our|the) )?", "", normalize(value))
    return "me" if person == "myself" else person


def resolved_preferences(preferences):
    """Group all known storage aliases under one canonical field scope."""
    groups = {}
    for storage_key, entry in preferences.items():
        parts = storage_key.split("|")
        if len(parts) != 3 or not isinstance(entry, dict) or not isinstance(entry.get("value"), str):
            continue
        item, person, key = map(normalize, parts)
        if item and person and key in KEYS:
            groups.setdefault((item, recipient_scope(person), key), {})[storage_key] = entry
    return groups


def reset_context(state, turn):
    state["brief"] = None
    state["reset_source_at"] = turn["source_at"]
    state["reset_sequence"] = turn["queue_sequence"]


def memory_command(clause):
    text = re.sub(r"^(?:please |(?:can|could|would) you (?:please )?)", "", clause.strip(), count=1, flags=re.I)
    if TASK_ONLY.search(text.rstrip(".!?")):
        return None
    if CONTROL_HEAD.match(text):
        return text.rstrip(".!?").strip()
    if re.search(r"[“\"‘]|(?<!\w)'", text) and CONTROL_HEAD.match(
        re.split(r"[“\"‘]|(?<!\w)'", text, maxsplit=1)[1]
    ):
        return ""
    if re.match(r"^(?:do not|don[’']t|never mind)\s+", text, re.I) and CONTROL_HEAD.match(
        re.sub(r"^(?:do not|don[’']t|never mind)[, ]+", "", text, flags=re.I)
    ):
        return ""
    return None


def is_memory_input(text):
    return any(memory_command(clause) is not None for clause in CONTROL_SPLIT.split(without_greeting(text)))


def memory_control(text, state, turn):
    clauses = CONTROL_SPLIT.split(without_greeting(text))
    commands = [(i, command) for i, clause in enumerate(clauses) if (command := memory_command(clause)) is not None]
    if not commands:
        return None
    if len(commands) != 1 or not commands[0][1]:
        return CONTROL_CLARIFICATION
    index, command = commands[0]
    rest = "; ".join(clause.strip() for i, clause in enumerate(clauses) if i != index and clause.strip())
    lower = normalize(command)
    operation = {
        "pause memory": "pause",
        "resume memory": "resume",
        "what do you remember": "inspect",
        "what preferences do you save": "inspect",
        "what preferences did you save": "inspect",
        "show my preferences": "inspect",
        "forget all preferences": "forget_all",
        "forget my name": "forget_name",
        "forget the name i told you": "forget_name",
    }.get(lower)
    match = MEMORY.fullmatch(command)
    if match:
        operation, key, item, person, value = match.groups()
        operation = operation.casefold()
        scope = (normalize(item), recipient_scope(person), key.casefold())
        if (
            not all(scope)
            or scope[1] in ("someone", "someone else", "anyone", "them")
            or MEMORY_CONFLICT.search(" ".join(filter(None, (item, person, value))))
            or re.search(r"\b(?:or|actually|maybe|never mind)\b", " ".join(filter(None, (item, person, value))), re.I)
            or re.search(r"\b(?:and|for)\b", person, re.I)
            or (value and re.search(r"[“”\"‘]|\band\b|^not\b", value, re.I))
        ):
            return CONTROL_CLARIFICATION
        if (operation == "forget" and value is not None) or (
            operation in ("remember", "save", "correct") and value is None
        ):
            return "Please state which preference to forget, or give the new value to save."
    if not operation:
        return "Use “show my preferences” or name the preference, item, and recipient."

    shopping = any(SHOPPING_REQUEST.match(part) for part in action_clauses(rest))
    if rest and (MEMORY_CONFLICT.search(rest) or (not shopping and not unsupported_action(rest))):
        return CONTROL_CLARIFICATION
    preferences = state["preferences"]
    groups = resolved_preferences(preferences)
    interrupted = state["brief"] is not None
    reset = False
    if operation == "pause":
        state["paused"] = True
        response, reset = "Preference memory is paused.", True
    elif operation == "forget_all":
        preferences.clear()
        response, reset = "Standing preferences were cleared.", True
    elif operation == "forget_name":
        response, reset = "I cleared my chat recall of your name.", True
    elif operation == "resume":
        state["paused"] = False
        response = "Preference memory is active."
    elif operation == "inspect":
        rows = []
        for (item, person, key), aliases in sorted(groups.items()):
            values = {normalize(entry["value"]): entry["value"] for entry in aliases.values()}
            if any(SECRET.search(value) for value in values.values()):
                continue
            detail = next(iter(values.values())) if len(values) == 1 else "conflicting values. Please save one or forget it."
            rows.append(f"{key} for {item} for {person}: {detail}")
        response = "Saved preferences:\n" + "\n".join(rows) if rows else "You have no saved preferences."
    else:
        aliases = groups.get(scope, {})
        existing = {normalize(entry["value"]) for entry in aliases.values()}
        if operation == "forget":
            for storage_key in aliases:
                preferences.pop(storage_key)
            response = (
                "I forgot that preference." if aliases
                else "There was no saved preference in that scope. I cleared the earlier shopping context."
            )
            reset = True
        else:
            value = value.strip() if value else None
            authorized = operation != "repair"
            if operation == "repair" and value:
                value, count = re.subn(r"\s+for future (?:purchases|shopping)$", "", value, flags=re.I)
                authorized = bool(count)
            same = value is not None and existing == {normalize(value)}
            if operation == "repair" and not authorized:
                if len(existing) > 1 or (existing and value is not None and not same):
                    return "Please explicitly save the replacement standing preference or ask me to forget it."
                response, reset = "I cleared the earlier shopping context for that repair.", True
            elif operation == "repair" and same:
                response, reset = "I cleared the earlier shopping context for that repair.", True
            elif state["paused"]:
                return "Preference memory is paused. Say “resume memory” to save a preference."
            elif same:
                response = "That standing preference is already saved."
            elif value and not SECRET.search(value):
                if len(preferences) >= 24 and not aliases:
                    return "Preference memory is full. Please forget a preference before saving another."
                for storage_key in aliases:
                    preferences.pop(storage_key)
                preferences["|".join(scope)] = {"value": value, "source": str(turn["id"])}
                reset = bool(existing) or operation == "repair"
                response = "I updated that standing preference." if reset else "I saved that standing preference."
            else:
                return CONTROL_CLARIFICATION
    if reset:
        reset_context(state, turn)
    if shopping:
        response += " Please resend the shopping request."
    elif reset and interrupted:
        response += " To pick up the comparison, please resend the item, recipient, and options."
    return response
