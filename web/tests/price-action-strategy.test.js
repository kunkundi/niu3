import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { priceActionLevels } from '../src/price-action/strategy.js'
import { preparePriceAction } from '../src/price-action/index.js'

function history() {
  const bars = Array.from({ length: 140 }, (_, i) => {
    const p = 1 + i * 0.002 + Math.sin(i * 0.6) * 0.025
    return {
      date: new Date(Date.UTC(2026, 0, 1 + i)).toISOString().slice(0, 10),
      open: p - 0.004,
      close: p + 0.004,
      high: p + 0.012,
      low: p - 0.012,
      volume: 1000000,
      closed: true,
    }
  })
  Object.assign(bars.at(-1), { open: 1.3, close: 1.305, high: 1.31, low: 1.24 })
  return bars
}

test('chart Pin Bar yields a conditional breakout above high and a structural stop below low', () => {
  const bars = history(),
    result = priceActionLevels(bars, bars.at(-1).date)
  assert.equal(result.setup, '看涨 Pin Bar')
  assert.equal(result.entry, 1.311)
  assert.equal(result.entry_stop, 1.239)
  assert.equal(result.signal_day, bars.at(-1).date)
  assert.ok(result.evidence.swing.confirmedIndex <= bars.length - 1)
  assert.ok(result.target > result.entry && result.entry_ceiling < result.target)
})

test('future bars and an unfinished bar cannot alter a historical decision', () => {
  const bars = history(),
    asOf = bars.at(-1).date
  const expected = priceActionLevels(bars, asOf)
  const future = { ...bars.at(-1), date: '2026-05-21', close: 9, high: 10 }
  assert.deepEqual(priceActionLevels([...bars, future], asOf), expected)
  assert.deepEqual(priceActionLevels([...bars, { ...future, date: asOf, closed: false }], asOf), expected)
})

test('continuation remains research-only and cannot use future candles', () => {
  const bars = Array.from({ length: 140 }, (_, i) => ({
    date: new Date(Date.UTC(2026, 0, 1 + i)).toISOString().slice(0, 10),
    open: 1,
    close: 1,
    high: 1.02,
    low: 0.98,
    closed: true,
  }))
  Object.assign(bars.at(-3), { open: 1.02, close: 1.022, high: 1.04, low: 1.01 })
  Object.assign(bars.at(-2), { open: 1.017, close: 1.024, high: 1.03, low: 1.015 })
  Object.assign(bars.at(-1), { open: 1.023, close: 1.026, high: 1.028, low: 1.019 })
  const asOf = bars.at(-1).date
  assert.equal(priceActionLevels(bars, asOf).entry, null)
  const researched = priceActionLevels(bars, asOf, 0.001, { continuation: true })
  assert.equal(researched.setup, '上行延续突破')
  assert.equal(researched.entry, 1.031)
  assert.equal(researched.entry_stop, 1.018)
  assert.deepEqual(
    priceActionLevels([...bars, { date: '2099-01-01', high: Infinity }], asOf, 0.001, {
      continuation: true,
    }),
    researched,
  )
})

test('current direction is an explicit research option and remains causal; the live default stays legacy', () => {
  const { bars } = JSON.parse(
    readFileSync(new URL('./fixtures/sz159587-current-state.json', import.meta.url), 'utf8'),
  )
  const asOf = '2026-03-16'
  const prefix = bars.filter((bar) => bar.date <= asOf)
  const live = priceActionLevels(prefix, asOf)
  assert.deepEqual(live, priceActionLevels(prefix, asOf, 0.001, { directionMode: 'legacy' }))
  assert.equal(live.direction_policy, undefined)
  const researched = priceActionLevels(prefix, asOf, 0.001, { directionMode: 'current' })
  assert.equal(researched.direction_policy, 'current-direction-v1')
  assert.equal(researched.direction.state, 'long')
  assert.equal(researched.direction.swingBoundaryPrice, 1.46)
  assert.deepEqual(
    researched,
    priceActionLevels(
      bars.map((bar) => (bar.date > asOf ? { ...bar, high: Infinity, close: 999 } : bar)),
      asOf,
      0.001,
      { directionMode: 'current' },
    ),
  )
})

test('missing, malformed, duplicate or stale OHLC never yields a trade setup', () => {
  const bars = history(),
    asOf = bars.at(-1).date
  assert.equal(priceActionLevels(bars.slice(-119), asOf).ready, false)
  assert.equal(priceActionLevels([...bars, bars.at(-1)], asOf).ready, false)
  assert.equal(priceActionLevels(bars.slice(0, -1), asOf).ready, false)
  for (const value of [0, NaN, Infinity, 2]) {
    const bad = structuredClone(bars)
    bad.at(-1).low = value
    assert.equal(priceActionLevels(bad, asOf).ready, false)
  }
})

test('a failed bullish signal is not reused, and bearish engulfing supplies an exit boundary', () => {
  const bars = history()
  bars.push({ date: '2026-05-21', open: 1.31, high: 1.315, low: 1.2, close: 1.21, volume: 1000000 })
  const result = priceActionLevels(bars, '2026-05-21')
  assert.notEqual(result.signal_day, '2026-05-20')
  assert.ok(result.exit < 1.2)
  assert.ok(result.exit_setup)
})

test('a previously confirmed buy setup expires after a later close invalidates it', () => {
  const bars = history()
  bars.push({ date: '2026-05-21', open: 1.312, close: 1.32, high: 1.325, low: 1.3, closed: true })
  bars.push({ date: '2026-05-22', open: 1.319, close: 1.23, high: 1.324, low: 1.22, closed: true })
  const legacy = priceActionLevels(bars, '2026-05-22', 0.001, { entryPolicy: 'legacy' })
  const fixed = priceActionLevels(bars, '2026-05-22')
  assert.equal(legacy.signal_day, '2026-05-20')
  assert.equal(fixed.entry, null)
  assert.match(fixed.entry_rejections[0].reason, /跌破失效线/)
  assert.equal(fixed.exit, legacy.exit)
  assert.equal(fixed.structural_stop, legacy.structural_stop)
  assert.deepEqual(fixed, priceActionLevels([...bars, { date: '2099-01-01', close: 999 }], '2026-05-22'))
})

test('strategy and chart share the recent 60-bar structure while retaining longer background levels', () => {
  const bars = history()
  const chart = preparePriceAction(
    bars.map((b) => ({ ...b, day: b.date })),
    0,
    'strategy',
  ).analysis
  const result = priceActionLevels(bars, bars.at(-1).date)
  assert.equal(result.history_bars, 140)
  assert.equal(result.history_limit, 250)
  assert.equal(result.structure_start, bars.at(-60).date)
  assert.equal(chart.structureStartIndex, 80)
  assert.equal(result.trend, chart.trendLabel)
  assert.equal(result.support, chart.levels.find((s) => s.type === 'support')?.price ?? null)
  assert.equal(result.resistance, chart.levels.find((s) => s.type === 'resistance')?.price ?? null)
  assert.ok(chart.recentSwings.every((s) => s.index >= 80 && s.confirmedIndex < 140))
  assert.ok(chart.levels.every((s) => s.lastTouchedIndex >= 80))

  // Old low prices remain visible as background; they cannot provide a current swing stop or T band.
  const flat = bars.map((b, i) => (i < 80 ? b : { ...b, open: 2, close: 2.001, high: 2.01, low: 1.99 }))
  const quiet = priceActionLevels(flat, flat.at(-1).date)
  assert.equal(quiet.trend, '震荡结构')
  assert.equal(quiet.structural_stop, null)
  assert.equal(quiet.support, null)
  assert.equal(quiet.resistance, 2.01)
  assert.equal(quiet.t_allowed, false)
  assert.ok(quiet.background.support > 0)
})

test('background is capped at 250 completed bars independently of the eligibility minimum', () => {
  const recent = history()
  const first = Date.parse(recent[0].date)
  const older = Array.from({ length: 160 }, (_, i) => ({
    ...recent[0],
    date: new Date(first - (160 - i) * 86400000).toISOString().slice(0, 10),
  }))
  const bars = [...older, ...recent]
  const result = priceActionLevels(bars, bars.at(-1).date)
  assert.equal(result.history_bars, 250)
  assert.deepEqual(result, priceActionLevels(bars.slice(-250), bars.at(-1).date))
  assert.equal(result.background.start, bars.at(-250).date)
  const withOldResistance = structuredClone(bars)
  for (const i of [60, 80])
    Object.assign(withOldResistance[i], { open: 1.36, close: 1.35, high: 1.4, low: 1.34 })
  const capped = priceActionLevels(withOldResistance, bars.at(-1).date)
  assert.equal(capped.resistance, result.resistance)
  assert.equal(capped.target, 1.4)
  assert.ok(capped.target < result.target)
  assert.ok(capped.evidence.target.lastTouchedIndex < 190)
  assert.equal(priceActionLevels(recent, recent.at(-1).date).ready, true)
  assert.equal(priceActionLevels(recent, recent.at(-1).date, 0.001, { minimumBars: 250 }).ready, false)
})
