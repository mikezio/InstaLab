from private_api_tracker import _enable_progress_private_request


class DummyClient:
    def __init__(self):
        self.calls = []
        self.last_json = {}
        self._queued = {}
        self.private_request = self._private_request

    def queue_last_json(self, endpoint, payload):
        self._queued.setdefault(endpoint, []).append(payload)

    def _private_request(
        self,
        endpoint,
        data=None,
        params=None,
        login=False,
        with_signature=True,
        headers=None,
        extra_sig=None,
        domain=None,
    ):
        _ = (data, params, login, with_signature, headers, extra_sig, domain)
        self.calls.append(endpoint)
        payloads = self._queued.get(endpoint) or []
        self.last_json = payloads.pop(0) if payloads else {}
        return {"ok": True}


def test_progress_updates_incrementally_for_paginated_followers_and_following():
    client = DummyClient()
    followers_endpoint = "friendships/123/followers/"
    following_endpoint = "friendships/123/following/"
    client.queue_last_json(followers_endpoint, {"users": [{"pk": "1"}, {"pk": "2"}]})
    client.queue_last_json(followers_endpoint, {"users": [{"pk": "2"}, {"pk": "3"}]})
    client.queue_last_json(following_endpoint, {"users": [{"pk": "9"}]})
    client.queue_last_json("friendships/123/close_friends/", {"users": [{"pk": "77"}]})

    events = []
    client._instalab_page_progress = {
        "target_id": "123",
        "callback": lambda phase, count: events.append((phase, count)),
        "followers_seen": set(),
        "following_seen": set(),
    }
    _enable_progress_private_request(client)

    client.private_request(followers_endpoint)
    client.private_request(followers_endpoint)
    client.private_request(following_endpoint)
    client.private_request("friendships/123/close_friends/")

    assert events == [("followers", 2), ("followers", 3), ("following", 1)]


def test_progress_ignores_different_target_id():
    client = DummyClient()
    followers_endpoint = "friendships/123/followers/"
    client.queue_last_json(followers_endpoint, {"users": [{"pk": "1"}, {"pk": "2"}]})

    events = []
    client._instalab_page_progress = {
        "target_id": "999",
        "callback": lambda phase, count: events.append((phase, count)),
        "followers_seen": set(),
        "following_seen": set(),
    }
    _enable_progress_private_request(client)
    client.private_request(followers_endpoint)

    assert events == []
