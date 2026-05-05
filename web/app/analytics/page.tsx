"use client";

import { useEffect, useMemo, useState } from "react";
import {
  fetchApi,
  Analytics,
  RootCauseRow,
  DiagnosisRow,
  GenderRow,
  AgeGroupRow,
  DaysBetweenRow,
  Summary,
} from "@/lib/api";

type CaseType = "readmission" | "mortality";

const CASE_TYPE_LABEL: Record<CaseType, { singular: string; plural: string }> = {
  readmission: { singular: "Readmission", plural: "Readmissions" },
  mortality: { singular: "Mortality case", plural: "Mortality cases" },
};

/* ── Shared components ─────────────────────────────────────────────────── */

function HBar({
  label,
  value,
  max,
  format,
}: {
  label: string;
  value: number;
  max: number;
  format?: (v: number) => string;
}) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  return (
    <div className="flex items-center gap-3 text-sm">
      <span
        className="w-56 truncate text-right text-[var(--muted)]"
        title={label}
      >
        {label}
      </span>
      <div className="flex-1 bg-[var(--border)] rounded-full h-5 overflow-hidden">
        <div
          className="bg-[var(--accent)] h-full rounded-full transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-14 text-right font-medium">
        {format ? format(value) : value}
      </span>
    </div>
  );
}

function Card({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`bg-[var(--card)] border border-[var(--border)] rounded-lg p-5 ${className}`}
    >
      {children}
    </div>
  );
}

function Loading() {
  return <p className="text-sm text-[var(--muted)] py-4">Loading...</p>;
}

function Err({ msg }: { msg: string }) {
  return <p className="text-sm text-red-500 py-4">Error: {msg}</p>;
}

function Empty({ caseType }: { caseType: CaseType }) {
  const noun = CASE_TYPE_LABEL[caseType].plural.toLowerCase();
  return (
    <p className="text-sm text-[var(--muted)] py-4">
      No reviewed {noun} yet — run reviews from the {CASE_TYPE_LABEL[caseType].plural} tab to populate analytics.
    </p>
  );
}

/* ── Tab definitions ───────────────────────────────────────────────────── */

const READMISSION_TABS = [
  "Root Cause",
  "Diagnosis",
  "Gender",
  "Age Group",
  "Days to Readmission",
  "Contributing Factors",
  "Interventions",
] as const;

const MORTALITY_TABS = [
  "Root Cause",
  "Diagnosis",
  "Gender",
  "Contributing Factors",
  "Interventions",
] as const;

type Tab =
  | (typeof READMISSION_TABS)[number]
  | (typeof MORTALITY_TABS)[number];

function tabsFor(caseType: CaseType): readonly Tab[] {
  return caseType === "readmission" ? READMISSION_TABS : MORTALITY_TABS;
}

/* ── Avg preventability helper ─────────────────────────────────────────── */

function avgPrev(
  items: Array<{ preventability_score?: number | null }>
): string {
  const scores = items
    .map((p) => p.preventability_score)
    .filter((s): s is number => s != null);
  if (scores.length === 0) return "-";
  return (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(1);
}

/* ── Section components ────────────────────────────────────────────────── */

function RootCauseSection({ caseType }: { caseType: CaseType }) {
  const [data, setData] = useState<RootCauseRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchApi<RootCauseRow[]>(
      `/api/analytics/by-root-cause?case_type=${caseType}`
    )
      .then(setData)
      .catch((e) => setError(e.message));
  }, [caseType]);
  if (error) return <Err msg={error} />;
  if (!data) return <Loading />;
  if (data.length === 0) return <Empty caseType={caseType} />;
  const max = Math.max(...data.map((d) => d.count), 1);
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <Card>
        <h3 className="font-semibold mb-4">Root Cause Distribution</h3>
        <div className="space-y-2">
          {data.map((d) => (
            <HBar key={d.category} label={d.category} value={d.count} max={max} />
          ))}
        </div>
      </Card>
      <Card>
        <h3 className="font-semibold mb-4">Root Cause Details</h3>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-left text-[var(--muted)]">
              <th className="pb-2">Category</th>
              <th className="pb-2 text-right">Count</th>
              <th className="pb-2 text-right">Avg Preventability</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.category} className="border-b border-[var(--border)]">
                <td className="py-1.5">{d.category}</td>
                <td className="py-1.5 text-right">{d.count}</td>
                <td className="py-1.5 text-right">{avgPrev(d.readmissions)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function DiagnosisSection({ caseType }: { caseType: CaseType }) {
  const [data, setData] = useState<DiagnosisRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchApi<DiagnosisRow[]>(
      `/api/analytics/by-diagnosis?case_type=${caseType}`
    )
      .then(setData)
      .catch((e) => setError(e.message));
  }, [caseType]);
  if (error) return <Err msg={error} />;
  if (!data) return <Loading />;
  if (data.length === 0) return <Empty caseType={caseType} />;
  const top = data.slice(0, 15);
  const max = Math.max(...top.map((d) => d.count), 1);
  const noun = CASE_TYPE_LABEL[caseType].singular;
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <Card>
        <h3 className="font-semibold mb-4">Top Diagnoses by {noun} Count</h3>
        <div className="space-y-2">
          {top.map((d) => (
            <HBar key={d.diagnosis} label={d.diagnosis} value={d.count} max={max} />
          ))}
        </div>
      </Card>
      <Card>
        <h3 className="font-semibold mb-4">Diagnosis Details</h3>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-left text-[var(--muted)]">
              <th className="pb-2">Diagnosis</th>
              <th className="pb-2 text-right">Count</th>
              <th className="pb-2 text-right">Avg Preventability</th>
            </tr>
          </thead>
          <tbody>
            {top.map((d) => (
              <tr key={d.diagnosis} className="border-b border-[var(--border)]">
                <td className="py-1.5 max-w-[200px] truncate" title={d.diagnosis}>{d.diagnosis}</td>
                <td className="py-1.5 text-right">{d.count}</td>
                <td className="py-1.5 text-right">{avgPrev(d.readmissions)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function GenderSection({ caseType }: { caseType: CaseType }) {
  const [data, setData] = useState<GenderRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchApi<GenderRow[]>(`/api/analytics/by-gender?case_type=${caseType}`)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [caseType]);
  if (error) return <Err msg={error} />;
  if (!data) return <Loading />;
  if (data.length === 0) return <Empty caseType={caseType} />;
  const max = Math.max(...data.map((d) => d.count), 1);
  const niceGender = (g: string) =>
    g === "M" ? "Male" : g === "F" ? "Female" : g;
  const noun = CASE_TYPE_LABEL[caseType].plural;
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <Card>
        <h3 className="font-semibold mb-4">{noun} by Gender</h3>
        <div className="space-y-2">
          {data.map((d) => (
            <HBar key={d.gender} label={niceGender(d.gender)} value={d.count} max={max} />
          ))}
        </div>
      </Card>
      <Card>
        <h3 className="font-semibold mb-4">Gender Details</h3>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-left text-[var(--muted)]">
              <th className="pb-2">Gender</th>
              <th className="pb-2 text-right">Count</th>
              <th className="pb-2 text-right">Avg Preventability</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.gender} className="border-b border-[var(--border)]">
                <td className="py-1.5">{niceGender(d.gender)}</td>
                <td className="py-1.5 text-right">{d.count}</td>
                <td className="py-1.5 text-right">{avgPrev(d.readmissions)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function AgeGroupSection() {
  // Backend endpoint is readmission-only; this section is hidden for mortality.
  const [data, setData] = useState<AgeGroupRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchApi<AgeGroupRow[]>("/api/analytics/by-age-group")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);
  if (error) return <Err msg={error} />;
  if (!data) return <Loading />;
  if (data.length === 0) return <Empty caseType="readmission" />;
  const max = Math.max(...data.map((d) => d.count), 1);
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <Card>
        <h3 className="font-semibold mb-4">Readmissions by Age Group</h3>
        <div className="space-y-2">
          {data.map((d) => (
            <HBar key={d.age_group} label={d.age_group} value={d.count} max={max} />
          ))}
        </div>
      </Card>
      <Card>
        <h3 className="font-semibold mb-4">Age Group Details</h3>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-left text-[var(--muted)]">
              <th className="pb-2">Age Group</th>
              <th className="pb-2 text-right">Count</th>
              <th className="pb-2 text-right">Avg Days to Readmit</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => {
              const avgDays = d.readmissions.length > 0
                ? (d.readmissions.reduce((a, p) => a + p.days_between, 0) / d.readmissions.length).toFixed(1)
                : "-";
              return (
                <tr key={d.age_group} className="border-b border-[var(--border)]">
                  <td className="py-1.5">{d.age_group}</td>
                  <td className="py-1.5 text-right">{d.count}</td>
                  <td className="py-1.5 text-right">{avgDays}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function DaysBetweenSection() {
  // Readmission-only by definition.
  const [data, setData] = useState<DaysBetweenRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchApi<DaysBetweenRow[]>("/api/analytics/by-days-between")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);
  if (error) return <Err msg={error} />;
  if (!data) return <Loading />;
  if (data.length === 0) return <Empty caseType="readmission" />;
  const max = Math.max(...data.map((d) => d.count), 1);
  const total = data.reduce((a, d) => a + d.count, 0);
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <Card>
        <h3 className="font-semibold mb-4">Days to Readmission</h3>
        <div className="space-y-2">
          {data.map((d) => (
            <HBar key={d.bucket} label={`${d.bucket} days`} value={d.count} max={max} />
          ))}
        </div>
      </Card>
      <Card>
        <h3 className="font-semibold mb-4">Days Breakdown</h3>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-left text-[var(--muted)]">
              <th className="pb-2">Bucket</th>
              <th className="pb-2 text-right">Count</th>
              <th className="pb-2 text-right">% of Total</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.bucket} className="border-b border-[var(--border)]">
                <td className="py-1.5">{d.bucket} days</td>
                <td className="py-1.5 text-right">{d.count}</td>
                <td className="py-1.5 text-right">
                  {total > 0 ? ((d.count / total) * 100).toFixed(0) : 0}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function FactorsSection({
  data,
  caseType,
}: {
  data: Analytics | null;
  caseType: CaseType;
}) {
  if (!data) return <Loading />;
  if (data.contributing_factors.length === 0) return <Empty caseType={caseType} />;
  const max = Math.max(...data.contributing_factors.map((f) => f.count), 1);
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <Card>
        <h3 className="font-semibold mb-4">Top Contributing Factors</h3>
        <div className="space-y-2">
          {data.contributing_factors.map((f) => (
            <HBar key={f.factor} label={f.factor} value={f.count} max={max} />
          ))}
        </div>
      </Card>
      <Card>
        <h3 className="font-semibold mb-4">Contributing Factors Table</h3>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-left text-[var(--muted)]">
              <th className="pb-2">Factor</th>
              <th className="pb-2 text-right">Count</th>
            </tr>
          </thead>
          <tbody>
            {data.contributing_factors.map((f) => (
              <tr key={f.factor} className="border-b border-[var(--border)]">
                <td className="py-1.5">{f.factor}</td>
                <td className="py-1.5 text-right">{f.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function InterventionsSection({
  data,
  caseType,
}: {
  data: Analytics | null;
  caseType: CaseType;
}) {
  if (!data) return <Loading />;
  if (data.recommended_interventions.length === 0) return <Empty caseType={caseType} />;
  const max = Math.max(...data.recommended_interventions.map((i) => i.count), 1);
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <Card>
        <h3 className="font-semibold mb-4">Top Recommended Interventions</h3>
        <div className="space-y-2">
          {data.recommended_interventions.map((i) => (
            <HBar key={i.intervention} label={i.intervention} value={i.count} max={max} />
          ))}
        </div>
      </Card>
      <Card>
        <h3 className="font-semibold mb-4">Interventions Table</h3>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-left text-[var(--muted)]">
              <th className="pb-2">Intervention</th>
              <th className="pb-2 text-right">Count</th>
            </tr>
          </thead>
          <tbody>
            {data.recommended_interventions.map((i) => (
              <tr key={i.intervention} className="border-b border-[var(--border)]">
                <td className="py-1.5">{i.intervention}</td>
                <td className="py-1.5 text-right">{i.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

/* ── Page ───────────────────────────────────────────────────────────────── */

function FactorsInterventionsLoader({
  caseType,
  active,
}: {
  caseType: CaseType;
  active: "Contributing Factors" | "Interventions";
}) {
  // Owns the /api/analytics fetch shared between Factors + Interventions tabs.
  // Keyed on caseType by the parent so it remounts cleanly on type switch —
  // that's why the effect can use `[]` deps without resetting state inline.
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchApi<Analytics>(`/api/analytics?case_type=${caseType}`)
      .then(setData)
      .catch((e) => setError(e.message));
    // caseType is captured at mount; parent uses key={caseType} for remount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (error) return <Err msg={error} />;
  return active === "Contributing Factors"
    ? <FactorsSection data={data} caseType={caseType} />
    : <InterventionsSection data={data} caseType={caseType} />;
}

export default function AnalyticsPage() {
  const [caseType, setCaseType] = useState<CaseType>("readmission");
  const [active, setActive] = useState<Tab>("Root Cause");

  // Total reviewed cases for the chip next to the case-type toggle.
  const [summary, setSummary] = useState<Summary | null>(null);
  useEffect(() => {
    fetchApi<Summary>("/api/summary").then(setSummary).catch(() => {});
  }, []);

  const visibleTabs = useMemo(() => tabsFor(caseType), [caseType]);

  // Compute the effective active tab synchronously during render. If the
  // user-clicked tab isn't available for the current case type, fall back
  // to the first visible tab — without an effect that would re-trigger
  // renders. (The user's `active` state is preserved so switching back to
  // the previous case type restores their last tab.)
  const effectiveActive: Tab = visibleTabs.includes(active) ? active : visibleTabs[0];

  const reviewedCount = summary?.reviews_by_type?.[caseType] ?? null;
  const totalCount = summary?.cases?.[caseType] ?? null;

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <h1 className="text-2xl font-bold">Analytics</h1>
        <div className="flex items-center gap-3">
          {/* Case-type segmented control */}
          <div
            role="tablist"
            aria-label="Case type"
            className="inline-flex rounded-md border border-[var(--border)] overflow-hidden text-sm"
          >
            {(["readmission", "mortality"] as CaseType[]).map((ct) => (
              <button
                key={ct}
                role="tab"
                aria-selected={caseType === ct}
                onClick={() => setCaseType(ct)}
                className={`px-3 py-1.5 transition-colors ${
                  caseType === ct
                    ? "bg-[var(--accent)] text-white"
                    : "bg-[var(--card)] text-[var(--muted)] hover:text-[var(--foreground)]"
                }`}
              >
                {CASE_TYPE_LABEL[ct].plural}
              </button>
            ))}
          </div>
          {reviewedCount != null && totalCount != null && (
            <span
              className="text-xs text-[var(--muted)] tabular-nums"
              title={`${reviewedCount} of ${totalCount} ${CASE_TYPE_LABEL[caseType].plural.toLowerCase()} reviewed`}
            >
              {reviewedCount} / {totalCount} reviewed
            </span>
          )}
        </div>
      </div>

      {/* Sub-tab bar */}
      <div className="flex flex-wrap gap-1 mb-6 border-b border-[var(--border)]">
        {visibleTabs.map((tab) => (
          <button
            key={tab}
            onClick={() => setActive(tab)}
            className={`px-4 py-2 text-sm font-medium transition-colors rounded-t ${
              effectiveActive === tab
                ? "bg-[var(--card)] border border-b-0 border-[var(--border)] text-[var(--accent)] -mb-px"
                : "text-[var(--muted)] hover:text-[var(--foreground)]"
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Active section. key={caseType} forces a clean remount on type switch
          so per-section state resets without setState-in-effect cascades. */}
      {effectiveActive === "Root Cause" && <RootCauseSection key={caseType} caseType={caseType} />}
      {effectiveActive === "Diagnosis" && <DiagnosisSection key={caseType} caseType={caseType} />}
      {effectiveActive === "Gender" && <GenderSection key={caseType} caseType={caseType} />}
      {effectiveActive === "Age Group" && <AgeGroupSection />}
      {effectiveActive === "Days to Readmission" && <DaysBetweenSection />}
      {(effectiveActive === "Contributing Factors" || effectiveActive === "Interventions") && (
        <FactorsInterventionsLoader key={caseType} caseType={caseType} active={effectiveActive} />
      )}
    </>
  );
}
