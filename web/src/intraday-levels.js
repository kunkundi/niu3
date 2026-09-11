import { STRATEGY_LEVELS, priceLineLayout } from './signal-chart.js'
import { prepareCandles } from './candles.js'
import { preparePriceAction } from './price-action/index.js'

const unavailable = (message) => ({ levels: [], message })
const validPrice = (value) => Number.isFinite(Number(value)) && Number(value) > 0

// Minute prices are unadjusted. ETF levels must come from the execution conversion,
// never directly from the qfq chart or from another session's conversion factor.
export function intradayReference(reference, minute) {
  if (!reference) return unavailable('')
  if (!minute?.points?.length) return unavailable('')
  if (!reference.symbol) return unavailable('关键点位加载中…')
  if (reference.symbol !== minute.symbol || minute.day !== minute.expected_day)
    return unavailable('关键点位与分时日期尚未对齐')
  if (reference.symbol === 'sh000001') return indexReference(reference, minute)
  if (!reference.matched || !reference.row?.pa?.ready) return unavailable('关键点位待策略结构与日 K 对齐')
  const raw = reference.intraday_reference
  if (!raw || raw.session_day !== minute.day || !(reference.as_of < minute.day))
    return unavailable('关键点位等待本交易日价格换算')
  const levels = STRATEGY_LEVELS.map((level) => ({ ...level, value: Number(raw.levels?.[level.id]) })).filter(
    (level) => validPrice(level.value),
  )
  return {
    levels,
    message: levels.length ? `日 K ${reference.as_of} · 关键点位已换算为交易价` : '暂无有效策略关键点位',
  }
}

function indexReference(reference, minute) {
  // After close the daily endpoint may include this session. Only the preceding
  // completed candles may provide levels drawn across this session's minute line.
  const prepared = prepareCandles((reference.bars || []).filter((bar) => bar.day < minute.day))
  const basis = prepared.bars.at(-1)
  if (
    !basis ||
    !validPrice(minute.previous_close) ||
    Math.abs(basis.close - Number(minute.previous_close)) > 0.011
  )
    return unavailable('指数关键点位等待昨收与日 K 对齐')
  const { analysis } = preparePriceAction(prepared.bars, prepared.omitted, 'market', 0.01)
  if (!analysis) return unavailable('指数日 K 样本不足，关键点位待更新')
  const levels = ['support', 'resistance'].flatMap((type) =>
    analysis.levels
      .filter((level) => level.type === type && validPrice(level.price))
      .sort((a, b) => (type === 'support' ? b.price - a.price : a.price - b.price))
      .slice(0, 3)
      .map((level, index) => ({
        id: `${type}-${index + 1}`,
        label: `${type === 'support' ? '支撑 S' : '压力 R'}${index + 1}`,
        value: level.price,
        color: type === 'support' ? 'var(--green)' : 'var(--yellow)',
      })),
  )
  return {
    levels,
    message: levels.length ? `日 K ${basis.day} · 指数支撑压力（点）` : '暂无已确认的指数支撑压力',
  }
}

// Keep only the nearest level on each side of the latest minute price. The
// neighborhood is determined by observed prices, so distant levels cannot widen it.
export function nearbyIntradayLevels(data, levels = []) {
  const latest = Number(data.points?.at(-1)?.price)
  if (!validPrice(latest) || !validPrice(data.previous_close)) return []
  const prices = [Number(data.previous_close), ...(data.points || []).map((p) => Number(p.price))].filter(
    validPrice,
  )
  const low = Math.min(...prices),
    high = Math.max(...prices)
  const padding = Math.max((high - low) * 0.2, latest * 0.003, 0.001)
  const nearby = levels.filter(
    (level) => validPrice(level.value) && level.value >= low - padding && level.value <= high + padding,
  )
  const below = nearby.filter((level) => level.value <= latest).sort((a, b) => b.value - a.value)[0]
  const above = nearby.filter((level) => level.value > latest).sort((a, b) => a.value - b.value)[0]
  return [below, above].filter(Boolean)
}

// Keep all active T references and only nearby daily levels. Label positions may
// move for legibility, but their price lines and minute anchors cannot.
export function intradayGeometry(data, levels = [], height = 220, tradePrices = [], tLevels = []) {
  const previous = Number(data.previous_close)
  const references = [
    ...nearbyIntradayLevels(data, levels),
    ...tLevels.filter((level) => validPrice(level.value)),
  ]
  const spread =
    Math.max(
      previous * 0.002,
      0.001,
      ...(data.points || []).map((p) => Math.abs(Number(p.price) - previous)),
      ...tradePrices.filter(validPrice).map((price) => Math.abs(Number(price) - previous)),
      ...references.map((level) => Math.abs(level.value - previous)),
    ) * 1.1
  const low = previous - spread,
    high = previous + spread
  const plotted = (data.points || []).map((point) => ({
    ...point,
    x: (point.minute / 240) * 600,
    y: 110 - ((Number(point.price) - previous) / spread) * 100,
  }))
  return {
    previous,
    spread,
    low,
    high,
    plotted,
    path: plotted.map((p) => `${p.x},${p.y}`).join(' '),
    references: priceLineLayout(references, low, high, 10, 210, (20 * 220) / Math.max(height, 1)),
  }
}
