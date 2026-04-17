# GhostCore — Agent Instructions

## Running the Application

```bash
python main.py
```

Single entry point. Requires Ollama running for AI tasks. If Ollama is offline, system runs in STANDBY mode — UI stays active but AI tasks blocked until `/reconnect`.

## Developer Commands

```bash
pip install -r Requirements.txt    # Install deps
ruff check .                     # Lint (not black)
mypy .                           # Type check
bandit -r .                      # Security audit
pytest                           # Run tests
```

## Available CLI Commands

- `/reconnect` — Retry Ollama connection
- `/maestro merge <folder> [output.xlsx]` — Merge CSV/JSON to Excel
- `/maestro sql <db>|<query>|[output.xlsx]` — SQL query to Excel
- `/designer preview [html]` — Serve HTML preview on port 8000
- `/schedule <seconds> <message>` — Delayed message (cron-style)
- `/warroom` — Agent discussion on topic
- `/silent` / `/verbose` — Toggle output verbosity
- `stats` / `cache` / `tokens` — System info
- `exit` — Quit

## Architecture Notes

- **Framework**: CrewAI + LangChain with local Ollama backend
- **Backend selection**: `brain.py:get_llm()` auto-falls back to local (phi3/llama3) if cloud unavailable
- **Async-first**: All UI and agents run on single asyncio event loop; non-blocking
- **Soft-fail**: Ollama offline → OLLAMA_STATUS="OFFLINE", system continues in limited mode
- **Session persistence**: `data/session_state.json` saves last topic/mode between launches
- **Long-term memory**: `data/long_term_memory.json` stores developer preferences

## Testing

```bash
pytest                           # Run all tests
pytest test_calculator.py        # Run specific test
```

Tests use pytest-asyncio with `asyncio_mode = "auto"`.

## .env Configuration

```
OPENAI_API_KEY=           # Optional: cloud fallback
OLLAMA_BASE_URL=         # Default: http://localhost:11434
OLLAMA_MODEL=llama3:8b   # Default model
GHOST_MODE=HYBRID        # LOCAL or HYBRID
MEMORY_BACKEND=json      # json or chromadb
SANDBOX_MODE=manual      # manual, subprocess, or docker
```