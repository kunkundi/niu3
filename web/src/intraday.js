import { api } from './state.js'

// Bound initial requests when several visible rows mount at once.
const queue = []
let running = 0
function drain() {
  while (running < 3 && queue.length) {
    const { symbol, kind, current, snapshotRetry, day, resolve, reject } = queue.shift()
    if (!current()) {
      resolve(null)
      continue
    }
    running++
    const query = new URLSearchParams()
    if (snapshotRetry) query.set('snapshot_retry', 'true')
    if (day) query.set('day', day)
    api(
      `/${kind === 'index' ? 'indices' : 'etfs'}/${encodeURIComponent(symbol)}/intraday${query.size ? `?${query}` : ''}`,
      {
        timeoutMs: 45000,
      },
    )
      .then(resolve, reject)
      .finally(() => {
        running--
        drain()
      })
  }
}
export function loadIntraday(
  symbol,
  current,
  kind = 'etf',
  priority = false,
  snapshotRetry = false,
  day = null,
) {
  return new Promise((resolve, reject) => {
    const request = { symbol, kind, current, snapshotRetry, day, resolve, reject }
    // An opened chart should not wait behind an entire screen of small previews.
    if (priority) queue.unshift(request)
    else queue.push(request)
    drain()
  })
}
