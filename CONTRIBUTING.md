# Contributing to Shodh a Code

Thank you for reviewing and contributing to **Shodh a Code**!

## Development Workflow

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/niladripalmca24-cyber/Shodh-A.I.-backend-assessment.git
   cd Shodh-A.I.-backend-assessment
   ```

2. **Boot Coordinated Stack**:
   ```bash
   docker compose up --build
   ```

3. **Running Database Migrations**:
   ```bash
   alembic upgrade head
   ```

4. **Running Automated Tests**:
   ```bash
   pytest -v
   # Or via Docker:
   docker compose --profile test run --rm test
   ```

5. **Code Standards**:
   - Ensure all database queries preserve immutable versioning foreign keys.
   - Any worker changes must honor the CAS fencing lease token protocol (`WHERE lease_token = :token AND status = 'RUNNING'`).
   - All participant-facing endpoints must redact hidden test inputs, outputs, and peer participant code.
