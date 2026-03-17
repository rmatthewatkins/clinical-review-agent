const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function fetchApi<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API ${path}: ${res.status}`);
  return res.json();
}

// --- Types ---

export interface Summary {
  patients: number;
  encounters: number;
  pairs: number;
  reviews: number;
  root_cause_distribution: Record<string, { count: number; percentage: number }>;
  preventability_score_distribution: Record<string, number>;
  mean_preventability_by_diagnosis: Record<string, number>;
}

export interface ReviewRow {
  pair_id: number;
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
    pair_id: number;
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

export interface Analytics {
  contributing_factors: Array<{ factor: string; count: number }>;
  recommended_interventions: Array<{ intervention: string; count: number }>;
}
