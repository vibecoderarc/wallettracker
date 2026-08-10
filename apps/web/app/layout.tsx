import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AlphaGraph",
  description: "Insider footprint intelligence — research only.",
};

const NAV = [
  ["/", "Today"],
  ["/radar", "Insider Radar"],
  ["/discovery", "Discovery"],
  ["/entities", "Dossiers"],
  ["/backtests", "Backtests"],
  ["/portfolio", "Paper Portfolio"],
  ["/system", "System"],
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <nav className="sidebar" aria-label="Primary">
            <div className="brand">
              AlphaGraph
              <small>footprint intelligence</small>
            </div>
            <div className="nav">
              {NAV.map(([href, label]) => (
                <a key={href} href={href}>
                  {label}
                </a>
              ))}
            </div>
          </nav>
          <main className="main">
            {children}
            <footer className="disclaimer">
              Research tool operating on public blockchain data. Not investment advice,
              and no guarantee of any outcome. Predictive skill and profitability are
              measured separately — a wallet can predict reliably and still lose money.
              Every position is your own decision.
            </footer>
          </main>
        </div>
      </body>
    </html>
  );
}
