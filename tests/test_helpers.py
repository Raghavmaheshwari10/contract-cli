"""Test helpers for CLM API tests."""
from unittest.mock import MagicMock


def make_mock_response(data=None, count=None):
    """Helper to create mock Supabase responses."""
    resp = MagicMock()
    resp.data = data or []
    resp.count = count
    return resp


def mock_chain(final_response=None):
    """Build a chainable mock (for .select().eq().execute() patterns).

    Returns a mock where any attribute access or call returns the same mock,
    except .execute() which returns final_response.
    """
    if final_response is None:
        final_response = make_mock_response()
    chain = MagicMock()
    chain.execute.return_value = final_response
    # Make all intermediate methods return the same chain
    for method in ['select', 'eq', 'neq', 'in_', 'ilike', 'like', 'gte', 'lte', 'lt',
                   'order', 'limit', 'range', 'or_', 'insert', 'update', 'delete', 'is_']:
        getattr(chain, method).return_value = chain
    # Support chained not_.in_() pattern
    chain.not_ = chain
    return chain
