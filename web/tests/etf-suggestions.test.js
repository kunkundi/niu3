import assert from 'node:assert/strict'
import test from 'node:test'
import { createSuggestionLoader, etfInputCode, fullEtfCode } from '../src/etf-suggestions.js'
const turn = () => new Promise((resolve) => setTimeout(resolve, 5))
const deferred = () => {
  let resolve, reject
  const promise = new Promise((ok, fail) => {
    resolve = ok
    reject = fail
  })
  return { promise, resolve, reject }
}

test('typing debounce sends only the latest query and keeps loading distinct from empty results', async () => {
  const calls = [],
    states = []
  const loader = createSuggestionLoader(
    async (query) => {
      calls.push(query)
      return { items: [] }
    },
    (s) => states.push(s),
    0,
  )
  loader.update('5')
  loader.update('51')
  loader.update('510')
  assert.equal(states.at(-1).loading, true)
  await turn()
  assert.deepEqual(calls, ['510'])
  assert.deepEqual(states.at(-1), { items: [], loading: false, error: '' })
  loader.dispose()
})

test('older success and failure cannot overwrite a newer query, even during its debounce', async () => {
  for (const failure of [false, true]) {
    const old = deferred(),
      next = deferred(),
      states = []
    const loader = createSuggestionLoader(
      (q) => (q === '煤炭' ? old.promise : next.promise),
      (s) => states.push(s),
      0,
    )
    loader.update('煤炭')
    await turn()
    loader.update('银行')
    if (failure) old.reject(new Error('old failure'))
    else old.resolve({ items: [{ symbol: 'old' }] })
    await turn()
    assert.deepEqual(states.at(-1), { items: [], loading: true, error: '' })
    next.resolve({ items: [{ symbol: 'new' }] })
    await turn()
    assert.deepEqual(states.at(-1).items, [{ symbol: 'new' }])
    loader.dispose()
  }
})

test('clearing, choosing or unmounting prevents an in-flight lookup from reopening results', async () => {
  for (const action of ['clear', 'cancel', 'dispose']) {
    const request = deferred(),
      states = []
    const loader = createSuggestionLoader(
      () => request.promise,
      (s) => states.push(s),
      0,
    )
    loader.update('沪深')
    await turn()
    if (action === 'clear') loader.update('')
    else loader[action]()
    const count = states.length
    request.resolve({ items: [{ symbol: 'late' }] })
    await turn()
    assert.equal(states.length, count)
    loader.dispose()
  }
})

test('shows lookup failure and permits only complete ETF codes for direct add', async () => {
  let state
  const loader = createSuggestionLoader(
    async () => {
      throw new Error('资料源暂时不可用')
    },
    (s) => (state = s),
    0,
  )
  loader.update('新ETF')
  await turn()
  assert.equal(state.error, '资料源暂时不可用')
  assert.equal(state.loading, false)
  for (const code of ['510300', '159915', 'SH510300', ' sz159915 ']) assert.equal(fullEtfCode(code), true)
  for (const code of ['沪深300', '510', 'SH159915', '600000', '000001', ''])
    assert.equal(fullEtfCode(code), false)
  loader.dispose()
})

test('add accepts restored suggestion labels while submitting only their validated code', () => {
  for (const label of [
    '科创半导体ETF华夏 (SH588170)',
    ' 科创半导体ETF华夏 （SH588170） ',
    '科创半导体ETF华夏 (sh588170)',
  ])
    assert.equal(etfInputCode(label), 'sh588170')
  assert.equal(etfInputCode('沪深300ETF嘉实 (SZ159919)'), 'sz159919')
  assert.equal(etfInputCode('沪深300ETF嘉实 (159919)'), '159919')
  assert.equal(etfInputCode('科创半导体ETF华夏（588170）'), '')
  assert.equal(etfInputCode('科创半导体ETF华夏 （588170）'), '588170')
  assert.equal(etfInputCode('588170'), '588170')
  assert.equal(etfInputCode(' SH588170 '), 'sh588170')
  for (const value of [
    '科创半导体ETF华夏',
    '沪深300ETF',
    '588',
    '科创半导体ETF华夏 (SZ588170)',
    '股票 (SH600000)',
    'ETF (SH588170) 其他文字',
    '',
  ])
    assert.equal(etfInputCode(value), '')
})
