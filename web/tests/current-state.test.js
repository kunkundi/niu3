import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { createPriceActionEngine } from '../src/price-action/engine.js'
import { selectCurrentTradingRange } from '../src/price-action/current-context.js'
import { dailyStructureOptions } from '../src/price-action/daily-policy.js'
import { prepareChartReview } from '../src/chart-review.js'
import { preparePriceAction } from '../src/price-action/index.js'

const engine = createPriceActionEngine(0.001)
const fixture = JSON.parse(
  readFileSync(new URL('./fixtures/sz159587-current-state.json', import.meta.url), 'utf8'),
)

test('grain ETF on September 9 uses current trend, retaining the failed April triangle only as history', () => {
  const analysis = engine.analyzePriceAction(fixture.bars, [], dailyStructureOptions)
  const oldTriangle = analysis.tradingRanges.find((range) => range.id === 'range:triangle:133:152')
  assert.equal(oldTriangle.status, 'failed-breakout')
  assert.equal(fixture.bars[oldTriangle.endIndex].date, '2026-04-24')
  assert.ok(oldTriangle.endIndex < analysis.structureStartIndex)
  assert.equal(analysis.trend, 'up')
  assert.equal(analysis.marketState.state, 'broad-bull-channel')
  assert.equal(analysis.alwaysIn.state, 'long')
  assert.equal(analysis.activeTradingRange, null)
  assert.equal(analysis.tradingRangePosition, null)
  assert.ok(!analysis.marketState.evidence.includes('摆动高点降低且摆动低点抬高'))
})

test('market and strategy chart presets agree on the same day and use the same 60-bar current structure', () => {
  const candles = fixture.bars.map((bar) => ({ ...bar, day: bar.date }))
  const market = preparePriceAction(candles, 0, 'market').analysis
  const strategy = preparePriceAction(candles, 0, 'strategy').analysis
  assert.deepEqual(market, strategy)
  assert.equal(market.structureStartIndex, candles.length - 60)
  assert.equal(market.marketState.state, 'broad-bull-channel')
  assert.equal(market.alwaysIn.state, 'long')
})

test('reviewing April preserves the then-known triangle and does not import its later failure', () => {
  const points = fixture.bars.map((bar) => ({ ...bar, day: bar.date }))
  const asOf = '2026-04-21'
  const before = prepareChartReview(points, asOf)
  const triangle = before.priceAction.analysis.tradingRanges.find(
    (range) => range.id === 'range:triangle:133:152',
  )
  assert.equal(triangle.status, 'breakout-mode')
  assert.equal(triangle.failedBreakoutIndex, null)
  const later = prepareChartReview(points, '2026-04-24')
  assert.equal(
    later.priceAction.analysis.tradingRanges.find((range) => range.id === triangle.id).status,
    'failed-breakout',
  )
  const changed = points.map((bar) =>
    bar.day > asOf ? { ...bar, high: Infinity, low: -1, close: 5000 } : bar,
  )
  assert.deepEqual(prepareChartReview(changed, asOf), before)
  assert.deepEqual(
    prepareChartReview(
      points.filter((bar) => bar.day <= asOf),
      asOf,
    ),
    before,
  )
})

const bars = Array.from({ length: 100 }, () => ({ close: 95 }))
const range = {
  id: 'recent-range',
  kind: 'trading-range',
  status: 'confirmed',
  startIndex: 60,
  knownAtIndex: 90,
  statusKnownAtIndex: 90,
  endIndex: 99,
  breakoutIndex: null,
  score: 0.8,
  upperStartPrice: 100,
  upperEndPrice: 100,
  lowerStartPrice: 90,
  lowerEndPrice: 90,
}

test('only known, live ranges anchored within the current structure window can control the reading', () => {
  assert.equal(selectCurrentTradingRange([range], bars, 60), range)
  for (const patch of [
    { status: 'failed-breakout' },
    { status: 'broken' },
    { endIndex: 98 },
    { startIndex: 59 },
    { knownAtIndex: 100 },
    { statusKnownAtIndex: 100 },
    { upperEndPrice: 80 }, // triangle boundaries have crossed
  ]) {
    assert.equal(selectCurrentTradingRange([{ ...range, ...patch }], bars, 60), null)
  }
})

test('a new range already left by price cannot mask the trend; pending breakouts have a two-bar lifetime', () => {
  const outside = bars.map((bar) => ({ ...bar, close: 105 }))
  assert.equal(selectCurrentTradingRange([range], outside, 60), null)
  const pending = { ...range, status: 'breakout-mode', breakoutIndex: 97 }
  assert.equal(selectCurrentTradingRange([pending], outside, 60), pending)
  assert.equal(selectCurrentTradingRange([{ ...pending, breakoutIndex: 96 }], outside, 60), null)
})

test('grain sell-side control begins after confirmation and survives June consolidation', () => {
  const analyze = (day) =>
    engine.analyzePriceAction(
      fixture.bars.filter((bar) => bar.date <= day),
      [],
      dailyStructureOptions,
    )
  assert.equal(analyze('2026-03-19').alwaysIn.state, 'unclear')
  const first = analyze('2026-03-20')
  assert.equal(first.alwaysIn.state, 'short')
  assert.equal(first.alwaysIn.source, 'breakout')
  assert.equal(fixture.bars[first.alwaysIn.premiseKnownAtIndex].date, '2026-03-20')
  const may = analyze('2026-05-15')
  assert.equal(may.trend, 'range') // Completed swing geometry still lags.
  assert.equal(may.alwaysIn.state, 'short')
  assert.equal(may.alwaysIn.boundaryPrice, 1.354)
  const june = analyze('2026-06-16')
  assert.equal(june.activeTradingRange.kind, 'tight-trading-range')
  assert.equal(june.marketState.label, '下降趋势内整理')
  assert.equal(june.alwaysIn.state, 'short')
  assert.equal(analyze('2026-03-30').alwaysIn.state, 'unclear') // Rebound loses EMA alignment.
})

test('each day of the actual rally and selloff is causal and symmetric under price reflection', () => {
  const points = fixture.bars.map((bar) => ({ ...bar, day: bar.date }))
  const mirrored = fixture.bars.map((bar) => ({
    ...bar,
    open: 4 - bar.open,
    high: 4 - bar.low,
    low: 4 - bar.high,
    close: 4 - bar.close,
  }))
  const states = new Set()
  for (const [index, bar] of fixture.bars.entries()) {
    if (index < 19 || bar.date > '2026-06-16') continue
    const prefix = fixture.bars.slice(0, index + 1)
    const current = engine.analyzePriceAction(prefix, [], dailyStructureOptions)
    const mirror = engine.analyzePriceAction(mirrored.slice(0, index + 1), [], dailyStructureOptions)
    assert.equal(
      mirror.alwaysIn.state,
      { short: 'long', long: 'short', unclear: 'unclear' }[current.alwaysIn.state],
      bar.date,
    )
    assert.ok(current.alwaysIn.premiseKnownAtIndex == null || current.alwaysIn.premiseKnownAtIndex <= index)
    states.add(current.alwaysIn.state)
    const changed = points.map((point) =>
      point.day > bar.date ? { ...point, close: 5000, high: Infinity, low: -1 } : point,
    )
    const review = prepareChartReview(changed, bar.date)
    assert.deepEqual(review.priceAction.analysis.alwaysIn, current.alwaysIn, bar.date)
    assert.deepEqual(review.priceAction.analysis.marketState, current.marketState, bar.date)
  }
  assert.deepEqual([...states].sort(), ['long', 'short', 'unclear'])
})

test('actual rally recovers after the failed January breakout and survives the March window roll', () => {
  const analyze = (day) =>
    engine.analyzePriceAction(
      fixture.bars.filter((bar) => bar.date <= day),
      [],
      dailyStructureOptions,
    )
  for (const day of ['2025-11-17', '2026-01-12', '2026-02-27', '2026-03-12', '2026-03-13', '2026-03-16']) {
    assert.equal(analyze(day).alwaysIn.state, 'long', day)
  }
  assert.equal(analyze('2026-02-02').alwaysIn.state, 'unclear')
  assert.equal(analyze('2026-02-11').alwaysIn.state, 'unclear')
  const recovered = analyze('2026-02-27').alwaysIn
  assert.equal(recovered.source, 'swings')
  assert.equal(recovered.breakoutId, null)
  assert.equal(recovered.swingBoundaryPrice, 1.371)
  assert.equal(fixture.bars[recovered.premiseKnownAtIndex].date, '2026-02-26')
  const march12 = analyze('2026-03-12')
  const march16 = analyze('2026-03-16')
  assert.equal(march12.trend, 'up')
  assert.equal(march16.trend, 'range') // Legacy score rolls off; the current pairs are still rising.
  assert.equal(march16.marketState.label, '宽幅牛通道')
  assert.equal(march16.alwaysIn.swingBoundaryPrice, 1.46)
  assert.equal(march16.alwaysIn.premiseKnownAtIndex, march12.alwaysIn.premiseKnownAtIndex)
  assert.equal(fixture.bars[march16.alwaysIn.premiseKnownAtIndex].date, '2026-03-12')
  assert.equal(analyze('2026-03-17').alwaysIn.state, 'long')
  assert.equal(analyze('2026-03-18').alwaysIn.state, 'unclear')
})
