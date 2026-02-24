from private_api_tracker import _default_device_settings_for_login


def test_default_device_profile_is_stable_per_login():
    a1 = _default_device_settings_for_login("collector.alpha")
    a2 = _default_device_settings_for_login("collector.alpha")
    b = _default_device_settings_for_login("collector.beta")
    assert a1 == a2
    assert a1 != b


def test_default_device_profile_avoids_dev_fingerprint_values():
    profile = _default_device_settings_for_login("collector.alpha")
    joined = " ".join(str(profile.get(k, "")) for k in ("model", "device", "manufacturer")).lower()
    assert "dev" not in joined
    assert profile.get("app_version")
    assert profile.get("version_code")
