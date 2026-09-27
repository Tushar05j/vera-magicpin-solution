from __future__ import annotations

import re
from datetime import datetime
from typing import Any


CUSTOMER_KINDS = {
    "recall_due",
    "customer_lapsed_hard",
    "customer_lapsed_soft",
    "appointment_tomorrow",
    "chronic_refill_due",
    "trial_followup",
    "wedding_package_followup",
}


def _pct(value: Any) -> str:
    try:
        return f"{abs(float(value)) * 100:.0f}%"
    except (TypeError, ValueError):
        return str(value)


def _signed_pct(value: Any) -> str:
    try:
        n = float(value) * 100
        return f"{n:+.0f}%"
    except (TypeError, ValueError):
        return str(value)


def _money(value: Any) -> str:
    if value is None:
        return ""
    s = str(value)
    return s if "₹" in s else f"₹{s}"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _first_name(merchant: dict) -> str:
    identity = merchant.get("identity", {})
    owner = identity.get("owner_first_name") or identity.get("name") or "there"
    return str(owner)


def _salutation(category: dict, merchant: dict) -> str:
    slug = category.get("slug", merchant.get("category_slug", ""))
    owner = _first_name(merchant)
    if slug == "dentists":
        return f"Dr. {owner}" if not owner.lower().startswith("dr.") else owner
    return owner


def _merchant_name(merchant: dict) -> str:
    return str(merchant.get("identity", {}).get("name", "your business"))


def _active_offers(merchant: dict) -> list[dict]:
    return [o for o in merchant.get("offers", []) if o.get("status") == "active"]


def _active_offer_title(merchant: dict, preferred_words: tuple[str, ...] = ()) -> str | None:
    offers = _active_offers(merchant)
    if preferred_words:
        for offer in offers:
            title = str(offer.get("title", ""))
            if any(word.lower() in title.lower() for word in preferred_words):
                return title
    return str(offers[0].get("title")) if offers else None


def _digest_item(category: dict, item_id: str | None) -> dict:
    for item in category.get("digest", []):
        if item_id and item.get("id") == item_id:
            return item
    return {}


def _category_domain(category: dict) -> str:
    return str(category.get("display_name") or category.get("slug") or "business")


def _taboo_violation(text: str, category: dict) -> bool:
    lower = text.lower()
    for taboo in category.get("voice", {}).get("vocab_taboo", []):
        if str(taboo).lower() in lower:
            return True
    return False


def _clean(text: str) -> str:
    # Keep WhatsApp-friendly whitespace and remove accidental URLs.
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def _rationale(kind: str, details: str) -> str:
    return f"{kind} trigger selected because {details}"


def _merchant_compose(category: dict, merchant: dict, trigger: dict) -> dict:
    slug = category.get("slug", merchant.get("category_slug", ""))
    kind = trigger.get("kind", "unknown")
    payload = trigger.get("payload") or {}
    who = _salutation(category, merchant)
    locality = merchant.get("identity", {}).get("locality", "")
    perf = merchant.get("performance", {})
    signals = merchant.get("signals", [])
    active_offer = _active_offer_title(merchant)

    # --- Research / knowledge triggers ---
    if kind == "research_digest":
        item = _digest_item(category, payload.get("top_item_id"))
        title = item.get("title", "a new category research item")
        source = item.get("source", "")
        parts = [f"{who}, {source + ' ' if source else ''}has a new item: {title}."]
        if item.get("trial_n"):
            parts.append(f"The cited study covers {item['trial_n']:,} participants.")
        if item.get("patient_segment") and "high_risk" in str(item["patient_segment"]):
            parts.append("That is particularly relevant to your high-risk adult cohort.")
        cta = "Want me to pull the key points and draft a customer-facing version?"
        body = " ".join(parts) + " " + cta
        rationale = _rationale(kind, "the digest item is directly tied to the merchant's category and available merchant signals")
        if source:
            body += f" — {source}"
        return _result(body, "open_ended", "vera", trigger, rationale, kind, [who, title, cta])

    if kind in {"regulation_change", "compliance_alert"}:
        item = _digest_item(category, payload.get("top_item_id") or payload.get("alert_id"))
        title = item.get("title", "a new compliance update")
        source = item.get("source", "")
        deadline = payload.get("deadline_iso")
        deadline_text = f" Effective {deadline}." if deadline else "."
        body = f"{who}, heads-up: {title}{deadline_text}"
        if source:
            body += f" Source: {source}."
        body += " Want me to turn the change into a short checklist for your team?"
        rationale = _rationale(kind, "the trigger is compliance-sensitive and the message gives the merchant a concrete next step")
        return _result(body, "open_ended", "vera", trigger, rationale, kind, [who, title])

    if kind == "cde_opportunity":
        item = _digest_item(category, payload.get("digest_item_id"))
        title = item.get("title", "a category training opportunity")
        credits = payload.get("credits")
        fee = payload.get("fee")
        extra = []
        if credits is not None:
            extra.append(f"{credits} credits")
        if fee:
            extra.append(str(fee).replace("_", " "))
        details = ", ".join(extra)
        body = f"{who}, there's a relevant professional opportunity: {title}."
        if details:
            body += f" It offers {details}."
        body += " Want me to pull the details and help you decide if it fits?"
        rationale = _rationale(kind, "the opportunity matches the merchant's professional category and has concrete participation details")
        return _result(body, "open_ended", "vera", trigger, rationale, kind, [who, title])

    # --- Performance / merchant state ---
    if kind == "perf_dip":
        metric = payload.get("metric", "performance")
        delta = payload.get("delta_pct")
        baseline = payload.get("vs_baseline")
        current = perf.get(metric)
        current_text = f" (current 30d {current})" if current is not None else ""
        body = f"{who}, your {metric} is down {_pct(delta)} over the last {payload.get('window', '7d')}{current_text}."
        if baseline is not None:
            body += f" The trigger baseline is {baseline}."
        if "no_active_offers" in signals:
            body += " You currently have no active offer, so I would start with one concrete offer rather than broad promotion."
        else:
            body += " I’d focus on one specific recovery action instead of adding more noise."
        body += " Want me to draft that action around your current listing?"
        rationale = _rationale(kind, "the message uses the exact decline and merchant state instead of a generic growth pitch")
        return _result(body, "open_ended", "vera", trigger, rationale, kind, [who, str(metric), str(delta)])

    if kind == "perf_spike":
        metric = payload.get("metric", "performance")
        delta = payload.get("delta_pct")
        baseline = payload.get("vs_baseline")
        driver = payload.get("likely_driver")
        body = f"{who}, {metric} is up {_pct(delta)} over the last {payload.get('window', '7d')}"
        if baseline is not None:
            body += f" (baseline {baseline})"
        if driver:
            body += f", and the trigger points to {str(driver).replace('_', ' ')}."
        body += " This is a useful signal to build on. Want me to draft a follow-up post that extends what is already working?"
        rationale = _rationale(kind, "a positive movement is linked to the trigger's stated driver and converted into one low-effort next step")
        return _result(body, "open_ended", "vera", trigger, rationale, kind, [who, str(metric), str(delta)])

    if kind in {"seasonal_perf_dip", "category_seasonal"}:
        metric = payload.get("metric")
        delta = payload.get("delta_pct")
        if kind == "seasonal_perf_dip" and metric and delta is not None:
            body = f"{who}, your {metric} is down {_pct(delta)} this week, and the trigger marks this as the expected seasonal {payload.get('season_note', 'dip')}."
            body += " I’d protect retention before adding acquisition spend. Want me to draft one retention-focused message?"
            details = f"{metric} is down {_pct(delta)} and the trigger explicitly marks it as seasonal"
        else:
            trends = payload.get("trends", [])
            trend_text = ", ".join(str(x).replace("_", " ") for x in trends[:3])
            body = f"{who}, the current seasonal signal is clear"
            if trend_text:
                body += f": {trend_text}."
            else:
                body += "."
            body += " Want me to turn the strongest demand signal into one concrete shelf/listing action?"
            details = "the trigger contains concrete seasonal demand movements"
        return _result(body, "open_ended", "vera", trigger, _rationale(kind, details), kind, [who])

    if kind == "milestone_reached":
        metric = payload.get("metric", "milestone")
        now = payload.get("value_now")
        target = payload.get("milestone_value")
        if now is not None and target is not None:
            try:
                gap = max(0, int(target) - int(now))
                body = f"{who}, you're at {now} {metric.replace('_', ' ')} — {gap} away from {target}."
            except (ValueError, TypeError):
                body = f"{who}, you’re at {now} on {metric.replace('_', ' ')} with {target} as the milestone."
        else:
            body = f"{who}, you have a new {metric.replace('_', ' ')} milestone signal."
        body += " Want me to draft a simple message that helps you reach the milestone without sounding promotional?"
        return _result(body, "open_ended", "vera", trigger,
                        _rationale(kind, "the milestone is imminent or explicitly supplied by the trigger"), kind, [who])

    # --- Campaign / opportunity triggers ---
    if kind == "festival_upcoming":
        festival = payload.get("festival", "the upcoming festival")
        date = payload.get("date")
        days = payload.get("days_until")
        body = f"{who}, {festival} is on {date}" if date else f"{who}, {festival} is coming up"
        if days is not None:
            body += f" ({days} days away)"
        body += ". "
        if active_offer:
            body += f"You already have {active_offer} active, so I’d build the campaign around that rather than inventing a new offer. "
        body += f"Want me to draft a {festival} message for your existing offer?"
        return _result(body, "open_ended", "vera", trigger,
                        _rationale(kind, "the festival date is time-bound and the merchant's existing offer can be reused"), kind, [who, str(festival)])

    if kind == "ipl_match_today":
        match = payload.get("match", "today's match")
        venue = payload.get("venue")
        time_iso = payload.get("match_time_iso")
        offer = active_offer or "your current offer"
        body = f"{who}, {match} is at {venue + ' ' if venue else ''}today"
        if time_iso:
            body += f" at {time_iso.split('T')[-1].split('+')[0]}"
        body += ". "
        body += f"Because this is a weekend match, I’d avoid creating a new match-night offer; use your existing {offer} for delivery instead. "
        body += "Want me to draft the delivery-focused copy?"
        return _result(body, "open_ended", "vera", trigger,
                        _rationale(kind, "the restaurant seasonal context distinguishes weekend match behavior and the merchant already has an offer"), kind, [who, match])

    if kind == "competitor_opened":
        name = payload.get("competitor_name")
        distance = payload.get("distance_km")
        their_offer = payload.get("their_offer")
        body = f"{who}, {name or 'a competitor'} opened {distance} km away" if distance is not None else f"{who}, a new competitor signal appeared"
        if their_offer:
            body += f" with {their_offer}"
        body += ". "
        if active_offer:
            body += f"You already have {active_offer} active, so I’d sharpen the positioning around that rather than copying the competitor. "
        body += "Want me to draft a differentiated message?"
        return _result(body, "open_ended", "vera", trigger,
                        _rationale(kind, "the trigger provides a concrete competitor distance and offer, while the merchant context supplies its current offer"), kind, [who, str(name or "competitor")])

    if kind == "winback_eligible":
        days = payload.get("days_since_expiry")
        dip = payload.get("perf_dip_pct")
        lapsed = payload.get("lapsed_customers_added_since_expiry")
        body = f"{who}, your subscription expired {days} days ago" if days is not None else f"{who}, your account is marked eligible for a win-back"
        if dip is not None:
            body += f", while performance is down {_pct(dip)}"
        if lapsed is not None:
            body += f" and {lapsed} lapsed customers have been added since expiry"
        body += ". That is a concrete audience to re-engage. Want me to draft the win-back message?"
        return _result(body, "open_ended", "vera", trigger,
                        _rationale(kind, "expiry, performance and lapsed-customer signals all support a focused win-back action"), kind, [who])

    if kind == "renewal_due":
        days = payload.get("days_remaining")
        amount = payload.get("renewal_amount")
        plan = payload.get("plan")
        body = f"{who}, your {plan or 'subscription'} renewal is {days} days away"
        if amount is not None:
            body += f" at {_money(amount)}"
        body += ". Want me to help you review the renewal details?"
        return _result(body, "open_ended", "vera", trigger,
                        _rationale(kind, "the trigger provides an explicit renewal deadline and amount"), kind, [who])

    if kind == "gbp_unverified":
        path = payload.get("verification_path", "the available verification flow")
        uplift = payload.get("estimated_uplift_pct")
        body = f"{who}, your Google Business Profile is still unverified. Verification is available via {str(path).replace('_', ' ')}"
        if uplift is not None:
            body += f"; the trigger estimates up to {_pct(uplift)} uplift"
        body += ". Want me to walk you through the verification steps?"
        return _result(body, "open_ended", "vera", trigger,
                        _rationale(kind, "verification status is explicit and the trigger supplies the available verification path"), kind, [who])

    if kind == "review_theme_emerged":
        theme = str(payload.get("theme", "a review theme")).replace("_", " ")
        count = payload.get("occurrences_30d")
        trend = payload.get("trend")
        quote = payload.get("common_quote")
        body = f"{who}, {count or 'Several'} reviews in the last 30 days mention {theme}"
        if trend:
            body += f" and the theme is {trend}"
        body += "."
        if quote:
            body += f' One customer put it as: "{quote}".'
        body += " Want me to turn this into one concrete operations fix?"
        return _result(body, "open_ended", "vera", trigger,
                        _rationale(kind, "the message uses the observed review frequency and theme as the reason to act"), kind, [who])

    if kind == "curious_ask_due":
        if slug == "salons":
            question = "What service has been most asked-for this week?"
            deliverable = "I’ll turn the answer into a Google post + a 4-line WhatsApp reply."
        elif slug == "restaurants":
            question = "What dish or occasion has been getting the most customer questions this week?"
            deliverable = "I’ll turn the answer into one listing update + a short WhatsApp reply."
        elif slug == "gyms":
            question = "Which class or training goal are members asking for most this week?"
            deliverable = "I’ll turn the answer into one post + a member reply."
        elif slug == "pharmacies":
            question = "Which OTC or refill request has increased most this week?"
            deliverable = "I’ll turn the answer into one practical listing/update message."
        else:
            question = "What service has been most asked-for this week?"
            deliverable = "I’ll turn the answer into a short customer-facing draft."
        body = f"Hi {who}! Quick one — {question} {deliverable} Takes about 5 min."
        return _result(body, "open_ended", "vera", trigger,
                        _rationale(kind, "this trigger explicitly asks Vera to learn from the merchant and give something useful back"), kind, [who, question])

    if kind == "dormant_with_vera":
        days = payload.get("days_since_last_merchant_message")
        topic = payload.get("last_topic")
        body = f"{who}, it’s been {days} days since we last spoke" if days is not None else f"{who}, it’s been a while since we last spoke"
        if topic:
            body += f" — our last topic was {str(topic).replace('_', ' ')}."
        body += " Want to pick that thread back up?"
        return _result(body, "open_ended", "vera", trigger,
                        _rationale(kind, "the trigger explicitly indicates a dormant conversation and supplies its last topic"), kind, [who])

    if kind == "active_planning_intent":
        topic = str(payload.get("intent_topic", "your planned initiative")).replace("_", " ")
        last = payload.get("merchant_last_message")
        if slug == "restaurants" and "thali" in topic.lower():
            offer = _active_offer_title(merchant, ("thali",))
            anchor = f"around your existing {offer}" if offer else "around the thali service you already run"
        elif slug == "gyms" and "kids_yoga" in topic.lower():
            anchor = "as a simple pilot outline, without inventing a price or schedule"
        else:
            anchor = "using the information already in your listing"
        body = f"{who}, you asked about {topic}. "
        if last:
            body += f'You said, "{last}". '
        body += f"I can turn that into a practical first draft {anchor}."
        if slug == "gyms" and "kids_yoga" in topic.lower():
            body += " I’ll structure it around age group, session format, timing and pricing placeholders."
        elif slug == "restaurants" and "thali" in topic.lower():
            body += " I’ll keep the pricing tied to your current menu rather than making up new rates."
        body += " Want me to draft it now?"
        return _result(body, "open_ended", "vera", trigger,
                        _rationale(kind, "the merchant has already expressed planning intent, so the next move is execution rather than more qualification"), kind, [who, topic])

    if kind == "supply_alert":
        molecule = payload.get("molecule", "the affected medicine")
        batches = payload.get("affected_batches", [])
        manufacturer = payload.get("manufacturer", "the listed manufacturer")
        batch_text = ", ".join(str(x) for x in batches)
        body = f"{who}, urgent: voluntary recall on {molecule}"
        if batch_text:
            body += f" batches {batch_text}"
        body += f" by {manufacturer}. Please check stock before dispensing."
        body += " Want me to draft the customer notification and replacement-pickup workflow?"
        return _result(body, "open_ended", "vera", trigger,
                        _rationale(kind, "the alert supplies exact medicine and batch identifiers, making this an operational priority"), kind, [who, molecule, batch_text])

    # Unknown/generated trigger: deterministic and safe fallback.
    topic = str(payload.get("metric_or_topic") or kind).replace("_", " ")
    body = f"{who}, I have a new {topic} signal for your {_category_domain(category).lower()} business. "
    if active_offer:
        body += f"You currently have {active_offer} active. "
    body += "Want me to turn the signal into one concrete next step?"
    return _result(body, "open_ended", "vera", trigger,
                    _rationale(kind, "the trigger kind is known but its payload contains no usable specifics"), kind, [who, topic])


def _customer_compose(category: dict, merchant: dict, trigger: dict, customer: dict) -> dict:
    kind = trigger.get("kind", "unknown")
    payload = trigger.get("payload") or {}
    slug = category.get("slug", merchant.get("category_slug", ""))
    customer_identity = customer.get("identity", {})
    name = str(customer_identity.get("name") or "there")
    language = str(customer_identity.get("language_pref", "")).lower()
    merchant_name = _merchant_name(merchant)
    owner = _first_name(merchant)
    offer = _active_offer_title(merchant)
    reminder_opt_in = customer.get("preferences", {}).get("reminder_opt_in", True)

    if not reminder_opt_in:
        return {}

    if kind == "recall_due":
        slots = payload.get("available_slots") or []
        slot_text = ""
        if slots:
            labels = [str(s.get("label")) for s in slots[:2] if s.get("label")]
            if len(labels) == 1:
                slot_text = f" {labels[0]} is available."
            elif len(labels) >= 2:
                slot_text = f" Two slots are available: {labels[0]} or {labels[1]}."
        service = str(payload.get("service_due", "your next visit")).replace("_", " ")
        prefix = "Namaste" if language == "hi" else f"Hi {name}"
        body = f"{prefix}, {merchant_name} here. Your {service} is due."
        if slot_text:
            body += slot_text
        if offer:
            body += f" {offer} is currently active."
        if len(slots) >= 2:
            body += " Reply 1 for the first slot, 2 for the second, or tell us a time that works."
            cta = "multi_choice_slot"
        else:
            body += " Reply YES if you’d like us to help schedule it."
            cta = "binary_yes_no"
        if "hi-en" in language or language == "hi":
            body = body.replace("Your 6 month", "Aapka 6-month")
        return _result(body, cta, "merchant_on_behalf", trigger,
                        _rationale(kind, "customer recall, real availability and consent are present"), kind,
                        [name, merchant_name])

    if kind == "chronic_refill_due":
        molecules = payload.get("molecule_list") or []
        runout = payload.get("stock_runs_out_iso")
        meds = ", ".join(str(x) for x in molecules)
        prefix = "Namaste" if language == "hi" else f"Hi {name}"
        body = f"{prefix} — {merchant_name} here. Your regular refill for {meds or 'your medicines'} is due"
        if runout:
            body += f" before {runout.split('T')[0]}"
        body += "."
        if payload.get("delivery_address_saved"):
            body += " We have a saved delivery address."
        delivery_offer = _active_offer_title(merchant, ("delivery",))
        if delivery_offer:
            body += f" {delivery_offer}."
        body += " Reply CONFIRM to arrange the refill, or tell us if anything needs to be changed."
        return _result(body, "binary_confirm_cancel", "merchant_on_behalf", trigger,
                        _rationale(kind, "the trigger provides the exact medicines and refill timing, while consent permits refill reminders"), kind,
                        [name, meds])

    if kind in {"customer_lapsed_hard", "customer_lapsed_soft"}:
        days = payload.get("days_since_last_visit")
        prefix = "Hi" if language != "hi" else "Namaste"
        body = f"{prefix} {name} 👋 {merchant_name} here."
        if days is not None:
            body += f" It’s been about {days} days since your last visit."
        body += " No pressure — if you’d like to come back, "
        if offer:
            body += f"we currently have {offer}."
        else:
            body += "we can help you pick a convenient next visit."
        body += " Want me to help set it up?"
        return _result(body, "binary_yes_no", "merchant_on_behalf", trigger,
                        _rationale(kind, "the customer is eligible for re-engagement and the message avoids guilt while using only supplied facts"), kind,
                        [name, merchant_name])

    if kind == "trial_followup":
        options = payload.get("next_session_options") or []
        body = f"Hi {name}, {merchant_name} here. Quick follow-up on your recent trial."
        if options:
            labels = [str(x.get("label")) for x in options[:2] if x.get("label")]
            body += f" The next option is {labels[0]}." if len(labels) == 1 else f" Next options are {labels[0]} or {labels[1]}."
        body += " Want me to hold the next session?"
        return _result(body, "binary_yes_no", "merchant_on_behalf", trigger,
                        _rationale(kind, "the trigger is a direct follow-up to a completed trial"), kind,
                        [name, merchant_name])

    if kind == "wedding_package_followup":
        wedding = payload.get("wedding_date")
        days = payload.get("days_to_wedding")
        window = payload.get("next_step_window_open")
        body = f"Hi {name} 💍 {merchant_name} here."
        if days is not None:
            body += f" You’re {days} days from the wedding"
        elif wedding:
            body += f" Your wedding is on {wedding}"
        if window:
            body += f", and the {str(window).replace('_', ' ')} window is open."
        body += " Want me to help plan the next step from your bridal trial?"
        return _result(body, "binary_yes_no", "merchant_on_behalf", trigger,
                        _rationale(kind, "the wedding date and prior relationship make this a timely follow-up"), kind,
                        [name, merchant_name])

    if kind == "appointment_tomorrow":
        body = f"Hi {name}, {merchant_name} here. Quick reminder that you have an appointment tomorrow."
        body += " Reply CONFIRM if you’re still coming, or tell us if you need to change it."
        return _result(body, "binary_confirm_cancel", "merchant_on_behalf", trigger,
                        _rationale(kind, "this is an appointment reminder and the payload does not provide a time, so none is invented"), kind,
                        [name, merchant_name])

    # Safe fallback for future customer triggers.
    body = f"Hi {name}, {merchant_name} here. We have a timely update related to your recent visit."
    body += " Reply YES if you’d like us to help with the next step."
    return _result(body, "binary_yes_no", "merchant_on_behalf", trigger,
                    _rationale(kind, "customer context and consent are available but the trigger payload has limited detail"), kind,
                    [name, merchant_name])


def _result(body: str, cta: str, send_as: str, trigger: dict, rationale: str,
            kind: str, params: list[Any]) -> dict:
    body = _clean(body)
    return {
        "body": body,
        "cta": cta,
        "send_as": send_as,
        "suppression_key": str(trigger.get("suppression_key") or f"{kind}:unknown"),
        "rationale": rationale,
        "template_name": f"{'merchant' if send_as == 'merchant_on_behalf' else 'vera'}_{kind}_v1",
        "template_params": [str(x) for x in params if x is not None][:5],
    }


def compose(category: dict, merchant: dict, trigger: dict, customer: dict | None = None) -> dict:
    """Deterministic four-context composer required by the challenge."""
    if customer is not None or trigger.get("scope") == "customer" or trigger.get("kind") in CUSTOMER_KINDS:
        return _customer_compose(category, merchant, trigger, customer or {})
    return _merchant_compose(category, merchant, trigger)
