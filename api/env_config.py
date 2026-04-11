"""Environment configuration for CLM API.
Detects staging vs production based on environment variables."""

import os

ENV = os.environ.get("VERCEL_ENV", os.environ.get("STAGING", "production"))
IS_STAGING = ENV in ("preview", "development") or os.environ.get("STAGING") == "true"
IS_PRODUCTION = not IS_STAGING

# Environment-specific settings
if IS_STAGING:
    LOG_LEVEL = "DEBUG"
    RATE_LIMIT_DEFAULT = 300  # More permissive for testing
    TOKEN_EXPIRY = 86400 * 7  # 7 days for staging
    CORS_ALLOW_ALL = True
else:
    LOG_LEVEL = "INFO"
    RATE_LIMIT_DEFAULT = 120
    TOKEN_EXPIRY = 86400  # 24 hours for production
    CORS_ALLOW_ALL = False
