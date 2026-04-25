import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import client from '../../api/client'

function StatCard({ label, value, color, icon, onClick }) {
  return (
    <div
      onClick={onClick}
      style={{
        background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12,
        padding: '1.2rem 1.4rem', cursor: onClick ? 'pointer' : 'default',
        transition: 'transform .15s, box-shadow .15s', display: 'flex', alignItems: 'center', gap: '1rem'
      }}
      onMouseEnter={e => onClick && (e.currentTarget.style.transform = 'translateY(-2px)')}
      onMouseLeave={e => onClick && (e.currentTarget.style.transform = '')}
    >
      <div style={{ fontSize: '1.6rem', opacity: .8 }}>{icon}</div>
      <div>
        <div style={{ fontSize: '1.6rem', fontWeight: 800, color, lineHeight: 1 }}>{value ?? '—'}</div>
        <div style={{ fontSize: '.75rem', color: 'var(--text4)', marginTop: '.2rem', fontWeight: 500 }}>{label}</div>
      </div>
    </div>
  )
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    client.get('/dashboard')
      .then(res => setData(res.data))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  const stats = [
    { label: 'Total Contracts', value: data?.total, icon: '📄', color: '#6366f1', filter: '' },
    { label: 'Draft', value: data?.draft, icon: '✏️', color: '#8b5cf6', filter: 'draft' },
    { label: 'Pending', value: data?.pending, icon: '⏳', color: '#ea580c', filter: 'pending' },
    { label: 'In Review', value: data?.in_review, icon: '🔍', color: '#0891b2', filter: 'in_review' },
    { label: 'Executed', value: data?.executed, icon: '✅', color: '#16a34a', filter: 'executed' },
  ]

  return (
    <div style={{ padding: '1.5rem' }}>
      <div style={{ marginBottom: '1.5rem' }}>
        <h1 style={{ fontSize: '1.1rem', fontWeight: 700, margin: '0 0 .2rem' }}>Dashboard</h1>
        <p style={{ fontSize: '.78rem', color: 'var(--text3)', margin: 0 }}>Overview of your contract portfolio</p>
      </div>

      {/* Stats grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '.8rem', marginBottom: '1.5rem' }}>
        {stats.map(s => (
          <StatCard
            key={s.label} {...s}
            onClick={() => navigate(`/contracts${s.filter ? `?status=${s.filter}` : ''}`)}
          />
        ))}
      </div>

      {/* Two column layout */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
        {/* Expiring soon */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: '1.2rem' }}>
          <h3 style={{ fontSize: '.88rem', fontWeight: 600, margin: '0 0 1rem', display: 'flex', alignItems: 'center', gap: '.4rem' }}>
            ⏰ Expiring Soon <span style={{ fontSize: '.7rem', color: 'var(--text4)', fontWeight: 400 }}>Next 30 days</span>
          </h3>
          {loading ? (
            <div style={{ color: 'var(--text4)', fontSize: '.82rem' }}>Loading…</div>
          ) : data?.expiring_contracts?.length ? (
            data.expiring_contracts.map(c => (
              <div key={c.id} onClick={() => navigate(`/contracts/${c.id}`)} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '.5rem .6rem', borderRadius: 8, cursor: 'pointer', marginBottom: '.3rem', transition: 'background .1s' }}
                onMouseEnter={e => e.currentTarget.style.background = 'var(--surface2)'}
                onMouseLeave={e => e.currentTarget.style.background = ''}>
                <span style={{ fontSize: '.83rem', fontWeight: 500 }}>Contract #{c.id}</span>
                <span style={{ fontSize: '.72rem', background: '#fff7ed', color: '#ea580c', padding: '2px 8px', borderRadius: 20, fontWeight: 600 }}>{c.days_left}d left</span>
              </div>
            ))
          ) : (
            <div style={{ color: 'var(--text4)', fontSize: '.82rem', textAlign: 'center', padding: '1.5rem 0' }}>No contracts expiring soon</div>
          )}
        </div>

        {/* Recent activity */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: '1.2rem' }}>
          <h3 style={{ fontSize: '.88rem', fontWeight: 600, margin: '0 0 1rem' }}>📋 Recent Activity</h3>
          {loading ? (
            <div style={{ color: 'var(--text4)', fontSize: '.82rem' }}>Loading…</div>
          ) : data?.recent_activity?.length ? (
            data.recent_activity.slice(0, 6).map((a, i) => (
              <div key={i} style={{ display: 'flex', gap: '.6rem', padding: '.45rem 0', borderBottom: '1px solid var(--border3)' }}>
                <div style={{ width: 7, height: 7, borderRadius: '50%', background: '#6366f1', marginTop: 5, flexShrink: 0 }} />
                <div>
                  <div style={{ fontSize: '.82rem', fontWeight: 500 }}>{a.action}</div>
                  <div style={{ fontSize: '.72rem', color: 'var(--text4)' }}>{a.user_name}</div>
                </div>
              </div>
            ))
          ) : (
            <div style={{ color: 'var(--text4)', fontSize: '.82rem', textAlign: 'center', padding: '1.5rem 0' }}>No recent activity</div>
          )}
        </div>
      </div>
    </div>
  )
}
