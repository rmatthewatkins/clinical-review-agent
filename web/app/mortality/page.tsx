"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { fetchApi, MortalityCase, PaginatedResponse } from "@/lib/api";

type SortKey = "case_id" | "age" | "diagnosis" | "death_date" | "review_status";
type SortDir = "asc" | "desc";

function StatusBadge({ reviewed }: { reviewed: boolean }) {
  return reviewed ? (
    <span className="inline-flex items-center rounded-full bg-emerald-100 text-emerald-800 px-2.5 py-0.5 text-xs font-semibold">
      Reviewed
    </span>
  ) : (
    <span className="inline-flex items-center rounded-full bg-amber-100 text-amber-800 px-2.5 py-0.5 text-xs font-semibold">
      Pending
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

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="bg-[var(--card)] border border-[var(--border)] rounded-lg shadow-sm px-5 py-4">
      <p className="text-xs text-[var(--muted)] uppercase tracking-wide font-medium">{label}</p>
      <p className="text-2xl font-bold mt-1">{value}</p>
    </div>
  );
}

export default function MortalityPage() {
  const [rows, setRows] = useState<MortalityCase[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  const [sortKey, setSortKey] = useState<SortKey>("case_id");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  useEffect(() => {
    fetchApi<PaginatedResponse<MortalityCase>>("/api/mortality-cases?limit=200")
      .then((res) => setRows(res.items))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  const stats = useMemo(() => {
    const reviewed = rows.filter((r) => r.review_status === "reviewed").length;
    return { total: rows.length, reviewed, pending: rows.length - reviewed };
  }, [rows]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    const list = rows.filter((r) => {
      if (!q) return true;
      return (
        String(r.case_id).includes(q) ||
        r.diagnosis.toLowerCase().includes(q) ||
        (r.gender ?? "").toLowerCase().includes(q) ||
        (r.root_cause ?? "").toLowerCase().includes(q)
      );
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
  }, [rows, search, sortKey, sortDir]);

  if (error) return <p className="text-red-600 p-8">Error: {error}</p>;

  if (loading)
    return (
      <div className="py-16 text-center">
        <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
        <p className="mt-3 text-sm text-[var(--muted)]">Loading mortality cases...</p>
      </div>
    );

  if (!rows.length)
    return (
      <>
        <h1 className="text-2xl font-bold mb-6">Mortality Cases</h1>
        <p className="text-[var(--muted)]">No mortality cases found. Run the identify pipeline with --type mortality first.</p>
      </>
    );

  return (
    <>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Mortality Cases</h1>
        <span className="text-sm text-[var(--muted)]">
          {filtered.length} of {rows.length} cases
        </span>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <StatCard label="Total Cases" value={stats.total} />
        <StatCard label="Reviewed" value={stats.reviewed} />
        <StatCard label="Pending" value={stats.pending} />
      </div>

      {/* Search */}
      <div className="mb-4">
        <input
          type="text"
          placeholder="Search by diagnosis, gender, root cause..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full max-w-md border border-[var(--border)] rounded-lg px-4 py-2 text-sm bg-[var(--card)] placeholder:text-[var(--muted)]"
        />
      </div>

      {/* Table */}
      <div className="overflow-x-auto bg-[var(--card)] border border-[var(--border)] rounded-lg shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-left text-xs text-[var(--muted)] uppercase tracking-wide">
              <SortHeader label="ID" sortKey="case_id" currentSort={sortKey} currentDir={sortDir} onSort={handleSort} />
              <SortHeader label="Age" sortKey="age" currentSort={sortKey} currentDir={sortDir} onSort={handleSort} />
              <th className="px-4 py-3">Gender</th>
              <SortHeader label="Diagnosis" sortKey="diagnosis" currentSort={sortKey} currentDir={sortDir} onSort={handleSort} />
              <SortHeader label="Death Date" sortKey="death_date" currentSort={sortKey} currentDir={sortDir} onSort={handleSort} />
              <th className="px-4 py-3">Disposition</th>
              <SortHeader label="Status" sortKey="review_status" currentSort={sortKey} currentDir={sortDir} onSort={handleSort} />
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={r.case_id} className="border-b border-[var(--border)] hover:bg-[var(--accent-light)]/50 transition-colors">
                <td className="px-4 py-3.5">
                  <Link href={`/reviews/mortality/${r.case_id}`} className="text-[var(--accent)] hover:underline font-medium">
                    #{r.case_id}
                  </Link>
                </td>
                <td className="px-4 py-3.5">{r.age ?? "\u2014"}</td>
                <td className="px-4 py-3.5">{r.gender ?? "\u2014"}</td>
                <td className="px-4 py-3.5 max-w-xs truncate" title={r.diagnosis}>
                  {r.diagnosis}
                </td>
                <td className="px-4 py-3.5 text-[var(--muted)]">{r.death_date ?? "\u2014"}</td>
                <td className="px-4 py-3.5 text-[var(--muted)]">{r.discharge_disposition ?? "\u2014"}</td>
                <td className="px-4 py-3.5">
                  <StatusBadge reviewed={r.review_status === "reviewed"} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
