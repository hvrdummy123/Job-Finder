import type { JobFilters } from "./api";

export const DEFAULT_FILTERS: JobFilters = {
  include_unspecified_yoe: true,
  tech_only: true,
  sort: "posted",
  limit: 100,
  offset: 0,
};

export function companySlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function filtersFromSearchParams(
  params: URLSearchParams,
  companyFromPath?: string | null,
): JobFilters {
  const locations = params.getAll("location").filter(Boolean);
  const starred = params.get("starred");
  const applied = params.get("applied");
  const companyId = params.getAll("company_id").map(Number).filter((n) => !Number.isNaN(n));

  const filters: JobFilters = {
    ...DEFAULT_FILTERS,
    q: params.get("q") || undefined,
    location: locations.length ? locations : undefined,
    city: params.get("city") || undefined,
    country: params.get("country") || undefined,
    category: params.get("category") || undefined,
    source_portal: params.get("source_portal") || undefined,
    workplace_type: params.get("workplace") ? [params.get("workplace")!] : undefined,
    yoe_min: params.get("yoe_min") ? Number(params.get("yoe_min")) : undefined,
    yoe_max: params.get("yoe_max") ? Number(params.get("yoe_max")) : undefined,
    include_unspecified_yoe: params.get("include_unspecified_yoe") !== "0",
    tech_only: params.get("tech_only") !== "0",
    first_seen_within_hours: params.get("first_seen")
      ? Number(params.get("first_seen"))
      : undefined,
    posted_within_hours: params.get("posted") ? Number(params.get("posted")) : undefined,
    saved_search_id: params.get("saved") ? Number(params.get("saved")) : undefined,
    new_since_visit: params.get("new") === "1" || undefined,
    starred: starred === "1" ? true : starred === "0" ? false : undefined,
    applied: applied === "1" ? true : applied === "0" ? false : undefined,
    active_titles: params.get("active_titles") === "1" || undefined,
    sort: params.get("sort") || "posted",
    company_id: companyId.length ? companyId : undefined,
    company: companyFromPath && companyFromPath !== "all" ? companyFromPath : undefined,
  };
  return filters;
}

export function searchParamsFromFilters(
  filters: JobFilters,
  opts?: { omitCompany?: boolean },
): URLSearchParams {
  const sp = new URLSearchParams();
  const set = (k: string, v: unknown) => {
    if (v === undefined || v === null || v === "" || v === false) return;
    if (v === true) {
      sp.set(k, "1");
      return;
    }
    sp.set(k, String(v));
  };

  set("q", filters.q);
  for (const loc of filters.location || []) sp.append("location", loc);
  // Prefer multi-location; keep legacy only if location empty
  if (!filters.location?.length) {
    set("city", filters.city);
    set("country", filters.country);
  }
  set("category", filters.category);
  set("source_portal", filters.source_portal);
  set("workplace", filters.workplace_type?.[0]);
  set("yoe_min", filters.yoe_min);
  set("yoe_max", filters.yoe_max);
  if (filters.include_unspecified_yoe === false) sp.set("include_unspecified_yoe", "0");
  if (filters.tech_only === false) sp.set("tech_only", "0");
  set("first_seen", filters.first_seen_within_hours);
  set("posted", filters.posted_within_hours);
  set("saved", filters.saved_search_id);
  if (filters.new_since_visit) sp.set("new", "1");
  if (filters.starred) sp.set("starred", "1");
  if (filters.applied) sp.set("applied", "1");
  if (filters.active_titles) sp.set("active_titles", "1");
  if (filters.sort && filters.sort !== "posted") set("sort", filters.sort);
  if (!opts?.omitCompany) {
    for (const id of filters.company_id || []) sp.append("company_id", String(id));
  }
  return sp;
}

export function jobsPath(filters: JobFilters, companySlugName?: string | null): string {
  const slug = companySlugName || filters.company || "all";
  const path = `/${slug}`;
  const sp = searchParamsFromFilters(filters, { omitCompany: true });
  const qs = sp.toString();
  return qs ? `${path}?${qs}` : path;
}
