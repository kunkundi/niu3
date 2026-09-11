import assert from 'node:assert/strict'
import test from 'node:test'
import { holdingWarning } from '../src/holding-warning.js'

const position = { price: 0.853, quote_at: '2026-09-10T15:00:00+08:00', stale: true }
const closed = {
  session_open: false,
  intraday_polling: { snapshot_key: '2026-09-10T15:00:00+08:00' },
}

test('valid last-session quotes stay quiet after close, overnight and through holidays', () => {
  for (const at of ['2026-09-10T16:00:00+08:00', '2026-09-11T09:00:00+08:00', '2026-09-13T12:00:00+08:00']) {
    assert.equal(holdingWarning(position, { ...closed, at }), '')
  }
  assert.equal(holdingWarning({ ...position, quote_at: '2026-09-10T14:58:30+08:00' }, closed), '')
})

test('lunch uses its own cutoff and quotes from an older session remain flagged', () => {
  const lunch = { session_open: false, intraday_polling: { snapshot_key: '2026-09-10T11:30:00+08:00' } }
  assert.equal(holdingWarning({ ...position, quote_at: '2026-09-10T11:29:30+08:00' }, lunch), '')
  for (const quote_at of [
    '2026-09-09T15:00:00+08:00',
    '2026-09-10T11:30:00+08:00',
    '2026-09-10T14:58:29+08:00',
  ]) {
    assert.equal(holdingWarning({ ...position, quote_at }, closed), '行情未更新')
  }
})

test('active-session delays remain visible and disappear when a fresh quote arrives', () => {
  assert.equal(holdingWarning(position, { session_open: true }), '行情延迟')
  assert.equal(holdingWarning({ ...position, stale: false }, { session_open: true }), '')
})

test('missing quotes and accounting issues stay visible even outside trading hours', () => {
  for (const quote_at of [null, '', 'invalid']) {
    assert.equal(holdingWarning({ ...position, quote_at }, closed), '行情缺失')
  }
  for (const price of [null, 0, -1, 'invalid']) {
    assert.equal(holdingWarning({ ...position, price }, closed), '行情缺失')
  }
  assert.equal(holdingWarning({ ...position, accounting_block: '折算核算未完成' }, closed), '折算核算未完成')
})

test('missing session or verified calendar data never implies a normal closing quote', () => {
  for (const status of [
    {},
    { session_open: false },
    { session_open: false, intraday_polling: { snapshot_key: 'invalid' } },
  ]) {
    assert.equal(holdingWarning(position, status), '行情待确认')
  }
})
