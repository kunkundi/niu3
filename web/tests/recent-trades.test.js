import assert from 'node:assert/strict'
import test from 'node:test'
import { recentTradeEvents } from '../src/recent-trades.js'

const fill = (id, overrides = {}) => ({
  id,
  order_id: 4,
  symbol: 'sh510300',
  name: '沪深300ETF',
  side: 'SELL',
  at: `2026-09-11T09:30:0${id}+08:00`,
  quantity: 100,
  price: 2,
  gross: 200,
  position_effect: 'reduce',
  ...overrides,
})

test('one order shows its full time range, weighted price and final position effect', () => {
  const items = [fill(3, { quantity: 300, price: 3, gross: 900, position_effect: 'close' }), fill(1)]
  const [event] = recentTradeEvents(items)
  assert.equal(event.start, '09:30:01')
  assert.equal(event.end, '09:30:03')
  assert.equal(event.quantity, 400)
  assert.equal(event.gross, 1100)
  assert.equal(event.price, 2.75)
  assert.equal(event.effect, '清仓')
  assert.deepEqual(
    event.items.map((item) => item.id),
    [1, 3],
  )
  assert.deepEqual(
    items.map((item) => item.id),
    [3, 1],
  )
})

test('separate orders, dates, ETFs and directions never merge; newest execution comes first', () => {
  const events = recentTradeEvents([
    fill(1, { at: '2026-09-10T09:30:00+08:00' }),
    fill(2),
    fill(3, { order_id: 5 }),
    fill(4, { symbol: 'sz159915' }),
    fill(5, { side: 'BUY', position_effect: 'add' }),
  ])
  assert.equal(events.length, 5)
  assert.deepEqual(
    events.map((event) => event.lastId),
    [5, 4, 3, 2, 1],
  )
})

test('exchange dates are UTC+8 and older IDs can have more recent execution times', () => {
  const events = recentTradeEvents([
    fill(9, { at: '2026-09-10T01:30:00Z' }),
    fill(1, { at: '2026-09-10T17:30:00Z' }),
  ])
  assert.equal(events[0].day, '2026-09-11')
  assert.equal(events[0].end, '01:30:00')
  assert.equal(events[0].lastId, 1)
})

test('partial exits remain reductions, T labels require explicit order kinds', () => {
  assert.equal(recentTradeEvents([fill(1)])[0].effect, '减仓')
  assert.equal(recentTradeEvents([fill(1, { order_kind: 't_sell' })])[0].effect, '做 T 卖出')
  assert.equal(recentTradeEvents([fill(1, { position_effect: null })])[0].effect, '卖出')
  assert.equal(recentTradeEvents([fill(1, { position_effect: null, name: null })])[0].name, '510300')
})

test('missing order IDs remain individual fills and unusable dates are not presented as trades', () => {
  assert.equal(recentTradeEvents([fill(1, { order_id: null }), fill(2, { order_id: null })]).length, 2)
  assert.deepEqual(recentTradeEvents([fill(1, { at: 'invalid' }), fill(2, { side: 'UNKNOWN' })]), [])
  assert.deepEqual(recentTradeEvents(), [])
})
