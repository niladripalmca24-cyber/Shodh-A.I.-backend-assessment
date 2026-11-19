# Security Policy & Sandbox Threat Model

## 1. Supported Environments & Sandboxing Boundaries

*Shodh a Code* executes arbitrary untrusted student-submitted source code. The security posture is built upon a defense-in-depth model:

| Control | Sandbox Parameter | Threat Mitigated |
| :--- | :--- | :--- |
| **Network Isolation** | `--network none` | Server-Side Request Forgery (SSRF), crypto-mining egress, remote shell dialing. |
| **User Demotion** | `--user 65534:65534` | Root access inside container disabled; runs as unprivileged `nobody:nogroup`. |
| **Capability Dropping** | `--cap-drop ALL`, `--security-opt no-new-privileges` | Privilege escalation, raw packet capture, kernel namespace manipulation. |
| **Filesystem Immutability** | `--read-only`, temporary `/tmp` tmpfs (`noexec,nosuid,size=16m`) | Persistent malware injection, rootfs tampering. |
| **Process Cap** | `--pids-limit=64`, `--cpus="0.5"` | Fork bombs and thread exhaustion denial of service. |
| **Memory Ceiling** | `--memory="128m"`, `--memory-swap="128m"` | Memory flooding attacks (triggers immediate OOM SIGKILL). |
| **Socket Exclusion** | `/var/run/docker.sock` explicitly excluded | Container breakouts to host Docker control socket. |

## 2. Reporting a Vulnerability

If you discover a security vulnerability or sandbox breakout within this codebase, please contact Niladri Pal directly via email at [palniladri120@gmail.com](mailto:palniladri120@gmail.com).

Please do not disclose security issues publicly until a patch has been coordinated.
