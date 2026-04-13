"""Tests for authentication and authorization endpoints."""
import time
import json
import pytest
from unittest.mock import patch, MagicMock
from test_helpers import make_mock_response, mock_chain


class TestLogin:
    """Tests for POST /api/auth/login."""

    def test_login_correct_admin_password(self, client):
        """Login with the correct APP_PASSWORD returns a token."""
        resp = client.post('/api/auth/login', json={
            'email': '',
            'password': 'test-password-123'
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'token' in data
        assert 'user' in data

    def test_login_wrong_password_returns_401(self, client):
        """Login with wrong password returns 401."""
        resp = client.post('/api/auth/login', json={
            'email': '',
            'password': 'wrong-password'
        })
        assert resp.status_code == 401
        data = resp.get_json()
        assert 'error' in data

    def test_login_user_email_and_password(self, client, mock_sb):
        """Login with user email checks the DB user table."""
        user_data = {
            'id': 1, 'email': 'user@example.com', 'name': 'Test User',
            'role': 'editor', 'department': 'Legal', 'is_active': True,
            'password_hash': '$2b$12$dummyhashvaluefortest000000000000000000000000000000'
        }
        chain = mock_chain(make_mock_response([user_data]))
        mock_sb.table.return_value = chain

        with patch('index._verify_password', return_value=(True, False)):
            resp = client.post('/api/auth/login', json={
                'email': 'user@example.com',
                'password': 'userpass123'
            })
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'token' in data
        assert data['user']['email'] == 'user@example.com'

    def test_login_user_wrong_password(self, client, mock_sb):
        """Login with user email but wrong password returns 401."""
        user_data = {
            'id': 1, 'email': 'user@example.com', 'name': 'Test User',
            'role': 'editor', 'is_active': True,
            'password_hash': '$2b$12$somehash'
        }
        chain = mock_chain(make_mock_response([user_data]))
        mock_sb.table.return_value = chain

        with patch('index._verify_password', return_value=(False, False)):
            resp = client.post('/api/auth/login', json={
                'email': 'user@example.com',
                'password': 'wrongpass'
            })
        assert resp.status_code == 401

    def test_login_invalid_email_format(self, client):
        """Login with invalid email format returns 400."""
        resp = client.post('/api/auth/login', json={
            'email': 'not-an-email',
            'password': 'test-password-123'
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'email' in data.get('error', {}).get('message', '').lower()


class TestVerify:
    """Tests for GET /api/auth/verify."""

    def test_verify_valid_token(self, client, auth_token):
        """Verify with valid token returns 200."""
        resp = client.get('/api/auth/verify', headers={
            'Authorization': f'Bearer {auth_token}'
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['valid'] is True

    def test_verify_invalid_token(self, client):
        """Verify with invalid token returns 401."""
        resp = client.get('/api/auth/verify', headers={
            'Authorization': 'Bearer invalid-token-here'
        })
        assert resp.status_code == 401
        data = resp.get_json()
        assert data['valid'] is False

    def test_verify_expired_token(self, client):
        """Verify with expired token returns 401."""
        import index
        # Create a token with old timestamp
        old_ts = str(int(time.time()) - 90000)  # > 86400 seconds ago
        payload = f"test@example.com:{old_ts}"
        sig = index._sign(payload)
        expired_token = f"{payload}:{sig}"
        resp = client.get('/api/auth/verify', headers={
            'Authorization': f'Bearer {expired_token}'
        })
        assert resp.status_code == 401

    def test_verify_no_token(self, client):
        """Verify without token returns 401."""
        resp = client.get('/api/auth/verify')
        assert resp.status_code == 401


class TestProtectedEndpoints:
    """Tests for auth decorator on protected endpoints."""

    def test_protected_endpoint_no_token_returns_401(self, client, mock_sb):
        """Accessing a protected endpoint without a token returns 401."""
        resp = client.get('/api/contracts')
        assert resp.status_code == 401

    def test_protected_endpoint_with_valid_token(self, client, auth_headers, mock_sb):
        """Accessing a protected endpoint with valid token succeeds."""
        chain = mock_chain(make_mock_response([], count=0))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts', headers=auth_headers)
        assert resp.status_code == 200

    def test_auth_skipped_when_password_empty(self, client, mock_sb):
        """When APP_PASSWORD is empty, auth is skipped entirely."""
        import config, auth, index
        originals = (config.PASSWORD, auth.PASSWORD, index.PASSWORD)
        try:
            config.PASSWORD = auth.PASSWORD = index.PASSWORD = ""
            chain = mock_chain(make_mock_response([], count=0))
            mock_sb.table.return_value = chain
            resp = client.get('/api/contracts')
            assert resp.status_code == 200
        finally:
            config.PASSWORD, auth.PASSWORD, index.PASSWORD = originals


class TestTokenRefresh:
    """Tests for POST /api/auth/refresh."""

    def test_refresh_valid_token(self, client, auth_token):
        """Refreshing a valid token returns a new token."""
        resp = client.post('/api/auth/refresh', headers={
            'Authorization': f'Bearer {auth_token}'
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'token' in data
        # Token may have same timestamp if test runs within the same second,
        # so just verify it's a valid token string with the expected format.
        assert ':' in data['token']

    def test_refresh_no_token(self, client):
        """Refreshing without a token returns 401."""
        resp = client.post('/api/auth/refresh')
        assert resp.status_code == 401

    def test_refresh_invalid_token(self, client):
        """Refreshing an invalid token returns 401."""
        resp = client.post('/api/auth/refresh', headers={
            'Authorization': 'Bearer garbage-token'
        })
        assert resp.status_code == 401


class TestLogout:
    """Tests for POST /api/auth/logout."""

    def test_logout_revokes_token(self, client, auth_token):
        """Logout revokes the token so it cannot be used again."""
        # First verify it works
        resp = client.get('/api/auth/verify', headers={
            'Authorization': f'Bearer {auth_token}'
        })
        assert resp.status_code == 200

        # Logout
        resp = client.post('/api/auth/logout', headers={
            'Authorization': f'Bearer {auth_token}'
        })
        assert resp.status_code == 200
        assert resp.get_json()['message'] == 'Logged out'

        # Now the token should be invalid
        resp = client.get('/api/auth/verify', headers={
            'Authorization': f'Bearer {auth_token}'
        })
        assert resp.status_code == 401

    def test_logout_without_token(self, client):
        """Logout without a token still returns 200."""
        resp = client.post('/api/auth/logout')
        assert resp.status_code == 200


class TestRateLimiting:
    """Tests for rate limiting."""

    def test_rate_limit_exceeded(self, client, auth_headers, mock_sb):
        """Exceeding rate limit returns 429."""
        import config
        original_limit = config.RATE_LIMIT
        try:
            config.RATE_LIMIT = 5
            chain = mock_chain(make_mock_response([], count=0))
            mock_sb.table.return_value = chain

            # Make requests up to the limit
            for _ in range(5):
                resp = client.get('/api/contracts', headers=auth_headers)
                assert resp.status_code == 200

            # Next request should be rate limited
            resp = client.get('/api/contracts', headers=auth_headers)
            assert resp.status_code == 429
        finally:
            config.RATE_LIMIT = original_limit
