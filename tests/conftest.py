"""Test configuration and fixtures for CLM API tests."""
import os
import sys
import json
import pytest
from unittest.mock import MagicMock, patch

# Add API and tests directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))
sys.path.insert(0, os.path.dirname(__file__))

# Set env vars before importing
os.environ['APP_SECRET'] = 'test-secret-key'
os.environ['APP_PASSWORD'] = 'test-password-123'
os.environ['SUPABASE_URL'] = ''
os.environ['SUPABASE_KEY'] = ''


@pytest.fixture
def app():
    """Create test Flask app."""
    from index import app
    app.config['TESTING'] = True
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def auth_token():
    """Generate a valid auth token.

    Uses empty email so the auth decorator skips the DB user lookup
    and defaults to admin role. Tests that need email-based auth
    should create tokens via mk_token("email@example.com") directly.
    """
    from index import mk_token
    return mk_token("")


@pytest.fixture
def auth_headers(auth_token):
    """Auth headers for authenticated requests."""
    return {'Authorization': f'Bearer {auth_token}', 'Content-Type': 'application/json'}


@pytest.fixture
def mock_sb():
    """Mock Supabase client — patch in all modules that import sb."""
    mock = MagicMock()
    with patch('config.sb', mock), \
         patch('index.sb', mock), \
         patch('auth.sb', mock, create=True), \
         patch('helpers.sb', mock), \
         patch('ai.sb', mock):
        yield mock


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Clear the rate limiter between tests."""
    import config
    config._rate_store.clear()
    yield
    config._rate_store.clear()


@pytest.fixture(autouse=True)
def reset_revoked_tokens():
    """Clear revoked tokens between tests."""
    import config
    config._revoked_tokens.clear()
    yield
    config._revoked_tokens.clear()


# Re-export helpers so they're available if needed
from test_helpers import make_mock_response, mock_chain
