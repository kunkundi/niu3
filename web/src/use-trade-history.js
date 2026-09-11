import { onMounted, onBeforeUnmount, reactive, watch } from 'vue'
import { api } from './state'
export function useTradeHistory(symbol) {
  const history = reactive({ items: [], total: 0, loading: false, error: '', updatedAt: null })
  let request = 0,
    alive = true,
    timer
  async function load() {
    const id = ++request,
      current = symbol.value
    if (!current || current === 'sh000001') {
      history.loading = false
      return
    }
    history.loading = true
    try {
      const data = await api(`/trades?symbol=${encodeURIComponent(current)}&limit=200`)
      if (!alive || id !== request) return
      history.items = data.items.filter((item) => item.symbol === current)
      history.total = data.total
      history.error = ''
      history.updatedAt = new Date().toISOString()
    } catch {
      if (alive && id === request) history.error = '成交记录更新失败，已显示的记录为上次缓存'
    } finally {
      if (alive && id === request) history.loading = false
    }
  }
  watch(
    symbol,
    () => {
      history.items = []
      history.total = 0
      history.error = ''
      history.updatedAt = null
      load()
    },
    { immediate: true },
  )
  function refresh() {
    if (!document.hidden && !history.loading) load()
  }
  onMounted(() => {
    timer = setInterval(refresh, 30000)
    document.addEventListener('visibilitychange', refresh)
  })
  onBeforeUnmount(() => {
    alive = false
    request++
    clearInterval(timer)
    document.removeEventListener('visibilitychange', refresh)
  })
  return history
}
