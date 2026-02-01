"""
Input validation schemas for API endpoints.

This module provides validation functions for common API request patterns
to ensure data integrity and security.
"""

import re
from typing import Any


class ValidationError(ValueError):
    """Raised when input validation fails."""
    pass


def validate_username(username: str, field_name: str = "username") -> str:
    """
    Validate an Instagram username.
    
    Args:
        username: Username to validate
        field_name: Name of the field for error messages
        
    Returns:
        Cleaned username
        
    Raises:
        ValidationError: If username is invalid
    """
    if not username or not isinstance(username, str):
        raise ValidationError(f"{field_name} is required")
    
    username = username.strip()
    
    if not username:
        raise ValidationError(f"{field_name} cannot be empty")
    
    # Instagram usernames: 1-30 chars, alphanumeric + period + underscore
    if not re.match(r'^[a-zA-Z0-9._]{1,30}$', username):
        raise ValidationError(
            f"{field_name} must be 1-30 characters, alphanumeric, period, or underscore"
        )
    
    return username


def validate_run_request(data: dict) -> dict:
    """
    Validate a run request payload.
    
    Args:
        data: Request data dict
        
    Returns:
        Validated and cleaned data
        
    Raises:
        ValidationError: If validation fails
    """
    if not isinstance(data, dict):
        raise ValidationError("Request body must be JSON object")
    
    target = validate_username(data.get("target", ""), "target")
    login = validate_username(data.get("login", ""), "login")
    
    scraper_backend = (data.get("scraper_backend") or "").strip().lower()
    if scraper_backend and scraper_backend not in {"instaloader", "selenium"}:
        raise ValidationError(
            "scraper_backend must be 'instaloader' or 'selenium'"
        )
    
    return {
        "target": target,
        "login": login,
        "scraper_backend": scraper_backend or "instaloader",
    }


def validate_login_add(data: dict) -> dict:
    """
    Validate login add/update request.
    
    Args:
        data: Request data dict
        
    Returns:
        Validated and cleaned data
        
    Raises:
        ValidationError: If validation fails
    """
    if not isinstance(data, dict):
        raise ValidationError("Request body must be JSON object")
    
    username = validate_username(
        data.get("login_username", ""), 
        "login_username"
    )
    
    password = (data.get("login_password") or "").strip()
    cookie_file = (data.get("cookie_file") or "").strip()
    
    if not password and not cookie_file:
        raise ValidationError(
            "Either login_password or cookie_file is required"
        )
    
    # Basic path traversal protection for cookie_file
    if cookie_file and ".." in cookie_file:
        raise ValidationError("Invalid cookie_file path")
    
    return {
        "login_username": username,
        "login_password": password,
        "cookie_file": cookie_file,
    }


def validate_unfollow_request(data: dict) -> dict:
    """
    Validate unfollow operation request.
    
    Args:
        data: Request data dict
        
    Returns:
        Validated and cleaned data
        
    Raises:
        ValidationError: If validation fails
    """
    if not isinstance(data, dict):
        raise ValidationError("Request body must be JSON object")
    
    login = validate_username(data.get("login", ""), "login")
    
    max_count = data.get("max_count", 25)
    try:
        max_count = int(max_count)
    except (ValueError, TypeError):
        raise ValidationError("max_count must be an integer")
    
    if not (1 <= max_count <= 100):
        raise ValidationError("max_count must be between 1 and 100")
    
    dry_run = bool(data.get("dry_run", False))
    
    delay_min = data.get("delay_min", 25)
    delay_max = data.get("delay_max", 45)
    
    try:
        delay_min = float(delay_min)
        delay_max = float(delay_max)
    except (ValueError, TypeError):
        raise ValidationError("delay_min and delay_max must be numbers")
    
    if delay_min < 1:
        raise ValidationError("delay_min must be at least 1 second")
    if delay_max < delay_min:
        raise ValidationError("delay_max must be >= delay_min")
    if delay_max > 300:
        raise ValidationError("delay_max must be <= 300 seconds")
    
    return {
        "login": login,
        "max_count": max_count,
        "dry_run": dry_run,
        "delay_min": delay_min,
        "delay_max": delay_max,
    }


def validate_positive_int(
    value: Any, 
    name: str, 
    min_val: int = 1, 
    max_val: int | None = None
) -> int:
    """
    Validate and convert to positive integer.
    
    Args:
        value: Value to validate
        name: Field name for error messages
        min_val: Minimum allowed value (default: 1)
        max_val: Maximum allowed value (optional)
        
    Returns:
        Validated integer
        
    Raises:
        ValidationError: If validation fails
    """
    try:
        val = int(value)
    except (ValueError, TypeError):
        raise ValidationError(f"{name} must be an integer")
    
    if val < min_val:
        raise ValidationError(f"{name} must be at least {min_val}")
    
    if max_val is not None and val > max_val:
        raise ValidationError(f"{name} must be at most {max_val}")
    
    return val


def sanitize_sql_limit(limit: Any, default: int = 50, max_limit: int = 1000) -> int:
    """
    Sanitize and validate SQL LIMIT parameter.
    
    Args:
        limit: Limit value from request
        default: Default if not provided
        max_limit: Maximum allowed limit
        
    Returns:
        Safe integer limit value
    """
    if limit is None:
        return default
    
    try:
        val = int(limit)
        if val < 1:
            return default
        return min(val, max_limit)
    except (ValueError, TypeError):
        return default
