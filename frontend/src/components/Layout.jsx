import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

const navItems = [
  { to: '/dashboard', icon: '⊞', label: 'Dashboard' },
  { to: '/contracts', icon: '📄', label: 'Contracts' },
  { to: '/chat', icon: '🤖', label: 'AI Assistant' },
  { to: '/templates', icon: '🗂', label: 'Templates' },
  { to: '/clauses', icon: '📚', label: 'Clause Library' },
  { to: '/reports', icon: '📊', label: 'Reports' },
  { to: '/calendar', icon: '📅', label: 'Calendar' },
  { to: '/workflows', icon: '⚡', label: 'Workflows' },
  { to: '/users', icon: '👥', label: 'Users' },
]

export default function Layout() {
  const { user, role, logout } = useAuth()

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', background: 'var(--bg)' }}>
      {/* Sidebar */}
      <aside style={{
        width: 220, flexShrink: 0, background: 'var(--surface)',
        borderRight: '1px solid var(--border)', display: 'flex',
        flexDirection: 'column', overflow: 'hidden'
      }}>
        {/* Logo */}
        <div style={{ padding: '1.2rem 1rem', borderBottom: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '.6rem' }}>
            <div style={{
              width: 34, height: 34, borderRadius: 9,
              background: 'linear-gradient(135deg,#6366f1,#8b5cf6)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: '1rem', flexShrink: 0
            }}>📋</div>
            <div>
              <div style={{ fontSize: '.88rem', fontWeight: 700, color: 'var(--text)' }}>EMB CLM</div>
              <div style={{ fontSize: '.65rem', color: 'var(--text4)', textTransform: 'uppercase', letterSpacing: '.05em' }}>Contract Mgmt</div>
            </div>
          </div>
        </div>

        {/* Nav */}
        <nav style={{ flex: 1, padding: '.6rem .5rem', overflowY: 'auto' }}>
          {navItems.map(({ to, icon, label }) => (
            <NavLink
              key={to}
              to={to}
              style={({ isActive }) => ({
                display: 'flex', alignItems: 'center', gap: '.6rem',
                padding: '.5rem .75rem', borderRadius: 8, marginBottom: '.15rem',
                textDecoration: 'none', fontSize: '.84rem', fontWeight: 500,
                color: isActive ? '#6366f1' : 'var(--text3)',
                background: isActive ? 'rgba(99,102,241,.08)' : 'transparent',
                transition: 'all .15s'
              })}
            >
              <span style={{ fontSize: '.9rem', width: 18, textAlign: 'center' }}>{icon}</span>
              {label}
            </NavLink>
          ))}
        </nav>

        {/* User footer */}
        <div style={{ padding: '.8rem', borderTop: '1px solid var(--border)' }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: '.5rem',
            padding: '.5rem .6rem', borderRadius: 8, background: 'var(--surface2)'
          }}>
            <div style={{
              width: 28, height: 28, borderRadius: '50%',
              background: 'linear-gradient(135deg,#6366f1,#8b5cf6)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: '.72rem', color: '#fff', fontWeight: 700, flexShrink: 0
            }}>
              {(user || 'U')[0].toUpperCase()}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: '.75rem', fontWeight: 600, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{user}</div>
              <div style={{ fontSize: '.65rem', color: 'var(--text4)', textTransform: 'capitalize' }}>{role}</div>
            </div>
            <button
              onClick={logout}
              title="Sign out"
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text4)', fontSize: '.85rem', padding: 2 }}
            >⎋</button>
          </div>
        </div>
      </aside>

      {/* Main */}
      <main style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
        <Outlet />
      </main>
    </div>
  )
}
