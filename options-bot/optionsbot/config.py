"""Bot configuration.

* Trading rules (strategy, risk, management, execution, schedule) live in a YAML file
  (``config.yaml``, see ``config.example.yaml``). Every value has a conservative default,
  so an empty file is a valid configuration.
* Secrets (broker keys, webhook URL) are read only from the environment or a ``.env`` file
  and are wrapped in ``SecretStr`` so they never appear in logs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from optionsbot.models import StrategyKind

LIVE_CONFIRMATION_PHRASE = "I understand this bot trades real money"

DEFAULT_WATCHLIST = ["SPY", "QQQ", "IWM", "DIA", "GLD", "TLT", "XLF", "XLE"]
# Funds without earnings reports. Single stocks need an earnings source (see earnings.py).
DEFAULT_NO_EARNINGS = [
    "SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "IEF", "HYG", "EEM", "EFA", "FXI", "USO", "UNG",
    "XLF", "XLE", "XLK", "XLV", "XLI", "XLP", "XLU", "XLY", "XLB", "XLC", "XLRE", "SMH", "XBI", "KRE", "GDX", "ARKK",
]  # fmt: skip


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrategyConfig(_Section):
    enabled: list[StrategyKind] = Field(default_factory=lambda: list(StrategyKind))
    # Expiration selection
    target_dte: int = Field(default=45, ge=7, le=120)
    min_dte: int = Field(default=30, ge=7, le=120)
    max_dte: int = Field(default=60, ge=7, le=180)
    prefer_monthly: bool = True
    # Credit spreads: short strike by |delta|
    credit_short_delta: float = Field(default=0.25, gt=0, lt=0.5)
    credit_short_delta_min: float = Field(default=0.15, gt=0, lt=0.5)
    credit_short_delta_max: float = Field(default=0.35, gt=0, lt=0.5)
    min_credit_to_width: float = Field(default=0.20, gt=0, lt=1)
    min_pop: float = Field(default=0.55, ge=0, le=1)
    # Iron condors: each short strike by |delta|
    condor_short_delta: float = Field(default=0.16, gt=0, lt=0.5)
    condor_short_delta_min: float = Field(default=0.10, gt=0, lt=0.5)
    condor_short_delta_max: float = Field(default=0.22, gt=0, lt=0.5)
    min_condor_credit_to_width: float = Field(default=0.25, gt=0, lt=1)
    # Debit spreads: long leg in the money, short leg out of the money
    debit_long_delta_min: float = Field(default=0.55, gt=0, lt=1)
    debit_long_delta_max: float = Field(default=0.75, gt=0, lt=1)
    debit_short_delta_min: float = Field(default=0.25, gt=0, lt=1)
    debit_short_delta_max: float = Field(default=0.40, gt=0, lt=1)
    max_debit_to_width: float = Field(default=0.55, gt=0, lt=1)
    # Spread width limits
    min_width: float = Field(default=1.0, gt=0)
    max_width_pct_of_spot: float = Field(default=0.04, gt=0, le=0.25)
    # Implied-volatility regimes (IV rank 0-100)
    min_iv_rank_credit: float = Field(default=30, ge=0, le=100)
    min_iv_rank_condor: float = Field(default=50, ge=0, le=100)
    max_iv_rank_debit: float = Field(default=30, ge=0, le=100)
    max_adx_condor: float = Field(default=25, ge=0, le=100)
    # Only sell premium when it is rich versus the realised-volatility forecast, by enough to
    # pay for the round trip: edge (net of expected entry and exit costs) must exceed this share of max loss
    require_positive_edge: bool = True
    min_edge_pct_of_risk: float = Field(default=0.02, ge=0, le=0.5)
    min_underlying_price: float = Field(default=20.0, ge=0)

    @model_validator(mode="after")
    def _ranges(self) -> StrategyConfig:
        if not self.min_dte <= self.target_dte <= self.max_dte:
            raise ValueError("strategy: need min_dte <= target_dte <= max_dte")
        if not self.credit_short_delta_min <= self.credit_short_delta <= self.credit_short_delta_max:
            raise ValueError("strategy: credit_short_delta must lie within [credit_short_delta_min, credit_short_delta_max]")
        if not self.condor_short_delta_min <= self.condor_short_delta <= self.condor_short_delta_max:
            raise ValueError("strategy: condor_short_delta must lie within [condor_short_delta_min, condor_short_delta_max]")
        return self


class LiquidityConfig(_Section):
    min_open_interest: int = Field(default=100, ge=0)
    max_leg_spread_abs: float = Field(default=0.05, ge=0)
    max_leg_spread_pct: float = Field(default=0.15, ge=0)
    max_combo_spread_pct: float = Field(default=0.25, ge=0)


class RiskConfig(_Section):
    max_risk_per_trade_pct: float = Field(default=0.02, gt=0, le=0.2)
    max_portfolio_risk_pct: float = Field(default=0.25, gt=0, le=1)
    max_open_trades: int = Field(default=8, ge=1)
    max_trades_per_underlying: int = Field(default=1, ge=1)
    max_new_trades_per_day: int = Field(default=3, ge=0)
    max_contracts_per_trade: int = Field(default=10, ge=1)
    # Keep a slice of options buying power unused for adjustments and margin changes
    buying_power_reserve_pct: float = Field(default=0.20, ge=0, lt=1)
    # Net beta-weighted (to SPY) delta exposure, as a fraction of equity in SPY-dollars
    max_net_beta_delta_pct: float = Field(default=1.0, gt=0)
    correlation_groups: dict[str, list[str]] = Field(default_factory=lambda: {"us_equity_index": ["SPY", "QQQ", "IWM", "DIA"]})
    max_trades_per_group: int = Field(default=2, ge=1)
    # Circuit breakers
    daily_loss_limit_pct: float = Field(default=0.03, gt=0, le=1)
    max_drawdown_pct: float = Field(default=0.15, gt=0, le=1)
    max_consecutive_losses: int = Field(default=4, ge=1)
    loss_streak_cooldown_days: int = Field(default=2, ge=0)
    reentry_cooldown_days_after_loss: int = Field(default=3, ge=0)
    # Market regime (measured on SPY)
    risk_off_size_multiplier: float = Field(default=0.5, ge=0, le=1)
    crisis_iv: float = Field(default=0.40, gt=0)
    crisis_daily_move_pct: float = Field(default=0.04, gt=0)


class ManagementConfig(_Section):
    credit_profit_target_pct: float = Field(default=0.50, gt=0, le=1)
    credit_stop_loss_multiple: float = Field(default=2.0, gt=0)
    debit_profit_target_pct: float = Field(default=0.50, gt=0, le=1)
    debit_stop_loss_pct: float = Field(default=0.50, gt=0, le=1)
    credit_time_exit_dte: int = Field(default=21, ge=0)
    debit_time_exit_dte: int = Field(default=14, ge=0)
    # Closing whenever price touches a short strike roughly doubles the stop-out rate (touch odds
    # are ~2x expiry-ITM odds); the 2x-credit stop already caps the loss. Opt in if you prefer it.
    close_on_short_strike_breach: bool = False
    min_extrinsic_itm_short: float = Field(default=0.10, ge=0)
    close_before_earnings_days: int = Field(default=1, ge=0)
    # Below this equity, profit-target and time exits never close a position opened the same day
    pdt_equity_threshold: float = Field(default=25_000, ge=0)


class ExecutionConfig(_Section):
    step_timeout_seconds: float = Field(default=45, gt=0)
    poll_seconds: float = Field(default=3, gt=0)
    cancel_timeout_seconds: float = Field(default=20, gt=0)
    entry_steps: int = Field(default=4, ge=1)
    exit_steps: int = Field(default=5, ge=1)
    # How far from mid towards the natural price the bot will concede (0 = mid only, 1 = natural)
    entry_max_concession: float = Field(default=0.4, ge=0, le=1)
    exit_max_concession: float = Field(default=0.6, ge=0, le=1)
    urgent_exit_max_concession: float = Field(default=1.0, ge=0, le=1)
    tick: float = Field(default=0.01, gt=0)


class ScheduleConfig(_Section):
    cycle_seconds: int = Field(default=300, ge=30)
    entry_start_minutes_after_open: int = Field(default=30, ge=0)
    entry_stop_minutes_before_close: int = Field(default=30, ge=0)
    exit_start_minutes_after_open: int = Field(default=5, ge=0)
    exit_stop_minutes_before_close: int = Field(default=3, ge=0)


class DataConfig(_Section):
    stock_quote_feed: Literal["iex", "sip"] = "iex"
    # Free Alpaca plans may query SIP history older than 15 minutes, which is all daily analysis needs
    stock_bars_feed: Literal["iex", "sip"] = "sip"
    options_feed: Literal["indicative", "opra"] = "indicative"
    history_days: int = Field(default=420, ge=120)
    iv_rank_min_history: int = Field(default=60, ge=10)
    risk_free_rate: float = Field(default=0.04, ge=0, le=0.2)


class BotConfig(_Section):
    mode: Literal["paper", "live"] = "paper"
    dry_run: bool = False
    watchlist: list[str] = Field(default_factory=lambda: list(DEFAULT_WATCHLIST))
    market_symbol: str = "SPY"
    no_earnings_symbols: list[str] = Field(default_factory=lambda: list(DEFAULT_NO_EARNINGS))
    allow_unknown_earnings: bool = False
    earnings_file: Path | None = None
    db_path: Path = Path("data/optionsbot.db")
    kill_switch_file: Path = Path("data/STOP")
    log_level: str = "INFO"

    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    liquidity: LiquidityConfig = Field(default_factory=LiquidityConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    management: ManagementConfig = Field(default_factory=ManagementConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    data: DataConfig = Field(default_factory=DataConfig)

    @field_validator("watchlist", "no_earnings_symbols")
    @classmethod
    def _upper(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for symbol in value:
            s = symbol.strip().upper()
            if s and s not in out:
                out.append(s)
        return out

    @field_validator("market_symbol")
    @classmethod
    def _upper_one(cls, value: str) -> str:
        return value.strip().upper()

    def group_of(self, symbol: str) -> str | None:
        for name, members in self.risk.correlation_groups.items():
            if symbol.upper() in {m.upper() for m in members}:
                return name
        return None


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False)

    alpaca_api_key_id: SecretStr | None = None
    alpaca_api_secret_key: SecretStr | None = None
    alpaca_paper_url: str = "https://paper-api.alpaca.markets"
    alpaca_live_url: str = "https://api.alpaca.markets"
    alpaca_data_url: str = "https://data.alpaca.markets"
    finnhub_api_key: SecretStr | None = None
    notify_webhook_url: SecretStr | None = None
    optionsbot_live_confirmation: str = ""

    @field_validator("alpaca_api_key_id", "alpaca_api_secret_key", "finnhub_api_key", "notify_webhook_url", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def alpaca_configured(self) -> bool:
        return self.alpaca_api_key_id is not None and self.alpaca_api_secret_key is not None


class ConfigError(RuntimeError):
    pass


def load_config(path: Path | None) -> BotConfig:
    if path is None:
        return BotConfig()
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the top level must be a mapping")
    return BotConfig.model_validate(data)


def check_live_guard(config: BotConfig, secrets: Secrets) -> None:
    """Live trading needs mode: live in the YAML, the confirmation phrase in the environment,
    and real-time OPRA option quotes (the free "indicative" feed is for testing only)."""
    if config.mode != "live":
        return
    if secrets.optionsbot_live_confirmation.strip() != LIVE_CONFIRMATION_PHRASE:
        raise ConfigError(
            "mode is 'live' but OPTIONSBOT_LIVE_CONFIRMATION is not set to the exact phrase "
            f"{LIVE_CONFIRMATION_PHRASE!r}. Refusing to trade real money."
        )
    if config.data.options_feed != "opra" and not config.dry_run:
        raise ConfigError("live trading needs data.options_feed: opra (Alpaca's indicative feed is derived and not meant for live decisions)")
