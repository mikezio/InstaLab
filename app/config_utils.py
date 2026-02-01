"""
Configuration validation and management utilities.

This module provides helpers for validating environment configuration
and managing runtime settings.
"""

import os
from typing import Any


def get_env_bool(key: str, default: bool = False) -> bool:
    """
    Get a boolean value from environment variable.
    
    Args:
        key: Environment variable name
        default: Default value if not set
        
    Returns:
        Boolean value
    """
    value = os.getenv(key, "").lower()
    if not value:
        return default
    return value in ("true", "1", "yes", "on")


def get_env_int(key: str, default: int = 0) -> int:
    """
    Get an integer value from environment variable.
    
    Args:
        key: Environment variable name
        default: Default value if not set
        
    Returns:
        Integer value
    """
    value = os.getenv(key, "")
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_env_float(key: str, default: float = 0.0) -> float:
    """
    Get a float value from environment variable.
    
    Args:
        key: Environment variable name
        default: Default value if not set
        
    Returns:
        Float value
    """
    value = os.getenv(key, "")
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def validate_required_env(keys: list[str]) -> dict[str, str]:
    """
    Validate that required environment variables are set.
    
    Args:
        keys: List of required environment variable names
        
    Returns:
        Dict of key -> value for all required keys
        
    Raises:
        ValueError: If any required key is missing
    """
    missing = []
    values = {}
    
    for key in keys:
        value = os.getenv(key)
        if not value:
            missing.append(key)
        else:
            values[key] = value
    
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )
    
    return values


class ConfigValidator:
    """Validate configuration values."""
    
    @staticmethod
    def validate_positive_int(value: Any, name: str) -> int:
        """Validate value is a positive integer."""
        try:
            val = int(value)
            if val <= 0:
                raise ValueError(f"{name} must be positive, got {val}")
            return val
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid {name}: {e}")
    
    @staticmethod
    def validate_positive_float(value: Any, name: str) -> float:
        """Validate value is a positive float."""
        try:
            val = float(value)
            if val <= 0:
                raise ValueError(f"{name} must be positive, got {val}")
            return val
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid {name}: {e}")
    
    @staticmethod
    def validate_range(value: Any, name: str, min_val: float, max_val: float) -> float:
        """Validate value is within a range."""
        try:
            val = float(value)
            if not (min_val <= val <= max_val):
                raise ValueError(
                    f"{name} must be between {min_val} and {max_val}, got {val}"
                )
            return val
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid {name}: {e}")
