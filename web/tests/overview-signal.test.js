import assert from 'node:assert/strict'
import test from 'node:test'
import { overviewSignal } from '../src/overview-signal.js'

test('overnight observations display next-session candidates rather than empty trade targets', () => {
  const candidate = { symbol: 'sh510300', post_close_candidate: true, selected: false }
  const observed = { symbol: 'sh510500', post_close_candidate: false, selected: true }
  const result = overviewSignal({
    mode: 'post_close',
    execute_day: '2026-09-10',
    targets: {},
    rows: [candidate, observed],
  })
  assert.equal(result.title, '盘后观察')
  assert.equal(result.dateLabel, '下一交易日')
  assert.equal(result.date, '2026-09-10')
  assert.equal(result.countLabel, '次日候选')
  assert.deepEqual(result.rows, [candidate])
})

test('live signals show their dated calculation time and selected target weights', () => {
  const selected = { symbol: 'sh510300', selected: true, target_weight: 0.2 }
  const result = overviewSignal({
    mode: 'live',
    created_at: '2026-09-10T10:35:00+08:00',
    rows: [selected, { selected: false }],
  })
  assert.equal(result.date, '09-10 10:35:00')
  assert.equal(result.dateLabel, '计算时间')
  assert.equal(result.countLabel, '目标标的')
  assert.deepEqual(result.rows, [selected])
})

test('daily schedules retain execution date and missing plans have no fabricated date or targets', () => {
  assert.equal(overviewSignal({ mode: 'daily', execute_day: '2026-09-11' }).date, '2026-09-11')
  for (const plan of [null, {}, { mode: 'live' }, { mode: 'post_close', rows: [] }]) {
    assert.equal(overviewSignal(plan).date, '—')
    assert.deepEqual(overviewSignal(plan).rows, [])
  }
})
