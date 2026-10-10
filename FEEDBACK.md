# Feedback log (Nebius Token Factory, AI Cloud, NVIDIA tools)

The hackathon requires written feedback, and "Most Valuable Feedback" is its own prize
(judged on completeness, viability, and potential impact). Add an entry every time you hit
something, good or bad. Be specific: what you tried, what happened, what you expected,
what would fix it.

## Template

- **Date / tool / model:**
- **What I tried:**
- **What happened:**
- **What I expected:**
- **Suggestion:**

## Entries

### 2026-10-10 / docs / Token Factory
- Found the docs index at `llms.txt` quickly: very helpful for agents and humans.
- Sandboxes (contree-sdk) are a great fit for coding agents, but the quickstart pins `contree-sdk==0.3.6` and Python 3.12. Worth saying clearly how this relates to Serverless Jobs.
- The structured-output docs show `response_format.json_schema` two ways (raw schema vs `{name, schema, strict}`) and the cURL example has a typo (`"response format"` and a missing comma). Worth tidying.
- The docs and model pages did not make the exact Nemotron model IDs easy to find from outside the console; I added a `--list` script to discover them from `/v1/models`.
- Observation (unconfirmed, being measured with `scripts/json_mode_check.py`): with `response_format=json_schema`, Nemotron Super sometimes returned words glued together in string fields ("onpayment", "ifwould"). Not seen as often in free-text replies. A note in the docs on quality trade-offs of constrained decoding would help when generating code inside JSON.