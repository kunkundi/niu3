import assert from 'node:assert/strict'
import test from 'node:test'
import { loadIntraday } from '../src/intraday.js'

test('opened charts bypass queued previews while requests remain bounded and hidden work is skipped', async () => {
  const previousFetch = globalThis.fetch
  const requests = []
  globalThis.fetch = (url) => new Promise((resolve) => requests.push({ url, resolve }))
  const settle = () => new Promise(setImmediate)
  const respond = (request) => request.resolve(new Response(JSON.stringify({ points: [] }), { status: 200 }))
  const pending = []
  try {
    for (const symbol of ['sh510300', 'sh510500', 'sh510050']) pending.push(loadIntraday(symbol, () => true))
    pending.push(loadIntraday('sh512000', () => true))
    const hidden = loadIntraday('sh512100', () => false)
    pending.push(hidden)
    pending.push(loadIntraday('sh000001', () => true, 'index', true))
    assert.equal(requests.length, 3)
    respond(requests[0])
    await settle()
    assert.equal(requests.length, 4)
    assert.equal(requests[3].url, '/api/v1/indices/sh000001/intraday')
    respond(requests[1])
    await settle()
    assert.equal(requests[4].url, '/api/v1/etfs/sh512000/intraday')
    respond(requests[2])
    await settle()
    assert.equal(await hidden, null)
    assert.equal(requests.length, 5)
  } finally {
    for (const request of requests) respond(request)
    await Promise.allSettled(pending)
    globalThis.fetch = previousFetch
  }
})
