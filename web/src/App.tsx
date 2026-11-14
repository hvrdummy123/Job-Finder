import { NavLink, Route, Routes } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "./api";
import { HomeRedirect, PortalPage } from "./pages/PortalPage";
import { HealthPage } from "./pages/HealthPage";
import { CompaniesPage } from "./pages/CompaniesPage";
import { TitlesPage } from "./pages/TitlesPage";
import { useEventSource } from "./hooks/useEventSource";
import type { RefreshProgress } from "./types";

export default function App() {
  const qc = useQueryClient();
  const [runId, setRunId] = useState<number | null>(null);
  const [progress, setProgress] = useState<Record<string, RefreshProgress>>({});
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    void api.touchVisit();
  }, []);

  useEventSource("/api/events", {
    refresh_progress: (raw) => {
      const data = raw as RefreshProgress;
      if (data.status === "run_started") {
        setRunId(data.run_id ?? null);
        setProgress({});
        setRefreshing(true);
      }
      if (data.company_name) {
        setProgress((prev) => ({
          ...prev,
          [String(data.source_id)]: data,
        }));
      }
      if (data.status === "refresh_complete") {
        setRefreshing(false);
        void qc.invalidateQueries({ queryKey: ["jobs"] });
        void qc.invalidateQueries({ queryKey: ["health"] });
        void qc.invalidateQueries({ queryKey: ["companies"] });
      }
    },
    job_new: () => {
      void qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    saved_search_reindexed: () => {
      void qc.invalidateQueries({ queryKey: ["saved-searches"] });
    },
  });

  const refreshMut = useMutation({
    mutationFn: api.refresh,
    onSuccess: (res) => {
      setRunId(res.run_id);
      setRefreshing(true);
      setProgress({});
    },
  });

  const progressItems = Object.values(progress);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          Job <span>Radar</span>
        </div>
        <nav>
          <NavLink to="/all" end>
            Jobs
          </NavLink>
          <NavLink to={{ pathname: "/all", search: "?starred=1&applied=1" }}>Saved</NavLink>
          <NavLink to="/titles">Titles</NavLink>
          <NavLink to="/companies">Companies</NavLink>
          <NavLink to="/health">Source health</NavLink>
        </nav>
        <div className="topbar-actions">
          {runId && refreshing && (
            <span style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
              Run #{runId} · {progressItems.filter((p) => p.status === "done" || p.status === "error").length}
              /{progressItems.length || "…"}
            </span>
          )}
          <button
            className="btn btn-primary"
            disabled={refreshMut.isPending}
            onClick={() => refreshMut.mutate()}
            title="Re-runs a full parallel refresh (does not block single-company refresh)"
          >
            {refreshing ? "Refreshing all…" : "Refresh all"}
          </button>
        </div>
      </header>

      <Routes>
        <Route path="/" element={<HomeRedirect />} />
        <Route path="/all" element={<PortalPage />} />
        <Route path="/titles" element={<TitlesPage />} />
        <Route path="/companies" element={<CompaniesPage />} />
        <Route path="/health" element={<HealthPage />} />
        <Route path="/:companySlug" element={<PortalPage />} />
      </Routes>

      {refreshing && progressItems.length > 0 && (
        <div className="progress-panel">
          <h3>Refresh progress</h3>
          {progressItems.map((p) => (
            <div className="progress-item" key={p.source_id}>
              <span>{p.company_name}</span>
              <span>
                {p.status}
                {p.was_baseline ? " · baseline" : ""}
                {p.jobs_new ? ` · +${p.jobs_new}` : ""}
                {p.error ? ` · ${p.error}` : ""}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
