import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { fetchPreferences, savePreferences } from "../api/hooks";
import type { Preferences } from "../api/types";

const STORAGE_KEY = "marketlens.preferences";

export const DEFAULT_PREFERENCES: Preferences = {
  theme: "dark",
  timeframe: "1d",
  chart_type: "candles",
  timezone_mode: "local",
  last_symbol: null,
  last_asset_type: "auto",
  bars: 500,
  overlays: ["sma", "ema"],
  panels: ["rsi", "macd"],
  show_volume: true,
  show_levels: true,
  show_reference_levels: false,
  show_pivots: false,
  show_fibonacci: false,
  show_trendlines: true,
  auto_refresh: true,
  indicator_params: {},
};

function loadLocal(): Preferences {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...DEFAULT_PREFERENCES, ...(JSON.parse(raw) as Partial<Preferences>) };
  } catch {
    /* ignore corrupt or unavailable storage */
  }
  return DEFAULT_PREFERENCES;
}

function storeLocal(prefs: Preferences): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    /* storage unavailable */
  }
}

interface PreferencesContextValue {
  prefs: Preferences;
  update: (patch: Partial<Preferences>) => void;
  syncError: string | null;
}

const PreferencesContext = createContext<PreferencesContextValue | null>(null);

/**
 * Preferences are applied instantly from localStorage, then reconciled with the server copy
 * (tied to an anonymous workspace token) so they persist across sessions and devices that
 * share the token. Writes are debounced.
 */
export function PreferencesProvider({ children, syncWithServer = true }: { children: ReactNode; syncWithServer?: boolean }) {
  const [prefs, setPrefs] = useState<Preferences>(loadLocal);
  const [syncError, setSyncError] = useState<string | null>(null);
  const loadedFromServer = useRef(!syncWithServer);
  const saveTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!syncWithServer) return;
    let cancelled = false;
    fetchPreferences()
      .then((server) => {
        if (cancelled) return;
        loadedFromServer.current = true;
        const hasLocal = (() => {
          try {
            return localStorage.getItem(STORAGE_KEY) !== null;
          } catch {
            return false;
          }
        })();
        // Local edits win on this device; otherwise adopt the server copy.
        if (!hasLocal) setPrefs({ ...DEFAULT_PREFERENCES, ...server });
      })
      .catch(() => {
        loadedFromServer.current = true;
        setSyncError("Preferences are saved in this browser only (server sync unavailable).");
      });
    return () => {
      cancelled = true;
    };
  }, [syncWithServer]);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", prefs.theme === "dark");
  }, [prefs.theme]);

  const update = useCallback(
    (patch: Partial<Preferences>) => {
      setPrefs((current) => {
        const next = { ...current, ...patch };
        storeLocal(next);
        if (syncWithServer && loadedFromServer.current) {
          window.clearTimeout(saveTimer.current);
          saveTimer.current = window.setTimeout(() => {
            savePreferences(next)
              .then(() => setSyncError(null))
              .catch(() => setSyncError("Could not save preferences to the server; they are kept in this browser."));
          }, 800);
        }
        return next;
      });
    },
    [syncWithServer],
  );

  const value = useMemo(() => ({ prefs, update, syncError }), [prefs, update, syncError]);
  return <PreferencesContext.Provider value={value}>{children}</PreferencesContext.Provider>;
}

export function usePreferences(): PreferencesContextValue {
  const ctx = useContext(PreferencesContext);
  if (!ctx) throw new Error("usePreferences must be used inside PreferencesProvider");
  return ctx;
}
