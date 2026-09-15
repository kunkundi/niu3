import { intradayTradeGroups, layoutTradeMarkers } from './trade-observation.js'
import { priceLineLayout } from './signal-chart.js'
import { intradayLinePoints, intradayLinePrice, intradayLinePath } from './intraday-line.js'

const quantityFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 })
const amountFormat = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function tradeIntradayChart(data, events, day, width = 300, levels = [], minHeight = 124) {
  if (!data || data.day !== day || !data.points?.length || !(Number(data.previous_close) > 0)) return null
  const points = intradayLinePoints(data)
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
  let height = Math.max(124, minHeight)
  const top = 22,
    inset = 8
  const previous = Number(data.previous_close)
  const spread =
    Math.max(
      previous * 0.002,
      0.001,
      ...points.map((point) => Math.abs(Number(point.price) - previous)),
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
    const execution = `成交${group.items.length > 1 ? '均' : ''}价 ${group.price.toFixed(3)} 元`
    return {
      ...group,
      gross,
      linePrice: intradayLinePrice(points, group.timestamp),
      label: `${action} ${group.time}`,
      labelDetails: [execution, quantity, amount],
      labelWidth: Math.min(
        width - 4,
        Math.max(
          group.label === 'T' ? 132 : 112,
          execution.length * 6 + 10,
          quantity.length * 6 + 10,
          amount.length * 6 + 10,
        ),
      ),
      labelHeight: 54,
      leaderDash: '3 3',
      title: `${group.event.name} ${day} ${group.time} ${action} ${quantity}，成交金额 ${amount}，${execution}；标记按成交时间贴合分时线，查看成交详情`,
    }
  })
  const located = labels.filter((label) => label.linePrice !== null)
  const unplaced = labels.filter((label) => label.linePrice === null)
  let anchors
  // Keep ordinary cards compact; add space only when every full callout cannot fit.
  // Never silently drop a transaction label because several executions are close together.
  for (let attempt = 0; attempt <= located.length; attempt++) {
    anchors = located.map((label) => ({
      ...label,
      x: x(label.minute) / width,
      y: y(label.linePrice) / height,
      labelTop: label.side === 'BUY' ? height - label.labelHeight - 2 : 2,
    }))
    const placed = layoutTradeMarkers(
      anchors.map((anchor) => ({ ...anchor, x: anchor.x * width, y: anchor.y * height })),
      width,
      height,
    )
    if (placed.length === anchors.length || attempt === located.length) break
    height += Math.max(44, ...located.map((label) => label.labelHeight + 4))
  }
  const plotted = points.map((point) => ({ ...point, x: x(point.minute), y: y(point.price) }))
  return {
    path: intradayLinePath(plotted),
    anchors,
    unplaced,
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
    last: plotted.at(-1),
  }
}
