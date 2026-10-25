import { useEffect, useState } from "react";

import { ApiError } from "../../api/client";
import { cx } from "./ui";

export function errorTitle(error: unknown): string {
  if (!(error instanceof ApiError)) return "Something went wrong";
  switch (error.code) {
    case "provider_not_configured":
      return "Market-data provider not configured";
    case "symbol_not_found":
      return "Symbol not found";
    case "invalid_symbol":
      return "Invalid symbol";
    case "provider_rate_limited":
      return "Data provider rate limit reached";
    case "rate_limited":
      return "Too many requests";
    case "provider_unavailable":
    case "provider_bad_response":
      return "Data provider unavailable";
    case "provider_auth_failed":
      return "Provider rejected the server's API key";
    case "provider_plan_restriction":
      return "Not included in the data plan";
    case "insufficient_data":
      return "Not enough data";
    case "network_error":
    case "backend_unreachable":
      return "Cannot reach the server";
    default:
      return "Request failed";
  }
}

function Countdown({ seconds }: { seconds: number }) {
  const [left, setLeft] = useState(Math.ceil(seconds));
  useEffect(() => {
    setLeft(Math.ceil(seconds));
    const id = window.setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => window.clearInterval(id);
  }, [seconds]);
  return <span className="tabular">{left > 0 ? ` Retrying automatically in ~${left}s.` : " Retrying…"}</span>;
}

export function ErrorState({ error, onRetry, compact, className }: { error: unknown; onRetry?: () => void; compact?: boolean; className?: string }) {
  const apiError = error instanceof ApiError ? error : null;
  const message = apiError?.message ?? (error instanceof Error ? error.message : "Unknown error");
  const isSetup = apiError?.code === "provider_not_configured";
  return (
    <div
      role="alert"
      className={cx(
        "rounded-lg border p-3 text-sm",
        isSetup ? "border-accent/40 bg-accent-soft" : "border-down/40 bg-down-soft",
        compact ? "text-xs" : "",
        className,
      )}
    >
      <div className="font-semibold">{errorTitle(error)}</div>
      <p className="mt-1 leading-relaxed text-fg/90">
        {message}
        {apiError?.code === "provider_rate_limited" && apiError.retryAfter ? <Countdown seconds={apiError.retryAfter} /> : null}
      </p>
      {isSetup && (
        <pre className="mt-2 overflow-x-auto rounded bg-surface p-2 text-[11px]">{`# backend/.env  (or the server's .env)\nTWELVEDATA_API_KEY=your_key_here\n# then restart the backend`}</pre>
      )}
      {onRetry && !isSetup && (
        <button onClick={onRetry} className="mt-2 rounded border border-line bg-surface px-2 py-1 text-xs hover:bg-surface-3">
          Try again
        </button>
      )}
    </div>
  );
}
