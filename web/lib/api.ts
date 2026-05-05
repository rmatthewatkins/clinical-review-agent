const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function fetchApi<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API ${path}: ${res.status}`);
  return res.json();
}

export async function postApi<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "POST", cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`API ${path}: ${res.status} ${body}`);
  }
  return res.json();
}

export interface JobSubmitResult {
  job_id: string;
  status: "pending";
  case_type?: string;
  case_id?: number;
  readmission_id?: number;
}

export interface JobStatus {
  job_id: string;
  case_type?: string;
  case_id?: number;
  readmission_id?: number;
  status: "pending" | "running" | "completed" | "failed";
  result: {
    root_cause_category: string | null;
    preventability_score: number | null;
    tokens_used: number;
  } | null;
  error: string | null;
}

// --- Pagination wrapper ---

export interface PaginatedResponse<T> {
  total: number;
  offset: number;
  limit: number;
  items: T[];
}

// --- Types ---

export interface Summary {
  patients: number;
  encounters: number;
  readmissions: number;
  mortality_cases: number;
  reviews: number;
  cases: { readmission: number; mortality: number };
  reviews_by_type: { readmission: number; mortality: number };
  root_cause_distribution: Record<string, { count: number; percentage: number }>;
  preventability_score_distribution: Record<string, number>;
  mean_preventability_by_diagnosis: Record<string, number>;
}

export interface ReviewRow {
  case_type?: string;
  case_id?: number;
  readmission_id: number;
  age: number | null;
  gender: string | null;
  index_diagnosis: string;
  root_cause: string;
  preventability_score: number | null;
  confidence: string | null;
  reviewed_at: string | null;
}

export interface ReviewDetail {
  context: {
    readmission_id: number;
    days_between: number;
    patient_baseline: {
      patient_id: string;
      age: number | null;
      gender: string | null;
      race: string | null;
      ethnicity: string | null;
      city: string | null;
      state: string | null;
      chronic_conditions: Array<{ display: string; onset: string | null }>;
      active_medications: Array<{ display: string }>;
      relevant_observations: Array<{ display: string; value: string; unit: string; date: string }>;
    };
    index_admission: {
      encounter_id: string;
      start: string | null;
      end: string | null;
      length_of_stay_days: number | null;
      reason_display: string | null;
      diagnoses: Array<{ display: string }>;
      procedures: Array<{ display: string }>;
      medications: Array<{ display: string }>;
      observations: Array<{ display: string; value: string; unit: string; date: string }>;
    };
    interval_care: {
      encounters: Array<{ type: string; start: string }>;
      new_medications_started: Array<{ display: string }>;
      stopped_medications: Array<{ display: string }>;
      new_conditions_diagnosed: Array<{ display: string }>;
    };
    readmission: {
      encounter_id: string;
      start: string | null;
      end: string | null;
      reason_display: string | null;
      diagnoses: Array<{ display: string }>;
      observations: Array<{ display: string; value: string; unit: string; date: string }>;
    };
  };
  review: {
    structured: {
      root_cause_category?: string;
      preventability_score?: number;
      preventability_rationale?: string;
      contributing_factors?: string[];
      recommended_interventions?: string[];
      confidence_level?: string;
    };
    clinical_narrative: string | null;
    model_used: string | null;
    tokens_used: number | null;
    created_at: string | null;
  } | null;
}

export interface Readmission {
  readmission_id: number;
  patient_id: string;
  age: number | null;
  gender: string | null;
  race: string | null;
  days_between: number;
  index_start: string | null;
  index_end: string | null;
  index_diagnosis: string;
  discharge_disposition: string | null;
  readmit_start: string | null;
  readmit_end: string | null;
  readmit_diagnosis: string;
  review_status: "reviewed" | "pending";
  root_cause: string | null;
  preventability_score: number | null;
  reviewed_at: string | null;
}

export interface MortalityCase {
  case_id: number;
  encounter_id: string;
  patient_id: string;
  death_date: string | null;
  lookback_days: number;
  age: number | null;
  gender: string | null;
  race: string | null;
  city: string | null;
  state: string | null;
  encounter_start: string | null;
  encounter_end: string | null;
  diagnosis: string;
  discharge_disposition: string | null;
  review_status: "reviewed" | "pending";
  root_cause: string | null;
  preventability_score: number | null;
  reviewed_at: string | null;
}

export interface Analytics {
  contributing_factors: Array<{ factor: string; count: number }>;
  recommended_interventions: Array<{ intervention: string; count: number }>;
}

export interface RootCauseRow {
  category: string;
  count: number;
  readmissions: Array<{ readmission_id: number; preventability_score: number | null }>;
}

export interface DiagnosisRow {
  diagnosis: string;
  count: number;
  readmissions: Array<{ readmission_id: number; root_cause: string | null; preventability_score: number | null }>;
}

export interface GenderRow {
  gender: string;
  count: number;
  readmissions: Array<{ readmission_id: number; root_cause: string | null; preventability_score: number | null }>;
}

export interface AgeGroupRow {
  age_group: string;
  count: number;
  readmissions: Array<{ readmission_id: number; age: number | null; days_between: number }>;
}

export interface DaysBetweenRow {
  bucket: string;
  count: number;
  readmissions: Array<{ readmission_id: number; days_between: number }>;
}
