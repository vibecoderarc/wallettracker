import { api, ApiUnavailable } from "@/lib/api";
import { ArchetypeTag, Empty, ErrorBanner } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function Entities() {
  let entities;
  try {
    entities = await api.entities();
  } catch (error) {
    return (
      <>
        <h1>Dossiers</h1>
        <ErrorBanner message={(error as ApiUnavailable).message} />
      </>
    );
  }

  return (
    <>
      <h1>Dossiers</h1>
      <p className="sub">
        Durable research artifacts, not derived views: your notes, member wallets,
        observed playbook, and track record.
      </p>
      {entities.entities.length === 0 ? (
        <Empty>
          No dossiers yet. Create one from a tracked wallet once you have a hypothesis
          worth writing down.
        </Empty>
      ) : (
        <div className="grid cols-2">
          {entities.entities.map((entity: any) => (
            <article key={entity.id} className="card">
              <div className="row">
                <strong>{entity.display_name}</strong>
                <ArchetypeTag archetype={entity.archetype} />
              </div>
              <div style={{ color: "var(--muted)", fontSize: 12, marginTop: 6 }}>
                {entity.summary || "No summary written yet."}
              </div>
              <div className="row" style={{ marginTop: 10 }}>
                <span className="tag">{entity.member_count} member wallets</span>
                <span className="tag">{entity.status}</span>
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  );
}
