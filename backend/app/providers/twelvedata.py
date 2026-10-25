"""Twelve Data REST integration for stocks, ETFs, indices and FX (API key required).

Endpoints used (https://twelvedata.com/docs):

* ``GET /quote``          - latest price, session OHLC, previous close, 52-week range (1 credit/symbol)
* ``GET /time_series``    - OHLCV, intervals 1min..1month, up to 5000 bars (1 credit)
* ``GET /symbol_search``  - instrument lookup

The API key is sent in the ``Authorization: apikey ...`` header so it never appears in
URLs or access logs. Credits are rationed locally with a token bucket (per minute) and
a daily counter so the free Basic plan (8/min, 800/day) is never exceeded.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import Settings
from app.errors import (
    AppError,
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderNotConfiguredError,
    ProviderPlanError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    SymbolNotFoundError,
)
from app.models.market import AssetInfo, AssetType, Candle, Quote, Timeframe
from app.providers.base import CandleSeries, MarketDataProvider, ProviderStatus, TimeframeSupport
from app.providers.http import ProviderHTTP
from app.ratelimit import DailyQuota, TokenBucket
from app.symbols import ParsedSymbol

logger = logging.getLogger(__name__)

INTERVALS: dict[Timeframe, str] = {
    Timeframe.M1: "1min",
    Timeframe.M5: "5min",
    Timeframe.M15: "15min",
    Timeframe.M30: "30min",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1day",
    Timeframe.W1: "1week",
    Timeframe.MN1: "1month",
}

TYPE_MAP: dict[str, AssetType] = {
    "common stock": AssetType.STOCK,
    "preferred stock": AssetType.STOCK,
    "american depositary receipt": AssetType.STOCK,
    "depositary receipt": AssetType.STOCK,
    "global depositary receipt": AssetType.STOCK,
    "reit": AssetType.STOCK,
    "etf": AssetType.ETF,
    "exchange-traded note": AssetType.ETF,
    "index": AssetType.INDEX,
    "mutual fund": AssetType.FUND,
    "closed-end fund": AssetType.FUND,
    "digital currency": AssetType.CRYPTO,
    "physical currency": AssetType.FOREX,
}

NOT_CONFIGURED_MESSAGE = (
    "Stock, ETF and index data requires a Twelve Data API key. Get a free key at "
    "https://twelvedata.com/pricing and set TWELVEDATA_API_KEY in the server's .env file, then restart "
    "the backend. Crypto pairs (e.g. BTC-USD) work without a key via Coinbase."
)


def map_type(raw: str | None) -> AssetType | None:
    if not raw:
        return None
    return TYPE_MAP.get(raw.strip().lower(), AssetType.OTHER)


class TwelveDataProvider(MarketDataProvider):
    name = "twelvedata"
    display_name = "Twelve Data"
    asset_classes: ClassVar[list[AssetType]] = [AssetType.STOCK, AssetType.ETF, AssetType.INDEX, AssetType.FOREX, AssetType.FUND]

    def __init__(self, settings: Settings, http: ProviderHTTP | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.http = http or ProviderHTTP(self.name, settings.twelvedata_base_url, settings.http_timeout_seconds, settings.http_max_retries)
        per_minute = settings.twelvedata_credits_per_minute
        self.minute_bucket = TokenBucket(per_minute / 60.0, per_minute)
        self.daily = DailyQuota(settings.twelvedata_credits_per_day)
        # Learned metadata (asset type, timezone, name) keyed by canonical symbol.
        self._meta: dict[str, dict[str, Any]] = {}

    @property
    def configured(self) -> bool:
        return self.settings.twelvedata_configured

    # ------------------------------------------------------------------ transport
    async def _get(self, path: str, params: dict[str, Any], credits: int = 1) -> Any:
        if not self.configured:
            raise ProviderNotConfiguredError(NOT_CONFIGURED_MESSAGE, details={"provider": self.name, "env": "TWELVEDATA_API_KEY"})
        self.health.check(self.display_name)
        wait = await self.minute_bucket.acquire(credits, max_wait=1.5)
        if wait:
            raise ProviderRateLimitedError(
                f"Twelve Data per-minute credit limit ({self.settings.twelvedata_credits_per_minute}/min) reached. Data will refresh automatically.",
                provider=self.name,
                retry_after=wait,
            )
        if not self.daily.try_consume(credits):
            raise ProviderRateLimitedError(
                "Twelve Data daily credit allowance is used up; it resets at 00:00 UTC.",
                provider=self.name,
                retry_after=self.daily.seconds_until_reset(),
            )
        assert self.settings.twelvedata_api_key is not None
        headers = {"Authorization": f"apikey {self.settings.twelvedata_api_key.get_secret_value()}"}
        try:
            response = await self.http.get(path, params=params, headers=headers)
            data = self.http.parse_json(response)
            self._raise_for_error(data, response.status_code)
        except AppError as exc:
            self.health.record_failure(exc)
            raise
        self.health.record_success()
        return data

    def _raise_for_error(self, data: Any, http_status: int) -> None:
        if isinstance(data, dict) and str(data.get("status", "")).lower() == "error":
            code = int(data.get("code") or http_status or 500)
            message = str(data.get("message") or "Unknown Twelve Data error").replace("**", "")
        elif http_status >= 400:
            code, message = http_status, f"Twelve Data returned HTTP {http_status}."
        else:
            return
        lowered = message.lower()
        if code == 401:
            raise ProviderAuthError("Twelve Data rejected the server's API key. Check TWELVEDATA_API_KEY.", provider=self.name)
        if code == 429:
            raise ProviderRateLimitedError("Twelve Data rate limit reached. Data will refresh automatically.", provider=self.name, retry_after=60)
        if code == 403:
            raise ProviderPlanError(f"Not available on the configured Twelve Data plan: {message}", provider=self.name)
        if code == 404 or "not found" in lowered or "invalid symbol" in lowered:
            raise SymbolNotFoundError("Twelve Data does not recognise this symbol.", details={"provider": self.name, "upstream": message[:200]})
        if code >= 500:
            raise ProviderUnavailableError(f"Twelve Data error: {message}", provider=self.name, retry_after=30)
        raise ProviderBadResponseError(f"Twelve Data error: {message}", provider=self.name)

    # ------------------------------------------------------------------ helpers
    def _provider_params(self, asset: AssetInfo) -> dict[str, str]:
        ticker, _, exchange = asset.symbol.partition(":")
        if asset.asset_type in (AssetType.FOREX, AssetType.CRYPTO) and "-" in ticker:
            ticker = ticker.replace("-", "/")
        params = {"symbol": ticker}
        if exchange:
            params["exchange"] = exchange
        return params

    def _remember(self, symbol: str, **meta: Any) -> None:
        entry = self._meta.setdefault(symbol, {})
        entry.update({k: v for k, v in meta.items() if v})

    # ------------------------------------------------------------------ provider API
    async def resolve(self, parsed: ParsedSymbol, asset_type: AssetType) -> AssetInfo | None:
        known = self._meta.get(parsed.symbol, {})
        if asset_type is AssetType.AUTO:
            if known.get("asset_type"):
                resolved_type = known["asset_type"]
            elif parsed.is_pair:
                resolved_type = AssetType.FOREX
            else:
                resolved_type = AssetType.STOCK
        else:
            resolved_type = asset_type
        display = parsed.symbol.replace("-", "/") if parsed.is_pair else parsed.symbol
        return AssetInfo(
            symbol=parsed.symbol,
            display_symbol=display,
            name=known.get("name"),
            asset_type=resolved_type,
            exchange=known.get("exchange"),
            mic_code=known.get("mic_code"),
            currency=known.get("currency"),
            timezone=known.get("timezone") or ("UTC" if parsed.exchange else "America/New_York"),
            provider=self.name,
        )

    async def get_quote(self, asset: AssetInfo) -> Quote:
        data = await self._get("/quote", self._provider_params(asset))
        return self._parse_quote(asset, data)

    def _parse_quote(self, asset: AssetInfo, data: dict[str, Any]) -> Quote:
        try:
            price = float(data["close"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderBadResponseError("Twelve Data quote did not include a price.", provider=self.name) from exc
        ts = data.get("last_quote_at") or data.get("timestamp")
        as_of = datetime.fromtimestamp(int(ts), UTC) if ts else datetime.now(UTC)
        self._remember(
            asset.symbol, name=data.get("name"), exchange=data.get("exchange"), mic_code=data.get("mic_code"), currency=data.get("currency")
        )
        week = data.get("fifty_two_week") or {}
        prev_close = _f(data.get("previous_close"))
        change = _f(data.get("change"))
        pct = _f(data.get("percent_change"))
        if change is None and prev_close:
            change = price - prev_close
            pct = change / prev_close * 100
        is_open = data.get("is_market_open")
        meta = self._meta.get(asset.symbol, {})
        return Quote(
            symbol=asset.symbol,
            display_symbol=asset.display_symbol,
            name=data.get("name") or asset.name,
            asset_type=meta.get("asset_type") or asset.asset_type,
            exchange=data.get("exchange") or asset.exchange,
            currency=data.get("currency") or asset.currency,
            price=price,
            change=change,
            change_percent=pct,
            change_basis="previous_close",
            open=_f(data.get("open")),
            high=_f(data.get("high")),
            low=_f(data.get("low")),
            previous_close=prev_close,
            volume=_f(data.get("volume")),
            volume_label="Session volume" if data.get("volume") not in (None, "") else None,
            fifty_two_week_high=_f(week.get("high")),
            fifty_two_week_low=_f(week.get("low")),
            as_of=as_of,
            provider=self.name,
            timezone=meta.get("timezone") or asset.timezone,
            market_open_flag=bool(is_open) if isinstance(is_open, bool) else None,
        )

    async def get_candles(self, asset: AssetInfo, timeframe: Timeframe, limit: int) -> CandleSeries:
        params = self._provider_params(asset) | {
            "interval": INTERVALS[timeframe],
            "outputsize": str(min(max(limit, 1), 5000)),
            "timezone": "UTC" if timeframe.is_intraday else "Exchange",
            "order": "asc",
        }
        data = await self._get("/time_series", params)
        meta = data.get("meta") or {}
        values = data.get("values")
        if not isinstance(values, list):
            raise ProviderBadResponseError("Twelve Data returned no candle values.", provider=self.name)
        tz = str(meta.get("exchange_timezone") or asset.timezone or "UTC")
        detected = map_type(meta.get("type"))
        if asset.asset_type in (AssetType.FOREX, AssetType.CRYPTO) and detected in (None, AssetType.OTHER):
            detected = asset.asset_type
        self._remember(
            asset.symbol,
            timezone=tz,
            asset_type=detected,
            exchange=meta.get("exchange"),
            mic_code=meta.get("mic_code"),
            currency=meta.get("currency"),
        )
        resolved = asset.model_copy(
            update={
                "asset_type": detected or asset.asset_type,
                "timezone": tz,
                "exchange": meta.get("exchange") or asset.exchange,
                "currency": meta.get("currency") or asset.currency,
                "name": asset.name or self._meta.get(asset.symbol, {}).get("name"),
            }
        )
        candles: dict[int, Candle] = {}
        for row in values:
            try:
                t = _parse_dt(str(row["datetime"]), timeframe)
                candles[t] = Candle(
                    time=t,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=_f(row.get("volume")),
                )
            except (KeyError, TypeError, ValueError):
                continue
        ordered = [candles[t] for t in sorted(candles)][-limit:]
        now = time.time()
        return CandleSeries(
            asset=resolved,
            timeframe=timeframe,
            candles=ordered,
            timezone=tz,
            last_bar_complete=_last_complete(ordered, timeframe, tz, now),
            limitations=self._limitations(timeframe),
        )

    def _limitations(self, timeframe: Timeframe) -> list[str]:
        notes = ["Regular-session bars only; pre-/post-market trading is not included."]
        if timeframe.is_intraday:
            notes.append("Intraday history depth depends on the Twelve Data plan (max 5,000 bars per request).")
        if timeframe in (Timeframe.D1, Timeframe.W1, Timeframe.MN1):
            notes.append("Daily and longer bars are labelled with the session date (exchange time zone).")
        return notes

    async def search(self, query: str, asset_type: AssetType) -> list[AssetInfo]:
        q = query.strip()
        if not q:
            return []
        data = await self._get("/symbol_search", {"symbol": q, "outputsize": "30"})
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []
        results: list[tuple[int, AssetInfo]] = []
        for row in rows:
            symbol = str(row.get("symbol", "")).upper()
            if not symbol:
                continue
            kind = map_type(row.get("instrument_type")) or AssetType.OTHER
            if kind is AssetType.CRYPTO:
                continue  # crypto is served by Coinbase
            if asset_type not in (AssetType.AUTO, kind):
                continue
            country = row.get("country") or ""
            canonical = symbol.replace("/", "-")
            # Non-US listings need the exchange suffix to select the right instrument.
            if country and country != "United States" and kind is not AssetType.FOREX:
                canonical = f"{canonical}:{row.get('exchange')}"
            info = AssetInfo(
                symbol=canonical,
                display_symbol=symbol,
                name=row.get("instrument_name"),
                asset_type=kind,
                exchange=row.get("exchange"),
                mic_code=row.get("mic_code"),
                country=country or None,
                currency=row.get("currency"),
                timezone=row.get("exchange_timezone") or "UTC",
                provider=self.name,
            )
            self._remember(
                canonical,
                name=info.name,
                asset_type=kind,
                exchange=info.exchange,
                mic_code=info.mic_code,
                currency=info.currency,
                timezone=info.timezone,
            )
            rank = 0 if symbol == q.upper() else 1
            rank += 0 if country == "United States" else 2
            results.append((rank, info))
        results.sort(key=lambda r: r[0])
        seen: set[str] = set()
        unique: list[AssetInfo] = []
        for _, info in results:
            if info.symbol not in seen:
                seen.add(info.symbol)
                unique.append(info)
        return unique[:20]

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            display_name=self.display_name,
            asset_classes=["stock", "etf", "index", "forex"],
            configured=self.configured,
            status=self.health.status if self.configured else "not_configured",
            last_success_at=self.health.last_success_at,
            last_error=self.health.last_error,
            last_error_at=self.health.last_error_at,
            data_type="Depends on plan; freshness is measured from each data point's timestamp",
            limits=(
                f"{self.settings.twelvedata_credits_per_minute} credits/minute"
                + (f", {self.settings.twelvedata_credits_per_day} credits/day" if self.settings.twelvedata_credits_per_day else "")
            ),
            credits_remaining_today=self.daily.remaining() if self.configured else None,
            timeframes=[TimeframeSupport(timeframe=tf, native=True, source=f"Twelve Data {iv}") for tf, iv in INTERVALS.items()],
            notes=[
                "Free Basic plan: 8 API credits/minute and 800/day; each quote or candle request costs 1 credit.",
                "Indices and non-US exchanges may require a paid plan; unavailable symbols return a clear error.",
                "Regular-session data only; extended-hours prices are not shown.",
            ]
            if self.configured
            else [NOT_CONFIGURED_MESSAGE],
        )

    async def close(self) -> None:
        await self.http.close()


def _f(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _parse_dt(text: str, timeframe: Timeframe) -> int:
    if timeframe.is_intraday:
        dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        return int(dt.timestamp())
    d = date.fromisoformat(text[:10])
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())


def _last_complete(candles: list[Candle], timeframe: Timeframe, tz: str, now: float) -> bool:
    if not candles:
        return True
    last = candles[-1].time
    if timeframe.is_intraday:
        assert timeframe.seconds is not None
        return last + timeframe.seconds <= now
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    today = datetime.fromtimestamp(now, zone).date()
    bar_date = datetime.fromtimestamp(last, UTC).date()
    if timeframe is Timeframe.D1:
        return bar_date < today
    if timeframe is Timeframe.W1:
        return (today - bar_date).days >= 7
    return (today.year, today.month) != (bar_date.year, bar_date.month)
