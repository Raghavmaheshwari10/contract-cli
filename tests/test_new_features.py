"""Tests for new features: Executive Dashboard, Approval SLA, Counterparty Risk,
   Invoices, Share Links, Slack Webhook, Contract Parties, Margins, Obligations."""
import json
import pytest
from unittest.mock import patch, MagicMock
from test_helpers import make_mock_response, mock_chain


class TestExecutiveDashboard:
    """Tests for GET /api/executive-dashboard."""

    def test_exec_dashboard_returns_summary(self, client, auth_headers, mock_sb):
        contracts = [
            {'id': 1, 'name': 'C1', 'party_name': 'Acme', 'contract_type': 'client',
             'status': 'executed', 'start_date': '2025-01-01', 'end_date': '2026-05-01',
             'value': '₹10,00,000', 'department': 'Finance'},
            {'id': 2, 'name': 'C2', 'party_name': 'Beta', 'contract_type': 'vendor',
             'status': 'pending', 'start_date': '2025-06-01', 'end_date': '2026-04-01',
             'value': '₹5,00,000', 'department': 'Engineering'},
        ]

        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response(contracts)
            elif t == 'contract_approvals':
                chain.execute.return_value = make_mock_response([])
            elif t == 'contract_obligations':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side

        resp = client.get('/api/executive-dashboard', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'tcv' in d
        assert 'total_contracts' in d
        assert d['total_contracts'] == 2
        assert 'departments' in d
        assert 'at_risk' in d
        assert 'pending_approvals' in d

    def test_exec_dashboard_no_auth(self, client, mock_sb):
        resp = client.get('/api/executive-dashboard')
        assert resp.status_code == 401


class TestApprovalSLA:
    """Tests for GET /api/approvals/sla."""

    def test_sla_returns_data(self, client, auth_headers, mock_sb):
        approvals = [
            {'id': 1, 'contract_id': 10, 'approver_name': 'John',
             'status': 'pending', 'created_at': '2026-04-01T00:00:00Z'}
        ]
        contracts = [{'id': 10, 'name': 'Test Contract', 'party_name': 'Acme'}]

        def table_side(t):
            chain = mock_chain()
            if t == 'contract_approvals':
                chain.execute.return_value = make_mock_response(approvals)
            elif t == 'contracts':
                chain.execute.return_value = make_mock_response(contracts)
            return chain
        mock_sb.table.side_effect = table_side

        resp = client.get('/api/approvals/sla?threshold=3', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'approvals' in d
        assert 'total' in d
        assert 'overdue' in d
        assert d['total'] == 1
        assert d['approvals'][0]['contract_name'] == 'Test Contract'

    def test_sla_custom_threshold(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side

        resp = client.get('/api/approvals/sla?threshold=7', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['threshold_days'] == 7


class TestCounterpartyRisk:
    """Tests for GET /api/counterparty-risk."""

    def test_risk_aggregation(self, client, auth_headers, mock_sb):
        contracts = [
            {'id': 1, 'name': 'C1', 'party_name': 'Acme', 'contract_type': 'client',
             'status': 'executed', 'value': '₹10,00,000', 'end_date': '2026-04-01', 'department': 'Sales'},
            {'id': 2, 'name': 'C2', 'party_name': 'Acme', 'contract_type': 'vendor',
             'status': 'pending', 'value': '₹3,00,000', 'end_date': '2026-06-01', 'department': 'Sales'},
            {'id': 3, 'name': 'C3', 'party_name': 'Beta', 'contract_type': 'client',
             'status': 'draft', 'value': '₹5,00,000', 'end_date': '', 'department': 'Finance'},
        ]
        chain = mock_chain(make_mock_response(contracts))
        mock_sb.table.return_value = chain

        resp = client.get('/api/counterparty-risk', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert d['total_parties'] == 2
        parties = d['parties']
        # Sorted by total_value desc, Acme should be first
        assert parties[0]['party_name'] == 'Acme'
        assert parties[0]['contract_count'] == 2


class TestContractInvoices:
    """Tests for contract invoice CRUD."""

    def test_add_invoice(self, client, auth_headers, mock_sb):
        created = {'id': 1, 'invoice_number': 'INV-001', 'contract_id': 5}

        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 5}])
            elif t == 'contract_invoices':
                chain.execute.return_value = make_mock_response([created])
            return chain
        mock_sb.table.side_effect = table_side

        with patch('index.log_activity'):
            resp = client.post('/api/contracts/5/invoices', headers=auth_headers, json={
                'invoice_number': 'INV-001', 'amount': '₹50,000',
                'status': 'pending', 'po_number': 'PO-100'
            })
        assert resp.status_code == 201

    def test_add_invoice_missing_number(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 5}])
            return chain
        mock_sb.table.side_effect = table_side

        resp = client.post('/api/contracts/5/invoices', headers=auth_headers, json={
            'amount': '₹50,000'
        })
        assert resp.status_code == 400
        assert 'invoice number' in resp.get_json()['error']['message'].lower()

    def test_get_invoices(self, client, auth_headers, mock_sb):
        invoices = [{'id': 1, 'invoice_number': 'INV-001', 'amount': '₹50,000'}]
        chain = mock_chain(make_mock_response(invoices))
        mock_sb.table.return_value = chain

        resp = client.get('/api/contracts/5/invoices', headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_delete_invoice(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([{'id': 1}]))
        mock_sb.table.return_value = chain

        resp = client.delete('/api/contract-invoices/1', headers=auth_headers)
        assert resp.status_code == 200

    def test_delete_invoice_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain

        resp = client.delete('/api/contract-invoices/999', headers=auth_headers)
        assert resp.status_code == 404


class TestSlackWebhook:
    """Tests for Slack webhook settings."""

    def test_get_slack_webhook(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([{'key': 'slack_webhook_url', 'value': 'https://hooks.slack.com/test'}]))
        mock_sb.table.return_value = chain

        resp = client.get('/api/settings/slack-webhook', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['url'] == 'https://hooks.slack.com/test'

    def test_set_slack_webhook(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([{'id': 1}]))
        mock_sb.table.return_value = chain

        resp = client.post('/api/settings/slack-webhook', headers=auth_headers, json={
            'url': 'https://hooks.slack.com/new'
        })
        assert resp.status_code == 200
        assert 'saved' in resp.get_json()['message'].lower()

    def test_test_slack_no_url(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain

        resp = client.post('/api/settings/slack-test', headers=auth_headers)
        assert resp.status_code == 400


class TestShareLinks:
    """Tests for shareable contract review links."""

    def test_create_share_link(self, client, auth_headers, mock_sb):
        contract = {'id': 5, 'name': 'Test', 'status': 'executed'}
        created_link = {'id': 1, 'token': 'abc123', 'contract_id': 5}

        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([contract])
            elif t == 'contract_share_links':
                chain.execute.return_value = make_mock_response([created_link])
            return chain
        mock_sb.table.side_effect = table_side

        with patch('index.log_activity'):
            resp = client.post('/api/contracts/5/share-links', headers=auth_headers, json={
                'recipient_name': 'External User',
                'permissions': 'view',
                'expires_hours': 72
            })
        assert resp.status_code == 201
        d = resp.get_json()
        assert 'token' in d or 'link' in d or 'id' in d

    def test_view_shared_invalid_token(self, client, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain

        resp = client.get('/api/shared/invalid-token-123')
        assert resp.status_code in (404, 410)


class TestContractParties:
    """Tests for contract multi-party management."""

    def test_get_parties(self, client, auth_headers, mock_sb):
        parties = [
            {'id': 1, 'contract_id': 5, 'party_name': 'Vendor A', 'party_type': 'vendor'}
        ]
        chain = mock_chain(make_mock_response(parties))
        mock_sb.table.return_value = chain

        resp = client.get('/api/contracts/5/parties', headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_add_party(self, client, auth_headers, mock_sb):
        created = {'id': 1, 'party_name': 'Sub Corp', 'party_type': 'subcontractor'}

        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 5}])
            elif t == 'contract_parties':
                chain.execute.return_value = make_mock_response([created])
            return chain
        mock_sb.table.side_effect = table_side

        with patch('index.log_activity'):
            resp = client.post('/api/contracts/5/parties', headers=auth_headers, json={
                'party_name': 'Sub Corp', 'party_type': 'subcontractor'
            })
        assert resp.status_code == 201

    def test_add_party_missing_name(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 5}])
            return chain
        mock_sb.table.side_effect = table_side

        resp = client.post('/api/contracts/5/parties', headers=auth_headers, json={
            'party_type': 'vendor'
        })
        assert resp.status_code == 400


class TestApprovalRoleChecks:
    """Tests for approval RBAC enforcement."""

    def test_request_approval_requires_editor(self, client, mock_sb):
        """Viewers cannot request approvals."""
        from index import make_token
        # Create a viewer token
        viewer_token = make_token("viewer@test.com")
        viewer_headers = {'Authorization': f'Bearer {viewer_token}', 'Content-Type': 'application/json'}

        # Mock user lookup to return viewer role
        user_data = {'id': 1, 'email': 'viewer@test.com', 'role': 'viewer', 'is_active': True}
        chain = mock_chain(make_mock_response([user_data]))
        mock_sb.table.return_value = chain

        resp = client.post('/api/contracts/1/approvals', headers=viewer_headers, json={
            'approver_name': 'Manager'
        })
        assert resp.status_code == 403

    def test_respond_approval_requires_manager(self, client, mock_sb):
        """Editors cannot approve/reject approvals."""
        from index import make_token
        editor_token = make_token("editor@test.com")
        editor_headers = {'Authorization': f'Bearer {editor_token}', 'Content-Type': 'application/json'}

        user_data = {'id': 2, 'email': 'editor@test.com', 'role': 'editor', 'is_active': True}
        chain = mock_chain(make_mock_response([user_data]))
        mock_sb.table.return_value = chain

        resp = client.put('/api/approvals/1', headers=editor_headers, json={
            'action': 'approved'
        })
        assert resp.status_code == 403


class TestSignContractValidation:
    """Tests for sign contract status validation."""

    def test_sign_draft_contract_returns_400(self, client, auth_headers, mock_sb):
        """Cannot sign a draft contract."""
        contract = {'id': 1, 'status': 'draft'}
        chain = mock_chain(make_mock_response([contract]))
        mock_sb.table.return_value = chain

        resp = client.post('/api/contracts/1/sign', headers=auth_headers, json={
            'signer_name': 'John', 'signature_data': 'base64sig'
        })
        assert resp.status_code == 400
        assert 'draft' in resp.get_json()['error']['message'].lower()

    def test_sign_pending_contract_succeeds(self, client, auth_headers, mock_sb):
        """Can sign a pending contract."""
        contract = {'id': 1, 'status': 'pending'}
        sig_row = {'id': 1, 'contract_id': 1, 'signer_name': 'John'}

        call_count = [0]
        def table_side(t):
            chain = mock_chain()
            nonlocal call_count
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([contract])
            elif t == 'contract_signatures':
                chain.execute.return_value = make_mock_response([sig_row])
            return chain
        mock_sb.table.side_effect = table_side

        with patch('index.log_activity'), patch('index.fire_webhooks'):
            resp = client.post('/api/contracts/1/sign', headers=auth_headers, json={
                'signer_name': 'John', 'signature_data': 'base64sig'
            })
        assert resp.status_code == 201


class TestObligationRoleCheck:
    """Tests for obligation RBAC."""

    def test_add_obligation_requires_editor(self, client, mock_sb):
        """Viewers cannot add obligations."""
        from index import make_token
        viewer_token = make_token("viewer@test.com")
        viewer_headers = {'Authorization': f'Bearer {viewer_token}', 'Content-Type': 'application/json'}

        user_data = {'id': 1, 'email': 'viewer@test.com', 'role': 'viewer', 'is_active': True}
        chain = mock_chain(make_mock_response([user_data]))
        mock_sb.table.return_value = chain

        resp = client.post('/api/contracts/1/obligations', headers=viewer_headers, json={
            'title': 'Test Obligation'
        })
        assert resp.status_code == 403
