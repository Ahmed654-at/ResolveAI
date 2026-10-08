# ResolveAI — Autonomous Customer Refund Agent

## What this project is
An AI agent that investigates customer refund requests and either resolves them on its own
or escalates risky cases to a human. It is a portfolio project, so it must be easy to demo,
easy to explain, and it must have measured results.

The agent works through each case in this order:
**understand the request → gather evidence → check refund policy → decide → refund or escalate**

## About me (the developer)
- I'm building this solo to learn agentic AI properly, not just to get working code.
- I learn by building. Explain *why* you wrote something, in plain language, after each step.
- I know workflow automation (I used n8n before), but I'm newer to writing agents in Python.

## How to work with me
1. **Plan first.** For any new feature, show me a short plan and wait for my OK before writing code.
2. **One small piece at a time.** Build one thing, help me test it, then stop. Don't build ahead.
3. **Keep it simple.** No frameworks unless I ask. A plain Python state machine is preferred over LangGraph.
4. **Explain after each step.** 3–5 sentences on what you built and how it works.
5. **Tell me when to commit.** Remind me to commit to git after each piece works.
6. Never put API keys in code. Read them from `.env`.

## Tech stack
- Python 3.11+, virtual environment in `.venv`
- **Agent model: NVIDIA Nemotron 3 Ultra via OpenRouter**, called with the `openai` Python SDK
  (OpenRouter is OpenAI-compatible). We write the agent loop ourselves using **tool calling**
  (`tools` + `tool_choice`), so it's easy to understand.
- SQLite for the fake business data (`data/resolveai.db`)
- Streamlit for the operator dashboard
- `python-dotenv` for config, `pydantic` to validate tool arguments, `pytest` for tests
- Claude Code is my coding assistant only. The agent itself runs on Nemotron.

## Model configuration (.env)
```
OPENROUTER_API_KEY=sk-or-...
BASE_URL=https://openrouter.ai/api/v1
MODEL=nvidia/nemotron-3-ultra-550b-a55b:free   # paid route: nvidia/nemotron-3-ultra-550b-a55b
```
- Keep the model and base URL in `.env` only, so switching models or providers is a one-line change.
- Put all model calls in one file, `agent/llm.py`. No other file talks to the API directly.

## Nemotron-specific rules
- **The free route is rate-limited and sometimes fails or returns an empty reply.** Every call in
  `llm.py` needs retries with backoff (3 attempts) and a timeout. If all attempts fail, escalate the
  case with the reason "model unavailable". Never crash.
- **Structured JSON output (`response_format`) isn't enforced on the free route.** Validate every
  tool call's arguments with pydantic. If they're invalid, send the error back to the model once and
  ask it to fix them. If they're still invalid, escalate.
- **The free route logs prompts.** Use only the fake data from `seed.py`. Never send real customer data.
- Reasoning mode can be turned on with OpenRouter's `reasoning` parameter. Start with it off for speed
  and cost, and test turning it on later as an eval experiment.
- Count failed and empty model calls in the evals, so the README shows how reliable the model was.

## Folder structure
```
resolveai/
├── CLAUDE.md
├── .env                  # OPENROUTER_API_KEY, BASE_URL, MODEL (never commit)
├── .env.example
├── requirements.txt
├── data/
│   ├── seed.py           # creates the fake database
│   ├── resolveai.db
│   └── refund_policy.md  # the policy the agent must follow
├── agent/
│   ├── llm.py            # the ONLY file that calls the model (retries, timeout, validation)
│   ├── tools.py          # tool functions + tool schemas for the model
│   ├── rules.py          # auto-resolve vs escalate rules (plain Python, not the LLM)
│   ├── loop.py           # the agent loop: call the model → run tools → repeat
│   └── logger.py         # logs every step to the database
├── app/
│   └── dashboard.py      # Streamlit: submit cases, approve/reject escalations, view logs
├── evals/
│   ├── cases.json        # test cases with the expected outcome
│   └── run_evals.py      # runs all cases and prints the metrics
└── tests/
```

## Fake data (data/seed.py)
- `customers`: id, name, email, signup_date, refunds_last_90_days
- `orders`: id, customer_id, product, amount, order_date, delivery_status
- `refunds`: id, order_id, amount, status (approved / pending_review / rejected), decided_by (agent / human)
- `agent_logs`: case_id, step, tool_name, input, output, timestamp
- About 30 customers and 100 orders, including some suspicious ones (many recent refunds, very high amounts).

## Agent tools
| Tool | What it does | Risk |
|---|---|---|
| `lookup_order(order_id)` | Order details and delivery status | read |
| `get_customer_history(customer_id)` | Past orders and refunds | read |
| `search_refund_policy(question)` | Searches `refund_policy.md` | read |
| `fraud_check(customer_id, order_id)` | Returns a risk score from 0 to 100 using simple rules | read |
| `create_refund(order_id, amount)` | Issues the refund | **write** |
| `escalate_to_human(case_id, reason)` | Sends the case to the dashboard queue | write |

## Decision rules (enforced in `rules.py`, not left to the LLM)
The agent may auto-refund **only if all** of these are true:
- amount is $100 or less
- the order is within 30 days of delivery
- the fraud score is under 40
- the customer has fewer than 3 refunds in the last 90 days

Anything else is escalated to a human. `create_refund` must check these rules itself and refuse
if they fail, even if the model asks for it.

## Safety limits
- Maximum **10 steps** per case. After that, stop and escalate with the reason "step limit reached".
- If the same tool is called with the same input twice in a row, treat it as a loop and escalate.
- Every tool call and decision is written to `agent_logs`.

## How we measure it (evals/run_evals.py)
- Correct-decision rate: did it refund or escalate when it should have?
- Autonomous resolution rate: the share of cases solved with no human
- Escalation accuracy: were the escalated cases actually risky?
- Average steps per case, average cost per case, average time per case

## Commands
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python data/seed.py              # create the fake database
python -m agent.loop             # run one case from the terminal
streamlit run app/dashboard.py   # open the dashboard
python evals/run_evals.py        # run the evaluation
pytest                           # run tests
```

## Build order (one at a time; tick off as done)
- [x] 1. Project setup, `requirements.txt`, `.env.example`
- [x] 2. `seed.py` with the fake database and `refund_policy.md`
- [x] 3. First tool, `lookup_order`, plus a minimal agent loop that can call it
- [x] 4. The other read tools
- [x] 5. `rules.py` and `create_refund` / `escalate_to_human`
- [x] 6. Step limit, loop detection, logging
- [x] 7. Streamlit dashboard
- [ ] 8. 20+ eval cases and `run_evals.py`
- [ ] 9. README with the eval results and a demo GIF
