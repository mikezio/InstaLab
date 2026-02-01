"""
Logging configuration for InstaLab.

This module provides structured logging setup to replace print() statements
throughout the application.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(
    app_name: str = "instalab",
    log_level: str | None = None,
    log_file: str | None = None,
    log_to_console: bool = True,
) -> logging.Logger:
    """
    Set up application logging.
    
    Args:
        app_name: Logger name (default: "instalab")
        log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
                   Defaults to INFO, or from INSTALAB_LOG_LEVEL env var
        log_file: Path to log file (optional)
                  Defaults to INSTALAB_LOG_FILE env var
        log_to_console: Whether to log to console (default: True)
        
    Returns:
        Configured logger instance
        
    Example:
        >>> logger = setup_logging()
        >>> logger.info("Application started", extra={"version": "1.0.0"})
    """
    # Determine log level
    if log_level is None:
        log_level = os.getenv("INSTALAB_LOG_LEVEL", "INFO").upper()
    
    try:
        level = getattr(logging, log_level)
    except AttributeError:
        level = logging.INFO
    
    # Get or create logger
    logger = logging.getLogger(app_name)
    logger.setLevel(level)
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Create formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    # Console handler
    if log_to_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    # File handler
    if log_file is None:
        log_file = os.getenv("INSTALAB_LOG_FILE")
    
    if log_file:
        try:
            # Ensure log directory exists
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Rotating file handler (10MB max, keep 5 backups)
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            # If file logging fails, log to console
            logger.warning(f"Could not set up file logging: {e}")
    
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """
    Get a logger instance.
    
    Args:
        name: Logger name (default: "instalab")
        
    Returns:
        Logger instance
        
    Example:
        >>> from logger_config import get_logger
        >>> logger = get_logger(__name__)
        >>> logger.info("Processing run", extra={"run_id": 42})
    """
    return logging.getLogger(name or "instalab")


# Example usage for migration from print() to logging:
#
# Before:
#   print(f"[init] Starting server on port {port}")
#   print(f"[error] Failed to connect: {exc}", file=sys.stderr)
#
# After:
#   logger = get_logger(__name__)
#   logger.info("Starting server", extra={"port": port})
#   logger.error("Failed to connect", exc_info=True)
