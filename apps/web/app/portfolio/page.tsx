import { api, timeAgo, ApiUnavailable } from "@/lib/api";
import { Empty, ErrorBanner } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function Portfolio() {
  let portfolio;
  try {
    portfolio = await api.portfolio();
  } catch (error) {
    return (
      <>
        <h1>Paper Portfolio</h1>
        <ErrorBanner message={(error as ApiUnavailable).message} />
      </>
    );
  }

  return (
    <>
      <h1>Paper Portfolio</h1>
      <div className="banner">
        <strong>Simulated only</strong>
        {portfolio.warning} This application cannot construct, sign, or submit a
        transaction, and holds no keys.
      </div>
      {portfolio.positions.length === 0 ? (
        <Empty>No paper positions yet.</Empty>
      ) : (
        <div className="card table-wrap">
          <table>
            <thead>
              <tr>
                <th>Asset</th><th>Opened</th><th>Size</th><th>Entry</th>
                <th>Fees</th><th>Slippage</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {portfolio.positions.map((p: any) => (
                <tr key={p.id}>
                  <td className="mono">{p.asset_id}</td>
                  <td style={{ color: "var(--muted)" }}>{timeAgo(p.opened_at)}</td>
                  <td>${Math.round(p.size_usd).toLocaleString()}</td>
                  <td>${Number(p.entry_price_usd).toPrecision(4)}</td>
                  <td>${Number(p.fees_usd).toFixed(2)}</td>
                  <td>{p.slippage_bps} bps</td>
                  <td><span className="tag warn">simulated ◇</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
