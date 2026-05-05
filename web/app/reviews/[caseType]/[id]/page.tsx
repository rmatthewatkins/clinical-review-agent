"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { fetchApi, postApi, ReviewDetail, JobSubmitResult, JobStatus } from "@/lib/api";

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
  if (score == null) return <span className="text-[var(--muted)]">&mdash;</span>;
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
  if (!level) return <span className="text-[var(--muted)]">&mdash;</span>;
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

function ReadmissionContext({ context }: { context: Record<string, unknown> }) {
  const pb = context.patient_baseline as Record<string, unknown>;
  const idx = context.index_admission as Record<string, unknown>;
  const interval = context.interval_care as Record<string, unknown>;
  const ra = context.readmission as Record<string, unknown>;

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">Clinical Context</h2>

      <Card>
        <SectionHeading>Patient Baseline</SectionHeading>
        <p className="text-sm mb-3">
          {String(pb?.race ?? "\u2014")} &middot; {String(pb?.city)}, {String(pb?.state)}
        </p>
        <SectionHeading>Chronic Conditions</SectionHeading>
        <ItemList items={(pb?.chronic_conditions as Array<Record<string, unknown>>) ?? []} />
      </Card>

      <Card>
        <SectionHeading>Active Medications</SectionHeading>
        <ItemList items={(pb?.active_medications as Array<Record<string, unknown>>) ?? []} />
      </Card>

      <Card>
        <SectionHeading>
          Index Admission ({String((idx as Record<string, unknown>)?.length_of_stay_days ?? "?")} days)
        </SectionHeading>
        <p className="text-sm mb-2">
          {String((idx as Record<string, unknown>)?.start)?.slice(0, 10)} &rarr; {String((idx as Record<string, unknown>)?.end)?.slice(0, 10)} &middot; Reason: {String((idx as Record<string, unknown>)?.reason_display ?? "\u2014")}
        </p>
        {((idx as Record<string, unknown>)?.diagnoses as Array<Record<string, unknown>>)?.length > 0 && (
          <details className="text-sm mt-2" open>
            <summary className="cursor-pointer text-[var(--accent)] font-medium">
              Diagnoses ({((idx as Record<string, unknown>)?.diagnoses as Array<Record<string, unknown>>)?.length})
            </summary>
            <div className="mt-2">
              <ItemList items={(idx as Record<string, unknown>)?.diagnoses as Array<Record<string, unknown>> ?? []} />
            </div>
          </details>
        )}
        {((idx as Record<string, unknown>)?.procedures as Array<Record<string, unknown>>)?.length > 0 && (
          <details className="text-sm mt-2">
            <summary className="cursor-pointer text-[var(--accent)] font-medium">
              Procedures ({((idx as Record<string, unknown>)?.procedures as Array<Record<string, unknown>>)?.length})
            </summary>
            <div className="mt-2">
              <ItemList items={(idx as Record<string, unknown>)?.procedures as Array<Record<string, unknown>> ?? []} />
            </div>
          </details>
        )}
      </Card>

      <Card>
        <SectionHeading>
          Interval Care ({((interval as Record<string, unknown>)?.encounters as Array<Record<string, unknown>>)?.length ?? 0} encounters)
        </SectionHeading>
        {((interval as Record<string, unknown>)?.new_medications_started as Array<Record<string, unknown>>)?.length > 0 && (
          <details className="text-sm mt-1" open>
            <summary className="cursor-pointer text-[var(--accent)] font-medium">
              New Medications ({((interval as Record<string, unknown>)?.new_medications_started as Array<Record<string, unknown>>)?.length})
            </summary>
            <div className="mt-2">
              <ItemList items={(interval as Record<string, unknown>)?.new_medications_started as Array<Record<string, unknown>> ?? []} />
            </div>
          </details>
        )}
        {((interval as Record<string, unknown>)?.new_conditions_diagnosed as Array<Record<string, unknown>>)?.length > 0 && (
          <details className="text-sm mt-2">
            <summary className="cursor-pointer text-[var(--accent)] font-medium">
              New Conditions ({((interval as Record<string, unknown>)?.new_conditions_diagnosed as Array<Record<string, unknown>>)?.length})
            </summary>
            <div className="mt-2">
              <ItemList items={(interval as Record<string, unknown>)?.new_conditions_diagnosed as Array<Record<string, unknown>> ?? []} />
            </div>
          </details>
        )}
      </Card>

      <Card>
        <SectionHeading>
          Readmission ({String(context.days_between)} days after discharge)
        </SectionHeading>
        <p className="text-sm mb-2">
          {String((ra as Record<string, unknown>)?.start)?.slice(0, 10)} &middot; Reason: {String((ra as Record<string, unknown>)?.reason_display ?? "\u2014")}
        </p>
        {((ra as Record<string, unknown>)?.diagnoses as Array<Record<string, unknown>>)?.length > 0 && (
          <details className="text-sm mt-2" open>
            <summary className="cursor-pointer text-[var(--accent)] font-medium">
              Diagnoses ({((ra as Record<string, unknown>)?.diagnoses as Array<Record<string, unknown>>)?.length})
            </summary>
            <div className="mt-2">
              <ItemList items={(ra as Record<string, unknown>)?.diagnoses as Array<Record<string, unknown>> ?? []} />
            </div>
          </details>
        )}
      </Card>
    </div>
  );
}

function MortalityContext({ context }: { context: Record<string, unknown> }) {
  const pb = context.patient_baseline as Record<string, unknown>;
  const deathEnc = context.death_encounter as Record<string, unknown>;
  const priorCare = context.prior_care as Record<string, unknown>;

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">Clinical Context</h2>

      <Card>
        <SectionHeading>Patient Baseline</SectionHeading>
        <p className="text-sm mb-3">
          {String(pb?.race ?? "\u2014")} &middot; {String(pb?.city)}, {String(pb?.state)}
        </p>
        <SectionHeading>Chronic Conditions</SectionHeading>
        <ItemList items={(pb?.chronic_conditions as Array<Record<string, unknown>>) ?? []} />
      </Card>

      <Card>
        <SectionHeading>Active Medications</SectionHeading>
        <ItemList items={(pb?.active_medications as Array<Record<string, unknown>>) ?? []} />
      </Card>

      <Card>
        <SectionHeading>
          Death Encounter ({String(deathEnc?.length_of_stay_days ?? "?")} days)
        </SectionHeading>
        <p className="text-sm mb-2">
          {String(deathEnc?.start)?.slice(0, 10)} &rarr; {String(deathEnc?.end)?.slice(0, 10)}
        </p>
        <p className="text-sm mb-2">
          Reason: {String(deathEnc?.reason_display ?? "\u2014")} &middot; Disposition: {String(deathEnc?.discharge_disposition ?? "\u2014")}
        </p>
        {(deathEnc?.diagnoses as Array<Record<string, unknown>>)?.length > 0 && (
          <details className="text-sm mt-2" open>
            <summary className="cursor-pointer text-[var(--accent)] font-medium">
              Diagnoses ({(deathEnc?.diagnoses as Array<Record<string, unknown>>)?.length})
            </summary>
            <div className="mt-2">
              <ItemList items={(deathEnc?.diagnoses as Array<Record<string, unknown>>) ?? []} />
            </div>
          </details>
        )}
        {(deathEnc?.procedures as Array<Record<string, unknown>>)?.length > 0 && (
          <details className="text-sm mt-2">
            <summary className="cursor-pointer text-[var(--accent)] font-medium">
              Procedures ({(deathEnc?.procedures as Array<Record<string, unknown>>)?.length})
            </summary>
            <div className="mt-2">
              <ItemList items={(deathEnc?.procedures as Array<Record<string, unknown>>) ?? []} />
            </div>
          </details>
        )}
      </Card>

      {priorCare && (
        <Card>
          <SectionHeading>
            Prior Care ({String(context.lookback_days)}-day lookback, {(priorCare?.encounters as Array<Record<string, unknown>>)?.length ?? 0} encounters)
          </SectionHeading>
          {(priorCare?.recent_conditions as Array<Record<string, unknown>>)?.length > 0 && (
            <details className="text-sm mt-1" open>
              <summary className="cursor-pointer text-[var(--accent)] font-medium">
                Recent Conditions ({(priorCare?.recent_conditions as Array<Record<string, unknown>>)?.length})
              </summary>
              <div className="mt-2">
                <ItemList items={(priorCare?.recent_conditions as Array<Record<string, unknown>>) ?? []} />
              </div>
            </details>
          )}
          {(priorCare?.active_medications as Array<Record<string, unknown>>)?.length > 0 && (
            <details className="text-sm mt-2">
              <summary className="cursor-pointer text-[var(--accent)] font-medium">
                Active Medications ({(priorCare?.active_medications as Array<Record<string, unknown>>)?.length})
              </summary>
              <div className="mt-2">
                <ItemList items={(priorCare?.active_medications as Array<Record<string, unknown>>) ?? []} />
              </div>
            </details>
          )}
        </Card>
      )}
    </div>
  );
}

export default function ReviewDetailPage() {
  const params = useParams<{ caseType: string; id: string }>();
  const caseType = params.caseType;
  const id = params.id;
  const [data, setData] = useState<{ context: Record<string, unknown>; review: ReviewDetail["review"] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reviewing, setReviewing] = useState(false);
  const [reviewStatus, setReviewStatus] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollErrorCount = useRef<number>(0);

  const loadData = useCallback(() => {
    if (!id || !caseType) return;
    fetchApi<{ context: Record<string, unknown>; review: ReviewDetail["review"] }>(`/api/reviews/${caseType}/${id}`)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [id, caseType]);

  useEffect(() => { loadData(); }, [loadData]);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  const stopPolling = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    if (timeoutRef.current) { clearTimeout(timeoutRef.current); timeoutRef.current = null; }
  };

  const pollJob = (jobId: string) => {
    pollErrorCount.current = 0;
    pollRef.current = setInterval(async () => {
      try {
        const job = await fetchApi<JobStatus>(`/api/jobs/${jobId}`);
        pollErrorCount.current = 0;
        if (job.status === "running") {
          setReviewStatus("Running Review...");
        } else if (job.status === "completed") {
          stopPolling();
          setReviewing(false);
          setReviewStatus(null);
          setLoading(true);
          loadData();
        } else if (job.status === "failed") {
          stopPolling();
          setReviewing(false);
          setReviewStatus(null);
          setReviewError(job.error ?? "Review failed");
        }
      } catch {
        pollErrorCount.current += 1;
        if (pollErrorCount.current >= 5) {
          stopPolling();
          setReviewing(false);
          setReviewStatus(null);
          setReviewError("Lost connection to server. Please try again.");
        }
      }
    }, 2000);

    timeoutRef.current = setTimeout(() => {
      stopPolling();
      setReviewing(false);
      setReviewStatus(null);
      setReviewError("Review timed out. Check back later.");
    }, 5 * 60 * 1000);
  };

  const handleRunReview = async () => {
    if (!id || !caseType) return;
    setReviewing(true);
    setReviewError(null);
    setReviewStatus("Queued...");
    try {
      const result = await postApi<JobSubmitResult>(`/api/reviews/${caseType}/${id}/run`);
      pollJob(result.job_id);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Review failed";
      if (msg.includes("409")) {
        try {
          const bodyStr = msg.substring(msg.indexOf("409 ") + 4);
          const body = JSON.parse(bodyStr);
          const jobId = body?.detail?.job_id;
          if (jobId) {
            pollJob(jobId);
            return;
          }
        } catch { /* fall through */ }
      }
      setReviewing(false);
      setReviewStatus(null);
      setReviewError(msg);
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
  const pb = context.patient_baseline as Record<string, unknown>;
  const isMortality = caseType === "mortality";
  const caseLabel = isMortality ? "Mortality Case" : "Readmission";
  const caseId = isMortality ? context.case_id : context.readmission_id;
  const backHref = isMortality ? "/mortality" : "/reviews";

  return (
    <>
      {/* Header */}
      <div className="mb-6">
        <Link
          href={backHref}
          className="text-sm text-[var(--accent)] hover:underline inline-flex items-center gap-1 mb-3"
        >
          &larr; Back to {isMortality ? "Mortality Cases" : "Reviews"}
        </Link>
        <h1 className="text-2xl font-bold">Review &mdash; {caseLabel} #{String(caseId)}</h1>
        <p className="text-sm text-[var(--muted)] mt-1">
          {String(pb?.gender ?? "\u2014")}, age {String(pb?.age ?? "\u2014")}
          {!isMortality && context.days_between && <> &middot; Readmitted {String(context.days_between)} days after discharge</>}
          {isMortality && context.death_date && <> &middot; Died {String(context.death_date)}</>}
        </p>
      </div>

      {/* Top summary cards (review highlights) */}
      {review && (
        <div className="grid grid-cols-3 gap-4 mb-6">
          <Card>
            <SectionHeading>Root Cause</SectionHeading>
            <p className="text-base font-semibold">{review.structured.root_cause_category ?? "\u2014"}</p>
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
        {isMortality ? (
          <MortalityContext context={context} />
        ) : (
          <ReadmissionContext context={context} />
        )}

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
                  {reviewStatus ?? "Running Review..."}
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
              <p className="text-[var(--muted)]">No review completed yet. Click &quot;Run AI Review&quot; to generate one.</p>
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
                <span>Model: {review.model_used ?? "\u2014"}</span>
                <span>&middot;</span>
                <span>Tokens: {review.tokens_used ?? "\u2014"}</span>
                <span>&middot;</span>
                <span>Reviewed: {review.created_at?.slice(0, 10) ?? "\u2014"}</span>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
