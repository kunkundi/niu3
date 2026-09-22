<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { displayCode } from '../display-code.js'
import { money, pct } from '../state'
import { tradeIntradayChart } from '../trade-intraday-chart.js'
import { minuteTLevels, tradeIntradayDays } from '../minute-t-levels.js'
import TradePoints from './TradePoints.vue'

const props = defineProps({
  security: { type: Object, required: true },
  day: String,
  data: Object,
  minuteT: Object,
  loading: Boolean,
  error: String,
  expanded: Boolean,
})
const emit = defineEmits(['update:day', 'select', 'expand', 'retry'])
const days = computed(() => tradeIntradayDays(props.security))
const symbol = computed(() => props.security.symbol)
const latest = computed(() => props.security.events.find((event) => event.day === props.day))
const plot = ref(null),
  width = ref(280)
const chart = computed(() =>
  tradeIntradayChart(
    props.data,
    props.security.events,
    props.day,
    width.value,
    minuteTLevels(props.minuteT, props.data),
    props.expanded ? 360 : 124,
  ),
)
const change = computed(() =>
  chart.value ? Number(chart.value.last.price) / chart.value.previous - 1 : null,
)
let observer
watch(
  plot,
  (element) => {
    observer?.disconnect()
    if (!element) return
    observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) width.value = entry.contentRect.width
    })
    observer.observe(element)
  },
  { flush: 'post' },
)
onUnmounted(() => observer?.disconnect())
</script>

<template>
  <article
    class="trade-intraday-card"
    :class="{ 'is-expanded': expanded }"
    :aria-label="`${displayCode(security.name)} 成交分时图`"
  >
    <header class="intraday-card-heading">
      <div>
        <h3>{{ displayCode(security.name) }}</h3>
        <small>{{ displayCode(symbol) }}</small>
      </div>
      <div v-if="chart" class="intraday-card-price" :class="change >= 0 ? 'buy' : 'sell'">
        <strong>{{ money(chart.last.price, 3) }}</strong
        ><small>{{ pct(change, true) }}</small>
      </div>
    </header>
    <div class="intraday-card-meta">
      <label
        ><span>交易日</span
        ><select
          :value="day"
          :aria-label="`${displayCode(security.name)}分时日期`"
          @change="emit('update:day', $event.target.value)"
        >
          <option v-for="date in days" :key="date" :value="date">{{ date }}</option>
        </select></label
      >
      <div class="intraday-card-tools">
        <span>{{ chart ? `分时至 ${chart.last.time}` : '分时采样' }}</span>
        <button
          v-if="chart && !expanded"
          type="button"
          class="intraday-card-expand"
          :aria-label="`放大查看${displayCode(security.name)} ${day} 分时图`"
          aria-haspopup="dialog"
          @click="emit('expand')"
        >
          放大 ↗
        </button>
      </div>
    </div>
    <div
      v-if="chart"
      class="intraday-card-axes"
      :class="{ 'can-expand': !expanded }"
      @click="!expanded && emit('expand')"
    >
      <div class="intraday-card-y" aria-hidden="true">
        <b class="intraday-card-y-title">价格/元</b>
        <span :style="{ top: `${chart.top}px` }">{{ money(chart.high, 3) }}</span>
        <span class="intraday-card-zero-label" :style="{ top: `${chart.previousY}px` }">
          {{ money(chart.previous, 3) }}<small>0% 昨收</small>
        </span>
        <span :style="{ top: `${chart.bottom}px` }">{{ money(chart.low, 3) }}</span>
      </div>
      <div ref="plot" class="intraday-card-plot" :style="{ height: `${chart.height}px` }">
        <svg
          :viewBox="`0 0 ${chart.width} ${chart.height}`"
          preserveAspectRatio="none"
          role="img"
          :aria-label="`${displayCode(security.name)} ${day} 分时线，09:30至15:00，已采集至${chart.last.time}；左侧 Y 轴为价格（元），0% 水平轴对应昨收 ${money(chart.previous, 3)} 元；买卖点按成交时间贴合分时线，悬停查看交易数据，点击查看详情`"
        >
          <path
            class="intraday-card-price-axis"
            :d="`M0.5 ${chart.top}V${chart.bottom}M0.5 ${chart.top}H5M0.5 ${chart.previousY}H5M0.5 ${chart.bottom}H5`"
            fill="none"
          />
          <path
            class="intraday-card-zero-axis"
            :d="`M0.5 ${chart.previousY}H${chart.width - 1}`"
            fill="none"
          />
          <g v-for="line in chart.references" :key="line.id" :data-intraday-level="line.id">
            <path
              :d="`M0 ${line.y}H${chart.width}`"
              :stroke="line.color"
              stroke-dasharray="4 3"
              fill="none"
              vector-effect="non-scaling-stroke"
            />
          </g>
          <path
            class="trade-intraday-line"
            :d="chart.path"
            fill="none"
            stroke="var(--accent)"
            stroke-width="1.5"
            stroke-linejoin="round"
          />
          <circle :cx="chart.last.x" :cy="chart.last.y" r="2" fill="var(--accent)" />
        </svg>
        <div class="intraday-card-levels" v-if="chart.references.length" aria-label="5 分钟做 T 参考价">
          <span
            v-for="line in chart.references"
            :key="line.id"
            :data-level-label="line.id"
            :style="{ top: `${line.labelY}px`, color: line.color }"
            tabindex="0"
            :title="line.title"
            >{{ line.label }} {{ money(line.value, 3) }}</span
          >
        </div>
        <TradePoints
          :anchors="chart.anchors"
          :width="chart.width"
          :height="chart.height"
          @select="emit('select', $event.event)"
        />
      </div>
      <div class="intraday-card-x"><span>09:30</span><span>11:30 / 13:00</span><span>15:00</span></div>
    </div>
    <div v-else class="intraday-card-empty" role="status">
      {{ loading ? '正在读取当日分时…' : error || data?.message || '暂无该日分时'
      }}<button v-if="!loading" @click="emit('retry')">重试</button>
    </div>
    <template v-if="chart">
      <p class="intraday-card-note">买卖点按成交时间贴线；悬停查看交易数据，点击查看详情。</p>
      <details v-if="chart.unplaced.length" class="intraday-card-unplaced">
        <summary>{{ chart.unplaced.length }} 个成交点缺少连续行情 · 查看明细</summary>
        <button
          v-for="marker in chart.unplaced"
          :key="marker.id"
          type="button"
          @click="emit('select', marker.event)"
        >
          <b :class="marker.side === 'BUY' ? 'buy' : 'sell'">{{ marker.label }}</b>
          <span>{{ marker.labelDetails.join(' · ') }}</span
          ><small>详情 ↗</small>
        </button>
      </details>
    </template>
    <p v-if="error && chart" class="intraday-card-warning" role="status">{{ error }}</p>
    <button
      v-if="latest"
      class="intraday-card-trade"
      @click="emit('select', latest)"
      :aria-label="`${latest.day} ${latest.end} ${latest.side === 'BUY' ? '买入' : '卖出'}${displayCode(security.name)}成交详情`"
    >
      <b :class="latest.side === 'BUY' ? 'buy' : 'sell'">{{ latest.side === 'BUY' ? '买入' : '卖出' }}</b>
      <time>{{ latest.end }}</time
      ><span>{{ money(latest.quantity, 0) }} 份</span><small>详情 ↗</small>
    </button>
  </article>
</template>

<style scoped>
.trade-intraday-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  border: 1px solid var(--line);
  background: var(--panel);
}
.intraday-card-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 8px 10px 4px;
}
.intraday-card-heading h3 {
  font-size: 13px;
  font-weight: 600;
  margin: 0;
  overflow-wrap: anywhere;
}
.intraday-card-heading small {
  font-size: 10px;
  color: var(--muted);
}
.intraday-card-price {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  font-family: var(--font-numeric);
  flex-shrink: 0;
}
.intraday-card-price strong {
  font-size: 16px;
  font-weight: 550;
}
.intraday-card-price small {
  color: inherit;
}
.intraday-card-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
  padding: 0 10px;
  font-size: 10px;
  color: var(--muted);
}
.intraday-card-meta label {
  display: flex;
  align-items: center;
  gap: 4px;
}
.intraday-card-meta select {
  background: transparent;
  color: var(--ink);
  border: 0;
  padding: 3px 2px;
  min-height: 28px;
  font-size: 11px;
  width: auto;
}
.intraday-card-tools {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 0 6px;
}
.intraday-card-expand {
  padding: 4px 0;
  min-height: 32px;
  border: 0;
  background: transparent;
  color: var(--accent);
  font-size: 11px;
  white-space: nowrap;
}
.can-expand {
  cursor: zoom-in;
}
.is-expanded .intraday-card-heading {
  padding-top: 12px;
}
.is-expanded .intraday-card-heading h3 {
  font-size: 16px;
}
.is-expanded .intraday-card-price strong {
  font-size: 20px;
}
.is-expanded .intraday-card-meta {
  margin-bottom: 8px;
}
.intraday-card-axes {
  padding: 0 8px 0 39px;
  position: relative;
}
.intraday-card-y {
  position: absolute;
  left: 3px;
  top: 0;
  width: 31px;
  text-align: right;
  font: 9px var(--font-numeric);
  color: var(--muted);
}
.intraday-card-y span {
  position: absolute;
  right: 0;
  transform: translateY(-50%);
}
.intraday-card-y-title {
  display: block;
  padding-top: 2px;
  font-weight: 500;
  white-space: nowrap;
}
.intraday-card-y .intraday-card-zero-label {
  color: var(--ink);
  font-weight: 600;
}
.intraday-card-zero-label small {
  position: absolute;
  top: 12px;
  right: 0;
  white-space: nowrap;
  font-size: 9px;
  font-weight: 500;
  color: var(--muted);
}
.intraday-card-price-axis,
.intraday-card-zero-axis {
  stroke: var(--muted);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}
.intraday-card-zero-axis {
  stroke-opacity: 0.75;
}
.intraday-card-plot {
  position: relative;
  height: 124px;
}
.intraday-card-plot > svg {
  display: block;
  width: 100%;
  height: 100%;
}
.intraday-card-levels {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.intraday-card-levels span {
  position: absolute;
  right: 0;
  transform: translateY(-50%);
  padding: 1px 3px;
  background: var(--panel);
  font: 10px/14px var(--font-numeric);
  white-space: nowrap;
  pointer-events: auto;
}
.intraday-card-x {
  display: flex;
  justify-content: space-between;
  color: var(--muted);
  font: 9px var(--font-numeric);
  padding: 0 0 5px;
}
.intraday-card-trade {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 5px;
  width: 100%;
  min-height: 34px;
  margin-top: auto;
  padding: 7px 10px;
  border: 0;
  border-top: 1px solid var(--line);
  background: transparent;
  color: var(--ink);
  font-size: 11px;
}
.intraday-card-note,
.intraday-card-unplaced summary {
  margin: 0;
  padding: 4px 10px 8px;
  font-size: 10px;
  color: var(--muted);
}
.intraday-card-unplaced summary {
  cursor: pointer;
}
.intraday-card-unplaced button {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 8px;
  width: 100%;
  padding: 6px 10px;
  border: 0;
  border-top: 1px solid var(--line);
  background: transparent;
  text-align: left;
  font-size: 11px;
}
.intraday-card-unplaced span {
  overflow-wrap: anywhere;
}
.intraday-card-unplaced small {
  margin-left: auto;
  color: var(--muted);
}
.intraday-card-trade:hover {
  background: var(--table-hover);
}
.intraday-card-trade b {
  font-weight: 600;
}
.intraday-card-trade time,
.intraday-card-trade span {
  font-family: var(--font-numeric);
}
.intraday-card-trade small {
  color: var(--muted);
  font-size: 10px;
}
.intraday-card-empty {
  min-height: 139px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  justify-content: center;
  align-items: center;
  color: var(--muted);
  font-size: 11px;
}
.intraday-card-empty button {
  background: transparent;
  border: 0;
  color: var(--accent);
}
.intraday-card-warning {
  margin: 0;
  padding: 3px 10px;
  color: var(--amber);
  font-size: 10px;
}
.buy {
  color: var(--red);
}
.sell {
  color: var(--green);
}
@media (max-width: 700px) {
  .intraday-card-meta select {
    min-height: 32px;
  }
  .intraday-card-trade {
    min-height: 36px;
  }
}
</style>
