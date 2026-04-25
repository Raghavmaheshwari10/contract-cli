import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useContracts } from '../../hooks/useContracts'

const STATUS_TABS = [
  { label: 'All', value: '' },
  { label: 'Draft', value: 'draft' },
  { label: 'Pending', value: 'pending' },
  { label: 'In Review', value: 'in_review' },
  { label: 'Executed', value: 'executed' },
  { label: 'Rejected', value: 'rejected' },
]

const BADGE = {
  draft: { bg: '#f3f4f6', color: '#6b7280' },
  pending: { bg: '#fff7ed', color: '#ea580c' },
  in_review: { bg: '#ecfeff', color: '#0891b2' },
  executed: { bg: '#f0fdf4', color: '#16a34a' },
  rejected: { bg: '#fef2f2', color: '#dc2626' },
  client: { bg: '#eef2ff', color: '#6366f1' },
  vendor: { bg: '#fefce8', color: '#ca8a04' },
}

function Badge({ value, type = 'status' }) {
  const style = BADGE[value] || { bg: '#f3f4f6', color: '#6b7280' }
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      padding: '2px 8px', borderRadius: 20, fontSize: '.72rem', fontWeight: 600,
      background: style.bg, color: style.color
    }}>
      <span style={{ width: 5, height: 5, borderRadius: '50%', background: style.color, flexShrink: 0 }} />
      {value?.replace('_', ' ')}
    </span>
  )
}

export default function ContractsList() {
  const navigate = useNavigate()
  const [status, setStatus] = useState('')
  const [type, setType] = useState('')
  const [page, setPage] = useState(1)

  const { data, total, pages, loading, error, refetch } = useContracts({ page, status, type })

  return (
    <div style={{ padding: '1.5rem', height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.2rem' }}>
        <div>
          <h1 style={{ fontSize: '1.1rem', fontWeight: 700, margin: 0 }}>Contracts</h1>
          <p style={{ fontSize: '.78rem', color: 'var(--text3)', margin: '.2rem 0 0' }}>{total} total</p>
        </div>
        <button
          onClick={() => navigate('/contracts/new')}
          style={{
            display: 'flex', alignItems: 'center', gap: '.4rem',
            padding: '.5rem 1rem', borderRadius: 8,
            background: 'linear-gradient(135deg,#6366f1,#8b5cf6)',
            color: '#fff', fontWeight: 600, fontSize: '.84rem',
            border: 'none', cursor: 'pointer'
          }}
        >
          + New Contract
        </button>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: '.5rem', marginBottom: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
        {STATUS_TABS.map(t => (
          <button
            key={t.value}
            onClick={() => { setStatus(t.value); setPage(1) }}
            style={{
              padding: '.3rem .75rem', borderRadius: 20, fontSize: '.78rem', fontWeight: 500,
              border: '1px solid', cursor: 'pointer', transition: 'all .15s',
              background: status === t.value ? '#6366f1' : 'var(--surface)',
              borderColor: status === t.value ? '#6366f1' : 'var(--border)',
              color: status === t.value ? '#fff' : 'var(--text3)'
            }}
          >
            {t.label}
          </button>
        ))}
        <select
          value={type}
          onChange={e => { setType(e.target.value); setPage(1) }}
          style={{
            padding: '.3rem .6rem', borderRadius: 8, fontSize: '.78rem',
            border: '1px solid var(--border)', background: 'var(--surface)',
            color: 'var(--text)', cursor: 'pointer', marginLeft: 'auto'
          }}
        >
          <option value="">All Types</option>
          <option value="client">Client</option>
          <option value="vendor">Vendor</option>
        </select>
      </div>

      {/* Table */}
      <div style={{ flex: 1, background: 'var(--surface)', borderRadius: 12, border: '1px solid var(--border)', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        {error && (
          <div style={{ padding: '1rem', color: 'var(--red)', fontSize: '.84rem', borderBottom: '1px solid var(--border)' }}>
            ⚠ {error} <button onClick={refetch} style={{ marginLeft: '.5rem', textDecoration: 'underline', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--red)' }}>Retry</button>
          </div>
        )}

        <div style={{ overflowX: 'auto', flex: 1 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.83rem' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
                {['Contract Name', 'Party', 'Type', 'Status', 'Value', 'Start', 'End', 'Dept'].map(h => (
                  <th key={h} style={{ padding: '.6rem .85rem', textAlign: 'left', fontSize: '.72rem', fontWeight: 600, color: 'var(--text4)', textTransform: 'uppercase', letterSpacing: '.04em', whiteSpace: 'nowrap' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 8 }).map((_, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid var(--border3)' }}>
                    {Array.from({ length: 8 }).map((_, j) => (
                      <td key={j} style={{ padding: '.7rem .85rem' }}>
                        <div style={{ height: 14, borderRadius: 4, background: 'var(--surface2)', animation: 'pulse 1.5s ease-in-out infinite' }} />
                      </td>
                    ))}
                  </tr>
                ))
              ) : data.length === 0 ? (
                <tr>
                  <td colSpan={8} style={{ padding: '4rem 2rem', textAlign: 'center', color: 'var(--text4)' }}>
                    <div style={{ fontSize: '2rem', marginBottom: '.5rem', opacity: .3 }}>📄</div>
                    <div style={{ fontWeight: 600, marginBottom: '.25rem' }}>No contracts found</div>
                    <div style={{ fontSize: '.78rem' }}>Create your first contract or upload existing PDFs</div>
                  </td>
                </tr>
              ) : data.map(c => (
                <tr
                  key={c.id}
                  onClick={() => navigate(`/contracts/${c.id}`)}
                  style={{ borderBottom: '1px solid var(--border3)', cursor: 'pointer', transition: 'background .1s' }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--surface2)'}
                  onMouseLeave={e => e.currentTarget.style.background = ''}
                >
                  <td style={{ padding: '.65rem .85rem', fontWeight: 600, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={c.name}>
                    {c.name}
                  </td>
                  <td style={{ padding: '.65rem .85rem', color: 'var(--text3)', maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.party_name || '—'}</td>
                  <td style={{ padding: '.65rem .85rem' }}><Badge value={c.contract_type} /></td>
                  <td style={{ padding: '.65rem .85rem' }}><Badge value={c.status || 'draft'} /></td>
                  <td style={{ padding: '.65rem .85rem', fontWeight: 500, maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={c.value || ''}>{c.value || '—'}</td>
                  <td style={{ padding: '.65rem .85rem', color: 'var(--text3)', whiteSpace: 'nowrap' }}>{c.start_date || '—'}</td>
                  <td style={{ padding: '.65rem .85rem', color: 'var(--text3)', whiteSpace: 'nowrap' }}>{c.end_date || '—'}</td>
                  <td style={{ padding: '.65rem .85rem', color: 'var(--text3)', maxWidth: 100, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.department || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {pages > 1 && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '.65rem 1rem', borderTop: '1px solid var(--border)', background: 'var(--surface2)', fontSize: '.78rem' }}>
            <span style={{ color: 'var(--text4)' }}>Showing {data.length} of {total}</span>
            <div style={{ display: 'flex', gap: '.3rem' }}>
              <PagBtn disabled={page <= 1} onClick={() => setPage(p => p - 1)}>‹</PagBtn>
              {Array.from({ length: Math.min(pages, 7) }, (_, i) => i + 1).map(p => (
                <PagBtn key={p} active={page === p} onClick={() => setPage(p)}>{p}</PagBtn>
              ))}
              <PagBtn disabled={page >= pages} onClick={() => setPage(p => p + 1)}>›</PagBtn>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function PagBtn({ children, active, disabled, onClick }) {
  return (
    <button
      disabled={disabled}
      onClick={onClick}
      style={{
        width: 28, height: 28, borderRadius: 6, border: '1px solid',
        borderColor: active ? '#6366f1' : 'var(--border)',
        background: active ? '#6366f1' : 'var(--surface)',
        color: active ? '#fff' : disabled ? 'var(--text4)' : 'var(--text)',
        cursor: disabled ? 'not-allowed' : 'pointer', fontSize: '.78rem', fontWeight: 500
      }}
    >
      {children}
    </button>
  )
}
