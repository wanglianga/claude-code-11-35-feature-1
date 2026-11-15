const LABELS = {
  // 批次状态
  open: ['待处理', ''],
  adjudicated: ['已裁定', 'info'],
  published: ['已发布', 'ok'],
  under_review: ['复审中', 'hold'],
  // 计量事实
  issued: ['已签发', 'ok'],
  pending_conflict: ['待核冲突', 'bad'],
  // 关联
  candidate: ['候选', 'info'],
  confirmed: ['已确认', 'ok'],
  frozen: ['冻结中', 'hold'],
  rejected: ['已拒绝', 'bad'],
  // 发布
  paused: ['已暂停', 'hold'],
  pending: ['待定', 'warn'],
  // 离场核查
  consistent: ['一致', 'ok'],
  contradiction: ['矛盾', 'bad'],
  insufficient: ['证据不足', 'warn'],
  // 金额事实
  superseded: ['已作废', 'bad'],
  // 策略
  priority: ['优先级裁定', 'info'],
  interval: ['区间裁定', 'hold'],
  // 校准
  active: ['有效', 'ok'],
  corrected: ['已更正', 'warn'],
  // 其他
  cross_tier: ['跨阶梯', 'warn'],
}

export default function Badge({ value }) {
  if (value === null || value === undefined) return <span className="muted">—</span>
  const [label, cls] = LABELS[value] || [value, '']
  return <span className={`badge ${cls}`}>{label}</span>
}
