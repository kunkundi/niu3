import assert from 'node:assert/strict'
import test from 'node:test'
import { entryRejection } from '../src/price-action/entry-quality.js'

const candidate = { index: 0, low: 9, high: 10, type: 'engulfing' }
const bar = { open: 9.2, low: 9, high: 10, close: 9.8 }

test('a confirmed setup cannot survive a later completed close through its invalidation', () => {
  const history = [bar, { ...bar, close: 10.2 }, { ...bar, close: 8.9 }]
  assert.equal(entryRejection(candidate, history, 'up', 0.1, 'legacy'), null)
  assert.match(entryRejection(candidate, history, 'up', 0.1, 'valid'), /跌破失效线/)
  assert.equal(entryRejection(candidate, history.slice(0, 2), 'up', 0.1, 'valid'), null)
  // An intraday wick alone is not the completed-close invalidation rule.
  assert.equal(entryRejection(candidate, [bar, { ...bar, low: 8, close: 9.2 }], 'up', 0.1, 'valid'), null)
})

test('research filters separate signal strength, trend and completed structural retests', () => {
  assert.equal(entryRejection(candidate, [bar], 'range', 0.1, 'valid'), null)
  assert.equal(entryRejection(candidate, [bar], 'range', 0.1, 'signal-quality'), null)
  assert.match(entryRejection(candidate, [{ ...bar, close: 9.5 }], 'up', 0.1, 'signal-quality'), /上三分之一/)
  assert.match(entryRejection(candidate, [bar], 'range', 0.1, 'trend-aligned'), /非上升/)
  const structure = { ...candidate, type: 'structure', followThrough: 'weak', retested: false }
  assert.match(entryRejection(structure, [bar], 'up', 0.1, 'signal-quality'), /弱跟进/)
  assert.match(entryRejection(structure, [bar], 'up', 0.1, 'retest-only'), /回测/)
  assert.equal(entryRejection({ ...structure, retested: true }, [bar], 'up', 0.1, 'retest-only'), null)
  assert.throws(() => entryRejection(candidate, [bar], 'up', 0.1, 'unknown'))
})

test('inside bar quality uses its mother bar; pin-bar shape can have a small red body', () => {
  const inside = { ...candidate, type: 'inside-bar', index: 1 }
  assert.equal(entryRejection(inside, [bar, { ...bar, close: 9.2 }], 'up', 0.1, 'signal-quality'), null)
  assert.equal(
    entryRejection({ ...candidate, type: 'pin-bar' }, [{ ...bar, open: 9.9 }], 'up', 0.1, 'signal-quality'),
    null,
  )
})
