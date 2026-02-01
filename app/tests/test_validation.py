"""
Unit tests for validation module.

Run with: pytest app/tests/test_validation.py
"""

import pytest
from validation import (
    ValidationError,
    validate_username,
    validate_run_request,
    validate_login_add,
    validate_unfollow_request,
    validate_positive_int,
    sanitize_sql_limit,
)


class TestValidateUsername:
    """Tests for username validation."""
    
    def test_valid_usernames(self):
        """Valid usernames should pass."""
        assert validate_username("user123") == "user123"
        assert validate_username("user.name") == "user.name"
        assert validate_username("user_name") == "user_name"
        assert validate_username("a" * 30) == "a" * 30
    
    def test_username_with_whitespace(self):
        """Whitespace should be stripped."""
        assert validate_username("  user123  ") == "user123"
    
    def test_empty_username(self):
        """Empty username should raise error."""
        with pytest.raises(ValidationError, match="is required"):
            validate_username("")
        with pytest.raises(ValidationError, match="is required"):
            validate_username(None)
    
    def test_invalid_characters(self):
        """Invalid characters should raise error."""
        with pytest.raises(ValidationError, match="alphanumeric"):
            validate_username("user@123")
        with pytest.raises(ValidationError, match="alphanumeric"):
            validate_username("user name")
    
    def test_too_long(self):
        """Username > 30 chars should raise error."""
        with pytest.raises(ValidationError):
            validate_username("a" * 31)


class TestValidateRunRequest:
    """Tests for run request validation."""
    
    def test_valid_request(self):
        """Valid request should return cleaned data."""
        data = {
            "target": "target_user",
            "login": "login_user",
            "scraper_backend": "instaloader",
        }
        result = validate_run_request(data)
        assert result["target"] == "target_user"
        assert result["login"] == "login_user"
        assert result["scraper_backend"] == "instaloader"
    
    def test_missing_target(self):
        """Missing target should raise error."""
        with pytest.raises(ValidationError, match="target"):
            validate_run_request({"login": "user"})
    
    def test_missing_login(self):
        """Missing login should raise error."""
        with pytest.raises(ValidationError, match="login"):
            validate_run_request({"target": "user"})
    
    def test_invalid_scraper_backend(self):
        """Invalid scraper backend should raise error."""
        data = {"target": "user1", "login": "user2", "scraper_backend": "invalid"}
        with pytest.raises(ValidationError, match="scraper_backend"):
            validate_run_request(data)
    
    def test_default_scraper_backend(self):
        """Default scraper backend should be instaloader."""
        data = {"target": "user1", "login": "user2"}
        result = validate_run_request(data)
        assert result["scraper_backend"] == "instaloader"


class TestValidateLoginAdd:
    """Tests for login add validation."""
    
    def test_valid_with_password(self):
        """Valid request with password."""
        data = {
            "login_username": "user123",
            "login_password": "secret123",
        }
        result = validate_login_add(data)
        assert result["login_username"] == "user123"
        assert result["login_password"] == "secret123"
    
    def test_valid_with_cookie_file(self):
        """Valid request with cookie file."""
        data = {
            "login_username": "user123",
            "cookie_file": "/data/cookies/session.json",
        }
        result = validate_login_add(data)
        assert result["login_username"] == "user123"
        assert result["cookie_file"] == "/data/cookies/session.json"
    
    def test_missing_credentials(self):
        """Missing both password and cookie should raise error."""
        data = {"login_username": "user123"}
        with pytest.raises(ValidationError, match="password or cookie_file"):
            validate_login_add(data)
    
    def test_path_traversal_protection(self):
        """Path traversal in cookie_file should raise error."""
        data = {
            "login_username": "user123",
            "cookie_file": "../../../etc/passwd",
        }
        with pytest.raises(ValidationError, match="Invalid cookie_file"):
            validate_login_add(data)


class TestValidateUnfollowRequest:
    """Tests for unfollow request validation."""
    
    def test_valid_request(self):
        """Valid unfollow request."""
        data = {
            "login": "user123",
            "max_count": 25,
            "dry_run": False,
            "delay_min": 25,
            "delay_max": 45,
        }
        result = validate_unfollow_request(data)
        assert result["login"] == "user123"
        assert result["max_count"] == 25
        assert result["dry_run"] is False
    
    def test_max_count_validation(self):
        """max_count should be validated."""
        # Too low
        with pytest.raises(ValidationError, match="between 1 and 100"):
            validate_unfollow_request({"login": "user", "max_count": 0})
        
        # Too high
        with pytest.raises(ValidationError, match="between 1 and 100"):
            validate_unfollow_request({"login": "user", "max_count": 101})
    
    def test_delay_validation(self):
        """Delay values should be validated."""
        # delay_min too low
        with pytest.raises(ValidationError, match="at least 1 second"):
            validate_unfollow_request({
                "login": "user",
                "delay_min": 0.5,
                "delay_max": 30,
            })
        
        # delay_max < delay_min
        with pytest.raises(ValidationError, match="delay_max must be"):
            validate_unfollow_request({
                "login": "user",
                "delay_min": 50,
                "delay_max": 30,
            })
        
        # delay_max too high
        with pytest.raises(ValidationError, match="<= 300"):
            validate_unfollow_request({
                "login": "user",
                "delay_min": 25,
                "delay_max": 400,
            })


class TestValidatePositiveInt:
    """Tests for positive integer validation."""
    
    def test_valid_integer(self):
        """Valid integer should pass."""
        assert validate_positive_int(42, "count") == 42
    
    def test_with_min_max(self):
        """Min/max constraints should be enforced."""
        assert validate_positive_int(50, "count", min_val=1, max_val=100) == 50
        
        with pytest.raises(ValidationError):
            validate_positive_int(0, "count", min_val=1)
        
        with pytest.raises(ValidationError):
            validate_positive_int(101, "count", max_val=100)
    
    def test_invalid_type(self):
        """Non-integer should raise error."""
        with pytest.raises(ValidationError, match="must be an integer"):
            validate_positive_int("not a number", "count")


class TestSanitizeSqlLimit:
    """Tests for SQL limit sanitization."""
    
    def test_valid_limit(self):
        """Valid limit should be returned."""
        assert sanitize_sql_limit(50) == 50
    
    def test_none_returns_default(self):
        """None should return default."""
        assert sanitize_sql_limit(None) == 50
        assert sanitize_sql_limit(None, default=100) == 100
    
    def test_exceeds_max(self):
        """Limit exceeding max should be capped."""
        assert sanitize_sql_limit(2000, max_limit=1000) == 1000
    
    def test_negative_returns_default(self):
        """Negative limit should return default."""
        assert sanitize_sql_limit(-10) == 50
    
    def test_invalid_type_returns_default(self):
        """Invalid type should return default."""
        assert sanitize_sql_limit("invalid") == 50
