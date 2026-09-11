import assert from 'node:assert/strict'
import test from 'node:test'
import { tradeIntradayChart } from '../src/trade-intraday-chart.js'
import { recentTradeEvents, tradedSecurities } from '../src/recent-trades.js'
import { loadIntraday } from '../src/intraday.js'
import { layoutTradeMarkers } from '../src/trade-observation.js'

const fill = (id, at, side = 'BUY') => ({
  id,
  order_id: id,
  at,
  symbol: 'sh510300',
  name: '沪深300ETF',
  side,
  quantity: 100,
  price: 1.05,
  gross: 105,
})
const data = {
  symbol: 'sh510300',
  day: '2026-09-10',
  previous_close: 1,
  points: [
    { time: '09:30', minute: 0, price: 1 },
    { time: '09:31', minute: 1, price: 1.02 },
    { time: '13:00', minute: 120, price: 1.01 },
    { time: '13:01', minute: 121, price: 1.03 },
  ],
}
test('intraday marks only the selected session, at actual second and unadjusted execution price', () => {
  const events = recentTradeEvents([
    fill(1, '2026-09-10T09:31:30+08:00'),
    fill(2, '2026-09-10T13:00:30+08:00', 'SELL'),
    fill(3, '2026-09-09T13:00:30+08:00'),
    fill(4, '2026-09-10T13:05:30+08:00'),
  ])
  const model = tradeIntradayChart(data, events, data.day, 280)
  assert.equal(model.anchors.length, 2)
  const buy = model.anchors.find((marker) => marker.side === 'BUY')
  assert.equal(buy.minute, 1.5)
  assert.equal(buy.x, (8 + (1.5 / 240) * 264) / 280)
  assert.equal(buy.price, 1.05)
  assert.ok(buy.y < model.previousY / model.height)
  assert.equal(buy.event.id, events.find((event) => event.lastId === 1).id)
  assert.equal(model.anchors.find((marker) => marker.side === 'SELL').minute, 120.5)
  assert.equal(tradeIntradayChart(data, events, '2026-09-09'), null)
})
test('missing intervals remain broken paths; early data keeps the full session axis', () => {
  const model = tradeIntradayChart(data, [], data.day)
  assert.equal((model.path.match(/M/g) || []).length, 2)
  assert.ok(model.last.x < model.width * 0.6)
  assert.equal(tradeIntradayChart({ ...data, points: [] }, [], data.day), null)
})
test('each callout shows its own quantities and ledger amounts, including earlier same-side trades', () => {
  const events = recentTradeEvents([
    { ...fill(1, '2026-09-10T09:30:05+08:00'), order_id: 1, price: 1.234567, gross: 123.46 },
    { ...fill(2, '2026-09-10T09:30:20+08:00'), order_id: 1, price: 1.2, gross: 120 },
    { ...fill(3, '2026-09-10T09:31:10+08:00'), order_id: 1, quantity: 200, gross: 210 },
    { ...fill(4, '2026-09-10T13:00:10+08:00', 'SELL'), quantity: 1200, gross: 1260 },
    { ...fill(5, '2026-09-10T13:01:10+08:00', 'SELL'), gross: undefined },
  ])
  const model = tradeIntradayChart(data, events, data.day, 246)
  const byTime = new Map(model.anchors.map((marker) => [marker.time, marker]))
  assert.equal(byTime.size, 4)
  assert.deepEqual(byTime.get('09:30:20').labelDetails, ['200 份', '243.46 元'])
  assert.deepEqual(byTime.get('09:31:10').labelDetails, ['200 份', '210.00 元'])
  assert.deepEqual(byTime.get('13:00:10').labelDetails, ['1,200 份', '1,260.00 元'])
  assert.deepEqual(byTime.get('13:01:10').labelDetails, ['100 份', '105.00 元'])
  for (const marker of model.anchors) {
    assert.match(marker.label, /^(买入|卖出) \d{2}:\d{2}:\d{2}$/)
    assert.equal(marker.leaderDash, '3 3')
    assert.ok(marker.title.includes(`成交金额 ${marker.labelDetails[1]}`))
  }
})
test('dense detailed callouts all remain visible without covering each other or execution dots', () => {
  const events = recentTradeEvents(
    Array.from({ length: 12 }, (_, i) =>
      fill(i, `2026-09-10T09:30:${String(i).padStart(2, '0')}+08:00`, i % 2 ? 'SELL' : 'BUY'),
    ),
  )
  for (const width of [200, 246, 290]) {
    const model = tradeIntradayChart(data, events, data.day, width)
    const anchors = model.anchors.map((marker) => ({
      ...marker,
      x: marker.x * width,
      y: marker.y * model.height,
    }))
    const placed = layoutTradeMarkers(anchors, width, model.height)
    assert.equal(placed.length, events.length)
    for (const [i, p] of placed.entries()) {
      assert.ok(p.left >= 0 && p.left + p.labelWidth <= width)
      assert.ok(p.top >= 0 && p.top + p.labelHeight <= model.height)
      assert.ok(
        anchors.every(
          (point) =>
            point.x < p.left - 4 ||
            point.x > p.left + p.labelWidth + 4 ||
            point.y < p.top - 4 ||
            point.y > p.top + p.labelHeight + 4,
        ),
      )
      assert.ok(
        placed
          .slice(i + 1)
          .every(
            (q) =>
              p.left >= q.left + q.labelWidth + 4 ||
              q.left >= p.left + p.labelWidth + 4 ||
              p.top >= q.top + q.labelHeight + 4 ||
              q.top >= p.top + p.labelHeight + 4,
          ),
      )
    }
  }
})
test('cards retain all trades for date selection and sort by recent execution', () => {
  const events = recentTradeEvents([
    fill(1, '2026-09-10T09:31:00+08:00'),
    fill(2, '2026-09-11T09:32:00+08:00', 'SELL'),
  ])
  const [card] = tradedSecurities(events)
  assert.equal(card.events[0].day, '2026-09-11')
  assert.equal(card.events[1].day, '2026-09-10')
})
test('dated chart requests retain the selected date through the shared request queue', async () => {
  const original = globalThis.fetch
  let url
  globalThis.fetch = async (value) => {
    url = value
    return new Response(JSON.stringify(data))
  }
  try {
    await loadIntraday('sh510300', () => true, 'etf', false, false, '2026-09-10')
    assert.equal(url, '/api/v1/etfs/sh510300/intraday?day=2026-09-10')
  } finally {
    globalThis.fetch = original
  }
})
