import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './context/AuthContext'
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/dashboard/Dashboard'
import ContractsList from './pages/contracts/ContractsList'

function PrivateRoute({ children }) {
  const { user, loading } = useAuth()
  if (loading) return (
    <div style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#9ca3af' }}>
      Loading…
    </div>
  )
  return user ? children : <Navigate to="/login" replace />
}

function PublicRoute({ children }) {
  const { user, loading } = useAuth()
  if (loading) return null
  return user ? <Navigate to="/dashboard" replace /> : children
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<PublicRoute><Login /></PublicRoute>} />
          <Route path="/" element={<PrivateRoute><Layout /></PrivateRoute>}>
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="dashboard" element={<Dashboard />} />
            <Route path="contracts" element={<ContractsList />} />
            <Route path="*" element={
              <div style={{ padding: '4rem', textAlign: 'center', color: '#9ca3af' }}>
                <div style={{ fontSize: '2.5rem', marginBottom: '1rem', opacity: .3 }}>🚧</div>
                <h3 style={{ color: '#374151', margin: '0 0 .5rem' }}>Coming soon in React</h3>
                <p style={{ fontSize: '.84rem', margin: 0 }}>This page is still being migrated.</p>
              </div>
            } />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
