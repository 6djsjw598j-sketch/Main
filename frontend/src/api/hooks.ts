import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { paramsQuery } from "../lib/catalog";
import { ApiError, apiGet, request } from "./client";
import type {
  AssetType,
  BacktestResponse,
  CandlesResponse,
  HealthResponse,
  IndicatorParams,
  IndicatorsResponse,
  MetaResponse,
  OverviewResponse,
  Preferences,
  QuoteResponse,
  SearchResponse,
  SetupsResponse,
  SummaryResponse,
  SupportResistanceResponse,
  Timeframe,
  WatchlistResponse,
} from "./types";

/** Retry transient failures (network, provider outages, rate limits) but not bad input. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (!(error instanceof ApiError)) return failureCount < 2;
  if (error.status === 429 || error.status === 0 || error.status >= 500) {
    return failureCount < 2 && error.code !== "provider_not_configured" && error.code !== "provider_auth_failed";
  }
  return false;
}

export function retryDelay(attempt: number, error: unknown): number {
  if (error instanceof ApiError && error.retryAfter) return Math.min(error.retryAfter * 1000, 30_000);
  return Math.min(1000 * 2 ** attempt, 10_000);
}

export interface AssetKey {
  symbol: string;
  assetType: AssetType;
}

export interface ChartKey extends AssetKey {
  timeframe: Timeframe;
  limit: number;
}

const asset = (k: AssetKey) => ({ asset_type: k.assetType });

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => apiGet<HealthResponse>("/health", undefined, signal),
    refetchInterval: 60_000,
    retry: 1,
  });
}

export function useMeta() {
  return useQuery({
    queryKey: ["meta"],
    queryFn: ({ signal }) => apiGet<MetaResponse>("/meta", undefined, signal),
    staleTime: 10 * 60_000,
  });
}

export function useQuote(k: AssetKey | null, autoRefresh: boolean) {
  return useQuery({
    queryKey: ["quote", k?.symbol, k?.assetType],
    queryFn: ({ signal }) => apiGet<QuoteResponse>(`/market/${encodeURIComponent(k!.symbol)}/quote`, asset(k!), signal),
    enabled: !!k,
    refetchInterval: (query) => (autoRefresh && query.state.data ? query.state.data.refresh_seconds * 1000 : false),
    refetchIntervalInBackground: false,
  });
}

export function useCandles(k: ChartKey | null, autoRefresh: boolean) {
  return useQuery({
    queryKey: ["candles", k?.symbol, k?.assetType, k?.timeframe, k?.limit],
    queryFn: ({ signal }) =>
      apiGet<CandlesResponse>(
        `/market/${encodeURIComponent(k!.symbol)}/candles`,
        { ...asset(k!), timeframe: k!.timeframe, limit: k!.limit },
        signal,
      ),
    enabled: !!k,
    placeholderData: (prev) => (prev && k && prev.symbol === k.symbol ? prev : undefined),
    refetchInterval: (query) => (autoRefresh && query.state.data ? query.state.data.refresh_seconds * 1000 : false),
  });
}

/** Analysis queries refresh on the candle cadence (they are computed from the same cached candles). */
function analysisInterval(autoRefresh: boolean, refreshSeconds: number | undefined): number | false {
  if (!autoRefresh || !refreshSeconds) return false;
  return Math.max(15, refreshSeconds) * 1000;
}

export function useIndicators(k: ChartKey | null, params: Partial<IndicatorParams>, autoRefresh: boolean, refreshSeconds?: number) {
  const extra = paramsQuery(params);
  return useQuery({
    queryKey: ["indicators", k?.symbol, k?.assetType, k?.timeframe, k?.limit, extra],
    queryFn: ({ signal }) =>
      apiGet<IndicatorsResponse>(
        `/analysis/${encodeURIComponent(k!.symbol)}/indicators`,
        { ...asset(k!), timeframe: k!.timeframe, limit: k!.limit, ...extra },
        signal,
      ),
    enabled: !!k,
    placeholderData: (prev) => (prev && k && prev.context.symbol === k.symbol ? prev : undefined),
    refetchInterval: analysisInterval(autoRefresh, refreshSeconds),
  });
}

export function useSupportResistance(k: ChartKey | null, autoRefresh: boolean, refreshSeconds?: number) {
  return useQuery({
    queryKey: ["levels", k?.symbol, k?.assetType, k?.timeframe, k?.limit],
    queryFn: ({ signal }) =>
      apiGet<SupportResistanceResponse>(
        `/analysis/${encodeURIComponent(k!.symbol)}/support-resistance`,
        { ...asset(k!), timeframe: k!.timeframe, limit: k!.limit, lookback: Math.min(300, k!.limit) },
        signal,
      ),
    enabled: !!k,
    placeholderData: (prev) => (prev && k && prev.context.symbol === k.symbol && prev.context.timeframe === k.timeframe ? prev : undefined),
    refetchInterval: analysisInterval(autoRefresh, refreshSeconds),
  });
}

export function useSummary(k: ChartKey | null, params: Partial<IndicatorParams>, autoRefresh: boolean, refreshSeconds?: number) {
  const extra = paramsQuery(params);
  return useQuery({
    queryKey: ["summary", k?.symbol, k?.assetType, k?.timeframe, k?.limit, extra],
    queryFn: ({ signal }) =>
      apiGet<SummaryResponse>(
        `/analysis/${encodeURIComponent(k!.symbol)}/summary`,
        { ...asset(k!), timeframe: k!.timeframe, limit: k!.limit, ...extra },
        signal,
      ),
    enabled: !!k,
    placeholderData: (prev) => (prev && k && prev.context.symbol === k.symbol && prev.context.timeframe === k.timeframe ? prev : undefined),
    refetchInterval: analysisInterval(autoRefresh, refreshSeconds),
  });
}

export function useSetups(k: ChartKey | null, params: Partial<IndicatorParams>, autoRefresh: boolean, refreshSeconds?: number) {
  const extra = paramsQuery(params);
  return useQuery({
    queryKey: ["setups", k?.symbol, k?.assetType, k?.timeframe, k?.limit, extra],
    queryFn: ({ signal }) =>
      apiGet<SetupsResponse>(
        `/analysis/${encodeURIComponent(k!.symbol)}/setups`,
        { ...asset(k!), timeframe: k!.timeframe, limit: k!.limit, ...extra },
        signal,
      ),
    enabled: !!k,
    placeholderData: keepPreviousData,
    refetchInterval: analysisInterval(autoRefresh, refreshSeconds),
  });
}

export interface BacktestSettings {
  entry_threshold: number;
  exit_threshold: number;
  commission_bps: number;
  slippage_bps: number;
  direction: "long_only" | "long_short";
  limit: number;
}

export function useBacktest(k: AssetKey & { timeframe: Timeframe }, settings: BacktestSettings, enabled: boolean) {
  return useQuery({
    queryKey: ["backtest", k.symbol, k.assetType, k.timeframe, settings],
    queryFn: ({ signal }) =>
      apiGet<BacktestResponse>(
        `/analysis/${encodeURIComponent(k.symbol)}/backtest`,
        { ...asset(k), timeframe: k.timeframe, ...settings },
        signal,
      ),
    enabled,
    staleTime: 5 * 60_000,
  });
}

export function useDebounced<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

export function useSearch(query: string, assetType: AssetType) {
  const q = useDebounced(query.trim(), 300);
  return useQuery({
    queryKey: ["search", q.toUpperCase(), assetType],
    queryFn: ({ signal }) => apiGet<SearchResponse>("/assets/search", { q, asset_type: assetType }, signal),
    enabled: q.length >= 1,
    staleTime: 10 * 60_000,
    placeholderData: keepPreviousData,
  });
}

// ------------------------------------------------------------------ watchlist & preferences
export function useWatchlist() {
  return useQuery({
    queryKey: ["watchlist"],
    queryFn: () => request<WatchlistResponse>("/watchlist", { auth: true }),
    staleTime: 30_000,
  });
}

export function useOverview(symbols: string[], autoRefresh: boolean) {
  return useQuery({
    queryKey: ["overview", symbols],
    queryFn: ({ signal }) => apiGet<OverviewResponse>("/market/overview", { symbols: symbols.join(",") }, signal),
    enabled: symbols.length > 0,
    refetchInterval: autoRefresh ? 60_000 : false,
    placeholderData: keepPreviousData,
  });
}

export function useWatchlistMutations() {
  const client = useQueryClient();
  const onSuccess = (data: WatchlistResponse) => client.setQueryData(["watchlist"], data);
  const add = useMutation({
    mutationFn: (item: { symbol: string; asset_type: AssetType }) =>
      request<WatchlistResponse>("/watchlist", { method: "POST", body: item, auth: true }),
    onSuccess,
  });
  const remove = useMutation({
    mutationFn: (symbol: string) => request<void>(`/watchlist/${encodeURIComponent(symbol)}`, { method: "DELETE", auth: true }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["watchlist"] }),
  });
  const reorder = useMutation({
    mutationFn: (symbols: string[]) => request<WatchlistResponse>("/watchlist/order", { method: "PUT", body: { symbols }, auth: true }),
    onMutate: async (symbols) => {
      await client.cancelQueries({ queryKey: ["watchlist"] });
      const previous = client.getQueryData<WatchlistResponse>(["watchlist"]);
      if (previous) {
        const bySymbol = new Map(previous.items.map((i) => [i.symbol, i]));
        client.setQueryData<WatchlistResponse>(["watchlist"], {
          ...previous,
          items: symbols.map((s, position) => ({ ...bySymbol.get(s)!, position })),
        });
      }
      return { previous };
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) client.setQueryData(["watchlist"], context.previous);
    },
    onSuccess,
  });
  return { add, remove, reorder };
}

export const fetchPreferences = () => request<Preferences>("/preferences", { auth: true });
export const savePreferences = (prefs: Preferences) => request<Preferences>("/preferences", { method: "PUT", body: prefs, auth: true });
