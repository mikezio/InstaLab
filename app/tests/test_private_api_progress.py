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

    assert [phase for phase, _ in events] == ["followers", "followers", "following"]

    first_followers = events[0][1]
    assert first_followers["count"] == 2
    assert first_followers["page_index"] == 1
    assert first_followers["page_unique_new"] == 2
    assert first_followers["duplicates_total"] == 0

    second_followers = events[1][1]
    assert second_followers["count"] == 3
    assert second_followers["page_index"] == 2
    assert second_followers["page_unique_new"] == 1
    assert second_followers["duplicates_total"] == 1

    following = events[2][1]
    assert following["count"] == 1
    assert following["page_index"] == 1


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
