"""Tests for contract CRUD and status transition endpoints."""
import json
import pytest
from unittest.mock import patch, MagicMock, call
from test_helpers import make_mock_response, mock_chain


class TestCreateContract:
    """Tests for POST /api/contracts."""

    def test_create_contract_valid_data(self, client, auth_headers, mock_sb):
        """Creating a contract with valid data returns 201."""
        created_row = {'id': 42, 'name': 'Test Contract'}
        chain = mock_chain(make_mock_response([created_row]))
        mock_sb.table.return_value = chain

        with patch('index.oai_h', return_value=None), \
             patch('index.log_activity'), \
             patch('index.fire_webhooks'), \
             patch('index.run_workflows'):
            resp = client.post('/api/contracts', headers=auth_headers, json={
                'name': 'Test Contract',
                'party_name': 'Acme Corp',
                'contract_type': 'client',
                'content': 'This is a test contract.'
            })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['id'] == 42
        assert 'Created' in data['message']

    def test_create_contract_missing_name(self, client, auth_headers, mock_sb):
        """Creating a contract without name returns 400."""
        resp = client.post('/api/contracts', headers=auth_headers, json={
            'party_name': 'Acme Corp',
            'contract_type': 'client',
            'content': 'Some content'
        })
        assert resp.status_code == 400
        assert 'name' in resp.get_json()['error']['message'].lower()

    def test_create_contract_missing_content(self, client, auth_headers, mock_sb):
        """Creating a contract without content returns 400."""
        resp = client.post('/api/contracts', headers=auth_headers, json={
            'name': 'Test',
            'party_name': 'Acme Corp',
            'contract_type': 'client'
        })
        assert resp.status_code == 400
        assert 'content' in resp.get_json()['error']['message'].lower()

    def test_create_contract_missing_party_name(self, client, auth_headers, mock_sb):
        """Creating a contract without party_name returns 400."""
        resp = client.post('/api/contracts', headers=auth_headers, json={
            'name': 'Test',
            'contract_type': 'client',
            'content': 'content'
        })
        assert resp.status_code == 400
        assert 'party_name' in resp.get_json()['error']['message'].lower()

    def test_create_contract_invalid_type(self, client, auth_headers, mock_sb):
        """Creating a contract with invalid type returns 400."""
        resp = client.post('/api/contracts', headers=auth_headers, json={
            'name': 'Test',
            'party_name': 'Acme Corp',
            'contract_type': 'invalid',
            'content': 'content'
        })
        assert resp.status_code == 400
        assert 'type' in resp.get_json()['error']['message'].lower()


class TestGetContract:
    """Tests for GET /api/contracts/<cid>."""

    def test_get_existing_contract(self, client, auth_headers, mock_sb):
        """Getting an existing contract returns 200."""
        contract_data = {'id': 1, 'name': 'Test', 'content': 'Hello'}
        chain = mock_chain(make_mock_response([contract_data]))
        mock_sb.table.return_value = chain

        resp = client.get('/api/contracts/1', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['name'] == 'Test'

    def test_get_nonexistent_contract(self, client, auth_headers, mock_sb):
        """Getting a non-existent contract returns 404."""
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain

        resp = client.get('/api/contracts/9999', headers=auth_headers)
        assert resp.status_code == 404


class TestUpdateContract:
    """Tests for PUT /api/contracts/<cid>."""

    def test_update_contract_success(self, client, auth_headers, mock_sb):
        """Updating a contract with valid data returns 200."""
        existing = {'id': 1, 'name': 'Old Name', 'status': 'draft',
                    'content': 'old', 'content_html': '', 'updated_at': '2024-01-01T00:00:00'}
        chain = mock_chain(make_mock_response([existing]))
        mock_sb.table.return_value = chain

        with patch('index.oai_h', return_value=None), \
             patch('index.log_activity'):
            resp = client.put('/api/contracts/1', headers=auth_headers, json={
                'name': 'New Name'
            })
        assert resp.status_code == 200
        assert 'Updated' in resp.get_json()['message']

    def test_update_executed_contract_returns_400(self, client, auth_headers, mock_sb):
        """Updating an executed contract returns 400."""
        existing = {'id': 1, 'name': 'Locked', 'status': 'executed',
                    'content': 'x', 'content_html': '', 'updated_at': '2024-01-01'}
        chain = mock_chain(make_mock_response([existing]))
        mock_sb.table.return_value = chain

        resp = client.put('/api/contracts/1', headers=auth_headers, json={
            'name': 'Try to change'
        })
        assert resp.status_code == 400
        assert 'executed' in resp.get_json()['error']['message'].lower()

    def test_update_optimistic_locking_conflict(self, client, auth_headers, mock_sb):
        """Mismatched updated_at triggers 409 conflict."""
        existing = {'id': 1, 'name': 'Test', 'status': 'draft',
                    'content': 'x', 'content_html': '',
                    'updated_at': '2024-01-02T00:00:00'}
        chain = mock_chain(make_mock_response([existing]))
        mock_sb.table.return_value = chain

        resp = client.put('/api/contracts/1', headers=auth_headers, json={
            'name': 'Updated',
            'updated_at': '2024-01-01T00:00:00'  # stale
        })
        assert resp.status_code == 409

    def test_update_nothing_to_update(self, client, auth_headers, mock_sb):
        """Sending no updatable fields returns 400."""
        existing = {'id': 1, 'name': 'Test', 'status': 'draft',
                    'content': 'x', 'content_html': '', 'updated_at': ''}
        chain = mock_chain(make_mock_response([existing]))
        mock_sb.table.return_value = chain

        resp = client.put('/api/contracts/1', headers=auth_headers, json={
            'irrelevant_field': 'value'
        })
        assert resp.status_code == 400

    def test_update_nonexistent_contract(self, client, auth_headers, mock_sb):
        """Updating a non-existent contract returns 404."""
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain

        resp = client.put('/api/contracts/9999', headers=auth_headers, json={
            'name': 'New Name'
        })
        assert resp.status_code == 404


class TestDeleteContract:
    """Tests for DELETE /api/contracts/<cid>."""

    def test_delete_existing_contract(self, client, auth_headers, mock_sb):
        """Deleting an existing contract returns 200."""
        chain = mock_chain(make_mock_response([{'id': 1}]))
        mock_sb.table.return_value = chain

        resp = client.delete('/api/contracts/1', headers=auth_headers)
        assert resp.status_code == 200
        assert 'Deleted' in resp.get_json()['message']

    def test_delete_nonexistent_contract(self, client, auth_headers, mock_sb):
        """Deleting a non-existent contract returns 404."""
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain

        resp = client.delete('/api/contracts/9999', headers=auth_headers)
        assert resp.status_code == 404


class TestStatusTransitions:
    """Tests for PUT /api/contracts/<cid>/status."""

    def _setup_status_mock(self, mock_sb, current_status, contract_id=1):
        """Set up mock for status transition tests."""
        contract = {'id': contract_id, 'name': 'Test Contract', 'status': current_status}

        # The status endpoint calls multiple sb.table() operations:
        # 1. contract lookup via _transition_status
        # 2. update
        # 3. log_activity
        # 4. fire_webhooks
        # etc.
        chain = mock_chain(make_mock_response([contract]))
        mock_sb.table.return_value = chain

        return contract

    def test_draft_to_pending(self, client, auth_headers, mock_sb):
        """Valid transition: draft -> pending."""
        self._setup_status_mock(mock_sb, 'draft')

        with patch('index.log_activity'), \
             patch('index.fire_webhooks'), \
             patch('index.create_notification'), \
             patch('index.run_workflows'):
            resp = client.put('/api/contracts/1/status', headers=auth_headers, json={
                'status': 'pending'
            })
        assert resp.status_code == 200

    def test_draft_to_in_review(self, client, auth_headers, mock_sb):
        """Valid transition: draft -> in_review."""
        self._setup_status_mock(mock_sb, 'draft')

        with patch('index.log_activity'), \
             patch('index.fire_webhooks'), \
             patch('index.create_notification'), \
             patch('index.run_workflows'):
            resp = client.put('/api/contracts/1/status', headers=auth_headers, json={
                'status': 'in_review'
            })
        assert resp.status_code == 200

    def test_draft_to_executed_invalid(self, client, auth_headers, mock_sb):
        """Invalid transition: draft -> executed returns 400."""
        self._setup_status_mock(mock_sb, 'draft')

        resp = client.put('/api/contracts/1/status', headers=auth_headers, json={
            'status': 'executed'
        })
        assert resp.status_code == 400
        assert 'cannot transition' in resp.get_json()['error']['message'].lower()

    def test_executed_to_draft_invalid(self, client, auth_headers, mock_sb):
        """Invalid transition: executed -> draft returns 400."""
        self._setup_status_mock(mock_sb, 'executed')

        resp = client.put('/api/contracts/1/status', headers=auth_headers, json={
            'status': 'draft'
        })
        assert resp.status_code == 400

    def test_executed_to_rejected_valid(self, client, auth_headers, mock_sb):
        """Valid transition: executed -> rejected."""
        self._setup_status_mock(mock_sb, 'executed')

        with patch('index.log_activity'), \
             patch('index.fire_webhooks'), \
             patch('index.create_notification'), \
             patch('index.run_workflows'):
            resp = client.put('/api/contracts/1/status', headers=auth_headers, json={
                'status': 'rejected'
            })
        assert resp.status_code == 200

    def test_rejected_to_draft_valid(self, client, auth_headers, mock_sb):
        """Valid transition: rejected -> draft."""
        self._setup_status_mock(mock_sb, 'rejected')

        with patch('index.log_activity'), \
             patch('index.fire_webhooks'), \
             patch('index.create_notification'), \
             patch('index.run_workflows'):
            resp = client.put('/api/contracts/1/status', headers=auth_headers, json={
                'status': 'draft'
            })
        assert resp.status_code == 200

    def test_in_review_to_executed_with_pending_approvals(self, client, auth_headers, mock_sb):
        """Execution with pending approvals returns 400."""
        contract = {'id': 1, 'name': 'Test', 'status': 'in_review'}
        pending_approvals = [{'id': 10}, {'id': 11}]

        # First call: contract lookup; second call: approvals lookup
        call_count = [0]
        def table_side_effect(table_name):
            chain = mock_chain()
            if table_name == 'contracts':
                chain.execute.return_value = make_mock_response([contract])
            elif table_name == 'contract_approvals':
                chain.execute.return_value = make_mock_response(pending_approvals)
            return chain
        mock_sb.table.side_effect = table_side_effect

        resp = client.put('/api/contracts/1/status', headers=auth_headers, json={
            'status': 'executed'
        })
        assert resp.status_code == 400
        assert 'approval' in resp.get_json()['error']['message'].lower()

    def test_in_review_to_executed_no_pending_approvals(self, client, auth_headers, mock_sb):
        """Execution with all approvals completed succeeds."""
        contract = {'id': 1, 'name': 'Test', 'status': 'in_review'}

        def table_side_effect(table_name):
            chain = mock_chain()
            if table_name == 'contracts':
                chain.execute.return_value = make_mock_response([contract])
            elif table_name == 'contract_approvals':
                chain.execute.return_value = make_mock_response([])  # no pending
            else:
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side_effect

        with patch('index.log_activity'), \
             patch('index.fire_webhooks'), \
             patch('index.create_notification'), \
             patch('index.run_workflows'):
            resp = client.put('/api/contracts/1/status', headers=auth_headers, json={
                'status': 'executed'
            })
        assert resp.status_code == 200

    def test_invalid_status_value(self, client, auth_headers, mock_sb):
        """Providing an unknown status returns 400."""
        chain = mock_chain(make_mock_response([{'id': 1, 'name': 'Test', 'status': 'draft'}]))
        mock_sb.table.return_value = chain

        resp = client.put('/api/contracts/1/status', headers=auth_headers, json={
            'status': 'unknown_status'
        })
        assert resp.status_code == 400

    def test_status_transition_contract_not_found(self, client, auth_headers, mock_sb):
        """Status transition on non-existent contract returns 404."""
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain

        resp = client.put('/api/contracts/9999/status', headers=auth_headers, json={
            'status': 'pending'
        })
        assert resp.status_code == 404


class TestListContracts:
    """Tests for GET /api/contracts."""

    def test_list_contracts_returns_paginated_data(self, client, auth_headers, mock_sb):
        """Listing contracts returns paginated results."""
        contracts = [{'id': i, 'name': f'Contract {i}'} for i in range(3)]
        chain = mock_chain(make_mock_response(contracts, count=3))
        mock_sb.table.return_value = chain

        resp = client.get('/api/contracts', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'data' in data
        assert 'total' in data
        assert 'page' in data
        assert data['total'] == 3

    def test_list_contracts_with_type_filter(self, client, auth_headers, mock_sb):
        """Listing contracts with type filter."""
        chain = mock_chain(make_mock_response([], count=0))
        mock_sb.table.return_value = chain

        resp = client.get('/api/contracts?type=client', headers=auth_headers)
        assert resp.status_code == 200
