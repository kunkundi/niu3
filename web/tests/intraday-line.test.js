import assert from 'node:assert/strict'
import test from 'node:test'
import { intradayLinePoints, intradayLinePrice } from '../src/intraday-line.js'
import { tradeIntradayChart } from '../src/trade-intraday-chart.js'
import { intradayGeometry } from '../src/intraday-levels.js'
import { recentTradeEvents } from '../src/recent-trades.js'

const day = '2026-09-09'
const sample = (time, price) => ({ at: `${day}T${time}+08:00`, minute: 126, price })
const data = {
  symbol: 'sh512800',
  day,
  previous_close: 0.839,
  points: [
    sample('13:05:32', 0.841),
    sample('13:06:35', 0.841),
    sample('13:07:38', 0.842),
    sample('13:08:38', 0.841),
  ],
}
const fills = ['13:06:09', '13:07:11', '13:08:11'].map((time, i) => ({
  id: i + 1,
  order_id: 1,
  symbol: data.symbol,
  name: '银行ETF华宝',
  at: `${day}T${time}+08:00`,
  side: 'BUY',
  quantity: 100,
  price: 0.842,
  gross: 84.2,
}))

function assertOnPath(chart) {
  const segments = [...chart.path.matchAll(/([ML])([\d.e+-]+),([\d.e+-]+)/g)].map((match) => ({
    move: match[1] === 'M',
    x: Number(match[2]),
    y: Number(match[3]),
  }))
  for (const anchor of chart.anchors) {
    const x = anchor.x * chart.width,
      y = anchor.y * chart.height
    assert.ok(
      segments.some((p, i) => {
        if (Math.hypot(p.x - x, p.y - y) < 1e-8) return true
        if (!i || p.move) return false
        const a = segments[i - 1]
        if (x < a.x || x > p.x || a.x === p.x) return false
        const expected = a.y + ((x - a.x) / (p.x - a.x)) * (p.y - a.y)
        return Math.abs(y - expected) < 1e-8
      }),
      'every trade anchor must lie on the drawn price path',
    )
  }
}

test('recorded quote seconds override rounded minute buckets and respect exchange timezones', () => {
  const original = structuredClone(data)
  const points = intradayLinePoints(data)
  assert.ok(Math.abs(points[0].minute - (125 + 32 / 60)) < 1e-12)
  assert.ok(Math.abs(points[1].minute - (126 + 35 / 60)) < 1e-12)
  const utc = { ...data, points: [{ at: `${day}T05:06:35Z`, price: 0.841 }] }
  assert.equal(intradayLinePoints(utc)[0].minute, points[1].minute)
  assert.deepEqual(data, original)
  const noSeconds = { ...data, points: [{ time: '13:06', minute: 126, price: 0.841 }] }
  assert.equal(intradayLinePoints(noSeconds)[0].minute, 126)
})

test('card and detail charts share the timeline; interpolated markers preserve ledger data', () => {
  const events = recentTradeEvents(fills),
    original = structuredClone(events)
  const detail = intradayGeometry(data)
  for (const width of [200, 315, 1117]) {
    for (const height of [124, 360]) {
      const chart = tradeIntradayChart(data, events, day, width, [], height)
      assert.equal(chart.anchors.length, 3)
      assert.equal(chart.unplaced.length, 0)
      assertOnPath(chart)
      assert.equal(chart.anchors[0].linePrice, 0.841)
      for (const anchor of chart.anchors) {
        assert.ok(Math.abs(anchor.price - 0.842) < 1e-12)
        assert.equal(anchor.gross, 84.2)
        assert.deepEqual(anchor.labelDetails, ['成交价 0.842 元', '100 份', '84.20 元'])
        assert.equal(anchor.linePrice, intradayLinePrice(detail.plotted, anchor.timestamp))
      }
      const modelWithoutTrades = tradeIntradayChart(data, [], day, width, [], height)
      assert.equal(chart.high, modelWithoutTrades.high)
      assert.equal(chart.low, modelWithoutTrades.low)
    }
  }
  assert.deepEqual(events, original)
})

test('subminute quotes retain every source point and intraminute peaks in both chart views', () => {
  const session = {
    ...data,
    points: [sample('13:06:05', 0.84), sample('13:06:20', 0.844), sample('13:06:35', 0.841)],
  }
  const points = intradayLinePoints(session)
  assert.equal(points.length, 3)
  assert.deepEqual(
    points.map((p) => p.time),
    ['13:06:05', '13:06:20', '13:06:35'],
  )
  assert.equal(intradayLinePrice(points, Date.parse(`${day}T13:06:20+08:00`)), 0.844)
  const events = recentTradeEvents([fills[0]])
  for (const width of [200, 315, 1117]) {
    const chart = tradeIntradayChart(session, events, day, width, [], 360)
    assert.equal([...chart.path.matchAll(/[ML]/g)].length, 3)
    assert.equal(chart.anchors.length, 1)
    assert.equal(chart.unplaced.length, 0)
    assertOnPath(chart)
    const detail = intradayGeometry(session)
    assert.equal(detail.plotted.length, 3)
    assert.equal(chart.anchors[0].linePrice, intradayLinePrice(detail.plotted, chart.anchors[0].timestamp))
    assert.ok(Math.abs(chart.anchors[0].price - fills[0].price) < 1e-12)
    assert.equal(chart.anchors[0].gross, fills[0].gross)
  }
})

test('missing quotes, lunch trades, and observations outside the chart never receive anchors', () => {
  const session = {
    ...data,
    points: [
      sample('09:30:30', 0.838),
      sample('09:31:30', 0.839),
      sample('09:34:00', 0.84),
      sample('11:30:00', 0.841),
      sample('13:00:00', 0.842),
      sample('13:01:00', 0.843),
    ],
  }
  const times = ['09:30:05', '09:31:00', '09:32:00', '11:30:00', '12:00:00', '13:00:00', '13:01:05']
  const events = recentTradeEvents(
    times.map((time, i) => ({ ...fills[0], id: i, order_id: i, at: `${day}T${time}+08:00` })),
  )
  const chart = tradeIntradayChart(session, events, day, 1117, [], 360)
  assert.deepEqual(
    chart.anchors.map((g) => g.time),
    ['13:00:00', '11:30:00', '09:31:00'],
  )
  assert.deepEqual(
    chart.unplaced.map((g) => g.time),
    ['13:01:05', '09:32:00', '09:30:05'],
  )
  assert.equal((chart.path.match(/M/g) || []).length, 3)
  const points = intradayLinePoints(session)
  assert.equal(points[4].segmentStart, false)
  assert.equal(points[3].minute, points[4].minute)
  assert.equal(intradayLinePrice(points, Date.parse(`${day}T11:30:00+08:00`)), 0.841)
  assert.equal(intradayLinePrice(points, Date.parse(`${day}T12:00:00+08:00`)), null)
  assert.equal(intradayLinePrice(points, Date.parse(`${day}T13:00:00+08:00`)), 0.842)
  assertOnPath(chart)
})

test('lunch joins use trading time for both the curve and second-precision trade positions', () => {
  const session = {
    ...data,
    points: [sample('11:29:30', 0.84), sample('13:00:30', 0.844), sample('13:01:30', 0.842)],
  }
  const original = structuredClone(session)
  const points = intradayLinePoints(session)
  assert.deepEqual(
    points.map((p) => p.segmentStart),
    [true, false, false],
  )
  for (const [time, expected] of [
    ['11:29:45', 0.841],
    ['11:30:00', 0.842],
    ['13:00:00', 0.842],
    ['13:00:15', 0.843],
  ]) {
    const timestamp = Date.parse(`${day}T${time}+08:00`)
    assert.ok(Math.abs(intradayLinePrice(points, timestamp) - expected) < 1e-12)
  }
  for (const time of ['11:30:01', '12:00:00', '12:59:59']) {
    assert.equal(intradayLinePrice(points, Date.parse(`${day}T${time}+08:00`)), null)
  }
  const events = recentTradeEvents(
    ['11:29:45', '11:30:00', '13:00:00', '13:00:15'].map((time, i) => ({
      ...fills[0],
      id: i,
      order_id: i,
      at: `${day}T${time}+08:00`,
    })),
  )
  const detail = intradayGeometry(session)
  for (const width of [200, 315, 1117]) {
    const chart = tradeIntradayChart(session, events, day, width, [], 360)
    assert.equal((chart.path.match(/M/g) || []).length, 1)
    assert.equal(chart.anchors.length, 4)
    assert.equal(chart.unplaced.length, 0)
    assertOnPath(chart)
    for (const anchor of chart.anchors) {
      assert.equal(anchor.linePrice, intradayLinePrice(detail.plotted, anchor.timestamp))
      assert.ok(Math.abs(anchor.price - 0.842) < 1e-12)
      assert.equal(anchor.gross, 84.2)
    }
  }
  assert.deepEqual(session, original)
})

test('minute-only lunch joins remain continuous while missing trading minutes still break', () => {
  const session = {
    ...data,
    points: [
      { time: '11:29', price: 0.84 },
      { time: '11:30', price: 0.841 },
      { time: '13:00', price: 0.842 },
      { time: '13:01', price: 0.843 },
    ],
  }
  assert.deepEqual(
    intradayLinePoints(session).map((p) => p.segmentStart),
    [true, false, false, false],
  )
  for (const [before, after, breaks] of [
    ['11:28:56', '13:00:59', false],
    ['11:28:56', '13:01:00', true],
    ['11:29:32', '13:01:32', false],
    ['11:29:32', '13:01:33', false],
    ['11:27:00', '13:01:00', true],
    ['11:29:00', '13:02:00', true],
  ]) {
    const points = intradayLinePoints({
      ...data,
      points: [sample(before, 0.84), sample(after, 0.842)],
    })
    assert.equal(points[1].segmentStart, breaks)
    assert.equal(intradayLinePrice(points, Date.parse(`${day}T13:00:00+08:00`)) === null, breaks)
  }
})

test('minute-sampling jitter is tolerated consistently within each trading session', () => {
  for (const [before, after, breaks] of [
    ['09:30:56', '09:32:59', false],
    ['09:30:56', '09:33:00', true],
    ['13:28:56', '13:30:59', false],
    ['13:28:56', '13:31:00', true],
  ]) {
    const points = intradayLinePoints({
      ...data,
      points: [sample(before, 0.84), sample(after, 0.842)],
    })
    assert.equal(points[1].segmentStart, breaks)
    const timestamp = Date.parse(`${day}T${before}+08:00`) + 90000
    assert.equal(intradayLinePrice(points, timestamp) === null, breaks)
  }
})

test('invalid samples break the line and never fall back from an invalid timestamp to minute zero', () => {
  const broken = {
    ...data,
    points: [
      sample('13:00:00', 0.841),
      { at: 'bad', minute: 121, price: 0.842 },
      sample('13:01:30', 0.843),
      sample('13:02:00', 0.842),
    ],
  }
  const points = intradayLinePoints(broken)
  assert.equal(points.length, 3)
  assert.equal(points[1].segmentStart, true)
  assert.equal(intradayLinePrice(points, Date.parse(`${day}T13:01:00+08:00`)), null)
  assert.equal(intradayLinePrice(points, Date.parse(`${day}T13:01:45+08:00`)), 0.8425)
  for (const bad of [
    { at: '2026-09-08T13:00:00+08:00', price: 1 },
    { at: `${day}T13:00:00`, price: 1 },
    { time: '12:00', minute: 120, price: 1 },
    { minute: null, price: 1 },
    { minute: 0, price: Infinity },
  ])
    assert.deepEqual(intradayLinePoints({ ...data, points: [bad] }), [])
})
