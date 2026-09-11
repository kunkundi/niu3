// Golden fixtures and assertions carried over from NiuTwo to verify migration parity.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { createPriceActionEngine } from '../src/price-action/engine.js'
import { drawPriceActionChart, drawPriceActionLayers } from '../src/price-action/renderer.js'
import {
  PA_OPTIONS,
  preparePriceAction,
  aggregateCompletedWeeks,
  etfEngine,
  layerItems,
} from '../src/price-action/index.js'
import { prepareCandles, candleWindow, DEFAULT_INDICATORS, loadIndicators } from '../src/candles.js'
const loadFixture = () =>
  JSON.parse(readFileSync(new URL('./fixtures/niutwo-price-action.json', import.meta.url), 'utf8'))
const { analyzePriceAction } = createPriceActionEngine(0.01)
function fixtureBars(fixture) {
  const start = Date.parse(`${fixture.startDate}T00:00:00Z`)
  return fixture.ohlcv.map(([open, high, low, close, volume], index) => ({
    date: new Date(start + index * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
    open,
    high,
    low,
    close,
    volume,
    closed: true,
  }))
}

function snapshotCanvasContext() {
  return {
    text: [],
    strokes: [],
    currentLineDash: [],
    lineDashCount: 0,
    saveCount: 0,
    arc() {},
    beginPath() {},
    clearRect() {},
    clip() {},
    closePath() {},
    fill() {},
    fillRect() {},
    fillText(value) {
      this.text.push(String(value))
    },
    lineTo() {},
    measureText(value) {
      return { width: String(value).length * 6 }
    },
    moveTo() {},
    quadraticCurveTo() {},
    rect() {},
    restore() {},
    save() {
      this.saveCount += 1
    },
    setLineDash(value) {
      this.lineDashCount += 1
      this.currentLineDash = value.slice()
    },
    stroke() {
      this.strokes.push(`${this.strokeStyle}|${this.lineWidth}|${this.currentLineDash.join(',')}`)
    },
    strokeRect() {},
  }
}

function countValues(values) {
  const counts = {}
  for (const value of values) counts[value] = (counts[value] || 0) + 1
  return counts
}

test('fixed OHLCV fixture preserves historical objects with corrected current range state', () => {
  const fixture = loadFixture()
  const bars = fixtureBars(fixture)
  const analysis = analyzePriceAction(bars, [], { period: fixture.period })
  const kindCounts = countValues(analysis.objects.map((object) => object.kind))
  const recentObjects = analysis.objects
    .slice()
    .sort((left, right) => right.knownAtIndex - left.knownAtIndex || right.score - left.score)
    .slice(0, 10)
    .map((object) => [
      object.id,
      object.kind,
      object.status,
      object.direction,
      object.startIndex,
      object.endIndex,
      object.knownAtIndex,
    ])

  assert.deepEqual(
    {
      trend: analysis.trend,
      marketState: analysis.marketState.state,
      alwaysIn: analysis.alwaysIn.state,
      kindCounts,
      recentObjects,
    },
    // The legacy snapshot selected a failed historical range. Current compression
    // is a valid tight range, while historical objects and Canvas remain unchanged.
    { ...fixture.expected.analysis, marketState: 'tight-trading-range' },
  )
  assert.equal(analysis.activeTradingRange.kind, 'tight-trading-range')
  assert.equal(analysis.activeTradingRange.endIndex, bars.length - 1)
  assert.equal(analysis.tradingRanges.length, 4)
  assert.ok(analysis.tradingRanges.every((range) => range.score > analysis.activeTradingRange.score))
  assert.ok(!analysis.tradingRanges.some((range) => range.id === analysis.activeTradingRange.id))
})

test('fixed OHLCV fixture preserves deterministic Canvas drawing commands', () => {
  const fixture = loadFixture()
  const bars = fixtureBars(fixture)
  const analysis = analyzePriceAction(bars, [], { period: fixture.period })
  const context = snapshotCanvasContext()
  drawPriceActionChart(context, bars, analysis, 640, 360, fixture.selectedIndex)

  assert.deepEqual(
    {
      text: context.text,
      strokeCounts: countValues(context.strokes),
      saveCount: context.saveCount,
      lineDashCount: context.lineDashCount,
    },
    fixture.expected.canvas,
  )
})

test('edge annotations stay complete and collision-free at both viewport and replay boundaries', () => {
  for (const width of [240, 760]) {
    for (const visibleCount of [10, 30, 120]) {
      for (const replay of [false, true]) {
        const count = replay ? Math.floor(visibleCount / 2) : visibleCount
        const offset = 40
        const bars = Array.from({ length: count }, () => ({ high: 110, low: 90 }))
        const chart = {
          width,
          height: 256,
          plotLeft: 0,
          plotRight: (width / visibleCount) * count,
          plotTop: 14,
          plotBottom: 240,
          priceMin: 0,
          priceMax: 200,
          barWidth: width / visibleCount,
          barOriginX: 0,
        }
        const event = (index, type) => ({
          index: index + offset,
          type,
          direction: 'ascending',
          price: 100,
          boundaryPrice: 100,
          lineRank: 'primary',
        })
        const analysis = {
          trendLines: [],
          channelLines: [],
          channelEvents: [
            event(0, 'trend-line-break'),
            event(count - 1, 'overshoot'),
            event(count - 1, 'break-and-test'),
          ],
        }
        const context = snapshotCanvasContext()
        const labels = []
        context.fillText = (text, x, y) => {
          const half = context.measureText(text).width / 2
          labels.push({ text, left: x - half, right: x + half, top: y - 5, bottom: y + 5 })
        }
        drawPriceActionLayers(context, bars, analysis, chart, 'light', offset, { trendChannels: true })
        assert.deepEqual(
          labels.map((label) => label.text),
          ['TLB', 'OS', 'B&T'],
        )
        for (const label of labels) {
          assert.ok(label.left >= chart.plotLeft && label.right <= chart.plotRight, JSON.stringify(label))
          assert.ok(label.top >= chart.plotTop && label.bottom <= chart.plotBottom, JSON.stringify(label))
        }
        const [overshoot, retest] = labels.slice(1)
        assert.ok(overshoot.bottom < retest.top || retest.bottom < overshoot.top)
      }
    }
  }
})

const candles = () => fixtureBars(loadFixture()).map((bar) => ({ ...bar, day: bar.date }))

test('NiuTwo layer inventory is fully represented without replacing existing indicators', () => {
  const sourceLayers = [
    'volume',
    'ema20',
    'gaps',
    'barClassifications',
    'tradingRanges',
    'measuredMoves',
    'trendPatterns',
    'reversalPatterns',
    'higherTimeframe',
    'support',
    'resistance',
    'trendLines',
    'trendChannels',
    'legs',
    'barCounts',
    'microChannels',
    'invalidation',
    'bos',
    'choch',
    'pinBar',
    'insideBar',
    'engulfing',
    'outsideBar',
    'swings',
  ]
  assert.deepEqual(['volume', 'ema20', ...PA_OPTIONS.map(({ id }) => id)].sort(), sourceLayers.sort())
  for (const key of [...sourceLayers, 'macd', 'boll', 'dc', 'ma60'])
    assert.equal(typeof DEFAULT_INDICATORS[key], 'boolean')
  const stored = loadIndicators({ getItem: () => '{"macd":false,"ma5":false,"boll":true}' })
  assert.equal(stored.macd, false)
  assert.equal(stored.ma5, false)
  assert.equal(stored.boll, true)
  assert.equal(stored.ema20, true)
  assert.equal(stored.trendChannels, false)
})

test('EMA20 uses NiuTwo SMA seed, unchanged historical values and restarts after missing close', () => {
  const input = candles()
  const output = prepareCandles(input).bars
  const expected = etfEngine.calculateExponentialMovingAverage(
    input.map((bar) => bar.close),
    20,
  )
  output.forEach((bar, index) => {
    if (expected[index] === null) assert.equal(bar.ema20, null)
    else assert.ok(Math.abs(bar.ema20 - expected[index]) < 1e-10)
  })
  assert.equal(candleWindow(output, 30)[0].ema20, output.at(-30).ema20)
  input[25].close = null
  const byDate = new Map(prepareCandles(input).bars.map((bar) => [bar.day, bar]))
  assert.equal(byDate.get(input[44].day).ema20, null)
  assert.ok(byDate.get(input[45].day).ema20 > 0)
})

test('ETF price classification uses 0.001 tick and preserves original stock tick as a separate engine', () => {
  const bars = [{ open: 1, high: 1.005, low: 0.998, close: 1.003, volume: 1000, date: '2026-09-07' }]
  assert.equal(etfEngine.calculateAtomicBarFeatures(bars)[0].direction, 'bullish')
  assert.equal(createPriceActionEngine(0.01).calculateAtomicBarFeatures(bars)[0].direction, 'neutral')
  assert.throws(() => createPriceActionEngine(0), /tick/)
})

test('weekly aggregation excludes both boundary weeks and preserves actual OHLC and volume', () => {
  const daily = []
  for (let index = 0; index < 180; index++) {
    const date = new Date(Date.UTC(2026, 0, 1 + index))
    if ([0, 6].includes(date.getUTCDay())) continue
    daily.push({
      date: date.toISOString().slice(0, 10),
      open: index + 10,
      high: index + 12,
      low: index + 9,
      close: index + 11,
      volume: 100,
    })
  }
  const weekly = aggregateCompletedWeeks(daily)
  assert.equal(weekly[0].date, '2026-01-09')
  assert.deepEqual(weekly[0], {
    date: '2026-01-09',
    open: 14,
    high: 20,
    low: 13,
    close: 19,
    volume: 500,
    closed: true,
  })
  assert.ok(weekly.at(-1).date < daily.at(-1).date)
  const result = preparePriceAction(daily.map((bar) => ({ ...bar, day: bar.date })))
  assert.equal(result.analysis.higherTimeframe.available, true)
  assert.equal(result.analysis.higherTimeframe.sourceDate, weekly.at(-1).date)
  assert.equal(result.weeklyCount, weekly.length)
})

test('short or malformed history reports unavailable structure, never a synthetic result', () => {
  assert.equal(preparePriceAction(candles().slice(0, 19)).analysis, null)
  assert.equal(preparePriceAction(candles(), 1).analysis, null)
  const result = preparePriceAction(candles().slice(0, 30))
  assert.equal(result.analysis.higherTimeframe.available, false)
  assert.match(result.analysis.higherTimeframe.warning, /至少需要 20/)
})

test('historical analysis only uses its supplied prefix and records confirmation dates within it', () => {
  const input = candles()
  const prefix = input.slice(0, 55)
  const historical = preparePriceAction(prefix)
  const expected = etfEngine.analyzePriceAction(historical.bars, [], { period: 'day' })
  assert.deepEqual(historical.analysis.objects, expected.objects)
  assert.ok(historical.analysis.objects.every((item) => item.knownAtIndex < prefix.length))
  assert.ok(
    historical.analysis.swings.every(
      (item) => item.confirmedIndex === item.index + 3 && item.confirmedIndex < prefix.length,
    ),
  )
  assert.equal(
    layerItems(historical.analysis, 'bos').length,
    historical.analysis.breakouts.filter((item) => item.structureLabel === 'BOS').length,
  )
})

test('each overlay can be drawn independently with finite coordinates; clearing layers leaves no drawing', () => {
  const { bars, analysis } = preparePriceAction(candles())
  const min = Math.min(...bars.map((bar) => bar.low)) - 1
  const max = Math.max(...bars.map((bar) => bar.high)) + 1
  const geometry = {
    width: 320,
    height: 256,
    plotLeft: 0,
    plotRight: 320,
    plotTop: 14,
    plotBottom: 240,
    priceMin: min,
    priceMax: max,
    barWidth: 320 / bars.length,
    barOriginX: 0,
  }
  let drawn = 0
  for (const layer of PA_OPTIONS) {
    const context = snapshotCanvasContext()
    for (const method of ['arc', 'moveTo', 'lineTo', 'rect', 'fillRect', 'strokeRect']) {
      context[method] = (...values) =>
        assert.ok(values.every(Number.isFinite), `${layer.id}: ${method} must be finite`)
    }
    drawPriceActionLayers(context, bars, analysis, geometry, 'light', 0, { [layer.id]: true })
    if (context.strokes.length || context.text.length) drawn++
  }
  assert.ok(drawn >= 15, `Fixture must exercise actual drawing for most layers, got ${drawn}`)
  const blank = snapshotCanvasContext()
  drawPriceActionLayers(blank, bars, analysis, geometry, 'dark', 0, {})
  assert.deepEqual(blank.strokes, [])
  assert.deepEqual(blank.text, [])
  assert.equal(blank.saveCount, 0)
})
