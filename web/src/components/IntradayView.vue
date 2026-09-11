<script setup>
import { displayCode } from '../display-code.js'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { state } from '../state'
import { loadIntraday } from '../intraday'
import { intradayReference } from '../intraday-levels.js'
import { minuteTLevels } from '../minute-t-levels.js'
import { useMinuteT } from '../use-minute-t.js'
import { intradayPolling, needsIntradaySnapshot } from '../intraday-polling.js'
import IntradayChart from './IntradayChart.vue'
const props = defineProps({
  symbol: { type: String, required: true },
  name: String,
  openLabel: String,
  compact: Boolean,
  fit: Boolean,
  kind: { type: String, default: 'etf' },
  reference: { type: Object, default: null },
  referenceError: String,
  tradeHistory: Object,
})
const emit = defineEmits(['open'])
const minuteT = useMinuteT(() => !props.compact && props.kind === 'etf')
const tLevels = computed(() => (props.kind === 'etf' ? minuteTLevels(minuteT.value, data.value) : []))
const root = ref(null),
  data = ref(null),
  error = ref(''),
  loading = ref(false),
  polling = ref(null),
  clockNow = ref(Date.now())
const levelState = computed(() =>
  props.referenceError
    ? { levels: [], message: '关键点位刷新失败，等待日 K 与策略重新同步' }
    : intradayReference(props.reference, data.value),
)
let observer,
  timer,
  alive = true,
  mounted = false,
  visible = !props.compact,
  loadedOnce = false,
  completedSnapshot = null,
  lastCompletedAt = 0,
  clockOffset = 0
const pollState = computed(() => intradayPolling(polling.value, clockNow.value))
const refreshDelay = computed(() => {
  if (error.value || (data.value?.refresh_seconds === 60 && data.value?.stale)) return 60000
  return (polling.value?.interval_seconds || 10) * 1000
})
function syncPolling(value) {
  if (!value || (polling.value && Date.parse(value.server_at) < Date.parse(polling.value.server_at))) return
  polling.value = value
  clockOffset = Date.parse(value.server_at) - Date.now()
  schedule()
}
watch(() => state.status.intraday_polling, syncPolling, { immediate: true })
function schedule() {
  clearTimeout(timer)
  clockNow.value = Date.now() + clockOffset
  if (!mounted || !alive || !visible || document.hidden || loading.value) return
  if (!loadedOnce || needsIntradaySnapshot(pollState.value, completedSnapshot)) {
    timer = setTimeout(load, 0)
    return
  }
  const boundary = pollState.value.nextChangeAt
  const delay = pollState.value.active
    ? Math.min(lastCompletedAt + refreshDelay.value - Date.now(), boundary - clockNow.value)
    : boundary == null
      ? null
      : boundary - clockNow.value
  // A long holiday can exceed the browser's maximum timeout. Waking locally makes no request.
  if (delay != null) timer = setTimeout(tick, Math.max(0, Math.min(delay, 2147483647)))
}
function tick() {
  clockNow.value = Date.now() + clockOffset
  if (
    needsIntradaySnapshot(pollState.value, completedSnapshot) ||
    (pollState.value.active && Date.now() >= lastCompletedAt + refreshDelay.value)
  )
    load()
  else schedule()
}
async function load(snapshotRetry = false) {
  if (!alive || !visible || document.hidden || loading.value) return
  clockNow.value = Date.now() + clockOffset
  if (
    loadedOnce &&
    !pollState.value.active &&
    !snapshotRetry &&
    !needsIntradaySnapshot(pollState.value, completedSnapshot)
  )
    return schedule()
  const requestedSnapshot = pollState.value.snapshotKey
  clearTimeout(timer)
  loading.value = true
  try {
    const result = await loadIntraday(
      props.symbol,
      () =>
        alive &&
        visible &&
        !document.hidden &&
        (!loadedOnce ||
          snapshotRetry ||
          (() => {
            const current = intradayPolling(polling.value, Date.now() + clockOffset)
            return current.active || needsIntradaySnapshot(current, completedSnapshot)
          })()),
      props.kind,
      !props.compact,
      snapshotRetry,
    )
    if (alive && result) {
      loadedOnce = true
      data.value = result
      error.value = ''
      completedSnapshot = result.snapshot_key || requestedSnapshot
      syncPolling(result.polling)
    }
  } catch {
    if (alive) {
      loadedOnce = true
      completedSnapshot = requestedSnapshot
      error.value = data.value?.points?.length ? '刷新失败，显示上次缓存' : '分时暂不可用，稍后重试'
    }
  } finally {
    if (alive) {
      loading.value = false
      lastCompletedAt = Date.now()
      schedule()
    }
  }
}
function resume() {
  schedule()
}
onMounted(() => {
  mounted = true
  if (props.compact) {
    observer = new IntersectionObserver(
      (entries) => {
        visible = entries[0].isIntersecting
        schedule()
      },
      { rootMargin: '80px' },
    )
    observer.observe(root.value)
  } else schedule()
  document.addEventListener('visibilitychange', resume)
})
onUnmounted(() => {
  alive = false
  observer?.disconnect()
  clearTimeout(timer)
  document.removeEventListener('visibilitychange', resume)
})
</script>
<template>
  <div ref="root" :class="['intraday-view', { compact, fit }]" :aria-busy="loading">
    <button
      v-if="compact"
      class="intraday-preview"
      @click="emit('open')"
      :aria-label="openLabel || `查看${displayCode(name || symbol)}分时线`"
      :title="openLabel"
    >
      <IntradayChart v-if="data?.points?.length" :data="data" compact />
      <span v-else class="intraday-placeholder">{{
        loading ? '分时加载中…' : error || data?.message || '查看详情'
      }}</span>
      <small :class="{ 'is-stale': data?.stale || error }"
        >{{ data?.day ? `${data.day.slice(5)} ${data.as_of.slice(11, 16)}` : '分钟价格'
        }}{{ data?.stale || error ? ' · 待更新' : '' }}</small
      >
    </button>
    <template v-else>
      <div class="intraday-status" role="status">
        <span :class="{ 'is-stale': data?.stale || error }">{{
          loading && !data
            ? '正在加载分时…'
            : !pollState.active
              ? data?.snapshot_key === pollState.snapshotKey
                ? data.message
                : pollState.message
              : error || (data?.stale ? data.message : pollState.message)
        }}</span>
        <button
          type="button"
          class="button secondary compact"
          @click="load(!pollState.active)"
          :disabled="loading || (!pollState.active && (!pollState.snapshotKey || data?.snapshot_complete))"
        >
          {{
            loading
              ? '加载中…'
              : pollState.active
                ? '刷新分时'
                : data?.snapshot_complete
                  ? '固定展示'
                  : '重新加载'
          }}
        </button>
      </div>
      <IntradayChart
        v-if="data?.points?.length"
        :data="data"
        :name="name"
        :fit="fit"
        :price-unit="kind === 'index' ? '点' : '元'"
        :price-digits="kind === 'index' ? 2 : 3"
        :reference-levels="levelState.levels"
        :t-levels="tLevels"
        :trade-history="tradeHistory"
      />
      <div v-else class="intraday-empty">
        {{ loading ? '正在获取分钟价格' : '暂无可用分时线'
        }}<small>{{
          pollState.active
            ? '数据缺失时保留空白，稍后自动重试。'
            : error || data?.message || '正在加载对应交易日的分时。'
        }}</small>
      </div>
      <div class="intraday-caption" v-if="data?.day">
        {{ data.day }} · 更新至 {{ data.as_of.slice(11, 16) }} ·
        <template v-if="data.fetched_at">检查 {{ data.fetched_at.slice(11, 19) }} · </template>
        {{ data.source === 'tencent' ? '腾讯行情' : '同花顺行情' }}
      </div>
      <p v-if="levelState.message" class="intraday-basis" role="status">{{ levelState.message }}</p>
      <p class="intraday-note">
        分钟数据，盘中每
        {{ polling?.interval_seconds || 10 }}
        秒检查；休市补齐一次当日分时并固定展示，开市恢复。虚线为昨收，午间休市压缩显示。
      </p>
    </template>
  </div>
</template>
<style scoped>
.intraday-preview {
  display: block;
  border: 0;
  background: transparent;
  padding: 0 3px;
  text-align: left;
  border-radius: var(--radius-control);
  width: 124px;
}
.intraday-preview:hover {
  background: var(--accent-soft);
}
.intraday-preview small {
  display: block;
  color: var(--muted);
  font-size: 9px;
  line-height: 12px;
  margin-top: 2px;
}
.intraday-placeholder {
  display: flex;
  align-items: center;
  height: 26px;
  color: var(--muted);
  font-size: 11px;
  white-space: normal;
}
.intraday-status {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 14px;
  font-size: 12px;
}
.intraday-caption,
.intraday-basis,
.intraday-note {
  font-size: 11px;
  color: var(--muted);
  line-height: 1.7;
}
.intraday-caption {
  margin-top: 12px;
}
.intraday-note,
.intraday-basis {
  margin: 5px 0 0;
}
.intraday-empty {
  display: grid;
  align-content: center;
  gap: 8px;
  text-align: center;
  min-height: 180px;
  color: var(--muted);
  font-size: 13px;
}
.intraday-empty small {
  font-size: 11px;
}
.is-stale,
.intraday-preview small.is-stale {
  color: var(--yellow);
}
.intraday-view.fit {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}
.fit .intraday-status {
  flex-shrink: 0;
  font-size: 11px;
  margin-bottom: 6px;
}
.fit .intraday-status button {
  padding: 3px 8px;
  font-size: 10px;
}
.fit .intraday-empty {
  flex: 1;
  min-height: 80px;
}
.fit .intraday-caption,
.fit .intraday-basis,
.fit .intraday-note {
  flex-shrink: 0;
  font-size: 10px;
  margin-top: 4px;
}
</style>
