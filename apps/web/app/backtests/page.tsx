import { Empty } from "@/components/ui";

export const dynamic = "force-dynamic";

export default function Backtests() {
  return (
    <>
      <h1>Backtests</h1>
      <p className="sub">
        Point-in-time simulation with a strict clock, pessimistic execution costs, and an
        automated look-ahead audit on every run.
      </p>
      <div className="banner">
        <strong>Run from the CLI</strong>
        Backtests are long-running jobs, so they are driven by{" "}
        <span className="mono">alphagraph demo</span> rather than a web request that would
        time out. Results are written to the <span className="mono">backtest_runs</span>{" "}
        table with their baseline comparison and leakage audit attached.
      </div>
      <Empty>
        No browser-triggered runs yet — this screen reads stored runs once the job queue
        lands.
      </Empty>
    </>
  );
}
