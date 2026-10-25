import { useState } from "react";

import { useHealth } from "../../api/hooks";
import type { AssetType } from "../../api/types";
import { formatDateTime } from "../../lib/time";
import { usePreferences } from "../../state/preferences";
import { IconList, IconMoon, IconSun, Logo } from "../common/icons";
import { Badge, Button, Popover, cx } from "../common/ui";
import { AssetSearch } from "./AssetSearch";

const TYPES: { value: AssetType; label: string }[] = [
  { value: "auto", label: "Auto-detect" },
  { value: "stock", label: "Stock" },
  { value: "etf", label: "ETF" },
  { value: "index", label: "Index" },
  { value: "crypto", label: "Crypto" },
  { value: "forex", label: "Forex" },
];

function DataStatus() {
  const health = useHealth();
  const [open, setOpen] = useState(false);
  const data = health.data;
  let tone = "bg-muted";
  let label = "Checking…";
  if (health.error) {
    tone = "bg-down";
    label = "API offline";
  } else if (data) {
    const configured = data.providers.filter((p) => p.configured);
    const down = configured.filter((p) => p.status === "down" || p.status === "degraded");
    tone = data.status === "error" ? "bg-down" : down.length ? "bg-warn" : "bg-up";
    label = data.status === "error" ? "Degraded" : down.length ? "Provider issues" : `${configured.length} source${configured.length === 1 ? "" : "s"} online`;
  }
  return (
    <div className="relative">
      <Button onClick={() => setOpen((v) => !v)} title="Data status" ariaPressed={open}>
        <span className={cx("h-2 w-2 rounded-full", tone)} aria-hidden />
        <span className="hidden md:inline">{label}</span>
      </Button>
      <Popover open={open} onClose={() => setOpen(false)} className="right-0 w-80">
        <h3 className="mb-2 text-xs font-semibold">Data sources</h3>
        {health.error && <p className="text-xs text-down">The MarketLens API is not responding.</p>}
        {data?.providers.map((p) => (
          <div key={p.name} className="mb-2 rounded border border-line p-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="font-medium">{p.display_name}</span>
              <Badge tone={p.status === "ok" ? "up" : p.status === "not_configured" ? "neutral" : p.status === "unknown" ? "accent" : "down"}>
                {p.status.replace("_", " ")}
              </Badge>
            </div>
            <div className="mt-1 text-muted">{p.asset_classes.join(", ")} · {p.limits}</div>
            {p.credits_remaining_today !== null && <div className="text-muted">Credits left today: {p.credits_remaining_today}</div>}
            {p.last_error && <div className="mt-1 text-down">Last error: {p.last_error}</div>}
            {!p.configured && <div className="mt-1 text-muted">Set TWELVEDATA_API_KEY on the server to enable stocks, ETFs and indices.</div>}
          </div>
        ))}
        {data && <p className="text-[11px] text-muted">API v{data.version} · checked {formatDateTime(data.time)}</p>}
      </Popover>
    </div>
  );
}

export function TopNav({
  assetType,
  onAssetType,
  onSelect,
  onOpenWatchlist,
  watchlistCount,
}: {
  assetType: AssetType;
  onAssetType: (t: AssetType) => void;
  onSelect: (symbol: string, type: AssetType) => void;
  onOpenWatchlist: () => void;
  watchlistCount: number;
}) {
  const { prefs, update } = usePreferences();
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-surface/95 backdrop-blur">
      <div className="mx-auto flex max-w-[1800px] flex-wrap items-center gap-2 px-3 py-2 sm:flex-nowrap">
        <a href="/" className="flex items-center gap-2 pr-2" aria-label="MarketLens home">
          <Logo />
          <span className="text-base font-semibold tracking-tight">MarketLens</span>
        </a>
        <div className="order-3 flex w-full items-center gap-2 sm:order-none sm:w-auto">
          <AssetSearch assetType={assetType} onSelect={onSelect} />
          <select
            aria-label="Asset category"
            className="h-8 rounded-md border border-line bg-surface-2 px-2 text-xs"
            value={assetType}
            onChange={(e) => onAssetType(e.target.value as AssetType)}
          >
            {TYPES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </div>
        <div className="ml-auto flex items-center gap-1">
          <DataStatus />
          <Button
            onClick={() => update({ theme: prefs.theme === "dark" ? "light" : "dark" })}
            ariaLabel={`Switch to ${prefs.theme === "dark" ? "light" : "dark"} theme`}
            title="Toggle theme"
          >
            {prefs.theme === "dark" ? <IconSun /> : <IconMoon />}
          </Button>
          <Button onClick={onOpenWatchlist} title="Watchlist" ariaLabel="Open watchlist">
            <IconList />
            <span className="hidden sm:inline">Watchlist</span>
            {watchlistCount > 0 && <Badge tone="accent">{watchlistCount}</Badge>}
          </Button>
        </div>
      </div>
    </header>
  );
}
