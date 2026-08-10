import { api, pct, shortAddr, timeAgo, ApiUnavailable } from "@/lib/api";
import { ArchetypeTag, Empty, ErrorBanner } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function WalletPage({
  params,
}: {
  params: Promise<{ address: string }>;
}) {
  const { address } = await params;
  let profile;
  try {
    profile = await api.wallet(decodeURIComponent(address));
  } catch (error) {
    return (
      <>
        <h1>Wallet</h1>
        <ErrorBanner message={(error as ApiUnavailable).message} />
      </>
    );
  }

  const m = profile.metrics ?? {};
  const candidate = profile.candidate;

  return (
    <>
      <h1 className="mono">{shortAddr(profile.wallet, 10)}</h1>
      <p className="sub">
        <ArchetypeTag archetype={m.archetype ?? "unclassified"} />{" "}
        {m.excluded_reason ? (
          <span className="tag bad">excluded: {m.excluded_reason}</span>
        ) : null}
      </p>

      <div className="grid cols-4">
        <div className="card stat">
          <div className="label">Trades / month</div>
          <div className="value">{(m.trades_per_month ?? 0).toFixed(1)}</div>
          <div className="note">lower is more interesting</div>
        </div>
        <div className="card stat">
          <div className="label">Distinct assets</div>
          <div className="value">{m.distinct_tokens ?? 0}</div>
          <div className="note">sample size for every rate below</div>
        </div>
        <div className="card stat">
          <div className="label">Median lead time</div>
          <div className="value">
            {m.median_lead_time_days ? `${m.median_lead_time_days.toFixed(0)}d` : "—"}
          </div>
          <div className="note">entry to the move becoming public</div>
        </div>
        <div className="card stat">
          <div className="label">Realized P&amp;L</div>
          <div className="value" style={{ fontSize: 19 }}>
            ${Math.round(m.realized_pnl_usd ?? 0).toLocaleString()}
          </div>
          <div className="note">context only — never used for ranking</div>
        </div>
      </div>

      <div className="banner" style={{ marginTop: 16 }}>
        <strong>Predictive skill and profitability are separate</strong>
        {profile.profitability_note}
      </div>

      <h2>Hit rate by outcome class</h2>
      <div className="card table-wrap">
        <table>
          <thead>
            <tr>
              <th>Outcome</th>
              <th>Hits</th>
              <th>Raw rate</th>
              <th>Shrunk</th>
              <th>Base rate</th>
              <th>Edge</th>
            </tr>
          </thead>
          <tbody>
            {Object.keys(m.hit_rate ?? {}).map((cls) => (
              <tr key={cls}>
                <td>{cls.replace(/_/g, " ")}</td>
                <td>{m.hits?.[cls] ?? 0}</td>
                <td>{pct(m.hit_rate?.[cls])}</td>
                <td>
                  <strong>{pct(m.shrunk_hit_rate?.[cls])}</strong>
                </td>
                <td style={{ color: "var(--muted)" }}>{pct(m.base_rate?.[cls])}</td>
                <td>
                  <span className={`tag ${(m.edge_ratio?.[cls] ?? 0) >= 2 ? "good" : ""}`}>
                    {(m.edge_ratio?.[cls] ?? 0).toFixed(1)}×
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Recent activity</h2>
      {(profile.timeline ?? []).length === 0 ? (
        <Empty>No recorded activity.</Empty>
      ) : (
        <div className="card table-wrap">
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Action</th>
                <th>Asset</th>
                <th>Size</th>
              </tr>
            </thead>
            <tbody>
              {profile.timeline.slice(0, 30).map((e: any) => (
                <tr key={e.event_id}>
                  <td style={{ color: "var(--muted)" }}>{timeAgo(e.observed_at)}</td>
                  <td>
                    {e.event_type.replace(/_/g, " ")}
                    {e.filled === false ? (
                      <span className="tag warn" style={{ marginLeft: 6 }}>
                        unfilled ○
                      </span>
                    ) : null}
                  </td>
                  <td className="mono">{shortAddr(e.asset_id)}</td>
                  <td>{e.usd_value ? `$${Math.round(e.usd_value).toLocaleString()}` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
