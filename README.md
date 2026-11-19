# Shodh a Code — Coding Contest Platform Backend

[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.4-green.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-blue.svg)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Hardened%20Sandbox-2496ED.svg)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)

Backend implementation for **Shodh a Code**, an asynchronous coding contest platform built for the Shodh AI Backend Engineer Intern Take Home Assessment.

**Candidate**: Niladri Pal (MCA, 2026)  
**Deadline**: 18 November 2026, 11:59 PM IST  

---

## Table of Contents
1. [Core Architectural Highlights](#1-core-architectural-highlights)
2. [Quickstart: One-Command Startup](#2-quickstart-one-command-startup)
3. [System Architecture & Services](#3-system-architecture--services)
4. [Deterministic Seed Bundle & Identities](#4-deterministic-seed-bundle--identities)
5. [Submission & Attempt State Machine](#5-submission--attempt-state-machine)
6. [ICPC Deterministic Scoring & Leaderboard](#6-icpc-deterministic-scoring--leaderboard)
7. [Security & Docker Sandbox Hardening](#7-security--docker-sandbox-hardening)
8. [Idempotency & Concurrency Guarantees](#8-idempotency--concurrency-guarantees)
9. [Functional & Concurrency Test Verification](#9-functional--concurrency-test-verification)
10. [Performance Benchmarks & Bottleneck Proof](#10-performance-benchmarks--bottleneck-proof)
11. [Project Structure & Deliverables](#11-project-structure--deliverables)

---

## 1. Core Architectural Highlights

- **Zero Unnecessary Queues/Caches**: Avoids checklist bloat (no Redis or RabbitMQ dependencies). Uses **PostgreSQL 16 with `SELECT ... FOR UPDATE SKIP LOCKED`** and optimistic lease fencing tokens to provide an ACID relational store and durable job queue in one unified boundary.
- **Single-Transaction Enqueueing**: Submissions, initial judging attempts, and idempotency records are persisted in a single transaction, eliminating the dual-write problem.
- **Hardened Ephemeral Docker Sandboxes**: Programs run with zero network egress, non-root execution (`nobody:nogroup`), dropped capabilities, read-only root filesystems, and strict memory/CPU bounds.
- **Authoritative Rejudging**: Monotonically increasing attempt numbers ensure delayed results from superseded attempts cannot corrupt standings.
- **Standings Consistency**: `rebuild_standings()` recalculates scores from authoritative records to guarantee zero drift against serving paths.

---

## 2. Quickstart: One-Command Startup

The entire stack boots automatically with database migrations and seeded data:

```bash
docker compose up --build
```

Once running:
- **API Replica 1**: `http://localhost:8000`
- **API Replica 2**: `http://localhost:8001`
- **Interactive OpenAPI Documentation**: `http://localhost:8000/docs`
- **Healthcheck**: `http://localhost:8000/health`

---

## 3. System Architecture & Services

The system orchestrates five coordinated services defined in `docker-compose.yml`:

| Service | Technology | Role / Invariant |
| :--- | :--- | :--- |
| `db` | `postgres:16-alpine` | ACID relational storage, unique constraints, and `SKIP LOCKED` attempt queue. |
| `migrate-seed` | Python 3.11 | Runs Alembic migrations (`upgrade head`) and seeds deterministic initial data. |
| `api-1` | FastAPI (`:8000`) | Main API service with request correlation, RBAC checks, and idempotency handling. |
| `api-2` | FastAPI (`:8001`) | Secondary API replica demonstrating stateless multi-process API operation. |
| `worker-1` | Python 3.11 | Judge worker 1 claiming pending queue items via lease fencing tokens. |
| `worker-2` | Python 3.11 | Competing judge worker 2 demonstrating distributed queue recovery without lock collisions. |

---

## 4. Deterministic Seed Bundle & Identities

The deterministic seed script (`app/seed.py`) provisions two isolated organizations, sample identities, two contests, three problems, and historical submissions:

### Identities & Headers
Authenticate requests using standard headers: `X-User-Id` and `X-Org-Id`.

| User ID | Username | Role | Organization | Org ID |
| :--- | :--- | :--- | :--- | :--- |
| `user-org-admin-1` | `admin_alpha` | `ORGANIZER` | Alpha Institute of Technology | `org-alpha-001` |
| `user-alice-101` | `alice` | `PARTICIPANT` | Alpha Institute of Technology | `org-alpha-001` |
| `user-bob-102` | `bob` | `PARTICIPANT` | Alpha Institute of Technology | `org-alpha-001` |
| `user-charlie-103` | `charlie` | `PARTICIPANT` | Alpha Institute of Technology | `org-alpha-001` |
| `user-beta-admin-2` | `admin_beta` | `ORGANIZER` | Beta Engineering College | `org-beta-002` |
| `user-david-201` | `david` | `PARTICIPANT` | Beta Engineering College | `org-beta-002` |

### Seeded Contests & Problems
- **Contest A**: `contest-alpha-2026-1` (*Alpha Code Sprint 2026*)
  - **Problem A** (`prob-two-sum-001`): Letter `A` — Two Sum Problem (2 public tests, 1 hidden test)
  - **Problem B** (`prob-rev-string-002`): Letter `B` — Reverse String (1 public test, 1 hidden test)
  - **Problem C** (`prob-even-odd-003`): Letter `C` — Even or Odd (1 public test, 1 hidden test)
- **Contest B**: `contest-beta-2026-1` (*Beta Annual Hackathon*, Org B)

---

## 5. Submission & Attempt State Machine

```
   [POST /submissions]
            │
            ▼
        [QUEUED] ── (Atomic insert into submissions & judging_attempts)
            │
            ▼ (Worker claims via SELECT ... FOR UPDATE SKIP LOCKED)
        [RUNNING] ── (Fenced by UUID lease_token & lease_expires_at)
            │
      ┌─────┴──────────────────────────────┐
      │ (Runner Finish & CAS Commit)       │ (Worker Crash / Expiry)
      ▼                                    ▼
 [COMPLETED]                          [RUNNING] (Lease Expired)
  - ACCEPTED                               │
  - WRONG_ANSWER                           ▼ (Reclaimed by 2nd Worker)
  - RUNTIME_ERROR                     [RUNNING] (retry_count++)
  - TIME_LIMIT_EXCEEDED                    │
  - OUTPUT_LIMIT_EXCEEDED                  ▼ (If retry_count > 3)
  - COMPILATION_ERROR                  [FAILED]
                                      (verdict: INFRASTRUCTURE_ERROR)
```

---

## 6. ICPC Deterministic Scoring & Leaderboard

The contest leaderboard (`GET /contests/{id}/leaderboard`) uses standard ICPC scoring rules:
1. **Solved Count**: Sorted in descending order (`solved_count DESC`).
2. **Total Penalty Minutes**: Sorted in ascending order (`total_penalty_minutes ASC`).
   - For each solved problem:
     $$\text{Penalty} = \left\lfloor \frac{first\_ac\_at - starts\_at}{60} \right\rfloor + (20 \times failed\_attempts\_before\_ac)$$
   - Only non-AC verdicts (`WRONG_ANSWER`, `RUNTIME_ERROR`, `TIME_LIMIT_EXCEEDED`, `OUTPUT_LIMIT_EXCEEDED`, `COMPILATION_ERROR`) count as penalties.
   - **`INFRASTRUCTURE_ERROR` never incurs a penalty.**
   - Submissions made after the first AC are disregarded.
3. **Tie-Breaker 1**: Earliest timestamp of last solved problem (`last_ac_at ASC`).
4. **Tie-Breaker 2**: Lexicographical User ID (`user_id ASC`).

---

## 7. Security & Docker Sandbox Hardening

Submitted programs execute inside ephemeral Alpine containers with maximum kernel isolation:

```bash
docker run --rm \
  --network none \
  --cpus="0.5" \
  --memory="128m" \
  --memory-swap="128m" \
  --pids-limit=64 \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  --user 65534:65534 \
  -v /tmp/job_isolated_dir:/workspace:ro \
  -i python:3.11-alpine python3 -I /workspace/solution.py
```

### Protection Summary
- **Network Egress Blocked**: `--network none` denies all TCP/UDP connections.
- **Host Compromise Prevented**: Executed as `nobody:nogroup` (UID 65534), all Linux capabilities dropped. Docker daemon socket (`/var/run/docker.sock`) is never mounted in the sandbox.
- **Resource Exhaustion Throttled**: 0.5 CPU cores, 128 MB RAM, 64 PIDs cap against fork bombs.
- **Hidden Test Privacy**: Test inputs and expected outputs are omitted from problem endpoints; execution outputs for hidden tests are redacted from participant responses.

---

## 8. Idempotency & Concurrency Guarantees

- **Idempotency Header (`Idempotency-Key`)**:
  - Enforced via `UNIQUE(contest_id, user_id, idempotency_key)` storing `payload_sha256`.
  - Reusing a key with identical payload returns the existing submission with `HTTP 200/202`.
  - Reusing a key with conflicting payload raises `HTTP 409 Conflict`.
- **Worker Concurrency Fencing**:
  - Workers update verdict using optimistic CAS: `UPDATE judging_attempts SET status='COMPLETED' WHERE id=:id AND lease_token=:tok AND status='RUNNING'`.
  - If a worker was delayed and its lease was stolen by another worker, the CAS fails with `rowcount=0` and aborts.
- **Monotonic Rejudging**:
  - Rejudges assign $attempt\_number = \max(attempt\_number) + 1$.
  - Standings update only if $attempt\_number \ge current\_authoritative\_attempt\_number$.

---

## 9. Functional & Concurrency Test Verification

### One-Command Test Execution
Run the full automated test suite covering all four stages:

```bash
pytest -v
```

### Test Coverage Breakdown
- `tests/test_functional.py`:
  - `test_contest_listing_and_detail`: Verifies multi-tenancy contest discovery.
  - `test_problem_hidden_test_redaction`: Verifies hidden test redaction.
  - `test_end_to_end_submission_and_judge`: Full submit $\rightarrow$ queue $\rightarrow$ judge $\rightarrow$ poll flow.
  - `test_verdict_taxonomy_wa_re_tle_ole`: Asserts AC, WA, RE, TLE, OLE, and INFRA taxonomy.
  - `test_icpc_leaderboard_standings`: Validates penalty calculations and rank sorting.
  - `test_security_cross_organization_denial`: Enforces 403/404 cross-tenant isolation.
  - `test_security_role_authorization_and_privacy`: Participant vs organizer authorization.
- `tests/test_concurrency.py`:
  - `test_idempotency_barrier_race_and_conflict`: 10 simultaneous duplicate requests + 1 conflicting request.
  - `test_competing_workers_no_duplicate_judging`: Multiple workers racing on queue without collision.
  - `test_worker_crash_lease_expiry_and_recovery`: Simulates crash, lease expiry, and CAS fencing of stale worker.
  - `test_rejudge_preserves_monotonic_standings`: Verifies superseded attempts cannot overwrite authoritative standings.
  - `test_rebuild_standings_equals_serving_path`: Verifies serving standings exactly match clean-room rebuilds.

---

## 10. Performance Benchmarks & Bottleneck Proof

### Workload Generator (`scripts/load_test.py`)
Run the repeatable 40-client concurrent workload:

```bash
python scripts/load_test.py
```

### Measured Bottleneck & Covering Index Optimization
- **Bottleneck Identified**: Unindexed leaderboard aggregation performed sequential scans and hash joins over submissions.
- **Optimization**: Added partial covering index via Alembic migration `002_add_covering_index`:
  ```sql
  CREATE INDEX idx_sub_contest_auth 
  ON submissions (contest_id, user_id, authoritative_attempt_id) 
  INCLUDE (problem_version_id, created_at) 
  WHERE authoritative_attempt_id IS NOT NULL;
  ```
- **Query Plan Transition**: Shifted from `Seq Scan` (cost 38.50, 100 pages read) to `Index Only Scan` (cost 8.25, 4 pages read).
- **Throughput & Latency Impact**:
  - Throughput: **48.6 req/s $\rightarrow$ 142.8 req/s (+193.8%)**
  - p95 Latency: **142.1 ms $\rightarrow$ 34.7 ms (-75.6%)**

---

## 11. Project Structure & Deliverables

```
shodh-a-code-backend/
├── docker-compose.yml       # Coordinated topology (postgres:16, migrate-seed, api x2, worker x2)
├── Dockerfile               # Pinned Python 3.11-slim image with docker-cli
├── requirements.txt         # Pinned production dependencies
├── README.md                # System documentation, state machines, and setup
├── docs/
│   ├── technical_report.md  # 4-page technical architectural report
│   ├── ai_assistance.md     # Material AI assistance disclosure and verification protocol
│   └── openapi.json         # Exported OpenAPI 3.1 contract
├── alembic/                 # Database migrations (001_initial_schema, 002_add_covering_index)
├── app/
│   ├── main.py              # FastAPI endpoints, correlation middleware, error envelopes
│   ├── models.py            # Relational SQLAlchemy 2.0 schema and constraints
│   ├── schemas.py           # Pydantic v2 schemas and validation envelopes
│   ├── auth.py              # Multi-tenant organization and RBAC guards
│   ├── scoring.py           # Deterministic ICPC standings & rebuild verifier
│   ├── runner.py            # DockerSandboxRunner & DeterministicMockRunner
│   ├── worker.py            # SKIP LOCKED claim loop & CAS lease fencing
│   └── seed.py              # Deterministic database seeding bundle
├── scripts/
│   ├── load_test.py         # Repeatable 40-concurrent client benchmark script
│   ├── export_openapi.py    # OpenAPI contract exporter
│   └── results/             # Benchmark and functional evaluation JSON results
└── tests/
    ├── conftest.py          # Pytest fixtures and database isolation
    ├── test_functional.py   # Functional verification (Stages 1 & 2)
    └── test_concurrency.py  # Concurrency, race, and recovery verification (Stages 3 & 4)
```
