import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async e => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(password)
      navigate('/dashboard')
    } catch (e) {
      setError(e.response?.data?.error?.message || 'Invalid password')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center', background: 'var(--bg)'
    }}>
      <div style={{
        background: 'var(--surface)', borderRadius: 16,
        padding: '2.5rem', width: 380, boxShadow: '0 20px 40px rgba(0,0,0,.1)',
        border: '1px solid var(--border)'
      }}>
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <div style={{
            width: 52, height: 52, borderRadius: 14,
            background: 'linear-gradient(135deg,#6366f1,#8b5cf6)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            margin: '0 auto .8rem', fontSize: '1.4rem'
          }}>📋</div>
          <h1 style={{ fontSize: '1.2rem', fontWeight: 700, margin: '0 0 .2rem' }}>EMB CLM</h1>
          <p style={{ fontSize: '.82rem', color: 'var(--text3)', margin: 0 }}>Contract Lifecycle Management</p>
        </div>

        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: '1rem' }}>
            <label style={{ fontSize: '.78rem', fontWeight: 600, color: 'var(--text3)', display: 'block', marginBottom: '.35rem' }}>
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="Enter access password"
              required
              autoFocus
              style={{
                width: '100%', padding: '.65rem .85rem', borderRadius: 8,
                border: '1px solid var(--border)', background: 'var(--surface2)',
                color: 'var(--text)', fontSize: '.9rem', outline: 'none',
                boxSizing: 'border-box'
              }}
            />
          </div>

          {error && (
            <div style={{
              background: 'var(--red-bg)', color: 'var(--red)',
              border: '1px solid var(--red-border)', borderRadius: 8,
              padding: '.6rem .85rem', fontSize: '.82rem', marginBottom: '1rem'
            }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              width: '100%', padding: '.7rem', borderRadius: 8,
              background: 'linear-gradient(135deg,#6366f1,#8b5cf6)',
              color: '#fff', fontWeight: 600, fontSize: '.9rem',
              border: 'none', cursor: loading ? 'not-allowed' : 'pointer',
              opacity: loading ? .7 : 1
            }}
          >
            {loading ? 'Signing in…' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  )
}
