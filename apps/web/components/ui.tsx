import { pct } from "@/lib/api";

export function Stat({
  label,
  value,
  note,
}: {
  label: string;
  value: string | number;
  note?: string;
}) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {note ? <div className="note">{note}</div> : null}
    </div>
  );
}

/**
 * Risk is shown as a word plus a symbol, never colour alone — a colour-blind
 * user or a greyscale screenshot must still read the severity.
 */
export function RiskTag({ band }: { band: string }) {
  const map: Record<string, [string, string]> = {
    critical: ["critical", "critical ⚠"],
    elevated: ["warn", "elevated ▲"],
    normal: ["good", "normal ●"],
    unknown: ["", "unknown ?"],
  };
  const [cls, label] = map[band] ?? ["", `${band} ?`];
  return <span className={`tag ${cls}`}>{label}</span>;
}

export function ArchetypeTag({ archetype }: { archetype: string }) {
  const cls =
    archetype === "pump_operator"
      ? "bad"
      : archetype === "unclassified"
        ? ""
        : "good";
  return <span className={`tag ${cls}`}>{archetype.replace(/_/g, " ")}</span>;
}

/**
 * An edge ratio means nothing without the base rate beside it. 8% looks
 * impressive until you learn the population rate is 6.8%.
 */
export function EdgeVsBase({
  hitRate,
  baseRate,
  edge,
  sample,
  independent,
}: {
  hitRate: number;
  baseRate: number;
  edge: number;
  sample: number;
  independent: number;
}) {
  return (
    <div>
      <div className="row">
        <strong>{pct(hitRate)}</strong>
        <span className="tag">base {pct(baseRate)}</span>
        <span className={`tag ${edge >= 2 ? "good" : "warn"}`}>{edge.toFixed(1)}× base</span>
      </div>
      <div className="note" style={{ color: "var(--muted)", fontSize: 11, marginTop: 3 }}>
        {sample} assets touched · {independent} independent events
      </div>
    </div>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="banner error" role="alert">
      <strong>Cannot load data</strong>
      {message}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ShadowBanner({ enabled }: { enabled: boolean }) {
  if (enabled) return null;
  return (
    <div className="banner">
      <strong>Shadow mode — no notifications are being sent</strong>
      Signals are computed and stored but nothing leaves this machine. Enable live
      alerts only after reviewing shadow output.
    </div>
  );
}
