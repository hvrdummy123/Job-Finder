import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

export function HealthPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["health"],
    queryFn: api.sourcesHealth,
  });

  if (isLoading) return <div className="health-page empty">Loading source health…</div>;
  if (isError) return <div className="health-page empty">Failed to load source health.</div>;

  const rows = data || [];
  const needsPw = rows.filter((r) => r.needs_playwright);

  return (
    <div className="health-page">
      <h1 style={{ marginTop: 0 }}>Source health</h1>
      <p style={{ color: "var(--muted)" }}>
        {rows.length} companies · {needsPw.length} need Playwright / are on degraded fallback
      </p>

      {needsPw.length > 0 && (
        <div style={{ marginBottom: "1rem" }}>
          <span className="badge warn">Needs attention</span>
          <span style={{ marginLeft: "0.5rem", color: "var(--muted)" }}>
            Install Playwright extras or override ATS for:{" "}
            {needsPw.map((r) => r.company_name).join(", ")}
          </span>
        </div>
      )}

      <table className="health-table">
        <thead>
          <tr>
            <th>Company</th>
            <th>ATS</th>
            <th>Fallback</th>
            <th>Baseline</th>
            <th>Open jobs</th>
            <th>Interval</th>
            <th>Last success</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.source_id}>
              <td>
                <div style={{ fontWeight: 600 }}>{r.company_name}</div>
                <div style={{ color: "var(--muted)", fontSize: "0.8rem" }}>{r.careers_url}</div>
              </td>
              <td>
                <code>{r.ats_type}</code>
                {r.ats_slug ? <div style={{ fontSize: "0.8rem" }}>{r.ats_slug}</div> : null}
              </td>
              <td>
                {r.fallback_stage}
                {r.needs_playwright ? (
                  <>
                    {" "}
                    <span className="badge warn">playwright</span>
                  </>
                ) : null}
              </td>
              <td>{r.baseline_completed ? "done" : "pending"}</td>
              <td>{r.open_jobs ?? "—"}</td>
              <td>{r.poll_interval_seconds ? `${Math.round(r.poll_interval_seconds / 60)}m` : "—"}</td>
              <td>{r.last_success_at ? new Date(r.last_success_at).toLocaleString() : "—"}</td>
              <td>
                {r.circuit_open_until ? <span className="badge danger">circuit</span> : null}{" "}
                {r.last_error || "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
