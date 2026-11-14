import type { Job } from "../api";

type Props = {
  job: Job | null | undefined;
  loading?: boolean;
  onClose: () => void;
  onState: (patch: Partial<Job>) => void;
};

export function JobDrawer({ job, loading, onClose, onState }: Props) {
  if (loading && !job) return <div className="empty">Loading…</div>;
  if (!job) return <div className="empty">Select a job</div>;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem" }}>
        <h2 style={{ textTransform: "none", letterSpacing: 0, color: "var(--ink)", fontSize: "1.1rem" }}>
          {job.title}
        </h2>
        <button className="btn" type="button" onClick={onClose}>
          Close
        </button>
      </div>

      <div className="drawer-section job-meta">
        <div>
          {job.employer_name || job.company_name}
          {job.source_portal ? (
            <span className="badge" style={{ marginLeft: "0.4rem" }}>
              via {job.source_portal}
            </span>
          ) : null}
        </div>
        {!job.employer_name || job.employer_name === job.company_name ? null : (
          <div style={{ color: "var(--muted)" }}>Listed under {job.company_name}</div>
        )}
        <div>
          {job.locations && job.locations.length > 0
            ? job.locations
                .map((l) =>
                  [l.city_normalized || l.city, l.state, l.country].filter(Boolean).join(", ") ||
                  l.raw,
                )
                .filter(Boolean)
                .join(" · ")
            : job.location_raw ||
              [job.city_normalized || job.city, job.state, job.country].filter(Boolean).join(", ") ||
              "—"}
        </div>
        <div>
          {job.workplace_type}
          {job.employment_type ? ` · ${job.employment_type}` : ""}
          {job.seniority ? ` · ${job.seniority}` : ""}
        </div>
        <div>
          YoE:{" "}
          {job.yoe_min == null && job.yoe_max == null
            ? "unspecified"
            : `${job.yoe_min ?? "?"}–${job.yoe_max ?? "?"}`}
        </div>
        <div>
          Posted:{" "}
          {job.posted_at
            ? new Date(job.posted_at).toLocaleString()
            : "unknown (first seen " + new Date(job.first_seen_at).toLocaleString() + ")"}
        </div>
      </div>

      <div className="drawer-section" style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
        {(job.apply_url || job.url) && (
          <a className="btn btn-primary" href={job.apply_url || job.url || "#"} target="_blank" rel="noreferrer">
            Apply
          </a>
        )}
        <button className="btn" type="button" onClick={() => onState({ starred: !job.starred })}>
          {job.starred ? "Unfavorite ★" : "Favorite ★"}
        </button>
        <button className="btn" type="button" onClick={() => onState({ applied: !job.applied })}>
          {job.applied ? "Unmark applied ✓" : "Mark applied ✓"}
        </button>
        <button className="btn" type="button" onClick={() => onState({ hidden: true })}>
          Hide
        </button>
      </div>

      <div className="drawer-section">
        <h2>Description</h2>
        <div className="desc">{job.description_text || "No description captured."}</div>
      </div>
    </div>
  );
}
