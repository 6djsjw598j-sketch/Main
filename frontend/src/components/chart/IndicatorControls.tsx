import { useState } from "react";

import type { IndicatorParams, OverlayKey, PanelKey } from "../../api/types";
import { DEFAULT_PARAMS, OVERLAYS, PANELS, PARAM_GROUPS, resolveParams } from "../../lib/catalog";
import { usePreferences } from "../../state/preferences";
import { IconSliders } from "../common/icons";
import { Button, Modal, Popover, Toggle } from "../common/ui";

export function IndicatorControls({ volumeAvailable, isIntraday }: { volumeAvailable: boolean; isIntraday: boolean }) {
  const { prefs, update } = usePreferences();
  const [open, setOpen] = useState(false);
  const [paramsOpen, setParamsOpen] = useState(false);

  const toggleOverlay = (key: OverlayKey, on: boolean) =>
    update({ overlays: on ? [...prefs.overlays, key] : prefs.overlays.filter((k) => k !== key) });
  const togglePanel = (key: PanelKey, on: boolean) =>
    update({ panels: on ? [...prefs.panels, key] : prefs.panels.filter((k) => k !== key) });

  return (
    <div className="relative">
      <Button onClick={() => setOpen((v) => !v)} ariaPressed={open} title="Indicators and levels">
        <IconSliders /> Indicators
      </Button>
      <Popover open={open} onClose={() => setOpen(false)} className="left-0 w-[min(92vw,560px)]">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div>
            <h3 className="mb-1 text-[11px] font-semibold uppercase text-muted">Price overlays</h3>
            {OVERLAYS.map((o) => (
              <Toggle
                key={o.key}
                label={o.label}
                hint={o.description}
                checked={prefs.overlays.includes(o.key)}
                disabled={o.key === "vwap" && !isIntraday}
                onChange={(v) => toggleOverlay(o.key, v)}
              />
            ))}
            <p className="mt-1 text-[10px] text-muted">VWAP is session-based and shown on intraday timeframes.</p>
          </div>
          <div>
            <h3 className="mb-1 text-[11px] font-semibold uppercase text-muted">Indicator panels</h3>
            {PANELS.map((p) => (
              <Toggle
                key={p.key}
                label={p.label}
                hint={p.needsVolume && !volumeAvailable ? "No volume data for this instrument" : p.description}
                checked={prefs.panels.includes(p.key)}
                disabled={!!p.needsVolume && !volumeAvailable}
                onChange={(v) => togglePanel(p.key, v)}
              />
            ))}
          </div>
          <div>
            <h3 className="mb-1 text-[11px] font-semibold uppercase text-muted">Levels</h3>
            <Toggle label="Volume" checked={prefs.show_volume} onChange={(v) => update({ show_volume: v })} disabled={!volumeAvailable} />
            <Toggle label="Support / resistance zones" checked={prefs.show_levels} onChange={(v) => update({ show_levels: v })} />
            <Toggle label="Trendlines" checked={prefs.show_trendlines} onChange={(v) => update({ show_trendlines: v })} />
            <Toggle label="Prev. day/week/month H/L" checked={prefs.show_reference_levels} onChange={(v) => update({ show_reference_levels: v })} />
            <Toggle label="Pivot points" checked={prefs.show_pivots} onChange={(v) => update({ show_pivots: v })} />
            <Toggle label="Fibonacci retracement" checked={prefs.show_fibonacci} onChange={(v) => update({ show_fibonacci: v })} />
            <Button className="mt-2 border border-line" onClick={() => { setParamsOpen(true); setOpen(false); }}>
              Parameters…
            </Button>
          </div>
        </div>
      </Popover>
      <ParamsDialog open={paramsOpen} onClose={() => setParamsOpen(false)} />
    </div>
  );
}

function ParamsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { prefs, update } = usePreferences();
  const current = resolveParams(prefs.indicator_params);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const value = (key: keyof IndicatorParams) => {
    if (key in draft) return draft[key];
    const v = current[key];
    return Array.isArray(v) ? v.join(",") : String(v);
  };

  const apply = () => {
    const next: Partial<IndicatorParams> = { ...prefs.indicator_params };
    for (const group of PARAM_GROUPS) {
      for (const field of group.fields) {
        if (!(field.key in draft)) continue;
        const raw = draft[field.key].trim();
        if (field.list) {
          const nums = raw.split(",").map((s) => Number(s.trim())).filter((n) => Number.isFinite(n));
          if (!nums.length || nums.length > 6 || nums.some((n) => !Number.isInteger(n) || n < field.min || n > field.max)) {
            setError(`${field.label}: enter up to 6 whole numbers between ${field.min} and ${field.max}, separated by commas.`);
            return;
          }
          (next as Record<string, unknown>)[field.key] = [...new Set(nums)].sort((a, b) => a - b);
        } else {
          const n = Number(raw);
          if (!Number.isFinite(n) || n < field.min || n > field.max) {
            setError(`${field.label} must be between ${field.min} and ${field.max}.`);
            return;
          }
          (next as Record<string, unknown>)[field.key] = n;
        }
      }
    }
    const merged = resolveParams(next);
    if (merged.macd_fast >= merged.macd_slow) return setError("MACD fast must be smaller than MACD slow.");
    if (merged.rsi_oversold >= merged.rsi_overbought) return setError("RSI oversold must be below overbought.");
    if (!(merged.ichimoku_tenkan < merged.ichimoku_kijun && merged.ichimoku_kijun < merged.ichimoku_senkou_b)) {
      return setError("Ichimoku periods must satisfy tenkan < kijun < senkou B.");
    }
    update({ indicator_params: next });
    setDraft({});
    setError(null);
    onClose();
  };

  return (
    <Modal open={open} onClose={onClose} title="Indicator parameters">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {PARAM_GROUPS.map((group) => (
          <fieldset key={group.title} className="space-y-1.5">
            <legend className="mb-1 text-[11px] font-semibold uppercase text-muted">{group.title}</legend>
            {group.fields.map((field) => (
              <label key={field.key} className="flex items-center justify-between gap-2 text-xs">
                <span>{field.label}</span>
                <input
                  className="tabular w-28 rounded border border-line bg-surface-2 px-2 py-1 text-right"
                  inputMode={field.list ? "text" : "decimal"}
                  value={value(field.key)}
                  onChange={(e) => setDraft((d) => ({ ...d, [field.key]: e.target.value }))}
                  aria-label={field.label}
                />
              </label>
            ))}
          </fieldset>
        ))}
      </div>
      {error && <p role="alert" className="mt-3 text-xs text-down">{error}</p>}
      <div className="mt-4 flex justify-between gap-2">
        <Button className="border border-line" onClick={() => { update({ indicator_params: {} }); setDraft({}); setError(null); onClose(); }}>
          Reset to defaults
        </Button>
        <div className="flex gap-2">
          <Button className="border border-line" onClick={onClose}>Cancel</Button>
          <Button active onClick={apply}>Apply</Button>
        </div>
      </div>
      <p className="mt-3 text-[11px] text-muted">
        Defaults: SMA {DEFAULT_PARAMS.sma_periods.join("/")}, EMA {DEFAULT_PARAMS.ema_periods.join("/")}, RSI 14, MACD 12/26/9, Stochastic 14/3/3, Bollinger 20/2, ATR 14, Keltner 20/10/2.
        The server validates every parameter again.
      </p>
    </Modal>
  );
}
