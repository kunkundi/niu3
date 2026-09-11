import assert from 'node:assert/strict'
import test from 'node:test'
import {
  exchangeTime,
  tradeGroups,
  intradayTradeGroups,
  layoutTradeMarkers,
  tradeIntent,
  tradeEffect,
} from '../src/trade-observation.js'
import { intradayGeometry } from '../src/intraday-levels.js'
const fill = (id, at, side = 'BUY', price = 1, quantity = 100) => ({
  id,
  symbol: 'sh510300',
  at,
  side,
  price,
  quantity,
})
test('exchange time is independent of browser timezone and preserves the lunch break', () => {
  assert.equal(exchangeTime('2026-09-10T01:34:04Z').day, '2026-09-10')
  assert.equal(exchangeTime('2026-09-09T17:00:00Z').day, '2026-09-10')
  assert.equal(exchangeTime('2026-09-10T09:30:00+08:00').minute, 0)
  assert.equal(exchangeTime('2026-09-10T11:30:00+08:00').minute, 120)
  assert.equal(exchangeTime('2026-09-10T12:00:00+08:00').minute, null)
  assert.equal(exchangeTime('2026-09-10T13:00:00+08:00').minute, 120)
  assert.equal(exchangeTime('2026-09-10T15:00:00+08:00').minute, 240)
  assert.equal(exchangeTime('invalid'), null)
})
test('partial fills group by side and exchange day with quantity weighted prices', () => {
  const items = [
    fill(1, '2026-09-10T09:31:00+08:00'),
    fill(2, '2026-09-10T09:31:30+08:00', 'BUY', 2, 300),
    fill(3, '2026-09-10T13:31:00+08:00', 'SELL'),
  ]
  const groups = tradeGroups(items)
  assert.equal(groups.length, 2)
  assert.equal(groups[0].price, 1.75)
  assert.equal(groups[0].quantity, 400)
  assert.equal(groups[0].items.length, 2)
  assert.equal(groups[1].tone, 'sell')
  assert.equal(tradeGroups([...items, { ...items[0], price: null }]).length, 2)
})
test('intraday marks only matching dates, market hours, and minutes covered by the chart', () => {
  const data = {
    day: '2026-09-10',
    previous_close: 1,
    points: [
      { minute: 0, price: 1 },
      { minute: 121, price: 1.01 },
    ],
  }
  const groups = intradayTradeGroups(
    [
      fill(1, '2026-09-09T10:00:00+08:00'),
      fill(2, '2026-09-10T12:00:00+08:00'),
      fill(3, '2026-09-10T13:01:30+08:00', 'SELL', 1.02),
      fill(4, '2026-09-10T13:02:00+08:00'),
    ],
    data,
  )
  assert.deepEqual(
    groups.map((g) => g.items[0].id),
    [3],
  )
  assert.equal(intradayTradeGroups([], {}).length, 0)
  const chart = intradayGeometry(
    data,
    [],
    220,
    groups.map((g) => g.price),
  )
  assert.ok(chart.high >= 1.02)
  assert.equal(chart.plotted[1].x, 302.5)
})
test('opening, adding, reducing and closing fills keep distinct B/S groups on one day or minute', () => {
  const effects = ['open', 'open', 'add', 'reduce', 'close']
  const items = effects.map((effect, i) => ({
    ...fill(i, `2026-09-10T09:31:0${i}+08:00`, i < 3 ? 'BUY' : 'SELL', i === 1 ? 2 : 1, i === 1 ? 300 : 100),
    position_effect: effect,
  }))
  for (const mode of ['daily', 'intraday']) {
    const groups = tradeGroups(items, mode)
    assert.deepEqual(
      groups.map((g) => [g.label, g.actionLabel]),
      [
        ['B', '开仓'],
        ['B', '加仓'],
        ['S', '减仓'],
        ['S', '清仓'],
      ],
    )
    assert.equal(new Set(groups.map((g) => g.effectTone)).size, 4)
    assert.equal(groups[0].price, 1.75)
    assert.equal(groups[0].items.length, 2)
    assert.equal(groups[1].items.length, 1)
  }
})
test('missing or side-inconsistent effects remain neutral, separate from classified fills', () => {
  const item = fill(1, '2026-09-10T09:31:00+08:00')
  assert.equal(tradeEffect(item).id, 'unknown')
  assert.equal(tradeEffect({ ...item, position_effect: 'close' }).id, 'unknown')
  const groups = tradeGroups([item, { ...item, id: 2, position_effect: 'open' }])
  assert.equal(groups.length, 2)
  assert.equal(groups[0].label, 'B')
  assert.equal(groups[0].effectTone, 'trade-effect-unknown')
})
test('actual T orders render T for both legs and stay separate from ordinary trades', () => {
  const items = [
    { ...fill(1, '2026-09-10T09:31:00+08:00', 'SELL'), position_effect: 'reduce', order_kind: 't_sell' },
    {
      ...fill(2, '2026-09-10T09:31:10+08:00', 'SELL', 2, 300),
      position_effect: 'reduce',
      order_kind: 't_sell',
    },
    { ...fill(3, '2026-09-10T09:31:20+08:00', 'BUY'), position_effect: 'add', order_kind: 't_buy' },
    { ...fill(4, '2026-09-10T09:31:30+08:00', 'SELL'), position_effect: 'reduce', order_kind: 'intraday' },
    { ...fill(5, '2026-09-10T09:31:40+08:00', 'BUY'), position_effect: 'add', order_kind: 'intraday' },
  ]
  for (const mode of ['daily', 'intraday']) {
    const groups = tradeGroups(items, mode)
    assert.deepEqual(
      groups.map((g) => [g.label, g.actionLabel, g.side]),
      [
        ['T', '做 T 卖出', 'SELL'],
        ['T', '做 T 买回', 'BUY'],
        ['S', '减仓', 'SELL'],
        ['B', '加仓', 'BUY'],
      ],
    )
    assert.equal(groups[0].items.length, 2)
    assert.equal(groups[0].price, 1.75)
    assert.equal(groups[1].quantity, 100)
  }
  assert.equal(tradeEffect({ ...items[0], order_kind: 't_buy' }).marker, 'S')
  assert.equal(tradeEffect({ ...items[2], order_kind: undefined, reason: '做 T 参考' }).marker, 'B')
  assert.equal(tradeEffect({ ...items[0], position_effect: 'unknown' }).marker, 'T')
})
test('buy and sell labels at dense edge positions remain separate and keep their anchors', () => {
  for (const [width, height] of [
    [220, 190],
    [900, 400],
  ]) {
    const anchors = Array.from({ length: 8 }, (_, i) => ({
      id: i,
      kind: 'fill',
      side: i % 2 ? 'SELL' : 'BUY',
      x: width - 4,
      y: height - 8 - i,
    }))
    const result = layoutTradeMarkers(anchors, width, height)
    assert.equal(result.length, anchors.length)
    for (const [i, p] of result.entries()) {
      assert.equal(p.x, anchors[p.id].x)
      assert.equal(p.y, anchors[p.id].y)
      assert.ok(
        p.left >= 0 && p.left + p.labelWidth <= width && p.top >= 0 && p.top + p.labelHeight <= height,
      )
      assert.ok(
        result
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
test('small candidates yield space to larger fills without changing fill positions', () => {
  const fills = [
    { id: 'buy', kind: 'fill', side: 'BUY', x: 205, y: 150 },
    { id: 'sell', kind: 'fill', side: 'SELL', x: 205, y: 150 },
  ]
  const candidates = Array.from({ length: 12 }, (_, i) => ({
    id: `candidate:${i}`,
    kind: 'candidate',
    side: 'BUY',
    x: 205 - i * 2,
    y: 150,
  }))
  const baseline = layoutTradeMarkers(fills, 220, 190)
  const mixed = layoutTradeMarkers([...candidates, ...fills], 220, 190)
  assert.deepEqual(
    mixed.filter((m) => m.kind === 'fill'),
    baseline,
  )
  assert.ok(mixed.some((m) => m.kind === 'candidate'))
  for (const p of mixed.filter((m) => m.kind === 'candidate')) {
    assert.ok(p.labelWidth < baseline[0].labelWidth && p.labelHeight < baseline[0].labelHeight)
    assert.ok(
      mixed
        .filter((m) => m.id !== p.id)
        .every(
          (q) =>
            p.left + p.labelWidth + 4 <= q.left ||
            q.left + q.labelWidth + 4 <= p.left ||
            p.top + p.labelHeight + 4 <= q.top ||
            q.top + q.labelHeight + 4 <= p.top,
        ),
    )
  }
})
test('current action never presents stale or post-close references as live buy or sell instructions', () => {
  const row = { symbol: 'sh510300', pa: { action: 'buy' }, selected: true, quote: {} }
  assert.equal(tradeIntent({ mode: 'live' }, row).title, '买入信号')
  assert.equal(tradeIntent({ mode: 'live' }, { ...row, pa: { action: 'exit' } }).title, '卖出信号')
  assert.equal(tradeIntent({ mode: 'live', stale: true }, row).tone, 'neutral')
  assert.equal(tradeIntent({ mode: 'live', session_snapshot: true }, row).title, '休市快照')
  assert.equal(tradeIntent({ mode: 'post_close' }, row).title, '盘后观察')
  assert.equal(tradeIntent({ mode: 'daily' }, row).title, '计划入选')
  assert.equal(tradeIntent({ mode: 'live' }, { ...row, pa: { action: 'hold' } }).title, '等待条件')
})

test('completed-day candidates keep their entry reason when the session quote expires', () => {
  const row = {
    post_close_candidate: true,
    quote: { stale: true },
    reasons: ['已形成看涨 Pin Bar；等待下一交易日触发'],
  }
  const intent = tradeIntent({ mode: 'post_close', stale: false }, row)
  assert.equal(intent.title, '次日候选')
  assert.equal(intent.message, row.reasons[0])
  assert.equal(tradeIntent({ mode: 'live' }, row).title, '信号待更新')
  assert.equal(tradeIntent({ mode: 'post_close', stale: true }, row).title, '信号待更新')
})
