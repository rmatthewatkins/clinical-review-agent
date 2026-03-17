"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { fetchApi, ReviewRow } from "@/lib/api";

export default function Reviews() {
  const [rows, setRows] = useState<ReviewRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Filters
  const [rootCause, setRootCause] = useState("All");
  const [minScore, setMinScore] = useState(1);
  const [maxScore, setMaxScore] = useState(5);
  const [confidence, setConfidence] = useState("All");

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

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (rootCause !== "All" && r.root_cause !== rootCause) return false;
      if (r.preventability_score != null && (r.preventability_score < minScore || r.preventability_score > maxScore))
        return false;
      if (confidence !== "All" && r.confidence !== confidence) return false;
      return true;
    });
  }, [rows, rootCause, minScore, maxScore, confidence]);

  if (error) return <p className="text-red-600">Error: {error}</p>;
  if (loading) return <p className="text-[var(--muted)]">Loading...</p>;
  if (!rows.length) return (
    <>
      <h1 className="text-2xl font-bold mb-6">Reviews</h1>
      <p className="text-[var(--muted)]">No completed reviews yet. Run the review pipeline first.</p>
    </>
  );

  return (
    <>
      <h1 className="text-2xl font-bold mb-6">Reviews</h1>

      {/* Filters */}
      <div className="flex gap-4 mb-6 flex-wrap items-end">
        <label className="flex flex-col text-sm gap-1">
          <span className="text-[var(--muted)]">Root Cause</span>
          <select
            className="border border-[var(--border)] rounded px-3 py-1.5 bg-[var(--card)]"
            value={rootCause}
            onChange={(e) => setRootCause(e.target.value)}
          >
            {rootCauses.map((rc) => (
              <option key={rc}>{rc}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-sm gap-1">
          <span className="text-[var(--muted)]">Min Score</span>
          <input
            type="range"
            min={1}
            max={5}
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
            className="w-24"
          />
          <span className="text-xs text-center">{minScore}</span>
        </label>
        <label className="flex flex-col text-sm gap-1">
          <span className="text-[var(--muted)]">Max Score</span>
          <input
            type="range"
            min={1}
            max={5}
            value={maxScore}
            onChange={(e) => setMaxScore(Number(e.target.value))}
            className="w-24"
          />
          <span className="text-xs text-center">{maxScore}</span>
        </label>
        <label className="flex flex-col text-sm gap-1">
          <span className="text-[var(--muted)]">Confidence</span>
          <select
            className="border border-[var(--border)] rounded px-3 py-1.5 bg-[var(--card)]"
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
      <div className="overflow-x-auto bg-[var(--card)] border border-[var(--border)] rounded-lg">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-left text-[var(--muted)]">
              <th className="px-4 py-3">Pair</th>
              <th className="px-4 py-3">Age</th>
              <th className="px-4 py-3">Gender</th>
              <th className="px-4 py-3">Index Diagnosis</th>
              <th className="px-4 py-3">Root Cause</th>
              <th className="px-4 py-3">Score</th>
              <th className="px-4 py-3">Confidence</th>
              <th className="px-4 py-3">Reviewed</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={r.pair_id} className="border-b border-[var(--border)] hover:bg-[var(--accent-light)] transition-colors">
                <td className="px-4 py-3">
                  <Link href={`/reviews/${r.pair_id}`} className="text-[var(--accent)] hover:underline font-medium">
                    #{r.pair_id}
                  </Link>
                </td>
                <td className="px-4 py-3">{r.age ?? "—"}</td>
                <td className="px-4 py-3">{r.gender ?? "—"}</td>
                <td className="px-4 py-3 max-w-xs truncate" title={r.index_diagnosis}>{r.index_diagnosis}</td>
                <td className="px-4 py-3">{r.root_cause}</td>
                <td className="px-4 py-3">{r.preventability_score ?? "—"}</td>
                <td className="px-4 py-3">{r.confidence ?? "—"}</td>
                <td className="px-4 py-3 text-[var(--muted)]">{r.reviewed_at?.slice(0, 10) ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-[var(--muted)] mt-2">{filtered.length} of {rows.length} reviews shown</p>
    </>
  );
}
