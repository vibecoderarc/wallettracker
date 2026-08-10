import { api, pct, shortAddr, ApiUnavailable } from "@/lib/api";
import { ArchetypeTag, Empty, ErrorBanner, EdgeVsBase } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function Discovery() {
  let candidates, baseRates;
  try {
    [candidates, baseRates] = await Promise.all([api.candidates(), api.baseRates()]);
  } catch (error) {
    return (
      <>
        <h1>Discovery</h1>
        <ErrorBanner message={(error as ApiUnavailable).message} />
      </>
    );
  }

  const pending = candidates.candidates
    .filter((c) => !["tracked", "retired"].includes(c.status))
    .sort((a, b) => b.edge_vs_base - a.edge_vs_base);
  const rejected = candidates.candidates.filter((c) => c.rejection_reason);

  return (
    <>
      <h1>Discovery</h1>
      <p className="sub">
        The reverse sweep: take assets that pumped, got listed, or woke from dormancy;
        rewind before the move; find who was buying during the quiet.
      </p>

      <h2>Population base rates</h2>
      <div className="grid cols-4">
        {Object.entries(baseRates.base_rates).map(([cls, stats]) => (
          <div key={cls} className="card stat">
            <div className="label">{cls.replace(/_/g, " ")}</div>
            <div className="value" style={{ fontSize: 20 }}>{pct(stats.rate)}</div>
            <div className="note">
              {stats.hits} of {stats.total_assets} assets
            </div>
          </div>
        ))}
      </div>

      <h2>Candidates awaiting review</h2>
      {pending.length === 0 ? (
        <Empty>No candidates pending. Every surfaced wallet has been promoted or retired.</Empty>
      ) : (
        <div className="grid" style={{ gap: 10 }}>
          {pending.map((candidate) => (
            <article key={candidate.id} className="card">
              <div className="spread">
                <div style={{ minWidth: 0 }}>
                  <div className="row">
                    <a className="mono" href={`/wallet/${candidate.wallet}`}>
                      {shortAddr(candidate.wallet, 8)}
                    </a>
                    <ArchetypeTag archetype={candidate.archetype} />
                    <span className="tag">{candidate.status.replace(/_/g, " ")}</span>
                  </div>
                  <div style={{ marginTop: 10 }}>
                    <EdgeVsBase
                      hitRate={candidate.hit_rate}
                      baseRate={candidate.base_rate}
                      edge={candidate.edge_vs_base}
                      sample={candidate.sample_size}
                      independent={candidate.independent_events}
                    />
                  </div>
                  <details style={{ marginTop: 10 }}>
                    <summary style={{ cursor: "pointer", fontSize: 12, color: "var(--muted)" }}>
                      Why this wallet surfaced ({candidate.discovery_provenance.length} outcomes)
                    </summary>
                    <ul className="facts">
                      {candidate.discovery_provenance.slice(0, 8).map((p, i) => (
                        <li key={i}>
                          {p.outcome_class.replace(/_/g, " ")} on{" "}
                          <span className="mono">{shortAddr(p.asset_id)}</span> — entered{" "}
                          {p.lead_time_days.toFixed(1)} days before the move
                        </li>
                      ))}
                    </ul>
                  </details>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}

      <h2>Rejected by the guards</h2>
      <p className="sub">
        Kept visible on purpose. A discovery engine that never rejects anything is a
        random number generator.
      </p>
      {rejected.length === 0 ? (
        <Empty>Nothing rejected in the last sweep.</Empty>
      ) : (
        <div className="card table-wrap">
          <table>
            <thead>
              <tr>
                <th>Wallet</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {rejected.slice(0, 25).map((candidate) => (
                <tr key={candidate.id}>
                  <td className="mono">{shortAddr(candidate.wallet, 8)}</td>
                  <td style={{ color: "var(--muted)" }}>{candidate.rejection_reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
