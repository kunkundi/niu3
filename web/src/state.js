import { reactive } from 'vue'
import { displayCodeText } from './display-code.js'
export const state = reactive({
  authenticated: false,
  authRequested: false,
  authNotice: '',
  checked: false,
  status: {},
  account: null,
  toast: '',
  error: '',
})
let toastTimer
let pendingAuthentication, resolveAuthentication, rejectAuthentication
export function requestAuthentication() {
  if (state.authenticated) return Promise.resolve()
  if (!pendingAuthentication) {
    state.authRequested = true
    pendingAuthentication = new Promise((resolve, reject) => {
      resolveAuthentication = resolve
      rejectAuthentication = reject
    })
  }
  return pendingAuthentication
}
export function completeAuthentication() {
  state.authenticated = true
  state.authNotice = ''
  settleAuthentication(true)
}
export function cancelAuthentication() {
  settleAuthentication(false)
}
function settleAuthentication(success) {
  const resolve = resolveAuthentication,
    reject = rejectAuthentication
  pendingAuthentication = resolveAuthentication = rejectAuthentication = null
  state.authRequested = false
  if (success) resolve?.()
  else reject?.(new Error('已取消操作，未进行更改'))
}
export function notify(message) {
  state.toast = displayCodeText(message)
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => {
    state.toast = ''
  }, 5000)
}
export async function api(path, options = {}) {
  const mutation =
    !['GET', 'HEAD', 'OPTIONS'].includes((options.method || 'GET').toUpperCase()) &&
    !['/auth/login', '/auth/logout'].includes(path)
  if (mutation) await requestAuthentication()
  try {
    return await sendRequest(path, options)
  } catch (error) {
    if (!mutation || error.status !== 401) throw error
    // A rejected session has made no change. Verify again before retrying once.
    await requestAuthentication()
    return sendRequest(path, options)
  }
}
async function sendRequest(path, options) {
  const { timeoutMs = 15000, ...requestOptions } = options
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`/api/v1${path}`, {
      ...requestOptions,
      signal: controller.signal,
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-NiuNo3-Request': '1',
        ...options.headers,
      },
    })
    if (response.status === 401) {
      state.authenticated = false
    }
    if (response.status === 204) return null
    let data
    try {
      data = await response.json()
    } catch (error) {
      // Proxies and failed servers may send plain text or HTML instead of JSON.
      // Keep transport failures (including timeouts) distinct from malformed bodies.
      if (!(error instanceof SyntaxError)) throw error
      if (response.ok) throw new Error('服务返回的数据格式异常，请刷新后重试')
    }
    if (!response.ok) {
      const fallback =
        response.status === 401
          ? '登录已失效，请重新登录'
          : response.status >= 500
            ? `服务暂时不可用（HTTP ${response.status}），请稍后重试`
            : `请求未完成（HTTP ${response.status}），请检查输入或稍后重试`
      throw Object.assign(
        new Error(
          displayCodeText(typeof data?.detail === 'string' && data.detail.trim() ? data.detail : fallback),
        ),
        { status: response.status },
      )
    }
    return data
  } finally {
    clearTimeout(timer)
  }
}
export async function refresh() {
  try {
    const status = await api('/status')
    state.status = status
    state.account = await api('/account')
    state.error = ''
  } catch (e) {
    state.error = e.name === 'AbortError' ? '服务响应超时，正在等待恢复' : e.message
  }
}
export const money = (value, digits = 2) =>
  value == null || value === '' || !Number.isFinite(Number(value))
    ? '—'
    : Number(value).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
export const pct = (value, signed = false) =>
  value == null || value === '' || !Number.isFinite(Number(value))
    ? '—'
    : `${signed && Number(value) > 0 ? '+' : ''}${(Number(value) * 100).toFixed(2)}%`
export const signedMoney = (value, digits = 2) =>
  `${Number.isFinite(Number(value)) && Number(value) > 0 ? '+' : ''}${money(value, digits)}`
export const changeClass = (value) => (Number(value) > 0 ? 'up' : Number(value) < 0 ? 'down' : '')
export const amount = (value) =>
  value == null
    ? '—'
    : Number(value) >= 1e8
      ? `${money(Number(value) / 1e8)} 亿`
      : `${money(Number(value) / 1e4)} 万`
export const dateTime = (value) => (value ? value.slice(0, 19).replace('T', ' ') : '—')
export const categories = {
  equity: '股票 ETF',
  bond: '债券 ETF',
  commodity: '商品 ETF',
  money: '货币 ETF',
  cross_border: '跨境 ETF',
  unknown: '待分类',
}
export const orderStatuses = {
  pending: '等待成交',
  partial: '部分成交',
  filled: '已成交',
  cancelled: '已撤销',
  expired: '已失效',
  rejected: '已拒绝',
}
