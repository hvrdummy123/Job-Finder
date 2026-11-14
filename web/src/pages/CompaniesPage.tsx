import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, type Company } from "../api";

const ATS_OPTIONS = [
  "greenhouse",
  "lever",
  "ashby",
  "workday",
  "amazon",
  "smartrecruiters",
  "oracle",
  "generic",
  "indeed",
  "wellfound",
  "naukri",
  "rocketship",
  "unknown",
];

type Draft = {
  name: string;
  careers_url: string;
  website: string;
  categories: string;
  ats_type: string;
  ats_slug: string;
};

const emptyDraft: Draft = {
  name: "",
  careers_url: "",
  website: "",
  categories: "",
  ats_type: "",
  ats_slug: "",
};

export function CompaniesPage() {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["companies"],
    queryFn: api.companies,
  });
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<"all" | "errors" | "generic" | "zero" | "portals">("all");
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [editing, setEditing] = useState<Company | null>(null);
  const [editForm, setEditForm] = useState<Draft>(emptyDraft);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const companies = data || [];

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return companies.filter((c) => {
      if (filter === "errors" && !c.last_error) return false;
      if (filter === "generic" && c.ats_type !== "generic" && c.ats_type !== "unknown") return false;
      if (filter === "zero" && (c.open_jobs ?? 0) > 0) return false;
      if (filter === "portals") {
        const portal = ["indeed", "wellfound", "naukri", "rocketship"].includes(c.ats_type || "");
        if (!portal) return false;
      }
      if (!needle) return true;
      return (
        c.name.toLowerCase().includes(needle) ||
        (c.careers_url || "").toLowerCase().includes(needle) ||
        (c.ats_type || "").toLowerCase().includes(needle) ||
        (c.last_error || "").toLowerCase().includes(needle)
      );
    });
  }, [companies, q, filter]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["companies"] });
    void qc.invalidateQueries({ queryKey: ["health"] });
    void qc.invalidateQueries({ queryKey: ["jobs"] });
  };

  const createMut = useMutation({
    mutationFn: () =>
      api.createCompany({
        name: draft.name.trim(),
        careers_url: draft.careers_url.trim(),
        website: draft.website.trim() || undefined,
        categories: draft.categories
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        ats_type: draft.ats_type || undefined,
        ats_slug: draft.ats_slug || undefined,
        refresh: true,
      }),
    onSuccess: (res) => {
      setDraft(emptyDraft);
      setMessage(`Added ${res.name} · ATS ${res.source?.ats_type || "?"} · refresh queued`);
      invalidate();
    },
    onError: (err: Error) => setMessage(err.message),
  });

  const fixMut = useMutation({
    mutationFn: () =>
      api.fixSources({
        redetect: true,
        clear_stale_errors: true,
        purge_junk: true,
        refresh: true,
      }),
    onSuccess: (res) => {
      setMessage(
        `Fixed sources · purged ${res.purged_junk_jobs} junk · cleared ${res.cleared_stale_errors} stale errors · cleared ${res.cleared_overrides ?? 0} overrides · redetected ${res.redetected} · refresh queued ${res.refresh_queued}`,
      );
      invalidate();
    },
    onError: (err: Error) => setMessage(err.message),
  });

  async function runAction(id: number, fn: () => Promise<unknown>, label: string) {
    setBusyId(id);
    setMessage(null);
    try {
      await fn();
      setMessage(label);
      invalidate();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  function startEdit(c: Company) {
    setEditing(c);
    setEditForm({
      name: c.name,
      careers_url: c.careers_url,
      website: c.website || "",
      categories: (c.categories || []).join(", "),
      ats_type: c.ats_type || "",
      ats_slug: c.ats_slug || "",
    });
  }

  async function saveEdit() {
    if (!editing) return;
    setBusyId(editing.id);
    try {
      await api.patchCompany(editing.id, {
        name: editForm.name.trim(),
        careers_url: editForm.careers_url.trim(),
        website: editForm.website.trim() || null,
        categories: editForm.categories
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        ats_type: editForm.ats_type || undefined,
        ats_slug: editForm.ats_slug || null,
        ats_override: Boolean(editForm.ats_type),
      });
      setEditing(null);
      setMessage(`Updated ${editForm.name}`);
      invalidate();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  if (isLoading) return <div className="companies-page empty">Loading companies…</div>;
  if (isError) return <div className="companies-page empty">Failed to load companies.</div>;

  const errorCount = companies.filter((c) => c.last_error).length;
  const genericCount = companies.filter((c) => c.ats_type === "generic" || c.ats_type === "unknown").length;

  return (
    <div className="companies-page">
      <div className="companies-header">
        <div>
          <h1 style={{ margin: 0 }}>Companies</h1>
          <p style={{ color: "var(--muted)", margin: "0.35rem 0 0" }}>
            {companies.length} tracked · {errorCount} with errors · {genericCount} generic/unknown
          </p>
        </div>
        <div className="companies-header-actions">
          <button
            className="btn btn-primary"
            disabled={fixMut.isPending}
            onClick={() => fixMut.mutate()}
            title="Purge junk jobs, clear stale DB errors, redetect broken ATS, refresh"
          >
            {fixMut.isPending ? "Fixing…" : "Fix broken sources"}
          </button>
        </div>
      </div>

      {message ? <div className="companies-banner">{message}</div> : null}

      <section className="companies-add">
        <h2>Add company</h2>
        <p style={{ color: "var(--muted)", marginTop: 0 }}>
          Paste a careers URL (or <code>portal://indeed/…</code>, Wellfound, Naukri, Rocketship). ATS is
          auto-detected unless you override; jobs refresh starts immediately.
        </p>
        <div className="companies-form">
          <input
            placeholder="Company name"
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
          <input
            placeholder="Careers URL"
            value={draft.careers_url}
            onChange={(e) => setDraft({ ...draft, careers_url: e.target.value })}
          />
          <input
            placeholder="Website (optional)"
            value={draft.website}
            onChange={(e) => setDraft({ ...draft, website: e.target.value })}
          />
          <input
            placeholder="Categories (eng, remote, …)"
            value={draft.categories}
            onChange={(e) => setDraft({ ...draft, categories: e.target.value })}
          />
          <select
            value={draft.ats_type}
            onChange={(e) => setDraft({ ...draft, ats_type: e.target.value })}
          >
            <option value="">Auto-detect ATS</option>
            {ATS_OPTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          <input
            placeholder="ATS slug / board id (optional)"
            value={draft.ats_slug}
            onChange={(e) => setDraft({ ...draft, ats_slug: e.target.value })}
          />
          <button
            className="btn btn-primary"
            disabled={createMut.isPending || !draft.name.trim() || !draft.careers_url.trim()}
            onClick={() => createMut.mutate()}
          >
            {createMut.isPending ? "Adding…" : "Add & fetch jobs"}
          </button>
        </div>
      </section>

      <div className="companies-toolbar">
        <input
          placeholder="Search name, URL, ATS, error…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="filter-chips">
          {(
            [
              ["all", "All"],
              ["zero", "Zero jobs"],
              ["errors", "Errors"],
              ["generic", "Generic"],
              ["portals", "Portals"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              className={`btn ${filter === id ? "btn-primary" : ""}`}
              onClick={() => setFilter(id)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <table className="health-table companies-table">
        <thead>
          <tr>
            <th>Company</th>
            <th>ATS</th>
            <th>Open</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((c) => (
            <tr key={c.id} className={c.last_error ? "row-error" : undefined}>
              <td>
                <div style={{ fontWeight: 600 }}>{c.name}</div>
                <div style={{ color: "var(--muted)", fontSize: "0.8rem", wordBreak: "break-all" }}>
                  {c.careers_url}
                </div>
                {(c.categories || []).length > 0 ? (
                  <div style={{ marginTop: "0.25rem" }}>
                    {(c.categories || []).map((cat) => (
                      <span className="badge" key={cat} style={{ marginRight: 4 }}>
                        {cat}
                      </span>
                    ))}
                  </div>
                ) : null}
              </td>
              <td>
                <code>{c.ats_type || "—"}</code>
                {c.ats_override ? <span className="badge warn">override</span> : null}
                {c.ats_slug ? (
                  <div style={{ fontSize: "0.8rem", color: "var(--muted)" }}>{c.ats_slug}</div>
                ) : null}
              </td>
              <td>{c.open_jobs ?? 0}</td>
              <td>
                {c.baseline_completed ? (
                  <span className="badge">baseline</span>
                ) : (
                  <span className="badge warn">pending</span>
                )}
                {c.needs_playwright ? <span className="badge warn">playwright</span> : null}
                {c.last_error ? (
                  <div style={{ color: "var(--danger)", fontSize: "0.8rem", marginTop: 4 }}>
                    {c.last_error}
                  </div>
                ) : (
                  <div style={{ color: "var(--muted)", fontSize: "0.8rem", marginTop: 4 }}>
                    {c.last_success_at
                      ? `ok ${new Date(c.last_success_at).toLocaleString()}`
                      : "never succeeded"}
                  </div>
                )}
              </td>
              <td>
                <div className="row-actions">
                  <button
                    className="btn"
                    disabled={busyId === c.id}
                    onClick={() =>
                      runAction(c.id, () => api.refreshCompany(c.id), `Refreshing ${c.name}`)
                    }
                  >
                    Refresh
                  </button>
                  <button
                    className="btn"
                    disabled={busyId === c.id || Boolean(c.ats_override)}
                    title={c.ats_override ? "Clear ATS override first" : "Re-detect ATS"}
                    onClick={() =>
                      runAction(c.id, () => api.detectCompany(c.id), `Redetected ${c.name}`)
                    }
                  >
                    Redetect
                  </button>
                  <button className="btn" disabled={busyId === c.id} onClick={() => startEdit(c)}>
                    Edit
                  </button>
                  <button
                    className="btn"
                    disabled={busyId === c.id}
                    onClick={() => {
                      if (!confirm(`Remove ${c.name}? This deletes its jobs.`)) return;
                      void runAction(c.id, () => api.deleteCompany(c.id), `Removed ${c.name}`);
                    }}
                  >
                    Remove
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {editing ? (
        <div className="modal-backdrop" onClick={() => setEditing(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2 style={{ marginTop: 0 }}>Edit {editing.name}</h2>
            <div className="companies-form stacked">
              <label>
                Name
                <input
                  value={editForm.name}
                  onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                />
              </label>
              <label>
                Careers URL
                <input
                  value={editForm.careers_url}
                  onChange={(e) => setEditForm({ ...editForm, careers_url: e.target.value })}
                />
              </label>
              <label>
                Website
                <input
                  value={editForm.website}
                  onChange={(e) => setEditForm({ ...editForm, website: e.target.value })}
                />
              </label>
              <label>
                Categories
                <input
                  value={editForm.categories}
                  onChange={(e) => setEditForm({ ...editForm, categories: e.target.value })}
                />
              </label>
              <label>
                ATS type (sets override)
                <select
                  value={editForm.ats_type}
                  onChange={(e) => setEditForm({ ...editForm, ats_type: e.target.value })}
                >
                  <option value="">Keep / auto</option>
                  {ATS_OPTIONS.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                ATS slug
                <input
                  value={editForm.ats_slug}
                  onChange={(e) => setEditForm({ ...editForm, ats_slug: e.target.value })}
                />
              </label>
              <div className="row-actions">
                <button
                  className="btn"
                  onClick={() =>
                    void runAction(
                      editing.id,
                      () => api.patchCompany(editing.id, { ats_override: false }),
                      `Cleared override for ${editing.name}`,
                    ).then(() => setEditing(null))
                  }
                >
                  Clear override
                </button>
                <button className="btn" onClick={() => setEditing(null)}>
                  Cancel
                </button>
                <button className="btn btn-primary" disabled={busyId === editing.id} onClick={() => void saveEdit()}>
                  Save
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
