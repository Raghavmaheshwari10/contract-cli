"""Tests for the standalone Receivables module."""
import io
import json
import pytest
from unittest.mock import patch, MagicMock
from test_helpers import make_mock_response, mock_chain


# ─── Local fixture overrides ──────────────────────────────────────────────
# After the modular refactor, make_token lives in auth (not index). Override
# here so this test module doesn't depend on the broken conftest binding.
@pytest.fixture
def auth_token():
    from auth import make_token
    return make_token("")


@pytest.fixture
def auth_headers(auth_token):
    return {'Authorization': f'Bearer {auth_token}', 'Content-Type': 'application/json'}


@pytest.fixture
def mock_rcv_sb():
    """Patch the sb binding everywhere the receivables endpoints touch it.

    Local override of the conftest mock_sb because the post-refactor index
    module no longer re-exports `sb`.
    """
    mock = MagicMock()
    with patch('config.sb', mock), \
         patch('auth.sb', mock, create=True), \
         patch('routes.receivables.sb', mock):
        yield mock


class TestListReceivables:
    """Tests for GET /api/receivables."""

    def test_list_empty(self, client, auth_headers, mock_rcv_sb):
        chain = mock_chain(make_mock_response([], count=0))
        mock_rcv_sb.table.return_value = chain
        resp = client.get('/api/receivables', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['data'] == []
        assert data['total'] == 0

    def test_list_with_overdue_reclassification(self, client, auth_headers, mock_rcv_sb):
        rows = [{
            'id': 1, 'client_name': 'Acme', 'amount': 1000,
            'status': 'pending', 'due_date': '2020-01-01',
        }]
        chain = mock_chain(make_mock_response(rows, count=1))
        mock_rcv_sb.table.return_value = chain
        resp = client.get('/api/receivables', headers=auth_headers)
        assert resp.status_code == 200
        # Past-due pending row should be classified as overdue on read
        assert resp.get_json()['data'][0]['status'] == 'overdue'


class TestCreateReceivable:
    """Tests for POST /api/receivables."""

    def test_create_success(self, client, auth_headers, mock_rcv_sb):
        created = {'id': 7, 'client_name': 'Globex', 'amount': 5000, 'status': 'pending'}
        chain = mock_chain(make_mock_response([created]))
        mock_rcv_sb.table.return_value = chain
        resp = client.post('/api/receivables', headers=auth_headers, json={
            'client_name': 'Globex',
            'amount': 5000,
        })
        assert resp.status_code == 201
        assert resp.get_json()['id'] == 7

    def test_create_missing_client_name(self, client, auth_headers, mock_rcv_sb):
        resp = client.post('/api/receivables', headers=auth_headers, json={'amount': 100})
        assert resp.status_code == 400
        assert 'client_name' in resp.get_json()['error']['message']

    def test_create_missing_amount(self, client, auth_headers, mock_rcv_sb):
        resp = client.post('/api/receivables', headers=auth_headers, json={'client_name': 'X'})
        assert resp.status_code == 400
        assert 'amount' in resp.get_json()['error']['message'].lower()

    def test_create_invalid_status(self, client, auth_headers, mock_rcv_sb):
        resp = client.post('/api/receivables', headers=auth_headers, json={
            'client_name': 'X', 'amount': 100, 'status': 'bogus',
        })
        assert resp.status_code == 400


class TestUpdateReceivable:
    """Tests for PATCH /api/receivables/<id>."""

    def test_mark_paid_sets_paid_date(self, client, auth_headers, mock_rcv_sb):
        updated = {'id': 1, 'status': 'paid', 'paid_date': '2026-04-25'}
        chain = mock_chain(make_mock_response([updated]))
        mock_rcv_sb.table.return_value = chain
        resp = client.patch('/api/receivables/1', headers=auth_headers, json={'status': 'paid'})
        assert resp.status_code == 200
        # The route should auto-fill paid_date when status flips to paid
        # Inspect the .update() call argument
        update_call = mock_rcv_sb.table.return_value.update.call_args
        assert update_call is not None
        update_payload = update_call[0][0]
        assert update_payload['status'] == 'paid'
        assert update_payload.get('paid_date'), 'paid_date should be auto-set'

    def test_update_empty_body_returns_400(self, client, auth_headers, mock_rcv_sb):
        resp = client.patch('/api/receivables/1', headers=auth_headers, json={})
        assert resp.status_code == 400


class TestDashboard:
    """Tests for GET /api/receivables/dashboard."""

    def test_dashboard_shape(self, client, auth_headers, mock_rcv_sb):
        rows = [
            {'id': 1, 'client_name': 'A', 'amount': 1000, 'status': 'pending',
             'invoice_date': '2026-04-01', 'due_date': '2026-04-15'},
            {'id': 2, 'client_name': 'B', 'amount': 2000, 'status': 'paid',
             'invoice_date': '2026-04-05', 'due_date': '2026-04-20', 'paid_date': '2026-04-10'},
        ]
        chain = mock_chain(make_mock_response(rows))
        mock_rcv_sb.table.return_value = chain
        resp = client.get('/api/receivables/dashboard', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'total_outstanding' in d
        assert 'total_paid' in d
        assert 'aging' in d and set(d['aging'].keys()) == {'0_30', '31_60', '61_90', '90_plus'}
        assert 'top_clients' in d
        assert 'trend' in d and len(d['trend']) == 12
        assert d['total_paid'] == 2000.0
        assert d['total_outstanding'] == 1000.0

    def test_dashboard_aging_buckets(self, client, auth_headers, mock_rcv_sb):
        # Row with due_date 45 days in the past should land in 31_60 bucket.
        from datetime import date, timedelta
        old_due = (date.today() - timedelta(days=45)).isoformat()
        rows = [{'id': 1, 'client_name': 'A', 'amount': 500, 'status': 'pending',
                 'invoice_date': old_due, 'due_date': old_due}]
        chain = mock_chain(make_mock_response(rows))
        mock_rcv_sb.table.return_value = chain
        resp = client.get('/api/receivables/dashboard', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['aging']['31_60'] == 500.0


class TestCSVImport:
    """Tests for POST /api/receivables/import."""

    def test_import_valid_rows(self, client, auth_headers, mock_rcv_sb):
        # Insert chain returns success for every row
        chain = mock_chain(make_mock_response([{'id': 1}]))
        mock_rcv_sb.table.return_value = chain
        csv_data = (
            "client_name,amount,status\n"
            "Acme,1000,pending\n"
            "Globex,2500,paid\n"
        )
        resp = client.post(
            '/api/receivables/import',
            headers={'Authorization': auth_headers['Authorization']},
            data={'file': (io.BytesIO(csv_data.encode()), 'r.csv')},
            content_type='multipart/form-data',
        )
        assert resp.status_code == 200
        d = resp.get_json()
        assert d['imported'] == 2
        assert d['skipped'] == 0

    def test_import_skips_invalid_rows(self, client, auth_headers, mock_rcv_sb):
        chain = mock_chain(make_mock_response([{'id': 1}]))
        mock_rcv_sb.table.return_value = chain
        csv_data = (
            "client_name,amount\n"
            "Acme,1000\n"
            ",500\n"          # missing client_name
            "Globex,bad\n"    # invalid amount
            "Initech,750\n"
        )
        resp = client.post(
            '/api/receivables/import',
            headers={'Authorization': auth_headers['Authorization']},
            data={'file': (io.BytesIO(csv_data.encode()), 'r.csv')},
            content_type='multipart/form-data',
        )
        assert resp.status_code == 200
        d = resp.get_json()
        assert d['imported'] == 2
        assert d['skipped'] == 2

    def test_import_rejects_non_csv(self, client, auth_headers, mock_rcv_sb):
        resp = client.post(
            '/api/receivables/import',
            headers={'Authorization': auth_headers['Authorization']},
            data={'file': (io.BytesIO(b'x'), 'r.txt')},
            content_type='multipart/form-data',
        )
        assert resp.status_code == 400
