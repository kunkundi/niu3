<script setup>
import { displayCode } from '../display-code.js'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, money, pct, dateTime, changeClass } from '../state'
import { STRATEGY_LEVELS, strategyLevels } from '../signal-chart.js'
import { useTradeHistory } from '../use-trade-history.js'
import { useLiveCandle } from '../use-live-candle.js'
import TradeSignalBanner from './TradeSignalBanner.vue'
import CandlestickChart from './CandlestickChart.vue'
import IntradayView from './IntradayView.vue'
import Icon from './Icon.vue'
import WorkbenchDialog from './WorkbenchDialog.vue'

const props = defineProps({ plan: Object, symbol: String, embedded: Boolean })
const emit = defineEmits(['select', 'refresh', 'ready'])
const symbol = computed(() => props.symbol || 'sh000001')
const tradeHistory = useTradeHistory(symbol)
const isIndex = computed(() => symbol.value === 'sh000001')
const selected = computed(() => props.plan?.rows?.find((row) => row.symbol === symbol.value))
const loaded = ref(null),
  loading = ref(false),
  error = ref('')
const detailsOpen = ref(false)
const chartMode = ref('daily')
const liveCandle = useLiveCandle(
  symbol,
  computed(() => chartMode.value === 'daily'),
)
const name = computed(() =>
  isIndex.value ? '上证指数' : displayCode(selected.value?.name || loaded.value?.name || symbol.value),
)
const reviewContext = ref(null)
const reviewing = computed(() => reviewContext.value?.active === true)
const sourcePa = computed(() => loaded.value?.row?.pa)
const pa = computed(() => (reviewing.value ? reviewContext.value.pa : sourcePa.value))
const chartDay = computed(() => (reviewing.value ? reviewContext.value.as_of : loaded.value?.as_of))
const isPa = computed(() => !isIndex.value && props.plan?.strategy?.startsWith('price-action-'))
const levels = computed(() => strategyLevels(sourcePa.value, loaded.value?.matched === true))
const candidateSetup = computed(() =>
  props.plan?.mode === 'post_close' &&
  selected.value?.post_close_candidate &&
  loaded.value?.matched &&
  loaded.value?.signal_id === props.plan?.id &&
  loaded.value?.symbol === symbol.value
    ? loaded.value.candidate_setup
    : null,
)
const price = (key) =>
  (reviewing.value || loaded.value?.matched) && pa.value?.[key] > 0 ? money(pa.value[key], 3) : '—'
const quote = computed(() => selected.value?.quote || loaded.value?.quote)
const lastBar = computed(() => loaded.value?.bars?.at(-1))
const closeChange = computed(() => {
  const bars = loaded.value?.bars || []
  return bars.length > 1 ? Number(bars.at(-1).close) / Number(bars.at(-2).close) - 1 : null
})
const chartChange = computed(() =>
  reviewing.value
    ? reviewContext.value.bar?.change
    : isIndex.value
      ? closeChange.value
      : quote.value?.change_pct,
)
const identity = computed(() =>
  [
    symbol.value,
    props.plan?.mode,
    props.plan?.config_id,
    props.plan?.as_of,
    selected.value?.pa?.input_sha256,
  ].join(':'),
)
let request = 0,
  active = true,
  loadedIdentity = '',
  timer
async function load() {
  const token = ++request,
    requestedSymbol = symbol.value,
    basis = identity.value
  if (basis !== loadedIdentity) {
    loaded.value = null
    reviewContext.value = null
  }
  loading.value = true
  error.value = ''
  try {
    let result
    if (isIndex.value) {
      result = await api('/indices/sh000001/chart')
    } else if (selected.value && props.plan?.id) {
      const params = new URLSearchParams({
        mode: props.plan.mode || 'auto',
        signal_id: String(props.plan.id),
      })
      result = await api(`/signals/${encodeURIComponent(requestedSymbol)}/chart?${params}`)
    } else {
      const detail = await api(`/etfs/${encodeURIComponent(requestedSymbol)}`)
      result = {
        ...detail,
        as_of: detail.bars.at(-1)?.day,
        matched: false,
        warning: '当前无对应策略信号，展示历史 K 线。',
      }
    }
    if (!active || token !== request) return
    // Preserve range/cursor state while minute-by-minute signal refreshes reuse the same candles.
    if (loaded.value && JSON.stringify(loaded.value.bars) === JSON.stringify(result.bars))
      result.bars = loaded.value.bars
    loaded.value = result
    loadedIdentity = basis
  } catch (e) {
    if (active && token === request) error.value = e.message
  } finally {
    if (active && token === request) {
      loading.value = false
      await nextTick()
      // Mobile scroll positioning must use the loaded chart's full height.
      if (active && token === request) emit('ready', requestedSymbol)
    }
  }
}
watch(() => [identity.value, props.plan?.id], load, { immediate: true })
function refreshVisible() {
  if (!document.hidden && !loading.value) load()
}
onMounted(() => {
  timer = setInterval(refreshVisible, 60000)
  document.addEventListener('visibilitychange', refreshVisible)
})
onBeforeUnmount(() => {
  active = false
  request++
  clearInterval(timer)
  document.removeEventListener('visibilitychange', refreshVisible)
})
function retry() {
  load()
  emit('refresh')
}
</script>

<template>
  <section id="selected-etf-chart" class="panel selected-chart" aria-label="分时与 K 线展示区域">
    <div class="chart-heading" data-touch-surface>
      <h2 v-if="!embedded">
        {{ name }} <small>{{ displayCode(symbol) }}</small>
      </h2>
      <div
        v-if="chartMode === 'daily' && !embedded"
        class="chart-quote"
        :title="
          reviewing
            ? `${chartDay} 收盘 · 前复权`
            : isIndex
              ? '最近完整交易日收盘点位'
              : quote
                ? dateTime(quote.at)
                : '行情待更新'
        "
      >
        <span>{{ reviewing ? '回顾收盘' : isIndex ? '收盘' : '最新' }}</span>
        <strong :class="changeClass(chartChange)">{{
          reviewing
            ? money(reviewContext.bar?.close, 3)
            : isIndex
              ? money(lastBar?.close)
              : money(quote?.last, 3)
        }}</strong>
        <b :class="changeClass(chartChange)">{{ pct(chartChange, true) }}</b>
        <span v-if="!reviewing && quote?.stale" class="amberText">已过期</span>
      </div>
      <small v-if="chartMode === 'daily' && !embedded" class="chart-date">{{
        (reviewing ? chartDay : reviewContext?.display_as_of || chartDay) || '等待数据'
      }}</small>
      <div class="chart-actions">
        <div class="chart-mode" role="group" aria-label="图表类型">
          <button
            v-for="mode in [
              { id: 'intraday', label: '分时' },
              { id: 'daily', label: '日 K' },
            ]"
            :key="mode.id"
            :class="{ active: chartMode === mode.id }"
            :aria-pressed="chartMode === mode.id"
            @click="chartMode = mode.id"
          >
            {{ mode.label }}
          </button>
        </div>
        <button
          v-if="!embedded"
          class="text-button"
          :aria-pressed="isIndex"
          @click="emit('select', 'sh000001')"
        >
          上证指数
        </button>
        <button class="text-button" @click="detailsOpen = true">详情</button>
        <button
          v-if="chartMode === 'daily'"
          class="icon-button"
          :disabled="loading"
          @click="retry"
          aria-label="刷新 K 线"
        >
          <Icon name="refresh" :size="15" />
        </button>
      </div>
    </div>
    <TradeSignalBanner
      v-if="!isIndex && !(chartMode === 'daily' && reviewing)"
      :plan="plan"
      :row="selected"
      :reference="loaded"
      :history="tradeHistory"
      :error="error"
    />
    <div
      class="selected-chart-body"
      :class="{ 'has-structure': chartMode === 'daily' && isPa && pa?.ready }"
      :aria-busy="chartMode === 'daily' && loading"
    >
      <div class="chart-column">
        <IntradayView
          v-if="chartMode === 'intraday'"
          :key="symbol"
          :symbol="symbol"
          :name="name"
          :kind="isIndex ? 'index' : 'etf'"
          :reference="loaded || {}"
          :reference-error="error"
          :trade-history="isIndex ? undefined : tradeHistory"
          fit
        />
        <p v-if="chartMode === 'daily' && error" class="chart-alert form-error" role="alert">
          {{ error }} <button class="text-button" @click="retry">重新同步</button>
        </p>
        <p v-if="chartMode === 'daily' && loaded?.warning" class="chart-alert amberText" role="status">
          {{ loaded.warning }}
        </p>
        <CandlestickChart
          v-if="loaded"
          v-show="chartMode === 'daily'"
          :key="`${symbol}:${isPa}:${plan?.mode}`"
          :points="loaded.bars"
          :buy-review="isIndex ? null : loaded.buy_review"
          :trade-history="isIndex ? undefined : tradeHistory"
          @show-intraday="chartMode = 'intraday'"
          :live-candle="liveCandle"
          :minimum-bars="loaded.minimum_bars || 120"
          @review-context="reviewContext = $event"
          :name="name"
          :preset="isPa ? 'strategy' : 'market'"
          :reference-levels="levels"
          :candidate-setup="candidateSetup"
          :levels-as-of="loaded.as_of"
          :adjustment-label="isIndex ? '指数点位' : '前复权'"
          :volume-unit="isIndex ? '手' : '份'"
          :tick-size="isIndex ? 0.01 : 0.001"
          compact
        />
        <div v-else-if="chartMode === 'daily' && loading" class="loading" role="status">
          正在加载 {{ name }} 的 K 线…
        </div>
      </div>
      <aside
        v-if="chartMode === 'daily' && isPa && pa?.ready"
        class="selected-structure"
        aria-label="策略关键价位"
      >
        <details class="structure-disclosure">
          <summary>
            策略关键价位 <span>{{ reviewing ? `回顾 ${chartDay}` : '前复权 · 展开查看' }}</span>
          </summary>
          <div class="structure-summary">
            <strong>{{ reviewing || loaded.matched ? pa.trend : '等待数据对齐' }}</strong
            ><span>{{ reviewing ? `回顾 ${chartDay}` : '近 60 日' }} · 前复权</span>
          </div>
          <dl class="structure-levels" aria-label="策略关键指标，前复权价位">
            <div v-for="item in STRATEGY_LEVELS" :key="item.id">
              <dt>{{ item.label }}</dt>
              <dd>{{ price(item.id) }}</dd>
            </div>
          </dl>
          <p class="structure-setup">
            {{ pa.setup || '暂无新入场形态'
            }}<span v-if="pa.reward_risk != null"> · 盈亏比 {{ money(pa.reward_risk) }}</span>
          </p>
          <p v-if="!reviewing && loaded.position_reference?.stop" class="position-stop">
            持仓保护（交易价）<b>{{ money(loaded.position_reference.stop, 3) }}</b>
          </p>
        </details>
      </aside>
    </div>
    <WorkbenchDialog v-model:open="detailsOpen" :title="`${name} · 数据与策略依据`">
      <p>日 K 基准 {{ chartDay || '—' }} · 已加载 {{ loaded?.bars.length ?? 0 }} 根 · 默认显示 120 根</p>
      <p v-if="isIndex">
        来源：{{ loaded?.source || '腾讯财经' }} · 指数点位，成交量单位为手，仅作市场观察。
      </p>
      <template v-else>
        <p>日 K 为前复权价格，分时价位为实际交易价格。</p>
        <p v-if="quote">行情时间 {{ dateTime(quote.at) }}{{ quote.stale ? ' · 已过期' : '' }}</p>
        <p v-if="(reviewing || loaded?.matched) && pa?.background">
          {{ pa.history_bars }} 日背景：{{ pa.background.trend }} · 支撑
          {{ money(pa.background.support, 3) }} · 压力 {{ money(pa.background.resistance, 3) }}。
        </p>
        <p v-if="pa?.signal_day">
          形态可知日 {{ pa.signal_day }}；近期支撑、压力与摆动失效取最近 60 根，目标同时参考较长历史压力。
        </p>
        <p v-if="pa?.ready">
          {{ pa.setup || '暂无新入场形态'
          }}<span v-if="pa.reward_risk != null"> · 盈亏比 {{ money(pa.reward_risk) }}</span>
        </p>
        <p v-if="!reviewing && loaded?.position_reference?.stop">
          持仓保护价（交易价口径）：{{ money(loaded.position_reference.stop, 3) }}
        </p>
        <p>
          {{
            reviewing
              ? `结构与价位重算截至 ${chartDay}，不使用后续 K 线。`
              : loaded?.row?.reasons?.join('；') || '暂无对应策略信号'
          }}
        </p>
        <RouterLink :to="{ path: '/market', query: { symbol } }">查看完整行情与历史 →</RouterLink>
      </template>
      <p>
        日 K 使用已完成交易日，每 60 秒检查更新；分时按“数据与运行”中设置的间隔获取，默认 10 秒。
        收盘、午休和非交易日停止轮询；补齐一次对应交易日分时后固定展示，开市自动恢复。盘中失败后 60 秒重试。
      </p>
    </WorkbenchDialog>
  </section>
</template>

<style scoped>
.selected-chart {
  container-type: inline-size;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  margin: 0;
  overflow: hidden;
}
.chart-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px;
  flex-shrink: 0;
  min-height: 34px;
  padding: 5px 10px;
  border-bottom: 1px solid var(--line);
}
.chart-heading h2 {
  font-size: 13px;
  white-space: nowrap;
}
.chart-heading h2 small {
  margin-left: 6px;
  font-size: 10px;
  font-weight: 400;
}
.chart-quote {
  display: flex;
  align-items: baseline;
  gap: 7px;
  font-size: 11px;
  white-space: nowrap;
}
.chart-quote > span {
  color: var(--muted);
  font-size: 10px;
}
.chart-quote strong {
  font: 17px var(--font-numeric);
}
.chart-quote b {
  font: 11px var(--font-numeric);
}
.chart-date {
  color: var(--muted);
  font-size: 10px;
}
.chart-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-left: auto;
  white-space: nowrap;
}
.chart-mode {
  display: flex;
  gap: 2px;
  padding: 2px;
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
}
.chart-mode button {
  border: 0;
  border-radius: 3px;
  background: transparent;
  color: var(--muted);
  padding: 3px 9px;
  font-size: 11px;
  line-height: 16px;
}
.chart-mode button.active {
  color: var(--accent);
  background: var(--accent-soft);
  font-weight: 600;
}
.chart-actions .text-button {
  font-size: 10px;
}
.chart-actions .icon-button {
  width: 24px;
  height: 24px;
  min-height: 0;
}
.selected-chart-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  padding: 5px 8px;
  gap: 10px;
}
.selected-chart-body.has-structure {
  grid-template-rows: minmax(min-content, 1fr) auto;
  overflow-y: auto;
}
.chart-column {
  min-width: 0;
  min-height: min-content;
  display: flex;
  flex-direction: column;
}
.chart-column > :deep(.kline-compact) {
  min-height: min-content;
}
.chart-alert {
  flex-shrink: 0;
  margin: 0;
  font-size: 10px;
  line-height: 1.4;
  max-height: 30px;
  overflow: auto;
}
.selected-structure {
  border-top: 1px solid var(--line);
  padding-top: 4px;
  min-height: min-content;
  overflow: visible;
}
.structure-disclosure > summary {
  cursor: pointer;
  padding: 5px 2px;
  color: var(--text-secondary);
  font-size: 11px;
}
.structure-disclosure > summary span {
  color: var(--muted);
  margin-left: 10px;
  font-size: 10px;
}
.structure-summary {
  display: flex;
  justify-content: space-between;
  gap: 6px;
  align-items: center;
  font-size: 11px;
  margin: 0 0 5px;
}
.structure-summary span {
  color: var(--muted);
  font-size: 9px;
}
.structure-levels {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 5px;
  margin: 0;
}
.structure-levels > div {
  background: var(--panel2);
  border: 1px solid var(--line);
  padding: 7px;
  border-radius: 3px;
}
.structure-levels dt {
  color: var(--muted);
  font-size: 10px;
}
.structure-levels dd {
  margin: 4px 0 0;
  font: 15px var(--font-numeric);
}
.structure-setup,
.position-stop {
  font-size: 10px;
  line-height: 1.6;
  margin: 8px 0 0;
  color: var(--muted);
}
.position-stop b {
  margin-left: 6px;
  color: var(--text-secondary);
}
@container (max-width: 520px) {
  .chart-heading {
    gap: 5px 8px;
  }
  .chart-heading h2 small,
  .chart-date {
    display: none;
  }
  .structure-levels > div {
    padding: 5px 3px;
  }
  .structure-levels dd {
    font-size: 12px;
  }
}
@media (max-width: 900px) {
  .selected-chart-body {
    flex: none;
  }
  .selected-chart-body.has-structure {
    grid-template-rows: auto auto;
  }
  .selected-structure {
    overflow: visible;
  }
  .chart-column > :deep(.intraday-view.fit) {
    flex: none;
  }
  .chart-column :deep(.intraday-chart.fit .intraday-plot-area) {
    flex: none;
    height: 240px;
  }
}
@media (max-width: 700px) {
  .chart-heading {
    gap: 5px 8px;
    padding: 4px 7px;
    flex-wrap: wrap;
  }
  .chart-heading h2 {
    font-size: 11px;
  }
  .chart-heading h2 small,
  .chart-date {
    display: none;
  }
  .chart-quote strong {
    font-size: 14px;
  }
  .chart-quote > span:not(.amberText) {
    display: none;
  }
  .chart-actions {
    gap: 8px;
  }
  .selected-chart-body {
    padding: 4px;
    gap: 4px;
  }
  .selected-chart-body.has-structure {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: auto auto;
  }
  .selected-structure {
    border-left: 0;
    border-top: 1px solid var(--line);
    padding: 4px 0 0;
    overflow: visible;
  }
  .structure-summary {
    margin: 0 0 3px;
    font-size: 10px;
  }
  .structure-levels {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 4px;
  }
  .structure-levels > div {
    padding: 5px 7px;
  }
  .structure-levels dt {
    font-size: 10px;
    white-space: nowrap;
  }
  .structure-levels dd {
    font-size: 13px;
  }
  .structure-setup,
  .position-stop {
    display: none;
  }
}
@media (max-width: 700px) {
  .chart-heading {
    padding: 8px 10px;
    gap: 6px 10px;
  }
  .chart-heading h2 {
    flex-basis: 100%;
    min-width: 0;
    overflow-wrap: anywhere;
    font-size: 14px;
    white-space: normal;
  }
  .chart-heading h2 small {
    display: inline;
    font-size: 10px;
    white-space: nowrap;
  }
  .chart-quote strong {
    font-size: 24px;
  }
  .chart-quote b {
    font-size: 14px;
  }
  .chart-quote > span:not(.amberText) {
    display: inline;
    font-size: 10px;
  }
  .chart-actions {
    flex-basis: 100%;
    min-width: 0;
    flex-wrap: wrap;
    gap: 4px;
    margin-left: 0;
  }
  .chart-mode {
    margin-right: auto;
    flex-shrink: 0;
  }
  .chart-actions .text-button {
    min-width: var(--mobile-control-height);
    min-height: var(--mobile-control-height);
    font-size: 11px;
  }
  .chart-actions .icon-button {
    width: var(--mobile-control-height);
    height: var(--mobile-control-height);
  }
  .chart-mode button {
    min-height: 32px;
    min-width: 36px;
    font-size: 12px;
    padding: 4px 8px;
  }
  .structure-levels dd {
    font-size: 18px;
  }
  .structure-levels dt {
    font-size: 11px;
  }
  .position-stop {
    display: block;
    font-size: 12px;
  }
}
</style>
