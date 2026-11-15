import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, saveAuth } from '../api'

export default function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  async function submit(e) {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const data = await api.login(username, password)
      saveAuth(data.token, data.user)
      navigate('/')
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-box" onSubmit={submit}>
        <h1>收运批次重量一致性与计费差异数据服务</h1>
        <div className="sub">来源证据 · 批次关联 · 计量裁定 · 费率编译 · 差异发布</div>
        {error && <div className="alert error">{error}</div>}
        <input placeholder="用户名" value={username} onChange={(e) => setUsername(e.target.value)} />
        <input
          type="password"
          placeholder="密码"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button className="primary" disabled={loading}>
          {loading ? '登录中…' : '登录'}
        </button>
        <div className="hint">
          测试账号：analyst / Analyst@2026（数据团队，可写）
          <br />
          auditor / Auditor@2026（财务复核，只读）
        </div>
      </form>
    </div>
  )
}
