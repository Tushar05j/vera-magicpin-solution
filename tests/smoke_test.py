import json
import urllib.request
import urllib.error


BASE = "http://127.0.0.1:8080"


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(r, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode())


category = {
    "slug": "dentists",
    "display_name": "Dentists",
    "voice": {"tone": "peer_clinical", "vocab_taboo": ["guaranteed"]},
    "offer_catalog": [{"title": "Dental Cleaning @ ₹299", "value": "299"}],
    "peer_stats": {"avg_ctr": 0.03},
    "digest": [{
        "id": "d_test",
        "kind": "research",
        "title": "3-month recall item",
        "source": "Test Journal p.14",
        "trial_n": 2100,
        "patient_segment": "high_risk_adults",
    }],
}
merchant = {
    "merchant_id": "m_test",
    "category_slug": "dentists",
    "identity": {
        "name": "Test Dental Clinic",
        "city": "Delhi",
        "locality": "Lajpat Nagar",
        "languages": ["en", "hi"],
        "owner_first_name": "Meera",
    },
    "performance": {"views": 2410, "calls": 18, "ctr": 0.021},
    "offers": [{"title": "Dental Cleaning @ ₹299", "status": "active"}],
    "signals": ["high_risk_adult_cohort"],
}
customer = {
    "customer_id": "c_test",
    "merchant_id": "m_test",
    "identity": {"name": "Priya", "language_pref": "hi-en mix"},
    "preferences": {"reminder_opt_in": True},
    "consent": {"opted_in_at": "2025-01-01", "scope": ["recall_reminders"]},
}
trigger = {
    "id": "trg_test",
    "scope": "merchant",
    "kind": "research_digest",
    "source": "external",
    "merchant_id": "m_test",
    "customer_id": None,
    "payload": {"category": "dentists", "top_item_id": "d_test"},
    "urgency": 2,
    "suppression_key": "research:test",
    "expires_at": "2030-01-01T00:00:00Z",
}

for scope, cid, payload in [
    ("category", "dentists", category),
    ("merchant", "m_test", merchant),
    ("customer", "c_test", customer),
    ("trigger", "trg_test", trigger),
]:
    status, out = req("POST", "/v1/context", {
        "scope": scope,
        "context_id": cid,
        "version": 1,
        "payload": payload,
        "delivered_at": "2026-04-26T10:00:00Z",
    })
    assert status == 200 and out["accepted"] is True, out

try:
    req("POST", "/v1/context", {
        "scope": "merchant",
        "context_id": "m_test",
        "version": 1,
        "payload": merchant,
        "delivered_at": "2026-04-26T10:00:00Z",
    })
    raise AssertionError("Expected HTTP 409 for stale version")
except urllib.error.HTTPError as e:
    assert e.code == 409

status, tick = req("POST", "/v1/tick", {
    "now": "2026-11-01T10:00:00Z",
    "available_triggers": ["trg_test"],
})
assert status == 200 and tick["actions"], tick

action = tick["actions"][0]
required = {
    "conversation_id", "merchant_id", "customer_id", "send_as",
    "trigger_id", "template_name", "template_params", "body",
    "cta", "suppression_key", "rationale",
}
assert required.issubset(action), action

print("SMOKE TEST PASSED")
print(json.dumps(action, indent=2, ensure_ascii=False))
