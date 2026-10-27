from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from optionsbot.cli import main
from optionsbot.config import LIVE_CONFIRMATION_PHRASE, BotConfig, ConfigError, Secrets, check_live_guard, load_config
from optionsbot.earnings import EarningsCalendar, FileEarningsCalendar, FinnhubEarningsCalendar, NoEarningsSymbols
from optionsbot.journal import Journal
from optionsbot.notify import Notifier
from tests.helpers import synthetic_bars


def write_config(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(f"db_path: {tmp_path / 'bot.db'}\nkill_switch_file: {tmp_path / 'STOP'}\nwatchlist: [spy, qqq, SPY]\n{extra}")
    return path


def test_load_config_defaults_and_validation(tmp_path):
    cfg = load_config(write_config(tmp_path, "risk:\n  max_risk_per_trade_pct: 0.01\n"))
    assert cfg.watchlist == ["SPY", "QQQ"]
    assert cfg.risk.max_risk_per_trade_pct == 0.01 and cfg.strategy.target_dte == 45
    assert load_config(None) == BotConfig()
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, "risk:\n  max_risk_per_trade: 0.01\n"))  # typo -> rejected, not ignored
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml")


def test_example_config_is_valid():
    example = Path(__file__).parent.parent / "config.example.yaml"
    assert load_config(example).mode == "paper"


def test_live_mode_needs_confirmation_and_real_time_quotes():
    live = BotConfig(mode="live", data={"options_feed": "opra"})
    confirmed = Secrets(_env_file=None, optionsbot_live_confirmation=LIVE_CONFIRMATION_PHRASE)
    with pytest.raises(ConfigError, match="CONFIRMATION"):
        check_live_guard(live, Secrets(_env_file=None))
    check_live_guard(live, confirmed)
    with pytest.raises(ConfigError, match="opra"):
        check_live_guard(BotConfig(mode="live"), confirmed)
    check_live_guard(BotConfig(), Secrets(_env_file=None))


def test_earnings_sources(tmp_path):
    csv = tmp_path / "earnings.csv"
    csv.write_text("symbol,date\nAAPL,2026-10-30\nMSFT,\n")
    cal = EarningsCalendar([NoEarningsSymbols(["SPY"]), FileEarningsCalendar(csv)])
    today = date(2026, 10, 1)
    assert cal.lookup("SPY", today).known and cal.lookup("SPY", today).next_date is None
    assert cal.lookup("AAPL", today).next_date == date(2026, 10, 30)
    assert cal.lookup("MSFT", today).known and cal.lookup("MSFT", today).next_date is None
    assert not cal.lookup("TSLA", today).known


@respx.mock
def test_finnhub_source():
    respx.get("https://finnhub.io/api/v1/calendar/earnings").mock(
        return_value=httpx.Response(200, json={"earningsCalendar": [{"date": "2026-11-05", "symbol": "NVDA", "hour": "amc"}]})
    )
    info = FinnhubEarningsCalendar("token").lookup("NVDA", date(2026, 10, 1), date(2026, 12, 1))
    assert info.known and info.next_date == date(2026, 11, 5)


@respx.mock
def test_notifier_posts_and_never_raises():
    route = respx.post("https://hooks.example.com/x").mock(return_value=httpx.Response(500))
    n = Notifier("https://hooks.example.com/x")
    n.send("hello")
    assert route.called and n.sent == ["[optionsbot] hello"]


def test_cli_halt_resume_history(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert main(["--config", str(cfg), "halt", "vacation"]) == 0
    assert Journal(tmp_path / "bot.db").get_state("halt")["reason"] == "vacation"
    assert main(["--config", str(cfg), "resume"]) == 0
    assert main(["--config", str(cfg), "history", "--events"]) == 0
    assert "resume" in capsys.readouterr().out


def test_cli_requires_keys(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    assert main(["--config", str(write_config(tmp_path)), "status"]) == 2
    assert "ALPACA_API_KEY_ID" in capsys.readouterr().err


def test_cli_backtest_from_csv(tmp_path, capsys):
    for symbol, seed in (("SPY", 1), ("QQQ", 2)):
        bars = synthetic_bars(400, 300, 0.1, 0.18, seed=seed)
        bars.reset_index().to_csv(tmp_path / f"{symbol}.csv", index=False)
    start = (date(2023, 1, 2) + timedelta(days=380)).isoformat()
    code = main(
        ["--config", str(write_config(tmp_path)), "backtest", "--csv-dir", str(tmp_path), "--start", start, "--json", str(tmp_path / "s.json")]
    )
    assert code == 0
    assert "Backtest" in capsys.readouterr().out
    assert (tmp_path / "s.json").exists()


def test_cli_resolve(tmp_path, capsys):
    from datetime import UTC, datetime

    from optionsbot.journal import TradeStatus
    from tests.test_risk_management import put_spread

    cfg = write_config(tmp_path)
    journal = Journal(tmp_path / "bot.db")
    tid = journal.create_trade(put_spread(), 1, date(2026, 3, 2), 1.0, {})
    journal.apply_entry_fill(tid, 1, -1.5, datetime(2026, 3, 2, 15, tzinfo=UTC))
    journal.set_status(tid, TradeStatus.NEEDS_ATTENTION, "test")
    assert main(["--config", str(cfg), "resolve", str(tid), "--reopen"]) == 0
    assert Journal(tmp_path / "bot.db").get_trade(tid).status is TradeStatus.OPEN
    assert main(["--config", str(cfg), "resolve", str(tid), "--exit-value", "-0.5"]) == 0
    trade = Journal(tmp_path / "bot.db").get_trade(tid)
    assert trade.status is TradeStatus.CLOSED and trade.realized_pnl == 100.0
    assert main(["--config", str(cfg), "resolve", "999"]) == 1
