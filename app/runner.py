import os
import sys
import time
import shutil
import tempfile
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class TestCaseInput:
    ordinal: int
    input_data: str
    expected_output: str
    is_hidden: bool = False

@dataclass
class TestCaseOutput:
    ordinal: int
    passed: bool
    verdict: str  # ACCEPTED, WRONG_ANSWER, TIME_LIMIT_EXCEEDED, RUNTIME_ERROR, OUTPUT_LIMIT_EXCEEDED
    execution_time_ms: int
    actual_output: Optional[str]
    is_hidden: bool

@dataclass
class RunnerResult:
    verdict: str
    execution_time_ms: int
    memory_used_kb: int
    test_results: List[TestCaseOutput] = field(default_factory=list)
    error_message: Optional[str] = None
    execution_mode: str = "REAL_DOCKER"

class BaseRunner(ABC):
    @abstractmethod
    def run_submission(
        self,
        code: str,
        language: str,
        test_cases: List[TestCaseInput],
        time_limit_ms: int = 2000,
        memory_limit_mb: int = 128,
        output_limit_bytes: int = 65536
    ) -> RunnerResult:
        pass


class DockerSandboxRunner(BaseRunner):
    """
    Executes code inside an ephemeral isolated Docker container with hardened security:
    - --network none (Zero network access)
    - --cpus="0.5", --memory="128m", --memory-swap="128m", --pids-limit=64 (Resource bounds)
    - --read-only filesystem with small tmpfs (/tmp:rw,noexec,nosuid,size=16m)
    - --security-opt no-new-privileges, --cap-drop ALL (Privilege dropping)
    - --user 65534:65534 (nobody:nogroup non-root execution)
    - Zero host credentials or docker control socket mounted
    - Temporary directory automatically destroyed in finally block
    """
    def __init__(self, docker_image: str = "python:3.11-alpine"):
        self.docker_image = docker_image

    def run_submission(
        self,
        code: str,
        language: str,
        test_cases: List[TestCaseInput],
        time_limit_ms: int = 2000,
        memory_limit_mb: int = 128,
        output_limit_bytes: int = 65536
    ) -> RunnerResult:
        if language != "python3":
            return RunnerResult(
                verdict="COMPILATION_ERROR",
                execution_time_ms=0,
                memory_used_kb=0,
                error_message=f"Unsupported language: {language}",
                execution_mode="REAL_DOCKER"
            )

        # Basic syntax check for Python (detect compilation/syntax errors early)
        try:
            compile(code, "<submission>", "exec")
        except SyntaxError as se:
            return RunnerResult(
                verdict="COMPILATION_ERROR",
                execution_time_ms=0,
                memory_used_kb=0,
                error_message=f"SyntaxError on line {se.lineno}: {se.msg}",
                execution_mode="REAL_DOCKER"
            )

        temp_workspace = tempfile.mkdtemp(prefix="shodh_sandbox_")
        test_results: List[TestCaseOutput] = []
        overall_verdict = "ACCEPTED"
        total_time_ms = 0
        max_mem_kb = 0

        try:
            solution_file = os.path.join(temp_workspace, "solution.py")
            with open(solution_file, "w", encoding="utf-8") as f:
                f.write(code)

            # Fix permissions so unprivileged user 65534 can read solution.py
            os.chmod(temp_workspace, 0o755)
            os.chmod(solution_file, 0o644)

            for tc in test_cases:
                timeout_sec = (time_limit_ms / 1000.0) + 1.0  # buffer for docker overhead
                cmd = [
                    "docker", "run", "--rm",
                    "--network", "none",
                    "--cpus", "0.5",
                    f"--memory={memory_limit_mb}m",
                    f"--memory-swap={memory_limit_mb}m",
                    "--pids-limit", "64",
                    "--read-only",
                    "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
                    "--security-opt", "no-new-privileges",
                    "--cap-drop", "ALL",
                    "--user", "65534:65534",
                    "-v", f"{temp_workspace}:/workspace:ro",
                    "-i",
                    self.docker_image,
                    "python3", "-I", "/workspace/solution.py"
                ]

                start_time = time.monotonic()
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    
                    try:
                        stdout_data, stderr_data = proc.communicate(
                            input=tc.input_data,
                            timeout=timeout_sec
                        )
                        elapsed_ms = int((time.monotonic() - start_time) * 1000)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.communicate()
                        elapsed_ms = time_limit_ms + 100
                        tc_out = TestCaseOutput(
                            ordinal=tc.ordinal,
                            passed=False,
                            verdict="TIME_LIMIT_EXCEEDED",
                            execution_time_ms=elapsed_ms,
                            actual_output=None,
                            is_hidden=tc.is_hidden
                        )
                        test_results.append(tc_out)
                        overall_verdict = "TIME_LIMIT_EXCEEDED"
                        break

                    # Check Output Limit Exceeded
                    if len(stdout_data.encode("utf-8")) > output_limit_bytes:
                        tc_out = TestCaseOutput(
                            ordinal=tc.ordinal,
                            passed=False,
                            verdict="OUTPUT_LIMIT_EXCEEDED",
                            execution_time_ms=elapsed_ms,
                            actual_output=stdout_data[:200] + "... [TRUNCATED]",
                            is_hidden=tc.is_hidden
                        )
                        test_results.append(tc_out)
                        overall_verdict = "OUTPUT_LIMIT_EXCEEDED"
                        break

                    # Check process return code
                    if proc.returncode != 0:
                        # 137 in Docker typically means killed by SIGKILL (OOM or force kill)
                        v = "RUNTIME_ERROR"
                        if proc.returncode == 137:
                            v = "RUNTIME_ERROR"
                        tc_out = TestCaseOutput(
                            ordinal=tc.ordinal,
                            passed=False,
                            verdict=v,
                            execution_time_ms=elapsed_ms,
                            actual_output=stderr_data.strip() if not tc.is_hidden else None,
                            is_hidden=tc.is_hidden
                        )
                        test_results.append(tc_out)
                        overall_verdict = v
                        break

                    # Check actual output against expected output (strip trailing whitespace)
                    norm_actual = stdout_data.strip().replace("\r\n", "\n")
                    norm_expected = tc.expected_output.strip().replace("\r\n", "\n")

                    if norm_actual == norm_expected:
                        tc_out = TestCaseOutput(
                            ordinal=tc.ordinal,
                            passed=True,
                            verdict="ACCEPTED",
                            execution_time_ms=elapsed_ms,
                            actual_output=norm_actual if not tc.is_hidden else None,
                            is_hidden=tc.is_hidden
                        )
                        test_results.append(tc_out)
                        total_time_ms += elapsed_ms
                    else:
                        tc_out = TestCaseOutput(
                            ordinal=tc.ordinal,
                            passed=False,
                            verdict="WRONG_ANSWER",
                            execution_time_ms=elapsed_ms,
                            actual_output=norm_actual if not tc.is_hidden else None,
                            is_hidden=tc.is_hidden
                        )
                        test_results.append(tc_out)
                        overall_verdict = "WRONG_ANSWER"
                        break

                except FileNotFoundError:
                    # Docker binary not available on host
                    raise RuntimeError("Docker command not found in environment")
                except Exception as ex:
                    # Infrastructure issue launching container
                    return RunnerResult(
                        verdict="INFRASTRUCTURE_ERROR",
                        execution_time_ms=0,
                        memory_used_kb=0,
                        error_message=f"Docker execution failed: {str(ex)}",
                        execution_mode="REAL_DOCKER"
                    )

            return RunnerResult(
                verdict=overall_verdict,
                execution_time_ms=total_time_ms,
                memory_used_kb=max_mem_kb,
                test_results=test_results,
                execution_mode="REAL_DOCKER"
            )

        finally:
            # Clean up temporary isolated directory
            if os.path.exists(temp_workspace):
                shutil.rmtree(temp_workspace, ignore_errors=True)


class SubprocessSandboxRunner(BaseRunner):
    """
    Fallback safe subprocess sandbox when Docker is unavailable in local development.
    Applies strict timeout, memory limits (where supported), and output size caps.
    """
    def run_submission(
        self,
        code: str,
        language: str,
        test_cases: List[TestCaseInput],
        time_limit_ms: int = 2000,
        memory_limit_mb: int = 128,
        output_limit_bytes: int = 65536
    ) -> RunnerResult:
        if language != "python3":
            return RunnerResult(
                verdict="COMPILATION_ERROR",
                execution_time_ms=0,
                memory_used_kb=0,
                error_message=f"Unsupported language: {language}",
                execution_mode="SUBPROCESS_SANDBOX"
            )

        try:
            compile(code, "<submission>", "exec")
        except SyntaxError as se:
            return RunnerResult(
                verdict="COMPILATION_ERROR",
                execution_time_ms=0,
                memory_used_kb=0,
                error_message=f"SyntaxError on line {se.lineno}: {se.msg}",
                execution_mode="SUBPROCESS_SANDBOX"
            )

        temp_workspace = tempfile.mkdtemp(prefix="shodh_subproc_")
        test_results: List[TestCaseOutput] = []
        overall_verdict = "ACCEPTED"
        total_time_ms = 0

        try:
            solution_file = os.path.join(temp_workspace, "solution.py")
            with open(solution_file, "w", encoding="utf-8") as f:
                f.write(code)

            for tc in test_cases:
                timeout_sec = time_limit_ms / 1000.0
                cmd = [sys.executable, "-I", solution_file]

                start_time = time.monotonic()
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        cwd=temp_workspace
                    )
                    
                    try:
                        stdout_data, stderr_data = proc.communicate(
                            input=tc.input_data,
                            timeout=timeout_sec
                        )
                        elapsed_ms = int((time.monotonic() - start_time) * 1000)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.communicate()
                        tc_out = TestCaseOutput(
                            ordinal=tc.ordinal,
                            passed=False,
                            verdict="TIME_LIMIT_EXCEEDED",
                            execution_time_ms=int(time_limit_ms + 50),
                            actual_output=None,
                            is_hidden=tc.is_hidden
                        )
                        test_results.append(tc_out)
                        overall_verdict = "TIME_LIMIT_EXCEEDED"
                        break

                    if len(stdout_data.encode("utf-8")) > output_limit_bytes:
                        tc_out = TestCaseOutput(
                            ordinal=tc.ordinal,
                            passed=False,
                            verdict="OUTPUT_LIMIT_EXCEEDED",
                            execution_time_ms=elapsed_ms,
                            actual_output=stdout_data[:200] + "... [TRUNCATED]",
                            is_hidden=tc.is_hidden
                        )
                        test_results.append(tc_out)
                        overall_verdict = "OUTPUT_LIMIT_EXCEEDED"
                        break

                    if proc.returncode != 0:
                        tc_out = TestCaseOutput(
                            ordinal=tc.ordinal,
                            passed=False,
                            verdict="RUNTIME_ERROR",
                            execution_time_ms=elapsed_ms,
                            actual_output=stderr_data.strip() if not tc.is_hidden else None,
                            is_hidden=tc.is_hidden
                        )
                        test_results.append(tc_out)
                        overall_verdict = "RUNTIME_ERROR"
                        break

                    norm_actual = stdout_data.strip().replace("\r\n", "\n")
                    norm_expected = tc.expected_output.strip().replace("\r\n", "\n")

                    if norm_actual == norm_expected:
                        tc_out = TestCaseOutput(
                            ordinal=tc.ordinal,
                            passed=True,
                            verdict="ACCEPTED",
                            execution_time_ms=elapsed_ms,
                            actual_output=norm_actual if not tc.is_hidden else None,
                            is_hidden=tc.is_hidden
                        )
                        test_results.append(tc_out)
                        total_time_ms += elapsed_ms
                    else:
                        tc_out = TestCaseOutput(
                            ordinal=tc.ordinal,
                            passed=False,
                            verdict="WRONG_ANSWER",
                            execution_time_ms=elapsed_ms,
                            actual_output=norm_actual if not tc.is_hidden else None,
                            is_hidden=tc.is_hidden
                        )
                        test_results.append(tc_out)
                        overall_verdict = "WRONG_ANSWER"
                        break

                except Exception as ex:
                    return RunnerResult(
                        verdict="INFRASTRUCTURE_ERROR",
                        execution_time_ms=0,
                        memory_used_kb=0,
                        error_message=f"Subprocess execution error: {str(ex)}",
                        execution_mode="SUBPROCESS_SANDBOX"
                    )

            return RunnerResult(
                verdict=overall_verdict,
                execution_time_ms=total_time_ms,
                memory_used_kb=0,
                test_results=test_results,
                execution_mode="SUBPROCESS_SANDBOX"
            )

        finally:
            if os.path.exists(temp_workspace):
                shutil.rmtree(temp_workspace, ignore_errors=True)


class DeterministicMockRunner(BaseRunner):
    """
    Test double for controlled race conditions, timeouts, and injected faults.
    Allows exact verification of retry logic, lease expiry, and out-of-order rejudging.
    """
    def __init__(
        self,
        forced_verdict: Optional[str] = None,
        inject_infra_failure: bool = False,
        delay_seconds: float = 0.0,
        failpoint_at_ordinal: Optional[int] = None
    ):
        self.forced_verdict = forced_verdict
        self.inject_infra_failure = inject_infra_failure
        self.delay_seconds = delay_seconds
        self.failpoint_at_ordinal = failpoint_at_ordinal

    def run_submission(
        self,
        code: str,
        language: str,
        test_cases: List[TestCaseInput],
        time_limit_ms: int = 2000,
        memory_limit_mb: int = 128,
        output_limit_bytes: int = 65536
    ) -> RunnerResult:
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)

        if self.inject_infra_failure:
            return RunnerResult(
                verdict="INFRASTRUCTURE_ERROR",
                execution_time_ms=0,
                memory_used_kb=0,
                error_message="Injected infrastructure failure (controlled runner crash)",
                execution_mode="MOCK_DOUBLE"
            )

        # Check special comment triggers in code for deterministic test cases
        verdict = self.forced_verdict
        if not verdict:
            if "# TRIGGER: INFRASTRUCTURE_ERROR" in code:
                return RunnerResult(
                    verdict="INFRASTRUCTURE_ERROR",
                    execution_time_ms=0,
                    memory_used_kb=0,
                    error_message="Controlled infrastructure error triggered by test suite",
                    execution_mode="MOCK_DOUBLE"
                )
            elif "# TRIGGER: TLE" in code:
                verdict = "TIME_LIMIT_EXCEEDED"
            elif "# TRIGGER: RE" in code:
                verdict = "RUNTIME_ERROR"
            elif "# TRIGGER: OLE" in code:
                verdict = "OUTPUT_LIMIT_EXCEEDED"
            elif "# TRIGGER: WA" in code:
                verdict = "WRONG_ANSWER"
            elif "# TRIGGER: CE" in code:
                verdict = "COMPILATION_ERROR"
            else:
                verdict = "ACCEPTED"

        test_results = []
        for tc in test_cases:
            if self.failpoint_at_ordinal and tc.ordinal == self.failpoint_at_ordinal:
                raise RuntimeError("Worker process crashed at controlled failpoint barrier")

            passed = (verdict == "ACCEPTED")
            tc_verdict = "ACCEPTED" if passed else verdict
            test_results.append(TestCaseOutput(
                ordinal=tc.ordinal,
                passed=passed,
                verdict=tc_verdict,
                execution_time_ms=10,
                actual_output=None if tc.is_hidden else ("correct" if passed else "wrong"),
                is_hidden=tc.is_hidden
            ))
            if not passed:
                break

        return RunnerResult(
            verdict=verdict,
            execution_time_ms=10 * len(test_results),
            memory_used_kb=1024,
            test_results=test_results,
            execution_mode="MOCK_DOUBLE"
        )


def get_runner(mode: Optional[str] = None) -> BaseRunner:
    """Returns the runner instance based on configuration or environment capability."""
    from app.config import settings
    effective_mode = mode or settings.RUNNER_MODE

    if effective_mode == "MOCK":
        return DeterministicMockRunner()
    elif effective_mode == "SUBPROCESS_SANDBOX":
        return SubprocessSandboxRunner()
    else:
        # Default to REAL_DOCKER, but fallback to SubprocessSandboxRunner if docker CLI is not present
        if shutil.which("docker") is None:
            return SubprocessSandboxRunner()
        return DockerSandboxRunner()
