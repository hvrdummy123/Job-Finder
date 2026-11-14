import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type RoleTitle } from "../api";

const CATEGORIES = [
  "all",
  "engineering",
  "ai",
  "data",
  "architecture",
  "security",
  "fintech",
  "blockchain",
  "custom",
] as const;

export function TitlesPage() {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["role-titles"],
    queryFn: api.roleTitles,
  });
  const [category, setCategory] = useState<(typeof CATEGORIES)[number]>("all");
  const [q, setQ] = useState("");
  const [customTitle, setCustomTitle] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  const catalog = data?.catalog || [];
  const activeIds = useMemo(
    () => new Set((data?.active || []).map((t) => t.id)),
    [data?.active],
  );

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return catalog.filter((t) => {
      if (category !== "all" && t.category !== category) return false;
      if (!needle) return true;
      return (
        t.title.toLowerCase().includes(needle) ||
        (t.aliases || []).some((a) => a.toLowerCase().includes(needle))
      );
    });
  }, [catalog, category, q]);

  const invalidate = () => void qc.invalidateQueries({ queryKey: ["role-titles"] });

  const saveMut = useMutation({
    mutationFn: (body: { title_ids: number[]; filter_enabled?: boolean }) =>
      api.setActiveRoleTitles(body),
    onSuccess: () => {
      setMessage("Saved active titles");
      invalidate();
      void qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e: Error) => setMessage(e.message),
  });

  const toggleMut = useMutation({
    mutationFn: (enabled: boolean) =>
      api.setActiveRoleTitles({
        title_ids: [...activeIds],
        filter_enabled: enabled,
      }),
    onSuccess: () => {
      invalidate();
      void qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const addCustomMut = useMutation({
    mutationFn: async () => {
      const row = await api.createRoleTitle({ title: customTitle.trim() });
      const next = new Set(activeIds);
      next.add(row.id);
      await api.setActiveRoleTitles({
        title_ids: [...next],
        filter_enabled: data?.filter_enabled,
      });
    },
    onSuccess: () => {
      setCustomTitle("");
      setMessage("Added custom title to catalog + active list");
      invalidate();
    },
    onError: (e: Error) => setMessage(e.message),
  });

  function toggleLocal(id: number) {
    const next = new Set(activeIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    saveMut.mutate({
      title_ids: [...next],
      filter_enabled: data?.filter_enabled,
    });
  }

  if (isLoading) return <div className="titles-page empty">Loading titles…</div>;
  if (isError) return <div className="titles-page empty">Failed to load titles.</div>;

  return (
    <div className="titles-page">
      <div className="titles-header">
        <div>
          <h1 style={{ margin: 0 }}>Role titles</h1>
          <p style={{ color: "var(--muted)", margin: "0.35rem 0 0" }}>
            Pick titles you’re actively looking for. Jobs still ingest broadly; this is an extra
            filter layer.
          </p>
        </div>
        <div className="titles-header-actions">
          <label className="check" style={{ margin: 0 }}>
            <input
              type="checkbox"
              checked={!!data?.filter_enabled}
              onChange={(e) => toggleMut.mutate(e.target.checked)}
            />
            Only show active titles in Jobs
          </label>
          <Link
            className="btn btn-primary"
            to={
              data?.filter_enabled
                ? "/all?active_titles=1"
                : "/all?active_titles=1"
            }
            onClick={() => {
              if (!data?.filter_enabled) toggleMut.mutate(true);
            }}
          >
            View matching jobs
          </Link>
        </div>
      </div>

      {message ? <div className="companies-banner">{message}</div> : null}

      <section className="titles-active">
        <h2>
          Actively looking for{" "}
          <span style={{ color: "var(--muted)", fontWeight: 500 }}>
            ({data?.active_count || 0})
          </span>
        </h2>
        {(data?.active || []).length === 0 ? (
          <p style={{ color: "var(--muted)" }}>
            Nothing selected yet — add titles from the catalog below.
          </p>
        ) : (
          <div className="location-chips">
            {(data?.active || []).map((t) => (
              <button
                type="button"
                className="chip"
                key={t.id}
                onClick={() => toggleLocal(t.id)}
                title="Remove from active"
              >
                {t.title} ×
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="titles-add">
        <h2>Add custom title</h2>
        <div className="location-add">
          <input
            placeholder="e.g. Forward Deployed AI Engineer"
            value={customTitle}
            onChange={(e) => setCustomTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && customTitle.trim()) addCustomMut.mutate();
            }}
          />
          <button
            type="button"
            className="btn btn-primary"
            disabled={!customTitle.trim() || addCustomMut.isPending}
            onClick={() => addCustomMut.mutate()}
          >
            Add
          </button>
        </div>
      </section>

      <div className="companies-toolbar">
        <input
          placeholder="Search catalog…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="filter-chips">
          {CATEGORIES.map((c) => (
            <button
              key={c}
              type="button"
              className={`btn ${category === c ? "btn-primary" : ""}`}
              onClick={() => setCategory(c)}
            >
              {c}
            </button>
          ))}
        </div>
      </div>

      <p style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
        Catalog: {data?.catalog_count || 0} titles · showing {filtered.length}
      </p>

      <div className="titles-grid">
        {filtered.map((t: RoleTitle) => {
          const on = activeIds.has(t.id);
          return (
            <button
              type="button"
              key={t.id}
              className={`title-card ${on ? "on" : ""}`}
              onClick={() => toggleLocal(t.id)}
            >
              <div className="title-card-top">
                <strong>{t.title}</strong>
                <span className="badge">{on ? "active" : t.category}</span>
              </div>
              {(t.aliases || []).length > 0 ? (
                <div className="title-aliases">{(t.aliases || []).join(" · ")}</div>
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}
