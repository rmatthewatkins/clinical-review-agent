"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { fetchApi, ReviewDetail } from "@/lib/api";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-5">
      <h3 className="font-semibold text-sm text-[var(--muted)] uppercase tracking-wide mb-2">{title}</h3>
      {children}
    </div>
  );
}

function ItemList({ items, field = "display" }: { items: Array<Record<string, unknown>>; field?: string }) {
  if (!items.length) return <p className="text-sm text-[var(--muted)]">None recorded</p>;
  return (
    <ul className="list-disc list-inside text-sm space-y-0.5">
      {items.map((item, i) => (
        <li key={i}>{String(item[field] ?? "Unknown")}</li>
      ))}
    </ul>
  );
}

export default function ReviewDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<ReviewDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (id) fetchApi<ReviewDetail>(`/api/reviews/${id}`).then(setData).catch((e) => setError(e.message));
  }, [id]);

  if (error) return <p className="text-red-600">Error: {error}</p>;
  if (!data) return <p className="text-[var(--muted)]">Loading...</p>;

  const { context, review } = data;
  const pb = context.patient_baseline;
  const idx = context.index_admission;
  const interval = context.interval_care;
  const ra = context.readmission;

  return (
    <>
      <h1 className="text-2xl font-bold mb-6">Review Detail — Pair #{context.pair_id}</h1>

      <div className="grid grid-cols-2 gap-8">
        {/* Left column — Clinical Context */}
        <div className="space-y-1">
          <h2 className="text-lg font-semibold mb-4">Clinical Context</h2>

          <Section title="Patient Baseline">
            <p className="text-sm">
              {pb.gender ?? "—"}, age {pb.age ?? "—"}, {pb.race ?? "—"} · {pb.city}, {pb.state}
            </p>
          </Section>

          <Section title="Chronic Conditions">
            <ItemList items={pb.chronic_conditions} />
          </Section>

          <Section title="Active Medications">
            <ItemList items={pb.active_medications} />
          </Section>

          <hr className="border-[var(--border)]" />

          <Section title={`Index Admission (${idx.length_of_stay_days ?? "?"} days)`}>
            <p className="text-sm mb-1">
              {idx.start?.slice(0, 10)} → {idx.end?.slice(0, 10)} · Reason: {idx.reason_display ?? "—"}
            </p>
            <details className="text-sm mt-2">
              <summary className="cursor-pointer text-[var(--accent)]">Diagnoses ({idx.diagnoses.length})</summary>
              <ItemList items={idx.diagnoses} />
            </details>
            {idx.procedures.length > 0 && (
              <details className="text-sm mt-2">
                <summary className="cursor-pointer text-[var(--accent)]">Procedures ({idx.procedures.length})</summary>
                <ItemList items={idx.procedures} />
              </details>
            )}
          </Section>

          <hr className="border-[var(--border)]" />

          <Section title={`Interval Care (${interval.encounters.length} encounters)`}>
            {interval.new_medications_started.length > 0 && (
              <details className="text-sm mt-1">
                <summary className="cursor-pointer text-[var(--accent)]">New Medications ({interval.new_medications_started.length})</summary>
                <ItemList items={interval.new_medications_started} />
              </details>
            )}
            {interval.new_conditions_diagnosed.length > 0 && (
              <details className="text-sm mt-1">
                <summary className="cursor-pointer text-[var(--accent)]">New Conditions ({interval.new_conditions_diagnosed.length})</summary>
                <ItemList items={interval.new_conditions_diagnosed} />
              </details>
            )}
            {interval.encounters.length === 0 && interval.new_medications_started.length === 0 && interval.new_conditions_diagnosed.length === 0 && (
              <p className="text-sm text-[var(--muted)]">No interval care recorded</p>
            )}
          </Section>

          <hr className="border-[var(--border)]" />

          <Section title={`Readmission (${context.days_between} days after discharge)`}>
            <p className="text-sm mb-1">
              {ra.start?.slice(0, 10)} · Reason: {ra.reason_display ?? "—"}
            </p>
            <details className="text-sm mt-2">
              <summary className="cursor-pointer text-[var(--accent)]">Diagnoses ({ra.diagnoses.length})</summary>
              <ItemList items={ra.diagnoses} />
            </details>
          </Section>
        </div>

        {/* Right column — AI Review */}
        <div>
          <h2 className="text-lg font-semibold mb-4">AI Review</h2>

          {!review ? (
            <p className="text-[var(--muted)]">No review completed for this pair yet.</p>
          ) : (
            <div className="space-y-4">
              <div className="bg-[var(--card)] border border-[var(--border)] rounded-lg p-5 space-y-3">
                <div className="flex justify-between">
                  <span className="text-sm text-[var(--muted)]">Root Cause</span>
                  <span className="font-medium">{review.structured.root_cause_category ?? "—"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm text-[var(--muted)]">Preventability</span>
                  <span className="font-medium">{review.structured.preventability_score ?? "—"} / 5</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm text-[var(--muted)]">Confidence</span>
                  <span className="font-medium">{review.structured.confidence_level ?? "—"}</span>
                </div>
              </div>

              {review.structured.preventability_rationale && (
                <Section title="Rationale">
                  <p className="text-sm">{review.structured.preventability_rationale}</p>
                </Section>
              )}

              {review.structured.contributing_factors && review.structured.contributing_factors.length > 0 && (
                <Section title="Contributing Factors">
                  <ul className="list-disc list-inside text-sm space-y-0.5">
                    {review.structured.contributing_factors.map((f, i) => (
                      <li key={i}>{f}</li>
                    ))}
                  </ul>
                </Section>
              )}

              {review.structured.recommended_interventions && review.structured.recommended_interventions.length > 0 && (
                <Section title="Recommended Interventions">
                  <ul className="list-disc list-inside text-sm space-y-0.5">
                    {review.structured.recommended_interventions.map((int_, i) => (
                      <li key={i}>{int_}</li>
                    ))}
                  </ul>
                </Section>
              )}

              {review.clinical_narrative && (
                <Section title="Clinical Narrative">
                  <div className="text-sm whitespace-pre-wrap bg-[var(--background)] border border-[var(--border)] rounded p-4">
                    {review.clinical_narrative}
                  </div>
                </Section>
              )}

              <p className="text-xs text-[var(--muted)] pt-4 border-t border-[var(--border)]">
                Model: {review.model_used ?? "—"} · Tokens: {review.tokens_used ?? "—"} · Reviewed: {review.created_at ?? "—"}
              </p>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
