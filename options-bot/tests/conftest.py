"""Shared fixtures. Tests never touch the network: Alpaca is mocked with respx and
everything else runs against the simulated broker."""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path

import pandas as pd
import pytest

from optionsbot.broker.simulated import SimSettings, SimulatedBroker
from optionsbot.config import BotConfig
from optionsbot.journal import Journal
from optionsbot.timeutil import ET
from tests.helpers import synthetic_bars


@pytest.fixture
def config(tmp_path: Path) -> BotConfig:
    return BotConfig(
        watchlist=["SPY", "QQQ"],
        db_path=tmp_path / "bot.db",
        kill_switch_file=tmp_path / "STOP",
    )


@pytest.fixture
def journal() -> Journal:
    j = Journal(":memory:")
    yield j
    j.close()


@pytest.fixture
def market_bars() -> dict[str, pd.DataFrame]:
    return {
        "SPY": synthetic_bars(450, 420, 0.25, 0.14, seed=5),
        "QQQ": synthetic_bars(380, 420, 0.30, 0.20, seed=6),
    }


@pytest.fixture
def sim(market_bars: dict[str, pd.DataFrame]) -> SimulatedBroker:
    broker = SimulatedBroker(market_bars, SimSettings(starting_cash=100_000, iv_hv_ratio=1.4))
    day = broker.trading_days()[300]
    broker.set_time(datetime.combine(day, time(10, 30), tzinfo=ET))
    return broker
