import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { clearAuth, getUser } from '../api'

const NAV = [
  { to: '/', label: '总览', end: true },
  { to: '/batches', label: '收运批次' },
  { to: '/spacetime', label: '时空证据' },
  { to: '/evidence', label: '来源证据库' },
  { to: '/review', label: '复核队列' },
  { to: '/reports', label: '月结报表' },
]

export default function Layout() {
  const user = getUser()
  const navigate = useNavigate()
  return (
    <div className="layout">
      <aside className="sidebar">
        <h1>收运批次重量一致性<br />与计费差异数据服务</h1>
        <div className="sub">证据 · 关联 · 裁定 · 费率 · 发布</div>
        <nav>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? 'active' : '')}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="user">
          <div>{user?.display_name}</div>
          <div className="muted">{user?.username} · {user?.role === 'analyst' ? '数据团队' : '财务只读'}</div>
          <button
            className="small"
            onClick={() => {
              clearAuth()
              navigate('/login')
            }}
          >
            退出登录
          </button>
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  )
}
