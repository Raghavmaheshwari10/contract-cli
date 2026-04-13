"""Deep endpoint tests covering ALL API routes with edge cases and validation.
Tests: Dashboard, Templates, Collaborators, Approvals, Signatures, Versions,
Clauses, Users, Reports, Audit, Bulk Import, Tags, Calendar, Workflows,
Custom Fields, Notifications, Email, Links, Parties, Margins, Renewals,
Share Links, Compare, Clone, Counterparty, Bulk Actions, Password Reset, PDF."""
import json
import io
import csv
import pytest
from unittest.mock import patch, MagicMock
from test_helpers import make_mock_response, mock_chain


# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════

class TestDashboard:
    def test_dashboard_returns_stats(self, client, auth_headers, mock_sb):
        contracts = [
            {'id': 1, 'status': 'executed', 'contract_type': 'client', 'end_date': '2027-01-01',
             'value': '100000', 'added_on': '2026-01-15T00:00:00'},
            {'id': 2, 'status': 'draft', 'contract_type': 'vendor', 'end_date': '2026-04-20',
             'value': '50000', 'added_on': '2026-03-10T00:00:00'},
        ]
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response(contracts)
            elif t == 'contract_activity':
                chain.execute.return_value = make_mock_response([])
            elif t == 'contract_obligations':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side

        resp = client.get('/api/dashboard', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert d['total'] == 2
        assert d['executed'] == 1
        assert d['draft'] == 1
        assert 'monthly_trend' in d
        assert 'recent_activity' in d

    def test_dashboard_no_auth(self, client, mock_sb):
        resp = client.get('/api/dashboard')
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════

class TestConfig:
    def test_config_returns_supabase_info(self, client, auth_headers):
        resp = client.get('/api/config', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'supabase_url' in d
        assert 'supabase_anon_key' in d

    def test_config_no_auth(self, client):
        resp = client.get('/api/config')
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════

class TestTemplates:
    def test_list_templates(self, client, auth_headers, mock_sb):
        templates = [{'id': 1, 'name': 'NDA', 'category': 'legal'}]
        chain = mock_chain(make_mock_response(templates))
        mock_sb.table.return_value = chain
        resp = client.get('/api/templates', headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_get_template(self, client, auth_headers, mock_sb):
        tpl = {'id': 1, 'name': 'NDA', 'content': 'NDA content here', 'category': 'legal'}
        chain = mock_chain(make_mock_response([tpl]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/templates/1', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['name'] == 'NDA'

    def test_get_template_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/templates/999', headers=auth_headers)
        assert resp.status_code == 404

    def test_create_template_success(self, client, auth_headers, mock_sb):
        created = {'id': 1, 'name': 'Service Agreement', 'category': 'service'}
        chain = mock_chain(make_mock_response([created]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/templates', headers=auth_headers, json={
            'name': 'Service Agreement', 'category': 'service',
            'contract_type': 'client', 'content': 'This is the template content for service agreements.'
        })
        assert resp.status_code == 201

    def test_create_template_short_name(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.post('/api/templates', headers=auth_headers, json={
            'name': 'AB', 'content': 'Template content that is long enough.'
        })
        assert resp.status_code == 400

    def test_create_template_short_content(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.post('/api/templates', headers=auth_headers, json={
            'name': 'Valid Name', 'content': 'Short'
        })
        assert resp.status_code == 400

    def test_create_template_invalid_type(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.post('/api/templates', headers=auth_headers, json={
            'name': 'Valid Name', 'content': 'Long enough content here.', 'contract_type': 'other'
        })
        assert resp.status_code == 400

    def test_update_template(self, client, auth_headers, mock_sb):
        existing = {'id': 1}
        updated = {'id': 1, 'name': 'Updated NDA'}
        call_count = [0]
        def table_side(t):
            nonlocal call_count
            chain = mock_chain()
            call_count[0] += 1
            if call_count[0] == 1:
                chain.execute.return_value = make_mock_response([existing])
            else:
                chain.execute.return_value = make_mock_response([updated])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.put('/api/templates/1', headers=auth_headers, json={'name': 'Updated NDA'})
        assert resp.status_code == 200

    def test_update_template_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.put('/api/templates/999', headers=auth_headers, json={'name': 'X'})
        assert resp.status_code == 404

    def test_delete_template(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([{'id': 1}]))
        mock_sb.table.return_value = chain
        resp = client.delete('/api/templates/1', headers=auth_headers)
        assert resp.status_code == 200

    def test_delete_template_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.delete('/api/templates/999', headers=auth_headers)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# COLLABORATORS
# ═══════════════════════════════════════════════════════════════════════════

class TestCollaborators:
    def test_list_collaborators(self, client, auth_headers, mock_sb):
        collabs = [{'id': 1, 'user_email': 'a@b.com', 'role': 'viewer'}]
        chain = mock_chain(make_mock_response(collabs))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/1/collaborators', headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_add_collaborator_success(self, client, auth_headers, mock_sb):
        created = {'id': 1, 'user_email': 'collab@test.com', 'role': 'editor'}
        collab_call = [0]
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 1, 'name': 'Test'}])
            elif t == 'clm_users':
                chain.execute.return_value = make_mock_response([{'name': 'Collab User', 'email': 'collab@test.com'}])
            elif t == 'contract_collaborators':
                collab_call[0] += 1
                if collab_call[0] == 1:
                    # First call: check duplicate — return empty
                    chain.execute.return_value = make_mock_response([])
                else:
                    # Second call: insert — return created
                    chain.execute.return_value = make_mock_response([created])
            elif t == 'notifications':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side

        with patch('index.log_activity'), patch('index.create_notification'):
            resp = client.post('/api/contracts/1/collaborators', headers=auth_headers, json={
                'user_email': 'collab@test.com', 'role': 'editor'
            })
        assert resp.status_code == 201

    def test_add_collaborator_invalid_email(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.post('/api/contracts/1/collaborators', headers=auth_headers, json={
            'user_email': 'not-an-email', 'role': 'viewer'
        })
        assert resp.status_code == 400

    def test_add_collaborator_invalid_role(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.post('/api/contracts/1/collaborators', headers=auth_headers, json={
            'user_email': 'valid@email.com', 'role': 'admin'
        })
        assert resp.status_code == 400

    def test_add_collaborator_contract_not_found(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([])
            elif t == 'clm_users':
                chain.execute.return_value = make_mock_response([])
            elif t == 'contract_collaborators':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.post('/api/contracts/999/collaborators', headers=auth_headers, json={
            'user_email': 'valid@email.com', 'role': 'viewer'
        })
        assert resp.status_code == 404

    def test_add_collaborator_duplicate(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 1, 'name': 'Test'}])
            elif t == 'clm_users':
                chain.execute.return_value = make_mock_response([])
            elif t == 'contract_collaborators':
                chain.execute.return_value = make_mock_response([{'id': 1}])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.post('/api/contracts/1/collaborators', headers=auth_headers, json={
            'user_email': 'dup@test.com', 'role': 'viewer'
        })
        assert resp.status_code == 400

    def test_update_collaborator(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.put('/api/contracts/1/collaborators/1', headers=auth_headers, json={'role': 'reviewer'})
        assert resp.status_code == 200

    def test_update_collaborator_invalid_role(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.put('/api/contracts/1/collaborators/1', headers=auth_headers, json={'role': 'superadmin'})
        assert resp.status_code == 400

    def test_remove_collaborator(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([{'id': 1, 'user_name': 'Test'}]))
        mock_sb.table.return_value = chain
        with patch('index.log_activity'):
            resp = client.delete('/api/contracts/1/collaborators/1', headers=auth_headers)
        assert resp.status_code == 200

    def test_remove_collaborator_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.delete('/api/contracts/1/collaborators/999', headers=auth_headers)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# APPROVALS — Full Flow
# ═══════════════════════════════════════════════════════════════════════════

class TestApprovalFullFlow:
    def test_list_approvals(self, client, auth_headers, mock_sb):
        approvals = [{'id': 1, 'approver_name': 'Manager', 'status': 'pending'}]
        chain = mock_chain(make_mock_response(approvals))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/1/approvals', headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_request_approval_success(self, client, auth_headers, mock_sb):
        created = {'id': 1, 'approver_name': 'Boss', 'status': 'pending', 'contract_id': 1}
        approval_call = [0]
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 1, 'status': 'draft'}])
            elif t == 'contract_approvals':
                approval_call[0] += 1
                if approval_call[0] == 1:
                    # First call: check existing pending — return empty
                    chain.execute.return_value = make_mock_response([])
                else:
                    # Second call: insert — return created
                    chain.execute.return_value = make_mock_response([created])
            elif t == 'notifications':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.log_activity'), patch('index.create_notification'):
            resp = client.post('/api/contracts/1/approvals', headers=auth_headers, json={
                'approver_name': 'Boss'
            })
        assert resp.status_code == 201

    def test_request_approval_missing_approver(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.post('/api/contracts/1/approvals', headers=auth_headers, json={})
        assert resp.status_code == 400

    def test_request_approval_contract_not_found(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([])
            elif t == 'contract_approvals':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.post('/api/contracts/999/approvals', headers=auth_headers, json={
            'approver_name': 'Boss'
        })
        assert resp.status_code == 404

    def test_request_approval_invalid_status(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 1, 'status': 'executed'}])
            elif t == 'contract_approvals':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.post('/api/contracts/1/approvals', headers=auth_headers, json={
            'approver_name': 'Boss'
        })
        assert resp.status_code == 400

    def test_request_approval_duplicate(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 1, 'status': 'draft'}])
            elif t == 'contract_approvals':
                chain.execute.return_value = make_mock_response([{'id': 99}])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.post('/api/contracts/1/approvals', headers=auth_headers, json={
            'approver_name': 'Boss'
        })
        assert resp.status_code == 409

    def test_respond_approval_approve(self, client, auth_headers, mock_sb):
        approval = {'id': 1, 'contract_id': 5, 'approver_name': 'Boss', 'status': 'pending'}
        def table_side(t):
            chain = mock_chain()
            if t == 'contract_approvals':
                chain.execute.return_value = make_mock_response([approval])
            elif t == 'contracts':
                chain.execute.return_value = make_mock_response([{'status': 'pending'}])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.log_activity'), patch('index.fire_webhooks'), patch('index._transition_status'):
            resp = client.put('/api/approvals/1', headers=auth_headers, json={'action': 'approved'})
        assert resp.status_code == 200

    def test_respond_approval_reject(self, client, auth_headers, mock_sb):
        approval = {'id': 1, 'contract_id': 5, 'approver_name': 'Boss', 'status': 'pending'}
        def table_side(t):
            chain = mock_chain()
            if t == 'contract_approvals':
                chain.execute.return_value = make_mock_response([approval])
            elif t == 'contracts':
                chain.execute.return_value = make_mock_response([{'status': 'pending'}])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.log_activity'), patch('index.fire_webhooks'), patch('index._transition_status'):
            resp = client.put('/api/approvals/1', headers=auth_headers, json={'action': 'rejected'})
        assert resp.status_code == 200

    def test_respond_approval_invalid_action(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.put('/api/approvals/1', headers=auth_headers, json={'action': 'maybe'})
        assert resp.status_code == 400

    def test_respond_approval_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.put('/api/approvals/999', headers=auth_headers, json={'action': 'approved'})
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# SIGNATURES
# ═══════════════════════════════════════════════════════════════════════════

class TestSignatures:
    def test_list_signatures(self, client, auth_headers, mock_sb):
        sigs = [{'id': 1, 'signer_name': 'CEO', 'signed_at': '2026-01-01'}]
        chain = mock_chain(make_mock_response(sigs))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/1/signatures', headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_sign_missing_fields(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([{'id': 1, 'status': 'pending'}]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/contracts/1/sign', headers=auth_headers, json={
            'signer_name': 'John'
            # missing signature_data
        })
        assert resp.status_code == 400

    def test_sign_contract_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/contracts/999/sign', headers=auth_headers, json={
            'signer_name': 'John', 'signature_data': 'base64sig'
        })
        assert resp.status_code == 404

    def test_sign_executed_contract_succeeds(self, client, auth_headers, mock_sb):
        """Executed contracts can still be signed (e.g. additional signers)."""
        sig_row = {'id': 1, 'signer_name': 'John', 'contract_id': 1}
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 1, 'status': 'executed'}])
            elif t == 'contract_signatures':
                chain.execute.return_value = make_mock_response([sig_row])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.log_activity'), patch('index.fire_webhooks'):
            resp = client.post('/api/contracts/1/sign', headers=auth_headers, json={
                'signer_name': 'John', 'signature_data': 'base64sig'
            })
        assert resp.status_code == 201


# ═══════════════════════════════════════════════════════════════════════════
# VERSIONS / REDLINE / DIFF
# ═══════════════════════════════════════════════════════════════════════════

class TestVersions:
    def test_list_versions(self, client, auth_headers, mock_sb):
        versions = [{'id': 1, 'version_number': 1, 'change_summary': 'Initial'}]
        chain = mock_chain(make_mock_response(versions))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/1/versions', headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_get_version(self, client, auth_headers, mock_sb):
        ver = {'id': 1, 'contract_id': 1, 'version_number': 1, 'content': 'Old content'}
        chain = mock_chain(make_mock_response([ver]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/1/versions/1', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['version_number'] == 1

    def test_get_version_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/1/versions/999', headers=auth_headers)
        assert resp.status_code == 404

    def test_restore_version(self, client, auth_headers, mock_sb):
        ver = {'id': 1, 'contract_id': 1, 'version_number': 2, 'content': 'Old content', 'content_html': ''}
        cur = {'content': 'Current content', 'content_html': ''}
        max_v = {'version_number': 3}
        call_count = [0]
        def table_side(t):
            chain = mock_chain()
            nonlocal call_count
            call_count[0] += 1
            if t == 'contract_versions':
                if call_count[0] == 1:
                    chain.execute.return_value = make_mock_response([ver])
                else:
                    chain.execute.return_value = make_mock_response([max_v])
            elif t == 'contracts':
                chain.execute.return_value = make_mock_response([cur])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.log_activity'):
            resp = client.post('/api/contracts/1/versions/1/restore', headers=auth_headers)
        assert resp.status_code == 200
        assert 'restored' in resp.get_json()['message'].lower()

    def test_restore_version_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/contracts/1/versions/999/restore', headers=auth_headers)
        assert resp.status_code == 404

    def test_redline_no_previous_version(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 1, 'name': 'Test', 'content': 'Hello'}])
            elif t == 'contract_versions':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/contracts/1/redline', headers=auth_headers)
        assert resp.status_code == 404

    def test_redline_with_version(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 1, 'name': 'Test', 'content': 'Hello world updated'}])
            elif t == 'contract_versions':
                chain.execute.return_value = make_mock_response([{'id': 1, 'version_number': 1, 'content': 'Hello world'}])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/contracts/1/redline', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'redline_html' in d
        assert 'stats' in d
        assert d['stats']['total_changes'] >= 0

    def test_redline_contract_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/999/redline', headers=auth_headers)
        assert resp.status_code == 404

    def test_diff_missing_params(self, client, auth_headers, mock_sb):
        resp = client.get('/api/contracts/1/diff', headers=auth_headers)
        assert resp.status_code == 400

    def test_diff_non_integer_versions(self, client, auth_headers, mock_sb):
        resp = client.get('/api/contracts/1/diff?v1=abc&v2=def', headers=auth_headers)
        assert resp.status_code == 400

    def test_diff_version_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/1/diff?v1=1&v2=2', headers=auth_headers)
        assert resp.status_code == 404

    def test_diff_success(self, client, auth_headers, mock_sb):
        v1 = {'id': 1, 'version_number': 1, 'content': 'Original text', 'contract_id': 1}
        v2 = {'id': 2, 'version_number': 2, 'content': 'Modified text', 'contract_id': 1}
        call_count = [0]
        def table_side(t):
            chain = mock_chain()
            nonlocal call_count
            call_count[0] += 1
            if call_count[0] == 1:
                chain.execute.return_value = make_mock_response([v1])
            else:
                chain.execute.return_value = make_mock_response([v2])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/contracts/1/diff?v1=1&v2=2', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'redline_html' in d
        assert 'additions' in d
        assert 'deletions' in d


# ═══════════════════════════════════════════════════════════════════════════
# CLAUSE LIBRARY
# ═══════════════════════════════════════════════════════════════════════════

class TestClauseLibrary:
    def test_list_clauses(self, client, auth_headers, mock_sb):
        clauses = [{'id': 1, 'title': 'NDA Clause', 'category': 'confidentiality'}]
        chain = mock_chain(make_mock_response(clauses))
        mock_sb.table.return_value = chain
        resp = client.get('/api/clauses', headers=auth_headers)
        assert resp.status_code == 200

    def test_create_clause_success(self, client, auth_headers, mock_sb):
        created = {'id': 1, 'title': 'IP Clause', 'category': 'ip', 'content': 'All IP belongs to...'}
        chain = mock_chain(make_mock_response([created]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/clauses', headers=auth_headers, json={
            'title': 'IP Clause', 'category': 'ip', 'content': 'All IP belongs to the company.'
        })
        assert resp.status_code == 201

    def test_create_clause_missing_fields(self, client, auth_headers, mock_sb):
        resp = client.post('/api/clauses', headers=auth_headers, json={
            'title': 'IP Clause'
            # missing category and content
        })
        assert resp.status_code == 400

    def test_update_clause(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.put('/api/clauses/1', headers=auth_headers, json={'title': 'Updated Title'})
        assert resp.status_code == 200

    def test_update_clause_nothing(self, client, auth_headers, mock_sb):
        resp = client.put('/api/clauses/1', headers=auth_headers, json={})
        assert resp.status_code == 400

    def test_delete_clause(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.delete('/api/clauses/1', headers=auth_headers)
        assert resp.status_code == 200

    def test_use_clause(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([{'usage_count': 5}]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/clauses/1/use', headers=auth_headers)
        assert resp.status_code == 200

    def test_use_clause_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/clauses/999/use', headers=auth_headers)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# USER MANAGEMENT — Full CRUD
# ═══════════════════════════════════════════════════════════════════════════

class TestUserManagementFull:
    def test_list_users(self, client, auth_headers, mock_sb):
        users = [{'id': 1, 'email': 'admin@test.com', 'name': 'Admin', 'role': 'admin'}]
        chain = mock_chain(make_mock_response(users))
        mock_sb.table.return_value = chain
        resp = client.get('/api/users', headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_update_user(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.put('/api/users/1', headers=auth_headers, json={
            'name': 'Updated Name', 'role': 'editor'
        })
        assert resp.status_code == 200

    def test_update_user_nothing(self, client, auth_headers, mock_sb):
        resp = client.put('/api/users/1', headers=auth_headers, json={})
        assert resp.status_code == 400

    def test_update_user_with_password(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.put('/api/users/1', headers=auth_headers, json={'password': 'newpass123'})
        assert resp.status_code == 200

    def test_delete_user(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.delete('/api/users/1', headers=auth_headers)
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# REPORTS — All types
# ═══════════════════════════════════════════════════════════════════════════

class TestReports:
    def _contracts(self):
        return [
            {'id': 1, 'name': 'C1', 'party_name': 'Acme', 'contract_type': 'client',
             'status': 'executed', 'start_date': '2025-01-01', 'end_date': '2026-06-01',
             'value': 'INR 10,00,000', 'added_on': '2025-01-01', 'department': 'Finance'},
            {'id': 2, 'name': 'C2', 'party_name': 'Beta', 'contract_type': 'vendor',
             'status': 'draft', 'start_date': '2025-06-01', 'end_date': '2025-12-01',
             'value': 'INR 5,00,000', 'added_on': '2025-06-01', 'department': 'Engineering'},
        ]

    def test_report_summary(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response(self._contracts()))
        mock_sb.table.return_value = chain
        resp = client.get('/api/reports?type=summary', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert d['total_contracts'] == 2
        assert 'monthly_trend' in d

    def test_report_expiry(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response(self._contracts()))
        mock_sb.table.return_value = chain
        resp = client.get('/api/reports?type=expiry', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'expired' in d
        assert 'contracts' in d

    def test_report_department(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response(self._contracts()))
        mock_sb.table.return_value = chain
        resp = client.get('/api/reports?type=department', headers=auth_headers)
        assert resp.status_code == 200
        assert 'departments' in resp.get_json()

    def test_report_health(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response(self._contracts())
            elif t == 'contract_obligations':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/reports?type=health', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'summary' in d
        assert 'contracts' in d
        assert d['summary']['total'] == 2

    def test_report_at_risk(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response(self._contracts())
            elif t == 'contract_obligations':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/reports?type=at_risk', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'total_at_risk' in d

    def test_report_dept_spend(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response(self._contracts())
            elif t == 'contract_links':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/reports?type=dept_spend', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'departments' in d
        assert 'summary' in d

    def test_report_unknown_type(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/reports?type=unknown_type', headers=auth_headers)
        assert resp.status_code == 400

    def test_report_with_date_filter(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response(self._contracts()))
        mock_sb.table.return_value = chain
        resp = client.get('/api/reports?type=summary&from=2025-01-01&to=2025-12-31', headers=auth_headers)
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT LOG
# ═══════════════════════════════════════════════════════════════════════════

class TestAuditLog:
    def test_audit_log_json(self, client, auth_headers, mock_sb):
        activity = [{'id': 1, 'contract_id': 5, 'action': 'created', 'user_name': 'Admin', 'created_at': '2026-01-01'}]
        def table_side(t):
            chain = mock_chain()
            if t == 'contract_activity':
                chain.execute.return_value = make_mock_response(activity)
            elif t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 5, 'name': 'Test Contract'}])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/audit-log', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['contract_name'] == 'Test Contract'

    def test_audit_log_csv(self, client, auth_headers, mock_sb):
        activity = [{'id': 1, 'contract_id': 5, 'action': 'created', 'user_name': 'Admin',
                      'details': 'Created', 'created_at': '2026-01-01'}]
        def table_side(t):
            chain = mock_chain()
            if t == 'contract_activity':
                chain.execute.return_value = make_mock_response(activity)
            elif t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 5, 'name': 'Test'}])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/audit-log?format=csv', headers=auth_headers)
        assert resp.status_code == 200
        assert 'text/csv' in resp.content_type


# ═══════════════════════════════════════════════════════════════════════════
# TAGS & TAG PRESETS
# ═══════════════════════════════════════════════════════════════════════════

class TestTags:
    def test_get_tags(self, client, auth_headers, mock_sb):
        tags = [{'id': 1, 'tag_name': 'urgent', 'tag_color': '#ff0000'}]
        chain = mock_chain(make_mock_response(tags))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/1/tags', headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_add_tag(self, client, auth_headers, mock_sb):
        tag_call = [0]
        def table_side(t):
            chain = mock_chain()
            if t == 'contract_tags':
                tag_call[0] += 1
                if tag_call[0] == 1:
                    # First call: check existing — return empty
                    chain.execute.return_value = make_mock_response([])
                else:
                    # Second call: insert — return created
                    chain.execute.return_value = make_mock_response([{'id': 1, 'tag_name': 'important'}])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.log_activity'):
            resp = client.post('/api/contracts/1/tags', headers=auth_headers, json={
                'tag_name': 'important', 'tag_color': '#0000ff'
            })
        assert resp.status_code == 201

    def test_add_tag_missing_name(self, client, auth_headers, mock_sb):
        resp = client.post('/api/contracts/1/tags', headers=auth_headers, json={})
        assert resp.status_code == 400

    def test_add_tag_duplicate(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([{'id': 1}]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/contracts/1/tags', headers=auth_headers, json={
            'tag_name': 'existing'
        })
        assert resp.status_code == 400

    def test_remove_tag(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([{'tag_name': 'removed'}]))
        mock_sb.table.return_value = chain
        with patch('index.log_activity'):
            resp = client.delete('/api/contracts/1/tags/1', headers=auth_headers)
        assert resp.status_code == 200


class TestTagPresets:
    def test_list_tag_presets(self, client, auth_headers, mock_sb):
        presets = [{'id': 1, 'name': 'Critical', 'color': '#ff0000'}]
        chain = mock_chain(make_mock_response(presets))
        mock_sb.table.return_value = chain
        resp = client.get('/api/tag-presets', headers=auth_headers)
        assert resp.status_code == 200

    def test_create_tag_preset(self, client, auth_headers, mock_sb):
        created = {'id': 1, 'name': 'Important', 'color': '#0000ff'}
        chain = mock_chain(make_mock_response([created]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/tag-presets', headers=auth_headers, json={
            'name': 'Important', 'color': '#0000ff'
        })
        assert resp.status_code == 201

    def test_create_tag_preset_missing_name(self, client, auth_headers, mock_sb):
        resp = client.post('/api/tag-presets', headers=auth_headers, json={})
        assert resp.status_code == 400

    def test_delete_tag_preset(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.delete('/api/tag-presets/1', headers=auth_headers)
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# CALENDAR
# ═══════════════════════════════════════════════════════════════════════════

class TestCalendar:
    def test_calendar_events(self, client, auth_headers, mock_sb):
        contracts = [
            {'id': 1, 'name': 'Test', 'party_name': 'Acme', 'contract_type': 'client',
             'status': 'executed', 'start_date': '2026-04-01', 'end_date': '2026-12-31',
             'value': '100000', 'department': 'Sales'}
        ]
        obligations = [
            {'id': 1, 'contract_id': 1, 'title': 'Payment Due', 'deadline': '2026-04-15',
             'assigned_to': 'John', 'status': 'pending'}
        ]
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response(contracts)
            elif t == 'contract_obligations':
                chain.execute.return_value = make_mock_response(obligations)
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/calendar?year=2026', headers=auth_headers)
        assert resp.status_code == 200
        events = resp.get_json()
        assert len(events) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# WORKFLOWS
# ═══════════════════════════════════════════════════════════════════════════

class TestWorkflows:
    def test_list_workflows(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([{'id': 1, 'name': 'Auto-approve'}]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/workflows', headers=auth_headers)
        assert resp.status_code == 200

    def test_create_workflow(self, client, auth_headers, mock_sb):
        created = {'id': 1, 'name': 'Auto Tag', 'trigger_event': 'contract_created', 'action_type': 'add_tag'}
        chain = mock_chain(make_mock_response([created]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/workflows', headers=auth_headers, json={
            'name': 'Auto Tag', 'trigger_event': 'contract_created', 'action_type': 'add_tag'
        })
        assert resp.status_code == 201

    def test_create_workflow_missing_name(self, client, auth_headers, mock_sb):
        resp = client.post('/api/workflows', headers=auth_headers, json={
            'trigger_event': 'contract_created', 'action_type': 'add_tag'
        })
        assert resp.status_code == 400

    def test_create_workflow_invalid_trigger(self, client, auth_headers, mock_sb):
        resp = client.post('/api/workflows', headers=auth_headers, json={
            'name': 'Test', 'trigger_event': 'invalid_event', 'action_type': 'add_tag'
        })
        assert resp.status_code == 400

    def test_create_workflow_invalid_action(self, client, auth_headers, mock_sb):
        resp = client.post('/api/workflows', headers=auth_headers, json={
            'name': 'Test', 'trigger_event': 'contract_created', 'action_type': 'invalid_action'
        })
        assert resp.status_code == 400

    def test_update_workflow(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.put('/api/workflows/1', headers=auth_headers, json={'name': 'Updated'})
        assert resp.status_code == 200

    def test_update_workflow_nothing(self, client, auth_headers, mock_sb):
        resp = client.put('/api/workflows/1', headers=auth_headers, json={})
        assert resp.status_code == 400

    def test_delete_workflow(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.delete('/api/workflows/1', headers=auth_headers)
        assert resp.status_code == 200

    def test_workflow_log(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/workflow-log', headers=auth_headers)
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# CUSTOM FIELDS
# ═══════════════════════════════════════════════════════════════════════════

class TestCustomFields:
    def test_list_custom_fields(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([{'id': 1, 'field_name': 'Priority', 'field_type': 'select'}]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/custom-fields', headers=auth_headers)
        assert resp.status_code == 200

    def test_create_custom_field(self, client, auth_headers, mock_sb):
        created = {'id': 1, 'field_name': 'Risk Level', 'field_type': 'select'}
        chain = mock_chain(make_mock_response([created]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/custom-fields', headers=auth_headers, json={
            'field_name': 'Risk Level', 'field_type': 'select'
        })
        assert resp.status_code == 201

    def test_create_custom_field_missing_name(self, client, auth_headers, mock_sb):
        resp = client.post('/api/custom-fields', headers=auth_headers, json={'field_type': 'text'})
        assert resp.status_code == 400

    def test_create_custom_field_invalid_type(self, client, auth_headers, mock_sb):
        resp = client.post('/api/custom-fields', headers=auth_headers, json={
            'field_name': 'Test', 'field_type': 'invalid'
        })
        assert resp.status_code == 400

    def test_delete_custom_field(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.delete('/api/custom-fields/1', headers=auth_headers)
        assert resp.status_code == 200

    def test_get_contract_custom_fields(self, client, auth_headers, mock_sb):
        defs = [{'id': 1, 'field_name': 'Priority', 'field_type': 'text'}]
        vals = [{'id': 10, 'field_id': 1, 'field_value': 'High', 'contract_id': 5}]
        def table_side(t):
            chain = mock_chain()
            if t == 'custom_field_defs':
                chain.execute.return_value = make_mock_response(defs)
            elif t == 'custom_field_values':
                chain.execute.return_value = make_mock_response(vals)
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/contracts/5/custom-fields', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['value'] == 'High'

    def test_save_contract_custom_fields(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            if t == 'custom_field_values':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.log_activity'):
            resp = client.post('/api/contracts/5/custom-fields', headers=auth_headers, json={
                'fields': [{'field_id': 1, 'value': 'Updated'}]
            })
        assert resp.status_code == 200
        assert resp.get_json()['saved'] == 1


# ═══════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════

class TestNotifications:
    def test_list_notifications(self, client, auth_headers, mock_sb):
        notifs = [{'id': 1, 'title': 'Test', 'is_read': False, 'user_email': '', 'created_at': '2026-01-01'}]
        chain = mock_chain(make_mock_response(notifs))
        mock_sb.table.return_value = chain
        resp = client.get('/api/notifications', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'notifications' in d
        assert 'unread' in d

    def test_mark_read_by_ids(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.post('/api/notifications/read', headers=auth_headers, json={'ids': [1, 2, 3]})
        assert resp.status_code == 200

    def test_mark_read_all(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.post('/api/notifications/read', headers=auth_headers, json={})
        assert resp.status_code == 200

    def test_clear_notifications(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.post('/api/notifications/clear', headers=auth_headers)
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL PREFERENCES
# ═══════════════════════════════════════════════════════════════════════════

class TestEmailPreferences:
    def test_get_email_prefs(self, client, auth_headers, mock_sb):
        prefs = {'user_email': 'test@test.com', 'enabled': True, 'on_status_change': True}
        chain = mock_chain(make_mock_response([prefs]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/email-preferences', headers=auth_headers)
        assert resp.status_code == 200

    def test_save_email_prefs(self, client, mock_sb):
        """Save email prefs requires email-based auth token."""
        from index import mk_token
        token = mk_token("user@test.com")
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        # Mock user lookup for auth decorator
        user_data = {'id': 1, 'email': 'user@test.com', 'role': 'admin', 'is_active': True}
        def table_side(t):
            chain = mock_chain()
            if t == 'clm_users':
                chain.execute.return_value = make_mock_response([user_data])
            elif t == 'email_preferences':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.post('/api/email-preferences', headers=headers, json={
            'enabled': True, 'on_status_change': True, 'on_approval': False
        })
        assert resp.status_code == 200

    def test_email_status(self, client, auth_headers):
        resp = client.get('/api/email-status', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'configured' in d

    def test_leegality_status(self, client, auth_headers):
        resp = client.get('/api/leegality/status', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'configured' in d


# ═══════════════════════════════════════════════════════════════════════════
# CONTRACT LINKS
# ═══════════════════════════════════════════════════════════════════════════

class TestContractLinks:
    def test_get_links(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 1, 'contract_type': 'client'}])
            elif t == 'contract_links':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/contracts/1/links', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['links'] == []

    def test_get_links_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/999/links', headers=auth_headers)
        assert resp.status_code == 404

    def test_add_link_success(self, client, auth_headers, mock_sb):
        contracts = [
            {'id': 1, 'contract_type': 'client', 'name': 'Client Co'},
            {'id': 2, 'contract_type': 'vendor', 'name': 'Vendor Co'}
        ]
        link = {'id': 1, 'client_contract_id': 1, 'vendor_contract_id': 2}
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response(contracts)
            elif t == 'contract_links':
                chain.execute.return_value = make_mock_response([link])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.log_activity'):
            resp = client.post('/api/contracts/1/links', headers=auth_headers, json={
                'linked_contract_id': 2
            })
        assert resp.status_code == 201

    def test_add_link_missing_target(self, client, auth_headers, mock_sb):
        resp = client.post('/api/contracts/1/links', headers=auth_headers, json={})
        assert resp.status_code == 400

    def test_add_link_same_type(self, client, auth_headers, mock_sb):
        contracts = [
            {'id': 1, 'contract_type': 'client', 'name': 'A'},
            {'id': 2, 'contract_type': 'client', 'name': 'B'}
        ]
        chain = mock_chain(make_mock_response(contracts))
        mock_sb.table.return_value = chain
        resp = client.post('/api/contracts/1/links', headers=auth_headers, json={
            'linked_contract_id': 2
        })
        assert resp.status_code == 400

    def test_delete_link(self, client, auth_headers, mock_sb):
        link = {'id': 1, 'client_contract_id': 5, 'vendor_contract_id': 10}
        chain = mock_chain(make_mock_response([link]))
        mock_sb.table.return_value = chain
        with patch('index.log_activity'):
            resp = client.delete('/api/contract-links/1', headers=auth_headers)
        assert resp.status_code == 200

    def test_delete_link_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.delete('/api/contract-links/999', headers=auth_headers)
        assert resp.status_code == 404

    def test_list_all_links(self, client, auth_headers, mock_sb):
        links = [{'id': 1, 'client_contract_id': 1, 'vendor_contract_id': 2, 'notes': '', 'created_at': '2026-01-01'}]
        contracts = [{'id': 1, 'name': 'Client', 'party_name': 'A'}, {'id': 2, 'name': 'Vendor', 'party_name': 'B'}]
        def table_side(t):
            chain = mock_chain()
            if t == 'contract_links':
                chain.execute.return_value = make_mock_response(links)
            elif t == 'contracts':
                chain.execute.return_value = make_mock_response(contracts)
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/contract-links', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['client_name'] == 'Client'

    def test_linkable_contracts(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 1, 'contract_type': 'client'}])
            elif t == 'contract_links':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/contracts/linkable?contract_id=1', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['contract_type'] == 'vendor'

    def test_linkable_missing_param(self, client, auth_headers, mock_sb):
        resp = client.get('/api/contracts/linkable', headers=auth_headers)
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# CONTRACT PARTIES — Full CRUD
# ═══════════════════════════════════════════════════════════════════════════

class TestContractPartiesFull:
    def test_add_party_invalid_type(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 1}])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.post('/api/contracts/1/parties', headers=auth_headers, json={
            'party_name': 'Test', 'party_type': 'invalid'
        })
        assert resp.status_code == 400

    def test_update_party(self, client, auth_headers, mock_sb):
        party = {'id': 1, 'contract_id': 5, 'party_name': 'Old Name'}
        chain = mock_chain(make_mock_response([party]))
        mock_sb.table.return_value = chain
        with patch('index.log_activity'):
            resp = client.put('/api/contract-parties/1', headers=auth_headers, json={
                'party_name': 'New Name'
            })
        assert resp.status_code == 200

    def test_update_party_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.put('/api/contract-parties/999', headers=auth_headers, json={'party_name': 'X'})
        assert resp.status_code == 404

    def test_update_party_nothing(self, client, auth_headers, mock_sb):
        party = {'id': 1, 'contract_id': 5, 'party_name': 'Test'}
        chain = mock_chain(make_mock_response([party]))
        mock_sb.table.return_value = chain
        resp = client.put('/api/contract-parties/1', headers=auth_headers, json={})
        assert resp.status_code == 400

    def test_update_party_invalid_type(self, client, auth_headers, mock_sb):
        party = {'id': 1, 'contract_id': 5, 'party_name': 'Test'}
        chain = mock_chain(make_mock_response([party]))
        mock_sb.table.return_value = chain
        resp = client.put('/api/contract-parties/1', headers=auth_headers, json={'party_type': 'invalid'})
        assert resp.status_code == 400

    def test_delete_party(self, client, auth_headers, mock_sb):
        party = {'id': 1, 'contract_id': 5, 'party_name': 'Removed Corp'}
        chain = mock_chain(make_mock_response([party]))
        mock_sb.table.return_value = chain
        with patch('index.log_activity'):
            resp = client.delete('/api/contract-parties/1', headers=auth_headers)
        assert resp.status_code == 200

    def test_delete_party_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.delete('/api/contract-parties/999', headers=auth_headers)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# MARGINS
# ═══════════════════════════════════════════════════════════════════════════

class TestMargins:
    def test_get_contract_margin(self, client, auth_headers, mock_sb):
        contract = {'id': 1, 'name': 'Client Deal', 'party_name': 'Acme', 'contract_type': 'client', 'value': 'INR 10,00,000'}
        vendor = {'id': 2, 'name': 'Vendor', 'party_name': 'Beta', 'value': 'INR 3,00,000', 'status': 'executed'}
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([contract])
            elif t == 'contract_links':
                chain.execute.return_value = make_mock_response([{'vendor_contract_id': 2}])
            elif t == 'contract_parties':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/contracts/1/margin', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'margin' in d
        assert 'margin_pct' in d

    def test_get_margin_vendor_contract_rejected(self, client, auth_headers, mock_sb):
        contract = {'id': 1, 'name': 'Test', 'party_name': 'A', 'contract_type': 'vendor', 'value': '100'}
        chain = mock_chain(make_mock_response([contract]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/1/margin', headers=auth_headers)
        assert resp.status_code == 400

    def test_get_margin_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/999/margin', headers=auth_headers)
        assert resp.status_code == 404

    def test_get_all_margins(self, client, auth_headers, mock_sb):
        clients = [{'id': 1, 'name': 'C1', 'party_name': 'A', 'value': '1000', 'status': 'executed', 'department': 'Sales'}]
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response(clients)
            elif t == 'contract_links':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/margins', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'contracts' in d
        assert 'summary' in d


# ═══════════════════════════════════════════════════════════════════════════
# AUTO-RENEW
# ═══════════════════════════════════════════════════════════════════════════

class TestAutoRenew:
    def test_auto_renew_success(self, client, auth_headers, mock_sb):
        orig = {'id': 1, 'name': 'Original', 'party_name': 'Acme', 'contract_type': 'client',
                'content': 'Contract text', 'content_html': '', 'start_date': '2025-01-01',
                'end_date': '2025-12-31', 'value': '100000', 'department': 'Sales',
                'jurisdiction': 'Mumbai', 'governing_law': 'Indian Law', 'notes': 'notes'}
        new = {'id': 2, 'name': 'Original — Renewal'}
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([orig])
            elif t == 'contract_obligations':
                chain.execute.return_value = make_mock_response([])
            elif t == 'notifications':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        # Override insert to return new id
        def insert_side(row):
            ichain = mock_chain(make_mock_response([new]))
            return ichain
        mock_sb.table.return_value.insert = insert_side
        # Re-setup with proper side_effect
        call_count = [0]
        def table_side2(t):
            chain = mock_chain()
            nonlocal call_count
            call_count[0] += 1
            if t == 'contracts' and call_count[0] <= 2:
                chain.execute.return_value = make_mock_response([orig])
                chain.insert.return_value = mock_chain(make_mock_response([new]))
            elif t == 'contracts':
                chain.execute.return_value = make_mock_response([])
                chain.insert.return_value = mock_chain(make_mock_response([new]))
            elif t == 'contract_obligations':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side2
        with patch('index.log_activity'), patch('index.create_notification'):
            resp = client.post('/api/contracts/1/auto-renew', headers=auth_headers)
        assert resp.status_code in (201, 409)  # 201 if new, 409 if renewal exists

    def test_auto_renew_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/contracts/999/auto-renew', headers=auth_headers)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# INVOICES — Update
# ═══════════════════════════════════════════════════════════════════════════

class TestInvoiceUpdate:
    def test_update_invoice(self, client, auth_headers, mock_sb):
        inv = {'id': 1, 'invoice_number': 'INV-001', 'amount': '50000'}
        chain = mock_chain(make_mock_response([inv]))
        mock_sb.table.return_value = chain
        resp = client.put('/api/contract-invoices/1', headers=auth_headers, json={
            'status': 'paid', 'amount': '55000'
        })
        assert resp.status_code == 200

    def test_update_invoice_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.put('/api/contract-invoices/999', headers=auth_headers, json={'status': 'paid'})
        assert resp.status_code == 404

    def test_update_invoice_nothing(self, client, auth_headers, mock_sb):
        inv = {'id': 1, 'invoice_number': 'INV-001'}
        chain = mock_chain(make_mock_response([inv]))
        mock_sb.table.return_value = chain
        resp = client.put('/api/contract-invoices/1', headers=auth_headers, json={})
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# SHARE LINKS — Revoke & Get
# ═══════════════════════════════════════════════════════════════════════════

class TestShareLinksFull:
    def test_get_share_links(self, client, auth_headers, mock_sb):
        links = [{'id': 1, 'token': 'abc', 'permissions': 'view'}]
        chain = mock_chain(make_mock_response(links))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/1/share-links', headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_revoke_share_link(self, client, auth_headers, mock_sb):
        link = {'id': 1, 'contract_id': 5, 'is_active': True}
        chain = mock_chain(make_mock_response([link]))
        mock_sb.table.return_value = chain
        with patch('index.log_activity'):
            resp = client.post('/api/share-links/1/revoke', headers=auth_headers)
        assert resp.status_code == 200

    def test_revoke_share_link_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/share-links/999/revoke', headers=auth_headers)
        assert resp.status_code == 404

    def test_shared_comment_view_only(self, client, mock_sb):
        link = {'id': 1, 'contract_id': 5, 'permissions': 'view',
                'expires_at': '2027-01-01T00:00:00', 'is_active': True, 'recipient_name': 'External'}
        chain = mock_chain(make_mock_response([link]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/shared/validtoken/comments', json={'text': 'A comment'},
                           content_type='application/json')
        assert resp.status_code == 403

    def test_shared_comment_success(self, client, mock_sb):
        link = {'id': 1, 'contract_id': 5, 'permissions': 'comment',
                'expires_at': '2027-01-01T00:00:00', 'is_active': True, 'recipient_name': 'External'}
        def table_side(t):
            chain = mock_chain()
            if t == 'contract_share_links':
                chain.execute.return_value = make_mock_response([link])
            elif t == 'contract_comments':
                chain.execute.return_value = make_mock_response([{'id': 1}])
            elif t == 'notifications':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.create_notification'):
            resp = client.post('/api/shared/validtoken/comments', json={'text': 'Nice contract!'},
                               content_type='application/json')
        assert resp.status_code == 201

    def test_shared_comment_empty_text(self, client, mock_sb):
        link = {'id': 1, 'contract_id': 5, 'permissions': 'comment',
                'expires_at': '2027-01-01T00:00:00', 'is_active': True, 'recipient_name': 'External'}
        chain = mock_chain(make_mock_response([link]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/shared/validtoken/comments', json={'text': ''},
                           content_type='application/json')
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# CONTRACT COMPARE
# ═══════════════════════════════════════════════════════════════════════════

class TestContractCompare:
    def test_compare_missing_params(self, client, auth_headers, mock_sb):
        resp = client.get('/api/contracts/compare', headers=auth_headers)
        assert resp.status_code == 400

    def test_compare_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/compare?id1=1&id2=2', headers=auth_headers)
        assert resp.status_code == 404

    def test_compare_success(self, client, auth_headers, mock_sb):
        c1 = {'id': 1, 'name': 'Contract A', 'party_name': 'Acme', 'contract_type': 'client',
              'status': 'executed', 'value': '100000', 'start_date': '2025-01-01',
              'end_date': '2026-01-01', 'department': 'Sales', 'jurisdiction': '', 'governing_law': '',
              'content': 'This is the content of contract A with some text.'}
        c2 = {'id': 2, 'name': 'Contract B', 'party_name': 'Beta', 'contract_type': 'vendor',
              'status': 'draft', 'value': '50000', 'start_date': '2025-06-01',
              'end_date': '2026-06-01', 'department': 'Engineering', 'jurisdiction': '', 'governing_law': '',
              'content': 'This is the content of contract B with different text.'}
        call_count = [0]
        def table_side(t):
            chain = mock_chain()
            nonlocal call_count
            call_count[0] += 1
            if call_count[0] == 1:
                chain.execute.return_value = make_mock_response([c1])
            else:
                chain.execute.return_value = make_mock_response([c2])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/contracts/compare?id1=1&id2=2', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'similarity' in d
        assert 'field_diffs' in d
        assert 'diff_html' in d


# ═══════════════════════════════════════════════════════════════════════════
# CONTRACT CLONE
# ═══════════════════════════════════════════════════════════════════════════

class TestContractClone:
    def test_clone_success(self, client, auth_headers, mock_sb):
        orig = {'id': 1, 'name': 'Original', 'party_name': 'Acme', 'contract_type': 'client',
                'content': 'Text', 'content_html': '', 'value': '100', 'notes': '', 'department': '',
                'jurisdiction': '', 'governing_law': ''}
        cloned = {'id': 2, 'name': 'Copy of Original'}
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([orig])
                chain.insert.return_value = mock_chain(make_mock_response([cloned]))
            elif t == 'contract_tags':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.log_activity'):
            resp = client.post('/api/contracts/1/clone', headers=auth_headers, json={})
        assert resp.status_code == 201
        assert resp.get_json()['id'] == 2

    def test_clone_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/contracts/999/clone', headers=auth_headers)
        assert resp.status_code == 404

    def test_clone_custom_name(self, client, auth_headers, mock_sb):
        orig = {'id': 1, 'name': 'Original', 'party_name': 'Acme', 'contract_type': 'client',
                'content': 'Text', 'content_html': '', 'value': '', 'notes': '', 'department': '',
                'jurisdiction': '', 'governing_law': ''}
        cloned = {'id': 3, 'name': 'My Custom Name'}
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([orig])
                chain.insert.return_value = mock_chain(make_mock_response([cloned]))
            elif t == 'contract_tags':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.log_activity'):
            resp = client.post('/api/contracts/1/clone', headers=auth_headers, json={
                'name': 'My Custom Name'
            })
        assert resp.status_code == 201


# ═══════════════════════════════════════════════════════════════════════════
# COUNTERPARTY VIEW
# ═══════════════════════════════════════════════════════════════════════════

class TestCounterpartyView:
    def test_counterparty_view(self, client, auth_headers, mock_sb):
        contracts = [
            {'id': 1, 'name': 'C1', 'party_name': 'Acme Corp', 'contract_type': 'client', 'status': 'executed'},
            {'id': 2, 'name': 'C2', 'party_name': 'Acme Corp', 'contract_type': 'vendor', 'status': 'draft'},
        ]
        chain = mock_chain(make_mock_response(contracts))
        mock_sb.table.return_value = chain
        resp = client.get('/api/counterparty/Acme%20Corp', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert d['party_name'] == 'Acme Corp'
        assert d['total_contracts'] == 2
        assert 'by_status' in d
        assert 'by_type' in d


# ═══════════════════════════════════════════════════════════════════════════
# BULK ACTIONS
# ═══════════════════════════════════════════════════════════════════════════

class TestBulkActions:
    def test_bulk_missing_params(self, client, auth_headers, mock_sb):
        resp = client.post('/api/contracts/bulk', headers=auth_headers, json={})
        assert resp.status_code == 400

    def test_bulk_too_many(self, client, auth_headers, mock_sb):
        resp = client.post('/api/contracts/bulk', headers=auth_headers, json={
            'ids': list(range(51)), 'action': 'delete'
        })
        assert resp.status_code == 400

    def test_bulk_unknown_action(self, client, auth_headers, mock_sb):
        resp = client.post('/api/contracts/bulk', headers=auth_headers, json={
            'ids': [1, 2], 'action': 'unknown'
        })
        assert resp.status_code == 400

    def test_bulk_delete(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.post('/api/contracts/bulk', headers=auth_headers, json={
            'ids': [1, 2], 'action': 'delete'
        })
        assert resp.status_code == 200
        d = resp.get_json()
        assert d['success'] == 2

    def test_bulk_add_tag(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.post('/api/contracts/bulk', headers=auth_headers, json={
            'ids': [1, 2], 'action': 'add_tag', 'tag_name': 'important'
        })
        assert resp.status_code == 200

    def test_bulk_add_tag_missing_name(self, client, auth_headers, mock_sb):
        resp = client.post('/api/contracts/bulk', headers=auth_headers, json={
            'ids': [1, 2], 'action': 'add_tag'
        })
        assert resp.status_code == 400

    def test_bulk_remove_tag(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.post('/api/contracts/bulk', headers=auth_headers, json={
            'ids': [1], 'action': 'remove_tag', 'tag_name': 'old_tag'
        })
        assert resp.status_code == 200

    def test_bulk_change_status_invalid(self, client, auth_headers, mock_sb):
        resp = client.post('/api/contracts/bulk', headers=auth_headers, json={
            'ids': [1], 'action': 'change_status', 'status': 'invalid_status'
        })
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# PASSWORD RESET
# ═══════════════════════════════════════════════════════════════════════════

class TestPasswordReset:
    def test_reset_missing_fields(self, client, mock_sb):
        resp = client.post('/api/auth/reset-password', json={}, content_type='application/json')
        assert resp.status_code == 400

    def test_reset_short_password(self, client, mock_sb):
        resp = client.post('/api/auth/reset-password', json={
            'email': 'user@test.com', 'new_password': '123', 'admin_password': 'test-password-123'
        }, content_type='application/json')
        assert resp.status_code == 400

    def test_reset_wrong_admin_password(self, client, mock_sb):
        resp = client.post('/api/auth/reset-password', json={
            'email': 'user@test.com', 'new_password': 'newpassword', 'admin_password': 'wrong'
        }, content_type='application/json')
        assert resp.status_code == 401

    def test_reset_user_not_found(self, client, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/auth/reset-password', json={
            'email': 'noexist@test.com', 'new_password': 'newpassword123', 'admin_password': 'test-password-123'
        }, content_type='application/json')
        assert resp.status_code == 404

    def test_reset_success(self, client, mock_sb):
        user = {'id': 1, 'email': 'user@test.com', 'name': 'Test User'}
        chain = mock_chain(make_mock_response([user]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/auth/reset-password', json={
            'email': 'user@test.com', 'new_password': 'newpassword123', 'admin_password': 'test-password-123'
        }, content_type='application/json')
        assert resp.status_code == 200
        assert 'reset' in resp.get_json()['message'].lower()


# ═══════════════════════════════════════════════════════════════════════════
# RENEWALS
# ═══════════════════════════════════════════════════════════════════════════

class TestRenewals:
    def test_renewals_list(self, client, auth_headers, mock_sb):
        contracts = [
            {'id': 1, 'name': 'Expiring', 'party_name': 'Acme', 'contract_type': 'client',
             'status': 'executed', 'value': '100000', 'end_date': '2026-05-01', 'department': 'Sales'}
        ]
        chain = mock_chain(make_mock_response(contracts))
        mock_sb.table.return_value = chain
        resp = client.get('/api/renewals?days=90', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'total' in d
        assert 'critical' in d
        assert 'warning' in d
        assert 'contracts' in d


# ═══════════════════════════════════════════════════════════════════════════
# PARTIES LIST
# ═══════════════════════════════════════════════════════════════════════════

class TestPartiesList:
    def test_list_parties(self, client, auth_headers, mock_sb):
        data = [{'party_name': 'Acme'}, {'party_name': 'Acme'}, {'party_name': 'Beta'}]
        chain = mock_chain(make_mock_response(data))
        mock_sb.table.return_value = chain
        resp = client.get('/api/parties', headers=auth_headers)
        assert resp.status_code == 200
        parties = resp.get_json()
        assert len(parties) == 2
        acme = next(p for p in parties if p['name'] == 'Acme')
        assert acme['count'] == 2


# ═══════════════════════════════════════════════════════════════════════════
# OBLIGATIONS — Full CRUD
# ═══════════════════════════════════════════════════════════════════════════

class TestObligationsFull:
    def test_list_obligations(self, client, auth_headers, mock_sb):
        obs = [{'id': 1, 'title': 'Payment', 'status': 'pending', 'deadline': '2026-05-01'}]
        chain = mock_chain(make_mock_response(obs))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/1/obligations', headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_add_obligation_success(self, client, auth_headers, mock_sb):
        created = {'id': 1, 'title': 'Deliver Report', 'status': 'pending'}
        chain = mock_chain(make_mock_response([created]))
        mock_sb.table.return_value = chain
        with patch('index.log_activity'):
            resp = client.post('/api/contracts/1/obligations', headers=auth_headers, json={
                'title': 'Deliver Report', 'deadline': '2026-06-01'
            })
        assert resp.status_code == 201

    def test_add_obligation_missing_title(self, client, auth_headers, mock_sb):
        resp = client.post('/api/contracts/1/obligations', headers=auth_headers, json={})
        assert resp.status_code == 400

    def test_update_obligation(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.put('/api/obligations/1', headers=auth_headers, json={
            'status': 'completed', 'title': 'Updated'
        })
        assert resp.status_code == 200

    def test_update_obligation_nothing(self, client, auth_headers, mock_sb):
        resp = client.put('/api/obligations/1', headers=auth_headers, json={})
        assert resp.status_code == 400

    def test_overdue_obligations(self, client, auth_headers, mock_sb):
        obs = [{'id': 1, 'contract_id': 5, 'title': 'Late', 'status': 'pending',
                'deadline': '2025-01-01'}]
        contracts = [{'id': 5, 'name': 'Test', 'party_name': 'Acme', 'department': 'Sales'}]
        def table_side(t):
            chain = mock_chain()
            if t == 'contract_obligations':
                chain.execute.return_value = make_mock_response(obs)
            elif t == 'contracts':
                chain.execute.return_value = make_mock_response(contracts)
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/obligations/overdue', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['contract_name'] == 'Test'
        assert data[0]['days_overdue'] > 0

    def test_escalate_obligations(self, client, auth_headers, mock_sb):
        ob = {'id': 1, 'title': 'Payment', 'contract_id': 5, 'assigned_to': 'John', 'deadline': '2025-01-01'}
        def table_side(t):
            chain = mock_chain()
            if t == 'contract_obligations':
                chain.execute.return_value = make_mock_response([ob])
            elif t == 'contracts':
                chain.execute.return_value = make_mock_response([{'name': 'Test Contract'}])
            elif t == 'notifications':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.log_activity'), patch('index.create_notification'):
            resp = client.post('/api/obligations/escalate', headers=auth_headers, json={
                'obligation_ids': [1], 'escalate_to': 'director@test.com'
            })
        assert resp.status_code == 200
        assert resp.get_json()['escalated'] == 1

    def test_escalate_no_ids(self, client, auth_headers, mock_sb):
        resp = client.post('/api/obligations/escalate', headers=auth_headers, json={})
        assert resp.status_code == 400

    def test_auto_escalate(self, client, auth_headers, mock_sb):
        obs = [{'id': 1, 'title': 'Old', 'contract_id': 5, 'status': 'pending',
                'deadline': '2025-01-01', 'escalated': False}]
        def table_side(t):
            chain = mock_chain()
            if t == 'contract_obligations':
                chain.execute.return_value = make_mock_response(obs)
            elif t == 'contracts':
                chain.execute.return_value = make_mock_response([{'name': 'Test'}])
            elif t == 'notifications':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.create_notification'):
            resp = client.post('/api/obligations/auto-escalate', headers=auth_headers, json={
                'threshold_days': 3
            })
        assert resp.status_code == 200
        d = resp.get_json()
        assert d['escalated'] >= 0
        assert 'total_overdue' in d


# ═══════════════════════════════════════════════════════════════════════════
# WEBHOOKS
# ═══════════════════════════════════════════════════════════════════════════

class TestWebhooks:
    def test_list_webhooks(self, client, auth_headers, mock_sb):
        wh = [{'id': 1, 'url': 'https://hook.test', 'event_type': 'contract.created'}]
        chain = mock_chain(make_mock_response(wh))
        mock_sb.table.return_value = chain
        resp = client.get('/api/webhooks', headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_create_webhook(self, client, auth_headers, mock_sb):
        created = {'id': 1, 'url': 'https://new.hook', 'event_type': 'contract.created'}
        chain = mock_chain(make_mock_response([created]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/webhooks', headers=auth_headers, json={
            'url': 'https://new.hook', 'event_type': 'contract.created'
        })
        assert resp.status_code == 201

    def test_create_webhook_missing_fields(self, client, auth_headers, mock_sb):
        resp = client.post('/api/webhooks', headers=auth_headers, json={'url': 'https://hook.test'})
        assert resp.status_code == 400

    def test_delete_webhook(self, client, auth_headers, mock_sb):
        chain = mock_chain()
        mock_sb.table.return_value = chain
        resp = client.delete('/api/webhooks/1', headers=auth_headers)
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# ACTIVITY TIMELINE
# ═══════════════════════════════════════════════════════════════════════════

class TestActivity:
    def test_get_activity(self, client, auth_headers, mock_sb):
        activity = [{'id': 1, 'action': 'created', 'user_name': 'Admin', 'created_at': '2026-01-01'}]
        chain = mock_chain(make_mock_response(activity))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/1/activity', headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_get_activity_custom_limit(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/1/activity?limit=10', headers=auth_headers)
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# BULK IMPORT
# ═══════════════════════════════════════════════════════════════════════════

class TestBulkImport:
    def test_bulk_import_no_file(self, client, auth_headers, mock_sb):
        resp = client.post('/api/bulk-import', headers={'Authorization': auth_headers['Authorization']})
        assert resp.status_code == 400

    def test_bulk_import_non_csv(self, client, auth_headers, mock_sb):
        data = io.BytesIO(b"not a csv")
        resp = client.post('/api/bulk-import',
                           headers={'Authorization': auth_headers['Authorization']},
                           data={'file': (data, 'test.txt')},
                           content_type='multipart/form-data')
        assert resp.status_code == 400

    def test_bulk_import_success(self, client, auth_headers, mock_sb):
        csv_content = "name,party_name,contract_type,content\nNDA,Acme,client,Full text here\n"
        data = io.BytesIO(csv_content.encode('utf-8'))
        chain = mock_chain(make_mock_response([{'id': 1}]))
        mock_sb.table.return_value = chain
        with patch('index.log_activity'):
            resp = client.post('/api/bulk-import',
                               headers={'Authorization': auth_headers['Authorization']},
                               data={'file': (data, 'contracts.csv')},
                               content_type='multipart/form-data')
        assert resp.status_code == 200
        d = resp.get_json()
        assert d['imported'] == 1

    def test_bulk_import_missing_required(self, client, auth_headers, mock_sb):
        csv_content = "name,party_name\nNDA,Acme\n"
        data = io.BytesIO(csv_content.encode('utf-8'))
        chain = mock_chain()
        mock_sb.table.return_value = chain
        with patch('index.log_activity'):
            resp = client.post('/api/bulk-import',
                               headers={'Authorization': auth_headers['Authorization']},
                               data={'file': (data, 'contracts.csv')},
                               content_type='multipart/form-data')
        assert resp.status_code == 200
        d = resp.get_json()
        assert d['skipped'] == 1
        assert len(d['errors']) > 0

    def test_bulk_import_template(self, client, auth_headers):
        resp = client.get('/api/bulk-import/template', headers=auth_headers)
        assert resp.status_code == 200
        assert 'text/csv' in resp.content_type


# ═══════════════════════════════════════════════════════════════════════════
# PAGINATION & FILTERS
# ═══════════════════════════════════════════════════════════════════════════

class TestPaginationAndFilters:
    def test_contracts_pagination(self, client, auth_headers, mock_sb):
        resp_data = make_mock_response([{'id': 1}])
        resp_data.count = 50
        chain = mock_chain(resp_data)
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts?page=2&per_page=10', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert d['page'] == 2
        assert d['per_page'] == 10

    def test_contracts_status_filter(self, client, auth_headers, mock_sb):
        resp_data = make_mock_response([])
        resp_data.count = 0
        chain = mock_chain(resp_data)
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts?status=draft', headers=auth_headers)
        assert resp.status_code == 200

    def test_contracts_invalid_page(self, client, auth_headers, mock_sb):
        resp_data = make_mock_response([])
        resp_data.count = 0
        chain = mock_chain(resp_data)
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts?page=abc&per_page=xyz', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert d['page'] == 1
        assert d['per_page'] == 20


# ═══════════════════════════════════════════════════════════════════════════
# COMMENTS — List
# ═══════════════════════════════════════════════════════════════════════════

class TestCommentsList:
    def test_list_comments(self, client, auth_headers, mock_sb):
        comments = [{'id': 1, 'user_name': 'Admin', 'content': 'Looks good', 'created_at': '2026-01-01'}]
        chain = mock_chain(make_mock_response(comments))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/1/comments', headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1
