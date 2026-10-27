"""The playbook: which structure fits the current read of the underlying.

                     IV rank high (rich premium)        IV rank low (cheap premium)
  bullish trend      bull put credit spread             bull call debit spread (strong trend only)
  bearish trend      bear call credit spread            bear put debit spread (strong trend only)
  no clear trend     iron condor (IVR >= condor min,    stand aside
                     ADX below max_adx_condor)

Every structure is defined-risk. A risk-off market blocks new bullish exposure; a crisis
regime blocks all new entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from optionsbot.analysis import Bias, MarketRegime, Regime, UnderlyingView
from optionsbot.config import StrategyConfig
from optionsbot.models import StrategyKind


@dataclass
class Playbook:
    strategies: list[StrategyKind] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def choose_playbook(view: UnderlyingView, regime: MarketRegime, cfg: StrategyConfig) -> Playbook:
    book = Playbook()
    if not regime.allows_entries:
        book.reasons.extend(regime.reasons)
        return book
    if view.price < cfg.min_underlying_price:
        book.reasons.append(f"price {view.price:.2f} below the {cfg.min_underlying_price:.0f} minimum")
        return book
    if view.iv_rank is None or view.vol_forecast is None:
        book.reasons.append("not enough data for IV rank / volatility forecast")
        return book

    ivr = view.iv_rank
    picks: list[StrategyKind] = []
    if ivr >= cfg.min_iv_rank_credit:
        if view.bias is Bias.BULLISH:
            picks = [StrategyKind.BULL_PUT_CREDIT]
            book.reasons.append(f"IV rank {ivr:.0f} >= {cfg.min_iv_rank_credit:.0f} and bullish trend: sell a put spread below the market")
        elif view.bias is Bias.BEARISH:
            picks = [StrategyKind.BEAR_CALL_CREDIT]
            book.reasons.append(f"IV rank {ivr:.0f} >= {cfg.min_iv_rank_credit:.0f} and bearish trend: sell a call spread above the market")
        elif ivr >= cfg.min_iv_rank_condor and (view.adx14 is None or view.adx14 < cfg.max_adx_condor):
            picks = [StrategyKind.IRON_CONDOR]
            book.reasons.append(f"IV rank {ivr:.0f} >= {cfg.min_iv_rank_condor:.0f} with no trend: sell an iron condor around the range")
        else:
            book.reasons.append(f"no trend and IV rank {ivr:.0f} not rich enough (or ADX too high) for a condor: stand aside")
    elif ivr <= cfg.max_iv_rank_debit:
        if view.strong_trend and view.bias is Bias.BULLISH:
            picks = [StrategyKind.BULL_CALL_DEBIT]
            book.reasons.append(f"IV rank {ivr:.0f} is low and the uptrend is strong: buy a call debit spread")
        elif view.strong_trend and view.bias is Bias.BEARISH:
            picks = [StrategyKind.BEAR_PUT_DEBIT]
            book.reasons.append(f"IV rank {ivr:.0f} is low and the downtrend is strong: buy a put debit spread")
        else:
            book.reasons.append(f"IV rank {ivr:.0f} is low (cheap premium) and there is no strong trend: stand aside")
    else:
        book.reasons.append(f"IV rank {ivr:.0f} is neither rich nor cheap: stand aside")

    if regime.regime is Regime.RISK_OFF:
        kept = [k for k in picks if k.direction <= 0]
        if len(kept) < len(picks):
            book.reasons.append("market is risk-off: no new bullish exposure")
        picks = kept

    enabled = set(cfg.enabled)
    disabled = [k for k in picks if k not in enabled]
    if disabled:
        book.reasons.append(f"disabled in config: {', '.join(k.value for k in disabled)}")
    book.strategies = [k for k in picks if k in enabled]
    return book
