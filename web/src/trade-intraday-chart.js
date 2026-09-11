import { intradayTradeGroups, layoutTradeMarkers } from './trade-observation.js'
import { priceLineLayout } from './signal-chart.js'

const quantityFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 })
const amountFormat = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function tradeIntradayChart(data, events, day, width = 300, levels = []) {
  if (!data || data.day !== day || !data.points?.length || !(Number(data.previous_close) > 0)) return null
  const points = data.points.filter(
    (point) => Number(point.price) > 0 && point.minute >= 0 && point.minute <= 240,
  )
  if (!points.length) return null
  const references = levels.filter((level) => Number.isFinite(level.value) && level.value > 0)
  const groups = events
    .filter((event) => event.day === day && event.symbol === data.symbol)
    .flatMap((event) =>
      intradayTradeGroups(event.items, { ...data, points }).map((group) => ({
        ...group,
        id: `${event.id}:${group.id}`,
        event,
      })),
    )
  let height = 124
  const top = 22,
    inset = 8
  const previous = Number(data.previous_close)
  const spread =
    Math.max(
      previous * 0.002,
      0.001,
      ...points.map((point) => Math.abs(Number(point.price) - previous)),
      ...groups.map((group) => Math.abs(group.price - previous)),
      ...references.map((level) => Math.abs(level.value - previous)),
    ) * 1.1
  const x = (minute) => inset + (minute / 240) * (width - inset * 2)
  const y = (price) => height / 2 - (((price - previous) / spread) * (height - top * 2)) / 2
  const labels = groups.map((group) => {
    const action = group.label === 'T' ? group.actionLabel : group.side === 'BUY' ? '买入' : '卖出'
    const gross = group.items.reduce(
      (sum, item) =>
        sum +
        (item.gross != null && Number.isFinite(Number(item.gross))
          ? Number(item.gross)
          : Number(item.price) * Number(item.quantity)),
      0,
    )
    const quantity = `${quantityFormat.format(group.quantity)} 份`
    const amount = `${amountFormat.format(gross)} 元`
    return {
      ...group,
      gross,
      label: `${action} ${group.time}`,
      labelDetails: [quantity, amount],
      labelWidth: Math.min(
        width - 4,
        Math.max(group.label === 'T' ? 132 : 96, quantity.length * 6 + 10, amount.length * 6 + 10),
      ),
      labelHeight: 40,
      leaderDash: '3 3',
      title: `${group.event.name} ${day} ${group.time} ${action} ${quantity}，成交金额 ${amount}，成交${group.items.length > 1 ? '均' : ''}价 ${group.price.toFixed(3)} 元，查看成交详情`,
    }
  })
  let anchors
  // Keep ordinary cards compact; add space only when every full callout cannot fit.
  // Never silently drop a transaction label because several executions are close together.
  for (let attempt = 0; attempt <= labels.length; attempt++) {
    anchors = labels.map((label) => ({
      ...label,
      x: x(label.minute) / width,
      y: y(label.price) / height,
      labelTop: label.side === 'BUY' ? height - label.labelHeight - 2 : 2,
    }))
    const placed = layoutTradeMarkers(
      anchors.map((anchor) => ({ ...anchor, x: anchor.x * width, y: anchor.y * height })),
      width,
      height,
    )
    if (placed.length === anchors.length || attempt === labels.length) break
    height += 44
  }
  let lastMinute = null
  const path = points
    .map((point) => {
      const command = lastMinute === null || point.minute - lastMinute > 2 ? 'M' : 'L'
      lastMinute = point.minute
      return `${command}${x(point.minute)},${y(Number(point.price))}`
    })
    .join(' ')
  return {
    path,
    anchors,
    references: priceLineLayout(references, previous - spread, previous + spread, top, height - top, 18).map(
      (line) => ({ ...line, x: x(line.minute) }),
    ),
    width,
    height,
    previous,
    previousY: y(previous),
    high: previous + spread,
    low: previous - spread,
    top,
    bottom: height - top,
    last: { ...points.at(-1), x: x(points.at(-1).minute), y: y(Number(points.at(-1).price)) },
  }
}
