import test from 'node:test'
import assert from 'node:assert/strict'
import {
  prepareCandles,
  candleWindow,
  loadIndicators,
  saveIndicators,
  DEFAULT_INDICATORS,
} from '../src/candles.js'

const bar = (overrides = {}) => ({
  day: '2026-09-07',
  open: '1.010',
  high: '1.060',
  low: '0.980',
  close: '1.040',
  volume: 1200000,
  amount: '1234000',
  ...overrides,
})
const history = (count) =>
  Array.from({ length: count }, (_, index) =>
    bar({
      day: new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10),
      open: index + 1,
      close: index + 1,
      high: index + 2,
      low: index + 0.5,
    }),
  )

test('candles preserve actual OHLC, share volume and chronological order', () => {
  const result = prepareCandles([bar(), bar({ day: '2026-09-04', open: 1.03, close: 1 })])
  assert.deepEqual(
    result.bars.map((row) => row.day),
    ['2026-09-04', '2026-09-07'],
  )
  assert.equal(result.bars[1].open, 1.01)
  assert.equal(result.bars[1].close, 1.04)
  assert.equal(result.bars[1].volume, 1200000)
  assert.ok(Math.abs(result.bars[1].change - 0.04) < 1e-10)
  assert.equal(result.bars[0].change, null)
})

test('close-only, nonfinite and inconsistent OHLC records never become fake candles', () => {
  for (const invalid of [
    { open: undefined, high: undefined, low: undefined },
    { open: '0', high: '0', low: '0' },
    { high: 1.02 },
    { low: 1.02 },
    { close: null },
    { close: Infinity },
    { open: '' },
    { low: -1 },
  ]) {
    const result = prepareCandles([bar(invalid)])
    assert.equal(result.bars.length, 0)
    assert.equal(result.omitted, 1)
  }
})

test('MA values use the full series before slicing the visible range', () => {
  const { bars } = prepareCandles(history(80))
  const visible = candleWindow(bars, 60)
  assert.equal(visible.length, 60)
  assert.equal(visible[0].close, 21)
  assert.equal(visible[0].ma[5], 19)
  assert.equal(visible[0].ma[10], 16.5)
  assert.equal(visible[0].ma[20], 11.5)
  assert.equal(bars[3].ma[5], null)
  assert.equal(bars[4].ma[5], 3)
  assert.equal(bars[18].ma[20], null)
})

test('missing close interrupts MA calculations rather than shifting the period', () => {
  const points = history(25)
  points[10].close = null
  const { bars, omitted } = prepareCandles(points)
  assert.equal(omitted, 1)
  assert.equal(bars.at(-1).ma[20], null)
  assert.equal(bars.at(-1).ma[5], 23)
})

test('missing volume and amount remain unavailable while zero values remain valid', () => {
  for (const value of [undefined, null, '', -1, Infinity]) {
    const result = prepareCandles([bar({ volume: value, amount: value })]).bars[0]
    assert.equal(result.volume, null)
    assert.equal(result.amount, null)
  }
  const result = prepareCandles([bar({ volume: 0, amount: 0 })]).bars[0]
  assert.equal(result.volume, 0)
  assert.equal(result.amount, 0)
})

test('date validation, deduplication, flat candles and history windows are deterministic', () => {
  const { bars } = prepareCandles([
    bar({ day: '2026-02-30' }),
    bar({ day: 'bad-date' }),
    bar(),
    bar({ open: 1, high: 1, low: 1, close: 1 }),
  ])
  assert.equal(bars.length, 1)
  assert.equal(bars[0].high, 1)
  const rows = prepareCandles(history(80)).bars
  assert.equal(candleWindow(rows, 30, 45)[0].close, 16)
  assert.equal(candleWindow(rows, 30, 45).at(-1).close, 45)
  assert.equal(candleWindow(rows, 120).length, 80)
  assert.equal(candleWindow(rows, 30, 100).at(-1).close, 80)
  assert.deepEqual(candleWindow([], 60), [])
})

const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-10, `${actual} != ${expected}`)

test('BOLL uses a 20-close population deviation and DC includes the current high and low', () => {
  const { bars } = prepareCandles(history(21))
  assert.equal(bars[18].boll, null)
  assert.equal(bars[18].dc, null)
  near(bars[19].boll.middle, 10.5)
  near(bars[19].boll.upper, 10.5 + 2 * Math.sqrt(33.25))
  near(bars[19].boll.lower, 10.5 - 2 * Math.sqrt(33.25))
  assert.deepEqual(bars[19].dc, { upper: 21, middle: 10.75, lower: 0.5 })
  assert.deepEqual(bars[20].dc, { upper: 22, middle: 11.75, lower: 1.5 })
})

test('MACD has 26-close warmup, standard EMA weights and doubled Chinese-market histogram', () => {
  const { bars } = prepareCandles(history(60))
  assert.equal(bars[24].macd, null)
  // Independent closed-form EMA for a linear series 1..n.
  const expectedDif = (n) => 7 + 5.5 * (11 / 13) ** (n - 1) - 12.5 * (25 / 27) ** (n - 1)
  const expectedDea = (n) =>
    7 * (1 - 0.8 ** n) +
    0.2 *
      ((5.5 * (0.8 ** n - (11 / 13) ** n)) / (0.8 - 11 / 13) -
        (12.5 * (0.8 ** n - (25 / 27) ** n)) / (0.8 - 25 / 27))
  for (const n of [26, 40, 60]) {
    near(bars[n - 1].macd.dif, expectedDif(n))
    near(bars[n - 1].macd.dea, expectedDea(n))
    near(bars[n - 1].macd.histogram, 2 * (expectedDif(n) - expectedDea(n)))
    assert.ok(bars[n - 1].macd.histogram > 0)
  }
  assert.equal(bars[58].ma[60], null)
  assert.equal(bars[59].ma[60], 30.5)
})

test('MACD correctly handles falling and flat series and channels collapse for flat prices', () => {
  const falling = history(60).map((point, index) => ({
    ...point,
    open: 100 - index,
    close: 100 - index,
    high: 101 - index,
    low: 99 - index,
  }))
  const last = prepareCandles(falling).bars.at(-1)
  assert.ok(last.macd.dif < 0 && last.macd.dea < 0 && last.macd.histogram < 0)
  const flat = history(60).map((point) => ({ ...point, open: 2, close: 2, high: 2, low: 2 }))
  const result = prepareCandles(flat).bars.at(-1)
  assert.deepEqual(result.macd, { dif: 0, dea: 0, histogram: 0 })
  assert.deepEqual(result.boll, { upper: 2, middle: 2, lower: 2 })
  assert.deepEqual(result.dc, { upper: 2, middle: 2, lower: 2 })
})

test('missing closes restart MACD warmup and invalidate BOLL until its window is complete', () => {
  const points = history(90)
  points[40].close = null
  const byDay = new Map(prepareCandles(points).bars.map((point) => [point.day, point]))
  assert.equal(byDay.get(points[59].day).boll, null)
  assert.ok(byDay.get(points[60].day).boll)
  assert.equal(byDay.get(points[65].day).macd, null)
  assert.ok(byDay.get(points[66].day).macd)
  assert.deepEqual(byDay.get(points[66].day).macd, prepareCandles(points.slice(41)).bars[25].macd)
})

test('DC rejects missing or inconsistent high/low while close-based indicators remain usable', () => {
  for (const high of [null, 1]) {
    const points = history(60)
    points[35].high = high
    const { bars } = prepareCandles(points)
    assert.equal(bars.find((point) => point.day === points[54].day).dc, null)
    assert.ok(bars.find((point) => point.day === points[54].day).boll)
    assert.ok(bars.find((point) => point.day === points[54].day).macd)
    assert.ok(bars.find((point) => point.day === points[55].day).dc)
  }
})

test('all indicators use only prior data and remain unchanged when the display range changes', () => {
  const points = history(100)
  const all = prepareCandles(points).bars
  const prefix = prepareCandles(points.slice(0, 70)).bars
  assert.deepEqual(all.slice(0, 70), prefix)
  assert.deepEqual(candleWindow(all, 30, 70).at(-1), prefix.at(-1))
  assert.deepEqual(candleWindow(all, 60, 70).at(-1), prefix.at(-1))
})

test('indicator preferences retain explicit deselection and survive damaged or unavailable storage', () => {
  let value
  const storage = {
    getItem: () => value,
    setItem: (key, next) => {
      value = next
    },
  }
  const selected = { ...DEFAULT_INDICATORS, ma5: false, macd: false, volume: false, boll: true }
  saveIndicators(storage, selected)
  assert.deepEqual(loadIndicators(storage), selected)
  value = '{"ma5":"false","macd":false,"unknown":true}'
  assert.deepEqual(loadIndicators(storage), { ...DEFAULT_INDICATORS, macd: false })
  for (value of ['broken', 'null', '[]', '3']) assert.deepEqual(loadIndicators(storage), DEFAULT_INDICATORS)
  assert.deepEqual(loadIndicators(undefined), DEFAULT_INDICATORS)
  assert.doesNotThrow(() => saveIndicators(undefined, selected))
})

test('legacy default charts upgrade once while custom charts retain their selections', () => {
  const legacyDefaults = {
    market: { ma5: true, ma10: true, ma20: true, volume: true, macd: true },
    strategy: {
      ma5: false,
      ma10: false,
      ma20: false,
      ema20: true,
      volume: true,
      macd: false,
      swings: true,
      bos: true,
      choch: true,
      pinBar: true,
      engulfing: true,
    },
  }
  for (const [preset, legacy] of Object.entries(legacyDefaults)) {
    const prefix = `niuno3.kline.${preset === 'strategy' ? 'strategy-indicators' : 'indicators'}`
    const saved = new Map([[`${prefix}.v1`, JSON.stringify(legacy)]])
    const storage = { getItem: (key) => saved.get(key), setItem: (key, value) => saved.set(key, value) }
    assert.deepEqual(loadIndicators(storage, preset), DEFAULT_INDICATORS)
    assert.deepEqual(JSON.parse(saved.get(`${prefix}.v2`)), DEFAULT_INDICATORS)
    assert.deepEqual(JSON.parse(saved.get(`${prefix}.v1`)), legacy)

    // An explicit choice saved after upgrading must win over the old preferences.
    const custom = { ...DEFAULT_INDICATORS, ema20: false, ma60: false, macd: true, support: false }
    saveIndicators(storage, custom, preset)
    assert.deepEqual(loadIndicators(storage, preset), custom)

    // Old custom charts keep both explicit deselections and omitted legacy defaults.
    saved.delete(`${prefix}.v2`)
    saved.set(`${prefix}.v1`, JSON.stringify({ ...legacy, volume: false, boll: true }))
    const migrated = loadIndicators(storage, preset)
    assert.equal(migrated.volume, false)
    assert.equal(migrated.boll, true)
    assert.equal(migrated.ma60, false)
    assert.equal(migrated.support, false)
    assert.equal(migrated.ema20, preset === 'strategy')
    assert.equal(migrated.bos, preset === 'strategy')
    assert.deepEqual(loadIndicators(storage, preset), migrated)
  }
})
