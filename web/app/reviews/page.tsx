"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { fetchApi, ReviewRow } from "@/lib/api";

type SortKey = "pair_id" | "age" | "preventability_score" | "root_cause" | "confidence" | "reviewed_at";
type SortDir = "asc" | "desc";

function ScoreBadge({ score }: { score: number | null }) {
  if (score == null) return <span className="text-[var(--muted)]">—</span>;
  const bg =
    score <= 2
      ? "bg-emerald-100 text-emerald-800"
      : score === 3
        ? "bg-amber-100 text-amber-800"
        : "bg-red-100 text-red-800";
  return (
    <span className={`inline-flex items-center justify-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${bg}`}>
      {score}/5
    </span>
  );
}

function ConfidenceBadge({ level }: { level: string | null }) {
  if (!level) return <span className="text-[var(--muted)]">—</span>;
  const bg =
    level.toLowerCase() === "high"
      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
      : level.toLowerCase() === "medium"
        ? "bg-amber-50 text-amber-700 border-amber-200"
        : "bg-slate-50 text-slate-600 border-slate-200";
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${bg}`}>
      {level}
    </span>
  );
}

function RootCausePill({ cause }: { cause: string }) {
  const colors: Record<string, string> = {
    "Premature Discharge": "bg-orange-100 text-orange-800",
    "Inadequate Follow-up": "bg-purple-100 text-purple-800",
    "Medication Issues": "bg-pink-100 text-pink-800",
    "Disease Progression": "bg-blue-100 text-blue-800",
    "Social Determinants": "bg-teal-100 text-teal-800",
    "Patient Non-adherence": "bg-yellow-100 text-yellow-800",
  };
  const cls = colors[cause] ?? "bg-slate-100 text-slate-700";
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap ${cls}`}>
      {cause}
    </span>
  );
}

function SortHeader({
  label,
  sortKey,
  currentSort,
  currentDir,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  currentSort: SortKey;
  currentDir: SortDir;
  onSort: (key: SortKey) => void;
}) {
  const active = currentSort === sortKey;
  return (
    <th
      className="px-4 py-3 cursor-pointer select-none hover:text-[var(--foreground)] transition-colors"
      onClick={() => onSort(sortKey)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active ? (
          <span className="text-[var(--accent)]">{currentDir === "asc" ? "\u2191" : "\u2193"}</span>
        ) : (
          <span className="opacity-30">{"\u2195"}</span>
        )}
      </span>
    </th>
  );
}

export default function Reviews() {
  const [rows, setRows] = useState<ReviewRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [rootCause, setRootCause] = useState("All");
  const [minScore, setMinScore] = useState(1);
  const [maxScore, setMaxScore] = useState(5);
  const [confidence, setConfidence] = useState("All");

  const [sortKey, setSortKey] = useState<SortKey>("pair_id");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  useEffect(() => {
    fetchApi<ReviewRow[]>("/api/reviews")
      .then(setRows)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const rootCauses = useMemo(
    () => ["All", ...Array.from(new Set(rows.map((r) => r.root_cause))).sort()],
    [rows]
  );
  const confidences = useMemo(
    () => ["All", ...Array.from(new Set(rows.map((r) => r.confidence).filter(Boolean) as string[])).sort()],
    [rows]
  );

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  const filtered = useMemo(() => {
    const list = rows.filter((r) => {
      if (rootCause !== "All" && r.root_cause !== rootCause) return false;
      if (r.preventability_score != null && (r.preventability_score < minScore || r.preventability_score > maxScore))
        return false;
      if (confidence !== "All" && r.confidence !== confidence) return false;
      return true;
    });

    list.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === "asc" ? cmp : -cmp;
    });

    return list;
  }, [rows, rootCause, minScore, maxScore, confidence, sortKey, sortDir]);

  if (error) return <p className="text-red-600 p-8">Error: {error}</p>;

  if (loading)
    return (
      <div className="py-16 text-center">
        <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
        <p className="mt-3 text-sm text-[var(--muted)]">Loading reviews...</p>
      </div>
    );

  if (!rows.length)
    return (
      <>
        <h1 className="text-2xl font-bold mb-6">Reviews</h1>
        <p className="text-[var(--muted)]">No completed reviews yet. Run the review pipeline first.</p>
      </>
    );

  return (
    <>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Reviews</h1>
        <span className="text-sm text-[var(--muted)]">
          {filtered.length} of {rows.length} reviews
        </span>
      </div>

      {/* Filters */}
      <div className="flex gap-4 mb-6 flex-wrap items-end bg-[var(--card)] border border-[var(--border)] rounded-lg p-4">
        <label className="flex flex-col text-sm gap-1">
          <span className="text-[var(--muted)] text-xs font-medium uppercase tracking-wide">Root Cause</span>
          <select
            className="border border-[var(--border)] rounded-md px-3 py-1.5 bg-white text-sm"
            value={rootCause}
            onChange={(e) => setRootCause(e.target.value)}
          >
            {rootCauses.map((rc) => (
              <option key={rc}>{rc}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-sm gap-1">
          <span className="text-[var(--muted)] text-xs font-medium uppercase tracking-wide">Min Score</span>
          <div className="flex items-center gap-2">
            <input
              type="range"
              min={1}
              max={5}
              value={minScore}
              onChange={(e) => setMinScore(Number(e.target.value))}
              className="w-20 accent-[var(--accent)]"
            />
            <span className="text-xs font-mono w-3">{minScore}</span>
          </div>
        </label>
        <label className="flex flex-col text-sm gap-1">
          <span className="text-[var(--muted)] text-xs font-medium uppercase tracking-wide">Max Score</span>
          <div className="flex items-center gap-2">
            <input
              type="range"
              min={1}
              max={5}
              value={maxScore}
              onChange={(e) => setMaxScore(Number(e.target.value))}
              className="w-20 accent-[var(--accent)]"
            />
            <span className="text-xs font-mono w-3">{maxScore}</span>
          </div>
        </label>
        <label className="flex flex-col text-sm gap-1">
          <span className="text-[var(--muted)] text-xs font-medium uppercase tracking-wide">Confidence</span>
          <select
            className="border border-[var(--border)] rounded-md px-3 py-1.5 bg-white text-sm"
            value={confidence}
            onChange={(e) => setConfidence(e.target.value)}
          >
            {confidences.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
      </div>

      {/* Table */}
      <div className="overflow-x-auto bg-[var(--card)] border border-[var(--border)] rounded-lg shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-left text-xs text-[var(--muted)] uppercase tracking-wide">
              <SortHeader label="Pair" sortKey="pair_id" currentSort={sortKey} currentDir={sortDir} onSort={handleSort} />
              <SortHeader label="Age" sortKey="age" currentSort={sortKey} currentDir={sortDir} onSort={handleSort} />
              <th className="px-4 py-3">Gender</th>
              <th className="px-4 py-3">Index Diagnosis</th>
              <SortHeader label="Root Cause" sortKey="root_cause" currentSort={sortKey} currentDir={sortDir} onSort={handleSort} />
              <SortHeader label="Score" sortKey="preventability_score" currentSort={sortKey} currentDir={sortDir} onSort={handleSort} />
              <SortHeader label="Confidence" sortKey="confidence" currentSort={sortKey} currentDir={sortDir} onSort={handleSort} />
              <SortHeader label="Reviewed" sortKey="reviewed_at" currentSort={sortKey} currentDir={sortDir} onSort={handleSort} />
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={r.pair_id} className="border-b border-[var(--border)] hover:bg-[var(--accent-light)]/50 transition-colors">
                <td className="px-4 py-3.5">
                  <Link href={`/reviews/${r.pair_id}`} className="text-[var(--accent)] hover:underline font-medium">
                    #{r.pair_id}
                  </Link>
                </td>
                <td className="px-4 py-3.5">{r.age ?? "—"}</td>
                <td className="px-4 py-3.5">{r.gender ?? "—"}</td>
                <td className="px-4 py-3.5 max-w-xs truncate" title={r.index_diagnosis}>
                  {r.index_diagnosis}
                </td>
                <td className="px-4 py-3.5">
                  <RootCausePill cause={r.root_cause} />
                </td>
                <td className="px-4 py-3.5">
                  <ScoreBadge score={r.preventability_score} />
                </td>
                <td className="px-4 py-3.5">
                  <ConfidenceBadge level={r.confidence} />
                </td>
                <td className="px-4 py-3.5 text-[var(--muted)]">{r.reviewed_at?.slice(0, 10) ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
