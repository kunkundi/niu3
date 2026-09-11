import assert from 'node:assert/strict'
import test from 'node:test'
import { layoutBuyMarkers, prepareChartReview, reviewMarkersAsOf } from '../src/chart-review.js'
import { prepareCandles } from '../src/candles.js'
import { preparePriceAction, PA_OPTIONS, layerItems } from '../src/price-action/index.js'
import { priceActionLevels } from '../src/price-action/strategy.js'
import { candleIndexAt, candlePlotLayout } from '../src/chart-layout.js'

test('mobile candle bodies keep readable pixel widths and remain within their date slots', () => {
  for (const width of [230, 299, 339, 610]) {
    const closeUp = candlePlotLayout(width, 30)
    assert.ok(closeUp.bodyWidth >= 4 && closeUp.bodyWidth <= 10)
    for (const count of [10, 30, 60, 120, 250]) {
      const layout = candlePlotLayout(width, count)
      // Match the SVG viewBox conversion used by the chart, then its CSS scale.
      const svgBody = (layout.bodyWidth / width) * 760
      assert.ok(Math.abs((svgBody / 760) * width - layout.bodyWidth) < 1e-9)
      assert.ok(layout.bodyWidth < layout.barWidth)
      for (let index = 0; index < count; index++) {
        const center = layout.left + (index + 0.5) * layout.barWidth
        assert.equal(candleIndexAt(center - layout.bodyWidth / 2, width, count), index)
        assert.equal(candleIndexAt(center + layout.bodyWidth / 2, width, count), index)
        assert.ok(center - layout.bodyWidth / 2 >= layout.left)
        assert.ok(center + layout.bodyWidth / 2 <= layout.right)
      }
    }
  }
})

test('edge triggers stay centered on their candles at every range and viewport size', () => {
  for (const width of [148, 218, 694, 1400]) {
    for (const count of [10, 30, 60, 120, 250]) {
      const layout = candlePlotLayout(width, count)
      const markers = Array.from({ length: Math.min(10, count) }, (_, index) => ({
        id: index,
        x: layout.left + (count - Math.min(10, count) + index + 0.5) * layout.barWidth,
        y: 120,
      }))
      const placed = layoutBuyMarkers(markers, width, 320)
      assert.equal(placed.length, markers.length)
      const latest = placed.at(-1)
      assert.ok(Math.abs(latest.left + 8 - latest.x) < 1e-9, `${width}px / ${count} bars`)
      assert.ok(latest.left + 16 <= width - 2)
      for (let index = 0; index < count; index++) {
        const x = layout.left + (index + 0.5) * layout.barWidth
        assert.equal(candleIndexAt(x, width, count), index)
      }
      assert.equal(candleIndexAt(0, width, count), 0)
      assert.equal(candleIndexAt(width, width, count), count - 1)
      const first = layoutBuyMarkers([{ x: layout.left + layout.barWidth / 2, y: 120 }], width, 320)[0]
      assert.ok(Math.abs(first.left + 8 - first.x) < 1e-9)
    }
  }
})

test('trigger labels remain complete, separate and attached to their original dates after resizing', () => {
  for (const [width, height] of [
    [694, 256],
    [240, 190],
  ]) {
    const markers = Array.from({ length: 10 }, (_, i) => ({
      id: `trigger-${i}`,
      day: `day-${i}`,
      x: width - (10 - i) * 2,
      y: height - 12 - i * 2,
    }))
    const before = structuredClone(markers)
    const result = layoutBuyMarkers(markers, width, height)
    assert.deepEqual(markers, before)
    assert.deepEqual(
      result.map((p) => p.id),
      markers.map((p) => p.id),
    )
    for (const [i, p] of result.entries()) {
      assert.equal(p.day, markers[i].day)
      assert.equal(p.x, markers[i].x)
      assert.equal(p.y, markers[i].y)
      assert.ok(p.left >= 0 && p.left + 16 <= width && p.top >= 0 && p.top + 20 <= height)
      assert.ok(
        result.slice(i + 1).every((q) => Math.abs(p.left - q.left) >= 18 || Math.abs(p.top - q.top) >= 22),
      )
    }
  }
})

function history() {
  return Array.from({ length: 160 }, (_, i) => {
    const close = 1 + i * 0.002 + Math.sin(i * 0.6) * 0.025
    return {
      day: new Date(Date.UTC(2026, 0, i + 1)).toISOString().slice(0, 10),
      open: close - 0.004,
      close: close + 0.004,
      high: close + 0.012,
      low: close - 0.012,
      volume: 100000 + i * 10,
      amount: 200000,
    }
  })
}

test('every overlay, weekly background and rolling indicator matches a chart ending on the selected day', () => {
  const points = history(),
    asOf = points[139].day
  const prefix = prepareCandles(points.slice(0, 140))
  const result = prepareChartReview(points, asOf)
  assert.deepEqual(result.prepared, prefix)
  const expected = preparePriceAction(prefix.bars)
  for (const { id } of PA_OPTIONS)
    assert.deepEqual(layerItems(result.priceAction.analysis, id), layerItems(expected.analysis, id), id)
  assert.deepEqual(result.priceAction.analysis.higherTimeframe, expected.analysis.higherTimeframe)
  assert.equal(result.priceAction.bars.at(-1).date, asOf)
  assert.deepEqual(result.prepared.bars.at(-1).macd, prefix.bars.at(-1).macd)
  assert.ok(result.priceAction.analysis.swings.every((s) => s.confirmedIndex < 140))
})

test('later OHLC extremes or malformed future data cannot change historical levels, states or warnings', () => {
  const points = history(),
    asOf = points[139].day
  const expected = prepareChartReview(points, asOf, { preset: 'strategy' })
  const changed = points.map((p, i) =>
    i <= 139 ? p : { ...p, high: Infinity, low: -100, close: 5000, volume: 1e15 },
  )
  assert.deepEqual(prepareChartReview(changed, asOf, { preset: 'strategy' }), expected)
  assert.deepEqual(prepareChartReview(points.slice(0, 140), asOf, { preset: 'strategy' }), expected)
  assert.equal(expected.prepared.omitted, 0)
})

test('moving across a swing confirmation day redraws the swing and returning reproduces the earlier state', () => {
  const points = history()
  const end = prepareChartReview(points, points.at(-1).day).priceAction.analysis
  const swing = end.swings.find((s) => s.index > 100 && s.confirmedIndex < 158)
  assert.ok(swing)
  const before = prepareChartReview(points, points[swing.confirmedIndex - 1].day)
  const after = prepareChartReview(points, points[swing.confirmedIndex].day)
  assert.equal(
    before.priceAction.analysis.swings.some((s) => s.index === swing.index),
    false,
  )
  assert.equal(
    after.priceAction.analysis.swings.some((s) => s.index === swing.index),
    true,
  )
  assert.deepEqual(prepareChartReview(points, points[swing.confirmedIndex - 1].day), before)
})

test('historical strategy boundaries are recalculated even when observation layers are disabled', () => {
  const points = history(),
    asOf = points[139].day
  const result = prepareChartReview(points, asOf, { preset: 'strategy', layers: false })
  assert.equal(result.priceAction, null)
  const expected = priceActionLevels(
    points.slice(0, 140).map((p) => ({ ...p, date: p.day })),
    asOf,
  )
  assert.deepEqual(result.strategy, expected)
  assert.equal(result.strategy.as_of, asOf)
  assert.equal(
    prepareChartReview(points, asOf, { preset: 'strategy', minimumBars: 250 }).strategy.ready,
    false,
  )
})

test('a bad bar only disables structure from its own date; markers respect their known date including legacy data', () => {
  const points = history()
  points[140].low = null
  assert.ok(prepareChartReview(points, points[139].day).priceAction.analysis)
  assert.equal(prepareChartReview(points, points[140].day).priceAction.analysis, null)
  const markers = [
    { id: 'later', day: '2026-09-04', confirmation_day: '2026-09-07' },
    { id: 'same', day: '2026-09-04', confirmation_day: '2026-09-04' },
    { id: 'causal', day: '2026-09-04', known_through: '2026-09-03' },
  ]
  assert.deepEqual(
    reviewMarkersAsOf(markers, '2026-09-04').map((m) => m.id),
    ['same', 'causal'],
  )
  assert.deepEqual(reviewMarkersAsOf(markers, '2026-09-07'), markers)
  assert.deepEqual(reviewMarkersAsOf(markers, '2026-09-03'), [])
})
