import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import Badge from '../components/Badge'
import { Ts } from '../components/Format'

const STATE_DESC = {
  trusted: '规则全部通过，轨迹与锚点互证',
  limited: '证据成立但存在明确约束（采样空洞/速度未知/补传）',
  review: '低精度边缘或走廊/门岗归属待核',
  uncertifiable: '退役终端/载荷冲突等，关键证据不可认证',
}

export default function Spacetime() {
  const [rows, setRows] = useState([])
  const [error, setError] = useState('')

  useEffect(() => {
    api.get('/api/spacetime/statuses').then(setRows).catch((e) => setError(e.message))
  }, [])

  const counts = rows.reduce((acc, r) => {
    if (r.status) acc[r.status] = (acc[r.status] || 0) + 1
    return acc
  }, {})

  return (
    <div>
      <h1 className="page-title">时空证据状态</h1>
      <p className="page-desc">
        车辆轨迹穿过多边形不等于收运可信：定位漂移、退役终端、长时间补传都可能画出看似完整的路线。
        每条批次输出四态证据状态，并可下钻定位、终端安装期、精度、走廊、门岗、地磅与签收锚点。
      </p>
      {error && <div className="alert error">{error}</div>}

      <div className="cards">
        <div className="card">
          <div className="k">可信</div>
          <div className="v">{counts.trusted || 0}</div>
        </div>
        <div className="card">
          <div className="k">受限</div>
          <div className="v">{counts.limited || 0}</div>
        </div>
        <div className="card">
          <div className="k">待核</div>
          <div className="v">{counts.review || 0}</div>
        </div>
        <div className="card">
          <div className="k">不可认证</div>
          <div className="v">{counts.uncertifiable || 0}</div>
        </div>
      </div>

      <div className="panel">
        <h2>
          批次时空证据一览
          <span className="tip">状态由声明式规则 R1–R8 逐项检查后聚合；路径推断保留全部候选</span>
        </h2>
        <table>
          <thead>
            <tr>
              <th>批次</th>
              <th>车辆</th>
              <th>服务时间</th>
              <th>证据状态</th>
              <th>入域概率（路径推断）</th>
              <th>候选路径</th>
              <th>下钻</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.batch_id}>
                <td>
                  <Link to={`/spacetime/${r.batch_id}`}>{r.batch_id}</Link>
                </td>
                <td className="mono">{r.vehicle_id}</td>
                <td>
                  <Ts value={r.service_time} />
                </td>
                <td>
                  {r.status ? <Badge value={r.status} /> : <span className="muted">未评估</span>}
                  {r.status && (
                    <div className="muted" style={{ fontSize: 11, maxWidth: 260 }}>
                      {STATE_DESC[r.status]}
                    </div>
                  )}
                </td>
                <td className="mono">{r.target_probability != null ? r.target_probability.toFixed(4) : '—'}</td>
                <td>{r.candidate_count ?? '—'}</td>
                <td>
                  <Link to={`/spacetime/${r.batch_id}`}>查看证据链</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
