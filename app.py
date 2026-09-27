from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from composer import compose
from schemas import ContextPush, ReplyRequest, TickRequest
from store import ContextStore


APP_VERSION = "1.0.0"
TEAM_NAME = "Tushar Joshi"
TEAM_MEMBERS = ["Tushar Joshi"]
CONTACT_EMAIL = "jtushar2005@gmail.com"

app = FastAPI(
    title="VERA Merchant AI Bot",
    version=APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

store = ContextStore()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", value)
    return value[:100].strip("_") or "conversation"


def is_expired(trigger: dict[str, Any], now: str) -> bool:
    expires = trigger.get("expires_at")
    if not expires:
        return False
    try:
        exp = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        current = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current >= exp
    except ValueError:
        return False


def customer_is_allowed(trigger: dict[str, Any], customer: dict[str, Any]) -> bool:
    preferences = customer.get("preferences", {})
    if preferences.get("reminder_opt_in") is False:
        return False

    consent = customer.get("consent", {})
    if consent and consent.get("opted_in_at") is None:
        return False

    kind = trigger.get("kind", "")
    scopes = set(consent.get("scope", []))
    if kind in {"recall_due", "appointment_tomorrow"}:
        return not scopes or bool(scopes & {"recall_reminders", "appointment_reminders"})
    if kind in {"customer_lapsed_hard", "customer_lapsed_soft", "wedding_package_followup", "trial_followup"}:
        return not scopes or bool(scopes & {"promotional_offers", "bridal_package_followup", "appointment_reminders", "program_updates"})
    if kind == "chronic_refill_due":
        return not scopes or bool(scopes & {"refill_reminders", "delivery_notifications", "recall_alerts"})
    return True


def validate_action(action: dict[str, Any]) -> bool:
    required = {"conversation_id", "merchant_id", "customer_id", "send_as",
                "trigger_id", "template_name", "template_params", "body",
                "cta", "suppression_key", "rationale"}
    if not required.issubset(action):
        return False
    if not isinstance(action["body"], str) or not action["body"].strip():
        return False
    if "http://" in action["body"] or "https://" in action["body"]:
        return False
    if action["send_as"] not in {"vera", "merchant_on_behalf"}:
        return False
    if action["cta"] not in {
        "none", "open_ended", "binary_yes_no", "binary_confirm_cancel",
        "multi_choice_slot",
    }:
        return False
    return True


@app.get("/v1/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "uptime_seconds": store.uptime_seconds,
        "contexts_loaded": store.counts(),
    }


@app.get("/v1/metadata")
async def metadata() -> dict[str, Any]:
    return {
        "team_name": TEAM_NAME,
        "team_members": TEAM_MEMBERS,
        "model": "deterministic-rule-composer-v1",
        "approach": "deterministic trigger router + category-aware templates + in-memory context store",
        "contact_email": CONTACT_EMAIL,
        "version": APP_VERSION,
        "submitted_at": utc_now_iso(),
    }


@app.post("/v1/context")
async def push_context(body: ContextPush):
    allowed = {"category", "merchant", "customer", "trigger"}
    if body.scope not in allowed:
        return JSONResponse(
            status_code=400,
            content={"accepted": False, "reason": "invalid_scope", "details": body.scope},
        )

    accepted, current_version = store.put_context(
        body.scope, body.context_id, body.version, body.payload
    )

    if not accepted:
        return JSONResponse(
            status_code=409,
            content={
                "accepted": False,
                "reason": "stale_version",
                "current_version": current_version,
            },
        )

    return {
        "accepted": True,
        "ack_id": f"ack_{safe_slug(body.context_id)}_v{body.version}",
        "stored_at": utc_now_iso(),
    }


@app.post("/v1/tick")
async def tick(body: TickRequest) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []

    # The judge caps this at 20 actions/tick. We enforce the same bound.
    for trigger_id in body.available_triggers[:20]:
        trigger = store.get_context("trigger", trigger_id)
        if not trigger:
            continue

        if is_expired(trigger, body.now):
            continue

        merchant_id = trigger.get("merchant_id")
        merchant = store.get_context("merchant", merchant_id)
        if not merchant or store.is_merchant_suppressed(merchant_id):
            continue

        category_slug = merchant.get("category_slug")
        category = store.get_context("category", category_slug)
        if not category:
            continue

        customer_id = trigger.get("customer_id")
        customer = store.get_context("customer", customer_id) if customer_id else None

        if trigger.get("scope") == "customer":
            if not customer or not customer_is_allowed(trigger, customer):
                continue

        suppression_key = str(trigger.get("suppression_key") or "")
        if suppression_key and store.has_sent_suppression(suppression_key):
            continue

        action = compose(category, merchant, trigger, customer)
        if not action:
            continue

        conversation_id = (
            f"conv_{safe_slug(customer_id)}_{safe_slug(trigger_id)}"
            if customer_id
            else f"conv_{safe_slug(merchant_id)}_{safe_slug(trigger_id)}"
        )

        action.update({
            "conversation_id": conversation_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "trigger_id": trigger_id,
        })

        if not validate_action(action):
            continue

        actions.append(action)
        store.mark_sent(conversation_id, action["body"], merchant_id, customer_id, trigger_id)
        store.mark_suppression(action["suppression_key"])

    return {"actions": actions}


def detect_auto_reply(message: str) -> bool:
    text = message.lower().strip()
    patterns = [
        "thank you for contacting",
        "thanks for contacting",
        "our team will respond shortly",
        "team will respond shortly",
        "we will respond shortly",
        "we'll respond shortly",
        "we will get back to you",
        "automated response",
    ]
    return any(p in text for p in patterns)


def detect_opt_out(message: str) -> bool:
    text = message.lower()
    patterns = [
        "stop messaging", "stop sending", "unsubscribe", "do not message",
        "don't message", "dont message", "not interested", "remove me",
        "leave me alone", "this is spam",
    ]
    return any(p in text for p in patterns)


def detect_commitment(message: str) -> bool:
    text = re.sub(r"[^a-z0-9\s']", " ", message.lower())
    patterns = [
        "lets do it", "let's do it", "ok do it", "okay do it",
        "go ahead", "whats next", "what's next", "what next",
        "confirm", "yes proceed", "proceed", "do it",
    ]
    return any(p in text for p in patterns)


def detect_out_of_scope(message: str) -> str | None:
    text = message.lower()
    if any(k in text for k in ["gst filing", "gst return", "tax filing", "income tax", "itr"]):
        return "GST/tax filing"
    if any(k in text for k in ["legal case", "lawyer", "court filing"]):
        return "legal filing"
    return None


def detect_positive_request(message: str) -> bool:
    text = message.lower()
    return any(k in text for k in [
        "yes", "please send", "send it", "draft it", "draft the",
        "pull it", "interested", "how do i", "how can i",
    ])


@app.post("/v1/reply")
async def reply(body: ReplyRequest) -> dict[str, Any]:
    message = body.message.strip()
    conv = store.get_conversation(body.conversation_id)

    conv.turns.append({
        "from": body.from_role,
        "message": message,
        "received_at": body.received_at,
        "turn_number": body.turn_number,
    })

    if conv.ended:
        return {
            "action": "end",
            "rationale": "Conversation was already closed; no further message is sent.",
        }

    merchant_id = body.merchant_id or conv.merchant_id
    merchant = store.get_context("merchant", merchant_id) if merchant_id else None

    # Hard opt-out has the highest priority.
    if detect_opt_out(message):
        store.end_conversation(body.conversation_id, merchant_id)
        return {
            "action": "end",
            "rationale": "Merchant explicitly asked to stop or said the outreach was unwanted; closing the conversation.",
        }

    # Auto-reply detection is tracked at merchant level as well as conversation
    # level because the local simulator may use a fresh conversation ID per turn.
    if detect_auto_reply(message):
        conv.auto_reply_count += 1
        if merchant_id:
            store.auto_reply_by_merchant[merchant_id] = store.auto_reply_by_merchant.get(merchant_id, 0) + 1
            count = store.auto_reply_by_merchant[merchant_id]
        else:
            count = conv.auto_reply_count

        if count >= 3:
            store.end_conversation(body.conversation_id, merchant_id)
            return {
                "action": "end",
                "rationale": "Repeated canned auto-replies show no owner engagement; closing rather than continuing to message.",
            }
        if count == 2:
            return {
                "action": "wait",
                "wait_seconds": 86400,
                "rationale": "The same canned auto-reply appeared repeatedly; backing off for 24 hours to wait for the owner.",
            }
        return {
            "action": "send",
            "body": "Looks like an auto-reply 😊 When the owner sees this, just reply YES if you'd like me to continue.",
            "cta": "binary_yes_no",
            "rationale": "Detected a canned auto-reply; one low-friction prompt is enough before backing off.",
        }

    out_of_scope = detect_out_of_scope(message)
    if out_of_scope:
        return {
            "action": "send",
            "body": (
                f"I’ll leave {out_of_scope} to the appropriate professional — that’s outside what I can help with here. "
                "Coming back to the original Vera task, want me to continue with the next step?"
            ),
            "cta": "open_ended",
            "rationale": "The new request is outside the merchant-growth mission, so it is declined briefly and the original thread is restored.",
        }

    if detect_commitment(message):
        offer = None
        if merchant:
            active = [
                o.get("title") for o in merchant.get("offers", [])
                if o.get("status") == "active" and o.get("title")
            ]
            offer = active[0] if active else None

        body_text = "Great — moving from planning to execution. "
        if offer:
            body_text += f"I’ll use your current {offer} as the concrete anchor. "
        body_text += "Reply CONFIRM and I’ll prepare the next customer-facing draft."
        return {
            "action": "send",
            "body": body_text,
            "cta": "binary_confirm_cancel",
            "rationale": "The merchant explicitly committed, so the conversation switches from qualification to execution.",
        }

    if detect_positive_request(message):
        # Continue the current thread with a concrete artifact instead of another
        # qualification question.
        if conv.trigger_id:
            trigger = store.get_context("trigger", conv.trigger_id) or {}
            kind = trigger.get("kind", "request")
            if kind == "research_digest":
                body_text = "Absolutely — I’ll turn the referenced research into a short merchant summary and a customer-safe draft next."
            elif kind in {"perf_dip", "seasonal_perf_dip"}:
                body_text = "Got it. I’ll keep this focused: one concrete recovery message using the metric that triggered this conversation."
            elif kind == "curious_ask_due":
                body_text = "Perfect. I’ll use your answer to draft the short customer-facing copy, keeping the category voice intact."
            else:
                body_text = "Absolutely. I’ll prepare the next concrete draft from the context we already have."
        else:
            body_text = "Absolutely. I’ll prepare the next concrete draft from the context already available."
        return {
            "action": "send",
            "body": body_text + " Reply CONFIRM when you want me to use it.",
            "cta": "binary_confirm_cancel",
            "rationale": "The merchant engaged positively, so the next response provides an execution step rather than repeating the original pitch.",
        }

    # Default: acknowledge and ask one focused question.
    return {
        "action": "send",
        "body": "Got it. I’ll keep this focused on the original task. What would you like me to handle first?",
        "cta": "open_ended",
        "rationale": "The reply does not contain a clear commitment or opt-out, so the bot asks one focused next-step question.",
    }
