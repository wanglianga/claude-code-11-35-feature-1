import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, getUser } from '../api'
import Badge from '../components/Badge'
import { Json, Ts } from '../components/Format'

const RULE_LABEL = {
  R1: '终端有效性与归属',
  R2: '采样空洞',
  R3: '速度连续性',
  R4: '目标区域交集',
  R5: '门岗通过与缓冲区',
  R6: '道路走廊归属',
  R7: '补传与载荷完整性',
  R8: '锚点时序与签收后离场',
}

export default function SpacetimeDetail() {
  const { id } = useParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [openRule, setOpenRule] = useState({})
  const analyst = getUser()?.role === 'analyst'

  const load = useCallback(() => {
    api.get(`/api/spacetime/batches/${id}`).then(setData).catch((e) => setError(e.message))
  }, [id])
  useEffect(load, [load])

  const targetArea = data?.geo_areas.find((a) => a.kind === 'target')

  async function act(path, label) {
    if (!targetArea) {
      setError('缺少目标区域（target），无法评估')
      return
    }
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await api.post(`/api/batches/${id}/spacetime/${path}`, { target_area_id: targetArea.id })
      setNotice(`${label}完成（版本化追加，历史结果保留）`)
      load()
    } catch (e) {
      setError(`${label}失败：${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  if (!data) return <div>{error ? <div className="alert error">{error}</div> : '加载中…'}</div>
  const latest = data.evaluations[0]
  const latestInf = data.inferences[0]

  return (
    <div>
      <h1 className="page-title">时空证据下钻 · {data.batch_id}</h1>
      <p className="page-desc">
        车辆 {data.vehicle_id} · 服务时间 <Ts value={data.service_time} /> ·{' '}
        <Link to={`/batches/${data.batch_id}`}>返回批次详情</Link>
      </p>
      {error && <div className="alert error">{error}</div>}
      {notice && <div className="alert ok">{notice}</div>}

      <div className="cards">
        <div className="card">
          <div className="k">最新证据状态</div>
          <div className="v" style={{ fontSize: 18 }}>
            {latest ? <Badge value={latest.status} /> : <span className="muted">尚未评估</span>}
          </div>
        </div>
        <div className="card">
          <div className="k">入域概率（候选合并）</div>
          <div className="v" style={{ fontSize: 18 }}>
            {latestInf ? latestInf.target_probability.toFixed(4) : '—'}
            {latestInf && <small> / 候选 {latestInf.candidates.length} 条 · 路网 v{latestInf.road_model_version}</small>}
          </div>
        </div>
        <div className="card">
          <div className="k">定位点 / 终端</div>
          <div className="v" style={{ fontSize: 18 }}>
            {data.points.length}
            <small> 点 · {data.terminals.length} 台终端</small>
          </div>
        </div>
        <div className="card">
          <div className="k">载荷冲突</div>
          <div className="v" style={{ fontSize: 18 }}>
            {data.payload_conflicts.length}
            <small> 组（同序列哈希不同，双份保留）</small>
          </div>
        </div>
      </div>

      {analyst && (
        <div className="panel">
          <div className="actions">
            <button className="primary" disabled={busy} onClick={() => act('evaluate', '声明式规则评估')}>
              执行时空证据评估（R1–R8）
            </button>
            <button disabled={busy} onClick={() => act('infer', '状态空间路径推断')}>
              执行路径推断（保留候选）
            </button>
            <span className="muted">目标区域：{targetArea?.id || '未登记'}</span>
          </div>
        </div>
      )}

      <div className="panel">
        <h2>终端注册与安装期</h2>
        <table>
          <thead>
            <tr>
              <th>终端</th>
              <th>车辆</th>
              <th>安装时间</th>
              <th>退役时间</th>
              <th>状态</th>
              <th>备注</th>
            </tr>
          </thead>
          <tbody>
            {data.terminals.map((t) => (
              <tr key={t.id}>
                <td className="mono">{t.terminal_id}</td>
                <td className="mono">{t.vehicle_id}</td>
                <td>
                  <Ts value={t.install_at} />
                </td>
                <td>
                  <Ts value={t.retire_at} />
                </td>
                <td>
                  <Badge value={t.status} />
                </td>
                <td className="muted">{t.note || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>
          定位点（含精度、补传、时钟校正）
          <span className="tip">退役/无安装期终端的点标注有效性；事件时间已按设备时钟偏移校正</span>
        </h2>
        <table>
          <thead>
            <tr>
              <th>序列</th>
              <th>终端</th>
              <th>设备事件时间</th>
              <th>校正时间</th>
              <th>经纬度</th>
              <th>精度半径(m)</th>
              <th>补传</th>
              <th>有效性</th>
              <th>载荷哈希</th>
            </tr>
          </thead>
          <tbody>
            {data.points.map((p) => (
              <tr key={p.id}>
                <td className="mono">{p.seq}</td>
                <td className="mono">{p.terminal_id}</td>
                <td>
                  <Ts value={p.event_time} />
                </td>
                <td>
                  <Ts value={p.corrected_time} />
                </td>
                <td className="mono" style={{ fontSize: 12 }}>
                  {p.lon.toFixed(5)}, {p.lat.toFixed(5)}
                </td>
                <td className={p.accuracy_m >= data.thresholds.low_accuracy_m ? 'mono' : 'mono'}
                    style={{ color: p.accuracy_m >= data.thresholds.low_accuracy_m ? 'var(--bad)' : undefined }}>
                  {p.accuracy_m}
                </td>
                <td>{p.delayed ? <Badge value="review" /> : '—'}</td>
                <td>
                  <Badge value={p.terminal_validity === 'valid' ? 'valid' : p.terminal_validity === 'retired' ? 'retired' : 'no_install'} />
                </td>
                <td className="hash">{p.payload_hash.slice(0, 16)}…</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.payload_conflicts.length > 0 && (
        <div className="panel">
          <h2>同序列载荷冲突（两份均保留，不得覆盖）</h2>
          <Json data={data.payload_conflicts} />
        </div>
      )}

      <div className="panel">
        <h2>声明式规则结果（理由透明，阈值公开）</h2>
        {latest ? (
          <>
            <p className="muted">{latest.note}</p>
            <table>
              <thead>
                <tr>
                  <th>规则</th>
                  <th>名称</th>
                  <th>结果</th>
                  <th>阻断</th>
                  <th>理由</th>
                  <th>明细</th>
                </tr>
              </thead>
              <tbody>
                {latest.rule_results.map((r) => (
                  <tr key={r.rule_id}>
                    <td className="mono">{r.rule_id}</td>
                    <td>{RULE_LABEL[r.rule_id] || r.rule_name}</td>
                    <td>
                      <Badge value={r.status} />
                    </td>
                    <td>{r.blocking ? '是' : '—'}</td>
                    <td style={{ maxWidth: 420 }}>{r.message}</td>
                    <td>
                      <button
                        className="small"
                        onClick={() => setOpenRule((s) => ({ ...s, [r.rule_id]: !s[r.rule_id] }))}
                      >
                        {openRule[r.rule_id] ? '收起' : '展开'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {latest.rule_results.map((r) =>
              openRule[r.rule_id] ? (
                <div key={r.rule_id} className="panel" style={{ background: 'var(--bg)' }}>
                  <b>{r.rule_id} {RULE_LABEL[r.rule_id]} · 明细</b>
                  <Json data={r.details} />
                </div>
              ) : null,
            )}
          </>
        ) : (
          <p className="muted">尚未执行评估</p>
        )}
      </div>

      <div className="panel">
        <h2>锚点互证：门岗 / 地磅 / 签收</h2>
        {latest ? (
          <table>
            <thead>
              <tr>
                <th>锚点</th>
                <th>是否存在</th>
                <th>事件时间</th>
                <th>场地</th>
                <th>定位互证</th>
                <th>支持定位点</th>
                <th>边缘待核点</th>
              </tr>
            </thead>
            <tbody>
              {latest.anchors.map((a) => (
                <tr key={a.anchor}>
                  <td>{a.anchor}</td>
                  <td>{a.present ? '是' : '否'}</td>
                  <td>
                    <Ts value={a.event_time} />
                  </td>
                  <td className="mono">{a.site_id || '—'}</td>
                  <td className="mono">{a.present ? a.match : '—'}</td>
                  <td className="mono">{a.track_support_point_ids?.join(', ') || '—'}</td>
                  <td className="mono">{a.edge_point_ids?.join(', ') || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">尚未执行评估</p>
        )}
      </div>

      <div className="panel">
        <h2>
          状态空间路径推断（候选路径全部保留，不补画缺失路段）
          <span className="tip">入域判定只基于观测吸附节点并带入精度</span>
        </h2>
        {latestInf ? (
          <>
            <p>
              经过目标区域合并概率 <b>{latestInf.target_probability.toFixed(4)}</b> · {latestInf.note}
            </p>
            <table>
              <thead>
                <tr>
                  <th>候选</th>
                  <th>概率</th>
                  <th>确定入域</th>
                  <th>边缘待核</th>
                  <th>走廊</th>
                  <th>空洞</th>
                  <th>回绕</th>
                  <th>节点路径</th>
                </tr>
              </thead>
              <tbody>
                {latestInf.candidates.map((c, i) => (
                  <tr key={i}>
                    <td>#{i + 1}</td>
                    <td className="mono">{c.probability.toFixed(4)}</td>
                    <td>{c.enters_target ? '是' : '否'}</td>
                    <td>{c.target_edge_uncertain ? '是' : '否'}</td>
                    <td className="mono" style={{ fontSize: 12 }}>{c.corridors.join(', ') || '—'}</td>
                    <td>{c.gaps.length}</td>
                    <td>{c.cycle_count}</td>
                    <td className="mono" style={{ fontSize: 12, maxWidth: 320 }}>
                      {c.path.join(' → ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h2 style={{ marginTop: 12 }}>假设与依赖（道路模型、校准、不确定性声明）</h2>
            <ul className="muted">
              {latestInf.assumptions.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
            {latestInf.candidates.some((c) => c.gaps.length > 0) && (
              <>
                <h2>候选空洞明细（缺失路段不补画）</h2>
                <Json data={latestInf.candidates.flatMap((c, i) => c.gaps.map((g) => ({ candidate: i + 1, ...g })))} />
              </>
            )}
          </>
        ) : (
          <p className="muted">尚未执行路径推断</p>
        )}
      </div>

      <div className="panel">
        <h2>地理区域与走廊登记</h2>
        <table>
          <thead>
            <tr>
              <th>区域ID</th>
              <th>名称</th>
              <th>类型</th>
              <th>场地</th>
              <th>缓冲半径(m)</th>
            </tr>
          </thead>
          <tbody>
            {data.geo_areas.map((a) => (
              <tr key={a.id}>
                <td className="mono">{a.id}</td>
                <td>{a.name}</td>
                <td>
                  <Badge value={a.kind} />
                </td>
                <td className="mono">{a.site_id || '—'}</td>
                <td>{a.buffer_radius_m ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
