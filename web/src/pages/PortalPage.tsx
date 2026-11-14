import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type Job, type JobFilters } from "../api";
import { Filters } from "../components/Filters";
import { JobDrawer } from "../components/JobDrawer";
import {
  DEFAULT_FILTERS,
  filtersFromSearchParams,
  jobsPath,
} from "../urlFilters";

export function PortalPage() {
  const { companySlug: pathSlug } = useParams<{ companySlug?: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const parentRef = useRef<HTMLDivElement>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const filters = useMemo(
    () => filtersFromSearchParams(searchParams, pathSlug || "all"),
    [searchParams, pathSlug],
  );

  function updateFilters(next: JobFilters) {
    navigate(jobsPath(next, next.company || "all"), { replace: true });
  }

  const jobsQuery = useQuery({
    queryKey: ["jobs", filters],
    queryFn: () => api.jobs(filters),
  });

  const detailQuery = useQuery({
    queryKey: ["job", selectedId],
    queryFn: () => api.job(selectedId!),
    enabled: selectedId != null,
  });

  const stateMut = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Partial<Job> }) =>
      api.patchJobState(id, patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["jobs"] });
      void qc.invalidateQueries({ queryKey: ["job", selectedId] });
    },
  });

  // Keep drawer selection in URL as ?job=id (optional convenience)
  useEffect(() => {
    const jobParam = searchParams.get("job");
    if (jobParam) setSelectedId(Number(jobParam));
  }, [searchParams]);

  const items = jobsQuery.data?.items ?? [];
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 78,
    overscan: 8,
  });

  const selected = useMemo(
    () => items.find((j) => j.id === selectedId) || detailQuery.data || null,
    [items, selectedId, detailQuery.data],
  );

  const scopeLabel =
    pathSlug && pathSlug !== "all"
      ? pathSlug.replace(/-/g, " ")
      : "all companies";

  return (
    <div className={`layout ${selectedId ? "with-drawer" : ""}`}>
      <aside className="sidebar">
        <Filters value={filters} onChange={updateFilters} />
      </aside>

      <section className="main">
        <div className="list-header">
          <div>
            <strong>{jobsQuery.data?.total ?? "—"}</strong> jobs
            <span style={{ color: "var(--muted)", marginLeft: "0.5rem" }}>· {scopeLabel}</span>
          </div>
          <div style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
            {(filters.location || []).length
              ? `Locations: ${(filters.location || []).join(", ")}`
              : filters.q
                ? `Search: ${filters.q}`
                : "Sorted by " + (filters.sort || "newest")}
            {filters.starred || filters.applied
              ? ` · ${[filters.starred ? "favorited" : null, filters.applied ? "applied" : null].filter(Boolean).join(" + ")}`
              : ""}
          </div>
        </div>

        <div className="job-list" ref={parentRef}>
          {jobsQuery.isLoading && <div className="empty">Loading jobs…</div>}
          {jobsQuery.isError && <div className="empty">Failed to load jobs.</div>}
          {!jobsQuery.isLoading && items.length === 0 && (
            <div className="empty">No jobs match these filters. Add companies and hit Refresh.</div>
          )}

          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualizer.getVirtualItems().map((vRow) => {
              const job = items[vRow.index];
              return (
                <div
                  key={job.id}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    transform: `translateY(${vRow.start}px)`,
                  }}
                >
                  <button
                    type="button"
                    className={`job-row ${selectedId === job.id ? "active" : ""}`}
                    onClick={() => setSelectedId(job.id)}
                  >
                    <div>
                      <div className="job-title">
                        {job.title}{" "}
                        {job.is_new_since_visit && <span className="badge">new</span>}
                        {job.starred ? <span className="badge">★</span> : null}
                        {job.applied ? <span className="badge">✓</span> : null}
                      </div>
                      <div className="job-meta">
                        <span>{job.employer_name || job.company_name}</span>
                        {job.source_portal ? <span className="badge">via {job.source_portal}</span> : null}
                        <span>
                          {formatLocations(job)}
                          {job.workplace_type !== "unknown" ? ` · ${job.workplace_type}` : ""}
                        </span>
                        <span>{formatYoe(job.yoe_min, job.yoe_max)}</span>
                        <span title={job.posted_at ? "Posted date" : "First seen (posted date unknown)"}>
                          {formatDate(job.posted_at || job.first_seen_at)}
                          {!job.posted_at ? " · seen" : ""}
                        </span>
                      </div>
                    </div>
                    <div className="row-actions" onClick={(e) => e.stopPropagation()}>
                      {(job.apply_url || job.url) && (
                        <a
                          className="btn btn-primary"
                          style={{ padding: "0.2rem 0.55rem", fontSize: "0.8rem" }}
                          href={job.apply_url || job.url || "#"}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(e) => e.stopPropagation()}
                        >
                          Apply
                        </a>
                      )}
                      <button
                        type="button"
                        className={`icon-btn ${job.starred ? "on" : ""}`}
                        title="Favorite"
                        onClick={() =>
                          stateMut.mutate({ id: job.id, patch: { starred: !job.starred } })
                        }
                      >
                        ★
                      </button>
                      <button
                        type="button"
                        className={`icon-btn ${job.applied ? "on" : ""}`}
                        title="Applied"
                        onClick={() =>
                          stateMut.mutate({ id: job.id, patch: { applied: !job.applied } })
                        }
                      >
                        ✓
                      </button>
                      <button
                        type="button"
                        className="icon-btn"
                        title="Hide"
                        onClick={() =>
                          stateMut.mutate({ id: job.id, patch: { hidden: true } })
                        }
                      >
                        ✕
                      </button>
                    </div>
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {selectedId && (
        <aside className="drawer">
          <JobDrawer
            job={detailQuery.data || selected}
            loading={detailQuery.isLoading}
            onClose={() => setSelectedId(null)}
            onState={(patch) => stateMut.mutate({ id: selectedId, patch })}
          />
        </aside>
      )}
    </div>
  );
}

/** Redirect bare `/` → `/all` keeping query string. */
export function HomeRedirect() {
  const [params] = useSearchParams();
  const qs = params.toString();
  return <Navigate to={qs ? `/all?${qs}` : "/all"} replace />;
}

export function PortalAllPage() {
  return <PortalPage />;
}

export function portalDefaults(): JobFilters {
  return { ...DEFAULT_FILTERS };
}

function formatLocations(job: Job): string {
  if (job.locations && job.locations.length > 1) {
    return job.locations
      .map((l) => l.city_normalized || l.city || l.raw)
      .filter(Boolean)
      .join(" · ");
  }
  const city = job.city_normalized || job.city;
  const country = job.country;
  if (city && country) return `${city}, ${country}`;
  return city || country || job.location_raw || "—";
}

function formatYoe(min?: number | null, max?: number | null): string {
  if (min == null && max == null) return "YoE n/a";
  if (min != null && max != null) return `${min}–${max} yrs`;
  if (min != null) return `${min}+ yrs`;
  return `≤${max} yrs`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
