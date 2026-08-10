/**
 * API client.
 *
 * Every fetch is uncached: this dashboard reports on live coverage and freshness,
 * and a stale cached page that looks current is worse than a slow one.
 */

const BASE = process.env.ALPHAGRAPH_API_URL ?? "http://127.0.0.1:8000";

/**
 * Read WITHOUT the NEXT_PUBLIC_ prefix, deliberately.
 *
 * Next.js inlines NEXT_PUBLIC_ variables into the client bundle, so naming the
 * token that way would ship it to every visitor's browser — the exact leak the
 * API token exists to prevent. Every page here is a server component, so the
 * fetch runs server-side and the token never crosses to the client.
 */
const TOKEN = process.env.ALPHAGRAPH_API_TOKEN ?? "";

export class ApiUnavailable extends Error {}

async function get<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      cache: "no-store",
      headers: TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {},
    });
  } catch {
    throw new ApiUnavailable(
      `Cannot reach the AlphaGraph API at ${BASE}. Start it with \`alphagraph serve\`.`,
    );
  }
  if (response.status === 401 || response.status === 403) {
    throw new ApiUnavailable(
      "API rejected the token. Check ALPHAGRAPH_API_TOKEN matches the API service.",
    );
  }
  if (!response.ok) {
    throw new ApiUnavailable(`${path} returned ${response.status}`);
  }
  return (await response.json()) as T;
}

export interface Signal {
  id: number;
  family: string;
  triggered_at: string;
  title: string;
  significance: number;
  confidence: number;
  risk_band: string;
  asset_id: string | null;
  wallet: string | null;
  supporting_facts: string[];
  risks: string[];
  unknowns: string[];
  base_rate_context: Record<string, number>;
  grade: string | null;
  shadow_mode: boolean;
}

export interface Candidate {
  id: number;
  wallet: string;
  status: string;
  archetype: string;
  hit_rate: number;
  shrunk_hit_rate: number;
  base_rate: number;
  sample_size: number;
  independent_events: number;
  edge_vs_base: number;
  surfaced_at: string;
  rejection_reason: string | null;
  discovery_provenance: {
    outcome_id: string;
    asset_id: string;
    outcome_class: string;
    t0: string;
    entered_at: string;
    lead_time_days: number;
  }[];
}

export interface Coverage {
  run_mode: string;
  provider_mode: string;
  notifications_enabled: boolean;
  events: number;
  assets: number;
  outcomes: number;
  latest_event_observed_at: string | null;
  providers: { provider: string; healthy: boolean; at: string; detail: string }[];
  disclaimer: string;
}

export const api = {
  coverage: () => get<Coverage>("/v1/system/coverage"),
  baseRates: () =>
    get<{ as_of: string; base_rates: Record<string, { rate: number; hits: number; total_assets: number }> }>(
      "/v1/system/base-rates",
    ),
  signals: (limit = 50) => get<{ signals: Signal[] }>(`/v1/signals?limit=${limit}`),
  signal: (id: number) => get<Signal & { evidence: Record<string, unknown>[] }>(`/v1/signals/${id}`),
  candidates: (status?: string) =>
    get<{ candidates: Candidate[] }>(`/v1/candidates${status ? `?status=${status}` : ""}`),
  wallet: (address: string) =>
    get<Record<string, any>>(`/v1/wallets/${encodeURIComponent(address)}`),
  asset: (id: string) => get<Record<string, any>>(`/v1/assets/${encodeURIComponent(id)}`),
  entities: () => get<{ entities: Record<string, any>[] }>("/v1/entities"),
  entity: (id: string) => get<Record<string, any>>(`/v1/entities/${encodeURIComponent(id)}`),
  proposals: () => get<{ proposals: Record<string, any>[]; note: string }>("/v1/proposals"),
  digests: () => get<{ digests: { run_date: string; body: any }[] }>("/v1/digests"),
  portfolio: () => get<{ positions: Record<string, any>[]; warning: string }>("/v1/paper/portfolio"),
};

export function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export function shortAddr(address: string | null | undefined, keep = 6): string {
  if (!address) return "—";
  const body = address.includes(":") ? address.split(":")[1] : address;
  return body.length <= keep * 2 + 1 ? body : `${body.slice(0, keep)}…${body.slice(-keep)}`;
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  const mins = Math.round((Date.now() - then) / 60000);
  if (!Number.isFinite(mins)) return "—";
  if (Math.abs(mins) < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (Math.abs(hours) < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}
