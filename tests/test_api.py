"""General API tests: health, export, search, error handling."""
import json
import pytest
from unittest.mock import patch, MagicMock
from test_helpers import make_mock_response, mock_chain


class TestHealthEndpoint:
    """Tests for GET /api/health."""

    def test_health_returns_ok(self, client):
        """Health endpoint returns ok status."""
        resp = client.get('/api/health')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'ok'
        assert 'db' in data
        assert 'ai' in data

    def test_health_no_auth_required(self, client):
        """Health endpoint does not require authentication."""
        resp = client.get('/api/health')
        assert resp.status_code == 200


class TestExportEndpoint:
    """Tests for GET /api/export."""

    def test_export_csv(self, client, auth_headers, mock_sb):
        """Export endpoint returns CSV data."""
        contracts = [
            {'id': 1, 'name': 'Contract A', 'party_name': 'Acme',
             'contract_type': 'client', 'status': 'draft',
             'start_date': '2024-01-01', 'end_date': '2025-01-01',
             'value': 'INR 100000', 'department': 'Legal',
             'added_on': '2024-01-01', 'notes': 'Test'}
        ]
        chain = mock_chain(make_mock_response(contracts))
        mock_sb.table.return_value = chain

        resp = client.get('/api/export', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.content_type == 'text/csv; charset=utf-8'
        csv_text = resp.data.decode('utf-8')
        assert 'ID' in csv_text
        assert 'Contract A' in csv_text

    def test_export_json_format(self, client, auth_headers, mock_sb):
        """Export with format=json returns JSON."""
        contracts = [{'id': 1, 'name': 'Test'}]
        chain = mock_chain(make_mock_response(contracts))
        mock_sb.table.return_value = chain

        resp = client.get('/api/export?format=json', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.content_type.startswith('application/json')
        data = resp.get_json()
        assert isinstance(data, list)
        assert data[0]['name'] == 'Test'

    def test_export_empty_contracts(self, client, auth_headers, mock_sb):
        """Export with no contracts returns empty CSV with headers."""
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain

        resp = client.get('/api/export', headers=auth_headers)
        assert resp.status_code == 200
        csv_text = resp.data.decode('utf-8')
        assert 'ID' in csv_text  # header row should still be present


class TestSearchEndpoint:
    """Tests for GET /api/search."""

    def test_search_empty_query_returns_empty(self, client, auth_headers, mock_sb):
        """Search with empty query returns empty list."""
        resp = client.get('/api/search?q=', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_search_no_query_returns_empty(self, client, auth_headers, mock_sb):
        """Search without query parameter returns empty list."""
        resp = client.get('/api/search', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_search_with_query(self, client, auth_headers, mock_sb):
        """Search with valid query returns results."""
        results = [{'id': 1, 'name': 'NDA Agreement'}]
        chain = mock_chain(make_mock_response(results))
        mock_sb.table.return_value = chain

        resp = client.get('/api/search?q=NDA', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['name'] == 'NDA Agreement'


class TestErrorHandling:
    """Tests for error handlers."""

    def test_unknown_route_returns_404(self, client):
        """Unknown API routes return 404 JSON error."""
        resp = client.get('/api/nonexistent-endpoint')
        assert resp.status_code == 404
        data = resp.get_json()
        assert 'error' in data
        assert data['error']['code'] == 404

    def test_404_returns_json(self, client):
        """404 response is JSON, not HTML."""
        resp = client.get('/api/does-not-exist')
        assert resp.content_type.startswith('application/json')


class TestDBNotConfigured:
    """Tests for endpoints when DB is not configured."""

    def test_contracts_without_db_returns_503(self, client, auth_headers):
        """Endpoints requiring DB return 503 when DB is not configured."""
        # sb is None when SUPABASE_URL/KEY are empty (which they are in tests)
        import index
        original_sb = index.sb
        try:
            index.sb = None
            resp = client.get('/api/contracts', headers=auth_headers)
            assert resp.status_code == 503
            assert 'db' in resp.get_json()['error']['message'].lower()
        finally:
            index.sb = original_sb


class TestCommentsEndpoint:
    """Tests for contract comments."""

    def test_add_comment_without_content_returns_400(self, client, auth_headers, mock_sb):
        """Adding a comment without content returns 400."""
        resp = client.post('/api/contracts/1/comments', headers=auth_headers, json={})
        assert resp.status_code == 400
        assert 'content' in resp.get_json()['error']['message'].lower()

    def test_add_comment_success(self, client, auth_headers, mock_sb):
        """Adding a comment with content returns 201."""
        comment = {'id': 1, 'content': 'Looks good', 'contract_id': 1}
        chain = mock_chain(make_mock_response([comment]))
        mock_sb.table.return_value = chain

        with patch('index.log_activity'), \
             patch('index.create_notification'):
            resp = client.post('/api/contracts/1/comments', headers=auth_headers, json={
                'content': 'Looks good',
                'user_name': 'Tester'
            })
        assert resp.status_code == 201


class TestUserManagement:
    """Tests for user management endpoints."""

    def test_create_user_duplicate_email(self, client, auth_headers, mock_sb):
        """Creating a user with duplicate email returns 409."""
        # First call for select (existing check): returns data
        # Need to handle multiple table calls differently
        def table_side_effect(table_name):
            chain = mock_chain()
            if table_name == 'clm_users':
                chain.execute.return_value = make_mock_response([{'id': 1}])
            return chain
        mock_sb.table.side_effect = table_side_effect

        resp = client.post('/api/users', headers=auth_headers, json={
            'email': 'existing@example.com',
            'name': 'Duplicate User',
            'password': 'password123'
        })
        assert resp.status_code == 409

    def test_create_user_invalid_role_defaults_to_viewer(self, client, auth_headers, mock_sb):
        """Creating a user with invalid role defaults to viewer."""
        call_count = {'n': 0}

        def table_side_effect(table_name):
            chain = mock_chain()
            if table_name == 'clm_users':
                call_count['n'] += 1
                if call_count['n'] == 1:
                    # First clm_users call: select for duplicate check - no data
                    chain.execute.return_value = make_mock_response([])
                else:
                    # Second clm_users call: insert - return created user
                    chain.execute.return_value = make_mock_response([{'id': 2, 'email': 'new@example.com'}])
            return chain
        mock_sb.table.side_effect = table_side_effect

        resp = client.post('/api/users', headers=auth_headers, json={
            'email': 'new@example.com',
            'name': 'New User',
            'password': 'password123',
            'role': 'superadmin'  # invalid role
        })
        assert resp.status_code == 201
