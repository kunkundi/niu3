import test from 'node:test'
import assert from 'node:assert/strict'
import { signalWatchlistGroups } from '../src/signal-watchlist.js'

const symbols = (rows) => rows.map((row) => row.symbol)
test('strategy selection and holdings use independent sources, including held exit signals', () => {
  const plan = {
    rows: [
      { symbol: 'held', representative: true, selected: true },
      { symbol: 'new', representative: true, selected: true },
      { symbol: 'exit', representative: true, selected: false, pa: { action: 'exit' } },
      { symbol: 'observe', representative: true, selected: false },
      { symbol: 'outside', representative: false, selected: false },
    ],
  }
  const positions = [
    { symbol: 'held', quantity: 100 },
    { symbol: 'exit', quantity: 50, available: 0 },
  ]
  const before = structuredClone({ plan, positions })
  const groups = signalWatchlistGroups(plan, positions)
  assert.deepEqual(symbols(groups.candidates), ['exit', 'observe'])
  assert.deepEqual(symbols(groups.selected), ['held', 'new'])
  assert.deepEqual(symbols(groups.holdings), ['held', 'exit'])
  assert.equal(groups.holdings[1].position.available, 0)
  assert.deepEqual({ plan, positions }, before)
})

test('real positions remain visible when signals are missing and costs are not used as quotes', () => {
  const position = { symbol: 'held', name: '持仓 ETF', quantity: '100', available: 0, price: 1.2 }
  const groups = signalWatchlistGroups({}, [position])
  assert.deepEqual(symbols(groups.holdings), ['held'])
  assert.equal(groups.holdings[0].name, '持仓 ETF')
  assert.equal(groups.holdings[0].quote, undefined)
  assert.equal(groups.holdings[0].selected, undefined)
  assert.deepEqual(groups.candidates, [])
  assert.deepEqual(groups.selected, [])
})

test('holdings exclude closed, invalid and pending-only positions and update after a fill', () => {
  const plan = { rows: [{ symbol: 'pending', representative: true, selected: true }] }
  const groups = signalWatchlistGroups(plan, [
    { symbol: 'closed', quantity: 0 },
    { symbol: 'bad', quantity: 'invalid' },
    { symbol: 'negative', quantity: -1 },
    { symbol: 'infinite', quantity: Infinity },
  ])
  assert.deepEqual(groups.holdings, [])
  assert.deepEqual(symbols(signalWatchlistGroups(plan, [{ symbol: 'pending', quantity: 100 }]).holdings), [
    'pending',
  ])
  assert.deepEqual(signalWatchlistGroups(plan, []).holdings, [])
})

test('post-close qualification changes selected previews without changing actual holdings', () => {
  const groups = signalWatchlistGroups(
    {
      mode: 'post_close',
      rows: [
        { symbol: 'qualified', representative: true, selected: false, post_close_candidate: true },
        { symbol: 'observe', representative: true, selected: true, post_close_candidate: false },
      ],
    },
    [{ symbol: 'observe', quantity: 100 }],
  )
  assert.deepEqual(symbols(groups.selected), ['qualified'])
  assert.deepEqual(symbols(groups.candidates), ['observe'])
  assert.deepEqual(symbols(groups.holdings), ['observe'])
})
