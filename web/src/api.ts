export type Job = {
  id: number;
  company_id: number;
  company_name: string;
  title: string;
  url?: string | null;
  apply_url?: string | null;
  location_raw?: string | null;
  city?: string | null;
  city_normalized?: string | null;
  state?: string | null;
  country?: string | null;
  locations?: Array<{
    raw?: string | null;
    city?: string | null;
    state?: string | null;
    country?: string | null;
    city_normalized?: string | null;
  }>;
  workplace_type: string;
  employment_type?: string | null;
  department?: string | null;
  seniority?: string | null;
  yoe_min?: number | null;
  yoe_max?: number | null;
  posted_at?: string | null;
  first_seen_at: string;
  status: string;
  is_baseline: boolean;
  skills?: string[];
  starred: boolean;
  hidden: boolean;
  applied: boolean;
  is_new_since_visit?: boolean;
  description_text?: string | null;
  description_html?: string | null;
  notes?: string | null;
  source_portal?: string | null;
  employer_name?: string | null;
  company_categories?: string[];
};

export type JobListResponse = {
  total: number;
  limit: number;
  offset: number;
  items: Job[];
};

export type Company = {
  id: number;
  name: string;
  careers_url: string;
  website?: string | null;
  notes?: string | null;
  categories?: string[];
  ats_type?: string;
  ats_slug?: string | null;
  ats_override?: boolean;
  baseline_completed?: boolean;
  needs_playwright?: boolean;
  fallback_stage?: string;
  last_success_at?: string | null;
  last_checked_at?: string | null;
  last_error?: string | null;
  poll_interval_seconds?: number;
  open_jobs?: number;
  source_id?: number;
  failure_count?: number;
  circuit_open_until?: string | null;
  source?: {
    id: number;
    ats_type: string;
    ats_slug?: string | null;
  };
  refresh_run_id?: number | null;
};

export type SourceHealth = {
  source_id: number;
  company_id: number;
  company_name: string;
  careers_url: string;
  ats_type: string;
  ats_slug?: string | null;
  ats_override?: boolean;
  fallback_stage?: string;
  needs_playwright?: boolean;
  baseline_completed?: boolean;
  baseline_completed_at?: string | null;
  poll_interval_seconds?: number;
  next_poll_at?: string | null;
  last_checked_at?: string | null;
  last_success_at?: string | null;
  failure_count?: number;
  last_error?: string | null;
  circuit_open_until?: string | null;
  open_jobs?: number;
};

export type SavedSearch = {
  id: number;
  name: string;
  include_terms: string[];
  exclude_terms: string[];
  locations: string[];
  yoe_min?: number | null;
  yoe_max?: number | null;
  include_unspecified_yoe: boolean;
  workplace_types: string[];
  is_active: boolean;
  match_count?: number;
};

export type JobFilters = {
  q?: string;
  company_id?: number[];
  /** Company slug from URL path, e.g. amazon */
  company?: string;
  workplace_type?: string[];
  yoe_min?: number;
  yoe_max?: number;
  include_unspecified_yoe?: boolean;
  first_seen_within_hours?: number;
  posted_within_hours?: number;
  /** Multi-location filter terms (India, Bangalore, Karnataka, …) */
  location?: string[];
  city?: string;
  country?: string;
  saved_search_id?: number;
  new_since_visit?: boolean;
  starred?: boolean;
  applied?: boolean;
  category?: string;
  source_portal?: string;
  tech_only?: boolean;
  /** Extra layer: only titles marked active on the Titles page */
  active_titles?: boolean;
  sort?: string;
  limit?: number;
  offset?: number;
};

export type RoleTitle = {
  id: number;
  title: string;
  category: string;
  aliases: string[];
  sort_order?: number;
  active: boolean;
};

export type RoleTitlesResponse = {
  filter_enabled: boolean;
  catalog: RoleTitle[];
  active: RoleTitle[];
  active_count: number;
  catalog_count: number;
};

function qs(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) {
      for (const item of v) sp.append(k, String(item));
    } else {
      sp.set(k, String(v));
    }
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  jobs: (filters: JobFilters) =>
    request<JobListResponse>(`/api/jobs${qs(filters as Record<string, unknown>)}`),
  job: (id: number) => request<Job>(`/api/jobs/${id}`),
  patchJobState: (id: number, body: Partial<Job>) =>
    request(`/api/jobs/${id}/state`, { method: "PATCH", body: JSON.stringify(body) }),
  companies: () => request<Company[]>("/api/companies"),
  createCompany: (body: {
    name: string;
    careers_url: string;
    website?: string;
    notes?: string;
    categories?: string[];
    ats_type?: string;
    ats_slug?: string;
    refresh?: boolean;
  }) => request<Company>("/api/companies", { method: "POST", body: JSON.stringify(body) }),
  patchCompany: (
    id: number,
    body: Partial<{
      name: string;
      careers_url: string;
      website: string | null;
      notes: string | null;
      categories: string[];
      ats_type: string;
      ats_slug: string | null;
      ats_override: boolean;
    }>,
  ) => request<Company>(`/api/companies/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteCompany: (id: number) =>
    request<{ status: string }>(`/api/companies/${id}`, { method: "DELETE" }),
  detectCompany: (id: number) => request<Company>(`/api/companies/${id}/detect`, { method: "POST" }),
  fixSources: (body?: {
    redetect?: boolean;
    clear_stale_errors?: boolean;
    purge_junk?: boolean;
    refresh?: boolean;
    company_ids?: number[];
  }) =>
    request<{
      purged_junk_jobs: number;
      cleared_stale_errors: number;
      cleared_overrides?: number;
      redetected: number;
      refresh_queued: number;
      company_ids: number[];
    }>("/api/companies/fix-sources", {
      method: "POST",
      body: JSON.stringify(body || {}),
    }),
  purgeJunk: () => request<{ purged: number }>("/api/companies/purge-junk", { method: "POST" }),
  sourcesHealth: () => request<SourceHealth[]>("/api/sources/health"),
  savedSearches: () => request<SavedSearch[]>("/api/saved-searches"),
  createSavedSearch: (body: Partial<SavedSearch>) =>
    request<SavedSearch>("/api/saved-searches", { method: "POST", body: JSON.stringify(body) }),
  refresh: () => request<{ run_id: number }>("/api/refresh", { method: "POST" }),
  refreshCompany: (companyId: number) =>
    request<{ run_id: number; company_id: number }>(`/api/refresh/company/${companyId}`, {
      method: "POST",
    }),
  touchVisit: () => request("/api/visits", { method: "POST" }),
  roleTitles: () => request<RoleTitlesResponse>("/api/role-titles"),
  setActiveRoleTitles: (body: { title_ids: number[]; filter_enabled?: boolean }) =>
    request<RoleTitlesResponse>("/api/role-titles/active", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  createRoleTitle: (body: { title: string; category?: string; aliases?: string[] }) =>
    request<RoleTitle>("/api/role-titles/catalog", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
