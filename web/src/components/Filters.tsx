import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type JobFilters } from "../api";
import { companySlug } from "../urlFilters";

type Props = {
  value: JobFilters;
  onChange: (next: JobFilters) => void;
};

export function Filters({ value, onChange }: Props) {
  const qc = useQueryClient();
  const companies = useQuery({ queryKey: ["companies"], queryFn: api.companies });
  const searches = useQuery({ queryKey: ["saved-searches"], queryFn: api.savedSearches });
  const [locationDraft, setLocationDraft] = useState("");

  const set = (patch: Partial<JobFilters>) => onChange({ ...value, ...patch, offset: 0 });

  const refreshOne = useMutation({
    mutationFn: (companyId: number) => api.refreshCompany(companyId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["jobs"] });
      void qc.invalidateQueries({ queryKey: ["health"] });
    },
  });

  const selectedCompany = useMemo(() => {
    const list = companies.data || [];
    if (value.company) {
      return list.find((c) => companySlug(c.name) === value.company) || null;
    }
    if (value.company_id?.[0]) {
      return list.find((c) => c.id === value.company_id![0]) || null;
    }
    return null;
  }, [companies.data, value.company, value.company_id]);

  const locations = value.location || [];

  function addLocation(raw: string) {
    const parts = raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (!parts.length) return;
    const next = [...locations];
    for (const p of parts) {
      if (!next.some((x) => x.toLowerCase() === p.toLowerCase())) next.push(p);
    }
    set({ location: next, city: undefined, country: undefined });
    setLocationDraft("");
  }

  function removeLocation(term: string) {
    const next = locations.filter((x) => x !== term);
    set({ location: next.length ? next : undefined });
  }

  return (
    <div className="filters">
      <h2>Filters</h2>

      <div className="field">
        <label htmlFor="q">Search</label>
        <input
          id="q"
          placeholder="title, skills…"
          value={value.q || ""}
          onChange={(e) =>
            set({
              q: e.target.value || undefined,
              sort: e.target.value ? "relevance" : "posted",
            })
          }
        />
      </div>

      <fieldset className="filter-fieldset">
        <legend>My jobs</legend>
        <label className="check">
          <input
            type="checkbox"
            checked={!!value.starred}
            onChange={(e) => set({ starred: e.target.checked || undefined })}
          />
          Favorited ★
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={!!value.applied}
            onChange={(e) => set({ applied: e.target.checked || undefined })}
          />
          Applied ✓
        </label>
        <p className="field-hint">Select both to show favorited or applied.</p>
      </fieldset>

      <fieldset className="filter-fieldset">
        <legend>Active titles</legend>
        <label className="check">
          <input
            type="checkbox"
            checked={!!value.active_titles}
            onChange={(e) => set({ active_titles: e.target.checked || undefined })}
          />
          Only titles I’m actively looking for
        </label>
        <p className="field-hint">
          Manage the list on the <Link to="/titles">Titles</Link> page, then enable this filter.
        </p>
      </fieldset>

      <label className="check">
        <input
          type="checkbox"
          checked={value.tech_only !== false}
          onChange={(e) => set({ tech_only: e.target.checked })}
        />
        Tech / quant / fintech roles only
      </label>

      <div className="field">
        <label htmlFor="category">Category</label>
        <select
          id="category"
          value={value.category || ""}
          onChange={(e) => set({ category: e.target.value || undefined })}
        >
          <option value="">All</option>
          <option value="engineering">Engineering companies</option>
          <option value="blockchain">Blockchain</option>
          <option value="fintech">Fintech / banks</option>
          <option value="remote">Remote-friendly</option>
          <option value="portal">Job portals (Indeed / Wellfound / Naukri / Rocketship)</option>
        </select>
      </div>

      <div className="field">
        <label htmlFor="portal">Found via portal</label>
        <select
          id="portal"
          value={value.source_portal || ""}
          onChange={(e) => set({ source_portal: e.target.value || undefined })}
        >
          <option value="">Any source</option>
          <option value="indeed">Indeed</option>
          <option value="wellfound">Wellfound</option>
          <option value="naukri">Naukri</option>
          <option value="rocketship">Rocketship</option>
        </select>
      </div>

      <div className="field">
        <label htmlFor="when">First seen</label>
        <select
          id="when"
          value={value.first_seen_within_hours ?? ""}
          onChange={(e) =>
            set({
              first_seen_within_hours: e.target.value ? Number(e.target.value) : undefined,
            })
          }
        >
          <option value="">Any time</option>
          <option value="24">Last 24h</option>
          <option value="72">Last 3 days</option>
          <option value="168">Last 7 days</option>
          <option value="720">Last 30 days</option>
        </select>
      </div>

      <div className="field">
        <label htmlFor="posted">Posted</label>
        <select
          id="posted"
          value={value.posted_within_hours ?? ""}
          onChange={(e) =>
            set({
              posted_within_hours: e.target.value ? Number(e.target.value) : undefined,
            })
          }
        >
          <option value="">Any time</option>
          <option value="24">Last 24h</option>
          <option value="72">Last 3 days</option>
          <option value="168">Last 7 days</option>
          <option value="720">Last 30 days</option>
        </select>
      </div>

      <div className="field">
        <label>Experience (years)</label>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.4rem" }}>
          <input
            type="number"
            min={0}
            placeholder="min"
            value={value.yoe_min ?? ""}
            onChange={(e) =>
              set({ yoe_min: e.target.value === "" ? undefined : Number(e.target.value) })
            }
          />
          <input
            type="number"
            min={0}
            placeholder="max"
            value={value.yoe_max ?? ""}
            onChange={(e) =>
              set({ yoe_max: e.target.value === "" ? undefined : Number(e.target.value) })
            }
          />
        </div>
      </div>

      <label className="check">
        <input
          type="checkbox"
          checked={value.include_unspecified_yoe !== false}
          onChange={(e) => set({ include_unspecified_yoe: e.target.checked })}
        />
        Include unspecified experience
      </label>

      <div className="field">
        <label htmlFor="location">Locations</label>
        <p className="field-hint">
          Add multiple: India, Bangalore, Karnataka, US… Country matches its cities.
        </p>
        <div className="location-chips">
          {locations.map((loc) => (
            <button
              type="button"
              className="chip"
              key={loc}
              onClick={() => removeLocation(loc)}
              title="Remove"
            >
              {loc} ×
            </button>
          ))}
        </div>
        <div className="location-add">
          <input
            id="location"
            value={locationDraft}
            placeholder="e.g. India or Bengaluru"
            onChange={(e) => setLocationDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === ",") {
                e.preventDefault();
                addLocation(locationDraft);
              }
            }}
          />
          <button type="button" className="btn" onClick={() => addLocation(locationDraft)}>
            Add
          </button>
        </div>
      </div>

      <div className="field">
        <label htmlFor="workplace">Workplace</label>
        <select
          id="workplace"
          value={value.workplace_type?.[0] || ""}
          onChange={(e) =>
            set({ workplace_type: e.target.value ? [e.target.value] : undefined })
          }
        >
          <option value="">Any</option>
          <option value="remote">Remote</option>
          <option value="hybrid">Hybrid</option>
          <option value="onsite">Onsite</option>
        </select>
      </div>

      <div className="field">
        <label htmlFor="company">Company</label>
        <select
          id="company"
          value={selectedCompany?.id || ""}
          onChange={(e) => {
            const id = e.target.value ? Number(e.target.value) : undefined;
            const c = (companies.data || []).find((x) => x.id === id);
            set({
              company_id: undefined,
              company: c ? companySlug(c.name) : undefined,
            });
          }}
        >
          <option value="">All companies (/all)</option>
          {(companies.data || []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        {selectedCompany ? (
          <button
            type="button"
            className="btn btn-primary"
            style={{ marginTop: "0.45rem" }}
            disabled={refreshOne.isPending}
            onClick={() => refreshOne.mutate(selectedCompany.id)}
          >
            {refreshOne.isPending ? "Refreshing company…" : "Refresh this company now"}
          </button>
        ) : null}
      </div>

      <div className="field">
        <label htmlFor="saved">Saved search</label>
        <select
          id="saved"
          value={value.saved_search_id || ""}
          onChange={(e) =>
            set({
              saved_search_id: e.target.value ? Number(e.target.value) : undefined,
            })
          }
        >
          <option value="">None</option>
          {(searches.data || []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} ({s.match_count ?? 0})
            </option>
          ))}
        </select>
      </div>

      <label className="check">
        <input
          type="checkbox"
          checked={!!value.new_since_visit}
          onChange={(e) => set({ new_since_visit: e.target.checked || undefined })}
        />
        New since last visit
      </label>

      <div className="field">
        <label htmlFor="sort">Sort</label>
        <select
          id="sort"
          value={value.sort || "newest"}
          onChange={(e) => set({ sort: e.target.value })}
        >
          <option value="posted">Newest posted</option>
          <option value="newest">Newest first seen</option>
          <option value="company">Company name</option>
          <option value="relevance">Relevance</option>
          <option value="experience">Experience</option>
        </select>
      </div>
    </div>
  );
}
