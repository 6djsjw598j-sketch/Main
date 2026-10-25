"""Educational trade-scenario builder.

Scenarios are derived only from detected S/R zones and ATR:

* Bullish "pullback": entry zone = nearest support zone; invalidation = zone low - 0.25 ATR.
* Bullish "breakout": used when price is within 0.75 ATR of the nearest resistance (or
  just broke it); entry zone = [zone high, zone high + 0.25 ATR]; invalidation = zone
  low - 0.25 ATR (a close back inside/below the zone means the breakout failed).
* Targets = the next resistance zones above the entry (their lower edge), never an
  invented number. Reward/risk = (target - entry reference) / (entry reference - invalidation).
* Bearish scenarios mirror these rules.

No scenario is produced when there is not enough data or no level to anchor it.
"""

from __future__ import annotations

import pandas as pd

from app.analysis.support_resistance import SRResult
from app.indicators.engine import fmt_price
from app.models.analysis import Level, SetupTarget, TradeScenario

MIN_BARS = 100

METHODOLOGY = [
    "Entry zones, invalidation levels and targets come only from algorithmically detected support/resistance zones and ATR.",
    "Pullback scenarios use the nearest zone as the entry area; breakout scenarios are used when price is within 0.75 ATR of the zone it would break.",
    "Invalidation sits 0.25 ATR beyond the anchoring zone to allow for normal noise.",
    "Targets are the next opposing zones (their near edge). If none exist, no target is shown rather than an invented one.",
    "Reward/risk is measured from the middle of the entry zone. It ignores fees, slippage, gaps and the chance of reaching each level.",
]


def _rr(entry: float, invalidation: float, target: float, bullish: bool) -> float | None:
    risk = entry - invalidation if bullish else invalidation - entry
    reward = target - entry if bullish else entry - target
    if risk <= 0 or reward <= 0:
        return None
    return round(reward / risk, 2)


def _alignment(bias: str, bullish: bool) -> str:
    if bias == "neutral":
        return "neutral_bias"
    return "with_bias" if (bias == "bullish") == bullish else "counter_bias"


def build_scenario(
    frame: pd.DataFrame,
    sr: SRResult,
    atr_value: float | None,
    bias: str,
    bullish: bool,
    volume_available: bool,
) -> TradeScenario:
    direction = "bullish" if bullish else "bearish"
    if len(frame) < MIN_BARS or atr_value is None or atr_value <= 0:
        return TradeScenario(
            direction=direction,
            available=False,  # type: ignore[arg-type]
            unavailable_reason=f"Needs at least {MIN_BARS} bars and a valid ATR; {len(frame)} bars available.",
        )
    price = float(frame["close"].iloc[-1])
    supports = sorted([lv for lv in sr.levels if lv.kind == "support"], key=lambda lv: lv.zone_high, reverse=True)
    resistances = sorted([lv for lv in sr.levels if lv.kind == "resistance"], key=lambda lv: lv.zone_low)
    near_side = supports if bullish else resistances  # zone price would pull back to
    far_side = resistances if bullish else supports  # zone price would break / target
    anchor: Level | None = near_side[0] if near_side else None
    barrier: Level | None = far_side[0] if far_side else None
    buffer = 0.25 * atr_value
    notes: list[str] = []

    breakout_ready = barrier is not None and (
        barrier.distance <= 0.75 * atr_value or barrier.status in (("breakout", "reclaimed") if bullish else ("breakdown", "lost"))
    )
    pullback_close = anchor is not None and anchor.distance <= 1.5 * atr_value

    if anchor is not None and (pullback_close or not breakout_ready):
        setup_type = "Pullback to support" if bullish else "Rally into resistance"
        zone_low, zone_high = anchor.zone_low, anchor.zone_high
        invalidation = zone_low - buffer if bullish else zone_high + buffer
        target_levels = far_side
        if not pullback_close:
            notes.append(f"Price is {anchor.distance / atr_value:.1f} ATR from the entry zone; the scenario needs price to come back to it first.")
        anchor_text = f"{'support' if bullish else 'resistance'} zone {fmt_price(zone_low)}–{fmt_price(zone_high)}"
        if bullish:
            confirmation = [
                f"Price trades into the {anchor_text} and a bar closes back above {fmt_price(zone_high)}",
                "RSI holds above 40 and turns higher during the pullback",
            ]
        else:
            confirmation = [
                f"Price trades into the {anchor_text} and a bar closes back below {fmt_price(zone_low)}",
                "RSI holds below 60 and turns lower during the rally",
            ]
        if volume_available:
            confirmation.append("Volume on the reversal bar is above its 20-bar average")
    elif barrier is not None:
        setup_type = "Breakout above resistance" if bullish else "Breakdown below support"
        if bullish:
            zone_low, zone_high = barrier.zone_high, barrier.zone_high + buffer
            invalidation = barrier.zone_low - buffer
            confirmation = [
                f"A bar closes above {fmt_price(barrier.zone_high)}",
                f"A retest of {fmt_price(barrier.zone_low)}–{fmt_price(barrier.zone_high)} from above holds",
            ]
        else:
            zone_low, zone_high = barrier.zone_low - buffer, barrier.zone_low
            invalidation = barrier.zone_high + buffer
            confirmation = [
                f"A bar closes below {fmt_price(barrier.zone_low)}",
                f"A retest of {fmt_price(barrier.zone_low)}–{fmt_price(barrier.zone_high)} from below fails",
            ]
        if volume_available:
            confirmation.append("Volume on the breakout bar is at least 1.5× its 20-bar average")
        target_levels = far_side[1:]
    else:
        return TradeScenario(
            direction=direction,
            available=False,  # type: ignore[arg-type]
            unavailable_reason="No support or resistance zone was detected to anchor a scenario.",
        )

    entry_ref = (zone_low + zone_high) / 2.0
    if bullish:
        candidates = [lv for lv in target_levels if lv.zone_low > entry_ref + 0.25 * atr_value][:2]
        targets = [
            SetupTarget(
                price=lv.zone_low,
                basis=f"Lower edge of resistance {fmt_price(lv.zone_low)}–{fmt_price(lv.zone_high)} ({lv.tier}, strength {lv.strength:.0f})",
                reward_risk=_rr(entry_ref, invalidation, lv.zone_low, True),
            )
            for lv in candidates
        ]
    else:
        candidates = [lv for lv in target_levels if lv.zone_high < entry_ref - 0.25 * atr_value][:2]
        targets = [
            SetupTarget(
                price=lv.zone_high,
                basis=f"Upper edge of support {fmt_price(lv.zone_low)}–{fmt_price(lv.zone_high)} ({lv.tier}, strength {lv.strength:.0f})",
                reward_risk=_rr(entry_ref, invalidation, lv.zone_high, False),
            )
            for lv in candidates
        ]
    if not targets:
        notes.append(f"No {'resistance' if bullish else 'support'} zone was detected beyond the entry, so no level-based target is shown.")
    rr = targets[0].reward_risk if targets else None
    if rr is not None and rr < 1.5:
        notes.append(f"Reward/risk to the first target is only {rr:.2f}; many traders require at least 1.5–2.")
    if _alignment(bias, bullish) == "counter_bias":
        notes.append(f"This scenario runs against the current {bias} factor bias.")

    invalidation_conditions = [
        f"A close {'below' if bullish else 'above'} {fmt_price(invalidation)}",
        f"The factor score turning {'bearish' if bullish else 'bullish'} (crossing the {'bearish' if bullish else 'bullish'} threshold)",
    ]
    return TradeScenario(
        direction=direction,  # type: ignore[arg-type]
        available=True,
        setup_type=setup_type,
        alignment=_alignment(bias, bullish),  # type: ignore[arg-type]
        entry_zone_low=round(zone_low, 10),
        entry_zone_high=round(zone_high, 10),
        entry_reference=round(entry_ref, 10),
        invalidation=round(invalidation, 10),
        risk_per_unit=round(abs(entry_ref - invalidation), 10),
        targets=targets,
        reward_risk=rr,
        confirmation_conditions=confirmation,
        invalidation_conditions=invalidation_conditions,
        notes=[*notes, f"Last price {fmt_price(price)}; ATR {fmt_price(atr_value)}."],
    )
