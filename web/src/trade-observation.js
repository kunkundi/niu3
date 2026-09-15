// All event times use the exchange timezone, independent of the viewer's device.
export function exchangeTime(at) {
  const timestamp = Date.parse(at)
  if (!Number.isFinite(timestamp)) return null
  const local = new Date(timestamp + 8 * 3600000).toISOString()
  const day = local.slice(0, 10),
    time = local.slice(11, 19)
  const clock = Number(time.slice(0, 2)) * 60 + Number(time.slice(3, 5)) + Number(time.slice(6)) / 60
  const minute =
    clock >= 570 && clock <= 690 ? clock - 570 : clock >= 780 && clock <= 900 ? clock - 660 : null
  return { day, time, minute, timestamp }
}

export const TRADE_EFFECTS = [
  { id: 'open', side: 'BUY', label: '开仓', marker: 'B' },
  { id: 'add', side: 'BUY', label: '加仓', marker: 'B' },
  { id: 'reduce', side: 'SELL', label: '减仓', marker: 'S' },
  { id: 'close', side: 'SELL', label: '清仓', marker: 'S' },
  { id: 't_sell', orderKind: 't_sell', side: 'SELL', label: '做 T 卖出', marker: 'T' },
  { id: 't_buy', orderKind: 't_buy', side: 'BUY', label: '做 T 买回', marker: 'T' },
]
export function tradeEffect(item) {
  return (
    TRADE_EFFECTS.find(
      (effect) => effect.orderKind && effect.orderKind === item.order_kind && effect.side === item.side,
    ) ||
    TRADE_EFFECTS.find(
      (effect) => !effect.orderKind && effect.id === item.position_effect && effect.side === item.side,
    ) || {
      id: 'unknown',
      side: item.side,
      label: item.side === 'BUY' ? '买入' : '卖出',
      marker: item.side === 'BUY' ? 'B' : 'S',
    }
  )
}

export function tradeGroups(items = [], mode = 'daily') {
  const groups = new Map()
  for (const item of items) {
    const when = exchangeTime(item.at)
    if (
      !when ||
      !['BUY', 'SELL'].includes(item.side) ||
      !Number.isFinite(Number(item.price)) ||
      !Number.isFinite(Number(item.quantity)) ||
      !(Number(item.price) > 0) ||
      !(Number(item.quantity) > 0)
    )
      continue
    const bucket = mode === 'intraday' ? when.time.slice(0, 5) : when.day
    const effect = tradeEffect(item)
    const id = `${item.symbol}:${when.day}:${bucket}:${item.side}:${effect.id}`
    if (!groups.has(id))
      groups.set(id, {
        id,
        day: when.day,
        time: when.time,
        minute: when.minute,
        timestamp: when.timestamp,
        side: item.side,
        effect: effect.id,
        effectTone: `trade-effect-${effect.id}`,
        actionLabel: effect.label,
        label: effect.marker,
        kind: 'fill',
        quantity: 0,
        gross: 0,
        items: [],
      })
    const group = groups.get(id)
    group.items.push(item)
    group.quantity += Number(item.quantity)
    group.gross += Number(item.price) * Number(item.quantity)
    if (when.timestamp > group.timestamp) Object.assign(group, when)
  }
  return [...groups.values()]
    .map((group) => ({
      ...group,
      price: group.gross / group.quantity,
      tone: group.side === 'BUY' ? 'buy' : 'sell',
    }))
    .sort((a, b) => a.timestamp - b.timestamp)
}

export function intradayTradeGroups(items, data) {
  return tradeGroups(
    (items || []).filter((item) => !data?.symbol || item.symbol === data.symbol),
    'intraday',
  ).filter((g) => g.day === data?.day && g.minute !== null)
}

export function tradeMarkerConnector(marker) {
  const cx = marker.left + marker.labelWidth / 2,
    cy = marker.top + marker.labelHeight / 2,
    dx = marker.x - cx,
    dy = marker.y - cy,
    scale = Math.max(Math.abs(dx) / (marker.labelWidth / 2), Math.abs(dy) / (marker.labelHeight / 2))
  // Stop at the label edge so its text is not crossed by its own leader.
  return scale > 1
    ? [
        { x: marker.x, y: marker.y },
        { x: cx + dx / scale, y: cy + dy / scale },
      ]
    : []
}

function untangleTradeLabels(markers) {
  const center = (m) => ({ x: m.left + m.labelWidth / 2, y: m.top + m.labelHeight / 2 })
  const cross = (a, b, c) => (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
  const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y)
  // Keep neighboring executions in time order instead of letting the greedy
  // collision search send an earlier label across all the later executions.
  for (const side of ['BUY', 'SELL']) {
    const ordered = markers
      .filter((m) => m.labelDetails?.length && m.side === side)
      .sort((a, b) => a.x - b.x || a.y - b.y)
    const slots = ordered
      .map(({ left, top }) => ({ left, top }))
      .sort((a, b) => a.left - b.left || a.top - b.top)
    ordered.forEach((marker, index) => Object.assign(marker, slots[index]))
  }
  // All detailed labels occupy equal-sized slots. Swapping two slots preserves
  // label/point clearance. Uncrossing strictly shortens the total leader length,
  // so repeat until no crossing remains without moving any execution anchor.
  let changed
  do {
    changed = false
    for (let i = 0; i < markers.length; i++) {
      const a = markers[i]
      if (!a.labelDetails?.length) continue
      for (let j = i + 1; j < markers.length; j++) {
        const b = markers[j]
        if (!b.labelDetails?.length) continue
        const ac = center(a),
          bc = center(b)
        if (
          cross(a, ac, b) * cross(a, ac, bc) < 0 &&
          cross(b, bc, a) * cross(b, bc, ac) < 0 &&
          distance(a, bc) + distance(b, ac) < distance(a, ac) + distance(b, bc) - 1e-8
        ) {
          const left = a.left,
            top = a.top
          a.left = b.left
          a.top = b.top
          b.left = left
          b.top = top
          changed = true
        }
      }
    }
  } while (changed)
  return markers
}

// The label may move to avoid collisions; its connector always ends at the original event.
export function layoutTradeMarkers(markers, width, height) {
  if (width < 40 || height < 36) return []
  const placed = []
  const clamp = (v, low, high) => Math.max(low, Math.min(high, v))
  const detailed = markers.filter((marker) => marker.labelDetails?.length)
  const detailedWidth = Math.max(18, ...detailed.map((marker) => marker.labelWidth || 22))
  const detailedHeight = Math.max(18, ...detailed.map((marker) => marker.labelHeight || 22))
  for (const marker of [...markers].sort(
    (a, b) => Number(a.kind === 'candidate') - Number(b.kind === 'candidate') || b.x - a.x,
  )) {
    const candidate = marker.kind === 'candidate'
    const labelWidth = marker.labelDetails?.length
        ? detailedWidth
        : marker.labelWidth >= 18
          ? marker.labelWidth
          : candidate
            ? 18
            : 22,
      labelHeight = marker.labelDetails?.length
        ? detailedHeight
        : marker.labelHeight >= 18
          ? marker.labelHeight
          : candidate
            ? 18
            : 22
    if (width < labelWidth + 4 || height < labelHeight + 4) continue
    const left = clamp(marker.x - labelWidth / 2, 2, width - labelWidth - 2)
    const direction = marker.side === 'SELL' ? -1 : 1
    const offset = 8
    const idealTop = Number.isFinite(marker.labelTop)
      ? marker.labelTop
      : marker.y + (direction === 1 ? offset : -labelHeight - offset)
    const candidates = []
    for (let row = 0; row <= Math.ceil(height / (labelHeight + 4)) * 2; row++) {
      for (const column of [0, -1, 1, -2, 2, -3, 3]) {
        candidates.push({
          left: clamp(left + column * (labelWidth + 6), 2, width - labelWidth - 2),
          top: clamp(
            idealTop + (row % 2 ? 1 : -1) * Math.ceil(row / 2) * (labelHeight + 4) * direction,
            2,
            height - labelHeight - 2,
          ),
        })
      }
    }
    const position = candidates.find(
      (p) =>
        (!marker.labelDetails ||
          markers.every(
            (point) =>
              point.kind !== 'fill' ||
              point.x < p.left - 4 ||
              point.x > p.left + labelWidth + 4 ||
              point.y < p.top - 4 ||
              point.y > p.top + labelHeight + 4,
          )) &&
        placed.every(
          (q) =>
            p.left >= q.left + q.labelWidth + 4 ||
            q.left >= p.left + labelWidth + 4 ||
            p.top >= q.top + q.labelHeight + 4 ||
            q.top >= p.top + labelHeight + 4,
        ),
    )
    if (position) placed.push({ ...marker, ...position, labelWidth, labelHeight })
  }
  return untangleTradeLabels(placed)
}

export function tradeIntent(plan, row) {
  if (!row) return { tone: 'neutral', title: '等待策略', message: '选择 ETF 查看买卖信号与模拟成交' }
  if (plan?.mode === 'post_close' && !plan?.stale)
    return {
      tone: 'candidate',
      title: row.post_close_candidate ? '次日候选' : '盘后观察',
      message: row.reasons?.join('；') || '盘后结构参考，等待下一交易日盘中确认',
    }
  if (plan?.stale || row.quote?.stale)
    return { tone: 'neutral', title: '信号待更新', message: '行情或信号已过期，等待重新确认' }
  if (plan?.session_snapshot)
    return { tone: 'neutral', title: '休市快照', message: '当前为休市时段，信号来自上次盘中快照' }
  if (plan?.mode !== 'live')
    return {
      tone: 'neutral',
      title: row.selected ? '计划入选' : '等待条件',
      message: '收盘计划参考，成交以模拟账户记录为准',
    }
  const action = row.pa?.action
  const execution = plan.execution?.items?.find((item) => item.symbol === row.symbol)?.message
  return {
    tone: action === 'buy' ? 'buy' : action === 'exit' ? 'sell' : 'neutral',
    title: action === 'buy' ? '买入信号' : action === 'exit' ? '卖出信号' : '等待条件',
    message: execution || row.reasons?.join('；') || '持续观察结构与触发条件',
  }
}
