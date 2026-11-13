# Submission

## Candidate
- **Name:** Harsh Dubey
- **Assignment chosen:** B (Workflow)
- **Time spent (honest estimate):** 8–10 focused hours

## How to run

Python 3.11 or later is required. From the repository root, install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

In one terminal, start the mock provider:

```powershell
Set-Location assignment-b-workflow
..\.venv\Scripts\python.exe -m mock_telephony --speed 1200
```

In a second terminal, from the repository root, run the dispatcher:

```powershell
.\.venv\Scripts\python.exe -m solution.main
```

When every lead reaches a final state, the dispatcher writes `final_state.csv` in the repository root. To run the tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

The dispatcher stores state in `data/dispatcher.sqlite3`. For a separate run, set `AU_DISPATCHER_DB` to a different database path in the dispatcher terminal before starting it. Do not commit database files.

## Design

The service uses a single dispatcher process, SQLite for durable state, and the supplied telephony mock. `leads` stores each campaign lead, its final or ready state, accepted-attempt count, and next retry/callback time. `calls` stores each provider request and its state (`prepared`, `active`, or `ended`). A separate webhook receipt table makes duplicate results harmless, and a phone-level table records global DND numbers.

Before contacting the provider, the dispatcher stores a stable request ID derived from campaign, lead, and attempt. It looks up that ID before resubmitting, so after a crash or ambiguous 504 it can reconcile or safely retry the same provider request. Only provider-accepted calls count as attempts. The dispatcher checks calling windows, weekdays, per-tenant concurrency, and active phone numbers before starting a call. It polls active requests as a fallback if a webhook is lost.

```text
Lead ready
   -> call prepared (request ID persisted)
   -> call active (provider accepted; attempt counted)
   -> completed (terminal disposition)
   -> ready (retry or callback scheduled)
   -> exhausted (attempt limit reached)

DND disposition -> that lead completed; other matching leads become
                   dnd_blocked unless already completed or exhausted.
```

The webhook endpoint verifies the provider's HMAC-SHA256 signature against the raw request body before recording a result. SQLite transactions keep each local state transition together. The partial unique index prevents two locally active calls for one phone, and the dispatcher checks the shared tenant limit before each request.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| Use SQLite and one dispatcher process | Simple durable storage fits the assignment and avoids coordinating multiple schedulers. | Temporal or a distributed database-backed queue. |
| Use the provider's simulated clock for scheduling | The assignment requires business rules to follow provider time, not machine time. | Local wall clock. |
| Reuse the same request ID after a 503 or uncertain 504 | Provider idempotency prevents duplicate calls while resolving an ambiguous response. | Generate a new ID for every HTTP attempt. |
| Poll active provider calls as well as receive webhooks | Some webhooks are intentionally lost. | Rely only on webhook delivery. |
| Use the next eligible campaign window for calls | Calls must follow both campaign and regulatory windows. | Call immediately when a retry/callback becomes due. |

## Failure modes considered

- **503:** leave the request prepared and retry with the same request ID; it does not count as an accepted attempt.
- **504:** query the provider by request ID; if it is still absent, a later retry uses the same ID.
- **Crash after preparing or after provider acceptance:** resume from SQLite and look up the persisted request ID.
- **Duplicate webhooks:** deduplicate by provider call SID and acknowledge duplicates.
- **Missing webhooks:** poll active calls and record their provider result when they end.
- **Bad webhook signatures or JSON:** reject the request without changing lead state.
- **DND:** persist the phone globally and block matching leads, preserving completed/exhausted precedence.
- **Not handled:** multiple dispatcher processes are not supported; the design assumes one dispatcher instance. Provider state itself is held in the mock provider process.

## Testing

- `python -m pytest tests -q -p no:cacheprovider`: **10 passed**.
- Tests cover calling-window boundaries, allowed weekdays, IST/UTC parsing, next-eligible times, work priority, idempotent fixture loading, and attempt counting only after acceptance.
- A clean mock run with `--failure-scale 0 --speed 1200` completed and its 200-row CSV was compared field-by-field with `/admin/leads`.
- A failure-enabled run used `--speed 1200`; the dispatcher was force-stopped while a provider call was active and restarted with the same database. Its final CSV was also compared field-by-field with `/admin/leads`.

## Results

Both full runs finished with 200 CSV rows, 0 differences from `/admin/leads`, and `violation_count: 0`. The failure-enabled run used 407 accepted calls and ended with 139 `completed`, 60 `exhausted`, and 1 `dnd_blocked` lead. No calls remained active. The provider observed maximum concurrency of 3 for Acme Insurance (limit 4) and 3 for Zen Retail (limit 3).

The failure-enabled run exercised duplicate, delayed, and missing webhooks. The provider report recorded 35 duplicate deliveries, 9 lost webhooks, 4 connection errors, and 1 delivery that gave up; polling/reconciliation still produced a matching final CSV. Callback lateness was p50 18.7 minutes, p90 46.4 minutes, and maximum 355.5 minutes. Reducing callback lateness is a known improvement area.

## If I had another day

1. Reduce callback lateness by scheduling the next wake-up around the earliest due callback and campaign opening instead of polling at a fixed interval.
2. Add tests for webhook signatures, duplicate results, DND propagation, 503/504 handling, and crash recovery.
3. Exercise recovery at more crash points and with additional provider seeds.
4. Add coordination before supporting multiple dispatcher processes.
