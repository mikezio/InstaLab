from types import SimpleNamespace

import pytest

import private_api_tracker as tracker


class DummyPublicClient:
    def __init__(self, user, followers=None, following=None):
        self._user = user
        self._followers = followers or []
        self._following = following or []
        self.calls = []

    def user_info_by_username_gql(self, username):
        self.calls.append(("info", username))
        return self._user

    def user_followers_gql(self, user_id, amount=0):
        self.calls.append(("followers", str(user_id), int(amount)))
        return list(self._followers)

    def user_following_gql(self, user_id, amount=0):
        self.calls.append(("following", str(user_id), int(amount)))
        return list(self._following)


def _public_user(username="target", *, pk="123", followers=10, following=7, is_private=False):
    return SimpleNamespace(
        username=username,
        pk=pk,
        follower_count=followers,
        following_count=following,
        is_private=is_private,
    )


def test_fetch_counts_anonymous_mode_uses_public_client(monkeypatch):
    client = DummyPublicClient(_public_user())

    def _fail_build_client(*args, **kwargs):
        raise AssertionError("_build_client should not be called for anonymous mode")

    monkeypatch.setattr(tracker, "_build_client", _fail_build_client)
    monkeypatch.setattr(tracker, "_build_public_client", lambda *args, **kwargs: client)

    result = tracker.fetch_counts(
        login_username="collector",
        login_password="",
        target_username="target",
        login_mode="anonymous",
    )

    assert result == {
        "followers_count": 10,
        "followees_count": 7,
        "target_id": "123",
    }
    assert client.calls == [("info", "target")]


def test_snapshot_profile_anonymous_mode_uses_public_lists(monkeypatch):
    user = _public_user(followers=3, following=2)
    followers = [SimpleNamespace(username="alice"), SimpleNamespace(username="bob")]
    following = [SimpleNamespace(username="bob"), SimpleNamespace(username="carol")]
    client = DummyPublicClient(user, followers=followers, following=following)

    def _fail_build_client(*args, **kwargs):
        raise AssertionError("_build_client should not be called for anonymous mode")

    monkeypatch.setattr(tracker, "_build_client", _fail_build_client)
    monkeypatch.setattr(tracker, "_build_public_client", lambda *args, **kwargs: client)

    progress_events = []
    result = tracker.snapshot_profile(
        login_username="collector",
        login_password="",
        target_username="target",
        db_path=None,
        login_mode="anonymous",
        progress=lambda phase, count: progress_events.append((phase, count)),
    )

    assert result["followers_count"] == 3
    assert result["followees_count"] == 2
    assert result["followers"] == ["alice", "bob"]
    assert result["followees"] == ["bob", "carol"]
    assert result["non_followbacks"] == ["carol"]
    assert result["non_followbacks_count"] == 1
    assert [event[0] for event in progress_events] == ["login", "totals", "followers", "followers", "following", "following"]
    assert client.calls == [
        ("info", "target"),
        ("followers", "123", 0),
        ("following", "123", 0),
    ]


def test_fetch_counts_anonymous_mode_rejects_private_targets(monkeypatch):
    client = DummyPublicClient(_public_user(is_private=True))
    monkeypatch.setattr(tracker, "_build_public_client", lambda *args, **kwargs: client)

    with pytest.raises(RuntimeError, match="anonymous mode only supports public target accounts"):
        tracker.fetch_counts(
            login_username="collector",
            login_password="",
            target_username="target",
            login_mode="anonymous",
        )
