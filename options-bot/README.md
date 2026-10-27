# optionsbot: a rules-based options trading bot

optionsbot trades US equity and ETF options through [Alpaca](https://alpaca.markets) by
following the routine a disciplined options trader uses. It reads the trend and the
volatility regime, picks a defined-risk structure that fits, chooses strikes by delta,
sizes the position from a fixed risk budget, and works limit orders from mid. Once a trade
is open, it manages it with profit targets, stops and time exits. Circuit breakers stop new
risk when things go wrong.

It runs on a **paper account by default**. Live trading needs three explicit opt-ins (see
[Going live](#going-live)).

> **Not financial advice. Read this before running it.** Options can lose money quickly.
> The rules here are common practice, not a guarantee of profit. The backtester uses
> *modelled* option prices, so it shows how the bot behaves, not whether it will make money.
> Run it on paper for months and understand every rule before you put real money behind it.
> You are responsible for every order it sends.

---

## How it trades

Every cycle (default: 5 minutes) while the market is open:

1. **Protect the account first.** Update the equity high-water mark and check the circuit
   breakers. Settle orders left over from an earlier cycle or a crash, and reconcile the
   journal with the broker's positions.
2. **Manage open trades.** Close the ones that hit a profit target, a stop, a time limit, or a
   risk event (see [Exit rules](#exit-rules)).
3. **Look for new trades** if nothing blocks new risk:
   - **Market regime** (from SPY): risk-on, neutral, risk-off (no new bullish trades, half
     size) or crisis (SPY IV ≥ 40% or a ≥ 4% daily move: no new trades).
   - **Read each underlying**: trend score from price vs. its 20/50/200-day averages and the
     50-day slope, trend strength (ADX), stretch (RSI), realised volatility (20/60/252-day),
     ATM implied volatility, IV rank and beta to SPY.
   - **Pick a structure** from the playbook below.
   - **Build and price spreads**: ~45 DTE (monthlies preferred, 30–60 allowed), never across
     an earnings date, liquid legs only, short strikes by delta, minimum credit vs. width.
   - **Score by edge.** Each leg is valued at the bot's realised-volatility forecast, scaled
     by the chain's own skew, and compared with the expected fill. The comparison is net of
     the expected cost to get in *and* out. Premium is sold only when that edge is
     positive and at least 2% of the max loss.
   - **Size it** and **work the order** (see [Risk controls](#risk-controls) and
     [Execution](#execution)).

### The playbook

| | IV rank high (rich premium) | IV rank low (cheap premium) |
|---|---|---|
| **Bullish trend** | Bull put credit spread (short ~0.25Δ) | Bull call debit spread (strong trend only) |
| **Bearish trend** | Bear call credit spread (short ~0.25Δ) | Bear put debit spread (strong trend only) |
| **No trend** | Iron condor (shorts ~0.16Δ), only if IVR ≥ 50 and ADX < 25 | Stand aside |

Every structure is defined-risk: the bot never sells a naked option, and it rejects any
combination with unbounded loss. A trend stretched too far (RSI ≥ 75 in an uptrend, ≤ 25 in a
downtrend) counts as neutral until it cools off, so the bot doesn't chase.

### Exit rules

| Rule | Credit spreads / condors | Debit spreads |
|---|---|---|
| Profit target | 50% of max profit | 50% of max profit |
| Stop loss | loss reaches 2× the credit | loss reaches 50% of the debit |
| Time exit | 21 DTE (gamma risk grows) | 14 DTE |
| Early-assignment risk | short leg in the money with < $0.10 of time value left | same |
| Earnings | close the day before a report that falls before expiration | same |
| Expiration day | never held into the close | same |
| Short strike touched | optional (`close_on_short_strike_breach`, off by default*) | n/a |

\* The odds of the price *touching* a strike are roughly twice the odds of it *finishing*
past that strike, so closing on every touch roughly doubles the stop-out rate. The 2× credit
stop already caps the loss.

Stops and the risk-event exits are *urgent* and may pay up to the natural price. Profit and
time exits are patient. On accounts under $25k, non-urgent exits never close a position
opened the same day, to avoid pattern-day-trader flags.

### Risk controls

- **Per trade:** max loss (plus the slippage the bot may concede) ≤ **2%** of equity.
- **Portfolio:** sum of open max losses ≤ **25%** of equity. At most 8 open trades, 1 per
  underlying, 2 per correlated group (SPY/QQQ/IWM/DIA by default), 3 new trades a day, and
  10 contracts per trade. 20% of options buying power is always kept in reserve.
- **Net beta-weighted delta** stays within ±100% of equity in SPY-equivalent dollars. Trades
  that reduce exposure are always allowed.
- **Circuit breakers** (they block new entries; exits keep running):
  - a **15% drawdown** from the high-water mark halts entries until `optionsbot resume`;
  - a **3% daily loss** pauses entries until the next day;
  - **4 losing trades in a row** start a 2-day cool-down;
  - after a loss on a symbol, no re-entry there for 3 days (no revenge trades);
  - a kill-switch file (`data/STOP`) or `optionsbot halt` stops entries immediately.
- **Earnings:** single stocks are traded only when an earnings source knows their next report
  date. Without one, they're skipped. ETFs on the `no_earnings_symbols` list are fine.
- **Reconciliation:** positions the bot didn't open are left alone, and their underlying is
  not traded. If the broker's positions don't match the journal, the trade is flagged for
  manual review (`optionsbot resolve`). A short leg disappearing (possible assignment) also
  halts new entries and sends an alert.

### Execution

The bot never sends market orders. Each spread goes out as one multi-leg limit order at
the **mid price**. If it doesn't fill within 45 seconds, the bot cancels it, refreshes
quotes and re-prices one step closer to the natural price. Entries concede at most 40% of the
mid-to-natural gap and never accept less than the strategy's minimum credit (or pay more than
its maximum debit). Every order carries a unique client order ID and is written to the
journal *before* it is sent. Each fill is applied in the same database transaction that marks
the order as settled, so a crash can't lose or double-count a fill. An order whose outcome is
unknown (network failure) is looked up on the next cycle instead of being assumed rejected.

## Quick start (Alpaca paper trading)

You need Python 3.12 and a free Alpaca account. Paper accounts have options trading (level 3,
spreads) enabled by default.

```bash
cd options-bot
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env              # add your *paper* API keys
cp config.example.yaml config.yaml

.venv/bin/python -m optionsbot check          # keys, account level, data, earnings coverage
.venv/bin/python -m optionsbot scan           # what it would trade right now, and why
.venv/bin/python -m optionsbot run --dry-run  # full loop, logs decisions, sends no orders
.venv/bin/python -m optionsbot run            # trade the paper account
```

Get API keys at <https://app.alpaca.markets> → Paper Trading → API Keys.

### Commands

| Command | What it does |
|---|---|
| `run [--once] [--dry-run]` | The trading loop (or a single cycle). Stops cleanly on Ctrl-C/SIGTERM after the current cycle |
| `scan` | Analyses the watchlist and prints each candidate: strikes, POP, edge, max loss, sizing, and the reasons behind each decision. Sends no orders |
| `status` | Equity, day P&L, open trades with live P&L, capital at risk |
| `history [--events]` | Closed trades, win rate, P&L, and the event log |
| `halt [reason]` / `resume` | Block or allow new entries (exits keep running) |
| `resolve ID --exit-value V` / `--reopen` | Settle a trade flagged for manual review: record how you closed it, or hand it back to the bot |
| `close-all --yes` | Close every open bot trade now (urgent pricing) and halt |
| `check` | Pre-flight checks |
| `backtest` | Replay history through the engine (see below) |

Every trade is stored in SQLite (`data/optionsbot.db`) with its full rationale: market regime,
trend and IV readings, playbook reason, POP, edge, Greeks and sizing limits.

### Run it unattended

With Docker (state and the kill switch live in `./data`):

```bash
docker build -t optionsbot .
docker run -d --name optionsbot --restart unless-stopped \
  --env-file .env -v "$PWD/config.yaml:/app/config.yaml:ro" -v "$PWD/data:/app/data" optionsbot
docker logs -f optionsbot
docker exec optionsbot python -m optionsbot status
touch data/STOP          # stop new entries immediately; delete the file to allow them again
```

Set `NOTIFY_WEBHOOK_URL` (Slack or Discord) to get a message for each entry, exit, halt and
reconciliation problem.

## Configuration

Everything is in [`config.example.yaml`](config.example.yaml), with the default value and a
comment for each setting. Unknown keys are rejected, so a typo can't quietly switch off a
safety rule. Secrets (API keys, webhook URL) come only from the environment or `.env`.

To trade single stocks, add a free [Finnhub](https://finnhub.io) key (`FINNHUB_API_KEY`) or an
`earnings_file` CSV (`symbol,date`) so the bot knows their report dates.

## Backtesting

```bash
# bars from Alpaca (needs keys)
.venv/bin/python -m optionsbot backtest --symbols SPY,QQQ,IWM --start 2023-01-01 --cash 50000
# or your own daily bars: <SYMBOL>.csv with date,open,high,low,close[,volume]
.venv/bin/python -m optionsbot backtest --csv-dir ./bars --iv-hv-ratio 1.2 --trades
```

The backtest runs the **real engine**: the same analysis, playbook, sizing, order-walking,
exit rules and circuit breakers. It runs against a simulated broker, twice per trading day
(10:30 at the open price, 15:45 at the close). **Historical option quotes are not used.**
Option prices are modelled with Black-Scholes, and IV is assumed to be `--iv-hv-ratio` × a
blend of trailing realised volatility, with a fixed put skew. That means:

- the volatility risk premium is an *input*, so results move a lot with `--iv-hv-ratio`;
- the skew shape matters: with the simulator's skew, put credit spreads look worse than call
  spreads, a property of the model and not of real markets (an independent Monte Carlo of the
  same pricing shows the same effect);
- stops can only trigger at the two daily samples, and earnings are ignored unless you pass
  an earnings file;
- in testing on random-walk prices, results ranged from small losses to small gains
  depending on the seed and the assumed premium. **Don't expect the backtest to tell you the
  strategy is profitable.** Use it to check that the bot behaves the way you expect.

For a real performance estimate, paper-trade it, or replay historical option quotes (for
example, Alpaca's historical option bars or a vendor like ORATS) through a broker adapter.

## Going live

Live trading needs **all** of these:

1. `mode: live` in `config.yaml`;
2. `OPTIONSBOT_LIVE_CONFIRMATION="I understand this bot trades real money"` in the
   environment;
3. `data.options_feed: opra`. Alpaca's free *indicative* option feed is derived from, not
   identical to, real OPRA quotes, and isn't meant for live decisions. OPRA data needs the
   Algo Trader Plus plan.

Before switching, run on paper long enough to see every exit rule and breaker fire. Set
`max_risk_per_trade_pct` to a level you could lose many times in a row. Remember that
assignments, early exercise and broker outages need a human.

## Limitations

- Alpaca only; US regular-hours trading only.
- No rolling or adjustments: losing trades are closed, not rolled.
- Stops are checked every cycle (5 minutes by default), not tick by tick, and gaps can blow
  through them. The defined-risk structure is the real limit on loss.
- Assignment is detected, not handled: the bot halts entries and alerts you.
- IV rank needs about 60 trading days of the bot's own daily IV readings. Until then, it ranks
  IV against a year of realised volatility and labels the result `hv_proxy`.
- The edge model assumes the market's skew is fair and only its level can be rich or cheap.
- No tax, wash-sale or commission optimisation beyond limit pricing.

## Development

```bash
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/pytest            # ~80 tests, no network: Alpaca is mocked, the rest runs on the simulator
.venv/bin/ruff check optionsbot tests && .venv/bin/ruff format --check optionsbot tests
```

```
optionsbot/
  cli.py          commands
  engine.py       the trading loop: breakers, reconciliation, exits, scanning, entries
  analysis.py     trend, momentum, realised/implied volatility, IV rank, market regime
  strategies/     selector.py (playbook), builder.py (strike selection, pricing, edge)
  risk.py         sizing and circuit breakers
  management.py   exit rules
  execution.py    limit-order walking with crash-safe fill accounting
  journal.py      SQLite journal: trades, orders, IV history, state, events
  pricing.py      Black-Scholes, Greeks, implied vol, breakevens, probability of profit
  broker/         alpaca.py (Trading + Market Data APIs), simulated.py (backtests/tests)
  earnings.py     earnings sources: no-earnings list, CSV file, Finnhub
  backtest.py     runs the engine over history on the simulated broker
```
