import { api, pct, shortAddr, timeAgo, ApiUnavailable } from "@/lib/api";
import { ArchetypeTag, Empty, ErrorBanner, EdgeVsBase } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function Radar() {
  let candidates;
  try {
    candidates = await api.candidates();
  } catch (error) {
    return (
      <>
        <h1>Insider Radar</h1>
        <ErrorBanner message={(error as ApiUnavailable).message} />
      </>
    );
  }

  const tracked = candidates.candidates
    .filter((c) => ["tracked", "shadow_watched"].includes(c.status))
    .sort((a, b) => b.edge_vs_base - a.edge_vs_base);

  return (
    <>
      <h1>Insider Radar</h1>
      <p className="sub">
        Wallets that keep showing up early and quiet across unrelated outcomes. Hit rate
        is always shown against the population base rate — a number on its own means
        nothing.
      </p>

      {tracked.length === 0 ? (
        <Empty>No wallets tracked yet. Check Discovery for pending candidates.</Empty>
      ) : (
        <div className="card table-wrap">
          <table>
            <thead>
              <tr>
                <th>Wallet</th>
                <th>Archetype</th>
                <th>Status</th>
                <th>Predictive record</th>
                <th>Surfaced</th>
              </tr>
            </thead>
            <tbody>
              {tracked.map((candidate) => (
                <tr key={candidate.id}>
                  <td>
                    <a className="mono" href={`/wallet/${candidate.wallet}`}>
                      {shortAddr(candidate.wallet, 8)}
                    </a>
                  </td>
                  <td>
                    <ArchetypeTag archetype={candidate.archetype} />
                  </td>
                  <td>
                    <span className={`tag ${candidate.status === "tracked" ? "good" : ""}`}>
                      {candidate.status === "tracked" ? "tracked ●" : "shadow ◐"}
                    </span>
                  </td>
                  <td>
                    <EdgeVsBase
                      hitRate={candidate.hit_rate}
                      baseRate={candidate.base_rate}
                      edge={candidate.edge_vs_base}
                      sample={candidate.sample_size}
                      independent={candidate.independent_events}
                    />
                  </td>
                  <td style={{ color: "var(--muted)", fontSize: 12 }}>
                    {timeAgo(candidate.surfaced_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="banner" style={{ marginTop: 20 }}>
        <strong>Shrunk hit rate, not raw</strong>
        Raw hit rate flatters small samples — 3-for-3 reads as 100%. The shrunk figure
        pulls toward the population base rate until there is enough evidence to justify
        moving away from it.
      </div>
    </>
  );
}
