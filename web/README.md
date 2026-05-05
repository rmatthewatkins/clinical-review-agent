# Clinical Review Agent — Web UI

Next.js 16 (App Router) + TypeScript + Tailwind frontend for the Clinical Review Agent. Hits the FastAPI backend (`server.py`) at `http://localhost:8000` by default.

## Pages

| Route | Purpose |
|---|---|
| `/` | Dashboard — patient/encounter/case/review counts, root-cause distribution, preventability charts, quick links |
| `/readmissions` | Readmission case list with patient demographics, encounter details, review status |
| `/mortality` | Mortality case list with death disposition + lookback details |
| `/reviews` | Completed reviews — preventability badges, root-cause pills, confidence indicators; filterable by `case_type` |
| `/reviews/[caseType]/[id]` | Two-column detail view: clinical context (left) + AI assessment + narrative (right). "Run AI Review" button triggers a Claude review for Pending cases (rate-limited). |
| `/analytics` | Multi-dimension cohort analytics — root cause, diagnosis, gender, age group, days to readmission, factors, interventions |

## Run locally

```bash
npm install                          # one-time
npm run dev                          # http://localhost:3000
```

`server.py` (FastAPI) must also be running on `:8000` — the dev server uses `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`) for all data fetches.

## API client

`lib/api.ts` exports `fetchApi<T>(path)` and the TypeScript types for every endpoint payload. Update both when the FastAPI shape changes.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | FastAPI backend base URL |

See the project root `README.md` and `CLAUDE.md` for end-to-end setup, CLI usage, and architecture.
