<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { money, pct, amount, changeClass } from '../state'
import {
  prepareCandles,
  candleWindow,
  MA_PERIODS,
  DEFAULT_INDICATORS,
  loadIndicators,
  saveIndicators,
} from '../candles'
import { PA_OPTIONS } from '../price-action/index.js'
import PriceActionOverlay from './PriceActionOverlay.vue'
import PriceActionSettings from './PriceActionSettings.vue'
import PriceActionSummary from './PriceActionSummary.vue'
import BuyPointReview from './BuyPointReview.vue'
import CandidateSetup from './CandidateSetup.vue'
import Icon from './Icon.vue'
import Empty from './Empty.vue'
import WorkbenchDialog from './WorkbenchDialog.vue'
import { visibleStrategyLevels, priceLineLayout, strategyLevels } from '../signal-chart.js'
import { prepareChartReview, reviewMarkersAsOf } from '../chart-review.js'
import TradeMarkers from './TradeMarkers.vue'
import TradeLegend from './TradeLegend.vue'
import TradeTape from './TradeTape.vue'
import PendingDailyTrades from './PendingDailyTrades.vue'
import { exchangeTime, tradeGroups } from '../trade-observation.js'
import { mergeLiveCandle } from '../live-candle.js'
import { DAILY_VISIBLE_BARS } from '../price-action/daily-policy.js'
import { candleIndexAt, candlePlotLayout } from '../chart-layout.js'
import { useMobile } from '../mobile'
import { createChartPointer } from '../chart-pointer'
const mobile = useMobile()

const props = defineProps({
  points: { type: Array, default: () => [] },
  name: String,
  preset: { type: String, default: 'market' },
  referenceLevels: { type: Array, default: () => [] },
  levelsAsOf: String,
  adjustmentLabel: { type: String, default: '前复权' },
  volumeUnit: { type: String, default: '份' },
  tickSize: { type: Number, default: 0.001 },
  compact: Boolean,
  buyReview: Object,
  candidateSetup: Object,
  tradeHistory: Object,
  liveCandle: Object,
  minimumBars: { type: Number, default: 120 },
})
const emit = defineEmits(['review-context', 'show-intraday'])
const replay = ref(false)
const completed = computed(() => prepareCandles(props.points))
const displayPoints = computed(() =>
  replay.value ? props.points : mergeLiveCandle(props.points, props.liveCandle?.bar),
)
const prepared = computed(() => prepareCandles(displayPoints.value))
const count = ref(mobile.value ? 30 : DAILY_VISIBLE_BARS),
  customRange = ref(false),
  end = ref(null),
  selectedDay = ref(null)
// Resize the default window only; an explicit range choice survives rotation.
watch(mobile, (value) => {
  if (!customRange.value) count.value = value ? 30 : DAILY_VISIBLE_BARS
})
const mobileStructure = ref(false)
let storage
try {
  storage = window.localStorage
} catch {
  // Browser storage may be disabled.
}
const indicators = ref(loadIndicators(storage, props.preset))
watch(indicators, (value) => saveIndicators(storage, value, props.preset), { deep: true })
const showLevels = ref(true)
const showBuyPoints = ref(true),
  reviewPanel = ref(null)
const candidateOpen = ref(false)
const plotElement = ref(null),
  plotSize = ref({ width: 0, height: 0 })
let plotObserver
watch(
  plotElement,
  (element) => {
    plotObserver?.disconnect()
    if (!element) return
    const resize = () => {
      const { width, height } = element.getBoundingClientRect()
      plotSize.value = { width, height }
    }
    plotObserver = new ResizeObserver(resize)
    plotObserver.observe(element)
    resize()
  },
  { flush: 'post' },
)
onBeforeUnmount(() => plotObserver?.disconnect())
const review = computed(() => {
  if (!props.buyReview) return null
  return props.buyReview.as_of === completed.value.bars.at(-1)?.day
    ? props.buyReview
    : { ...props.buyReview, ready: false, markers: [], reason: '复盘与日 K 尚未对齐，等待数据同步。' }
})
const showTrades = ref(true),
  tradeTape = ref(null)
const groups = computed(() => tradeGroups(props.tradeHistory?.items))
const tapeGroups = computed(() =>
  replay.value ? groups.value.filter((g) => g.day <= analysisDay.value) : groups.value,
)
const pendingTrades = computed(() =>
  replay.value ? [] : groups.value.filter((g) => g.day > (prepared.value.bars.at(-1)?.day || '')).reverse(),
)
const tradeDay = computed(() => exchangeTime(props.tradeHistory?.updatedAt)?.day)
const tradeAnchors = computed(() => {
  if (!showTrades.value) return []
  return groups.value.flatMap((group) => {
    const bar = chart.value?.plotted.find((b) => b.day === group.day && !b.future)
    if (!bar) return []
    return [
      {
        ...group,
        x: bar.x / width,
        y: (group.side === 'BUY' ? bar.lowY : bar.highY) / priceHeight,
        title: `${group.day} ${group.label} ${group.actionLabel} · ${group.items.length} 笔模拟成交 · 点击查看交易价与原因`,
      },
    ]
  })
})
const buyPoints = computed(() => {
  if (!showBuyPoints.value || !review.value?.ready) return []
  const markers = reviewMarkersAsOf(review.value.markers, analysisDay.value)
  return markers.flatMap((marker) => {
    const bar = chart.value?.plotted.find((b) => b.day === marker.day)
    return bar
      ? [
          {
            ...marker,
            id: `candidate:${marker.id}`,
            kind: 'candidate',
            tone: 'candidate',
            side: 'BUY',
            label: 'm',
            x: bar.x / width,
            y: bar.lowY / priceHeight,
            title: `${marker.day} m 复盘触价候选（未代表成交） · ${marker.setup} · 点击查看依据`,
          },
        ]
      : []
  })
})
function selectMarker(marker) {
  if (marker.kind === 'setup') candidateOpen.value = true
  else if (marker.kind === 'candidate') showBuyPoint(marker)
  else tradeTape.value?.show(marker)
}
async function focusTrade(group) {
  const index = prepared.value.bars.findIndex((b) => b.day === group.day)
  if (index < 0) return
  // Inspect the event's date without changing the strategy's historical cutoff.
  replay.value = false
  if (!bars.value.some((b) => b.day === group.day))
    end.value = Math.min(prepared.value.bars.length, index + Math.ceil(count.value / 2))
  await nextTick()
  selectedDay.value = group.day
}
async function focusBuyPoint(day) {
  replay.value = true
  end.value = null
  await nextTick()
  selectedDay.value = day
}
function showBuyPoint(marker) {
  replay.value = true
  selectedDay.value = marker.day
  reviewPanel.value.show(marker.day)
}
const settingsOpen = ref(false),
  compactPane = ref('volume')
function startReview() {
  settingsOpen.value = false
  toggleReplay()
}
function resetIndicators() {
  indicators.value = { ...DEFAULT_INDICATORS }
  compactPane.value = 'volume'
}
const mainOptions = [
  ...MA_PERIODS.map((period) => ({ id: 'ma' + period, label: 'MA' + period, color: 'ma-' + period })),
  { id: 'ema20', label: 'EMA20', color: 'ema-20' },
  { id: 'boll', label: '布林带 BOLL(20,2)', color: 'boll-line' },
  { id: 'dc', label: '唐奇安 DC(20)', color: 'dc-line' },
]
const mainLines = computed(() => [
  ...(indicators.value.ema20
    ? [{ id: 'ema20', label: 'EMA20', color: 'ema-20', read: (bar) => bar.ema20 }]
    : []),
  ...MA_PERIODS.filter((period) => indicators.value['ma' + period]).map((period) => ({
    id: 'ma' + period,
    label: 'MA' + period,
    color: 'ma-' + period,
    read: (bar) => bar.ma[period],
  })),
  ...['boll', 'dc']
    .filter((id) => indicators.value[id])
    .flatMap((id) =>
      ['upper', 'middle', 'lower'].map((edge, index) => ({
        id: id + edge,
        label: id.toUpperCase() + ['上', '中', '下'][index],
        color: id + '-line',
        dashed: edge === 'middle',
        read: (bar) => bar[id]?.[edge],
      })),
    ),
])
const bars = computed(() =>
  candleWindow(prepared.value.bars, count.value, end.value ?? prepared.value.bars.length),
)
const current = computed(() => bars.value.find((bar) => bar.day === selectedDay.value) || bars.value.at(-1))
const latestBar = computed(() => prepared.value.bars.at(-1))
const changeToLatest = computed(() =>
  current.value?.close > 0 && latestBar.value?.close > 0
    ? latestBar.value.close / current.value.close - 1
    : null,
)
const analysisDay = computed(() => (replay.value ? current.value?.day : completed.value.bars.at(-1)?.day))
const candidate = computed(() =>
  !replay.value &&
  props.candidateSetup?.as_of === analysisDay.value &&
  bars.value.some((bar) => bar.day === props.candidateSetup.as_of)
    ? props.candidateSetup
    : null,
)
watch(
  () => candidate.value?.signal_day,
  () => {
    candidateOpen.value = false
  },
)
const historical = computed(() =>
  prepareChartReview(props.points, analysisDay.value, {
    preset: props.preset,
    tick: props.tickSize,
    minimumBars: props.minimumBars,
    layers: paEnabled.value,
  }),
)
const referenceLevels = computed(() =>
  showLevels.value
    ? replay.value
      ? strategyLevels(historical.value.strategy)
      : visibleStrategyLevels(props.referenceLevels, analysisDay.value, props.levelsAsOf)
    : [],
)
const startIndex = computed(() => Math.max(0, (end.value ?? prepared.value.bars.length) - count.value))
const paEnabled = computed(() => PA_OPTIONS.some(({ id }) => indicators.value[id]))
const paResult = computed(() => {
  if (!paEnabled.value) return null
  return historical.value.priceAction
})
const paVisibleBars = computed(() => paResult.value?.bars.slice(startIndex.value) || [])
const historicalBars = computed(() => new Map(historical.value.prepared.bars.map((bar) => [bar.day, bar])))
const indicatorBars = computed(() =>
  bars.value.map(
    (bar) =>
      historicalBars.value.get(bar.day) ||
      (!replay.value && bar.live
        ? bar
        : {
            ...bar,
            ma: {},
            ema20: null,
            boll: null,
            dc: null,
            macd: null,
            volume: null,
          }),
  ),
)
watch(
  () => [replay.value, analysisDay.value, historical.value.strategy, current.value],
  () => {
    emit('review-context', {
      active: replay.value,
      as_of: analysisDay.value,
      display_as_of: latestBar.value?.day,
      pa: historical.value.strategy,
      bar: current.value,
    })
  },
  { immediate: true },
)
watch(
  () => props.points,
  () => {
    if (replay.value && prepared.value.bars.some((bar) => bar.day === selectedDay.value)) return
    end.value = null
    selectedDay.value = null
  },
)
watch([count, end], () => {
  if (!replay.value || !bars.value.some((bar) => bar.day === selectedDay.value)) selectedDay.value = null
})
function toggleReplay() {
  const day = current.value?.live ? completed.value.bars.at(-1)?.day : current.value?.day
  replay.value = !replay.value
  if (replay.value) selectedDay.value = day
}
function leaveChart() {
  if (!replay.value) selectedDay.value = null
}
function moveReview(direction) {
  const index = prepared.value.bars.findIndex((bar) => bar.day === current.value?.day)
  const next = prepared.value.bars[index + direction]
  if (!next) return
  replay.value = true
  selectedDay.value = next.day
  if (!bars.value.some((bar) => bar.day === next.day)) end.value = index + direction + 1
}

// Every pane shares the same x coordinates but has an independent y scale.
const width = 760,
  priceTop = 14,
  priceBottom = 240,
  priceHeight = 256,
  volumeTop = 6,
  volumeBottom = 66,
  macdTop = 10,
  macdBottom = 110,
  macdZero = (macdTop + macdBottom) / 2
function linePath(plotted, read, y) {
  let segment = false
  return plotted
    .map((bar) => {
      const value = read(bar)
      if (value == null) {
        segment = false
        return ''
      }
      const command = `${segment ? 'L' : 'M'}${bar.x},${y(value)}`
      segment = true
      return command
    })
    .join(' ')
}
const chart = computed(() => {
  if (!bars.value.length) return null
  const values = indicatorBars.value.flatMap((bar) => [
    bar.low,
    bar.high,
    ...mainLines.value.map((line) => line.read(bar)).filter((value) => value != null),
  ])
  values.push(...referenceLevels.value.map((level) => level.value))
  const min = Math.min(...values),
    max = Math.max(...values)
  const padding = Math.max((max - min) * 0.08, max * 0.002, 0.001)
  const low = min - padding,
    high = max + padding
  const y = (value) => priceBottom - ((value - low) / (high - low)) * (priceBottom - priceTop)
  const volumeMax = Math.max(1, ...indicatorBars.value.map((bar) => bar.volume ?? 0))
  const viewportWidth = plotSize.value.width || width
  const layout = candlePlotLayout(viewportWidth, bars.value.length)
  const inset = (layout.left / viewportWidth) * width
  const step = (layout.barWidth / viewportWidth) * width
  const bodyWidth = mobile.value ? (layout.bodyWidth / viewportWidth) * width : Math.min(12, step * 0.62)
  const macdValues = indicatorBars.value.flatMap((bar) => (bar.macd ? Object.values(bar.macd) : []))
  const macdMax = Math.max(0.0001, ...macdValues.map(Math.abs)) * 1.1
  const macdY = (value) => macdZero - (value / macdMax) * (macdZero - macdTop)
  const plotted = indicatorBars.value.map((bar, index) => ({
    ...bar,
    future: replay.value && bar.day > analysisDay.value,
    x: inset + (index + 0.5) * step,
    highY: y(bar.high),
    lowY: y(bar.low),
    closeY: y(bar.close),
    bodyY: y(Math.max(bar.open, bar.close)),
    bodyHeight: Math.max(1, Math.abs(y(bar.open) - y(bar.close))),
    volumeHeight: ((bar.volume ?? 0) / volumeMax) * (volumeBottom - volumeTop),
    macdY: bar.macd ? macdY(bar.macd.histogram) : null,
    color: bar.close > bar.open ? 'var(--red)' : bar.close < bar.open ? 'var(--green)' : 'var(--muted)',
  }))
  const lines = mainLines.value.map((line) => ({ ...line, path: linePath(plotted, line.read, y) }))
  const macdLines = ['dif', 'dea'].map((id) => ({
    id,
    path: linePath(plotted, (bar) => bar.macd?.[id], macdY),
  }))
  return {
    plotted,
    bodyWidth,
    lines,
    ticks: [high, (high + low) / 2, low],
    volumeMax,
    macdLines,
    macdMax,
    hasMACD: macdValues.length > 0,
    low,
    high,
    references: priceLineLayout(
      referenceLevels.value,
      low,
      high,
      priceTop,
      priceBottom,
      (24 * priceHeight) / (plotSize.value.height || priceHeight),
    ),
  }
})
const crosshair = computed(() => chart.value?.plotted.find((bar) => bar.day === selectedDay.value))
const candidateBar = computed(
  () => candidate.value && chart.value?.plotted.find((bar) => bar.day === candidate.value.signal_day),
)
const candidateAnchors = computed(() =>
  candidateBar.value
    ? [
        {
          id: `setup:${candidate.value.signal_day}`,
          day: candidate.value.signal_day,
          kind: 'setup',
          side: 'BUY',
          label: '候',
          x: candidateBar.value.x / width,
          y: candidateBar.value.lowY / priceHeight,
          title: `${candidate.value.signal_day} ${candidate.value.setup} · 次日候选，待触发 · 点击查看入选依据`,
        },
      ]
    : [],
)
const chartPointer = createChartPointer(inspectAt)
function inspectAt(event, tapped = false, dragging = false) {
  const markerDay = event.target.closest('[data-buy-day]')?.dataset.buyDay
  if (markerDay && !dragging) {
    selectedDay.value = markerDay
    // Let the marker's click open its reason before entering historical replay.
    return
  }
  const bounds = event.currentTarget.getBoundingClientRect()
  const index = candleIndexAt(event.clientX - bounds.left, bounds.width, bars.value.length)
  const bar = bars.value[index]
  if (event.type === 'pointerdown' || tapped) replay.value = !bar?.live
  selectedDay.value = bar?.day ?? null
}
function step(event) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const last = bars.value.length - 1
  const index = selectedDay.value ? bars.value.findIndex((bar) => bar.day === selectedDay.value) : last
  const next =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? last
        : Math.max(0, Math.min(last, index + (event.key === 'ArrowLeft' ? -1 : 1)))
  const bar = bars.value[next]
  replay.value = !bar?.live
  selectedDay.value = bar?.day ?? null
}
function latest() {
  replay.value = false
  end.value = null
  selectedDay.value = null
}
function shift(direction) {
  replay.value = true
  selectedDay.value = null
  end.value = Math.max(
    Math.min(count.value, prepared.value.bars.length),
    Math.min(
      prepared.value.bars.length,
      (end.value ?? prepared.value.bars.length) + direction * Math.max(1, Math.floor(count.value / 2)),
    ),
  )
}
const shares = (value) =>
  value == null
    ? '—'
    : value >= 1e8
      ? `${money(value / 1e8)} 亿${props.volumeUnit}`
      : value >= 1e4
        ? `${money(value / 1e4)} 万${props.volumeUnit}`
        : `${money(value, 0)} ${props.volumeUnit}`
</script>
<template>
  <section
    class="kline"
    :class="{ 'kline-compact': compact, 'kline-with-levels': chart?.references.length > 0 }"
    aria-label="日 K 线图"
  >
    <div class="kline-toolbar">
      <span class="kline-adjustment"
        >日 K <span>{{ adjustmentLabel }}</span></span
      >
      <span
        v-if="!replay && prepared.bars.at(-1)?.live"
        class="live-candle-time"
        :class="{ amberText: liveCandle?.bar?.stale }"
      >
        {{ liveCandle?.bar?.at?.slice(11, 19) }}{{ liveCandle?.bar?.stale ? ' · 行情待更新' : '' }}
      </span>
      <button
        v-if="mobile"
        class="chart-settings-button mobile-structure-toggle"
        :aria-pressed="mobileStructure"
        @click="mobileStructure = !mobileStructure"
      >
        结构标注{{ mobileStructure ? ' · 开' : ' · 关' }}
      </button>
      <div class="chart-tools">
        <select
          v-model="count"
          class="kline-range-select"
          aria-label="K 线显示范围"
          @change="customRange = true"
        >
          <option
            v-for="range in buyReview ? [10, 30, 60, 120, 250] : [30, 60, 120, 250]"
            :key="range"
            :value="range"
          >
            {{ range }} 日
          </option>
        </select>
        <button
          class="chart-settings-button"
          aria-haspopup="dialog"
          :aria-expanded="settingsOpen"
          @click="settingsOpen = true"
        >
          <Icon name="settings" :size="14" />图表设置
        </button>
      </div>
    </div>
    <p v-if="mobile" class="chart-touch-hint">左右拖动查看读数 · 上下滑动浏览页面</p>
    <div
      v-if="replay"
      class="chart-review-controls"
      aria-label="历史 K 线回顾"
      :data-review-as-of="analysisDay"
    >
      <strong>指标截至 {{ analysisDay }}</strong>
      <button class="text-button" @click="moveReview(-1)" :disabled="current?.day === prepared.bars[0]?.day">
        前一日
      </button>
      <button
        class="text-button"
        @click="moveReview(1)"
        :disabled="current?.day === prepared.bars.at(-1)?.day"
      >
        后一日
      </button>
      <button class="text-button" @click="latest">退出回顾</button>
      <span>选择 K 线逐日重绘；淡色后续 K 线不参与指标，买点仅显示当日已确认项。</span>
    </div>
    <WorkbenchDialog v-model:open="settingsOpen" title="图表设置">
      <p class="chart-settings-hint">按需调整指标和绘图显示。</p>
      <label v-if="mobile" class="mobile-structure-setting">
        <input v-model="mobileStructure" type="checkbox" />显示结构标注
        <small>关闭时保留均线、买卖点与策略价位。</small>
      </label>
      <div class="kline-indicators" aria-label="图表指标设置">
        <div class="indicator-group" role="group" aria-label="主图指标">
          <span class="indicator-group-title">主图</span>
          <label v-for="option in mainOptions" :key="option.id" :class="{ active: indicators[option.id] }">
            <input v-model="indicators[option.id]" type="checkbox" />
            <span :class="indicators[option.id] ? option.color : ''">{{ option.label }}</span>
          </label>
        </div>
        <div class="indicator-group" role="group" aria-label="副图指标">
          <span class="indicator-group-title">副图</span>
          <label :class="{ active: indicators.volume }">
            <input v-model="indicators.volume" type="checkbox" />成交量 VOL
          </label>
          <label :class="{ active: indicators.macd }">
            <input v-model="indicators.macd" type="checkbox" />MACD(12,26,9)
          </label>
          <button class="text-button" type="button" @click="resetIndicators">恢复精简显示</button>
        </div>
      </div>
      <PriceActionSettings v-model="indicators" />
      <div v-if="referenceLevels.length || props.referenceLevels.length" class="strategy-level-toggle">
        <label><input type="checkbox" v-model="showLevels" />显示策略关键价位</label>
        <small>{{
          replay
            ? `回顾价位截至 ${analysisDay} · 前复权`
            : bars.at(-1)?.day === levelsAsOf
              ? `价位基准 ${levelsAsOf} · 前复权`
              : '历史窗口中隐藏当前策略价位，回到最新可查看'
        }}</small>
      </div>
      <div
        v-if="compact && indicators.volume && indicators.macd"
        class="compact-pane-switch"
        role="group"
        aria-label="副图切换"
      >
        <span class="indicator-group-title">当前副图</span>
        <button :aria-pressed="compactPane === 'volume'" @click="compactPane = 'volume'">成交量</button>
        <button :aria-pressed="compactPane === 'macd'" @click="compactPane = 'macd'">MACD</button>
      </div>
      <div v-if="tradeHistory || buyReview" class="trade-legend" aria-label="买卖点显示控制">
        <button v-if="tradeHistory" :aria-pressed="showTrades" @click="showTrades = !showTrades">
          {{ showTrades ? '●' : '○' }} 成交 B/S/T
        </button>
        <TradeLegend v-if="tradeHistory" />
        <button v-if="buyReview" :aria-pressed="showBuyPoints" @click="showBuyPoints = !showBuyPoints">
          {{ showBuyPoints ? 'm' : '○' }} 候选（复盘）
        </button>
        <small>日 K 按成交日定位 · 点击标记看依据</small>
      </div>
      <div class="chart-settings-review">
        <button class="button secondary compact" :disabled="!current" @click="startReview">
          {{ replay ? '退出历史回顾' : '进入历史回顾' }}
        </button>
        <span>逐日查看历史 K 线与当时的指标。</span>
      </div>
      <details v-if="chart && current" class="chart-settings-info">
        <summary>图表读数与说明</summary>
        <div class="kline-volume-readout">
          <span
            >成交量 <strong>{{ shares(current.volume) }}</strong></span
          ><span
            >成交额
            <strong>{{ current.amount == null ? '不可用' : `${amount(current.amount)}元` }}</strong></span
          >
        </div>
        <PriceActionSummary v-if="paEnabled" :result="paResult" :layers="indicators" :day="analysisDay" />
        <p class="kline-note">
          最新日 K 为 {{ prepared.bars.at(-1).day
          }}{{ prepared.bars.at(-1).live ? '，随当日行情更新' : '' }}。
        </p>
        <p v-if="!replay && liveCandle?.message" class="kline-note amberText">{{ liveCandle.message }}</p>
        <p v-if="historical.prepared.omitted" class="kline-note amberText">
          截至 {{ analysisDay }}，{{ historical.prepared.omitted }} 条开高低收不完整或异常的记录未绘制。
        </p>
        <details class="kline-help">
          <summary>指标说明与参数</summary>
          <p>
            距今涨幅 =（图中最新日 K 的收盘价或最新价 ÷ 选中日收盘价 − 1）× 100%，采用{{
              adjustmentLabel
            }}口径。 截止日期显示在读数旁；翻页或进入回顾模式时，仍以图中最新日 K 为基准。
          </p>
          <p>MA：收盘价的 5／10／20／60 日简单均线，各条可独立勾选。</p>
          <p>
            EMA20：以前 20 根收盘价均值初始化，之后按 2/21
            权重递推；缺失收盘价后重新预热。价格行为图层中的趋势通道线连接摆动结构，与 BOLL、DC
            的滚动通道计算方式不同。
          </p>
          <p>
            BOLL(20,2)：20 日均线为中轨，上下轨为中轨 ± 2 倍总体标准差。DC(20)：含当日的近 20
            日最高价、最低价为上下轨，两轨均值为中轨；通道中轨用虚线表示。
          </p>
          <p>
            MACD(12,26,9)：DIF = EMA12 − EMA26，DEA = DIF 的 9 日 EMA，柱值 = 2 × (DIF −
            DEA)；正柱红色、负柱绿色，与当天涨跌方向独立。
          </p>
          <p>
            指标使用已加载的全部{{ adjustmentLabel }}日线计算，切换显示范围不会重新起算。EMA
            从首个有效收盘价初始化；缺失收盘价后重新预热 26
            根。均线和通道样本不足时显示「—」，不同数据长度或初始化方式可能导致与其他软件的数值略有差异。
          </p>
        </details>
      </details>
    </WorkbenchDialog>
    <template v-if="chart && current">
      <div class="kline-readout" data-touch-surface aria-live="polite" aria-atomic="true">
        <div class="kline-day">
          <strong>{{ current.day }}</strong
          ><span :class="changeClass(current.change)">{{ pct(current.change, true) }}</span
          ><small>较前一交易日</small>
          <span
            class="kline-return"
            :title="`距今涨幅 =（${latestBar.day} ${latestBar.live ? '最新价' : '收盘价'} ÷ ${current.day} ${current.live ? '最新价' : '收盘价'} − 1）× 100%，采用${adjustmentLabel}口径`"
          >
            <span class="kline-return-label">距今涨幅</span>
            <b :class="changeClass(changeToLatest)">{{ pct(changeToLatest, true) }}</b>
            <span class="kline-return-asof">至 {{ latestBar.day }}</span>
          </span>
        </div>
        <dl class="kline-ohlc">
          <div>
            <dt>开</dt>
            <dd>{{ money(current.open, 3) }}</dd>
          </div>
          <div>
            <dt>高</dt>
            <dd>{{ money(current.high, 3) }}</dd>
          </div>
          <div>
            <dt>低</dt>
            <dd>{{ money(current.low, 3) }}</dd>
          </div>
          <div>
            <dt>{{ current.live ? '现' : '收' }}</dt>
            <dd :class="changeClass(current.close - current.open)">{{ money(current.close, 3) }}</dd>
          </div>
        </dl>
      </div>
      <div v-if="mainLines.length" data-touch-surface class="kline-legend" aria-label="主图指标读数">
        <span v-for="line in mainLines" :key="line.id" :class="line.color">
          {{ line.label }} <b>{{ money(line.read(current), 3) }}</b>
        </span>
      </div>
      <PendingDailyTrades
        :groups="pendingTrades"
        :last-day="prepared.bars.at(-1)?.day"
        :today="tradeDay"
        :error="tradeHistory?.error"
        @select="tradeTape?.show($event)"
        @show-intraday="emit('show-intraday')"
      />
      <CandidateSetup :candidate="candidate" />
      <WorkbenchDialog v-model:open="candidateOpen" title="次日候选 · 入选依据">
        <CandidateSetup :candidate="candidate" />
      </WorkbenchDialog>
      <div class="kline-axes" data-touch-surface>
        <div class="kline-y" aria-hidden="true">
          <span
            v-for="(tick, index) in chart.ticks"
            :key="index"
            :style="{ top: `${((priceTop + (index * (priceBottom - priceTop)) / 2) / priceHeight) * 100}%` }"
            >{{ money(tick, mobile && tickSize >= 0.01 ? 2 : 3) }}</span
          >
        </div>
        <div
          ref="plotElement"
          class="kline-main-plot"
          @pointermove="chartPointer.move"
          @pointerdown="chartPointer.start"
          @pointerup="chartPointer.end"
          @pointercancel="chartPointer.cancel"
          @click.capture="chartPointer.click"
          @pointerleave="leaveChart"
        >
          <svg
            class="kline-plot"
            :viewBox="`0 0 ${width} ${priceHeight}`"
            preserveAspectRatio="none"
            role="img"
            tabindex="0"
            :aria-label="`${name || 'ETF'} ${adjustmentLabel}日 K，${bars.length} 个交易日，${bars[0].day}至${bars.at(-1).day}。红色阳线、绿色阴线。左右方向键逐日查看，Home 和 End 跳至两端。`"
            @keydown="step"
          >
            <path
              :d="`M0 ${priceTop}H${width} M0 ${(priceTop + priceBottom) / 2}H${width} M0 ${priceBottom}H${width}`"
              stroke="var(--chart-grid)"
              stroke-dasharray="3 5"
              fill="none"
              vector-effect="non-scaling-stroke"
            />
            <g
              v-for="bar in chart.plotted"
              :key="bar.day"
              :data-day="bar.day"
              :data-live-candle="bar.live ? bar.day : undefined"
              :opacity="bar.future ? 0.22 : 1"
            >
              <line
                :x1="bar.x"
                :x2="bar.x"
                :y1="bar.highY"
                :y2="bar.lowY"
                :stroke="bar.color"
                vector-effect="non-scaling-stroke"
              />
              <rect
                class="candle-body"
                :x="bar.x - chart.bodyWidth / 2"
                :y="bar.bodyY"
                :width="chart.bodyWidth"
                :height="bar.bodyHeight"
                :fill="bar.color"
              />
            </g>
            <path
              v-for="line in chart.lines"
              :key="line.id"
              :d="line.path"
              :class="['indicator-line', line.color]"
              :data-indicator="line.id"
              :stroke-dasharray="line.dashed ? '4 3' : undefined"
              fill="none"
              stroke="currentColor"
              stroke-width="1.2"
              vector-effect="non-scaling-stroke"
            />
            <g v-if="crosshair" class="kline-crosshair" aria-hidden="true">
              <path
                :d="`M${crosshair.x} 0V${priceHeight} M0 ${crosshair.closeY}H${width}`"
                stroke="var(--muted)"
                stroke-dasharray="4 3"
                fill="none"
                vector-effect="non-scaling-stroke"
              />
            </g>
            <g v-for="line in chart.references" :key="line.id" :data-strategy-level="line.id">
              <path
                :d="`M0 ${line.y}H${width}`"
                :stroke="line.color"
                stroke-dasharray="5 4"
                fill="none"
                vector-effect="non-scaling-stroke"
              />
            </g>
            <rect
              v-if="candidateBar"
              :data-candidate-day="candidate.signal_day"
              :x="candidateBar.x - chart.bodyWidth / 2 - 3"
              :y="candidateBar.highY - 3"
              :width="chart.bodyWidth + 6"
              :height="Math.max(6, candidateBar.lowY - candidateBar.highY + 6)"
              fill="none"
              stroke="var(--yellow)"
              stroke-dasharray="3 2"
              vector-effect="non-scaling-stroke"
            />
          </svg>
          <PriceActionOverlay
            v-if="paResult?.analysis && (!mobile || mobileStructure)"
            :analysis="paResult.analysis"
            :bars="paVisibleBars"
            :visible-count="bars.length"
            :layers="indicators"
            :offset="startIndex"
            :low="chart.low"
            :high="chart.high"
            :top="priceTop"
            :bottom="priceBottom"
            :height="priceHeight"
          />
          <TradeMarkers
            :anchors="[...tradeAnchors, ...buyPoints, ...candidateAnchors]"
            @select="selectMarker"
          />
        </div>
        <div
          v-if="!mobile && chart.references.length"
          class="strategy-price-labels"
          aria-label="图中策略价位"
        >
          <svg
            class="strategy-price-guides"
            :viewBox="`0 0 16 ${priceHeight}`"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <path
              v-for="line in chart.references"
              :key="line.id"
              :d="`M0 ${line.y}H4 L12 ${line.labelY}H16`"
              :stroke="line.color"
              fill="none"
              vector-effect="non-scaling-stroke"
            />
          </svg>
          <span
            v-for="line in chart.references"
            :key="line.id"
            :style="{ top: `${(line.labelY / priceHeight) * 100}%`, color: line.color }"
          >
            {{ line.label }} {{ money(line.value, 3) }}
          </span>
        </div>
      </div>
      <div
        v-if="indicators.volume && (!compact || compactPane === 'volume' || !indicators.macd)"
        class="kline-pane volume-pane"
      >
        <div class="kline-legend pane-legend">
          <strong>VOL</strong>
          <span>成交量 {{ shares(current.volume) }}</span>
        </div>
        <div class="kline-axes volume-axes" data-touch-surface>
          <div class="kline-y" aria-hidden="true">
            <span :style="{ top: `${(volumeTop / 72) * 100}%` }">{{
              mobile ? shares(chart.volumeMax).replace(volumeUnit, '') : shares(chart.volumeMax)
            }}</span>
            <span :style="{ top: `${(volumeBottom / 72) * 100}%` }">0</span>
          </div>
          <svg
            class="kline-plot"
            :viewBox="`0 0 ${width} 72`"
            preserveAspectRatio="none"
            role="img"
            tabindex="0"
            aria-label="成交量副图，左右方向键逐日查看"
            @pointermove="chartPointer.move"
            @pointerdown="chartPointer.start"
            @pointerup="chartPointer.end"
            @pointercancel="chartPointer.cancel"
            @click.capture="chartPointer.click"
            @pointerleave="leaveChart"
            @keydown="step"
          >
            <path
              :d="`M0 ${volumeTop}H${width} M0 ${volumeBottom}H${width}`"
              stroke="var(--chart-grid)"
              fill="none"
              stroke-dasharray="3 5"
              vector-effect="non-scaling-stroke"
            />
            <template v-for="bar in chart.plotted" :key="bar.day">
              <rect
                v-if="bar.volume != null && bar.volume > 0"
                class="volume-bar"
                :x="bar.x - chart.bodyWidth / 2"
                :y="volumeBottom - bar.volumeHeight"
                :width="chart.bodyWidth"
                :height="bar.volumeHeight"
                :fill="bar.color"
                opacity=".7"
              />
            </template>
            <path
              v-if="crosshair"
              class="kline-crosshair"
              :d="`M${crosshair.x} 0V72`"
              stroke="var(--muted)"
              stroke-dasharray="4 3"
              vector-effect="non-scaling-stroke"
              aria-hidden="true"
            />
          </svg>
        </div>
      </div>
      <div
        v-if="indicators.macd && (!compact || compactPane === 'macd' || !indicators.volume)"
        class="kline-pane macd-pane"
      >
        <div class="kline-legend pane-legend" aria-label="MACD 指标读数">
          <strong>MACD(12,26,9)</strong>
          <span class="dif-line"
            >DIF <b>{{ money(current.macd?.dif, 4) }}</b></span
          >
          <span class="dea-line"
            >DEA <b>{{ money(current.macd?.dea, 4) }}</b></span
          >
          <span :class="changeClass(current.macd?.histogram)"
            >MACD <b>{{ money(current.macd?.histogram, 4) }}</b></span
          >
        </div>
        <div v-if="chart.hasMACD" class="kline-axes macd-axes" data-touch-surface>
          <div class="kline-y" aria-hidden="true">
            <span
              v-for="(tick, index) in [chart.macdMax, 0, -chart.macdMax]"
              :key="index"
              :style="{ top: `${((macdTop + index * (macdZero - macdTop)) / 120) * 100}%` }"
              >{{ money(tick, 4) }}</span
            >
          </div>
          <svg
            class="kline-plot"
            :viewBox="`0 0 ${width} 120`"
            preserveAspectRatio="none"
            role="img"
            tabindex="0"
            aria-label="MACD 副图，DIF、DEA 与红绿柱，左右方向键逐日查看"
            @pointermove="chartPointer.move"
            @pointerdown="chartPointer.start"
            @pointerup="chartPointer.end"
            @pointercancel="chartPointer.cancel"
            @click.capture="chartPointer.click"
            @pointerleave="leaveChart"
            @keydown="step"
          >
            <path
              :d="`M0 ${macdTop}H${width} M0 ${macdBottom}H${width}`"
              stroke="var(--chart-grid)"
              fill="none"
              stroke-dasharray="3 5"
              vector-effect="non-scaling-stroke"
            />
            <path
              :d="`M0 ${macdZero}H${width}`"
              stroke="var(--muted)"
              opacity=".5"
              vector-effect="non-scaling-stroke"
            />
            <template v-for="bar in chart.plotted" :key="bar.day">
              <rect
                v-if="bar.macd && bar.macd.histogram !== 0"
                class="macd-bar"
                :x="bar.x - chart.bodyWidth / 2"
                :y="Math.min(macdZero, bar.macdY)"
                :width="chart.bodyWidth"
                :height="Math.abs(bar.macdY - macdZero)"
                :fill="bar.macd.histogram > 0 ? 'var(--red)' : 'var(--green)'"
                opacity=".7"
              />
            </template>
            <path
              v-for="line in chart.macdLines"
              :key="line.id"
              :d="line.path"
              :class="line.id + '-line'"
              fill="none"
              stroke="currentColor"
              stroke-width="1.3"
              vector-effect="non-scaling-stroke"
            />
            <path
              v-if="crosshair"
              class="kline-crosshair"
              :d="`M${crosshair.x} 0V120`"
              stroke="var(--muted)"
              stroke-dasharray="4 3"
              vector-effect="non-scaling-stroke"
              aria-hidden="true"
            />
          </svg>
        </div>
        <p v-else class="kline-note">当前范围内尚无 MACD，至少需要 26 根连续有效收盘价。</p>
      </div>
      <div class="kline-x">
        <span>{{ bars[0].day }}</span
        ><span>{{ bars.at(-1).day }}</span>
      </div>
      <dl v-if="mobile && chart.references.length" class="mobile-strategy-levels" aria-label="图中策略价位">
        <div v-for="line in chart.references" :key="line.id" :style="{ color: line.color }">
          <dt>{{ line.label }}</dt>
          <dd>{{ money(line.value, 3) }}</dd>
        </div>
      </dl>
      <div class="kline-navigation">
        <button class="button secondary compact" @click="shift(-1)" :disabled="startIndex === 0">
          <Icon name="chevron" :size="12" class="previous-icon" />更早
        </button>
        <span>{{ startIndex + 1 }}–{{ startIndex + bars.length }} / {{ prepared.bars.length }} 根</span>
        <button
          class="button secondary compact"
          @click="shift(1)"
          :disabled="end == null || end >= prepared.bars.length"
        >
          更近<Icon name="chevron" :size="12" />
        </button>
        <button
          class="text-button"
          @click="latest"
          :disabled="!replay && (end == null || end >= prepared.bars.length)"
        >
          回到最新
        </button>
      </div>
      <TradeTape
        v-if="tradeHistory"
        ref="tradeTape"
        :groups="tapeGroups"
        :day="replay ? analysisDay : undefined"
        :history="tradeHistory"
        mode="daily"
        @focus="focusTrade"
      />
      <BuyPointReview
        ref="reviewPanel"
        :review="review"
        :day="current.day"
        :visible="showBuyPoints"
        @toggle="showBuyPoints = !showBuyPoints"
        @recent="focusBuyPoint(review.markers[0]?.day || review.as_of)"
        @select="focusBuyPoint"
      />
    </template>
    <Empty
      v-else
      title="暂无完整日 K 数据"
      :description="
        points.length
          ? '现有记录缺少完整开高低收，待历史行情补齐后展示。'
          : '历史数据同步完成后，这里会显示日 K 和成交量。'
      "
      icon="market"
    />
  </section>
</template>
<style scoped>
.chart-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}
.kline-range-select,
.chart-settings-button,
.compact-pane-switch button {
  min-height: 30px;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  background: var(--panel);
  color: var(--text-secondary);
  padding: 4px 8px;
  font-size: 11px;
}
.kline-range-select {
  width: 88px;
}
.chart-settings-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}
.chart-settings-button:hover,
.compact-pane-switch button[aria-pressed='true'] {
  color: var(--accent-text);
  border-color: var(--accent-border);
  background: var(--accent-soft);
}
.chart-settings-hint,
.chart-settings-review span {
  color: var(--muted);
  font-size: 12px;
}
.chart-settings-hint {
  margin: 0 0 16px;
}
.chart-settings-review {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  padding: 16px 0;
}
.chart-settings-info {
  border-top: 1px solid var(--line);
  padding-top: 12px;
  font-size: 12px;
}
.chart-settings-info > summary {
  cursor: pointer;
}
@media (max-width: 700px) {
  .kline-range-select,
  .chart-settings-button {
    min-height: var(--mobile-field-height);
    font-size: 12px;
  }
}

.live-candle-time {
  color: var(--muted);
  font-size: 10px;
}
.trade-legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  padding: 5px 0;
  flex-shrink: 0;
}
.trade-legend button {
  border: 1px solid var(--line);
  background: var(--panel2);
  color: var(--muted);
  border-radius: 4px;
  padding: 4px 7px;
  font-size: 11px;
}
.trade-legend button[aria-pressed='true'] {
  color: var(--text);
  border-color: var(--accent-border);
}
.trade-legend button span {
  margin-left: 4px;
}
.trade-legend small {
  font-size: 10px;
  color: var(--muted);
}
@media (max-width: 700px) {
  .trade-legend button {
    min-height: var(--mobile-control-height);
  }
}

.chart-review-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px 12px;
  flex-shrink: 0;
  padding: 6px 0;
  font-size: 11px;
  color: var(--muted);
}
.chart-review-controls .text-button {
  font-size: 11px;
}
.chart-review-controls strong {
  color: var(--accent-text);
  font-weight: 500;
}
.chart-review-controls span {
  font-size: 10px;
}
.strategy-level-toggle {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  align-items: center;
  margin-bottom: 12px;
  font-size: 11px;
}
.strategy-level-toggle label {
  display: flex;
  align-items: center;
  gap: 5px;
}
.strategy-level-toggle small {
  color: var(--muted);
}
.strategy-price-labels {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: var(--strategy-gutter);
  border-left: 1px solid var(--line);
  pointer-events: none;
}
.strategy-price-guides {
  display: block;
  width: 16px;
  height: 100%;
}
.strategy-price-labels span {
  position: absolute;
  left: 16px;
  transform: translateY(-50%);
  padding: 2px 5px;
  background: var(--panel);
  border: 1px solid currentColor;
  border-radius: 3px;
  font-size: 10px;
  line-height: 14px;
  white-space: nowrap;
}
.kline {
  --strategy-gutter: 0px;
  --ma-5: #e1bd6e;
  --ma-10: #72b5ff;
  --ma-20: #e2a2c0;
  --ma-60: #b4a0ef;
  --boll: #78d1ca;
  --dc: #c7b692;
  --ema-20: #e59a6e;
  min-width: 0;
}
.kline-with-levels {
  --strategy-gutter: 116px;
}
.kline.kline-with-levels > .kline-axes {
  min-height: 170px;
}
:global(:root[data-theme='light'] .kline) {
  --ma-5: #996300;
  --ma-10: #236ec2;
  --ma-20: #a54570;
  --ma-60: #7855bc;
  --boll: #187f78;
  --dc: #77623c;
  --ema-20: #b15a27;
}
.kline-toolbar,
.kline-legend,
.kline-day,
.kline-navigation,
.kline-volume-readout {
  display: flex;
  align-items: center;
}
.kline-toolbar {
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.kline-adjustment {
  font-size: 12px;
  font-weight: 600;
}
.kline-adjustment span {
  color: var(--muted);
  font-size: 10px;
  font-weight: 400;
  margin-left: 6px;
}
.kline-readout {
  background: var(--panel2);
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  padding: 12px;
}
.kline-indicators {
  display: grid;
  gap: 6px;
  margin-bottom: 12px;
}
.indicator-group {
  display: flex;
  align-items: center;
  gap: 4px 6px;
  flex-wrap: wrap;
  font-size: 11px;
}
.indicator-group-title {
  color: var(--muted);
  margin-right: 2px;
}
.indicator-group label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  color: var(--muted);
  cursor: pointer;
}
.indicator-group label.active {
  background: var(--panel2);
  color: var(--text-secondary);
}
.indicator-group input {
  margin: 0;
  accent-color: var(--accent);
}
.indicator-group small {
  color: var(--muted);
  font-size: 10px;
}
.kline-day {
  flex-wrap: wrap;
  gap: 10px;
  font: 12px var(--font-numeric);
}
.kline-day strong {
  font-weight: 500;
}
.kline-day small {
  font-size: 10px;
}
.kline-return {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.kline-return b {
  font-weight: 500;
}
.kline-return-label,
.kline-return-asof {
  color: var(--muted);
}
.kline-return-asof {
  font-size: 10px;
}
.kline-ohlc {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin: 12px 0 0;
  font-size: 12px;
}
.kline-ohlc > div {
  display: flex;
  gap: 8px;
  align-items: center;
}
.kline-ohlc dt {
  color: var(--muted);
}
.kline-ohlc dd {
  margin: 0;
  font-family: var(--font-numeric);
}
.kline-legend {
  gap: 12px;
  flex-wrap: wrap;
  font: 10px var(--font-numeric);
  min-height: 38px;
  padding: 10px 0;
}
.kline-legend b {
  font-weight: 400;
}
.ma-5,
.dif-line {
  color: var(--ma-5);
}
.ma-10,
.dea-line {
  color: var(--ma-10);
}
.ma-20 {
  color: var(--ma-20);
}
.ma-60 {
  color: var(--ma-60);
}
.ema-20 {
  color: var(--ema-20);
}
.kline-main-plot {
  position: relative;
  flex: 1;
  min-width: 0;
}
.kline-main-plot::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  z-index: 1;
  border-left: 1px solid var(--line);
  pointer-events: none;
}
.boll-line {
  color: var(--boll);
}
.dc-line {
  color: var(--dc);
}
.kline-axes {
  position: relative;
  display: flex;
  height: 256px;
  gap: 8px;
  margin-top: 6px;
  padding-right: var(--strategy-gutter);
}
.kline-y {
  position: relative;
  width: 58px;
  flex-shrink: 0;
  font: 10px var(--font-numeric);
  color: var(--muted);
}
.kline-y span {
  position: absolute;
  right: 0;
  transform: translateY(-50%);
  white-space: nowrap;
}
.kline-pane {
  border-top: 1px solid var(--line);
}
.pane-legend {
  min-height: 30px;
  padding: 8px 0 4px;
  color: var(--muted);
  gap: 8px 12px;
}
.pane-legend strong {
  font-weight: 500;
  color: var(--text-secondary);
}
.volume-axes {
  height: 72px;
}
.volume-axes .kline-y {
  font-size: 9px;
}
.macd-axes {
  height: 120px;
}
.kline-main-plot {
  touch-action: pan-y pinch-zoom;
}
.kline-plot {
  display: block;
  min-width: 0;
  width: 100%;
  height: 100%;
  touch-action: pan-y pinch-zoom;
  outline-offset: 2px;
}
.kline-plot:focus-visible {
  outline: 2px solid var(--accent);
}
.kline-x {
  display: flex;
  justify-content: space-between;
  margin: 6px var(--strategy-gutter) 12px 66px;
  color: var(--muted);
  font: 10px var(--font-numeric);
}
.kline-volume-readout {
  gap: 8px 20px;
  flex-wrap: wrap;
  font-size: 11px;
  color: var(--muted);
  padding: 10px 0;
  border-top: 1px solid var(--line);
}
.kline-volume-readout strong {
  font: 11px var(--font-numeric);
  color: var(--text-secondary);
}
.kline-navigation {
  gap: 10px;
  flex-wrap: wrap;
  padding: 4px 0 10px;
}
.kline-navigation > span {
  margin-right: auto;
  color: var(--muted);
  font: 10px var(--font-numeric);
}
.previous-icon {
  transform: rotate(180deg);
}
.kline-note {
  color: var(--muted);
  font-size: 11px;
  line-height: 1.8;
  margin-top: 5px;
}
.kline-help {
  color: var(--muted);
  font-size: 11px;
  line-height: 1.8;
  margin-top: 8px;
}
.kline-help summary {
  cursor: pointer;
  width: fit-content;
}
.kline-help p {
  margin: 8px 0;
}
@media (max-width: 600px) {
  .kline-axes {
    gap: 6px;
  }
  .kline-y {
    width: 48px;
    font-size: 9px;
  }
  .kline-x {
    margin-left: 54px;
    font-size: 9px;
  }
  .kline-ohlc {
    gap: 6px;
    font-size: 11px;
  }
  .kline-ohlc > div {
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
  }
  .kline-legend {
    gap: 8px;
  }
  .kline-navigation {
    gap: 6px;
  }
  .kline-navigation .button {
    padding: 5px 7px;
  }
  .kline-readout {
    padding: 10px;
  }
}

.kline-compact {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.kline-compact > .kline-toolbar {
  min-height: 24px;
  margin-bottom: 3px;
  gap: 4px 8px;
  flex-shrink: 0;
}
.kline-compact > .kline-readout {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 7px;
  flex-shrink: 0;
  flex-wrap: wrap;
}
.kline-compact .kline-day {
  font-size: 10px;
  gap: 6px;
}
.kline-compact .kline-day small {
  display: none;
}
.kline-compact .kline-ohlc {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin: 0;
  font-size: 10px;
}
.kline-compact .kline-ohlc > div {
  flex-direction: row;
  gap: 4px;
}
.kline-compact > .kline-legend {
  min-height: 18px;
  padding: 2px 0;
  margin: 3px 0 0;
  font-size: 10px;
  flex-shrink: 0;
}
.kline-compact > .kline-axes {
  flex: 1;
  height: auto;
  min-height: 75px;
  margin-top: 3px;
}
.kline-compact > .kline-pane {
  flex-shrink: 0;
}
.kline-compact .pane-legend {
  min-height: 18px;
  padding: 2px 0;
  font-size: 9px;
}
.kline-compact .volume-axes {
  height: 32px;
  margin-top: 0;
}
.kline-compact .macd-axes {
  height: 45px;
  margin-top: 0;
}
.kline-compact > .kline-x {
  margin-top: 2px;
  margin-bottom: 2px;
  font-size: 9px;
  flex-shrink: 0;
}
.kline-compact > .kline-navigation {
  padding: 2px 0 0;
  gap: 7px;
  flex-shrink: 0;
  margin: 0;
  font-size: 9px;
}
.kline-compact > .kline-navigation .button {
  min-height: 20px;
  padding: 2px 5px;
  font-size: 9px;
}
.kline-compact > .kline-navigation .text-button {
  font-size: 9px;
}
@media (max-width: 900px) {
  .kline-compact {
    flex: none;
  }
  .kline-compact > .kline-axes {
    flex: none;
    height: clamp(240px, 40svh, 320px);
  }
  .kline-compact .volume-axes {
    height: 48px;
  }
  .kline-compact .macd-axes {
    height: 90px;
  }
}
@media (max-width: 700px) {
  .kline {
    --strategy-gutter: 0px;
  }
  .kline .kline-axes {
    gap: 4px;
    padding-right: 0;
  }
  .kline .kline-y {
    order: 1;
    width: 42px;
    border-left: 1px solid var(--line);
    font-size: 9px;
  }
  .kline-y span {
    left: 4px;
    right: auto;
  }
  .kline-main-plot::before {
    display: none;
  }
  .kline .kline-x {
    margin-left: 0;
    margin-right: 46px;
  }
  .chart-tools {
    gap: 6px;
  }
  .kline-range-select {
    width: 80px;
  }
  .mobile-structure-toggle {
    flex-shrink: 0;
  }
  .mobile-structure-toggle[aria-pressed='true'] {
    border-color: var(--accent-border);
    background: var(--accent-soft);
    color: var(--accent-text);
  }
  .mobile-structure-setting {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    margin-bottom: 12px;
    font-size: 12px;
  }
  .mobile-structure-setting small {
    flex-basis: 100%;
    color: var(--muted);
  }
  .mobile-strategy-levels {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 12px;
    margin: 6px 0 8px;
    font-size: 10px;
  }
  .mobile-strategy-levels > div {
    display: flex;
    align-items: baseline;
    gap: 4px;
  }
  .mobile-strategy-levels dt {
    border-bottom: 1px dashed currentColor;
  }
  .mobile-strategy-levels dd {
    margin: 0;
    font: 11px var(--font-numeric);
  }
  .kline-compact > .kline-toolbar {
    gap: 3px;
  }
  .kline-compact .kline-adjustment {
    display: none;
  }
  .kline-compact > .kline-readout {
    gap: 3px 8px;
    padding: 3px 5px;
  }
  .kline-compact .kline-ohlc {
    gap: 8px;
    font-size: 9px;
  }
  .kline-compact .kline-legend {
    font-size: 9px;
    gap: 6px;
  }
  .kline-compact .pane-legend {
    min-height: 16px;
  }
}
@media (max-width: 700px) {
  .kline-toolbar {
    flex-wrap: wrap;
  }
  .chart-review-controls {
    gap: 4px 8px;
  }
  .chart-review-controls .text-button {
    min-height: var(--mobile-control-height);
  }
  .chart-review-controls > span {
    font-size: 10px;
  }
  .kline-indicators label {
    min-height: var(--mobile-field-height);
    display: inline-flex;
    align-items: center;
  }
  .indicator-group {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    align-items: stretch;
    gap: 6px;
  }
  .indicator-group-title,
  .indicator-group > .text-button {
    grid-column: 1 / -1;
    justify-self: start;
  }
  .indicator-group label {
    min-width: 0;
    padding: 5px 6px;
    white-space: normal;
  }
  .indicator-group input {
    flex-shrink: 0;
    margin: 0;
  }
  .strategy-level-toggle {
    gap: 2px 10px;
  }
}

.compact-pane-switch {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
@media (max-width: 700px) {
  .compact-pane-switch {
    order: 2;
    flex-basis: 100%;
  }
  .compact-pane-switch button {
    flex: 1;
    min-height: var(--mobile-control-height);
    font-size: 12px;
  }
  .chart-touch-hint {
    margin: 2px 0 4px;
    color: var(--muted);
    font-size: 10px;
    line-height: 16px;
  }
  .kline-compact .kline-navigation {
    display: grid;
    grid-template-columns: 1fr 1fr 1.2fr;
    gap: 6px;
  }
  .kline-compact .kline-navigation > span {
    grid-column: 1 / -1;
    grid-row: 2;
    margin: 0;
    text-align: center;
    font-size: 10px;
  }
  .kline-compact .kline-navigation .button,
  .kline-compact .kline-navigation .text-button {
    min-height: var(--mobile-control-height);
    width: 100%;
    margin: 0;
    font-size: 12px;
  }
  .kline-compact .kline-ohlc {
    gap: 6px;
    font-size: 10px;
  }
}
</style>
