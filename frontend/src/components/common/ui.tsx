import { useEffect, useRef, type ReactNode } from "react";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function Card({ title, actions, children, className, bodyClassName, id }: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  id?: string;
}) {
  return (
    <section id={id} className={cx("rounded-lg border border-line bg-surface", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h2>
          {actions && <div className="flex items-center gap-1">{actions}</div>}
        </header>
      )}
      <div className={cx("p-3", bodyClassName)}>{children}</div>
    </section>
  );
}

export type Tone = "up" | "down" | "warn" | "accent" | "neutral";

const toneClasses: Record<Tone, string> = {
  up: "bg-up-soft text-up",
  down: "bg-down-soft text-down",
  warn: "bg-warn-soft text-warn",
  accent: "bg-accent-soft text-accent",
  neutral: "bg-surface-3 text-muted",
};

export function Badge({ tone = "neutral", children, title, className }: { tone?: Tone; children: ReactNode; title?: string; className?: string }) {
  return (
    <span title={title} className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-medium", toneClasses[tone], className)}>
      {children}
    </span>
  );
}

export function directionTone(value: string | null | undefined): Tone {
  switch (value) {
    case "bullish":
    case "positive":
    case "confirming":
    case "up":
    case "support":
    case "breakout":
    case "reclaimed":
    case "oversold":
      return "up";
    case "bearish":
    case "negative":
    case "diverging":
    case "down":
    case "resistance":
    case "breakdown":
    case "lost":
    case "overbought":
      return "down";
    case "mixed":
    case "testing":
    case "high":
      return "warn";
    default:
      return "neutral";
  }
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span role="status" aria-label={label} className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-muted border-t-transparent" />
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cx("animate-pulse rounded bg-surface-3", className)} />;
}

export function Button({
  children,
  onClick,
  active,
  title,
  className,
  disabled,
  type = "button",
  ariaLabel,
  ariaPressed,
}: {
  children: ReactNode;
  onClick?: () => void;
  active?: boolean;
  title?: string;
  className?: string;
  disabled?: boolean;
  type?: "button" | "submit";
  ariaLabel?: string;
  ariaPressed?: boolean;
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      title={title}
      disabled={disabled}
      aria-label={ariaLabel}
      aria-pressed={ariaPressed}
      className={cx(
        "inline-flex items-center justify-center gap-1 rounded px-2 py-1 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        active ? "bg-accent text-white" : "text-fg hover:bg-surface-3",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function Toggle({ checked, onChange, label, disabled, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean; hint?: string }) {
  return (
    <label className={cx("flex cursor-pointer items-center gap-2 py-0.5 text-xs", disabled && "cursor-not-allowed opacity-50")} title={hint}>
      <input type="checkbox" className="h-3.5 w-3.5 accent-[var(--accent)]" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange, ariaLabel }: { tabs: { id: T; label: string }[]; value: T; onChange: (id: T) => void; ariaLabel: string }) {
  return (
    <div role="tablist" aria-label={ariaLabel} className="scroll-thin flex gap-1 overflow-x-auto border-b border-line px-2">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={value === tab.id}
          onClick={() => onChange(tab.id)}
          className={cx(
            "-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-xs font-medium transition-colors",
            value === tab.id ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg",
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export function Popover({ open, onClose, children, className }: { open: boolean; onClose: () => void; children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div ref={ref} className={cx("absolute z-40 mt-1 rounded-lg border border-line bg-surface p-3 shadow-xl", className)}>
      {children}
    </div>
  );
}

export function Modal({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4" onMouseDown={onClose}>
      <div role="dialog" aria-modal="true" aria-label={title} className="mt-10 w-full max-w-2xl rounded-lg border border-line bg-surface shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <header className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          <Button onClick={onClose} ariaLabel="Close">✕</Button>
        </header>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

export function Stat({ label, value, tone, title }: { label: string; value: ReactNode; tone?: "up" | "down" | "flat"; title?: string }) {
  return (
    <div className="min-w-0" title={title}>
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className={cx("tabular truncate text-sm font-medium", tone === "up" && "text-up", tone === "down" && "text-down")}>{value}</div>
    </div>
  );
}

export function Disclaimer({ children }: { children: ReactNode }) {
  return <p className="mt-3 border-t border-line pt-2 text-[11px] leading-relaxed text-muted">{children}</p>;
}

export function Details({ summary, children }: { summary: string; children: ReactNode }) {
  return (
    <details className="group mt-2 text-xs">
      <summary className="cursor-pointer select-none text-muted hover:text-fg">{summary}</summary>
      <div className="mt-2 space-y-1 text-muted">{children}</div>
    </details>
  );
}
