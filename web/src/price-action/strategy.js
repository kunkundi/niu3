// Trading adapter for the same causal engine used by the chart. Prices are qfq.
import { createPriceActionEngine } from './engine.js'
import { DAILY_HISTORY_BARS, DAILY_STRUCTURE_BARS, dailyStructureOptions } from './daily-policy.js'
import { entryRejection } from './entry-quality.js'

export function priceActionLevels(input, asOf, tick = 0.001, options = {}) {
  const bars = input.filter((b) => b.date <= asOf && b.closed !== false).slice(-DAILY_HISTORY_BARS)
  if (
    bars.length < (options.minimumBars ?? 120) ||
    bars.at(-1).date !== asOf ||
    bars.some(
      (b, i) =>
        (i && b.date <= bars[i - 1].date) ||
        ![b.open, b.high, b.low, b.close].every((v) => Number.isFinite(v) && v > 0) ||
        b.low > Math.min(b.open, b.close) ||
        b.high < Math.max(b.open, b.close),
    )
  )
    return { ready: false, reason: '完整日 K 不足、过期或 OHLC 异常' }
  const a = createPriceActionEngine(tick).analyzePriceAction(bars, [], dailyStructureOptions)
  // Explicit research option; live requests retain the existing default policy.
  const trend = options.directionMode === 'current' ? a.alwaysIn.trend : a.trend
  const n = bars.length - 1
  const candidates = []
  for (const s of a.signals) {
    if (s.index < n - 2 || !['pending', 'confirmed'].includes(s.status)) continue
    const directional =
      s.direction === 'neutral'
        ? trend === 'up'
          ? 'bullish'
          : trend === 'down'
            ? 'bearish'
            : ''
        : s.direction
    if (!directional) continue
    // Neutral inside bars use the mother bar boundary; no breakout of an inner bar alone.
    const b = s.type === 'inside-bar' ? bars[s.index - 1] : bars[s.index]
    if (directional === 'bullish' && !/顺势|关键位/.test(s.contextLabel) && trend !== 'up') continue
    candidates.push({
      direction: directional,
      label: s.label,
      index: s.index,
      knownAt: bars[s.index].date,
      high: b.high,
      low: b.low,
      context: s.contextLabel,
      type: s.type,
    })
  }
  for (const b of a.breakouts) {
    if (
      b.knownAtIndex < n - 2 ||
      b.knownAtIndex > n ||
      b.boundaryIndex < a.structureStartIndex ||
      !['confirmed-breakout', 'breakout-retest', 'continuation'].includes(b.phase)
    )
      continue
    const bar = bars[b.knownAtIndex]
    candidates.push({
      direction: b.direction,
      label: `${b.structureLabel} 突破跟进`,
      index: b.knownAtIndex,
      knownAt: bar.date,
      high: Math.max(bar.high, b.boundaryPrice),
      low: Math.min(bar.low, b.boundaryPrice),
      context: b.phase,
      type: 'structure',
      followThrough: b.followThrough,
      retested: b.retestIndex != null,
    })
  }
  candidates.sort((x, y) => y.index - x.index)
  const entryPolicy = options.entryPolicy ?? 'valid'
  const rejectedEntries = []
  let bull = candidates.find((s) => {
    if (s.direction !== 'bullish') return false
    const reason = entryRejection(s, bars, trend, tick, entryPolicy)
    if (reason) rejectedEntries.push({ label: s.label, knownAt: s.knownAt, reason })
    return !reason
  })
  // Recovery need not print a named candle pattern. Its breakout still needs a
  // later quote; current-day OHLC never participates in creating this setup.
  // Research-only: broad historical validation did not justify enabling it live.
  if (!bull && options.continuation === true && trend !== 'down' && bars.length >= 3) {
    const [first, previous, latest] = bars.slice(-3)
    if (
      first.close < previous.close &&
      previous.close < latest.close &&
      latest.low > previous.low &&
      latest.close >= (latest.high + latest.low) / 2
    ) {
      bull = {
        direction: 'bullish',
        label: '上行延续突破',
        index: n,
        knownAt: latest.date,
        high: Math.max(previous.high, latest.high),
        low: latest.low,
        context: '连续收盘抬高、最近低点抬高，等待突破两日高点',
      }
    }
  }
  const bear = candidates.find((s) => s.direction === 'bearish')
  const lows = a.recentSwings.filter((s) => s.type === 'low' && s.confirmedIndex <= n)
  const support = a.levels.filter((s) => s.type === 'support').sort((x, y) => y.price - x.price)[0]
  const resistance = a.levels.filter((s) => s.type === 'resistance').sort((x, y) => x.price - y.price)[0]
  const entry = bull ? bull.high + tick : null
  const stop = bull ? bull.low - tick : null
  const backgroundSupport = a.background.levels.find((s) => s.type === 'support')
  const backgroundResistance = a.background.levels.find((s) => s.type === 'resistance')
  const targetLevel = bull
    ? [...a.levels, ...a.background.levels]
        .filter((s) => s.type === 'resistance' && s.price > entry)
        .sort((x, y) => x.price - y.price)[0]
    : null
  // Nearest real resistance caps reward. Without it, use a measured move of the signal bar.
  const target = bull ? (targetLevel?.price ?? entry + 2 * (bull.high - bull.low)) : null
  return {
    ready: true,
    as_of: asOf,
    timeframe: 'day',
    entry_policy: entryPolicy,
    entry_rejections: rejectedEntries,
    history_bars: bars.length,
    history_limit: DAILY_HISTORY_BARS,
    structure_bars: DAILY_STRUCTURE_BARS,
    structure_start: bars[a.structureStartIndex].date,
    background: {
      trend: { up: '上升结构', down: '下降结构', range: '震荡结构' }[a.background.trend],
      start: a.background.startDate,
      support: backgroundSupport?.price ?? null,
      resistance: backgroundResistance?.price ?? null,
    },
    trend:
      options.directionMode === 'current'
        ? { up: '上升结构', down: '下降结构', range: '震荡结构' }[trend]
        : a.trendLabel,
    ...(options.directionMode === 'current'
      ? { direction_policy: 'current-direction-v1', direction: a.alwaysIn }
      : {}),
    setup: bull?.label ?? '',
    signal_day: bull?.knownAt ?? null,
    entry,
    entry_stop: stop,
    target,
    entry_ceiling: bull ? Math.min(entry + (entry - stop) * 0.5, target) : null,
    exit: bear ? bear.low - tick : null,
    exit_setup: bear?.label ?? '',
    exit_day: bear?.knownAt ?? null,
    structural_stop: lows.length ? lows.at(-1).price - tick : null,
    support: support?.price ?? null,
    resistance: resistance?.price ?? null,
    t_stop: support?.zoneLow > tick ? support.zoneLow - tick : null,
    t_allowed: trend !== 'down' && !!support && !!resistance && resistance.price > support.price,
    evidence: {
      bull: bull ?? null,
      bear: bear ?? null,
      swing: lows.at(-1) ?? null,
      support: support ?? null,
      resistance: resistance ?? null,
      target: targetLevel ?? null,
    },
  }
}
