<script setup>
import { displayCode } from '../display-code.js'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { state } from '../state'
import { loadIntraday } from '../intraday.js'
import { exchangeTime } from '../trade-observation.js'
import { intradayPolling, needsIntradaySnapshot } from '../intraday-polling.js'
import { tradeIntradayDays } from '../minute-t-levels.js'
import TradeIntradayChart from './TradeIntradayChart.vue'
import WorkbenchDialog from './WorkbenchDialog.vue'

const props = defineProps({ security: { type: Object, required: true }, minuteT: Object })
const emit = defineEmits(['select'])
const expanded = ref(false)
const chosenDay = ref(null),
  data = ref(null),
  error = ref(''),
  loading = ref(false)
const days = computed(() => tradeIntradayDays(props.security))
const day = computed(() => chosenDay.value || days.value[0])
const symbol = computed(() => props.security.symbol)
let active = true,
  request = 0,
  timer,
  snapshot = null
async function load() {
  const token = ++request,
    requestedDay = day.value,
    requestedSymbol = symbol.value
  loading.value = true
  try {
    const result = await loadIntraday(
      requestedSymbol,
      () => active && token === request && !document.hidden,
      'etf',
      false,
      false,
      requestedDay,
    )
    if (!result || !active || token !== request) return
    if (result.symbol !== requestedSymbol || result.day !== requestedDay) throw new Error('date mismatch')
    data.value = result
    error.value = ''
    snapshot = result.polling?.snapshot_key
  } catch {
    if (active && token === request)
      error.value = data.value ? '更新失败，显示已读取分时' : '该日分时暂不可用'
  } finally {
    if (active && token === request) loading.value = false
  }
}
watch(
  () => [symbol.value, day.value],
  () => {
    data.value = null
    error.value = ''
    snapshot = null
    load()
  },
  { immediate: true },
)
function refresh() {
  if (document.hidden || loading.value) return
  if (!data.value) return load()
  if (day.value !== exchangeTime(new Date().toISOString())?.day) return
  const polling = intradayPolling(state.status.intraday_polling, Date.now())
  if (polling.active || needsIntradaySnapshot(polling, snapshot)) load()
}
onMounted(() => {
  timer = setInterval(refresh, 30000)
  document.addEventListener('visibilitychange', refresh)
})
onUnmounted(() => {
  active = false
  request++
  clearInterval(timer)
  document.removeEventListener('visibilitychange', refresh)
})
</script>

<template>
  <TradeIntradayChart
    :security="security"
    :day="day"
    :data="data"
    :minute-t="minuteT"
    :loading="loading"
    :error="error"
    @update:day="chosenDay = $event"
    @select="emit('select', $event)"
    @expand="expanded = true"
    @retry="load"
  />
  <WorkbenchDialog
    v-model:open="expanded"
    class="trade-intraday-dialog"
    :title="`${displayCode(security.name)} · 分时买卖点`"
  >
    <TradeIntradayChart
      v-if="expanded"
      :security="security"
      :day="day"
      :data="data"
      :minute-t="minuteT"
      :loading="loading"
      :error="error"
      expanded
      @update:day="chosenDay = $event"
      @select="emit('select', $event)"
      @retry="load"
    />
  </WorkbenchDialog>
</template>

<style scoped>
@media (min-width: 701px) {
  .trade-intraday-dialog {
    width: min(1200px, calc(100vw - 32px));
  }
}
</style>
