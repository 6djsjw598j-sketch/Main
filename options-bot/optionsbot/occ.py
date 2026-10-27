"""OCC option symbols as used by Alpaca, e.g. ``SPY261218P00550000``.

Format: root (1-6 chars) + expiration YYMMDD + C/P + strike x 1000 as 8 digits.
"""

from __future__ import annotations

import re
from datetime import date

from optionsbot.models import OptionContract, OptionType

_OCC = re.compile(r"^([A-Z0-9.]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


def format_occ(root: str, expiration: date, option_type: OptionType, strike: float) -> str:
    milli = round(strike * 1000)
    if milli <= 0 or milli >= 100_000_000:
        raise ValueError(f"strike out of range for an OCC symbol: {strike}")
    return f"{root.upper()}{expiration:%y%m%d}{'C' if option_type is OptionType.CALL else 'P'}{milli:08d}"


def parse_occ(symbol: str, underlying: str | None = None) -> OptionContract:
    match = _OCC.match(symbol.strip().upper())
    if not match:
        raise ValueError(f"not an OCC option symbol: {symbol!r}")
    root, yy, mm, dd, cp, strike = match.groups()
    return OptionContract(
        symbol=symbol.strip().upper(),
        underlying=(underlying or root).upper(),
        expiration=date(2000 + int(yy), int(mm), int(dd)),
        strike=int(strike) / 1000,
        type=OptionType.CALL if cp == "C" else OptionType.PUT,
    )


def is_occ(symbol: str) -> bool:
    return bool(_OCC.match(symbol.strip().upper()))
