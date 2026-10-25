import { describe, expect, it } from "vitest";

import { changeTone, formatAge, formatCompact, formatPct, formatPrice, formatSigned, pricePrecision } from "./format";
import { formatBarTime } from "./time";
import { paramsQuery, resolveParams } from "./catalog";

describe("formatting", () => {
  it("chooses precision by magnitude", () => {
    expect(pricePrecision(65000)).toBe(2);
    expect(pricePrecision(0.25)).toBe(4);
    expect(pricePrecision(0.0000123)).toBe(8);
    expect(formatPrice(65432.1)).toBe("65,432.10");
    expect(formatPrice(0.000012345)).toBe("0.00001235");
  });

  it("never renders invalid numbers as values", () => {
    expect(formatPrice(null)).toBe("—");
    expect(formatPrice(Number.NaN)).toBe("—");
    expect(formatPct(undefined)).toBe("—");
  });

  it("formats signed values and percentages", () => {
    expect(formatSigned(12.5)).toBe("+12.50");
    expect(formatSigned(-12.5)).toBe("−12.50");
    expect(formatPct(1.234)).toBe("+1.23%");
    expect(formatPct(-0.5)).toBe("−0.50%");
    expect(formatPct(3, 1, false)).toBe("3.0%");
    expect(changeTone(-1)).toBe("down");
    expect(changeTone(0)).toBe("flat");
  });

  it("formats volume and ages compactly", () => {
    expect(formatCompact(1_250_000)).toBe("1.25M");
    expect(formatAge(30)).toBe("30s");
    expect(formatAge(600)).toBe("10 min");
    expect(formatAge(7200)).toBe("2.0 h");
  });

  it("formats daily bars as UTC session dates regardless of display zone", () => {
    const ts = Date.UTC(2024, 2, 4) / 1000; // 2024-03-04 00:00 UTC
    expect(formatBarTime(ts, "1d", "America/Los_Angeles")).toBe("Mar 04, 2024");
    expect(formatBarTime(ts, "1h", "UTC")).toContain("00:00");
  });
});

describe("indicator params", () => {
  it("sends only changed parameters", () => {
    expect(paramsQuery({ rsi_period: 14 })).toEqual({});
    expect(paramsQuery({ rsi_period: 7, sma_periods: [10, 20] })).toEqual({ rsi_period: 7, sma_periods: "10,20" });
    expect(resolveParams({ rsi_period: 7 }).macd_slow).toBe(26);
  });
});
