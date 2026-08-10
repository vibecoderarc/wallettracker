import { api, timeAgo, ApiUnavailable } from "@/lib/api";
import { Empty, ErrorBanner, Stat } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function System() {
  let coverage, proposals, digests;
  try {
    [coverage, proposals, digests] = await Promise.all([
      api.coverage(),
      api.proposals(),
      api.digests(),
    ]);
  } catch (error) {
    return (
      <>
        <h1>System</h1>
        <ErrorBanner message={(error as ApiUnavailable).message} />
      </>
    );
  }

  const latest = digests.digests[0];

  return (
    <>
      <h1>System</h1>
      <p className="sub">Coverage, freshness, and anything the nightly loop wants to change.</p>

      <div className="grid cols-4">
        <Stat label="Run mode" value={coverage.run_mode} note={coverage.notifications_enabled ? "alerts live" : "alerts withheld"} />
        <Stat label="Provider mode" value={coverage.provider_mode} />
        <Stat label="Events" value={coverage.events.toLocaleString()} />
        <Stat label="Freshness" value={timeAgo(coverage.latest_event_observed_at)} />
      </div>

      <h2>Providers</h2>
      {coverage.providers.length === 0 ? (
        <Empty>No provider health samples recorded yet.</Empty>
      ) : (
        <div className="card table-wrap">
          <table>
            <thead>
              <tr><th>Provider</th><th>Status</th><th>Checked</th><th>Detail</th></tr>
            </thead>
            <tbody>
              {coverage.providers.map((p, i) => (
                <tr key={i}>
                  <td>{p.provider}</td>
                  <td>
                    <span className={`tag ${p.healthy ? "good" : "bad"}`}>
                      {p.healthy ? "healthy ●" : "degraded ✕"}
                    </span>
                  </td>
                  <td style={{ color: "var(--muted)" }}>{timeAgo(p.at)}</td>
                  <td style={{ color: "var(--muted)" }}>{p.detail || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Tuning proposals</h2>
      <div className="banner">
        <strong>Nothing here has been applied</strong>
        {proposals.note} An LLM does not tune these — the numbers come from statistical
        code with a point-in-time backtest attached, and you decide.
      </div>
      {proposals.proposals.length === 0 ? (
        <Empty>No proposals. The loop found no change worth the risk of making.</Empty>
      ) : (
        <div className="card table-wrap">
          <table>
            <thead>
              <tr><th>Target</th><th>Change</th><th>Sample</th><th>Variants tested</th><th>Status</th></tr>
            </thead>
            <tbody>
              {proposals.proposals.map((p: any) => (
                <tr key={p.id}>
                  <td className="mono">{p.target}</td>
                  <td>
                    {p.current_value} → <strong>{p.proposed_value}</strong>
                    <div style={{ color: "var(--muted)", fontSize: 12 }}>{p.justification}</div>
                  </td>
                  <td>{p.sample_size}</td>
                  <td>
                    <span className={`tag ${p.variants_tested > 10 ? "warn" : ""}`}>
                      {p.variants_tested}
                    </span>
                  </td>
                  <td><span className="tag">{p.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Latest digest</h2>
      {!latest ? (
        <Empty>No digest yet. Run <span className="mono">alphagraph nightly</span>.</Empty>
      ) : (
        <div className="card">
          <div style={{ color: "var(--muted)", fontSize: 12, marginBottom: 8 }}>
            {new Date(latest.run_date).toUTCString()}
          </div>
          {latest.body?.family_precision ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Signal family</th><th>Precision</th><th>Graded</th></tr>
                </thead>
                <tbody>
                  {Object.entries(latest.body.family_precision as Record<string, any>)
                    .sort((a, b) => b[1].precision - a[1].precision)
                    .map(([family, stats]) => (
                      <tr key={family}>
                        <td>{family.replace(/_/g, " ")}</td>
                        <td>
                          <span className={`tag ${stats.precision > 0.2 ? "good" : "warn"}`}>
                            {(stats.precision * 100).toFixed(1)}%
                          </span>
                        </td>
                        <td style={{ color: "var(--muted)" }}>
                          {stats.hits}/{stats.graded}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div style={{ color: "var(--muted)" }}>No graded signals in this run.</div>
          )}
        </div>
      )}
    </>
  );
}
