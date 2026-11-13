# Scheduling, Retries and Metrics

This page describes how approved content becomes a published post, what happens
when publication fails, and how engagement is collected.

## 1. Lifecycle of a post

```
draft ──approve──▶ approved ──schedule──▶ scheduled ──dispatch──▶ published
  │                    │                      │
  └──reject──▶ rejected                       └──permanent error──▶ failed
                                              └──retries exhausted──▶ failed (job: dead_letter)
```

- Only an `approved` item can be scheduled. This is the human gate.
- Scheduling puts a row in the `jobs` table with status `pending`.
- The dispatcher publishes the item when its `run_at` time has come.
- Publication goes through the same publishing service as the CLI, so
  `DRY_RUN` writes the payload to `data/dryrun/` and nothing is sent.

## 2. Queueing a post

CLI:

```bash
# Next time from a fixed list of slots, in Paris time
smo schedule 12 -a <linkedin-account-id> -s fixed_slots --slot 09:00 --slot 17:00 -z Europe/Paris

# Random time inside a window
smo schedule 12 -a <account-id> -s randomized_window --window 07:00-12:00 -z Europe/Paris

# Best hour according to past engagement
smo schedule 12 -a <account-id> -s weighted_by_engagement

# Exact time (overrides the strategy)
smo schedule 12 -a <account-id> --at 2026-11-20T08:30:00 -z Europe/Paris
```

MCP: the `schedule_post` tool takes the same options (see [MCP.md](MCP.md)).

### Strategies

| Strategy | Chooses | Notes |
|---|---|---|
| `fixed_slots` | The earliest `HH:MM` slot later than now, today or tomorrow | Default slots: `09:00`, `17:00` |
| `randomized_window` | A random moment inside `HH:MM-HH:MM` | Today if the window has not ended, otherwise tomorrow. Default window `09:00-12:00` |
| `weighted_by_engagement` | The hour with the best decayed `likes + reach` for the item's platform | Newest snapshot per post, 30-day half-life, at least 3 posts per hour. Exploits the best hour and explores another daytime hour 15% of the time; without qualified history it falls back to `09:00` |

All times are interpreted in the timezone given with `-z` / `timezone`
(IANA name, for example `Europe/Paris`). Stored times are UTC.

`--at` must be in the future; a past time is refused.

## 3. The dispatcher

The scheduler is APScheduler, running in one of two places:

- **Inside the web server** (`python -m smo.main` or the container). It starts
  with the application.
- **`smo daemon`**, a foreground process for a machine without the web server.

Each job publishes to the platform of its content item (`linkedin` or `wordpress`).
Both are safe to run at the same time: a job is claimed with an atomic
`UPDATE ... WHERE status = 'pending'`, so it is published once.

Every **30 seconds** the dispatcher runs every pending job whose `run_at` has
passed. A job is skipped while its platform is paused or its circuit breaker is open.

Publishing only happens while one of these processes is running. Jobs queued
while nothing is running wait in the database until one starts.

## 4. Job statuses

| Status | Meaning |
|---|---|
| `pending` | Waiting for `run_at` (or for a retry time) |
| `running` | Claimed by the dispatcher |
| `completed` | Published (or written as a dry-run payload) |
| `failed` | Permanent error (bad content, missing token, invalid request). Content becomes `failed` |
| `dead_letter` | Transient errors used up all attempts. Content becomes `failed` |
| `cancelled` | Cancelled because the item was published with `publish_now` |

`jobs.last_error` holds the exception class name only (for example
`ConnectError`), never the message, to avoid leaking data.

## 5. Retries (FR-11)

Only failures where LinkedIn clearly did not accept the post are retried:

| Error | Retried? |
|---|---|
| Connection refused or connect timeout | Yes |
| HTTP `429 Too Many Requests` | Yes |
| HTTP `5xx` | Yes |
| Read timeout | **No.** LinkedIn may already have created the post, and a retry would publish it twice |
| Any other error (4xx, missing token, moderation) | No |

Retry policy:

- Maximum **3 attempts** in total.
- Delay before attempt *n+1*: `60 s × 2^(n-1)`, capped at 1 hour, plus up to 10 % random jitter.
- While a job waits for a retry the content stays `scheduled`.

## 6. Circuit breaker and pauses

Per platform, the breaker counts **consecutive retryable failures**:

- After **3** consecutive retryable failures, publications to that platform are held for **15 minutes**.
- A successful publication resets the counter.
- Held jobs stay `pending` and run after the cooldown.

Manual pause: `pause_platform` (MCP) stops publishing until `resume_platform`.
Resuming also clears the breaker. `get_health` reports each platform as
`active`, `paused` or `circuit_open`.

## 7. Metrics (FR-13)

- Every **6 hours**, and on demand with `trigger_metrics_poll`, SMO reads the
  engagement of every published LinkedIn post that has a platform post id.
- Each reading is stored as a row in `metric_snapshots`. Nothing is written back
  to the platform, and comments are not read.
- `get_metrics` returns the latest snapshots.
- `weighted_by_engagement` is the self-optimizing strategy. It ranks hours of the
  day from `metric_snapshots` with three safeguards:
  - **One observation per post**: only the newest snapshot of a post is used, so
    polling every 6 hours does not count the same post several times.
  - **Recency**: older posts are discounted with a 30-day half-life, so a new
    reading at an hour quickly replaces stale numbers.
  - **Per platform**: a post on X is scheduled from X history, not from LinkedIn's.
- Hours with fewer than **3** posts are ignored (no overfitting to a single lucky
  post). With no qualifying hour the schedule falls back to `09:00`.
- The strategy **exploits** the best hour most of the time and **explores** another
  daytime hour (07:00–21:59) 15% of the time. Exploration keeps collecting data, so
  the ranking can move instead of locking onto its first peak.
- Constants live in `smo/services/scheduling_service.py`
  (`ENGAGEMENT_HALF_LIFE_DAYS`, `MIN_ENGAGEMENT_SAMPLES`, `EXPLORATION_RATE`).

Limits:

- Through the LinkedIn social-actions endpoint only **likes** are available.
  `views` and `reach` stay at `0` until organization analytics access is granted.
- The endpoint and the `x-restli-id` response header have not yet been checked
  against a live LinkedIn application. Verify them on a real account before
  relying on the numbers.

## 8. Limits of this version

- No time-zone aware calendar (holidays, per-day slots).
- The weighting is a simple average per hour, not a learned model.
- Retries are not re-queued after a restart in the middle of an attempt: a job
  left `running` by a crash stays `running` and must be reset by hand.
