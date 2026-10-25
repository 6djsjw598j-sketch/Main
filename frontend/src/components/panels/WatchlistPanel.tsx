import { useState } from "react";

import { useOverview, useWatchlist, useWatchlistMutations } from "../../api/hooks";
import type { AssetType } from "../../api/types";
import { POPULAR } from "../../lib/catalog";
import { changeTone, formatPct, formatPrice } from "../../lib/format";
import { ErrorState } from "../common/ErrorState";
import { IconChevronDown, IconChevronUp, IconGrip, IconX } from "../common/icons";
import { Badge, Button, Skeleton, cx } from "../common/ui";

export function WatchlistPanel({ onSelect, current, autoRefresh, layout = "list" }: {
  onSelect: (symbol: string, type: AssetType) => void;
  current: string | null;
  autoRefresh: boolean;
  layout?: "list" | "grid";
}) {
  const watchlist = useWatchlist();
  const items = watchlist.data?.items ?? [];
  const overview = useOverview(items.map((i) => i.symbol), autoRefresh);
  const { remove, reorder } = useWatchlistMutations();
  const [dragging, setDragging] = useState<string | null>(null);
  const quotes = new Map((overview.data?.items ?? []).map((i) => [i.symbol, i]));

  const move = (symbol: string, delta: number) => {
    const order = items.map((i) => i.symbol);
    const idx = order.indexOf(symbol);
    const target = idx + delta;
    if (idx < 0 || target < 0 || target >= order.length) return;
    [order[idx], order[target]] = [order[target], order[idx]];
    reorder.mutate(order);
  };

  const dropOn = (target: string) => {
    if (!dragging || dragging === target) return;
    const order = items.map((i) => i.symbol).filter((s) => s !== dragging);
    order.splice(order.indexOf(target), 0, dragging);
    reorder.mutate(order);
    setDragging(null);
  };

  if (watchlist.isLoading) return <Skeleton className="m-3 h-32" />;
  if (watchlist.error) return <ErrorState error={watchlist.error} onRetry={() => watchlist.refetch()} className="m-3" />;
  if (items.length === 0) {
    return (
      <div className="p-3 text-xs text-muted" data-testid="watchlist-empty">
        <p>Your watchlist is empty. Use the ☆ next to a symbol to add it, or start with a popular asset:</p>
        <div className="mt-2 flex flex-wrap gap-1">
          {POPULAR.map((p) => (
            <Button key={p.symbol} className="border border-line" onClick={() => onSelect(p.symbol, p.type as AssetType)}>{p.symbol}</Button>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className="p-2">
      <ul className={cx(layout === "grid" ? "grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4" : "space-y-1")}>
        {items.map((item, idx) => {
          const entry = quotes.get(item.symbol);
          const q = entry?.quote;
          const tone = changeTone(q?.change_percent);
          return (
            <li
              key={item.symbol}
              draggable
              onDragStart={() => setDragging(item.symbol)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => dropOn(item.symbol)}
              className={cx(
                "flex items-center gap-2 rounded border border-line px-2 py-1.5 text-xs",
                item.symbol === current ? "bg-accent-soft" : "bg-surface hover:bg-surface-2",
                dragging === item.symbol && "opacity-50",
              )}
            >
              <IconGrip className="h-3.5 w-3.5 shrink-0 cursor-grab text-muted" />
              <button className="min-w-0 flex-1 text-left" onClick={() => onSelect(item.symbol, item.asset_type)}>
                <div className="flex items-center gap-1 font-semibold">
                  {item.symbol}
                  {entry?.freshness && <Badge className="!px-1 !py-0" tone={entry.freshness.is_stale ? "down" : "neutral"}>{entry.freshness.label}</Badge>}
                </div>
                <div className="truncate text-[11px] text-muted">{q?.name ?? (entry?.error ? entry.error.message : "…")}</div>
              </button>
              <div className="tabular text-right">
                <div>{q ? formatPrice(q.price) : overview.isLoading ? "…" : "—"}</div>
                <div className={cx(tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-muted")}>{q ? formatPct(q.change_percent) : ""}</div>
              </div>
              <div className="flex flex-col">
                <button aria-label={`Move ${item.symbol} up`} disabled={idx === 0} onClick={() => move(item.symbol, -1)} className="text-muted hover:text-fg disabled:opacity-30">
                  <IconChevronUp className="h-3 w-3" />
                </button>
                <button aria-label={`Move ${item.symbol} down`} disabled={idx === items.length - 1} onClick={() => move(item.symbol, 1)} className="text-muted hover:text-fg disabled:opacity-30">
                  <IconChevronDown className="h-3 w-3" />
                </button>
              </div>
              <button aria-label={`Remove ${item.symbol}`} onClick={() => remove.mutate(item.symbol)} className="text-muted hover:text-down">
                <IconX className="h-3.5 w-3.5" />
              </button>
            </li>
          );
        })}
      </ul>
      <p className="mt-2 text-[10px] text-muted">
        Quotes refresh every minute. Stock quotes use provider credits; keep the list short on free plans.
      </p>
    </div>
  );
}

export function WatchlistDrawer({ open, onClose, ...rest }: Parameters<typeof WatchlistPanel>[0] & { open: boolean; onClose: () => void }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/40" onMouseDown={onClose}>
      <aside
        role="dialog"
        aria-label="Watchlist"
        className="scroll-thin h-full w-full max-w-sm overflow-y-auto border-l border-line bg-surface shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b border-line px-3 py-2">
          <h2 className="text-sm font-semibold">Watchlist</h2>
          <Button onClick={onClose} ariaLabel="Close watchlist"><IconX /></Button>
        </header>
        <WatchlistPanel {...rest} onSelect={(s, t) => { rest.onSelect(s, t); onClose(); }} />
      </aside>
    </div>
  );
}
