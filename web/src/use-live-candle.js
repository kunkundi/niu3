import { onMounted, onBeforeUnmount, reactive, watch } from 'vue'
import { api } from './state'

export function useLiveCandle(symbol, enabled) {
  const data = reactive({ bar: null, message: '', loading: false })
  let request = 0,
    alive = true,
    timer
  async function refresh() {
    if (!enabled.value || !symbol.value || symbol.value === 'sh000001' || document.hidden) return
    const id = ++request,
      current = symbol.value
    data.loading = true
    try {
      const result = await api(`/etfs/${encodeURIComponent(current)}/live-candle`)
      if (!alive || id !== request || current !== symbol.value) return
      if (data.bar && result.bar && result.bar.at < data.bar.at) return
      data.bar = result.bar
      data.message = result.message
    } catch {
      if (alive && id === request) {
        if (data.bar) data.bar = { ...data.bar, stale: true }
        data.message = '实时 K 线更新失败，等待重试'
      }
    } finally {
      if (alive && id === request) data.loading = false
    }
  }
  watch(
    symbol,
    () => {
      request++
      data.bar = null
      data.message = ''
      data.loading = false
      refresh()
    },
    { immediate: true },
  )
  watch(enabled, () => {
    request++
    data.loading = false
    refresh()
  })
  function refreshVisible() {
    if (!data.loading) refresh()
  }
  onMounted(() => {
    timer = setInterval(refreshVisible, 10000)
    document.addEventListener('visibilitychange', refreshVisible)
  })
  onBeforeUnmount(() => {
    alive = false
    request++
    clearInterval(timer)
    document.removeEventListener('visibilitychange', refreshVisible)
  })
  return data
}
