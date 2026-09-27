# VERA Challenge — Deterministic Merchant AI Bot

## Approach
This submission uses a deterministic, rule-first architecture:
1. `/v1/context` stores versioned Category, Merchant, Customer and Trigger contexts.
2. `/v1/tick` routes each active trigger to a category-aware composer.
3. The composer uses only facts present in the pushed contexts and selects one clear CTA.
4. `/v1/reply` handles opt-outs, repeated auto-replies, explicit intent transitions and normal follow-ups.
5. State is kept in memory for low latency and deterministic behavior.

No merchant/customer payload is sent to an external API. The design is intentionally fast enough for the 30-second judge budget.

## Run
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8080
```

Local URL: `http://localhost:8080`

## Test
From the project directory:
```bash
curl http://localhost:8080/v1/healthz
python tests/smoke_test.py
```

For the official simulator, copy the supplied `judge_simulator.py` into this directory, configure its LLM provider/key, set `BOT_URL=http://localhost:8080`, then run it.

## Submission
Deploy the project so the public base URL exposes:
- `GET /v1/healthz`
- `GET /v1/metadata`
- `POST /v1/context`
- `POST /v1/tick`
- `POST /v1/reply`

The supplied `submission.jsonl` contains the 30 canonical pairs generated from the challenge dataset.

## Important
Keep the process alive during judging because the judge pushes context incrementally. Do not replace context version `N` with an older version. Never invent prices, research claims, customer details, competitor facts, or appointment times.
