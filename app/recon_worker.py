import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from validation import ValidationError, validate_username

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[1-9]\d{6,14}$")


class ReconExecutionError(RuntimeError):
    pass


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, text: str) -> None:
    path.write_text(text or "", encoding="utf-8")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_json(url: str, *, method: str = "GET", body: dict | None = None, timeout: int = 30) -> dict:
    payload = None
    headers = {}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=payload, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw}


def _normalize_mode_and_value(mode: str, query_value: str) -> tuple[str, str]:
    cleaned_mode = (mode or "").strip().lower()
    value = (query_value or "").strip()
    if cleaned_mode not in {"username", "email", "phone"}:
        raise ValidationError("mode must be one of: username, email, phone")
    if cleaned_mode == "username":
        return cleaned_mode, validate_username(value, "query_value")
    if cleaned_mode == "email":
        if not EMAIL_RE.match(value):
            raise ValidationError("query_value must be a valid email")
        return cleaned_mode, value.lower()
    normalized = re.sub(r"[\s\-()]+", "", value)
    if not PHONE_RE.match(normalized):
        raise ValidationError("query_value must be an E.164 style phone number")
    if not normalized.startswith("+"):
        normalized = "+" + normalized
    return cleaned_mode, normalized


def _resolve_blackbird_results_dir(command_parts: list[str], cfg: dict) -> Path:
    configured = str(cfg.get("recon_blackbird_results_dir") or "").strip()
    if configured:
        return Path(configured)

    for part in command_parts:
        if part.endswith("blackbird.py"):
            return Path(part).resolve().parent / "results"

    return Path("/opt/blackbird/results")


def _latest_file(paths: list[Path], *, after_mtime: float) -> Path | None:
    filtered = [p for p in paths if p.exists() and p.is_file() and p.stat().st_mtime >= after_mtime]
    if not filtered:
        return None
    return max(filtered, key=lambda p: p.stat().st_mtime)


def _confidence_from_status(status: str) -> str:
    s = (status or "").strip().upper()
    if s == "FOUND":
        return CONFIDENCE_HIGH
    if s in {"NOT-FOUND", "NONE"}:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def _normalize_blackbird_findings(payload) -> list[dict]:
    findings = []
    if not isinstance(payload, list):
        return findings
    for item in payload:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        findings.append(
            {
                "platform": str(item.get("name") or "unknown"),
                "url": item.get("url"),
                "category": item.get("category"),
                "tool_status": status,
                "confidence_tier": _confidence_from_status(status),
                "evidence": {
                    "metadata": item.get("metadata"),
                },
            }
        )
    return findings


def _run_blackbird(mode: str, query_value: str, options: dict, job_dir: Path, cfg: dict) -> dict:
    raw_dir = job_dir / "raw"
    _ensure_dir(raw_dir)

    command_template = str(cfg.get("recon_blackbird_cmd") or "python /opt/blackbird/blackbird.py").strip()
    cmd = shlex.split(command_template)
    if not cmd:
        raise ReconExecutionError("recon_blackbird_cmd is empty")

    cmd.extend(["--json", "--no-update"])  # keep scans deterministic and avoid runtime updates
    if mode == "username":
        cmd.extend(["--username", query_value])
    elif mode == "email":
        cmd.extend(["--email", query_value])
    else:
        raise ReconExecutionError("blackbird only supports username/email")

    timeout_seconds = int(options.get("timeout_seconds") or cfg.get("recon_timeout_seconds") or 240)
    if options.get("no_nsfw", cfg.get("recon_blackbird_no_nsfw", True)):
        cmd.append("--no-nsfw")

    if options.get("ai", cfg.get("recon_blackbird_ai_enabled", False)):
        cmd.append("--ai")

    result_dir = _resolve_blackbird_results_dir(cmd, cfg)
    before_mtime = time.time() - 1
    before_files = set(glob.glob(str(result_dir / "**" / "*.json"), recursive=True)) if result_dir.exists() else set()

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_seconds,
        check=False,
        cwd="/app",
    )

    stdout_path = raw_dir / "blackbird.stdout.log"
    stderr_path = raw_dir / "blackbird.stderr.log"
    _write_text(stdout_path, proc.stdout)
    _write_text(stderr_path, proc.stderr)

    if proc.returncode != 0:
        raise ReconExecutionError(f"blackbird exited with code {proc.returncode}")

    after_files = set(glob.glob(str(result_dir / "**" / "*.json"), recursive=True)) if result_dir.exists() else set()
    new_files = [Path(p) for p in (after_files - before_files)]
    json_file = _latest_file(new_files, after_mtime=before_mtime)

    if not json_file:
        candidates = [Path(p) for p in after_files]
        json_file = _latest_file(candidates, after_mtime=before_mtime)

    if not json_file or not json_file.exists():
        raise ReconExecutionError("blackbird did not produce a JSON artifact")

    copied_json = raw_dir / "blackbird.result.json"
    shutil.copy2(json_file, copied_json)

    payload = _read_json(copied_json)
    findings = _normalize_blackbird_findings(payload)

    artifacts = [
        {"artifact_type": "json", "path": str(copied_json), "size_bytes": copied_json.stat().st_size},
        {"artifact_type": "log", "path": str(stdout_path), "size_bytes": stdout_path.stat().st_size},
        {"artifact_type": "log", "path": str(stderr_path), "size_bytes": stderr_path.stat().st_size},
    ]

    return {
        "tool": "blackbird",
        "status": "success",
        "raw_output_path": str(copied_json),
        "findings": findings,
        "artifacts": artifacts,
        "command_fingerprint": hashlib.sha256(" ".join(cmd).encode("utf-8")).hexdigest(),
    }


def _wait_for_phoneinfoga(base_url: str, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            _http_json(f"{base_url}/api/v2/scanners", timeout=5)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.5)
    raise ReconExecutionError(f"phoneinfoga API did not start: {last_error}")


def _normalize_phoneinfoga_findings(number_info: dict, scanner_results: list[dict]) -> list[dict]:
    findings = []
    if number_info:
        findings.append(
            {
                "platform": "phone_number",
                "url": None,
                "category": "telephony",
                "tool_status": "FOUND" if number_info.get("valid") else "UNKNOWN",
                "confidence_tier": CONFIDENCE_HIGH if number_info.get("valid") else CONFIDENCE_MEDIUM,
                "evidence": number_info,
            }
        )

    for item in scanner_results:
        scanner = str(item.get("scanner") or "scanner")
        error = item.get("error")
        result = item.get("result")
        has_data = isinstance(result, dict) and bool(result)
        findings.append(
            {
                "platform": scanner,
                "url": None,
                "category": "phone_osint",
                "tool_status": "ERROR" if error else ("FOUND" if has_data else "UNKNOWN"),
                "confidence_tier": CONFIDENCE_LOW if error else (CONFIDENCE_HIGH if has_data else CONFIDENCE_MEDIUM),
                "evidence": {
                    "error": error,
                    "result": result,
                },
            }
        )
    return findings


def _run_phoneinfoga(query_value: str, options: dict, job_dir: Path, cfg: dict) -> dict:
    raw_dir = job_dir / "raw"
    _ensure_dir(raw_dir)

    phoneinfoga_cmd = str(cfg.get("recon_phoneinfoga_cmd") or "/usr/local/bin/phoneinfoga").strip()
    base_cmd = shlex.split(phoneinfoga_cmd)
    if not base_cmd:
        raise ReconExecutionError("recon_phoneinfoga_cmd is empty")

    timeout_seconds = int(options.get("timeout_seconds") or cfg.get("recon_timeout_seconds") or 240)
    startup_timeout = min(30, max(5, timeout_seconds // 4))
    port = _find_free_port()
    cmd = [*base_cmd, "serve", "--no-client", "--port", str(port)]

    stdout_path = raw_dir / "phoneinfoga.stdout.log"
    stderr_path = raw_dir / "phoneinfoga.stderr.log"

    with open(stdout_path, "w", encoding="utf-8") as out_fh, open(stderr_path, "w", encoding="utf-8") as err_fh:
        proc = subprocess.Popen(cmd, stdout=out_fh, stderr=err_fh, text=True)
        base_url = f"http://127.0.0.1:{port}"
        try:
            _wait_for_phoneinfoga(base_url, startup_timeout)

            number_info = _http_json(
                f"{base_url}/api/v2/numbers",
                method="POST",
                body={"number": query_value},
                timeout=20,
            )
            scanners_payload = _http_json(f"{base_url}/api/v2/scanners", timeout=20)
            scanners = [s.get("name") for s in scanners_payload.get("scanners", []) if isinstance(s, dict) and s.get("name")]
            requested = options.get("scanners")
            if isinstance(requested, list) and requested:
                wanted = {str(x).strip() for x in requested if str(x).strip()}
                scanners = [s for s in scanners if s in wanted]

            scanner_results = []
            for scanner in scanners:
                try:
                    result = _http_json(
                        f"{base_url}/api/v2/scanners/{quote(scanner)}/run",
                        method="POST",
                        body={"number": query_value, "options": {}},
                        timeout=30,
                    )
                    scanner_results.append({"scanner": scanner, "result": result.get("result"), "error": None})
                except Exception as exc:  # noqa: BLE001
                    scanner_results.append({"scanner": scanner, "result": None, "error": str(exc)})

            combined = {
                "number": query_value,
                "number_info": number_info,
                "scanners": scanner_results,
            }
            result_json = raw_dir / "phoneinfoga.result.json"
            _write_text(result_json, json.dumps(combined, ensure_ascii=False, indent=2))

            findings = _normalize_phoneinfoga_findings(number_info, scanner_results)
            artifacts = [
                {"artifact_type": "json", "path": str(result_json), "size_bytes": result_json.stat().st_size},
                {"artifact_type": "log", "path": str(stdout_path), "size_bytes": stdout_path.stat().st_size},
                {"artifact_type": "log", "path": str(stderr_path), "size_bytes": stderr_path.stat().st_size},
            ]
            return {
                "tool": "phoneinfoga",
                "status": "success",
                "raw_output_path": str(result_json),
                "findings": findings,
                "artifacts": artifacts,
                "command_fingerprint": hashlib.sha256(" ".join(cmd).encode("utf-8")).hexdigest(),
            }
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def run_recon_scan(mode: str, query_value: str, options: dict | None, job_dir: str | Path, cfg: dict) -> dict:
    cleaned_mode, cleaned_value = _normalize_mode_and_value(mode, query_value)
    options = options or {}
    path = Path(job_dir)
    _ensure_dir(path)

    started = time.time()
    if cleaned_mode in {"username", "email"}:
        payload = _run_blackbird(cleaned_mode, cleaned_value, options, path, cfg)
    elif cleaned_mode == "phone":
        payload = _run_phoneinfoga(cleaned_value, options, path, cfg)
    else:
        raise ReconExecutionError("Unsupported recon mode")

    payload["mode"] = cleaned_mode
    payload["query_value"] = cleaned_value
    payload["duration_seconds"] = int(round(time.time() - started))
    return payload
