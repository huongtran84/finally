# FinAlly — AI Trading Workstation

A visually stunning AI-powered trading workstation that streams live market data, lets users trade a simulated portfolio, and integrates an LLM chat assistant that can analyze positions and execute trades. Inspired by Bloomberg terminals, built entirely by coding agents.

## What It Does

- **Live price streaming** via SSE with green/red flash animations and sparkline charts
- **Simulated trading** — start with $10k virtual cash, buy/sell with market orders, fractional shares supported
- **Portfolio visualization** — heatmap (treemap) sized by weight, colored by P&L, plus a value-over-time chart
- **AI chat assistant** — ask about your portfolio, get analysis, and have the AI execute trades via natural language

## Architecture

Single Docker container serving everything on port 8000:

- **Frontend**: Next.js (TypeScript), static export served by FastAPI
- **Backend**: FastAPI (Python/uv) — REST API, SSE streaming, LLM integration
- **Database**: SQLite (volume-mounted for persistence)
- **Market data**: Built-in simulator (default) or Massive API with a key
- **AI**: LiteLLM via OpenRouter (Cerebras inference)

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env with your OPENROUTER_API_KEY

# 2. Run
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally

# 3. Open
open http://localhost:8000
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key for LLM chat |
| `MASSIVE_API_KEY` | No | Massive (Polygon.io) key for real market data; omit to use simulator |
| `LLM_MOCK` | No | Set `true` for deterministic mock LLM responses (testing) |
| `LLM_MODEL` | No | Override LLM model (default: `openrouter/openai/gpt-oss-120b`) |

## Project Structure

```
finally/
├── frontend/          # Next.js TypeScript (static export)
├── backend/           # FastAPI Python (uv project)
├── planning/          # Project docs and specs
├── scripts/           # Docker start/stop helpers
├── test/              # Playwright E2E tests
├── db/                # SQLite volume mount point
└── Dockerfile         # Multi-stage build (Node -> Python)
```

## Documentation

See `planning/PLAN.md` for the full project specification.

## License

See [LICENSE](LICENSE).
