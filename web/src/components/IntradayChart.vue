<script setup>
import { displayCode } from '../display-code.js'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { money, pct } from '../state'
import { createChartPointer } from '../chart-pointer'
import TradeMarkers from './TradeMarkers.vue'
import TradeLegend from './TradeLegend.vue'
import TradeTape from './TradeTape.vue'
import Icon from './Icon.vue'
import WorkbenchDialog from './WorkbenchDialog.vue'
import { intradayTradeGroups } from '../trade-observation.js'
import { intradayGeometry } from '../intraday-levels.js'
import { intradayLinePrice } from '../intraday-line.js'
const props = defineProps({
  data: { type: Object, required: true },
  compact: Boolean,
  fit: Boolean,
  name: String,
  priceUnit: { type: String, default: '元' },
  priceDigits: { type: Number, default: 3 },
  tradeHistory: Object,
  referenceLevels: { type: Array, default: () => [] },
  tLevels: { type: Array, default: () => [] },
})
const selected = ref(null),
  settingsOpen = ref(false),
  showTrades = ref(true),
  tradeTape = ref(null)
const groups = computed(() => intradayTradeGroups(props.tradeHistory?.items, props.data))
const located = computed(() =>
  groups.value.map((group) => ({
    ...group,
    linePrice: intradayLinePrice(chart.value.plotted, group.timestamp),
  })),
)
const anchors = computed(() =>
  !showTrades.value
    ? []
    : located.value
        .filter((g) => g.linePrice !== null)
        .map((g) => ({
          ...g,
          x: g.minute / 240,
          y: (110 - ((g.linePrice - chart.value.previous) / chart.value.spread) * 100) / 220,
          title: `${g.day} ${g.time} ${g.label} ${g.actionLabel} · ${g.items.length} 笔 · 成交${g.items.length > 1 ? '均' : ''}价 ${money(g.price, 3)} · 标记按成交时间贴合分时线，点击查看依据`,
        })),
)
function focusTrade(group) {
  selected.value = chart.value.plotted.reduce(
    (best, p, index, rows) =>
      Math.abs(p.minute - group.minute) < Math.abs(rows[best].minute - group.minute) ? index : best,
    0,
  )
}
const showLevels = ref(true),
  showTLevels = ref(true),
  plot = ref(null),
  plotHeight = ref(220)
let observer
onMounted(() => {
  if (props.compact || !plot.value) return
  observer = new ResizeObserver(([entry]) => {
    if (entry.contentRect.height > 0) plotHeight.value = entry.contentRect.height
  })
  observer.observe(plot.value)
})
onUnmounted(() => observer?.disconnect())
const chart = computed(() =>
  intradayGeometry(
    props.data,
    !props.compact && showLevels.value ? props.referenceLevels : [],
    plotHeight.value,
    [],
    !props.compact && showTLevels.value ? props.tLevels : [],
  ),
)
const current = computed(() => chart.value.plotted[selected.value] || chart.value.plotted.at(-1))
const chartPointer = createChartPointer((event) => {
  if (!props.compact) inspect(event)
})
function inspect(event) {
  const bounds = event.currentTarget.getBoundingClientRect()
  const target = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)) * 240
  selected.value = chart.value.plotted.reduce(
    (best, p, index, rows) =>
      Math.abs(p.minute - target) < Math.abs(rows[best].minute - target) ? index : best,
    0,
  )
}
function step(event) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const last = chart.value.plotted.length - 1
  selected.value =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? last
        : Math.max(0, Math.min(last, (selected.value ?? last) + (event.key === 'ArrowLeft' ? -1 : 1)))
}
</script>
<template>
  <div :class="['intraday-chart', { compact, fit }]" v-if="chart.plotted.length">
    <div class="intraday-readout" v-if="!compact && current">
      <span>{{ current.time }}</span>
      <strong>{{ money(current.price, priceDigits) }} {{ priceUnit }}</strong>
      <span :class="Number(current.price) >= chart.previous ? 'up' : 'down'">{{
        pct(Number(current.price) / chart.previous - 1, true)
      }}</span>
      <small>昨收 {{ money(chart.previous, priceDigits) }}</small>
      <button
        v-if="referenceLevels.length || tLevels.length || tradeHistory"
        class="chart-settings-button"
        aria-haspopup="dialog"
        :aria-expanded="settingsOpen"
        @click="settingsOpen = true"
      >
        <Icon name="settings" :size="14" />图表设置
      </button>
    </div>
    <WorkbenchDialog v-if="!compact" v-model:open="settingsOpen" title="分时图表设置">
      <div v-if="tLevels.length" class="intraday-display-setting">
        <label><input v-model="showTLevels" type="checkbox" />显示做 T 参考价</label>
        <small>虚线标出 5 分钟 K 的买回支撑、卖出压力及失效价；实际成交以 T 标记。</small>
      </div>
      <div v-if="referenceLevels.length" class="intraday-display-setting">
        <label><input v-model="showLevels" type="checkbox" />显示附近关键点位</label>
        <small>仅绘制现价上下最近的点位，远离当日波动范围的点位自动隐藏。</small>
        <p v-if="showLevels && !chart.references.length" class="nearby-empty">当前价格附近暂无关键点位</p>
      </div>
      <div v-if="tradeHistory" class="intraday-display-setting">
        <label><input v-model="showTrades" type="checkbox" />显示成交 B/S/T</label>
        <TradeLegend />
        <small>标记按成交时间贴合分时线，实际成交价见成交明细；缺少连续行情时保留图下明细。</small>
      </div>
    </WorkbenchDialog>
    <div class="intraday-touch-tools" v-if="!compact">
      <span>左右拖动查看价格 · 上下滑动浏览页面</span>
      <button class="text-button" v-if="selected !== null" @click="selected = null">回到最新</button>
    </div>
    <div class="intraday-axes" data-touch-surface>
      <div class="intraday-y" v-if="!compact">
        <span>{{ money(chart.high, priceDigits) }}</span
        ><span>{{ money(chart.previous, priceDigits) }}</span
        ><span>{{ money(chart.low, priceDigits) }}</span>
      </div>
      <div class="intraday-plot">
        <div ref="plot" class="intraday-plot-area">
          <svg
            class="intraday-plot-svg"
            viewBox="0 0 600 220"
            preserveAspectRatio="none"
            :tabindex="compact ? undefined : 0"
            role="img"
            :aria-label="`${displayCode(name || data.symbol)} ${data.day} 分时线，${chart.plotted.length} 个分钟价格，昨收 ${money(chart.previous, priceDigits)} ${priceUnit}${compact ? '' : '；左右方向键查看价格'}`"
            @pointerdown="chartPointer.start"
            @pointermove="chartPointer.move"
            @pointerup="chartPointer.end"
            @pointercancel="chartPointer.cancel"
            @pointerleave="$event.pointerType !== 'touch' && (selected = null)"
            @keydown="step"
          >
            <path
              d="M0 110h600"
              fill="none"
              stroke="var(--muted)"
              stroke-dasharray="4 5"
              vector-effect="non-scaling-stroke"
            />
            <path
              v-if="!compact"
              d="M0 10h600 M0 210h600 M300 0v220"
              fill="none"
              stroke="var(--chart-grid)"
              stroke-dasharray="3 5"
              vector-effect="non-scaling-stroke"
            />
            <g v-for="line in chart.references" :key="line.id" :data-intraday-level="line.id">
              <path
                :d="
                  line.kind === 'minute-t'
                    ? `M0 ${line.y}H600`
                    : `M0 ${line.y}H600 M598 ${line.y}V${line.labelY}`
                "
                :stroke="line.color"
                stroke-dasharray="5 4"
                fill="none"
                vector-effect="non-scaling-stroke"
              />
            </g>
            <path
              class="intraday-price-line"
              :d="chart.path"
              fill="none"
              :stroke="
                compact
                  ? Number(chart.plotted.at(-1).price) >= chart.previous
                    ? 'var(--red)'
                    : 'var(--green)'
                  : 'var(--accent)'
              "
              stroke-width="1.7"
              vector-effect="non-scaling-stroke"
            />
            <circle :cx="chart.plotted.at(-1).x" :cy="chart.plotted.at(-1).y" r="3" fill="var(--accent)" />
            <template v-if="!compact && selected !== null && current">
              <path
                :d="`M${current.x} 0v220`"
                stroke="var(--muted)"
                stroke-dasharray="3 3"
                vector-effect="non-scaling-stroke"
              />
              <circle :cx="current.x" :cy="current.y" r="4" fill="var(--accent)" />
            </template>
          </svg>
          <TradeMarkers v-if="!compact" :anchors="anchors" @select="tradeTape?.show($event)" />
          <div
            v-if="chart.references.length"
            class="intraday-price-labels"
            aria-label="分时关键点位与做 T 参考价"
          >
            <span
              v-for="line in chart.references"
              :key="line.id"
              :data-level-label="line.id"
              :class="{ 't-reference': line.kind === 'minute-t' }"
              :title="line.title"
              :tabindex="line.title ? 0 : undefined"
              :style="{ top: `${(line.labelY / 220) * 100}%`, color: line.color }"
              >{{ line.label }} {{ money(line.value, priceDigits) }}</span
            >
          </div>
        </div>
        <div class="intraday-x" v-if="!compact">
          <span>09:30</span><span>11:30 / 13:00</span><span>15:00</span>
        </div>
      </div>
    </div>
    <p
      v-if="!compact && showTrades && located.some((group) => group.linePrice === null)"
      class="intraday-unplaced-note"
    >
      部分成交时刻暂无连续行情，暂未在曲线上定位，可查看下方成交明细。
    </p>
    <TradeTape
      v-if="!compact && tradeHistory"
      ref="tradeTape"
      :groups="groups"
      :history="tradeHistory"
      :day="data.day"
      mode="intraday"
      @focus="focusTrade"
    />
  </div>
</template>
<style scoped>
.intraday-unplaced-note {
  margin: 6px 0;
  color: var(--muted);
  font-size: 11px;
}
.intraday-display-setting {
  display: grid;
  gap: 12px;
  margin-bottom: 16px;
  font-size: 12px;
}
.intraday-display-setting label {
  display: flex;
  gap: 8px;
  align-items: center;
}
.intraday-display-setting input {
  accent-color: var(--accent);
}
.intraday-display-setting small {
  font-size: 11px;
  color: var(--muted);
}
.chart-settings-button {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 30px;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  padding: 4px 8px;
  color: var(--text-secondary);
  background: var(--panel);
  font-size: 11px;
  white-space: nowrap;
}
.chart-settings-button:hover {
  color: var(--accent-text);
  border-color: var(--accent-border);
  background: var(--accent-soft);
}
@media (max-width: 700px) {
  .chart-settings-button,
  .intraday-display-setting label {
    min-height: 44px;
    font-size: 12px;
  }
}

.intraday-chart {
  --ma-60: #b4a0ef;
}
:global(:root[data-theme='light'] .intraday-chart) {
  --ma-60: #7855bc;
}
.intraday-readout {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.intraday-readout small {
  color: var(--muted);
  margin-left: auto;
}
.nearby-empty {
  flex-shrink: 0;
  margin: 0 0 5px;
  color: var(--muted);
  font-size: 10px;
}
.intraday-plot-area {
  position: relative;
}
.intraday-price-labels {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.intraday-price-labels span {
  position: absolute;
  right: 2px;
  transform: translateY(-50%);
  background: var(--panel);
  border-left: 2px solid currentColor;
  padding: 1px 4px;
  font-size: 10px;
  line-height: 15px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  pointer-events: auto;
}
.intraday-price-labels .t-reference {
  border-left: 0;
}
.intraday-axes {
  display: flex;
  gap: 8px;
}
.intraday-y {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 5px 0 22px;
  color: var(--muted);
  font-size: 10px;
  min-width: 46px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.intraday-plot {
  flex: 1;
  min-width: 0;
}
.intraday-plot-svg {
  display: block;
  width: 100%;
  height: 190px;
  overflow: visible;
}
.intraday-x {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: var(--muted);
  margin-top: 6px;
}
.compact .intraday-plot-svg {
  width: 116px;
  height: 26px;
}
@media (max-width: 600px) {
  .intraday-plot-svg {
    height: 155px;
  }
  .intraday-x {
    font-size: 9px;
  }
}
.intraday-chart.fit {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}
.fit .intraday-readout {
  flex-shrink: 0;
  margin-bottom: 6px;
}
.fit .intraday-axes {
  flex: 1;
  min-height: 0;
}
.fit .intraday-plot {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.fit .intraday-plot-area {
  flex: 1;
  height: 0;
  min-height: 60px;
}
.fit .intraday-plot-svg {
  height: 100%;
}
.fit .intraday-x {
  flex-shrink: 0;
}

.intraday-plot-svg {
  touch-action: pan-y pinch-zoom;
}
.intraday-touch-tools {
  display: none;
}
@media (max-width: 700px) {
  .intraday-touch-tools {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 4px;
    margin-bottom: 6px;
    color: var(--muted);
    font-size: 10px;
  }
  .intraday-readout {
    gap: 6px 10px;
  }
  .intraday-readout strong {
    font: 600 22px var(--font-numeric);
  }
  .intraday-readout > span:nth-of-type(2) {
    font-size: 15px;
  }
}
</style>
