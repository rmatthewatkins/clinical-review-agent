"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { fetchApi, postApi, ReviewDetail, RunReviewResult } from "@/lib/api";

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`bg-[var(--card)] border border-[var(--border)] rounded-lg shadow-sm p-5 ${className}`}>
      {children}
    </div>
  );
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="font-semibold text-xs text-[var(--muted)] uppercase tracking-wide mb-2">{children}</h3>
  );
}

function ItemList({ items, field = "display" }: { items: Array<Record<string, unknown>>; field?: string }) {
  if (!items.length) return <p className="text-sm text-[var(--muted)]">None recorded</p>;
  return (
    <ul className="text-sm space-y-1">
      {items.map((item, i) => (
        <li key={i} className="flex items-start gap-2">
          <span className="text-[var(--muted)] mt-1.5 shrink-0 block h-1.5 w-1.5 rounded-full bg-[var(--border)]" />
          {String(item[field] ?? "Unknown")}
        </li>
      ))}
    </ul>
  );
}

function ScoreBadge({ score }: { score: number | undefined }) {
  if (score == null) return <span className="text-[var(--muted)]">—</span>;
  const bg =
    score <= 2
      ? "bg-emerald-100 text-emerald-800 border-emerald-200"
      : score === 3
        ? "bg-amber-100 text-amber-800 border-amber-200"
        : "bg-red-100 text-red-800 border-red-200";
  return (
    <span className={`inline-flex items-center rounded-lg border px-3 py-1 text-lg font-bold ${bg}`}>
      {score}/5
    </span>
  );
}

function ConfidenceBadge({ level }: { level: string | undefined }) {
  if (!level) return <span className="text-[var(--muted)]">—</span>;
  const bg =
    level.toLowerCase() === "high"
      ? "bg-emerald-50 text-emerald-700"
      : level.toLowerCase() === "medium"
        ? "bg-amber-50 text-amber-700"
        : "bg-slate-100 text-slate-600";
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${bg}`}>
      {level}
    </span>
  );
}

export default function ReviewDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<ReviewDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reviewing, setReviewing] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

  const loadData = () => {
    if (!id) return;
    fetchApi<ReviewDetail>(`/api/reviews/${id}`)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadData(); }, [id]);

  const handleRunReview = async () => {
    if (!id) return;
    setReviewing(true);
    setReviewError(null);
    try {
      await postApi<RunReviewResult>(`/api/reviews/${id}/run`);
      // Reload the page data to show the new review
      setLoading(true);
      loadData();
    } catch (e: unknown) {
      setReviewError(e instanceof Error ? e.message : "Review failed");
    } finally {
      setReviewing(false);
    }
  };

  if (error) return <p className="text-red-600 p-8">Error: {error}</p>;

  if (loading)
    return (
      <div className="py-16 text-center">
        <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
        <p className="mt-3 text-sm text-[var(--muted)]">Loading review...</p>
      </div>
    );

  if (!data) return <p className="text-[var(--muted)] p-8">Review not found.</p>;

  const { context, review } = data;
  const pb = context.patient_baseline;
  const idx = context.index_admission;
  const interval = context.interval_care;
  const ra = context.readmission;

  return (
    <>
      {/* Header */}
      <div className="mb-6">
        <Link
          href="/reviews"
          className="text-sm text-[var(--accent)] hover:underline inline-flex items-center gap-1 mb-3"
        >
          &larr; Back to Reviews
        </Link>
        <h1 className="text-2xl font-bold">Review — Pair #{context.pair_id}</h1>
        <p className="text-sm text-[var(--muted)] mt-1">
          {pb.gender ?? "—"}, age {pb.age ?? "—"} &middot; Readmitted {context.days_between} days after discharge
        </p>
      </div>

      {/* Top summary cards (review highlights) */}
      {review && (
        <div className="grid grid-cols-3 gap-4 mb-6">
          <Card>
            <SectionHeading>Root Cause</SectionHeading>
            <p className="text-base font-semibold">{review.structured.root_cause_category ?? "—"}</p>
          </Card>
          <Card>
            <SectionHeading>Preventability Score</SectionHeading>
            <ScoreBadge score={review.structured.preventability_score} />
          </Card>
          <Card>
            <SectionHeading>Confidence</SectionHeading>
            <ConfidenceBadge level={review.structured.confidence_level} />
          </Card>
        </div>
      )}

      <div className="grid grid-cols-2 gap-8">
        {/* Left column — Clinical Context */}
        <div className="space-y-4">
          <h2 className="text-lg font-semibold">Clinical Context</h2>

          <Card>
            <SectionHeading>Patient Baseline</SectionHeading>
            <p className="text-sm mb-3">
              {pb.race ?? "—"} &middot; {pb.city}, {pb.state}
            </p>

            <SectionHeading>Chronic Conditions</SectionHeading>
            <ItemList items={pb.chronic_conditions} />
          </Card>

          <Card>
            <SectionHeading>Active Medications</SectionHeading>
            <ItemList items={pb.active_medications} />
          </Card>

          <Card>
            <SectionHeading>
              Index Admission ({idx.length_of_stay_days ?? "?"} days)
            </SectionHeading>
            <p className="text-sm mb-2">
              {idx.start?.slice(0, 10)} &rarr; {idx.end?.slice(0, 10)} &middot; Reason: {idx.reason_display ?? "—"}
            </p>
            {idx.diagnoses.length > 0 && (
              <details className="text-sm mt-2" open>
                <summary className="cursor-pointer text-[var(--accent)] font-medium">
                  Diagnoses ({idx.diagnoses.length})
                </summary>
                <div className="mt-2">
                  <ItemList items={idx.diagnoses} />
                </div>
              </details>
            )}
            {idx.procedures.length > 0 && (
              <details className="text-sm mt-2">
                <summary className="cursor-pointer text-[var(--accent)] font-medium">
                  Procedures ({idx.procedures.length})
                </summary>
                <div className="mt-2">
                  <ItemList items={idx.procedures} />
                </div>
              </details>
            )}
          </Card>

          <Card>
            <SectionHeading>
              Interval Care ({interval.encounters.length} encounters)
            </SectionHeading>
            {interval.new_medications_started.length > 0 && (
              <details className="text-sm mt-1" open>
                <summary className="cursor-pointer text-[var(--accent)] font-medium">
                  New Medications ({interval.new_medications_started.length})
                </summary>
                <div className="mt-2">
                  <ItemList items={interval.new_medications_started} />
                </div>
              </details>
            )}
            {interval.new_conditions_diagnosed.length > 0 && (
              <details className="text-sm mt-2">
                <summary className="cursor-pointer text-[var(--accent)] font-medium">
                  New Conditions ({interval.new_conditions_diagnosed.length})
                </summary>
                <div className="mt-2">
                  <ItemList items={interval.new_conditions_diagnosed} />
                </div>
              </details>
            )}
            {interval.encounters.length === 0 &&
              interval.new_medications_started.length === 0 &&
              interval.new_conditions_diagnosed.length === 0 && (
                <p className="text-sm text-[var(--muted)]">No interval care recorded</p>
              )}
          </Card>

          <Card>
            <SectionHeading>
              Readmission ({context.days_between} days after discharge)
            </SectionHeading>
            <p className="text-sm mb-2">
              {ra.start?.slice(0, 10)} &middot; Reason: {ra.reason_display ?? "—"}
            </p>
            {ra.diagnoses.length > 0 && (
              <details className="text-sm mt-2" open>
                <summary className="cursor-pointer text-[var(--accent)] font-medium">
                  Diagnoses ({ra.diagnoses.length})
                </summary>
                <div className="mt-2">
                  <ItemList items={ra.diagnoses} />
                </div>
              </details>
            )}
          </Card>
        </div>

        {/* Right column — AI Review */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">AI Review</h2>
            <button
              onClick={handleRunReview}
              disabled={reviewing}
              className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors ${
                reviewing
                  ? "bg-[var(--muted)] cursor-not-allowed"
                  : "bg-[var(--accent)] hover:bg-blue-600"
              }`}
            >
              {reviewing ? (
                <>
                  <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
                  Running Review...
                </>
              ) : review ? (
                "Re-run Review"
              ) : (
                "Run AI Review"
              )}
            </button>
          </div>

          {reviewError && (
            <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg p-3 text-sm">
              {reviewError}
            </div>
          )}

          {!review ? (
            <Card>
              <p className="text-[var(--muted)]">No review completed for this pair yet. Click &quot;Run AI Review&quot; to generate one.</p>
            </Card>
          ) : (
            <>
              {review.structured.preventability_rationale && (
                <Card>
                  <SectionHeading>Preventability Rationale</SectionHeading>
                  <p className="text-sm leading-relaxed">{review.structured.preventability_rationale}</p>
                </Card>
              )}

              {review.structured.contributing_factors && review.structured.contributing_factors.length > 0 && (
                <Card>
                  <SectionHeading>Contributing Factors</SectionHeading>
                  <ul className="text-sm space-y-2">
                    {review.structured.contributing_factors.map((f, i) => (
                      <li key={i} className="flex items-start gap-2">
                        <span className="shrink-0 mt-0.5 h-5 w-5 rounded-full bg-amber-100 text-amber-700 flex items-center justify-center text-xs font-bold">
                          {i + 1}
                        </span>
                        <span>{f}</span>
                      </li>
                    ))}
                  </ul>
                </Card>
              )}

              {review.structured.recommended_interventions && review.structured.recommended_interventions.length > 0 && (
                <Card>
                  <SectionHeading>Recommended Interventions</SectionHeading>
                  <ul className="text-sm space-y-2">
                    {review.structured.recommended_interventions.map((int_, i) => (
                      <li key={i} className="flex items-start gap-2">
                        <span className="shrink-0 mt-0.5 h-5 w-5 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-xs font-bold">
                          {i + 1}
                        </span>
                        <span>{int_}</span>
                      </li>
                    ))}
                  </ul>
                </Card>
              )}

              {review.clinical_narrative && (
                <Card className="bg-slate-50 border-slate-200">
                  <SectionHeading>Clinical Narrative</SectionHeading>
                  <div className="text-sm leading-relaxed whitespace-pre-wrap">
                    {review.clinical_narrative}
                  </div>
                </Card>
              )}

              <div className="text-xs text-[var(--muted)] pt-2 flex items-center gap-3">
                <span>Model: {review.model_used ?? "—"}</span>
                <span>&middot;</span>
                <span>Tokens: {review.tokens_used ?? "—"}</span>
                <span>&middot;</span>
                <span>Reviewed: {review.created_at?.slice(0, 10) ?? "—"}</span>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
