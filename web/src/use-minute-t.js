import { onMounted, onUnmounted, ref } from 'vue'
import { api } from './state'

export function useMinuteT(enabled = () => true) {
  const data = ref(null)
  let active = true,
    loading = false,
    timer
  async function load() {
    if (loading || document.hidden || !enabled()) return
    loading = true
    try {
      const result = await api('/t-strategy')
      if (active) data.value = result
    } catch {
      // Do not present a cached reference as the current trading condition.
      if (active) data.value = null
    } finally {
      loading = false
    }
  }
  onMounted(() => {
    load()
    timer = setInterval(load, 15000)
    document.addEventListener('visibilitychange', load)
  })
  onUnmounted(() => {
    active = false
    clearInterval(timer)
    document.removeEventListener('visibilitychange', load)
  })
  return data
}
