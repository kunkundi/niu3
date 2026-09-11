import assert from 'node:assert/strict'
import test from 'node:test'
import { intradayPolling, needsIntradaySnapshot } from '../src/intraday-polling.js'

const policy = {
  interval_seconds: 5,
  snapshot_key: '2026-09-10T15:00:00+08:00',
  windows: ['2026-09-11', '2026-09-14'].flatMap((day) => [
    { start: `${day}T09:30:00+08:00`, end: `${day}T11:30:00+08:00` },
    { start: `${day}T13:00:00+08:00`, end: `${day}T15:00:00+08:00` },
  ]),
}
const at = (stamp) => Date.parse(`${stamp}+08:00`)

test('stops exactly at lunch and close, stays paused over weekends, resumes at each opening', () => {
  for (const [stamp, active, next] of [
    ['2026-09-11T09:29:59', false, '2026-09-11T09:30:00'],
    ['2026-09-11T09:30:00', true, '2026-09-11T11:30:00'],
    ['2026-09-11T11:29:59', true, '2026-09-11T11:30:00'],
    ['2026-09-11T11:30:00', false, '2026-09-11T13:00:00'],
    ['2026-09-11T13:00:00', true, '2026-09-11T15:00:00'],
    ['2026-09-11T15:00:00', false, '2026-09-14T09:30:00'],
    ['2026-09-12T10:00:00', false, '2026-09-14T09:30:00'],
    ['2026-09-14T09:30:00', true, '2026-09-14T11:30:00'],
  ]) {
    const state = intradayPolling(policy, at(stamp))
    assert.equal(state.active, active, stamp)
    assert.equal(state.nextChangeAt, at(next), stamp)
  }
  assert.match(intradayPolling(policy, at('2026-09-11T10:00:00')).message, /5 秒/)
  assert.match(intradayPolling(policy, at('2026-09-11T12:00:00')).message, /午间休市/)
})

test('loads one snapshot per lunch or close, then stays frozen across repeated status checks', () => {
  const lunch = intradayPolling(policy, at('2026-09-11T11:30:00'))
  assert.equal(lunch.snapshotKey, '2026-09-11T11:30:00+08:00')
  assert.equal(needsIntradaySnapshot(lunch, null), true)
  assert.equal(needsIntradaySnapshot(lunch, lunch.snapshotKey), false)
  const afternoon = intradayPolling(policy, at('2026-09-11T13:00:00'))
  assert.equal(needsIntradaySnapshot(afternoon, lunch.snapshotKey), false)
  const close = intradayPolling(policy, at('2026-09-11T15:00:00'))
  assert.equal(close.snapshotKey, '2026-09-11T15:00:00+08:00')
  assert.equal(needsIntradaySnapshot(close, lunch.snapshotKey), true)
  for (const stamp of [
    '2026-09-11T15:00:05',
    '2026-09-11T22:00:00',
    '2026-09-12T12:00:00',
    '2026-09-14T09:29:59',
  ]) {
    assert.equal(needsIntradaySnapshot(intradayPolling(policy, at(stamp)), close.snapshotKey), false)
  }
  const beforeOpen = intradayPolling(
    { ...policy, windows: policy.windows.slice(2), snapshot_key: close.snapshotKey },
    at('2026-09-14T09:29:59'),
  )
  assert.equal(beforeOpen.snapshotKey, close.snapshotKey)
})

test('missing or exhausted verified calendar does not invent trading windows', () => {
  for (const schedule of [null, { interval_seconds: 5, windows: [] }, policy]) {
    const state = intradayPolling(schedule, at('2027-01-05T10:00:00'))
    assert.equal(state.active, false)
    assert.equal(state.nextChangeAt, null)
  }
})
