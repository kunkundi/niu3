import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { intradayReference, intradayGeometry, nearbyIntradayLevels } from '../src/intraday-levels.js'
import { prepareCandles } from '../src/candles.js'
import { preparePriceAction } from '../src/price-action/index.js'

const minute = () => ({
  symbol: 'sh510300',
  day: '2026-09-07',
  expected_day: '2026-09-07',
  previous_close: '1.000',
  points: [
    { minute: 0, time: '09:30', price: '1.001' },
    { minute: 30, time: '10:00', price: '1.004' },
  ],
})
const reference = () => ({
  symbol: 'sh510300',
  as_of: '2026-09-04',
  matched: true,
  row: { pa: { ready: true, entry: 1.311, target: 1.451, raw: { entry: 1005000 } } },
  intraday_reference: {
    session_day: '2026-09-07',
    levels: {
      entry: 1.005,
      entry_stop: 0.949,
      support: 0.944,
      resistance: 1.01,
      target: 1.111,
      structural_stop: 0.941,
    },
  },
})

test('intraday ETF labels use execution-converted yuan, not qfq values or stored millionths', () => {
  const result = intradayReference(reference(), minute())
  assert.equal(result.levels.length, 6)
  assert.equal(result.levels.find((l) => l.id === 'entry').value, 1.005)
  assert.equal(result.levels.find((l) => l.id === 'support').value, 0.944)
  assert.match(result.message, /2026-09-04.*交易价/)
  const bad = reference()
  bad.intraday_reference.levels = { entry: null, support: 0, resistance: Infinity, target: -1 }
  assert.deepEqual(intradayReference(bad, minute()).levels, [])
})

test('intraday overlays reject mismatched symbols, old sessions, unconfirmed daily bars and unavailable conversion', () => {
  for (const change of [
    { symbol: 'sh510500' },
    { matched: false },
    { row: { pa: { ready: false } } },
    { as_of: '2026-09-07' },
    { as_of: '2026-09-08' },
    { intraday_reference: undefined },
    { intraday_reference: { session_day: '2026-09-04', levels: { entry: 1.005 } } },
  ])
    assert.deepEqual(intradayReference({ ...reference(), ...change }, minute()).levels, [])
  assert.deepEqual(intradayReference(reference(), { ...minute(), day: '2026-09-04' }).levels, [])
  assert.deepEqual(intradayReference(reference(), { ...minute(), expected_day: null }).levels, [])
  assert.deepEqual(intradayReference(reference(), { ...minute(), points: [] }).levels, [])
  assert.deepEqual(intradayReference(null, minute()), { levels: [], message: '' })
  assert.equal(intradayReference(reference(), { ...minute(), stale: true }).levels.length, 6)
})

test('only nearby levels expand the minute axis; distant targets cannot flatten the line', () => {
  const levels = intradayReference(reference(), minute()).levels
  const enabled = intradayGeometry(minute(), levels, 260)
  const hidden = intradayGeometry(minute())
  assert.ok(enabled.high < 1.01 && enabled.low > 0.99)
  assert.ok(hidden.high - hidden.low < enabled.high - enabled.low)
  assert.equal(enabled.plotted.at(-1).x, 75) // 10:00 is 1/8 of the full trading session.
  assert.deepEqual(
    enabled.references.map((line) => line.id),
    ['entry'],
  )
  const distant = levels.filter((line) => line.id !== 'entry')
  assert.deepEqual(intradayGeometry(minute(), distant), hidden)
  for (const line of enabled.references) {
    const expected = 210 - ((line.value - enabled.low) / (enabled.high - enabled.low)) * 200
    assert.ok(Math.abs(line.y - expected) < 1e-9)
    assert.ok(line.y >= 10 && line.y <= 210)
  }
  const atLevel = intradayGeometry({ ...minute(), points: [{ minute: 120, price: 1.005 }] }, levels)
  assert.ok(Math.abs(atLevel.plotted[0].y - atLevel.references.find((l) => l.id === 'entry').y) < 1e-9)
})

test('nearby levels follow the latest price, keeping at most one distinct level above and below', () => {
  const levels = [0.8, 0.999, 1.002, 1.003, 1.005, 1.005, 1.006, 1.008, 2].map((value, i) => ({
    id: String(i),
    value,
  }))
  const original = structuredClone(levels)
  assert.deepEqual(
    nearbyIntradayLevels(minute(), levels).map((l) => l.value),
    [1.003, 1.005],
  )
  const moved = { ...minute(), points: [...minute().points, { minute: 31, price: '1.006' }] }
  assert.deepEqual(
    nearbyIntradayLevels(moved, levels).map((l) => l.value),
    [1.006, 1.008],
  )
  assert.deepEqual(
    nearbyIntradayLevels(minute(), [{ value: 1.005 }, { value: 1.005 }]).map((l) => l.value),
    [1.005],
  )
  assert.deepEqual(levels, original)
  assert.deepEqual(nearbyIntradayLevels({ ...minute(), points: [] }, levels), [])
  assert.deepEqual(nearbyIntradayLevels({ ...minute(), previous_close: null }, levels), [])
})

test('nearby selection adapts to observed volatility and works in index point units', () => {
  const index = {
    ...minute(),
    previous_close: 4000,
    points: [
      { minute: 0, price: 3990 },
      { minute: 30, price: 4010 },
    ],
  }
  const levels = [3800, 3980, 4005, 4015, 4030, 4200].map((value) => ({ value }))
  assert.deepEqual(
    nearbyIntradayLevels(index, levels).map((l) => l.value),
    [4005, 4015],
  )
  const flat = { ...minute(), points: [{ minute: 0, price: 1 }] }
  assert.deepEqual(nearbyIntradayLevels(flat, [{ value: 0.9 }, { value: 1.1 }]), [])
  const volatile = {
    ...minute(),
    points: [
      { minute: 0, price: 1 },
      { minute: 30, price: 1.1 },
    ],
  }
  assert.deepEqual(
    nearbyIntradayLevels(volatile, [{ value: 1.11 }, { value: 1.15 }]).map((l) => l.value),
    [1.11],
  )
})

test('dense price labels stay inside the plot and remain separated at desktop and mobile heights', () => {
  const levels = Array.from({ length: 6 }, (_, i) => ({ id: String(i), value: 1 + i / 100000 }))
  const data = { ...minute(), points: [{ minute: 30, price: 1.000025 }] }
  for (const height of [190, 260, 560]) {
    const chart = intradayGeometry(data, levels, height)
    assert.equal(chart.references.length, 2)
    chart.references.forEach((line, i) => {
      assert.ok(line.labelY >= 10 && line.labelY <= 210)
      if (i) assert.ok(((line.labelY - chart.references[i - 1].labelY) * height) / 220 >= 19.999)
    })
  }
})

test('index support and resistance reuse the daily engine, exclude this session, and require matching previous close', () => {
  const fixture = JSON.parse(readFileSync(new URL('./fixtures/niutwo-price-action.json', import.meta.url)))
  const start = Date.parse(`${fixture.startDate}T00:00:00Z`)
  const bars = fixture.ohlcv.map(([open, high, low, close, volume], i) => ({
    day: new Date(start + i * 86400000).toISOString().slice(0, 10),
    open,
    high,
    low,
    close,
    volume,
  }))
  const day = new Date(start + bars.length * 86400000).toISOString().slice(0, 10)
  const data = { ...minute(), symbol: 'sh000001', day, expected_day: day, previous_close: bars.at(-1).close }
  const input = { symbol: 'sh000001', bars }
  const result = intradayReference(input, data)
  const analysis = preparePriceAction(prepareCandles(bars).bars, 0, 'market', 0.01).analysis
  assert.ok(result.levels.length > 0)
  assert.ok(result.levels.every((level) => analysis.levels.some((l) => l.price === level.value)))
  assert.match(result.message, /指数支撑压力/)
  const future = { ...bars.at(-1), day, high: 9999, close: 9000 }
  assert.deepEqual(intradayReference({ ...input, bars: [...bars, future] }, data), result)
  assert.deepEqual(intradayReference(input, { ...data, previous_close: 0 }).levels, [])
  assert.deepEqual(
    intradayReference(input, { ...data, previous_close: Number(data.previous_close) + 1 }).levels,
    [],
  )
  assert.deepEqual(intradayReference({ ...input, bars: bars.slice(-5) }, data).levels, [])
})
