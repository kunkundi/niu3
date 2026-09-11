import { PA_DEFAULTS } from './price-action/index.js'

const number = (value) =>
  value == null || value === '' || !Number.isFinite(Number(value)) ? null : Number(value)

export const MA_PERIODS = [5, 10, 20, 60]
export const DEFAULT_INDICATORS = {
  ma5: false,
  ma10: false,
  ma20: false,
  ma60: true,
  ema20: true,
  boll: false,
  dc: false,
  volume: true,
  macd: false,
  ...PA_DEFAULTS,
  support: true,
  resistance: true,
  swings: true,
  invalidation: true,
}
export const STRATEGY_INDICATORS = { ...DEFAULT_INDICATORS }

// Recognize the previous defaults so upgrading does not overwrite a custom chart.
const legacyMarketIndicators = {
  ma5: true,
  ma10: true,
  ma20: true,
  ma60: false,
  ema20: false,
  boll: false,
  dc: false,
  volume: true,
  macd: true,
  ...PA_DEFAULTS,
}
const legacyStrategyIndicators = {
  ...legacyMarketIndicators,
  ma5: false,
  ma10: false,
  ma20: false,
  ema20: true,
  macd: false,
  swings: true,
  bos: true,
  choch: true,
  pinBar: true,
  engulfing: true,
}
const keyFor = (preset, version = 2) =>
  `niuno3.kline.${preset === 'strategy' ? 'strategy-indicators' : 'indicators'}.v${version}`

function readIndicators(storage, key) {
  try {
    const saved = JSON.parse(storage.getItem(key))
    if (saved && typeof saved === 'object' && !Array.isArray(saved)) return saved
  } catch {
    // Private browsing or damaged preferences must not prevent chart rendering.
  }
}

export function loadIndicators(storage, preset = 'market') {
  const defaults = preset === 'strategy' ? STRATEGY_INDICATORS : DEFAULT_INDICATORS
  let saved = readIndicators(storage, keyFor(preset))
  const migrate = saved == null
  if (migrate) {
    const legacy = readIndicators(storage, keyFor(preset, 1))
    const previous = preset === 'strategy' ? legacyStrategyIndicators : legacyMarketIndicators
    const customized = Object.entries(previous).some(
      ([key, value]) => typeof legacy?.[key] === 'boolean' && legacy[key] !== value,
    )
    if (customized)
      saved = Object.fromEntries(
        Object.entries(previous).map(([key, value]) => [
          key,
          typeof legacy[key] === 'boolean' ? legacy[key] : value,
        ]),
      )
  }
  const indicators = Object.fromEntries(
    Object.entries(defaults).map(([key, value]) => [
      key,
      typeof saved?.[key] === 'boolean' ? saved[key] : value,
    ]),
  )
  if (migrate) saveIndicators(storage, indicators, preset)
  return indicators
}

export function saveIndicators(storage, indicators, preset = 'market') {
  try {
    storage.setItem(keyFor(preset), JSON.stringify(indicators))
  } catch {
    // The controls still work for this session if storage is unavailable.
  }
}

// Preserve actual OHLC values. A close-only record must never become a candle.
export function prepareCandles(points = []) {
  const byDay = new Map()
  for (const point of points) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(point.day || '')) continue
    const stamp = new Date(`${point.day}T00:00:00Z`)
    if (!Number.isFinite(stamp.getTime()) || stamp.toISOString().slice(0, 10) !== point.day) continue
    byDay.set(point.day, {
      day: point.day,
      ...(point.live ? { live: true, at: point.at, stale: point.stale } : {}),
      open: number(point.open),
      high: number(point.high),
      low: number(point.low),
      close: number(point.close),
      volume: number(point.volume),
      amount: number(point.amount),
    })
  }
  const rows = [...byDay.values()].sort((a, b) => a.day.localeCompare(b.day))
  const bars = []
  let fast = null,
    slow = null,
    signal = 0,
    consecutive = 0,
    ema20 = null
  let emaCloses = []
  rows.forEach((row, index) => {
    const { open, high, low, close } = row
    let macd = null
    // Seed EMAs from the first close; warm up for 26 consecutive valid closes.
    // Chinese-market BAR convention: 2 × (DIF - DEA). Never bridge missing closes.
    if (close > 0) {
      emaCloses.push(close)
      if (emaCloses.length > 20) emaCloses.shift()
      if (ema20 != null) ema20 += (2 / 21) * (close - ema20)
      else if (emaCloses.length === 20) ema20 = emaCloses.reduce((sum, value) => sum + value, 0) / 20
      fast = fast == null ? close : fast + (2 / 13) * (close - fast)
      slow = slow == null ? close : slow + (2 / 27) * (close - slow)
      const dif = fast - slow
      signal += (2 / 10) * (dif - signal)
      consecutive += 1
      if (consecutive >= 26) macd = { dif, dea: signal, histogram: 2 * (dif - signal) }
    } else {
      ema20 = null
      emaCloses = []
      fast = slow = null
      signal = consecutive = 0
    }
    if (
      ![open, high, low, close].every((value) => value !== null && value > 0) ||
      high < Math.max(open, close) ||
      low > Math.min(open, close) ||
      high < low
    )
      return
    const previous = rows[index - 1]?.close
    const ma = Object.fromEntries(
      MA_PERIODS.map((period) => {
        const window = rows.slice(Math.max(0, index - period + 1), index + 1)
        return [
          period,
          window.length === period && window.every((point) => point.close > 0)
            ? window.reduce((total, point) => total + point.close, 0) / period
            : null,
        ]
      }),
    )
    const channelWindow = rows.slice(Math.max(0, index - 19), index + 1)
    let boll = null,
      dc = null
    if (ma[20] != null) {
      const deviation = Math.sqrt(
        channelWindow.reduce((total, point) => total + (point.close - ma[20]) ** 2, 0) / 20,
      )
      boll = { upper: ma[20] + 2 * deviation, middle: ma[20], lower: ma[20] - 2 * deviation }
    }
    if (
      channelWindow.length === 20 &&
      channelWindow.every(
        (point) =>
          point.low > 0 &&
          point.high >= point.low &&
          (point.open == null || (point.open >= point.low && point.open <= point.high)) &&
          (point.close == null || (point.close >= point.low && point.close <= point.high)),
      )
    ) {
      const upper = Math.max(...channelWindow.map((point) => point.high))
      const lower = Math.min(...channelWindow.map((point) => point.low))
      dc = { upper, middle: (upper + lower) / 2, lower }
    }
    bars.push({
      ...row,
      volume: row.volume !== null && row.volume >= 0 ? row.volume : null,
      amount: row.amount !== null && row.amount >= 0 ? row.amount : null,
      change: previous > 0 ? close / previous - 1 : null,
      ma,
      boll,
      dc,
      macd,
      ema20,
    })
  })
  return { bars, omitted: rows.length - bars.length }
}

export function candleWindow(bars, count = 60, end = bars.length) {
  const stop = Math.max(0, Math.min(bars.length, end))
  return bars.slice(Math.max(0, stop - count), stop)
}
