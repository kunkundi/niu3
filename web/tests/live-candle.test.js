import test from 'node:test'
import assert from 'node:assert/strict'
import { mergeLiveCandle } from '../src/live-candle.js'
import { prepareCandles } from '../src/candles.js'
import { prepareChartReview } from '../src/chart-review.js'

const points = Array.from({ length: 40 }, (_, i) => ({
  day: new Date(Date.UTC(2026, 7, i + 1)).toISOString().slice(0, 10),
  open: 1,
  high: 1.1,
  low: 0.9,
  close: 1,
  volume: 100,
}))
const live = {
  day: '2026-09-10',
  open: 1.02,
  high: 1.2,
  low: 0.99,
  close: 1.1,
  volume: 50,
  basis_day: '2026-09-09',
  basis_close: 1,
  at: '2026-09-10T10:00:00+08:00',
  stale: false,
}

test('live daily candle and indicators update without changing completed candles or structure', () => {
  const before = structuredClone(points)
  const merged = mergeLiveCandle(points, live)
  const completed = prepareCandles(points)
  const display = prepareCandles(merged)
  assert.equal(display.bars.length, 41)
  assert.equal(display.bars.at(-1).live, true)
  assert.equal(display.bars.at(-1).open, 1.02)
  assert.equal(display.bars.at(-1).volume, 50)
  assert.equal(display.bars.at(-1).ma[5], 1.02)
  assert.deepEqual(display.bars.slice(0, -1), completed.bars)
  assert.deepEqual(points, before)
  assert.deepEqual(prepareChartReview(merged, '2026-09-09'), prepareChartReview(points, '2026-09-09'))
  const updated = prepareCandles(mergeLiveCandle(points, { ...live, close: 1.15, volume: 80 }))
  assert.equal(updated.bars.at(-1).close, 1.15)
  assert.equal(updated.bars.at(-1).volume, 80)
})

test('completed bars replace the live day and mismatched bases or invalid OHLC never append', () => {
  for (const value of [
    null,
    { ...live, day: '2026-09-09' },
    { ...live, basis_day: '2026-09-08' },
    { ...live, basis_close: 2 },
    { ...live, basis_close: null },
    { ...live, open: 0 },
    { ...live, high: 1.05 },
  ]) {
    assert.equal(mergeLiveCandle(points, value), points)
  }
  const settled = [...points, { ...live, close: 1.08 }]
  assert.equal(mergeLiveCandle(settled, live), settled)
})
