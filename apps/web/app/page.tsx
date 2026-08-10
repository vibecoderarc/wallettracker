import { api, pct, shortAddr, timeAgo, ApiUnavailable } from "@/lib/api";
import { Empty, ErrorBanner, RiskTag, ShadowBanner, Stat } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function Today() {
  let coverage, signals, candidates;
  try {
    [coverage, signals, candidates] = await Promise.all([
      api.coverage(),
      api.signals(40),
      api.candidates(),
    ]);
  } catch (error) {
    return (
      <>
        <h1>Today</h1>
        <ErrorBanner message={(error as ApiUnavailable).message} />
      </>
    );
  }

  const tracked = candidates.candidates.filter((c) => c.status === "tracked");
  // Signals still awaiting an outcome are the actionable ones; already-graded
  // signals are history and are kept below for context rather than competing
  // for attention at the top of the page.
  const isOpen = (s: (typeof signals.signals)[number]) =>
    !s.grade || s.grade === "pending";
  const ranked = [...signals.signals].sort((a, b) => {
    if (isOpen(a) !== isOpen(b)) return isOpen(a) ? -1 : 1;
    return b.significance - a.significance;
  });
  const open = ranked.filter(isOpen);
  const critical = ranked.filter((s) => s.risk_band === "critical");

  return (
    <>
      <h1>Today</h1>
      <p className="sub">
        What needs attention, ranked by how unusual the action is <em>for that wallet</em> —
        not by dollar size.
      </p>

      <ShadowBanner enabled={coverage.notifications_enabled} />

      <div className="grid cols-4">
        <Stat label="Tracked wallets" value={tracked.length} note="passed shadow period" />
        <Stat
          label="Open signals"
          value={open.length}
          note={`${ranked.length - open.length} already graded`}
        />
        <Stat
          label="Critical risk"
          value={critical.length}
          note={critical.length ? "shown regardless of score" : "none flagged"}
        />
        <Stat
          label="Data freshness"
          value={timeAgo(coverage.latest_event_observed_at)}
          note={`${coverage.events.toLocaleString()} events ingested`}
        />
      </div>

      <h2>Ranked signals</h2>
      {ranked.length === 0 ? (
        <Empty>
          No signals yet. Run <span className="mono">alphagraph demo</span> to populate
          the database from fixtures.
        </Empty>
      ) : (
        <div className="grid" style={{ gap: 10 }}>
          {ranked.slice(0, 20).map((signal) => (
            <article key={signal.id} className="card">
              <div className="spread">
                <div style={{ minWidth: 0 }}>
                  <div className="row">
                    <span className="tag">{signal.family.replace(/_/g, " ")}</span>
                    <RiskTag band={signal.risk_band} />
                    {signal.shadow_mode ? <span className="tag">shadow</span> : null}
                    {signal.grade && signal.grade !== "pending" ? (
                      <span className={`tag ${signal.grade === "hit" ? "good" : "bad"}`}>
                        graded {signal.grade}
                      </span>
                    ) : null}
                  </div>
                  <div style={{ marginTop: 7, fontWeight: 600 }}>{signal.title}</div>
                  <ul className="facts">
                    {signal.supporting_facts.slice(0, 3).map((fact, i) => (
                      <li key={i}>{fact}</li>
                    ))}
                  </ul>
                  {signal.risks.length > 0 && (
                    <ul className="facts" style={{ color: "var(--warn)" }}>
                      {signal.risks.slice(0, 2).map((risk, i) => (
                        <li key={i}>Risk: {risk}</li>
                      ))}
                    </ul>
                  )}
                  {signal.unknowns.length > 0 && (
                    <ul className="facts">
                      {signal.unknowns.slice(0, 2).map((unknown, i) => (
                        <li key={i}>Unknown: {unknown}</li>
                      ))}
                    </ul>
                  )}
                </div>
                <div style={{ textAlign: "right", flexShrink: 0 }}>
                  <div className="stat">
                    <div className="label">significance</div>
                    <div className="value" style={{ fontSize: 19 }}>
                      {pct(signal.significance)}
                    </div>
                    <div className="meter" aria-hidden="true">
                      <span style={{ width: `${Math.round(signal.significance * 100)}%` }} />
                    </div>
                    <div className="note">confidence {pct(signal.confidence)}</div>
                  </div>
                  <div className="note" style={{ marginTop: 8, color: "var(--muted)", fontSize: 11 }}>
                    {timeAgo(signal.triggered_at)}
                  </div>
                  {signal.wallet ? (
                    <a
                      className="mono"
                      href={`/wallet/${signal.wallet}`}
                      style={{ fontSize: 11, color: "var(--accent)" }}
                    >
                      {shortAddr(signal.wallet)}
                    </a>
                  ) : null}
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  );
}
