"""Alpaca adapter against mocked HTTP responses shaped like the documented API."""

from datetime import date

import httpx
import pytest
import respx

from optionsbot.broker.alpaca import AlpacaBroker, parse_ts
from optionsbot.broker.base import BrokerError, OrderLeg, OrderRequest, OrderStatus
from optionsbot.models import OptionType, Side

PAPER = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"


@pytest.fixture
def broker():
    b = AlpacaBroker("key", "secret", PAPER, DATA)
    b._sleep = lambda s: None
    yield b
    b.close()


def test_parse_ts_handles_nanoseconds():
    ts = parse_ts("2026-10-27T10:30:00.123456789-04:00")
    assert ts.microsecond == 123456 and ts.utcoffset().total_seconds() == -4 * 3600
    assert parse_ts("2026-10-27T14:30:00Z").tzinfo is not None


@respx.mock
def test_account_clock_and_positions(broker):
    respx.get(f"{PAPER}/v2/account").mock(
        return_value=httpx.Response(
            200,
            json={
                "equity": "100250.5",
                "last_equity": "100000",
                "cash": "90000",
                "buying_power": "180000",
                "options_buying_power": "85000",
                "options_trading_level": 3,
                "trading_blocked": False,
                "status": "ACTIVE",
            },
        )
    )
    respx.get(f"{PAPER}/v2/clock").mock(
        return_value=httpx.Response(
            200,
            json={
                "timestamp": "2026-10-27T10:30:00.5-04:00",
                "is_open": True,
                "next_open": "2026-10-28T09:30:00-04:00",
                "next_close": "2026-10-27T16:00:00-04:00",
            },
        )
    )
    respx.get(f"{PAPER}/v2/positions").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "symbol": "SPY261218P00550000",
                    "qty": "-2",
                    "side": "short",
                    "avg_entry_price": "3.1",
                    "current_price": "2.5",
                    "market_value": "-500",
                    "asset_class": "us_option",
                },
                {
                    "symbol": "SPY261218P00545000",
                    "qty": "2",
                    "side": "long",
                    "avg_entry_price": "2.0",
                    "current_price": "1.6",
                    "market_value": "320",
                    "asset_class": "us_option",
                },
            ],
        )
    )
    account = broker.get_account()
    assert (account.equity, account.last_equity, account.options_buying_power, account.options_level) == (100250.5, 100000.0, 85000.0, 3)
    clock = broker.get_clock()
    assert clock.is_open and clock.next_close.hour == 16
    positions = broker.get_positions()
    assert [p.qty for p in positions] == [-2, 2]
    assert respx.calls[0].request.headers["APCA-API-KEY-ID"] == "key"


@respx.mock
def test_daily_bars_paginate_and_use_exchange_dates(broker):
    route = respx.get(f"{DATA}/v2/stocks/SPY/bars").mock(
        side_effect=[
            httpx.Response(
                200, json={"bars": [{"t": "2026-10-22T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}], "next_page_token": "abc"}
            ),
            httpx.Response(
                200, json={"bars": [{"t": "2026-10-23T04:00:00Z", "o": 1.5, "h": 2.5, "l": 1, "c": 2, "v": 200}], "next_page_token": None}
            ),
        ]
    )
    bars = broker.get_daily_bars("SPY", date(2026, 10, 1), date(2026, 10, 27))
    assert list(bars.index) == [date(2026, 10, 22), date(2026, 10, 23)]
    assert bars["close"].tolist() == [1.5, 2.0]
    second = route.calls[1].request.url.params
    assert second["page_token"] == "abc" and second["adjustment"] == "all" and second["feed"] == "sip"


@respx.mock
def test_option_chain_parses_snapshots_and_merges_open_interest(broker):
    respx.get(f"{DATA}/v1beta1/options/snapshots/SPY").mock(
        return_value=httpx.Response(
            200,
            json={
                "snapshots": {
                    "SPY261218P00550000": {
                        "latestQuote": {"ap": 3.2, "as": 10, "ax": "C", "bp": 3.1, "bs": 12, "bx": "C", "c": "A", "t": "2026-10-27T14:29:59.1Z"},
                        "greeks": {"delta": -0.25, "gamma": 0.01, "theta": -0.05, "vega": 0.6, "rho": -0.1},
                        "impliedVolatility": 0.19,
                    },
                    "SPY261218C00650000": {
                        "latestQuote": {"ap": 1.1, "as": 5, "ax": "C", "bp": 1.0, "bs": 5, "bx": "C", "c": "A", "t": "2026-10-27T14:29:59Z"}
                    },
                    "SPY261218C00660000": {"latestTrade": {"p": 0.5}},
                },
                "next_page_token": None,
            },
        )
    )
    respx.get(f"{PAPER}/v2/options/contracts").mock(
        return_value=httpx.Response(
            200, json={"option_contracts": [{"symbol": "SPY261218P00550000", "open_interest": "1234"}], "next_page_token": None}
        )
    )
    chain = broker.get_option_chain("SPY", date(2026, 11, 20), date(2026, 12, 31), 600.0)
    by = chain.by_symbol()
    assert set(by) == {"SPY261218P00550000", "SPY261218C00650000"}  # no quote, no row
    put = by["SPY261218P00550000"]
    assert (put.contract.strike, put.contract.type, put.contract.expiration) == (550.0, OptionType.PUT, date(2026, 12, 18))
    assert (put.bid, put.ask, put.delta, put.iv, put.open_interest) == (3.1, 3.2, -0.25, 0.19, 1234)
    assert by["SPY261218C00650000"].delta is None and by["SPY261218C00650000"].open_interest is None


def test_order_payloads_follow_alpaca_conventions():
    credit = OrderRequest(
        legs=[OrderLeg("SPY261218P00550000", Side.SELL, "sell_to_open"), OrderLeg("SPY261218P00545000", Side.BUY, "buy_to_open")],
        qty=3,
        limit_price=-1.25,
        client_order_id="ob-1-open-0-abc",
    )
    body = AlpacaBroker.order_payload(credit)
    assert body["order_class"] == "mleg" and body["limit_price"] == "-1.25" and body["qty"] == "3"
    assert body["legs"][0] == {"symbol": "SPY261218P00550000", "ratio_qty": "1", "side": "sell", "position_intent": "sell_to_open"}
    single = OrderRequest(legs=[OrderLeg("SPY261218P00550000", Side.BUY, "buy_to_close")], qty=1, limit_price=0.35, client_order_id="x")
    body = AlpacaBroker.order_payload(single)
    assert body["side"] == "buy" and body["limit_price"] == "0.35" and "order_class" not in body
    with pytest.raises(ValueError):
        AlpacaBroker.order_payload(OrderRequest(legs=credit.legs * 3, qty=1, limit_price=1, client_order_id="y"))


def test_parse_order_net_price_from_legs():
    order = AlpacaBroker.parse_order(
        {
            "id": "abc",
            "client_order_id": "ob-1",
            "status": "filled",
            "qty": "2",
            "filled_qty": "2",
            "filled_avg_price": "1.20",
            "limit_price": "-1.20",
            "order_class": "mleg",
            "legs": [
                {"symbol": "A", "side": "sell", "filled_avg_price": "3.10", "ratio_qty": "1"},
                {"symbol": "B", "side": "buy", "filled_avg_price": "1.88", "ratio_qty": "1"},
            ],
        }
    )
    assert order.status is OrderStatus.FILLED and order.filled_qty == 2
    assert order.filled_price == pytest.approx(-1.22)
    flat = {
        "id": "x",
        "status": "filled",
        "qty": "1",
        "filled_qty": "1",
        "filled_avg_price": "1.20",
        "limit_price": "-1.20",
        "order_class": "mleg",
        "legs": None,
    }
    assert AlpacaBroker.parse_order(flat).filled_price == pytest.approx(-1.20)
    sell = AlpacaBroker.parse_order({"id": "y", "status": "filled", "qty": "1", "filled_qty": "1", "filled_avg_price": "0.40", "side": "sell"})
    assert sell.filled_price == pytest.approx(-0.40)


@respx.mock
def test_submit_cancel_lookup_and_errors(broker):
    respx.post(f"{PAPER}/v2/orders").mock(
        return_value=httpx.Response(
            200, json={"id": "o1", "client_order_id": "c1", "status": "accepted", "qty": "1", "filled_qty": "0", "limit_price": "-1.25", "legs": []}
        )
    )
    respx.delete(f"{PAPER}/v2/orders/o1").mock(return_value=httpx.Response(422, json={"code": 42210000, "message": "order is already filled"}))
    respx.get(f"{PAPER}/v2/orders:by_client_order_id").mock(return_value=httpx.Response(404, json={"message": "order not found"}))
    request = OrderRequest(
        legs=[OrderLeg("A", Side.SELL, "sell_to_open"), OrderLeg("B", Side.BUY, "buy_to_open")], qty=1, limit_price=-1.25, client_order_id="c1"
    )
    state = broker.submit_order(request)
    assert state.id == "o1" and state.status is OrderStatus.ACCEPTED
    broker.cancel_order("o1")  # 422 "already filled" is not an error
    assert broker.get_order_by_client_id("nope") is None
    respx.get(f"{PAPER}/v2/account").mock(return_value=httpx.Response(403, json={"message": "forbidden"}))
    with pytest.raises(BrokerError) as err:
        broker.get_account()
    assert err.value.status == 403 and "forbidden" in str(err.value)


@respx.mock
def test_get_requests_retry_on_rate_limit(broker):
    respx.get(f"{PAPER}/v2/clock").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(
                200,
                json={
                    "timestamp": "2026-10-27T10:30:00-04:00",
                    "is_open": False,
                    "next_open": "2026-10-28T09:30:00-04:00",
                    "next_close": "2026-10-28T16:00:00-04:00",
                },
            ),
        ]
    )
    assert broker.get_clock().is_open is False


@respx.mock
def test_transport_failure_on_submit_checks_for_the_order(broker):
    respx.post(f"{PAPER}/v2/orders").mock(side_effect=httpx.ConnectError("reset"))
    respx.get(f"{PAPER}/v2/orders:by_client_order_id").mock(
        return_value=httpx.Response(200, json={"id": "o9", "client_order_id": "c9", "status": "new", "qty": "1", "filled_qty": "0"})
    )
    request = OrderRequest(legs=[OrderLeg("A", Side.BUY, "buy_to_open")], qty=1, limit_price=1.0, client_order_id="c9")
    assert broker.submit_order(request).id == "o9"
