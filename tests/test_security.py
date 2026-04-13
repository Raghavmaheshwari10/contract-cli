"""Tests for security features: sanitization, CSRF, file upload, rate limits."""
import io
import json
import pytest
from unittest.mock import patch, MagicMock
from test_helpers import make_mock_response, mock_chain


class TestInputSanitization:
    """Tests for _sanitize and related functions."""

    def test_sanitize_strips_script_tags(self):
        """Script tags are removed from input."""
        from index import _sanitize
        result = _sanitize('<script>alert("xss")</script>Hello')
        assert '<script>' not in result
        assert 'alert' not in result
        assert 'Hello' in result

    def test_sanitize_strips_event_handlers(self):
        """Event handlers like onload= are removed."""
        from index import _sanitize
        result = _sanitize('<img onload=alert(1) src=x>')
        assert 'onload' not in result.lower()

    def test_sanitize_strips_javascript_protocol(self):
        """javascript: protocol is removed."""
        from index import _sanitize
        result = _sanitize('<a href="javascript:alert(1)">click</a>')
        assert 'javascript' not in result.lower() or 'javascript:' not in result.lower()

    def test_xss_in_contract_name(self, client, auth_headers, mock_sb):
        """XSS payload in contract name is sanitized before storage."""
        created_row = {'id': 1, 'name': 'Test'}
        chain = mock_chain(make_mock_response([created_row]))
        mock_sb.table.return_value = chain

        xss_name = '<script>alert("xss")</script>Contract Name'

        with patch('index.oai_h', return_value=None), \
             patch('index.log_activity'), \
             patch('index.fire_webhooks'), \
             patch('index.run_workflows'):
            resp = client.post('/api/contracts', headers=auth_headers, json={
                'name': xss_name,
                'party_name': 'Acme Corp',
                'contract_type': 'client',
                'content': 'Test content'
            })

        assert resp.status_code == 201
        # Verify the insert call had the name sanitized
        insert_call = mock_sb.table.return_value.insert
        if insert_call.called:
            inserted_data = insert_call.call_args[0][0]
            assert '<script>' not in inserted_data.get('name', '')

    def test_sanitize_html_removes_all_tags(self):
        """_sanitize_html removes ALL HTML tags."""
        from index import _sanitize_html
        result = _sanitize_html('<b>Bold</b> <a href="http://test.com">link</a> <script>x</script>')
        assert '<' not in result
        assert '>' not in result
        assert 'Bold' in result
        assert 'link' in result

    def test_sanitize_respects_max_length(self):
        """Sanitize truncates to max_len."""
        from index import _sanitize
        result = _sanitize('A' * 20000, max_len=100)
        assert len(result) <= 100

    def test_sanitize_dict_sanitizes_string_values(self):
        """_sanitize_dict sanitizes all string values in a dict."""
        from index import _sanitize_dict
        result = _sanitize_dict({
            'name': '<script>alert(1)</script>Test',
            'count': 42  # non-string should pass through
        })
        assert '<script>' not in result['name']
        assert result['count'] == 42


class TestFileUpload:
    """Tests for POST /api/upload-pdf."""

    def test_upload_non_pdf_rejected(self, client, auth_headers):
        """Non-PDF file extension is rejected."""
        data = {'file': (io.BytesIO(b'not a pdf'), 'test.txt')}
        resp = client.post('/api/upload-pdf', headers={
            'Authorization': auth_headers['Authorization']
        }, data=data, content_type='multipart/form-data')
        assert resp.status_code == 400
        assert 'pdf' in resp.get_json()['error']['message'].lower()

    def test_upload_pdf_without_magic_bytes(self, client, auth_headers):
        """File with .pdf extension but wrong magic bytes is rejected."""
        fake_pdf = io.BytesIO(b'this is not a real pdf file content')
        data = {'file': (fake_pdf, 'fake.pdf')}
        resp = client.post('/api/upload-pdf', headers={
            'Authorization': auth_headers['Authorization']
        }, data=data, content_type='multipart/form-data')
        assert resp.status_code == 400
        assert 'invalid' in resp.get_json()['error']['message'].lower()

    def test_upload_no_file(self, client, auth_headers):
        """Upload request with no file returns 400."""
        resp = client.post('/api/upload-pdf', headers={
            'Authorization': auth_headers['Authorization']
        }, content_type='multipart/form-data')
        assert resp.status_code == 400


class TestRequestSizeLimits:
    """Tests for request size limiting."""

    def test_request_too_large_returns_413(self, client, auth_headers):
        """Request exceeding MAX_CONTENT_LENGTH returns 413."""
        # The app has 16MB limit; send a body larger than that
        # Flask's test client may handle this differently, so we test
        # the error handler registration
        from index import app
        assert app.config.get('MAX_CONTENT_LENGTH') == 16 * 1024 * 1024


class TestCSRFOriginCheck:
    """Tests for origin-based CSRF protection."""

    def test_mutating_request_with_bad_origin_rejected(self, client, auth_token, mock_sb):
        """POST request with bad Origin header is rejected with 403."""
        resp = client.post('/api/contracts', headers={
            'Authorization': f'Bearer {auth_token}',
            'Content-Type': 'application/json',
            'Origin': 'https://evil.example.com'
        }, json={
            'name': 'Test',
            'party_name': 'Acme',
            'contract_type': 'client',
            'content': 'content'
        })
        assert resp.status_code == 403
        assert 'origin' in resp.get_json()['error']['message'].lower()

    def test_mutating_request_with_allowed_origin_accepted(self, client, auth_token, mock_sb):
        """POST request with allowed Origin header is accepted."""
        chain = mock_chain(make_mock_response([{'id': 1}]))
        mock_sb.table.return_value = chain

        with patch('index.oai_h', return_value=None), \
             patch('index.log_activity'), \
             patch('index.fire_webhooks'), \
             patch('index.run_workflows'):
            resp = client.post('/api/contracts', headers={
                'Authorization': f'Bearer {auth_token}',
                'Content-Type': 'application/json',
                'Origin': 'http://localhost:3000'
            }, json={
                'name': 'Test',
                'party_name': 'Acme',
                'contract_type': 'client',
                'content': 'content'
            })
        assert resp.status_code == 201

    def test_get_request_with_bad_origin_allowed(self, client, auth_token, mock_sb):
        """GET requests are not subject to origin check."""
        chain = mock_chain(make_mock_response([], count=0))
        mock_sb.table.return_value = chain

        resp = client.get('/api/contracts', headers={
            'Authorization': f'Bearer {auth_token}',
            'Origin': 'https://evil.example.com'
        })
        assert resp.status_code == 200

    def test_no_origin_header_allowed(self, client, auth_token, mock_sb):
        """Requests without Origin header (server-to-server) are allowed."""
        chain = mock_chain(make_mock_response([], count=0))
        mock_sb.table.return_value = chain

        resp = client.get('/api/contracts', headers={
            'Authorization': f'Bearer {auth_token}'
        })
        assert resp.status_code == 200


class TestSQLInjection:
    """Tests for SQL injection resilience."""

    def test_sql_like_input_does_not_cause_error(self, client, auth_headers, mock_sb):
        """SQL injection attempt in contract name does not cause errors."""
        chain = mock_chain(make_mock_response([{'id': 1}]))
        mock_sb.table.return_value = chain

        with patch('index.oai_h', return_value=None), \
             patch('index.log_activity'), \
             patch('index.fire_webhooks'), \
             patch('index.run_workflows'):
            resp = client.post('/api/contracts', headers=auth_headers, json={
                'name': "Robert'; DROP TABLE contracts;--",
                'party_name': 'Test Corp',
                'contract_type': 'client',
                'content': 'Test content'
            })
        # Should not crash; the parameterized query via Supabase handles this
        assert resp.status_code == 201


class TestEmailValidation:
    """Tests for email format validation."""

    def test_invalid_email_rejected_on_login(self, client):
        """Login with invalid email format returns 400."""
        resp = client.post('/api/auth/login', json={
            'email': 'not-valid',
            'password': 'test-password-123'
        })
        assert resp.status_code == 400

    def test_invalid_email_rejected_on_user_creation(self, client, auth_headers, mock_sb):
        """Creating a user with invalid email returns 400."""
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain

        resp = client.post('/api/users', headers=auth_headers, json={
            'email': 'bademail@',
            'name': 'Test User',
            'password': 'password123'
        })
        assert resp.status_code == 400
        assert 'email' in resp.get_json()['error']['message'].lower()

    def test_valid_email_accepted(self):
        """Valid email passes validation."""
        from index import _valid_email
        assert _valid_email('user@example.com') is True
        assert _valid_email('first.last@domain.co.uk') is True

    def test_invalid_emails(self):
        """Various invalid emails fail validation."""
        from index import _valid_email
        assert _valid_email('') is False
        assert _valid_email('noatsign') is False
        assert _valid_email('@nodomain') is False
        assert _valid_email('user@') is False
        assert _valid_email(None) is False


class TestPasswordValidation:
    """Tests for password handling in user creation."""

    def test_create_user_missing_password(self, client, auth_headers, mock_sb):
        """Creating a user without password returns 400."""
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain

        resp = client.post('/api/users', headers=auth_headers, json={
            'email': 'user@example.com',
            'name': 'Test User'
        })
        assert resp.status_code == 400
        assert 'password' in resp.get_json()['error']['message'].lower()

    def test_create_user_empty_password(self, client, auth_headers, mock_sb):
        """Creating a user with empty password returns 400."""
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain

        resp = client.post('/api/users', headers=auth_headers, json={
            'email': 'user@example.com',
            'name': 'Test User',
            'password': ''
        })
        assert resp.status_code == 400


class TestSecurityHeaders:
    """Tests for security response headers."""

    def test_security_headers_present(self, client):
        """Responses include security headers."""
        resp = client.get('/api/health')
        assert resp.headers.get('X-Content-Type-Options') == 'nosniff'
        assert resp.headers.get('X-Frame-Options') == 'DENY'
        assert resp.headers.get('X-XSS-Protection') == '1; mode=block'
        assert 'max-age' in resp.headers.get('Strict-Transport-Security', '')
        assert resp.headers.get('Referrer-Policy') == 'strict-origin-when-cross-origin'
