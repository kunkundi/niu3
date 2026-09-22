import test from 'node:test'
import assert from 'node:assert/strict'
import { api, refresh, state, completeAuthentication, cancelAuthentication } from '../src/state.js'

test('plain text and HTML server failures show HTTP errors instead of JSON parser errors', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch')
  for (const [status, body] of [
    [500, 'Internal Server Error'],
    [502, '<html><body>Bad Gateway</body></html>'],
    [503, ''],
    [504, 'Gateway Timeout'],
  ]) {
    fetch.mock.mockImplementation(async () => new Response(body, { status }))
    await assert.rejects(api('/status'), {
      name: 'Error',
      message: `服务暂时不可用（HTTP ${status}），请稍后重试`,
    })
  }
})

test('JSON responses and server detail messages remain usable', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => Response.json({ ready: true }))
  assert.deepEqual(await api('/status'), { ready: true })
  fetch.mock.mockImplementation(async () => Response.json({ detail: '服务数据暂时不可用' }, { status: 503 }))
  await assert.rejects(api('/status'), { message: '服务数据暂时不可用' })
  fetch.mock.mockImplementation(async () => Response.json(null, { status: 500 }))
  await assert.rejects(api('/status'), { message: '服务暂时不可用（HTTP 500），请稍后重试' })
  fetch.mock.mockImplementation(async () => new Response(null, { status: 204 }))
  assert.equal(await api('/auth/logout', { method: 'POST' }), null)
})

test('an expired settings session preserves the public account', async (t) => {
  state.authenticated = true
  state.account = { cash: 100 }
  t.mock.method(globalThis, 'fetch', async () => new Response('Unauthorized', { status: 401 }))
  await assert.rejects(api('/config'), { message: '登录已失效，请重新登录' })
  assert.equal(state.authenticated, false)
  assert.deepEqual(state.account, { cash: 100 })
})

test('public account data refreshes before login and after the settings session expires', async (t) => {
  state.authenticated = false
  state.account = null
  const fetch = t.mock.method(globalThis, 'fetch', async (url) =>
    Response.json(url.endsWith('/status') ? { ready: true } : { cash: 100 }),
  )
  await refresh()
  assert.deepEqual(state.status, { ready: true })
  assert.deepEqual(state.account, { cash: 100 })
  assert.equal(state.error, '')
  assert.deepEqual(
    fetch.mock.calls.map((call) => call.arguments[0]),
    ['/api/v1/status', '/api/v1/account'],
  )
})

test('all changes wait for authentication; cancelling never sends the change', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => Response.json({ ok: true }))
  t.after(cancelAuthentication)
  for (const [path, method] of [
    ['/etfs', 'POST'],
    ['/etfs/sh510300', 'DELETE'],
    ['/config', 'PATCH'],
    ['/notifications/config', 'PATCH'],
    ['/notifications/test/feishu', 'POST'],
    ['/auth/password', 'POST'],
  ]) {
    state.authenticated = false
    const pending = api(path, { method })
    const rejected = assert.rejects(pending, { message: '已取消操作，未进行更改' })
    assert.equal(state.authRequested, true)
    assert.equal(fetch.mock.callCount(), 0)
    cancelAuthentication()
    await rejected
    assert.equal(state.authRequested, false)
    assert.equal(fetch.mock.callCount(), 0)
  }
})

test('successful verification continues the original change once with its original payload', async (t) => {
  state.authenticated = false
  const fetch = t.mock.method(globalThis, 'fetch', async () => Response.json({ ok: true }))
  t.after(cancelAuthentication)
  const options = { method: 'POST', body: JSON.stringify({ code: '510500' }) }
  const pending = api('/etfs', options)
  assert.equal(fetch.mock.callCount(), 0)
  completeAuthentication()
  assert.deepEqual(await pending, { ok: true })
  assert.equal(fetch.mock.callCount(), 1)
  assert.equal(fetch.mock.calls[0].arguments[0], '/api/v1/etfs')
  assert.equal(fetch.mock.calls[0].arguments[1].body, options.body)
  assert.equal(state.authRequested, false)
})

test('a wrong password keeps the change pending and is never sent as part of the change', async (t) => {
  state.authenticated = false
  const fetch = t.mock.method(globalThis, 'fetch', async () =>
    Response.json({ detail: '密码或管理密钥不正确' }, { status: 401 }),
  )
  t.after(cancelAuthentication)
  const pending = api('/etfs', { method: 'POST', body: JSON.stringify({ code: '510500' }) })
  const rejected = assert.rejects(pending, { message: '已取消操作，未进行更改' })
  await assert.rejects(api('/auth/login', { method: 'POST', body: JSON.stringify({ password: 'bad' }) }))
  assert.equal(state.authRequested, true)
  assert.deepEqual(
    fetch.mock.calls.map((call) => call.arguments[0]),
    ['/api/v1/auth/login'],
  )
  cancelAuthentication()
  await rejected
})

test('an expired session asks for verification before retrying a rejected change', async (t) => {
  state.authenticated = true
  const fetch = t.mock.method(globalThis, 'fetch', async () =>
    Response.json({ detail: '请先验证管理密码后再进行变更操作' }, { status: 401 }),
  )
  t.after(cancelAuthentication)
  const pending = api('/config', { method: 'PATCH', body: JSON.stringify({ max_weight: '.04' }) })
  await new Promise(setImmediate)
  assert.equal(state.authenticated, false)
  assert.equal(state.authRequested, true)
  assert.equal(fetch.mock.callCount(), 1)
  fetch.mock.mockImplementation(async () => Response.json({ version: 2 }))
  completeAuthentication()
  assert.deepEqual(await pending, { version: 2 })
  assert.equal(fetch.mock.callCount(), 2)
})

test('an uncertain server failure never automatically repeats a change', async (t) => {
  state.authenticated = true
  const fetch = t.mock.method(globalThis, 'fetch', async () => new Response('', { status: 503 }))
  await assert.rejects(api('/etfs', { method: 'POST', body: JSON.stringify({ code: '510500' }) }))
  assert.equal(fetch.mock.callCount(), 1)
  assert.equal(state.authRequested, false)
})

test('invalid successful bodies are rejected without displaying raw response text', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => new Response('<html>private proxy detail</html>'))
  await assert.rejects(api('/status'), { message: '服务返回的数据格式异常，请刷新后重试' })
})

test('timeouts during response decoding remain abort errors', async (t) => {
  const aborted = new DOMException('Aborted', 'AbortError')
  t.mock.method(globalThis, 'fetch', async () => ({
    status: 200,
    ok: true,
    json: async () => {
      throw aborted
    },
  }))
  await assert.rejects(api('/status'), (error) => error === aborted)
})
