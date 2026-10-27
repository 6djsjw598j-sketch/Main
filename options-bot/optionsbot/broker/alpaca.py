"""Alpaca Trading API (paper or live) and Market Data API.

Endpoints used:
  trading  GET  /v2/account, /v2/clock, /v2/positions, /v2/orders, /v2/orders/{id},
                /v2/orders:by_client_order_id, /v2/options/contracts
           POST /v2/orders          (order_class "mleg" for spreads; limit_price > 0 debit, < 0 credit)
           DELETE /v2/orders/{id}
  data     GET  /v2/stocks/{symbol}/bars, /v2/stocks/{symbol}/snapshot,
                /v1beta1/options/snapshots/{underlying}, /v1beta1/options/snapshots
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pandas as pd

from optionsbot import __version__
from optionsbot.broker.base import Account, BrokerError, BrokerPosition, Clock, OrderRequest, OrderState, OrderStatus
from optionsbot.models import OptionChain, OptionQuote
from optionsbot.occ import parse_occ
from optionsbot.timeutil import to_et

logger = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


def parse_ts(value: str | None) -> datetime | None:
    """RFC 3339 with up to nanosecond precision (Python only parses microseconds)."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    if "." in text:
        head, rest = text.split(".", 1)
        frac = ""
        i = 0
        while i < len(rest) and rest[i].isdigit():
            frac += rest[i]
            i += 1
        text = f"{head}.{frac[:6].ljust(6, '0')}{rest[i:]}"
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _f(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class AlpacaBroker:
    name = "alpaca"

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        trading_url: str,
        data_url: str = "https://data.alpaca.markets",
        stock_quote_feed: str = "iex",
        stock_bars_feed: str = "sip",
        options_feed: str = "indicative",
        timeout: float = 15.0,
        max_retries: int = 3,
        requests_per_minute: int = 180,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
            "User-Agent": f"optionsbot/{__version__}",
            "Accept": "application/json",
        }
        self._trading = httpx.Client(base_url=trading_url, headers=headers, timeout=timeout, transport=transport)
        self._data = httpx.Client(base_url=data_url, headers=headers, timeout=timeout, transport=transport)
        self.stock_quote_feed = stock_quote_feed
        self.stock_bars_feed = stock_bars_feed
        self.options_feed = options_feed
        self.max_retries = max_retries
        self._min_interval = 60.0 / requests_per_minute
        self._last_request = 0.0
        self._lock = threading.Lock()
        self._sleep = time.sleep

    # ------------------------------------------------------------------ HTTP plumbing
    def _throttle(self) -> None:
        with self._lock:
            wait = self._last_request + self._min_interval - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last_request = time.monotonic()

    def _request(self, client: httpx.Client, method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> Any:
        attempt = 0
        while True:
            self._throttle()
            try:
                response = client.request(method, path, params=params, json=json)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if method != "GET" or attempt >= self.max_retries:
                    raise BrokerError(f"Alpaca {method} {path} failed: {type(exc).__name__}") from exc
            else:
                if response.status_code in RETRY_STATUSES and method == "GET" and attempt < self.max_retries:
                    retry_after = _f(response.headers.get("Retry-After"))
                    self._sleep(retry_after if retry_after else 0.5 * 2**attempt)
                    attempt += 1
                    continue
                if response.status_code == 204:
                    return None
                if response.status_code >= 400:
                    raise BrokerError(
                        f"Alpaca {method} {path} -> HTTP {response.status_code}: {self._error_text(response)}", status=response.status_code
                    )
                try:
                    return response.json()
                except ValueError as exc:
                    raise BrokerError(f"Alpaca {method} {path} returned non-JSON (HTTP {response.status_code})") from exc
            attempt += 1
            self._sleep(0.5 * 2 ** (attempt - 1))

    @staticmethod
    def _error_text(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:300]
        if isinstance(body, dict):
            return str(body.get("message") or body)[:300]
        return str(body)[:300]

    def close(self) -> None:
        self._trading.close()
        self._data.close()

    # ------------------------------------------------------------------ account
    def get_account(self) -> Account:
        a = self._request(self._trading, "GET", "/v2/account")
        equity = _f(a.get("equity"), 0.0) or 0.0
        return Account(
            equity=equity,
            last_equity=_f(a.get("last_equity"), equity) or equity,
            cash=_f(a.get("cash"), 0.0) or 0.0,
            buying_power=_f(a.get("buying_power"), 0.0) or 0.0,
            options_buying_power=_f(a.get("options_buying_power"), _f(a.get("buying_power"), 0.0)) or 0.0,
            options_level=int(a.get("options_trading_level") or a.get("options_approved_level") or 0),
            trading_blocked=bool(a.get("trading_blocked") or a.get("account_blocked")),
            status=str(a.get("status") or ""),
        )

    def get_clock(self) -> Clock:
        c = self._request(self._trading, "GET", "/v2/clock")
        return Clock(
            timestamp=parse_ts(c["timestamp"]),
            is_open=bool(c["is_open"]),
            next_open=parse_ts(c["next_open"]),
            next_close=parse_ts(c["next_close"]),
        )

    def get_positions(self) -> list[BrokerPosition]:
        rows = self._request(self._trading, "GET", "/v2/positions") or []
        out = []
        for p in rows:
            qty = int(float(p.get("qty") or 0))
            if p.get("side") == "short" and qty > 0:
                qty = -qty
            out.append(
                BrokerPosition(
                    symbol=p["symbol"],
                    qty=qty,
                    avg_entry_price=_f(p.get("avg_entry_price"), 0.0) or 0.0,
                    current_price=_f(p.get("current_price")),
                    market_value=_f(p.get("market_value")),
                    asset_class=p.get("asset_class") or "",
                )
            )
        return out

    # ------------------------------------------------------------------ market data
    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        params: dict[str, Any] = {
            "timeframe": "1Day",
            "start": start.isoformat(),
            "adjustment": "all",
            "feed": self.stock_bars_feed,
            "limit": 10000,
        }
        # SIP data on free plans must be at least 15 minutes old
        latest = datetime.now(UTC) - timedelta(minutes=20)
        end_moment = min(datetime.combine(end, datetime.max.time(), tzinfo=UTC), latest).replace(microsecond=0)
        params["end"] = end_moment.isoformat().replace("+00:00", "Z")
        rows: list[dict] = []
        while True:
            body = self._request(self._data, "GET", f"/v2/stocks/{symbol}/bars", params=params)
            rows.extend(body.get("bars") or [])
            token = body.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        frame = pd.DataFrame(
            {
                "date": [to_et(parse_ts(r["t"])).date() for r in rows],
                "open": [float(r["o"]) for r in rows],
                "high": [float(r["h"]) for r in rows],
                "low": [float(r["l"]) for r in rows],
                "close": [float(r["c"]) for r in rows],
                "volume": [float(r.get("v") or 0) for r in rows],
            }
        )
        return frame.drop_duplicates("date", keep="last").set_index("date").sort_index()

    def get_spot(self, symbol: str) -> float:
        snap = self._request(self._data, "GET", f"/v2/stocks/{symbol}/snapshot", params={"feed": self.stock_quote_feed})
        trade = (snap or {}).get("latestTrade") or {}
        quote = (snap or {}).get("latestQuote") or {}
        price = _f(trade.get("p"))
        bid, ask = _f(quote.get("bp")), _f(quote.get("ap"))
        if bid and ask and 0 < bid <= ask and (price is None or not bid * 0.98 <= price <= ask * 1.02):
            price = (bid + ask) / 2
        if not price or price <= 0:
            raise BrokerError(f"no usable price for {symbol}")
        return price

    def _parse_snapshot(self, symbol: str, snap: dict, underlying: str | None = None) -> OptionQuote | None:
        quote = snap.get("latestQuote") or {}
        bid, ask = _f(quote.get("bp")), _f(quote.get("ap"))
        if bid is None or ask is None:
            return None
        greeks = snap.get("greeks") or {}
        trade = snap.get("latestTrade") or {}
        return OptionQuote(
            contract=parse_occ(symbol, underlying),
            bid=bid,
            ask=ask,
            bid_size=int(quote.get("bs") or 0),
            ask_size=int(quote.get("as") or 0),
            last=_f(trade.get("p")),
            iv=_f(snap.get("impliedVolatility")),
            delta=_f(greeks.get("delta")),
            gamma=_f(greeks.get("gamma")),
            theta=_f(greeks.get("theta")),
            vega=_f(greeks.get("vega")),
            quote_time=parse_ts(quote.get("t")),
        )

    def _open_interest(self, underlying: str, expiration_from: date, expiration_to: date, strike_lo: float, strike_hi: float) -> dict[str, int]:
        params: dict[str, Any] = {
            "underlying_symbols": underlying,
            "status": "active",
            "expiration_date_gte": expiration_from.isoformat(),
            "expiration_date_lte": expiration_to.isoformat(),
            "strike_price_gte": f"{strike_lo:.2f}",
            "strike_price_lte": f"{strike_hi:.2f}",
            "limit": 10000,
        }
        out: dict[str, int] = {}
        while True:
            body = self._request(self._trading, "GET", "/v2/options/contracts", params=params)
            for c in body.get("option_contracts") or []:
                oi = _f(c.get("open_interest"))
                if oi is not None:
                    out[c["symbol"]] = int(oi)
            token = body.get("next_page_token") or body.get("page_token")
            if not token:
                return out
            params["page_token"] = token

    def get_option_chain(self, underlying: str, expiration_from: date, expiration_to: date, spot: float) -> OptionChain:
        strike_lo, strike_hi = spot * 0.7, spot * 1.3
        params: dict[str, Any] = {
            "feed": self.options_feed,
            "expiration_date_gte": expiration_from.isoformat(),
            "expiration_date_lte": expiration_to.isoformat(),
            "strike_price_gte": f"{strike_lo:.2f}",
            "strike_price_lte": f"{strike_hi:.2f}",
            "limit": 1000,
        }
        quotes: list[OptionQuote] = []
        while True:
            body = self._request(self._data, "GET", f"/v1beta1/options/snapshots/{underlying}", params=params)
            for symbol, snap in (body.get("snapshots") or {}).items():
                q = self._parse_snapshot(symbol, snap, underlying)
                if q is not None:
                    quotes.append(q)
            token = body.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        try:
            oi = self._open_interest(underlying, expiration_from, expiration_to, strike_lo, strike_hi)
        except BrokerError as exc:
            logger.warning("open interest unavailable for %s: %s", underlying, exc)
            oi = {}
        for q in quotes:
            q.open_interest = oi.get(q.symbol)
        return OptionChain(underlying=underlying, spot=spot, as_of=datetime.now(UTC), quotes=quotes)

    def get_option_quotes(self, symbols: list[str]) -> dict[str, OptionQuote]:
        out: dict[str, OptionQuote] = {}
        unique = sorted(set(symbols))
        for i in range(0, len(unique), 100):
            params: dict[str, Any] = {"symbols": ",".join(unique[i : i + 100]), "feed": self.options_feed, "limit": 1000}
            while True:
                body = self._request(self._data, "GET", "/v1beta1/options/snapshots", params=params)
                for symbol, snap in (body.get("snapshots") or {}).items():
                    q = self._parse_snapshot(symbol, snap)
                    if q is not None:
                        out[symbol] = q
                token = body.get("next_page_token")
                if not token:
                    break
                params["page_token"] = token
        return out

    # ------------------------------------------------------------------ orders
    @staticmethod
    def order_payload(request: OrderRequest) -> dict:
        if request.qty < 1:
            raise ValueError("order quantity must be at least 1")
        if len(request.legs) == 1:
            leg = request.legs[0]
            # single-leg limit prices are always positive; the side carries the direction
            return {
                "symbol": leg.symbol,
                "qty": str(request.qty * leg.ratio),
                "side": leg.side.value,
                "type": "limit",
                "limit_price": f"{abs(request.limit_price):.2f}",
                "time_in_force": request.time_in_force,
                "position_intent": leg.position_intent,
                "client_order_id": request.client_order_id,
            }
        if len(request.legs) > 4:
            raise ValueError("Alpaca multi-leg orders take at most 4 legs")
        return {
            "order_class": "mleg",
            "qty": str(request.qty),
            "type": "limit",
            "limit_price": f"{request.limit_price:.2f}",
            "time_in_force": request.time_in_force,
            "client_order_id": request.client_order_id,
            "legs": [
                {"symbol": leg.symbol, "ratio_qty": str(leg.ratio), "side": leg.side.value, "position_intent": leg.position_intent}
                for leg in request.legs
            ],
        }

    @staticmethod
    def parse_order(o: dict) -> OrderState:
        limit = _f(o.get("limit_price"))
        legs = o.get("legs") or []
        multi_leg = o.get("order_class") == "mleg" or bool(legs)
        filled_price: float | None = None
        if legs:
            total = 0.0
            complete = True
            for leg in legs:
                price = _f(leg.get("filled_avg_price"))
                if price is None:
                    complete = False
                    break
                ratio = int(float(leg.get("ratio_qty") or 1))
                total += (1 if leg.get("side") == "buy" else -1) * price * ratio
            if complete:
                filled_price = total
        if filled_price is None:
            avg = _f(o.get("filled_avg_price"))
            if avg is not None:
                credit = (limit is not None and limit < 0) if multi_leg else o.get("side") == "sell"
                filled_price = -abs(avg) if credit else avg
        if not multi_leg and limit is not None and o.get("side") == "sell":
            limit = -abs(limit)
        return OrderState(
            id=o["id"],
            client_order_id=o.get("client_order_id") or "",
            status=OrderStatus.parse(o.get("status")),
            qty=int(float(o.get("qty") or 0)),
            filled_qty=int(float(o.get("filled_qty") or 0)),
            filled_price=filled_price,
            limit_price=limit,
            raw=o,
        )

    def submit_order(self, request: OrderRequest) -> OrderState:
        payload = self.order_payload(request)
        try:
            body = self._request(self._trading, "POST", "/v2/orders", json=payload)
        except BrokerError as exc:
            if exc.status is None:
                # transport failure: the order may or may not have reached Alpaca
                existing = self.get_order_by_client_id(request.client_order_id)
                if existing is not None:
                    return existing
            raise
        return self.parse_order(body)

    def get_order(self, order_id: str) -> OrderState:
        return self.parse_order(self._request(self._trading, "GET", f"/v2/orders/{order_id}", params={"nested": "true"}))

    def get_order_by_client_id(self, client_order_id: str) -> OrderState | None:
        try:
            body = self._request(self._trading, "GET", "/v2/orders:by_client_order_id", params={"client_order_id": client_order_id})
        except BrokerError as exc:
            if exc.status == 404:
                return None
            raise
        return self.parse_order(body)

    def cancel_order(self, order_id: str) -> None:
        try:
            self._request(self._trading, "DELETE", f"/v2/orders/{order_id}")
        except BrokerError as exc:
            if exc.status not in (404, 422):  # already filled / already gone
                raise

    def list_open_orders(self) -> list[OrderState]:
        rows = self._request(self._trading, "GET", "/v2/orders", params={"status": "open", "nested": "true", "limit": 500}) or []
        return [self.parse_order(o) for o in rows]
