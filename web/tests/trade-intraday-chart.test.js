import assert from 'node:assert/strict'
import test from 'node:test'
import { tradeIntradayChart } from '../src/trade-intraday-chart.js'
import { recentTradeEvents, tradedSecurities } from '../src/recent-trades.js'
import { loadIntraday } from '../src/intraday.js'
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
    { time: '09:32', minute: 2, price: 1.03 },
    { time: '13:00', minute: 120, price: 1.01 },
    { time: '13:01', minute: 121, price: 1.03 },
    { time: '13:02', minute: 122, price: 1.01 },
  ],
}
test('intraday marks use the actual second on the line while retaining execution prices', () => {
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
  assert.ok(Math.abs(buy.linePrice - 1.025) < 1e-12)
  assert.equal(model.unplaced[0].time, '13:05:30')
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
test('hover details retain quantities and ledger amounts, including earlier same-side trades', () => {
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
  assert.deepEqual(byTime.get('09:30:20').labelDetails, ['成交均价 1.217 元', '200 份', '243.46 元'])
  assert.deepEqual(byTime.get('09:31:10').labelDetails, ['成交价 1.050 元', '200 份', '210.00 元'])
  assert.deepEqual(byTime.get('13:00:10').labelDetails, ['成交价 1.050 元', '1,200 份', '1,260.00 元'])
  assert.deepEqual(byTime.get('13:01:10').labelDetails, ['成交价 1.050 元', '100 份', '105.00 元'])
  for (const marker of model.anchors) {
    assert.match(marker.label, /^(买入|卖出) \d{2}:\d{2}:\d{2}$/)
    assert.ok(marker.title.includes(`成交金额 ${marker.labelDetails[2]}`))
  }
})
test('dense trade points retain every execution without changing chart height or the price path', () => {
  const events = recentTradeEvents(
    Array.from({ length: 18 }, (_, i) => {
      const price = [0.99, 1.05, 1.01, 1.03][i % 4]
      const quantity = i % 2 ? 12345600 : 100
      return {
        ...fill(i, `2026-09-10T09:30:${String(i * 3).padStart(2, '0')}+08:00`, i % 3 ? 'BUY' : 'SELL'),
        price,
        quantity,
        gross: price * quantity,
      }
    }),
  )
  const original = structuredClone(events)
  for (const width of [200, 246, 315, 1117]) {
    for (const height of [124, 360]) {
      const model = tradeIntradayChart(data, events, data.day, width, [], height)
      const withoutTrades = tradeIntradayChart(data, [], data.day, width, [], height)
      assert.equal(model.height, height)
      assert.equal(model.path, withoutTrades.path)
      assert.equal(model.anchors.length, events.length)
      assert.equal(new Set(model.anchors.map((point) => point.id)).size, events.length)
      assert.equal(model.unplaced.length, 0)
      for (const point of model.anchors) {
        const event = events.find((event) => event.id === point.event.id)
        assert.ok(Math.abs(point.price - event.price) < 1e-12)
        assert.equal(point.quantity, event.quantity)
        assert.equal(point.gross, event.gross)
        assert.ok(point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1)
        assert.ok(point.title.includes(`成交金额 ${point.labelDetails[2]}`))
      }
    }
  }
  assert.deepEqual(events, original)
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
