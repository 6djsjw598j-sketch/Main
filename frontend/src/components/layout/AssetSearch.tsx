import { useEffect, useId, useRef, useState } from "react";

import { useSearch } from "../../api/hooks";
import type { AssetInfo, AssetType } from "../../api/types";
import { ErrorState } from "../common/ErrorState";
import { IconSearch } from "../common/icons";
import { Badge, Spinner, cx } from "../common/ui";

const TYPE_LABEL: Record<string, string> = {
  stock: "Stock", etf: "ETF", index: "Index", crypto: "Crypto", forex: "FX", fund: "Fund", other: "Other", auto: "",
};

export function AssetSearch({ assetType, onSelect }: { assetType: AssetType; onSelect: (symbol: string, type: AssetType) => void }) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const wrapRef = useRef<HTMLDivElement>(null);
  const search = useSearch(text, assetType);
  const results = search.data?.results ?? [];

  useEffect(() => setActive(0), [search.data]);
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const choose = (item: AssetInfo | null) => {
    if (item) {
      onSelect(item.symbol, assetType === "auto" ? item.asset_type : assetType);
    } else if (text.trim()) {
      onSelect(text.trim().toUpperCase().replace("/", "-"), assetType);
    }
    setText("");
    setOpen(false);
  };

  return (
    <div ref={wrapRef} className="relative w-full sm:w-80">
      <div className="flex items-center gap-2 rounded-md border border-line bg-surface-2 px-2 focus-within:border-accent">
        <IconSearch className="h-4 w-4 text-muted" />
        <input
          role="combobox"
          aria-expanded={open && text.length > 0}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-label="Search symbol"
          placeholder="Search symbol or name (AAPL, BTC-USD)…"
          className="h-8 w-full bg-transparent text-sm outline-none placeholder:text-muted"
          value={text}
          maxLength={40}
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((a) => Math.min(a + 1, results.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(a - 1, 0));
            } else if (e.key === "Enter") {
              e.preventDefault();
              choose(results[active] ?? null);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
        />
        {search.isFetching && <Spinner label="Searching" />}
      </div>
      {open && text.trim().length > 0 && (
        <div className="absolute z-50 mt-1 w-full overflow-hidden rounded-md border border-line bg-surface shadow-xl sm:w-[28rem]">
          {search.error ? (
            <ErrorState error={search.error} compact className="m-2" />
          ) : (
            <ul id={listId} role="listbox" className="scroll-thin max-h-80 overflow-y-auto py-1">
              {results.map((item, i) => (
                <li
                  key={`${item.provider}:${item.symbol}`}
                  role="option"
                  aria-selected={i === active}
                  onMouseEnter={() => setActive(i)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    choose(item);
                  }}
                  className={cx("flex cursor-pointer items-center justify-between gap-2 px-3 py-1.5 text-sm", i === active && "bg-surface-3")}
                >
                  <div className="min-w-0">
                    <div className="font-semibold">{item.display_symbol}</div>
                    <div className="truncate text-xs text-muted">{item.name ?? "—"}</div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {item.exchange && <span className="text-[11px] text-muted">{item.exchange}</span>}
                    <Badge>{TYPE_LABEL[item.asset_type] ?? item.asset_type}</Badge>
                  </div>
                </li>
              ))}
              {!search.isFetching && results.length === 0 && search.data && (
                <li className="px-3 py-2 text-xs text-muted">
                  No matches. Press Enter to try “{text.trim().toUpperCase()}” directly.
                </li>
              )}
            </ul>
          )}
          {search.data?.notes.map((note) => (
            <p key={note} className="border-t border-line px-3 py-1.5 text-[11px] text-muted">{note}</p>
          ))}
        </div>
      )}
    </div>
  );
}
