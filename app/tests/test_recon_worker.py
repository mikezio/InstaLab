from pathlib import Path

import pytest

import recon_worker
from validation import ValidationError


def test_run_recon_scan_username_uses_blackbird(monkeypatch, tmp_path):
    captured = {}

    def fake_blackbird(mode, query_value, options, job_dir, cfg):
        captured["mode"] = mode
        captured["query_value"] = query_value
        return {
            "tool": "blackbird",
            "status": "success",
            "raw_output_path": str(job_dir / "result.json"),
            "findings": [],
            "artifacts": [],
            "command_fingerprint": "abc",
        }

    monkeypatch.setattr(recon_worker, "_run_blackbird", fake_blackbird)

    result = recon_worker.run_recon_scan(
        mode="username",
        query_value="  user_name  ",
        options={},
        job_dir=tmp_path,
        cfg={},
    )

    assert captured["mode"] == "username"
    assert captured["query_value"] == "user_name"
    assert result["mode"] == "username"
    assert result["query_value"] == "user_name"


def test_run_recon_scan_phone_normalizes_e164(monkeypatch, tmp_path):
    captured = {}

    def fake_phoneinfoga(query_value, options, job_dir, cfg):
        captured["query_value"] = query_value
        return {
            "tool": "phoneinfoga",
            "status": "success",
            "raw_output_path": str(job_dir / "phone.json"),
            "findings": [],
            "artifacts": [],
            "command_fingerprint": "abc",
        }

    monkeypatch.setattr(recon_worker, "_run_phoneinfoga", fake_phoneinfoga)

    result = recon_worker.run_recon_scan(
        mode="phone",
        query_value="(555) 123-4567",
        options={},
        job_dir=tmp_path,
        cfg={},
    )

    assert captured["query_value"] == "+5551234567"
    assert result["mode"] == "phone"


def test_run_recon_scan_rejects_invalid_email(tmp_path):
    with pytest.raises(ValidationError):
        recon_worker.run_recon_scan(
            mode="email",
            query_value="bad-email",
            options={},
            job_dir=tmp_path,
            cfg={},
        )


def test_blackbird_findings_normalization_maps_confidence():
    payload = [
        {"name": "SiteA", "url": "https://a", "category": "social", "status": "FOUND"},
        {"name": "SiteB", "url": "https://b", "category": "misc", "status": "ERROR"},
    ]
    findings = recon_worker._normalize_blackbird_findings(payload)
    assert findings[0]["confidence_tier"] == "high"
    assert findings[1]["confidence_tier"] == "low"


def test_blackbird_ai_requires_key(tmp_path):
    cfg = {
        "recon_blackbird_cmd": "python /app/scripts/blackbird_proxy.py",
        "recon_blackbird_results_dir": str(tmp_path / "runtime" / "results"),
        "recon_timeout_seconds": 30,
    }
    with pytest.raises(recon_worker.ReconExecutionError, match="AI key not configured"):
        recon_worker._run_blackbird(
            mode="username",
            query_value="someuser",
            options={"ai": True},
            job_dir=tmp_path / "job",
            cfg=cfg,
        )


def test_phoneinfoga_api_number_strips_non_digits():
    assert recon_worker._phoneinfoga_api_number("+1 (415) 555-2671") == "14155552671"
