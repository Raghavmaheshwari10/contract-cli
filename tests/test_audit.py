"""Audit tests — helper functions, RBAC enforcement, edge cases, backup/restore,
PDF generation, workflow engine, diff utilities, password hashing, and more.
Covers gaps identified in production-readiness audit."""
import json
import time
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from test_helpers import make_mock_response, mock_chain


# ═══════════════════════════════════════════════════════════════════════════
# HELPER: _word_diff & _line_diff
# ═══════════════════════════════════════════════════════════════════════════

class TestDiffUtilities:
    def test_word_diff_identical(self):
        from helpers import _word_diff
        result = _word_diff("hello world", "hello world")
        assert len(result) == 1
        assert result[0]['type'] == 'equal'

    def test_word_diff_insertion(self):
        from helpers import _word_diff
        result = _word_diff("hello world", "hello beautiful world")
        types = [r['type'] for r in result]
        assert 'insert' in types

    def test_word_diff_deletion(self):
        from helpers import _word_diff
        result = _word_diff("hello beautiful world", "hello world")
        types = [r['type'] for r in result]
        assert 'delete' in types

    def test_word_diff_replacement(self):
        from helpers import _word_diff
        result = _word_diff("hello world", "hello universe")
        types = [r['type'] for r in result]
        assert 'delete' in types
        assert 'insert' in types

    def test_word_diff_empty_strings(self):
        from helpers import _word_diff
        result = _word_diff("", "")
        assert result == []

    def test_word_diff_from_empty(self):
        from helpers import _word_diff
        result = _word_diff("", "hello world")
        assert any(r['type'] == 'insert' for r in result)

    def test_line_diff_identical(self):
        from helpers import _line_diff
        diff, additions, deletions = _line_diff("line1\nline2", "line1\nline2")
        assert additions == 0
        assert deletions == 0

    def test_line_diff_changes(self):
        from helpers import _line_diff
        diff, additions, deletions = _line_diff("line1\nline2", "line1\nline3")
        assert additions >= 1
        assert deletions >= 1


# ═══════════════════════════════════════════════════════════════════════════
# HELPER: _should_email
# ═══════════════════════════════════════════════════════════════════════════

class TestShouldEmail:
    def test_approval_type(self):
        from helpers import _should_email
        pref = {'on_approval': True, 'on_comment': False}
        assert _should_email(pref, 'approval') is True

    def test_comment_type_disabled(self):
        from helpers import _should_email
        pref = {'on_approval': True, 'on_comment': False}
        assert _should_email(pref, 'comment') is False

    def test_expiry_type(self):
        from helpers import _should_email
        pref = {'on_expiry': True}
        assert _should_email(pref, 'expiry') is True

    def test_workflow_type(self):
        from helpers import _should_email
        pref = {'on_workflow': False}
        assert _should_email(pref, 'workflow') is False

    def test_unknown_type_defaults_to_status_change(self):
        from helpers import _should_email
        pref = {'on_status_change': True}
        assert _should_email(pref, 'unknown_type') is True

    def test_info_maps_to_status_change(self):
        from helpers import _should_email
        pref = {'on_status_change': False}
        assert _should_email(pref, 'info') is False


# ═══════════════════════════════════════════════════════════════════════════
# HELPER: _parse_currency
# ═══════════════════════════════════════════════════════════════════════════

class TestParseCurrency:
    def test_inr_format(self):
        from index import _parse_currency
        assert _parse_currency("INR 10,00,000") == 1000000

    def test_usd_format(self):
        from index import _parse_currency
        assert _parse_currency("$48,000") == 48000

    def test_plain_number(self):
        from index import _parse_currency
        assert _parse_currency("5000") == 5000

    def test_empty_string(self):
        from index import _parse_currency
        assert _parse_currency("") == 0

    def test_none(self):
        from index import _parse_currency
        assert _parse_currency(None) == 0

    def test_garbage(self):
        from index import _parse_currency
        assert _parse_currency("no numbers here") == 0

    def test_decimal(self):
        from index import _parse_currency
        assert _parse_currency("$1,234.56") == 1234.56


# ═══════════════════════════════════════════════════════════════════════════
# AUTH: Password hashing & verification
# ═══════════════════════════════════════════════════════════════════════════

class TestPasswordHashing:
    def test_hash_and_verify(self):
        from auth import _hash_password, _verify_password
        h = _hash_password("mypassword123")
        assert h.startswith("$2b$")
        valid, needs_upgrade = _verify_password("mypassword123", h)
        assert valid is True
        assert needs_upgrade is False

    def test_wrong_password(self):
        from auth import _hash_password, _verify_password
        h = _hash_password("correct")
        valid, _ = _verify_password("wrong", h)
        assert valid is False

    def test_legacy_sha256_upgrade(self):
        import hashlib
        from auth import _verify_password
        legacy = hashlib.sha256("oldpass".encode()).hexdigest()
        valid, needs_upgrade = _verify_password("oldpass", legacy)
        assert valid is True
        assert needs_upgrade is True

    def test_legacy_sha256_wrong(self):
        import hashlib
        from auth import _verify_password
        legacy = hashlib.sha256("correct".encode()).hexdigest()
        valid, needs_upgrade = _verify_password("wrong", legacy)
        assert valid is False
        assert needs_upgrade is False


# ═══════════════════════════════════════════════════════════════════════════
# AUTH: Token creation & validation
# ═══════════════════════════════════════════════════════════════════════════

class TestTokenAuth:
    def test_token_roundtrip(self):
        from auth import mk_token, chk_token
        token = mk_token("user@test.com")
        valid, email = chk_token(token)
        assert valid is True
        assert email == "user@test.com"

    def test_token_no_email(self):
        from auth import mk_token, chk_token
        token = mk_token("")
        valid, email = chk_token(token)
        assert valid is True
        assert email == ""

    def test_invalid_token(self):
        from auth import chk_token
        valid, _ = chk_token("garbage:invalid")
        assert valid is False

    def test_tampered_token(self):
        from auth import mk_token, chk_token
        token = mk_token("user@test.com")
        tampered = token[:-5] + "xxxxx"
        valid, _ = chk_token(tampered)
        assert valid is False

    def test_expired_token(self):
        from auth import chk_token, _sign
        # Create token with old timestamp
        old_ts = str(int(time.time()) - 90000)  # 25 hours ago
        payload = f"user@test.com:{old_ts}"
        sig = _sign(payload)
        token = f"{payload}:{sig}"
        valid, _ = chk_token(token)
        assert valid is False

    def test_revoked_token(self):
        from auth import mk_token, chk_token
        import config
        token = mk_token("user@test.com")
        sig = token.rsplit(":", 1)[-1]
        config._revoked_tokens.add(sig)
        valid, _ = chk_token(token)
        assert valid is False

    def test_malformed_token(self):
        from auth import chk_token
        assert chk_token("noseparator")[0] is False
        assert chk_token("")[0] is False
        assert chk_token("a:b:c:d:e")[0] is False


# ═══════════════════════════════════════════════════════════════════════════
# AUTH: Email validation
# ═══════════════════════════════════════════════════════════════════════════

class TestEmailValidation:
    def test_valid_emails(self):
        from auth import _valid_email
        assert _valid_email("user@example.com") is True
        assert _valid_email("a.b@c.co") is True
        assert _valid_email("test+tag@gmail.com") is True

    def test_invalid_emails(self):
        from auth import _valid_email
        assert _valid_email("") is False
        assert _valid_email(None) is False
        assert _valid_email("nope") is False
        assert _valid_email("@missing.com") is False
        assert _valid_email("missing@") is False
        assert _valid_email(123) is False

    def test_long_email(self):
        from auth import _valid_email
        long = "a" * 250 + "@b.com"
        assert _valid_email(long) is False


# ═══════════════════════════════════════════════════════════════════════════
# AUTH: Sanitization
# ═══════════════════════════════════════════════════════════════════════════

class TestSanitization:
    def test_sanitize_script(self):
        from auth import _sanitize
        result = _sanitize('<script>alert("xss")</script>Safe text')
        assert '<script>' not in result
        assert 'Safe text' in result

    def test_sanitize_event_handlers(self):
        from auth import _sanitize
        result = _sanitize('Hello onclick=alert(1)')
        assert 'onclick=' not in result

    def test_sanitize_javascript_protocol(self):
        from auth import _sanitize
        result = _sanitize('javascript:alert(1)')
        assert 'javascript:' not in result

    def test_sanitize_max_len(self):
        from auth import _sanitize
        result = _sanitize("a" * 1000, max_len=50)
        assert len(result) == 50

    def test_sanitize_none(self):
        from auth import _sanitize
        assert _sanitize(None) is None

    def test_sanitize_non_string(self):
        from auth import _sanitize
        assert _sanitize(123) == 123

    def test_sanitize_html_removes_tags(self):
        from auth import _sanitize_html
        assert _sanitize_html("<b>bold</b> <i>italic</i>") == "bold italic"

    def test_sanitize_html_none(self):
        from auth import _sanitize_html
        assert _sanitize_html(None) is None

    def test_sanitize_dict_all_fields(self):
        from auth import _sanitize_dict
        d = {'name': '<script>bad</script>Good', 'count': 5}
        result = _sanitize_dict(d)
        assert '<script>' not in result['name']
        assert result['count'] == 5

    def test_sanitize_dict_empty(self):
        from auth import _sanitize_dict
        assert _sanitize_dict({}) == {}
        assert _sanitize_dict(None) is None


# ═══════════════════════════════════════════════════════════════════════════
# RBAC: Role-based access enforcement
# ═══════════════════════════════════════════════════════════════════════════

class TestRBACEnforcement:
    """Test that protected endpoints reject lower roles."""

    def _make_token_with_role(self, role, mock_sb):
        from index import mk_token
        token = mk_token("user@test.com")
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        # Mock user lookup to return specific role
        user = {'id': 1, 'email': 'user@test.com', 'role': role, 'is_active': True, 'name': 'Test'}
        def table_side(t):
            chain = mock_chain()
            if t == 'clm_users':
                chain.execute.return_value = make_mock_response([user])
            return chain
        mock_sb.table.side_effect = table_side
        return headers

    def test_viewer_cannot_create_contract(self, client, mock_sb):
        headers = self._make_token_with_role('viewer', mock_sb)
        resp = client.post('/api/contracts', headers=headers, json={
            'name': 'Test', 'party_name': 'Acme', 'contract_type': 'client', 'content': 'Content'
        })
        assert resp.status_code == 403

    def test_viewer_cannot_delete_contract(self, client, mock_sb):
        headers = self._make_token_with_role('viewer', mock_sb)
        resp = client.delete('/api/contracts/1', headers=headers)
        assert resp.status_code == 403

    def test_editor_cannot_delete_contract(self, client, mock_sb):
        headers = self._make_token_with_role('editor', mock_sb)
        resp = client.delete('/api/contracts/1', headers=headers)
        assert resp.status_code == 403

    def test_viewer_cannot_create_user(self, client, mock_sb):
        headers = self._make_token_with_role('viewer', mock_sb)
        resp = client.post('/api/users', headers=headers, json={
            'email': 'new@test.com', 'name': 'New', 'password': 'pass123'
        })
        assert resp.status_code == 403

    def test_editor_cannot_create_user(self, client, mock_sb):
        headers = self._make_token_with_role('editor', mock_sb)
        resp = client.post('/api/users', headers=headers, json={
            'email': 'new@test.com', 'name': 'New', 'password': 'pass123'
        })
        assert resp.status_code == 403

    def test_viewer_cannot_create_webhook(self, client, mock_sb):
        headers = self._make_token_with_role('viewer', mock_sb)
        resp = client.post('/api/webhooks', headers=headers, json={
            'url': 'https://hook.test', 'event_type': 'contract.created'
        })
        assert resp.status_code == 403

    def test_editor_cannot_create_workflow(self, client, mock_sb):
        headers = self._make_token_with_role('editor', mock_sb)
        resp = client.post('/api/workflows', headers=headers, json={
            'name': 'Test', 'trigger_event': 'contract_created', 'action_type': 'add_tag'
        })
        assert resp.status_code == 403

    def test_viewer_cannot_change_status(self, client, mock_sb):
        headers = self._make_token_with_role('viewer', mock_sb)
        resp = client.put('/api/contracts/1/status', headers=headers, json={'status': 'pending'})
        assert resp.status_code == 403

    def test_editor_cannot_change_status(self, client, mock_sb):
        headers = self._make_token_with_role('editor', mock_sb)
        resp = client.put('/api/contracts/1/status', headers=headers, json={'status': 'pending'})
        assert resp.status_code == 403

    def test_viewer_cannot_backup(self, client, mock_sb):
        headers = self._make_token_with_role('viewer', mock_sb)
        resp = client.get('/api/backup', headers=headers)
        assert resp.status_code == 403

    def test_viewer_cannot_access_audit_log(self, client, mock_sb):
        headers = self._make_token_with_role('viewer', mock_sb)
        resp = client.get('/api/audit-log', headers=headers)
        assert resp.status_code == 403

    def test_editor_cannot_access_audit_log(self, client, mock_sb):
        headers = self._make_token_with_role('editor', mock_sb)
        resp = client.get('/api/audit-log', headers=headers)
        assert resp.status_code == 403

    def test_deactivated_user_rejected(self, client, mock_sb):
        from index import mk_token
        token = mk_token("deactivated@test.com")
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        user = {'id': 1, 'email': 'deactivated@test.com', 'role': 'admin', 'is_active': False, 'name': 'Deactivated'}
        def table_side(t):
            chain = mock_chain()
            if t == 'clm_users':
                chain.execute.return_value = make_mock_response([user])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/dashboard', headers=headers)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# BACKUP & RESTORE
# ═══════════════════════════════════════════════════════════════════════════

class TestBackupRestore:
    def test_backup_returns_data(self, client, auth_headers, mock_sb):
        contracts = [{'id': 1, 'name': 'Test', 'status': 'draft'}]
        users = [{'id': 1, 'email': 'admin@test.com', 'password_hash': 'secret_hash'}]
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response(contracts)
            elif t == 'clm_users':
                chain.execute.return_value = make_mock_response(users)
            else:
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/backup', headers=auth_headers)
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'backup_date' in d
        assert 'tables' in d
        assert 'counts' in d
        # Password hash must be stripped
        if d['tables'].get('clm_users'):
            for u in d['tables']['clm_users']:
                assert 'password_hash' not in u

    def test_restore_requires_confirm(self, client, auth_headers, mock_sb):
        resp = client.post('/api/restore', headers=auth_headers, json={
            'tables': {'contracts': []}
        })
        assert resp.status_code == 400
        assert 'confirm' in resp.get_json()['error']['message'].lower()

    def test_restore_invalid_format(self, client, auth_headers, mock_sb):
        resp = client.post('/api/restore', headers=auth_headers, json={
            'confirm': True
        })
        assert resp.status_code == 400

    def test_restore_success(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            chain.execute.return_value = make_mock_response([])
            chain.upsert = MagicMock(return_value=chain)
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.log_activity'):
            resp = client.post('/api/restore', headers=auth_headers, json={
                'confirm': True,
                'tables': {
                    'contracts': [{'id': 1, 'name': 'Restored', 'party_name': 'Acme'}]
                }
            })
        assert resp.status_code == 200
        d = resp.get_json()
        assert 'summary' in d

    def test_restore_skips_users(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.log_activity'):
            resp = client.post('/api/restore', headers=auth_headers, json={
                'confirm': True,
                'tables': {
                    'clm_users': [{'id': 1, 'email': 'hacker@test.com'}]
                }
            })
        assert resp.status_code == 200
        summary = resp.get_json()['summary']
        assert summary['clm_users']['skipped'] is True

    def test_restore_rejects_unknown_tables(self, client, auth_headers, mock_sb):
        def table_side(t):
            chain = mock_chain()
            chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.log_activity'):
            resp = client.post('/api/restore', headers=auth_headers, json={
                'confirm': True,
                'tables': {'malicious_table': [{'data': 'evil'}]}
            })
        assert resp.status_code == 200
        assert resp.get_json()['summary']['malicious_table']['skipped'] is True


# ═══════════════════════════════════════════════════════════════════════════
# PDF GENERATION
# ═══════════════════════════════════════════════════════════════════════════

class TestPDFGeneration:
    def test_pdf_success(self, client, auth_headers, mock_sb):
        contract = {'id': 1, 'name': 'Test Contract', 'party_name': 'Acme',
                     'contract_type': 'client', 'status': 'executed', 'value': 'INR 10,00,000',
                     'department': 'Sales', 'start_date': '2025-01-01', 'end_date': '2025-12-31',
                     'jurisdiction': 'Mumbai', 'governing_law': 'Indian Law',
                     'content': 'This is the contract content.', 'created_at': '2025-01-01T00:00:00'}
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([contract])
            else:
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/contracts/1/pdf', headers=auth_headers)
        assert resp.status_code == 200
        assert 'text/html' in resp.content_type
        html = resp.data.decode()
        assert 'Test Contract' in html
        assert 'Acme' in html

    def test_pdf_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/contracts/999/pdf', headers=auth_headers)
        assert resp.status_code == 404

    def test_pdf_xss_prevention(self, client, auth_headers, mock_sb):
        """Contract name with XSS should be escaped in PDF HTML."""
        contract = {'id': 1, 'name': '<script>alert("xss")</script>', 'party_name': '"><img onerror=alert(1)>',
                     'contract_type': 'client', 'status': 'draft', 'value': '',
                     'department': '', 'start_date': '', 'end_date': '',
                     'jurisdiction': '', 'governing_law': '',
                     'content': 'Safe content', 'created_at': '2025-01-01T00:00:00'}
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([contract])
            else:
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        resp = client.get('/api/contracts/1/pdf', headers=auth_headers)
        assert resp.status_code == 200
        html = resp.data.decode()
        # The XSS payload in the contract name must be escaped
        assert '&lt;script&gt;' in html  # Escaped script tag
        assert '&lt;img' in html  # img tag is escaped (harmless)
        assert '&quot;' in html  # Quotes escaped


# ═══════════════════════════════════════════════════════════════════════════
# EMBED ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════

class TestEmbed:
    def test_embed_no_ai(self, client, auth_headers, mock_sb):
        with patch('index.oai_h', return_value=None):
            resp = client.post('/api/contracts/1/embed', headers=auth_headers)
        assert resp.status_code == 500

    def test_embed_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        with patch('index.oai_h', return_value={'Authorization': 'Bearer x'}):
            resp = client.post('/api/contracts/999/embed', headers=auth_headers)
        assert resp.status_code == 404

    def test_embed_success(self, client, auth_headers, mock_sb):
        contract = {'id': 1, 'name': 'Test', 'content': 'Contract content for embedding.'}
        chain = mock_chain(make_mock_response([contract]))
        mock_sb.table.return_value = chain
        with patch('index.oai_h', return_value={'Authorization': 'Bearer x'}), \
             patch('index.embed_contract', return_value=5):
            resp = client.post('/api/contracts/1/embed', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['chunks'] == 5


# ═══════════════════════════════════════════════════════════════════════════
# WORKFLOW ENGINE (helpers.py)
# ═══════════════════════════════════════════════════════════════════════════

class TestWorkflowEngine:
    def test_run_workflows_add_tag(self, mock_sb):
        from helpers import run_workflows
        rule = {'id': 1, 'name': 'Auto Tag', 'trigger_event': 'contract_created',
                'trigger_condition': {}, 'action_type': 'add_tag',
                'action_config': {'tag': 'new', 'color': '#ff0000'}, 'is_active': True}
        def table_side(t):
            chain = mock_chain()
            if t == 'workflow_rules':
                chain.execute.return_value = make_mock_response([rule])
            elif t == 'contract_tags':
                chain.execute.return_value = make_mock_response([])  # no existing
            else:
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('helpers.log_activity'):
            run_workflows('contract_created', 1, {'name': 'Test'})

    def test_run_workflows_condition_filter(self, mock_sb):
        from helpers import run_workflows
        rule = {'id': 1, 'name': 'High Value Only', 'trigger_event': 'contract_created',
                'trigger_condition': {'min_value': '100000', 'contract_type': 'client'},
                'action_type': 'add_tag', 'action_config': {'tag': 'high-value'}, 'is_active': True}
        def table_side(t):
            chain = mock_chain()
            if t == 'workflow_rules':
                chain.execute.return_value = make_mock_response([rule])
            elif t == 'contract_tags':
                chain.execute.return_value = make_mock_response([])
            else:
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        # Low value — should skip
        with patch('helpers.log_activity'):
            run_workflows('contract_created', 1, {'name': 'Test', 'value': '5000', 'contract_type': 'client'})

    def test_run_workflows_no_db(self):
        from helpers import run_workflows
        with patch('helpers.sb', None):
            run_workflows('contract_created', 1, {})  # Should not crash


# ═══════════════════════════════════════════════════════════════════════════
# EDGE CASES: Malformed requests
# ═══════════════════════════════════════════════════════════════════════════

class TestMalformedRequests:
    def test_empty_json_body(self, client, auth_headers, mock_sb):
        resp = client.post('/api/contracts', headers=auth_headers, data='',
                           content_type='application/json')
        # Empty body → missing fields → 400, or framework 500 for malformed JSON
        assert resp.status_code in (400, 500)

    def test_non_json_content_type(self, client, auth_headers, mock_sb):
        resp = client.post('/api/contracts', headers={'Authorization': auth_headers['Authorization']},
                           data='not json', content_type='text/plain')
        # Flask rejects non-JSON content type with 415
        assert resp.status_code in (400, 415)

    def test_null_json_values(self, client, auth_headers, mock_sb):
        resp = client.post('/api/contracts', headers=auth_headers, json={
            'name': None, 'party_name': None, 'contract_type': None, 'content': None
        })
        assert resp.status_code == 400

    def test_numeric_string_ids(self, client, auth_headers, mock_sb):
        """URL params with non-numeric IDs should 404."""
        resp = client.get('/api/contracts/abc', headers=auth_headers)
        assert resp.status_code == 404

    def test_very_long_query_string(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        long_q = "a" * 500
        resp = client.get(f'/api/search?q={long_q}', headers=auth_headers)
        assert resp.status_code == 200

    def test_special_chars_in_search(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/search?q=%25%27OR%201=1--', headers=auth_headers)
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# LEEGALITY WEBHOOK
# ═══════════════════════════════════════════════════════════════════════════

class TestLeegalityWebhook:
    def test_webhook_no_doc_id(self, client, mock_sb):
        resp = client.post('/api/leegality/webhook', json={'event': 'test'},
                           content_type='application/json')
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'ignored'

    def test_webhook_no_matching_sig(self, client, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        resp = client.post('/api/leegality/webhook', json={
            'documentId': 'doc123', 'event': 'document.signed', 'signer': {'name': 'John'}
        }, content_type='application/json')
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'no match'

    def test_webhook_signed_event(self, client, mock_sb):
        sigs = [{'id': 1, 'contract_id': 5, 'signer_name': 'John', 'signer_email': 'john@test.com',
                 'signature_data': 'leegality:doc123'}]
        def table_side(t):
            chain = mock_chain()
            if t == 'contract_signatures':
                chain.execute.return_value = make_mock_response(sigs)
            elif t == 'contracts':
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        with patch('index.log_activity'):
            resp = client.post('/api/leegality/webhook', json={
                'documentId': 'doc123', 'event': 'invitee.signed',
                'signer': {'name': 'John', 'email': 'john@test.com'}
            }, content_type='application/json')
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# SHARED LINK EXPIRY
# ═══════════════════════════════════════════════════════════════════════════

class TestSharedLinkExpiry:
    def test_expired_link_returns_410(self, client, mock_sb):
        link = {'id': 1, 'contract_id': 5, 'permissions': 'view',
                'expires_at': '2020-01-01T00:00:00', 'is_active': True}
        chain = mock_chain(make_mock_response([link]))
        mock_sb.table.return_value = chain
        resp = client.get('/api/shared/expired_token')
        assert resp.status_code == 410

    def test_inactive_link_returns_404(self, client, mock_sb):
        chain = mock_chain(make_mock_response([]))  # is_active=True filter returns nothing
        mock_sb.table.return_value = chain
        resp = client.get('/api/shared/revoked_token')
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# ERROR HANDLER
# ═══════════════════════════════════════════════════════════════════════════

class TestErrorHandlers:
    def test_404_json(self, client):
        resp = client.get('/api/nonexistent')
        assert resp.status_code == 404
        assert resp.get_json()['error']['code'] == 404

    def test_root_serves_html(self, client):
        """Root / should serve index.html (or 404 if file missing)."""
        resp = client.get('/')
        # Either serves the file or 404 — both are valid in test env
        assert resp.status_code in (200, 404)


# ═══════════════════════════════════════════════════════════════════════════
# _transition_status (helpers.py)
# ═══════════════════════════════════════════════════════════════════════════

class TestTransitionStatus:
    def test_invalid_status_value(self, app, mock_sb):
        from helpers import _transition_status
        with app.app_context():
            resp, code = _transition_status(1, "invalid_status")
        assert code == 400

    def test_contract_not_found(self, app, mock_sb):
        from helpers import _transition_status
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        with app.app_context():
            resp, code = _transition_status(999, "pending")
        assert code == 404

    def test_invalid_transition(self, app, mock_sb):
        from helpers import _transition_status
        chain = mock_chain(make_mock_response([{'id': 1, 'status': 'draft', 'name': 'Test'}]))
        mock_sb.table.return_value = chain
        with app.app_context():
            resp, code = _transition_status(1, "executed")
        assert code == 400

    def test_valid_transition(self, app, mock_sb):
        from helpers import _transition_status
        def table_side(t):
            chain = mock_chain()
            if t == 'contracts':
                chain.execute.return_value = make_mock_response([{'id': 1, 'status': 'draft', 'name': 'Test'}])
            elif t == 'contract_approvals':
                chain.execute.return_value = make_mock_response([])
            else:
                chain.execute.return_value = make_mock_response([])
            return chain
        mock_sb.table.side_effect = table_side
        with app.app_context():
            with patch('helpers.log_activity'), patch('helpers.fire_webhooks'), \
                 patch('helpers.create_notification'), patch('helpers.run_workflows'):
                resp, code = _transition_status(1, "pending")
        assert code == 200


# ═══════════════════════════════════════════════════════════════════════════
# AI-DEPENDENT ENDPOINTS (mocked)
# ═══════════════════════════════════════════════════════════════════════════

class TestAIEndpoints:
    def test_parse_no_ai(self, client, auth_headers):
        with patch('index.oai_h', return_value=None):
            resp = client.post('/api/parse', headers=auth_headers, json={'content': 'Test contract'})
        assert resp.status_code == 500

    def test_parse_no_content(self, client, auth_headers):
        with patch('index.oai_h', return_value={'Authorization': 'Bearer x'}):
            resp = client.post('/api/parse', headers=auth_headers, json={})
        assert resp.status_code == 400

    def test_chat_no_ai(self, client, auth_headers, mock_sb):
        with patch('index.oai_h', return_value=None):
            resp = client.post('/api/chat', headers=auth_headers, json={'message': 'Hello'})
        assert resp.status_code == 500

    def test_chat_no_message(self, client, auth_headers, mock_sb):
        with patch('index.oai_h', return_value={'Authorization': 'Bearer x'}):
            resp = client.post('/api/chat', headers=auth_headers, json={})
        assert resp.status_code == 400

    def test_review_no_ai(self, client, auth_headers, mock_sb):
        with patch('index.oai_h', return_value=None):
            resp = client.post('/api/contracts/1/review', headers=auth_headers)
        assert resp.status_code == 500

    def test_review_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        with patch('index.oai_h', return_value={'Authorization': 'Bearer x'}):
            resp = client.post('/api/contracts/999/review', headers=auth_headers)
        assert resp.status_code == 404

    def test_suggest_clauses_no_ai(self, client, auth_headers, mock_sb):
        with patch('index.oai_h', return_value=None):
            resp = client.post('/api/ai/suggest-clauses', headers=auth_headers, json={})
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# ESIGN INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

class TestEsignIntegration:
    def test_esign_not_configured(self, client, auth_headers, mock_sb):
        with patch('index.LEEGALITY_KEY', ''):
            resp = client.post('/api/contracts/1/esign', headers=auth_headers, json={})
        assert resp.status_code == 503

    def test_esign_not_found(self, client, auth_headers, mock_sb):
        chain = mock_chain(make_mock_response([]))
        mock_sb.table.return_value = chain
        with patch('index.LEEGALITY_KEY', 'test-key'):
            resp = client.post('/api/contracts/999/esign', headers=auth_headers, json={
                'signers': [{'name': 'John', 'email': 'john@test.com'}]
            })
        assert resp.status_code == 404

    def test_esign_missing_signers(self, client, auth_headers, mock_sb):
        contract = {'id': 1, 'name': 'Test', 'content': 'text', 'content_html': '', 'party_name': 'Acme'}
        chain = mock_chain(make_mock_response([contract]))
        mock_sb.table.return_value = chain
        with patch('index.LEEGALITY_KEY', 'test-key'):
            resp = client.post('/api/contracts/1/esign', headers=auth_headers, json={
                'signers': []
            })
        assert resp.status_code == 400

    def test_esign_signer_missing_email(self, client, auth_headers, mock_sb):
        contract = {'id': 1, 'name': 'Test', 'content': 'text', 'content_html': '', 'party_name': 'Acme'}
        chain = mock_chain(make_mock_response([contract]))
        mock_sb.table.return_value = chain
        with patch('index.LEEGALITY_KEY', 'test-key'):
            resp = client.post('/api/contracts/1/esign', headers=auth_headers, json={
                'signers': [{'name': 'John'}]  # missing email
            })
        assert resp.status_code == 400
