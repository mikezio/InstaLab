import base64
import hashlib
import hmac

from private_api_tracker import (
    _normalize_totp_seed,
    _totp_candidate_codes,
    _totp_code_for_timestamp,
)


def _expected_totp_code(seed: str, when: float, digits: int = 6, interval: int = 30) -> str:
    secret = base64.b32decode(seed, casefold=True)
    counter = int(when // interval)
    msg = counter.to_bytes(8, byteorder="big")
    digest = hmac.new(secret, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = (
        ((digest[offset] & 0x7F) << 24)
        | ((digest[offset + 1] & 0xFF) << 16)
        | ((digest[offset + 2] & 0xFF) << 8)
        | (digest[offset + 3] & 0xFF)
    ) % (10**digits)
    return f"{code_int:0{digits}d}"


def test_normalize_totp_seed_handles_otpauth_and_spacing():
    seed = _normalize_totp_seed("otpauth://totp/Instagram:demo?secret=JBSW Y3DP-EHPK3PXP&issuer=Instagram")
    assert seed == "JBSWY3DPEHPK3PXP"


def test_totp_code_for_timestamp_matches_reference_algorithm():
    seed = "JBSWY3DPEHPK3PXP"
    when = 1_700_000_000
    expected = _expected_totp_code(seed, when)
    assert _totp_code_for_timestamp(seed, when) == expected


def test_totp_candidate_codes_are_windowed_and_unique():
    seed = "JBSWY3DPEHPK3PXP"
    codes = _totp_candidate_codes(seed, when=1_700_000_000)
    assert len(codes) >= 2
    assert len(codes) == len(set(codes))
    assert all(code.isdigit() and len(code) == 6 for code in codes)
