"""Command-line interface: ``python -m optionsbot <command>``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from optionsbot import __version__
from optionsbot.broker.alpaca import AlpacaBroker
from optionsbot.broker.base import BrokerError
from optionsbot.broker.simulated import SimSettings
from optionsbot.config import BotConfig, ConfigError, Secrets, check_live_guard, load_config
from optionsbot.earnings import EarningsCalendar, FileEarningsCalendar, FinnhubEarningsCalendar, NoEarningsSymbols
from optionsbot.engine import CycleReport, SymbolScan, TradingEngine
from optionsbot.journal import Journal, TradeStatus
from optionsbot.models import combo_value
from optionsbot.notify import Notifier
from optionsbot.risk import RiskManager
from optionsbot.timeutil import et_date

DISCLAIMER = (
    "optionsbot is software, not financial advice. Options can lose money quickly; past or simulated results do not "
    "predict future results. Run it on a paper account first and understand every rule before risking real money."
)


def setup_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(asctime)s %(levelname)-8s %(name)s: %(message)s", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def make_broker(config: BotConfig, secrets: Secrets) -> AlpacaBroker:
    if not secrets.alpaca_configured:
        raise ConfigError("ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY must be set (see .env.example)")
    check_live_guard(config, secrets)
    return AlpacaBroker(
        key_id=secrets.alpaca_api_key_id.get_secret_value(),
        secret_key=secrets.alpaca_api_secret_key.get_secret_value(),
        trading_url=secrets.alpaca_live_url if config.mode == "live" else secrets.alpaca_paper_url,
        data_url=secrets.alpaca_data_url,
        stock_quote_feed=config.data.stock_quote_feed,
        stock_bars_feed=config.data.stock_bars_feed,
        options_feed=config.data.options_feed,
    )


def make_earnings(config: BotConfig, secrets: Secrets) -> EarningsCalendar:
    sources: list = [NoEarningsSymbols(config.no_earnings_symbols)]
    if config.earnings_file is not None:
        sources.append(FileEarningsCalendar(config.earnings_file))
    if secrets.finnhub_api_key is not None:
        sources.append(FinnhubEarningsCalendar(secrets.finnhub_api_key.get_secret_value()))
    return EarningsCalendar(sources)


def make_engine(config: BotConfig, secrets: Secrets) -> TradingEngine:
    broker = make_broker(config, secrets)
    journal = Journal(config.db_path)
    notifier = Notifier(secrets.notify_webhook_url.get_secret_value() if secrets.notify_webhook_url else None, prefix=f"optionsbot/{config.mode}")
    return TradingEngine(config, broker, journal, make_earnings(config, secrets), notifier)


# ---------------------------------------------------------------------- output
def print_scan(scans: list[SymbolScan]) -> None:
    for scan in scans:
        head = scan.view.summary() if scan.view else scan.symbol
        print(head)
        if scan.chosen:
            c = scan.chosen
            print(
                f"    -> {scan.contracts}x {c.describe()}\n"
                f"       POP {c.pop:.0%} (forecast {c.pop_forecast:.0%}), edge ${c.expected_value:+.0f}, "
                f"max profit ${c.max_profit:,.0f}, max loss ${c.max_loss:,.0f} per contract, breakevens {c.breakevens}"
            )
            for line in scan.sizing:
                print(f"       sizing: {line}")
        for note in scan.notes:
            print(f"    - {note}")


def print_report(report: CycleReport) -> None:
    print(f"cycle at {report.at.isoformat()} market_open={report.market_open}")
    if report.regime:
        print(f"market regime: {report.regime.regime.value} ({'; '.join(report.regime.reasons)})")
    if report.block_reason:
        print(f"new entries blocked: {report.block_reason}")
    for line in report.exits + report.entries + report.messages:
        print(f"  {line}")
    if report.scans:
        print_scan(report.scans)


# ---------------------------------------------------------------------- commands
def cmd_run(args: argparse.Namespace, config: BotConfig, secrets: Secrets) -> int:
    if args.dry_run:
        config = config.model_copy(update={"dry_run": True})
    engine = make_engine(config, secrets)
    account = engine.broker.get_account()
    logging.getLogger(__name__).info(
        "connected to Alpaca %s: equity %.2f, options level %d. %s", config.mode, account.equity, account.options_level, DISCLAIMER
    )
    if args.once:
        print_report(engine.run_cycle())
        return 0
    engine.run_forever()
    return 0


def cmd_scan(args: argparse.Namespace, config: BotConfig, secrets: Secrets) -> int:
    engine = make_engine(config, secrets)
    now = engine.broker.get_clock().timestamp
    today = et_date(now)
    account = engine.broker.get_account()
    regime = engine.regime(now, today)
    print(f"market regime: {regime.regime.value} ({'; '.join(regime.reasons)})")
    block = engine.risk.entry_block_reason(account, today, engine.journal.active_trades())
    if block:
        print(f"note: new entries are currently blocked: {block}")
    print_scan(engine.scan(now, today, account, regime))
    return 0


def cmd_status(args: argparse.Namespace, config: BotConfig, secrets: Secrets) -> int:
    engine = make_engine(config, secrets)
    account = engine.broker.get_account()
    trades = engine.journal.active_trades()
    print(
        f"mode {config.mode}  equity {account.equity:,.2f}  day P&L {account.equity - account.last_equity:+,.2f}  options BP {account.options_buying_power:,.2f}"
    )
    if reason := engine.risk.halted():
        print(f"HALTED: {reason}")
    quotes = engine.broker.get_option_quotes([leg.symbol for t in trades for leg in t.legs]) if trades else {}
    total_risk = 0.0
    for t in trades:
        value = combo_value(t.legs, quotes, "mid")
        pnl = f"{t.unrealized_pnl(value):+,.2f}" if value is not None else "n/a"
        total_risk += t.open_risk
        print(f"  #{t.id} {t.status.value:<15} {t.qty}x {t.rationale.get('summary', t.underlying)}  P&L {pnl}  max loss {t.open_risk:,.0f}")
    print(f"open trades {len(trades)}, capital at risk {total_risk:,.0f} ({total_risk / account.equity:.1%} of equity)" if account.equity else "")
    return 0


def cmd_history(args: argparse.Namespace, config: BotConfig, secrets: Secrets) -> int:
    journal = Journal(config.db_path)
    closed = journal.closed_trades(limit=args.limit)
    total = sum(t.realized_pnl for t in closed)
    for t in closed:
        when = t.exit_time.date().isoformat() if t.exit_time else "?"
        print(f"#{t.id} {when} {t.underlying:<5} {t.strategy.value:<24} x{t.closed_qty:<3} {t.exit_reason or '':<22} {t.realized_pnl:+10,.2f}")
    wins = sum(1 for t in closed if t.realized_pnl > 0)
    if closed:
        print(f"{len(closed)} trades, {wins / len(closed):.0%} winners, total P&L {total:+,.2f}")
    if args.events:
        for event in journal.recent_events(args.limit):
            print(f"{event['at']} {event['level']:<8} {event['kind']}: {event['message']}")
    return 0


def cmd_halt(args: argparse.Namespace, config: BotConfig, secrets: Secrets) -> int:
    RiskManager(config, Journal(config.db_path)).halt(args.reason or "manual halt")
    print("new entries halted; open trades are still managed. Undo with `optionsbot resume`.")
    return 0


def cmd_resume(args: argparse.Namespace, config: BotConfig, secrets: Secrets) -> int:
    RiskManager(config, Journal(config.db_path)).resume()
    print("trading resumed")
    return 0


def cmd_resolve(args: argparse.Namespace, config: BotConfig, secrets: Secrets) -> int:
    journal = Journal(config.db_path)
    try:
        trade = journal.get_trade(args.trade_id)
    except KeyError:
        print(f"no trade {args.trade_id}", file=sys.stderr)
        return 1
    if args.reopen:
        journal.set_status(trade.id, TradeStatus.OPEN, "reopened manually")
        journal.log_event("INFO", "resolve", f"trade {trade.id} set back to open")
        print(f"trade {trade.id} is open again and will be managed by the bot")
        return 0
    if args.exit_value is None:
        print("pass --exit-value (position value per share when you closed it) or --reopen", file=sys.stderr)
        return 2
    updated = journal.apply_exit_fill(trade.id, trade.qty, args.exit_value, datetime.now(UTC), "manual")
    journal.log_event("INFO", "resolve", f"trade {trade.id} closed manually at {args.exit_value:+.2f}", {"pnl": updated.realized_pnl})
    print(f"trade {trade.id} recorded as closed; realised P&L {updated.realized_pnl:+,.2f}")
    return 0


def cmd_close_all(args: argparse.Namespace, config: BotConfig, secrets: Secrets) -> int:
    if not args.yes:
        print("this closes every open bot trade with urgent limit orders; re-run with --yes to confirm")
        return 2
    engine = make_engine(config, secrets)
    if not engine.broker.get_clock().is_open:
        print("market is closed; nothing can be closed now")
        return 1
    engine.risk.halt("close-all requested")
    for line in engine.close_all():
        print(line)
    return 0


def cmd_check(args: argparse.Namespace, config: BotConfig, secrets: Secrets) -> int:
    print(f"optionsbot {__version__}, mode {config.mode}{' (dry run)' if config.dry_run else ''}, watchlist {', '.join(config.watchlist)}")
    broker = make_broker(config, secrets)
    account = broker.get_account()
    clock = broker.get_clock()
    print(
        f"account: equity {account.equity:,.2f}, options BP {account.options_buying_power:,.2f}, options level {account.options_level}, status {account.status}"
    )
    print(f"market open: {clock.is_open} (next open {clock.next_open.isoformat()}, next close {clock.next_close.isoformat()})")
    problems = []
    if account.options_level < 3:
        problems.append("options level 3 (spreads) is required; enable it in the Alpaca dashboard")
    if account.trading_blocked:
        problems.append("trading is blocked on this account")
    earnings = make_earnings(config, secrets)
    today = et_date(clock.timestamp)
    for symbol in config.watchlist:
        info = earnings.lookup(symbol, today)
        if not info.known and not config.allow_unknown_earnings:
            problems.append(f"{symbol}: no earnings source knows its next report; it will be skipped")
    try:
        spot = broker.get_spot(config.market_symbol)
        print(f"{config.market_symbol} last price {spot:.2f}")
    except BrokerError as exc:
        problems.append(f"market data: {exc}")
    for p in problems:
        print(f"PROBLEM: {p}")
    return 1 if problems else 0


def cmd_backtest(args: argparse.Namespace, config: BotConfig, secrets: Secrets) -> int:
    from optionsbot.backtest import load_csv_bars, run_backtest

    symbols = [s.strip().upper() for s in (args.symbols or ",".join(config.watchlist)).split(",") if s.strip()]
    if config.market_symbol not in symbols:
        symbols.append(config.market_symbol)
    if args.csv_dir:
        bars = load_csv_bars(Path(args.csv_dir), symbols)
    else:
        broker = make_broker(config.model_copy(update={"mode": "paper"}), secrets)
        start = date.fromisoformat(args.start) if args.start else date(date.today().year - 3, 1, 1)
        history_start = date(start.year - 2, start.month, 1)
        end = date.fromisoformat(args.end) if args.end else date.today()
        bars = {s: broker.get_daily_bars(s, history_start, end) for s in symbols}
    config = config.model_copy(update={"watchlist": symbols})
    sim = SimSettings(starting_cash=args.cash, iv_hv_ratio=args.iv_hv_ratio, fee_per_contract=args.fee)
    result = run_backtest(
        bars,
        config,
        start=date.fromisoformat(args.start) if args.start else None,
        end=date.fromisoformat(args.end) if args.end else None,
        sim=sim,
    )
    print(result.report())
    if args.json:
        Path(args.json).write_text(json.dumps(result.stats(), indent=2, default=str))
    if args.trades:
        for line in result.events:
            print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="optionsbot", description="Rules-based, risk-first options trading bot for Alpaca.", epilog=DISCLAIMER)
    parser.add_argument("--config", type=Path, default=None, help="YAML config (default: ./config.yaml if it exists)")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="run the trading loop")
    p.add_argument("--once", action="store_true", help="run a single cycle and exit")
    p.add_argument("--dry-run", action="store_true", help="decide and log, but never send orders")
    p.set_defaults(func=cmd_run)

    sub.add_parser("scan", help="analyse the watchlist and show what the bot would trade (no orders)").set_defaults(func=cmd_scan)
    sub.add_parser("status", help="account, open trades and risk in use").set_defaults(func=cmd_status)
    sub.add_parser("check", help="verify keys, account permissions, data and earnings coverage").set_defaults(func=cmd_check)

    p = sub.add_parser("history", help="closed trades and P&L")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--events", action="store_true", help="also show the event log")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("halt", help="stop opening new trades (exits keep running)")
    p.add_argument("reason", nargs="?", default="")
    p.set_defaults(func=cmd_halt)
    sub.add_parser("resume", help="clear a halt").set_defaults(func=cmd_resume)

    p = sub.add_parser("resolve", help="settle a trade flagged for manual review")
    p.add_argument("trade_id", type=int)
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--exit-value",
        type=float,
        help="position value per share when you closed it: negative for a credit spread bought back (e.g. -0.45), positive for a debit spread sold",
    )
    group.add_argument("--reopen", action="store_true", help="the positions are intact: hand the trade back to the bot")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("close-all", help="close every open bot trade now and halt")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_close_all)

    p = sub.add_parser("backtest", help="replay daily bars through the engine with simulated option prices")
    p.add_argument("--symbols", help="comma-separated (default: the watchlist)")
    p.add_argument("--start", help="YYYY-MM-DD (default: after a one-year warm-up)")
    p.add_argument("--end", help="YYYY-MM-DD")
    p.add_argument("--csv-dir", help="directory of <SYMBOL>.csv daily bars; otherwise bars come from Alpaca")
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--iv-hv-ratio", type=float, default=1.10, help="assumed implied/realised volatility ratio")
    p.add_argument("--fee", type=float, default=0.05, help="fee per contract")
    p.add_argument("--json", help="write statistics to this file")
    p.add_argument("--trades", action="store_true", help="print every entry and exit")
    p.set_defaults(func=cmd_backtest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = args.config or (Path("config.yaml") if Path("config.yaml").exists() else None)
    try:
        config = load_config(path)
        setup_logging(config.log_level)
        return args.func(args, config, Secrets())
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except BrokerError as exc:
        print(f"broker error: {exc}", file=sys.stderr)
        return 1
