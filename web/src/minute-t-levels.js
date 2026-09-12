import { exchangeTime } from './trade-observation.js'

const definitions = [
  { key: 'support', label: 'T 买参考', color: 'var(--red)' },
  { key: 'resistance', label: 'T 卖参考', color: 'var(--green)' },
  { key: 'support_stop', label: 'T 失效', color: 'var(--muted)' },
]

export function minuteTDay(snapshot) {
  if (!snapshot?.enabled || snapshot.model !== 'minute5' || !exchangeTime(snapshot.at)) return null
  return 'day' in snapshot ? snapshot.day : exchangeTime(snapshot.at).day
}

// These are current reference prices, never historical signals or executed fills.
export function minuteTLevels(snapshot, data) {
  const day = minuteTDay(snapshot)
  if (!day || data?.day !== day || !data.points?.length) return []
  const item = snapshot.items?.find((item) => item.symbol === data.symbol)
  const basis = exchangeTime(item?.bar_at)
  if (
    !basis ||
    basis.day !== day ||
    basis.minute == null ||
    Date.parse(item.bar_at) > Date.parse(snapshot.at)
  )
    return []
  const detail = [
    `5 分钟做 T${item.frozen ? ' · 本轮冻结价位' : ''}`,
    `最近完整 K ${basis.time.slice(0, 5)} · ${item.source || '来源待更新'}`,
    item.message,
    item.blocked_reason,
    item.data_error ? '分钟行情更新异常，等待恢复' : '',
    '参考价需形态确认，实际成交另以 T 标记',
  ]
    .filter(Boolean)
    .join('；')
  return definitions.flatMap(({ key, ...definition }) => {
    const value = Number(item[key])
    return Number.isFinite(value) && value > 0
      ? [
          {
            ...definition,
            id: `minute-t-${key}`,
            kind: 'minute-t',
            value,
            minute: basis.minute,
            title: detail,
          },
        ]
      : []
  })
}

// The server supplies the exchange date, including the last session on holidays.
// Older closed positions remain in the timeline.
export function minuteTSecurities(securities, snapshot, positions = [], displayDay) {
  const day = displayDay || snapshot?.day || minuteTDay(snapshot)
  const today = day || exchangeTime(snapshot?.at)?.day || exchangeTime(new Date().toISOString()).day
  const held = positions
    .filter((item) => Number(item.quantity) > 0)
    .map((item) => {
      const events = securities.find((security) => security.symbol === item.symbol)?.events || []
      return {
        symbol: item.symbol,
        name: item.name,
        events,
        currentDay: day || exchangeTime(item.quote_at)?.day || events[0]?.day || today,
      }
    })
  const symbols = new Set(held.map((item) => item.symbol))
  const soldToday = securities
    .filter(
      (security) =>
        !symbols.has(security.symbol) &&
        security.events.some((event) => event.day === today && event.side === 'SELL'),
    )
    .map((security) => ({ ...security, currentDay: today }))
  return [...held, ...soldToday]
}

export function tradeIntradayDays(security) {
  return [...new Set([security.currentDay, ...security.events.map((event) => event.day)].filter(Boolean))]
    .sort()
    .reverse()
}
