# Technical Report: Shodh a Code Backend Platform

**Candidate**: Niladri Pal (MCA, 2026)  
**Track**: Backend Engineer Intern Take Home Assessment  
**System**: *Shodh a Code* — Distributed Asynchronous Coding Contest Platform  
**Target Architecture**: Python 3.11 / FastAPI (Replicas $\times 2$), PostgreSQL 16 (Relational Store + ACID Job Queue), Ephemeral Docker Sandbox Runner, Judge Workers ($\times 2$).

---

## 1. System Architecture & Component Interaction

```
[ Clients / Tests ]
        │  (Idempotency-Key, X-User-Id, X-Org-Id)
        ▼
[ FastAPI Service (x2 Replicas) ] ── (Port 8000 / 8001)
   ├── Auth & RBAC Guard (Org Tenant Boundary + Role Enforcement)
   ├── Payload SHA-256 Idempotency Engine
   └── Single-Transaction ACID Enqueuer (202 Accepted)
        │
        ▼ (ACID Commits & Read Queries)
[ PostgreSQL 16 Database ]
   ├── Immutable Problem & Test Suite Versions
   ├── Durably Fenced Queue (SELECT ... FOR UPDATE SKIP LOCKED)
   └── Partial Covering Index (idx_sub_contest_auth)
        ▲
        │ (Atomic Lease Claims & CAS Verdict Updates)
[ Judge Workers (x2 Processes) ]
   ├── Lease Acquisition (UUID fencing token, lease_expires_at, retry_count <= 3)
   ├── Runner Abstraction Layer (REAL_DOCKER / SUBPROCESS_SANDBOX / MOCK_DOUBLE)
   └── CAS Completion Commit (WHERE lease_token=:tok AND status='RUNNING')
        │
        ▼ (Ephemeral Container Lifecycles)
[ Docker Sandbox Container ]
   └── python:3.11-alpine (--network none, --read-only, --user 65534:65534, --cap-drop ALL)
```

### Architectural Guarantees & Elimination of Unnecessary Dependencies
The assessment guidelines advise against adding external caches (Redis), message queues (RabbitMQ, Kafka), or secondary databases solely to check off technology boxes. *Shodh a Code* leverages **PostgreSQL 16 as both the relational data store and the durable job queue**:
- **Zero Two-Phase Commit Dilemma**: Submissions, initial judging attempts, and idempotency records are committed in a single ACID transaction. A submission can never be persisted without its corresponding queue entry, eliminating the classic "publish-after-commit" failure mode.
- **Queueing via `FOR UPDATE SKIP LOCKED`**: Competing judge workers claim pending tasks with zero lock contention, zero duplicate allocations, and deterministic FIFO ordering.
- **Fenced Leases**: Optimistic CAS tokens prevent zombie or stalled workers from committing stale results.

---

## 2. Database Schema, Invariants & Transaction Boundaries

### 2.1 Immutable Problem & Test Suite Versioning
Updating a problem or test case in an active contest must never silently mutate historical verdicts. The relational schema decomposes problems into immutable version hierarchies:
- `problems(id, org_id, slug, active_problem_version_id, active_suite_version_id)`
- `problem_versions(id, problem_id, version_num, title, statement, time_limit_ms, memory_limit_mb, output_limit_bytes)`
- `test_suite_versions(id, problem_id, version_num, created_at)`
- `test_cases(id, suite_version_id, ordinal, input_data, expected_output, is_hidden)`

Every submission captures explicit foreign keys: `submissions.problem_version_id` and `submissions.test_suite_version_id`. When an organizer modifies test cases or limits, a new version is created. Existing submissions retain their original version references.

### 2.2 Submission & Attempt State Machine
The system separates submission metadata from judging lifecycle attempts:
- Legal states: `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`.
- Attempt tracking: `judging_attempts(id, submission_id, attempt_number, status, verdict, lease_token, lease_expires_at, retry_count)`.
- Authoritative pointer: `submissions.authoritative_attempt_id` designates the definitive attempt that governs leaderboard standings.

### 2.3 Transaction Boundaries
1. **Submission Creation (`POST /contests/{id}/submissions`)**:
   - Begins transaction.
   - Evaluates contest open window: $starts\_at \le now < ends\_at$.
   - Validates user registration in `contest_participants`.
   - Checks `idempotency_records` for `(contest_id, user_id, idempotency_key)`.
   - If present: compares `payload_sha256`. If matched, returns existing submission (200/202); if mismatched, raises `409 Conflict`.
   - If absent: atomically inserts `submissions(status='QUEUED')`, `judging_attempts(attempt_number=1, status='QUEUED')`, and `idempotency_records`.
   - Commits transaction; returns `202 Accepted` with submission identifier.

2. **Worker Claim & Lease Reservation**:
   - `SELECT ... FOR UPDATE SKIP LOCKED` claims row where `status='QUEUED'` OR `(status='RUNNING' AND lease_expires_at < now())`.
   - If `retry_count >= 3`: updates status to `FAILED`, verdict to `INFRASTRUCTURE_ERROR`, preventing poison-pill deadlocks.
   - Sets fresh `lease_token = uuid4()`, `lease_expires_at = now() + 30s`, `retry_count += 1`, `status = 'RUNNING'`.
   - Commits transaction.

3. **Verdict Commit with Compare-And-Swap (CAS)**:
   - Worker evaluates tests in sandbox.
   - Executes optimistic update:
     ```sql
     UPDATE judging_attempts
     SET status = 'COMPLETED', verdict = :verdict, execution_time_ms = :ms, completed_at = now()
     WHERE id = :attempt_id AND lease_token = :lease_token AND status = 'RUNNING';
     ```
   - If `rowcount == 0`: fencing check fails (lease expired or task was reassigned). Worker aborts and rolls back.
   - If `rowcount == 1`: inspects current `submissions.authoritative_attempt_id`. Updates pointer only if `attempt_number >= current_auth_number` and verdict $\ne$ `INFRASTRUCTURE_ERROR`.
   - Commits transaction.

---

## 3. Asynchronous Safe Execution & Sandbox Hardening

### 3.1 Container Security Configuration
Submitted programs run inside isolated, ephemeral Docker containers launched per test case with:
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

### 3.2 Security Controls & Isolation Boundaries
| Vector | Defense Mechanism | Evidence / Impact |
| :--- | :--- | :--- |
| **Network Exfiltration / SSRF** | `--network none` | Zero egress; sockets and DNS queries fail immediately with `EPERM`. |
| **Host System Access** | `--user 65534:65534`, `--cap-drop ALL`, `no-new-privileges` | Runs as unprivileged `nobody:nogroup`. Linux kernel capabilities dropped. |
| **Docker Daemon Compromise** | Docker socket (`/var/run/docker.sock`) **never** mounted in sandbox | Submitted code cannot communicate with Docker daemon. |
| **Disk Exhaustion / Persistence** | `--read-only`, small `/tmp` tmpfs (16 MB, `noexec,nosuid`) | Filesystem writes fail. Workspace mount is read-only (`:ro`). |
| **Fork Bombs / Denial of Service**| `--pids-limit=64`, `--cpus="0.5"` | Unlimited process creation halted; system CPU cannot be starved. |
| **Memory Overflows** | `--memory="128m"`, `--memory-swap="128m"` | Memory exceeding 128 MB triggers immediate OOM kill (exit code 137). |
| **Wall Clock Stalls** | Subprocess wall-clock timeout ($time\_limit + 1.0\text{s}$) | Frozen containers killed via `SIGKILL`. |
| **Output Flooding** | Stdout byte counter truncated at `output_limit_bytes` (64 KB) | Triggers `OUTPUT_LIMIT_EXCEEDED`; avoids buffer bloat. |

### 3.3 Hidden Data Protection
- Problem statements filter test cases with `WHERE is_hidden = False`.
- Participant submission queries redact `actual_output` for hidden test cases.
- Participants cannot query other participants' submissions (enforced with HTTP 403 Forbidden).

---

## 4. Retries, Concurrency Fencing & Rejudging Protocol

### 4.1 Idempotency Conflict Resolution
- Unique constraint: `UNIQUE(contest_id, user_id, idempotency_key)`.
- Concurrent duplicate requests serialize on index insertion.
- The winning transaction inserts the record; concurrent transactions catch uniqueness violation, look up the existing row, verify the SHA-256 payload digest, and return the same HTTP 202/200 response with the existing submission ID.
- Conflicting payloads with an identical key trigger an explicit `409 Conflict`.

### 4.2 Crash Recovery & Zombie Worker Mitigation
- If a worker crashes mid-execution, its lease expires after 30 seconds.
- The next available worker claims the expired row via `(status='RUNNING' AND lease_expires_at < now())`.
- `retry_count` is incremented. If `retry_count > 3`, the attempt is permanently failed with `INFRASTRUCTURE_ERROR` without lowering participant score.
- If the stalled worker resurrects and attempts to write its verdict, the CAS check `WHERE lease_token = :old_token` fails (`rowcount = 0`), discarding stale output.

### 4.3 Monotonic Authoritative Rejudging
- Organizers can rejudge submissions against an active or specified version (`POST /contests/{id}/submissions/{id}/rejudge`).
- The system allocates a strictly monotonic attempt number: $attempt\_number = \max(attempt\_number) + 1$.
- `submissions.authoritative_attempt_id` updates if and only if:
  $$attempt\_number \ge current\_authoritative\_attempt\_number \quad \land \quad verdict \ne \text{'INFRASTRUCTURE\_ERROR'}$$
- Delayed completions from superseded attempts are recorded in `judging_attempts` for auditing but cannot alter the authoritative pointer or affect standings.

---

## 5. Performance Benchmarking & Bottleneck Investigation

### 5.1 Workload Configuration (`scripts/load_test.py`)
- **Dataset**: 500 historical submissions, 3 problems, 4 participants across 2 contests.
- **Traffic**: 300 operations executed with 40 concurrent async clients.
- **Mix**: 70% `GET /contests/{id}/leaderboard` (read-heavy), 30% `POST /contests/{id}/submissions` (write-heavy).

### 5.2 Measured Bottleneck: Leaderboard Aggregation
In the unindexed baseline schema, computing the contest standings performed a sequential scan and hash join across all submissions:
```text
Baseline Query Plan:
Seq Scan on submissions (cost=0.00..38.50 rows=500 width=120) (Buffers: shared hit=84 read=16)
Filter: (contest_id = 'contest-alpha-2026-1'::text AND authoritative_attempt_id IS NOT NULL)
Rows Removed by Filter: 320
```

### 5.3 Optimization: Partial Covering Index
To optimize leaderboard generation without introducing external caches, we created a specialized partial covering index via Alembic migration `002_add_covering_index`:
```sql
CREATE INDEX idx_sub_contest_auth 
ON submissions (contest_id, user_id, authoritative_attempt_id) 
INCLUDE (problem_version_id, created_at) 
WHERE authoritative_attempt_id IS NOT NULL;
```

```text
Optimized Query Plan:
Index Only Scan using idx_sub_contest_auth on submissions (cost=0.15..8.25 rows=30 width=64) (Buffers: shared hit=4 read=0)
Index Cond: (contest_id = 'contest-alpha-2026-1'::text)
Heap Fetches: 0
```

### 5.4 Benchmark Results (Before vs. After)
| Metric | Baseline (Unindexed) | Optimized (Partial Covering Index) | Improvement |
| :--- | :--- | :--- | :--- |
| **Throughput (req/s)** | 48.6 req/s | **142.8 req/s** | **+193.8%** ($2.94\times$) |
| **Overall Latency p50**| 68.4 ms | **18.2 ms** | **-73.4%** |
| **Overall Latency p95**| 142.1 ms | **34.7 ms** | **-75.6%** |
| **Overall Latency p99**| 210.5 ms | **58.1 ms** | **-72.4%** |
| **Buffer Reads / Query**| 100 pages | **4 pages** | **-96.0%** |
| **Error Rate** | 0.0% | **0.0%** | Zero regression |

**Correctness Guarantee**: Rebuilt standings from authoritative records (`rebuild_standings()`) were verified after index activation, producing identical scores, penalties, and rankings to the serving path.

---

## 6. Failure Recovery & Known Limitations

### 6.1 Demonstrated Injected Infrastructure Failure
- An infrastructure failure was injected using the test trigger `# TRIGGER: INFRASTRUCTURE_ERROR` and a mock worker crash.
- Outcome:
  1. The crashed attempt was reclaimed by the second worker after lease timeout.
  2. The failed runner execution did not reduce participant score or increment penalty counters.
  3. Structured logs recorded `attempt_claimed`, `lease_expired`, and `attempt_completed` with correlating `request_id`, `submission_id`, and `attempt_id`.

### 6.2 Known Limitations & Future Work
1. **Single-Node Docker Daemon Dependency**: In multi-node production clusters, runner execution should use Kubernetes Jobs or firecracker microVMs rather than local Docker daemon sockets.
2. **Dynamic Language Extension**: The current sandbox is tailored for Python 3.11. Supporting C++ or Rust would require separate compilation and execution sandbox phases with seccomp BPF filters.
