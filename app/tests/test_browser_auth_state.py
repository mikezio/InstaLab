import json

from account_browser_flow import storage_state_has_session
from browser_tracker import _friendship_users_payload
from browser_tracker import _graphql_edge_payload
from browser_tracker import _web_profile_payload
from browser_tracker import _visibility_limited_message as browser_visibility_limited_message
from private_api_tracker import _visibility_limited_message as private_visibility_limited_message


def test_storage_state_has_session_requires_instagram_session_cookie(tmp_path):
    path = tmp_path / "storage.json"
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    assert storage_state_has_session(str(path)) is False


def test_storage_state_has_session_matches_expected_username(tmp_path):
    path = tmp_path / "storage.json"
    payload = {
        "cookies": [
            {
                "name": "sessionid",
                "value": "abc123",
                "domain": ".instagram.com",
                "path": "/",
            },
            {
                "name": "csrftoken",
                "value": "csrf123",
                "domain": ".instagram.com",
                "path": "/",
            },
            {
                "name": "ds_user_id",
                "value": "47128576196",
                "domain": ".instagram.com",
                "path": "/",
            },
        ],
        "origins": [
            {
                "origin": "https://www.instagram.com",
                "localStorage": [
                    {
                        "name": "one_tap_storage_version",
                        "value": json.dumps(
                            {
                                "iglab2026fl": {
                                    "username": "iglab2026fl",
                                }
                            }
                        ),
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert storage_state_has_session(str(path), expected_username="iglab2026fl") is True
    assert storage_state_has_session(str(path), expected_username="otherlogin") is False


def test_visibility_limited_message_detects_special_empty_state():
    payload = {
        "users": [],
        "special_empty_state": {
            "title": "No results",
            "body": "We limit certain things you can see, like someone's followers or following lists, to protect our community.",
        },
    }
    assert "visibility limited" in (private_visibility_limited_message(payload, "followers") or "")
    assert "visibility limited" in (browser_visibility_limited_message(payload, "following") or "")


def test_storage_state_has_session_matches_expected_username_from_nested_value(tmp_path):
    path = tmp_path / "storage.json"
    payload = {
        "cookies": [
            {
                "name": "sessionid",
                "value": "abc123",
                "domain": ".instagram.com",
                "path": "/",
            },
            {
                "name": "csrftoken",
                "value": "csrf123",
                "domain": ".instagram.com",
                "path": "/",
            },
            {
                "name": "ds_user_id",
                "value": "47128576196",
                "domain": ".instagram.com",
                "path": "/",
            },
        ],
        "origins": [
            {
                "origin": "https://www.instagram.com",
                "localStorage": [
                    {
                        "name": "one_tap_storage_version",
                        "value": json.dumps(
                            {
                                "47128576196": {
                                    "username": "iglab2026fl",
                                }
                            }
                        ),
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert storage_state_has_session(str(path), expected_username="iglab2026fl") is True


def test_storage_state_has_session_accepts_session_without_one_tap_marker(tmp_path):
    path = tmp_path / "storage.json"
    payload = {
        "cookies": [
            {
                "name": "sessionid",
                "value": "abc123",
                "domain": ".instagram.com",
                "path": "/",
            },
            {
                "name": "csrftoken",
                "value": "csrf123",
                "domain": ".instagram.com",
                "path": "/",
            },
            {
                "name": "ds_user_id",
                "value": "47128576196",
                "domain": ".instagram.com",
                "path": "/",
            },
        ],
        "origins": [
            {
                "origin": "https://www.instagram.com",
                "localStorage": [
                    {
                        "name": "IGSession",
                        "value": "opaque-session-marker",
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert storage_state_has_session(str(path), expected_username="iglab2026fl") is True


def test_graphql_edge_payload_extracts_followers_and_following():
    payload = {
        "data": {
            "user": {
                "edge_followed_by": {"count": 12, "edges": [], "page_info": {"has_next_page": False}},
                "edge_follow": {"count": 9, "edges": [], "page_info": {"has_next_page": False}},
            }
        }
    }
    assert _graphql_edge_payload(payload, "followers")["count"] == 12
    assert _graphql_edge_payload(payload, "following")["count"] == 9


def test_web_profile_payload_extracts_user_dict():
    payload = {
        "data": {
            "user": {
                "id": "123",
                "follower_count": 12,
                "following_count": 9,
            }
        }
    }
    assert _web_profile_payload(payload, "target")["id"] == "123"


def test_friendship_users_payload_extracts_users_and_next_max_id():
    payload = {
        "users": [
            {"pk": "1", "username": "alice"},
            {"pk": "2", "username": "bob"},
        ],
        "next_max_id": "abc123",
    }
    users, next_max_id = _friendship_users_payload(payload, "followers")
    assert [user["username"] for user in users] == ["alice", "bob"]
    assert next_max_id == "abc123"
