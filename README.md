# strategy_lab

> **TEACHING SANDBOX.** Makes zero real exchange calls, holds no API keys, trades
> only against an in-memory paper broker + synthetic price feed defined in this
> repo. Not connected to `sat_strategy` or any real account.

A worked example of composing trading strategies from three layers, built
incrementally so each layer is runnable and testable before the next is
added:

1. **Plugin layer** — independently-testable entry/exit/time-window modules
   behind a small registry.
2. **Rule engine** — composable conditions (`And`/`Or`/`Not`) that decide
   *when* a plugin fires.
3. **DSL** — strategies described as YAML data instead of Python code, so a
   new strategy can be assembled by editing config, not writing code.

Two demo strategies drive every phase so the module boundaries actually get
exercised instead of just changing constants:

- **Weekend mean-reversion** — ported from `/Users/mac/sat_strategy`. Entry
  when price deviates X% below a fixed reference, exit back at that
  reference, weekly HKT Sat 04:00 → Mon 06:00 window.
- **MA-crossover with bracket TP/SL** — entry on a fast/slow SMA crossover,
  exit on take-profit OR stop-loss relative to entry price, daily intraday
  session window.

## Setup

```bash
/opt/anaconda3/bin/python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest -v
```

## Status

- [ ] Phase 1 — Plugin layer
- [ ] Phase 2 — Rule engine
- [ ] Phase 3 — DSL
- [ ] Phase 4 — Capstone

Porting the real `sat_strategy/app/bot.py` onto this architecture is a
separate follow-up, only after all four phases here are validated against
both demo strategies.
