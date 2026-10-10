# Tessera

**Hire an AI software agency that proves what it built.**

Other tools generate code. Tessera delivers *verified* software: you describe a small SaaS idea,
an AI account manager turns it into numbered requirements with acceptance criteria, a team of
agents builds it, a tester agent verifies each requirement in a sandbox, and a live dashboard
shows "17 of 20 requirements verified passing", with reasons for the rest.

> A *tessera* was the Roman token used to verify an identity or a promise. Every passing
> requirement is one.

Built for the **Nebius x NVIDIA Global AI Hackathon**, track: **Coding and Agentic Engineering**.

> Status: day 1 scaffold. Agents, dashboard and sandbox runner are in progress.

## How it works

| Role | Job | Nemotron tier |
|---|---|---|
| Main AI (account manager) | Client chat, requirements, plain-language updates | Ultra |
| TL AI | Task breakdown, standups, re-planning on change requests | Super |
| Senior developer AI | Writes code into a fixed scaffold | Super |
| Tester AI | Writes and runs tests per requirement | Nano |
| Status summaries | Standup and status updates | Nano |

All calls go through `tessera/llm.py`, which routes each role to its Nemotron size, tracks
tokens and latency per role, and enforces a hard token budget per project.

## Nebius and NVIDIA usage

- **NVIDIA Nemotron 3** (Ultra, Super, Nano) for every agent role, via an OpenAI-compatible API.
- **Nebius Token Factory** inference API at runtime (`https://api.tokenfactory.nebius.com/v1/`),
  including structured JSON output (`response_format: json_schema`) for requirements and tasks.
- **Token Factory Sandboxes** (`contree-sdk`) to run generated code and tests in isolation (in progress).
- **Tavily** to ground requirements briefs in real competitor research.

*(Section to be completed with real numbers: where Token Factory accelerated the workflow,
per-role latency and cost.)*

## Setup

Requires Python 3.12+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in your keys
python scripts/hello_nemotron.py --list   # find Nemotron model IDs, put them in .env
python scripts/hello_nemotron.py          # one test call per tier
pytest -q                                  # smoke tests (no API keys needed)
uvicorn tessera.main:app --reload          # http://127.0.0.1:8000/health
```

## API (current)

- `GET /health` configuration check
- `POST /api/projects` `{ "idea": "..." }` create a project
- `GET /api/projects/{id}` project, requirements, verified summary
- `GET /api/projects/{id}/events` live event stream (server-sent events)

## Guardrails

Hard caps on rounds (`TESSERA_MAX_ROUNDS`) and tokens per project
(`TESSERA_MAX_TOKENS_PER_PROJECT`) so agents cannot loop or overspend.

## Feedback

See [FEEDBACK.md](FEEDBACK.md) for notes on Token Factory, AI Cloud, and NVIDIA tooling.

## License

MIT, see [LICENSE](LICENSE).
