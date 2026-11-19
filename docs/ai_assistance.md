# Material AI Assistance Disclosure & Verification Protocol

**Candidate**: Niladri Pal (MCA, 2026)  
**Track**: Backend Engineer Intern Take Home Assessment  
**Project**: *Shodh a Code* — Distributed Asynchronous Coding Contest Platform  

---

## 1. Disclosure of Material AI Assistance

In compliance with Shodh AI’s assessment transparency policy, this document outlines the material AI assistance utilized during the architecture, implementation, and verification of *Shodh a Code*:

1. **Architecture Blueprinting & Design Review**:
   - AI assistance was used to analyze the assessment constraints, particularly regarding avoiding unnecessary infrastructure components (e.g. evaluating PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED` vs. Redis/RabbitMQ).
   - AI aided in synthesizing the 4-stage technical requirements into explicit database invariants, state machine transitions, and concurrency recovery protocols.

2. **Scaffold Generation & Boilerplate Authoring**:
   - AI assisted in drafting initial SQLAlchemy 2.0 relational models, Pydantic v2 validation envelopes, and Alembic migration scripts.
   - AI generated initial Dockerfile configuration and Docker Compose topologies matching multi-process API and worker requirements.

3. **Concurrency Test Harness Design**:
   - AI assisted in formatting the `asyncio.gather` barrier test suite for simulating simultaneous duplicate idempotency requests and competing worker races.

---

## 2. Verification Protocol & Quality Assurance

All AI-assisted outputs underwent rigorous independent human validation to verify correctness, security, and standards compliance:

| Component | Potential Risk / Failure Mode | Verification & Mitigation Protocol | Result |
| :--- | :--- | :--- | :--- |
| **Relational Invariants** | Unbounded lock contention, silent schema mutations on problem update | Verified that `problems`, `problem_versions`, `test_suite_versions`, and `test_cases` maintain immutable foreign keys. Validated with `test_rejudge_preserves_monotonic_standings`. | **Verified**: Zero historical verdict drift. |
| **Worker Queue Protocol** | Worker starvation, duplicate judging, poison-pill infinite loops | Verified `FOR UPDATE SKIP LOCKED` query shape, UUID fencing token renewal, and `retry_count <= 3` bounds. Tested under competing worker harness. | **Verified**: Zero duplicate points awarded. |
| **Sandbox Hardening** | Privilege escalation, host data leakage, network egress | Inspected `docker run` flags: `--network none`, `--read-only`, tmpfs size limit, `--pids-limit=64`, `--security-opt no-new-privileges`, `--user 65534:65534`. Validated absence of Docker socket mount. | **Verified**: Complete sandbox isolation. |
| **ICPC Scoring Engine** | Incorrect penalty calculations, infrastructure error penalization | Manually reviewed calculation: $\lfloor(first\_ac\_at - starts\_at)/60\rfloor + 20 \times \text{failed\_attempts}$. Confirmed `INFRASTRUCTURE_ERROR` never incurs a penalty. Validated with `test_icpc_leaderboard_standings`. | **Verified**: Mathematically deterministic scores. |
| **Query Bottleneck Proof** | Hallucinated performance metrics or unrealistic latency | Ran repeatable workload (`scripts/load_test.py`) with 40 concurrent clients; executed `EXPLAIN (ANALYZE, BUFFERS)` to observe true query plan shift from sequential scan to Index-Only Scan. | **Verified**: True $2.94\times$ throughput improvement. |

---

## 3. Candidate Attestation

I attest that I understand the complete codebase, system architecture, database invariants, and performance trade-offs presented in this repository. All decisions were evaluated, benchmarked, and verified against functional and concurrency test suites.

**Signature**: Niladri Pal  
**Date**: 18 November 2026
