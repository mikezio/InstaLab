from private_api_tracker import _wrap_requests_timeout


class DummySession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, *args, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": dict(kwargs)})
        return {"ok": True}


def test_wrap_requests_timeout_injects_default_timeout():
    session = DummySession()
    _wrap_requests_timeout(session, 12.5)

    session.request("GET", "https://example.com")
    assert session.calls[-1]["kwargs"]["timeout"] == 12.5


def test_wrap_requests_timeout_does_not_override_explicit_timeout():
    session = DummySession()
    _wrap_requests_timeout(session, 12.5)

    session.request("GET", "https://example.com", timeout=1)
    assert session.calls[-1]["kwargs"]["timeout"] == 1

