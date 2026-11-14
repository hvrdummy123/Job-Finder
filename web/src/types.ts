export type RefreshProgress = {
  run_id?: number;
  source_id?: number;
  company_id?: number;
  company_name?: string;
  status: string;
  was_baseline?: boolean;
  jobs_found?: number;
  jobs_new?: number;
  jobs_closed?: number;
  error?: string | null;
  duration_ms?: number;
  companies_total?: number;
};
