import { Navigate, Route, Routes } from 'react-router-dom'
import { getToken } from './api'
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Batches from './pages/Batches'
import BatchDetail from './pages/BatchDetail'
import Evidence from './pages/Evidence'
import ReviewQueue from './pages/ReviewQueue'
import Reports from './pages/Reports'

function RequireAuth({ children }) {
  return getToken() ? children : <Navigate to="/login" replace />
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="batches" element={<Batches />} />
        <Route path="batches/:id" element={<BatchDetail />} />
        <Route path="evidence" element={<Evidence />} />
        <Route path="review" element={<ReviewQueue />} />
        <Route path="reports" element={<Reports />} />
      </Route>
    </Routes>
  )
}
