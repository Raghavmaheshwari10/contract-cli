import { createContext, useContext, useState, useEffect } from 'react'
import client from '../api/client'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [role, setRole] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const token = localStorage.getItem('cm_token')
    const storedRole = localStorage.getItem('cm_role')
    if (token) {
      client.get('/auth/verify')
        .then(res => {
          setUser(res.data.email)
          setRole(storedRole || res.data.role)
        })
        .catch(() => logout())
        .finally(() => setLoading(false))
    } else {
      setLoading(false)
    }
  }, [])

  const login = async (password) => {
    const res = await client.post('/auth/login', { password })
    const { token, role: r, email } = res.data
    localStorage.setItem('cm_token', token)
    localStorage.setItem('cm_role', r)
    setUser(email)
    setRole(r)
    return { token, role: r }
  }

  const logout = () => {
    localStorage.removeItem('cm_token')
    localStorage.removeItem('cm_role')
    setUser(null)
    setRole(null)
  }

  return (
    <AuthContext.Provider value={{ user, role, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)
