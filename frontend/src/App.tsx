import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  useCandles,
  useIndicators,
  useQuote,
  useSetups,
  useSummary,
  useSupportResistance,
  useWatchlist,
  useWatchlistMutations,
  type ChartKey,
} from "./api/hooks";
import type { AssetType } from "./api/types";
import { ChartPanel } from "./components/chart/ChartPanel";
import { Tabs } from "./components/common/ui";
import { TopNav } from "./components/layout/TopNav";
import { AnalysisSidebar } from "./components/panels/AnalysisSidebar";
import { AssetSummary } from "./components/panels/AssetSummary";
import { BacktestPanel } from "./components/panels/BacktestPanel";
import { DataSourcePanel } from "./components/panels/DataSourcePanel";
import { EventsPanel } from "./components/panels/EventsPanel";
import { IndicatorReadings } from "./components/panels/IndicatorReadings";
import { LevelsPanel } from "./components/panels/LevelsPanel";
import { OhlcvTable } from "./components/panels/OhlcvTable";
import { SetupPanel } from "./components/panels/SetupPanel";
import { WatchlistDrawer, WatchlistPanel } from "./components/panels/WatchlistPanel";
import { displayZone } from "./lib/time";
import { usePreferences } from "./state/preferences";

type LowerTab = "indicators" | "levels" | "events" | "ohlcv" | "backtest" | "watchlist" | "data";
const LOWER_TABS: { id: LowerTab; label: string }[] = [
  { id: "indicators", label: "Indicator readings" },
  { id: "levels", label: "Levels" },
  { id: "events", label: "Recent events" },
  { id: "ohlcv", label: "OHLCV data" },
  { id: "backtest", label: "Backtest" },
  { id: "watchlist", label: "Watchlist" },
  { id: "data", label: "Data source" },
];
const ASSET_TYPES: AssetType[] = ["auto", "stock", "etf", "index", "crypto", "forex", "fund", "other"];

function readUrl(): { symbol: string | null; type: AssetType | null } {
  const params = new URLSearchParams(window.location.search);
  const symbol = params.get("symbol");
  const type = params.get("type") as AssetType | null;
  return {
    symbol: symbol && /^[A-Za-z0-9.\-_^=:/]{1,40}$/.test(symbol) ? symbol.toUpperCase().replace("/", "-") : null,
    type: type && ASSET_TYPES.includes(type) ? type : null,
  };
}

export function Dashboard() {
  const { prefs, update, syncError } = usePreferences();
  const queryClient = useQueryClient();
  const [symbol, setSymbol] = useState<string>(() => readUrl().symbol ?? prefs.last_symbol ?? "BTC-USD");
  const [assetType, setAssetType] = useState<AssetType>(() => readUrl().type ?? (readUrl().symbol ? "auto" : prefs.last_asset_type));
  const [tab, setTab] = useState<LowerTab>("indicators");
  const [drawerOpen, setDrawerOpen] = useState(false);

  const select = useCallback(
    (next: string, type: AssetType) => {
      setSymbol(next);
      setAssetType(type);
      update({ last_symbol: next, last_asset_type: type });
      const url = new URL(window.location.href);
      url.searchParams.set("symbol", next);
      if (type === "auto") url.searchParams.delete("type");
      else url.searchParams.set("type", type);
      window.history.pushState({}, "", url);
    },
    [update],
  );

  useEffect(() => {
    const onPop = () => {
      const { symbol: s, type } = readUrl();
      if (s) {
        setSymbol(s);
        setAssetType(type ?? "auto");
      }
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    document.title = `${symbol} · MarketLens`;
  }, [symbol]);

  const assetKey = useMemo(() => ({ symbol, assetType }), [symbol, assetType]);
  const chartKey: ChartKey = useMemo(() => ({ symbol, assetType, timeframe: prefs.timeframe, limit: prefs.bars }), [symbol, assetType, prefs.timeframe, prefs.bars]);
  const auto = prefs.auto_refresh;

  const quote = useQuote(assetKey, auto);
  const candles = useCandles(chartKey, auto);
  const refresh = candles.data?.refresh_seconds;
  const indicators = useIndicators(chartKey, prefs.indicator_params, auto, refresh);
  const levels = useSupportResistance(chartKey, auto, refresh);
  const summary = useSummary(chartKey, prefs.indicator_params, auto, refresh);
  const setups = useSetups(chartKey, prefs.indicator_params, auto, refresh);

  const watchlist = useWatchlist();
  const { add, remove } = useWatchlistMutations();
  const canonical = quote.data?.quote.symbol ?? symbol;
  const inWatchlist = !!watchlist.data?.items.some((i) => i.symbol === canonical);
  const zone = displayZone(prefs.timezone_mode, candles.data?.timezone ?? "UTC");

  const refreshAll = () => {
    for (const key of ["quote", "candles", "indicators", "levels", "summary", "setups"]) {
      queryClient.invalidateQueries({ queryKey: [key, symbol] });
    }
  };

  return (
    <div className="min-h-screen">
      <TopNav
        assetType={assetType}
        onAssetType={(t) => select(symbol, t)}
        onSelect={select}
        onOpenWatchlist={() => setDrawerOpen(true)}
        watchlistCount={watchlist.data?.items.length ?? 0}
      />
      <main className="mx-auto max-w-[1800px] space-y-3 p-3">
        {syncError && <p className="rounded bg-warn-soft px-2 py-1 text-[11px] text-warn">{syncError}</p>}
        <AssetSummary
          symbol={symbol}
          query={quote}
          inWatchlist={inWatchlist}
          watchlistBusy={add.isPending || remove.isPending}
          onToggleWatchlist={() =>
            inWatchlist ? remove.mutate(canonical) : add.mutate({ symbol: canonical, asset_type: assetType })
          }
        />
        {(add.error || remove.error) && (
          <p role="alert" className="text-xs text-down">{(add.error ?? remove.error)?.message}</p>
        )}
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_360px]">
          <div className="min-w-0 space-y-3">
            <ChartPanel
              symbol={symbol}
              candlesQuery={candles}
              indicators={indicators.data}
              indicatorsError={indicators.error}
              levels={levels.data}
              onRefresh={refreshAll}
            />
            <section className="rounded-lg border border-line bg-surface" aria-label="Details">
              <Tabs tabs={LOWER_TABS} value={tab} onChange={setTab} ariaLabel="Detail panels" />
              {tab === "indicators" && <IndicatorReadings query={indicators} />}
              {tab === "levels" && <LevelsPanel query={levels} />}
              {tab === "events" && <EventsPanel data={summary.data} isLoading={summary.isLoading} timeframe={prefs.timeframe} zone={zone} />}
              {tab === "ohlcv" && <OhlcvTable data={candles.data} zone={zone} />}
              {tab === "backtest" && (
                <BacktestPanel symbol={symbol} assetType={assetType} timeframe={prefs.timeframe} theme={prefs.theme} zone={zone} />
              )}
              {tab === "watchlist" && <WatchlistPanel onSelect={select} current={canonical} autoRefresh={auto} layout="grid" />}
              {tab === "data" && <DataSourcePanel quote={quote.data} candles={candles.data} />}
            </section>
          </div>
          <aside className="min-w-0 space-y-3" aria-label="Analysis">
            <AnalysisSidebar query={summary} />
            <SetupPanel query={setups} />
          </aside>
        </div>
        <footer className="pb-4 pt-2 text-center text-[11px] text-muted">
          MarketLens provides educational technical analysis of market data. Nothing here is financial advice or a recommendation to buy or sell.
          Data: Coinbase Exchange (crypto) and Twelve Data (stocks, ETFs, indices) where configured.
        </footer>
      </main>
      <WatchlistDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} onSelect={select} current={canonical} autoRefresh={auto} />
    </div>
  );
}
