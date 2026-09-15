import assert from 'node:assert/strict'
import test from 'node:test'
import { minuteTDay, minuteTLevels, minuteTSecurities, tradeIntradayDays } from '../src/minute-t-levels.js'
import { intradayGeometry } from '../src/intraday-levels.js'
import { tradeIntradayChart } from '../src/trade-intraday-chart.js'
import { recentTradeEvents, tradedSecurities } from '../src/recent-trades.js'

const snapshot = {
  enabled: true,
  model: 'minute5',
  at: '2026-09-11T05:31:00Z',
  items: [
    {
      symbol: 'sh512800',
      name: '银行ETF',
      quantity: 1000,
      support: 0.85,
      resistance: 0.855,
      support_stop: 0.848,
      bar_at: '2026-09-11T13:30:00+08:00',
      source: 'tencent',
      message: '等待压力附近转弱的 5 分钟 K',
      frozen: true,
    },
  ],
}
const data = {
  symbol: 'sh512800',
  day: '2026-09-11',
  previous_close: 0.851,
  points: [
    { minute: 0, price: 0.851 },
    { minute: 150, price: 0.85 },
  ],
}

test('T references use raw prices, completed-bar time and frozen-cycle context without fabricating trades', () => {
  const levels = minuteTLevels(snapshot, data)
  assert.deepEqual(
    levels.map((level) => level.value),
    [0.85, 0.855, 0.848],
  )
  assert.ok(levels.every((level) => level.minute === 150 && level.kind === 'minute-t'))
  assert.match(levels[0].title, /本轮冻结价位.*13:30.*等待压力.*参考价需形态确认/)
  assert.equal(tradeIntradayChart(data, [], data.day, 280, levels).anchors.length, 0)
})

test('closed-position observation prices render the same three lines without inventing T fills', () => {
  const closed = {
    ...snapshot,
    items: [
      {
        ...snapshot.items[0],
        quantity: 0,
        available: 0,
        observation_only: true,
        frozen: false,
        message: '今日已清仓，继续观察分钟支撑压力；买回需满足交易条件',
      },
    ],
  }
  const levels = minuteTLevels(closed, data)
  assert.deepEqual(
    levels.map((level) => level.value),
    [0.85, 0.855, 0.848],
  )
  assert.match(levels[0].title, /今日已清仓/)
  const chart = tradeIntradayChart(data, [], data.day, 280, levels)
  assert.equal(chart.references.length, 3)
  assert.equal(chart.anchors.length, 0)
})

test('current T references cannot leak onto other symbols or historical days, or use future candles', () => {
  for (const change of [{ symbol: 'sh515220' }, { day: '2026-09-10' }, { points: [] }])
    assert.deepEqual(minuteTLevels(snapshot, { ...data, ...change }), [])
  for (const change of [
    { enabled: false },
    { model: 'daily' },
    { items: [] },
    { at: 'bad' },
    { at: '2026-09-11T13:29:00+08:00' },
  ])
    assert.deepEqual(minuteTLevels({ ...snapshot, ...change }, data), [])
  for (const bar_at of ['2026-09-10T13:30:00+08:00', 'bad', '2026-09-11T12:30:00+08:00'])
    assert.deepEqual(minuteTLevels({ ...snapshot, items: [{ ...snapshot.items[0], bar_at }] }, data), [])
  assert.deepEqual(minuteTLevels(null, data), [])
  const invalid = {
    ...snapshot,
    items: [{ ...snapshot.items[0], support: null, resistance: Infinity, support_stop: -1 }],
  }
  assert.deepEqual(minuteTLevels(invalid, data), [])
})

test('a delayed price line keeps T references at their later source time without adding price samples', () => {
  const delayed = { ...data, points: [{ minute: 125, price: 0.85 }] }
  const chart = tradeIntradayChart(delayed, [], delayed.day, 280, minuteTLevels(snapshot, delayed))
  assert.equal(chart.last.minute, 125)
  assert.equal(chart.references.length, 3)
  assert.ok(chart.references.every((line) => line.x > chart.last.x))
  assert.equal(chart.anchors.length, 0)
})

test('both chart sizes retain all T references and align price lines exactly; labels stay apart', () => {
  const levels = minuteTLevels(snapshot, data)
  for (const height of [155, 260]) {
    const chart = intradayGeometry(data, [], height, [], levels)
    assert.equal(chart.references.length, 3)
    const support = chart.references.find((line) => line.id === 'minute-t-support')
    assert.ok(Math.abs(support.y - chart.plotted.at(-1).y) < 1e-9)
    chart.references.forEach((line, i) => {
      assert.ok(line.y >= 10 && line.y <= 210)
      if (i) assert.ok(((line.labelY - chart.references[i - 1].labelY) * height) / 220 >= 19.99)
    })
  }
  for (const width of [200, 300]) {
    const chart = tradeIntradayChart(data, [], data.day, width, levels)
    const support = chart.references.find((line) => line.id === 'minute-t-support')
    assert.ok(Math.abs(support.y - chart.last.y) < 1e-9)
    assert.equal(support.x, chart.last.x)
    assert.equal(chart.references.length, 3)
    chart.references.forEach((line, i) => {
      assert.ok(line.y >= chart.top && line.y <= chart.bottom)
      if (i) assert.ok(line.labelY - chart.references[i - 1].labelY >= 17.99)
    })
  }
})

test('held ETFs default to today within existing cards and retain historical fills without duplicates', () => {
  const previous = [
    { symbol: 'sh515220', name: '煤炭ETF', events: [{ day: '2026-09-10' }] },
    { symbol: 'sh512800', name: '银行ETF', events: [{ day: '2026-09-09' }] },
  ]
  const original = structuredClone(previous)
  const cards = minuteTSecurities(previous, snapshot, snapshot.items)
  assert.equal(cards.length, 1)
  assert.equal(cards[0].symbol, 'sh512800')
  assert.deepEqual(tradeIntradayDays(cards[0]), ['2026-09-11', '2026-09-09'])
  assert.deepEqual(previous, original)
  assert.equal(minuteTSecurities(previous, { ...snapshot, enabled: false }, snapshot.items).length, 1)
  const [withoutFills] = minuteTSecurities([], snapshot, snapshot.items)
  assert.deepEqual(withoutFills.events, [])
  assert.deepEqual(tradeIntradayDays(withoutFills), ['2026-09-11'])
})

test('account ownership filters charts even when T data is disabled, unavailable or older than liquidation', () => {
  const securities = [
    { symbol: 'sh512800', events: [{ day: '2026-09-09' }] },
    { symbol: 'sh515220', events: [{ day: '2026-09-10' }] },
  ]
  const positions = [
    { symbol: 'sh512800', name: '银行ETF', quantity: 0 },
    {
      symbol: 'sh515220',
      name: '煤炭ETF',
      quantity: 100,
      available: 0,
      quote_at: '2026-09-11T14:00:00+08:00',
    },
  ]
  for (const state of [snapshot, null, { ...snapshot, enabled: false }, { ...snapshot, model: 'daily' }]) {
    const cards = minuteTSecurities(securities, state, positions)
    assert.deepEqual(
      cards.map((card) => card.symbol),
      ['sh515220'],
    )
    assert.deepEqual(tradeIntradayDays(cards[0]), ['2026-09-11', '2026-09-10'])
    assert.deepEqual(minuteTSecurities(securities, state, []), [])
    assert.deepEqual(minuteTSecurities(securities, state), [])
  }
  assert.equal(securities.length, 2) // Timeline history is retained.
})

test("today's actual sellers remain visible after closing, while held sellers appear only once", () => {
  const events = recentTradeEvents([
    {
      id: 1,
      order_id: 1,
      symbol: 'sh512800',
      name: '银行ETF',
      at: '2026-09-11T01:35:00Z',
      side: 'SELL',
      quantity: 100,
      price: 0.85,
      gross: 85,
    },
    {
      id: 2,
      order_id: 2,
      symbol: 'sh561360',
      name: '石油ETF',
      at: '2026-09-11T09:34:00+08:00',
      side: 'SELL',
      quantity: 100,
      price: 1.5,
      gross: 150,
    },
    {
      id: 3,
      order_id: 3,
      symbol: 'sh515220',
      name: '煤炭ETF',
      at: '2026-09-10T14:30:00+08:00',
      side: 'SELL',
      quantity: 100,
      price: 1.3,
      gross: 130,
    },
    {
      id: 4,
      order_id: 4,
      symbol: 'sz159981',
      name: '能源ETF',
      at: '2026-09-11T09:33:00+08:00',
      side: 'BUY',
      quantity: 100,
      price: 1.7,
      gross: 170,
    },
  ])
  const securities = tradedSecurities(events)
  const original = structuredClone(securities)
  for (const status of [snapshot, null, { ...snapshot, enabled: false }, { ...snapshot, model: 'daily' }]) {
    const cards = minuteTSecurities(securities, status, snapshot.items, '2026-09-11')
    assert.deepEqual(
      cards.map((card) => card.symbol),
      ['sh512800', 'sh561360'],
    )
    assert.ok(cards.every((card) => tradeIntradayDays(card)[0] === '2026-09-11'))
    assert.deepEqual(
      minuteTSecurities(securities, status, [], '2026-09-11').map((card) => card.symbol),
      ['sh512800', 'sh561360'],
    )
    assert.deepEqual(minuteTSecurities(securities, status, [], '2026-09-12'), [])
    assert.deepEqual(
      minuteTSecurities(securities, status, snapshot.items, '2026-09-12').map((card) => card.symbol),
      ['sh512800'],
    )
  }
  assert.deepEqual(securities, original)
})

test('real T fills retain actual prices and explicit T labels while locating on the minute line', () => {
  const events = recentTradeEvents([
    {
      id: 1,
      order_id: 1,
      symbol: data.symbol,
      name: '银行ETF',
      at: '2026-09-11T13:30:00+08:00',
      quantity: 100,
      price: 0.85,
      gross: 85,
      side: 'BUY',
      order_kind: 't_buy',
    },
  ])
  const chart = tradeIntradayChart(data, events, data.day, 280, minuteTLevels(snapshot, data))
  assert.equal(chart.anchors.length, 1)
  assert.match(chart.anchors[0].label, /做 T 买回 13:30:00/)
  assert.equal(chart.anchors[0].minute, 150)
  assert.equal(chart.anchors[0].price, 0.85)
})

test('weekends preserve the previous session cards and T levels until the next opening', () => {
  const closed = { ...snapshot, day: '2026-09-11', at: '2026-09-13T16:00:00+08:00' }
  const securities = [
    { symbol: 'sh515220', events: [{ day: '2026-09-11', side: 'SELL' }] },
    { symbol: 'sh561360', events: [{ day: '2026-09-10', side: 'SELL' }] },
  ]
  assert.equal(minuteTDay(closed), '2026-09-11')
  assert.equal(minuteTLevels(closed, data).length, 3)
  for (const state of [closed, { ...closed, enabled: false }, null]) {
    const cards = minuteTSecurities(securities, state, snapshot.items, '2026-09-11')
    assert.deepEqual(
      cards.map((card) => card.symbol),
      ['sh512800', 'sh515220'],
    )
    assert.ok(cards.every((card) => card.currentDay === '2026-09-11'))
  }
  const reopened = { ...closed, day: '2026-09-14', at: '2026-09-14T09:30:00+08:00' }
  assert.deepEqual(minuteTLevels(reopened, data), [])
  assert.deepEqual(minuteTLevels({ ...closed, day: null }, data), [])
  assert.deepEqual(minuteTLevels(closed, { ...data, day: '2026-09-10' }), [])
  const cards = minuteTSecurities(securities, reopened, snapshot.items, '2026-09-14')
  assert.deepEqual(
    cards.map((card) => card.symbol),
    ['sh512800'],
  )
  assert.equal(cards[0].currentDay, '2026-09-14')
  assert.equal(tradeIntradayChart(data, [], data.day, 280, minuteTLevels(closed, data)).anchors.length, 0)
})
