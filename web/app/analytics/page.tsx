"use client";

import { useEffect, useState } from "react";
import {
  fetchApi,
  Analytics,
  RootCauseRow,
  DiagnosisRow,
  GenderRow,
  AgeGroupRow,
  DaysBetweenRow,
} from "@/lib/api";

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

const tabs = [
  "Root Cause",
  "Diagnosis",
  "Gender",
  "Age Group",
  "Days to Readmission",
  "Contributing Factors",
  "Interventions",
] as const;
type Tab = (typeof tabs)[number];

/* ── Avg preventability helper ─────────────────────────────────────────── */

function avgPrev(
  pairs: Array<{ preventability_score?: number | null }>
): string {
  const scores = pairs
    .map((p) => p.preventability_score)
    .filter((s): s is number => s != null);
  if (scores.length === 0) return "-";
  return (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(1);
}

/* ── Section components ────────────────────────────────────────────────── */

function RootCauseSection() {
  const [data, setData] = useState<RootCauseRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchApi<RootCauseRow[]>("/api/analytics/by-root-cause")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);
  if (error) return <Err msg={error} />;
  if (!data) return <Loading />;
  const max = Math.max(...data.map((d) => d.count), 1);
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <Card>
        <h3 className="font-semibold mb-4">Root Cause Distribution</h3>
        {data.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No data</p>
        ) : (
          <div className="space-y-2">
            {data.map((d) => (
              <HBar key={d.category} label={d.category} value={d.count} max={max} />
            ))}
          </div>
        )}
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
                <td className="py-1.5 text-right">{avgPrev(d.pairs)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function DiagnosisSection() {
  const [data, setData] = useState<DiagnosisRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchApi<DiagnosisRow[]>("/api/analytics/by-diagnosis")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);
  if (error) return <Err msg={error} />;
  if (!data) return <Loading />;
  const top = data.slice(0, 15);
  const max = Math.max(...top.map((d) => d.count), 1);
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <Card>
        <h3 className="font-semibold mb-4">Top Diagnoses by Readmission Count</h3>
        {top.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No data</p>
        ) : (
          <div className="space-y-2">
            {top.map((d) => (
              <HBar key={d.diagnosis} label={d.diagnosis} value={d.count} max={max} />
            ))}
          </div>
        )}
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
                <td className="py-1.5 text-right">{avgPrev(d.pairs)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function GenderSection() {
  const [data, setData] = useState<GenderRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchApi<GenderRow[]>("/api/analytics/by-gender")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);
  if (error) return <Err msg={error} />;
  if (!data) return <Loading />;
  const max = Math.max(...data.map((d) => d.count), 1);
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <Card>
        <h3 className="font-semibold mb-4">Readmissions by Gender</h3>
        <div className="space-y-2">
          {data.map((d) => (
            <HBar key={d.gender} label={d.gender === "M" ? "Male" : d.gender === "F" ? "Female" : d.gender} value={d.count} max={max} />
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
                <td className="py-1.5">{d.gender === "M" ? "Male" : d.gender === "F" ? "Female" : d.gender}</td>
                <td className="py-1.5 text-right">{d.count}</td>
                <td className="py-1.5 text-right">{avgPrev(d.pairs)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function AgeGroupSection() {
  const [data, setData] = useState<AgeGroupRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchApi<AgeGroupRow[]>("/api/analytics/by-age-group")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);
  if (error) return <Err msg={error} />;
  if (!data) return <Loading />;
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
              const avgDays = d.pairs.length > 0
                ? (d.pairs.reduce((a, p) => a + p.days_between, 0) / d.pairs.length).toFixed(1)
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
  const [data, setData] = useState<DaysBetweenRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchApi<DaysBetweenRow[]>("/api/analytics/by-days-between")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);
  if (error) return <Err msg={error} />;
  if (!data) return <Loading />;
  const max = Math.max(...data.map((d) => d.count), 1);
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
            {(() => {
              const total = data.reduce((a, d) => a + d.count, 0);
              return data.map((d) => (
                <tr key={d.bucket} className="border-b border-[var(--border)]">
                  <td className="py-1.5">{d.bucket} days</td>
                  <td className="py-1.5 text-right">{d.count}</td>
                  <td className="py-1.5 text-right">
                    {total > 0 ? ((d.count / total) * 100).toFixed(0) : 0}%
                  </td>
                </tr>
              ));
            })()}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function FactorsSection() {
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchApi<Analytics>("/api/analytics")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);
  if (error) return <Err msg={error} />;
  if (!data) return <Loading />;
  const max = Math.max(...data.contributing_factors.map((f) => f.count), 1);
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <Card>
        <h3 className="font-semibold mb-4">Top Contributing Factors</h3>
        {data.contributing_factors.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No data</p>
        ) : (
          <div className="space-y-2">
            {data.contributing_factors.map((f) => (
              <HBar key={f.factor} label={f.factor} value={f.count} max={max} />
            ))}
          </div>
        )}
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

function InterventionsSection() {
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchApi<Analytics>("/api/analytics")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);
  if (error) return <Err msg={error} />;
  if (!data) return <Loading />;
  const max = Math.max(...data.recommended_interventions.map((i) => i.count), 1);
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <Card>
        <h3 className="font-semibold mb-4">Top Recommended Interventions</h3>
        {data.recommended_interventions.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No data</p>
        ) : (
          <div className="space-y-2">
            {data.recommended_interventions.map((i) => (
              <HBar key={i.intervention} label={i.intervention} value={i.count} max={max} />
            ))}
          </div>
        )}
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

export default function AnalyticsPage() {
  const [active, setActive] = useState<Tab>("Root Cause");

  return (
    <>
      <h1 className="text-2xl font-bold mb-6">Analytics</h1>

      {/* Tab bar */}
      <div className="flex flex-wrap gap-1 mb-6 border-b border-[var(--border)]">
        {tabs.map((tab) => (
          <button
            key={tab}
            onClick={() => setActive(tab)}
            className={`px-4 py-2 text-sm font-medium transition-colors rounded-t ${
              active === tab
                ? "bg-[var(--card)] border border-b-0 border-[var(--border)] text-[var(--accent)] -mb-px"
                : "text-[var(--muted)] hover:text-[var(--foreground)]"
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Active section */}
      {active === "Root Cause" && <RootCauseSection />}
      {active === "Diagnosis" && <DiagnosisSection />}
      {active === "Gender" && <GenderSection />}
      {active === "Age Group" && <AgeGroupSection />}
      {active === "Days to Readmission" && <DaysBetweenSection />}
      {active === "Contributing Factors" && <FactorsSection />}
      {active === "Interventions" && <InterventionsSection />}
    </>
  );
}
