<script setup>
import { computed, ref, watch } from 'vue'
import { money } from '../state'
const props = defineProps({
  points: { type: Array, default: () => [] },
  valueKey: { type: String, default: 'nav' },
  labelKey: { type: String, default: 'at' },
})
const activeIndex = ref(null)
const points = computed(() =>
  props.points.filter((p) => p[props.valueKey] != null && Number.isFinite(Number(p[props.valueKey]))),
)
watch(points, () => {
  activeIndex.value = null
})
const chart = computed(() => {
  const values = points.value.map((p) => Number(p[props.valueKey]))
  if (!values.length) return null
  const minimum = Math.min(...values),
    maximum = Math.max(...values)
  const margin = Math.max((maximum - minimum) * 0.2, Math.abs(maximum) * 0.001, 0.01)
  const low = minimum - margin,
    high = maximum + margin
  const coords = values.map((n, i) => [
    values.length === 1 ? 350 : 10 + (i / (values.length - 1)) * 680,
    180 - ((n - low) / (high - low)) * 160,
  ])
  return {
    coords,
    path: coords.map((p) => p.join(',')).join(' '),
    high,
    low,
  }
})
const selectedIndex = computed(() => activeIndex.value ?? points.value.length - 1)
const selected = computed(() => points.value[selectedIndex.value])
const marker = computed(() => chart.value?.coords[selectedIndex.value])
const selectedDate = computed(() => String(selected.value?.[props.labelKey] || '').slice(0, 10))
function inspect(event) {
  const bounds = event.currentTarget.getBoundingClientRect()
  if (!bounds.width) return
  const ratio = (((event.clientX - bounds.left) / bounds.width) * 700 - 10) / 680
  activeIndex.value = Math.min(
    points.value.length - 1,
    Math.max(0, Math.round(ratio * (points.value.length - 1))),
  )
}
function step(direction) {
  activeIndex.value = Math.min(points.value.length - 1, Math.max(0, selectedIndex.value + direction))
}
</script>
<template>
  <div class="equity-chart" v-if="chart">
    <div class="equity-readout">
      <span
        >{{ selectedDate }}<small>{{ activeIndex === null ? '最新记录' : '历史权益' }}</small></span
      >
      <strong>{{ money(selected[valueKey]) }}<small>CNY</small></strong>
    </div>
    <div class="equity-plot">
      <div class="equity-axis" aria-hidden="true">
        <span>{{ money(chart.high) }}</span
        ><span>{{ money((chart.high + chart.low) / 2) }}</span
        ><span>{{ money(chart.low) }}</span>
      </div>
      <div class="equity-canvas">
        <svg
          viewBox="0 0 700 210"
          preserveAspectRatio="none"
          role="slider"
          tabindex="0"
          aria-label="历史资产走势，使用左右方向键查看逐日权益"
          :aria-valuemin="1"
          :aria-valuemax="points.length"
          :aria-valuenow="selectedIndex + 1"
          :aria-valuetext="`${selectedDate}，权益 ${money(selected[valueKey])} 元`"
          @pointermove="inspect"
          @pointerleave="activeIndex = null"
          @keydown.left.prevent="step(-1)"
          @keydown.right.prevent="step(1)"
          @keydown.home.prevent="activeIndex = 0"
          @keydown.end.prevent="activeIndex = points.length - 1"
          @blur="activeIndex = null"
        >
          <path
            d="M0 20h700 M0 100h700 M0 180h700"
            stroke="var(--chart-grid)"
            stroke-dasharray="3 5"
            fill="none"
            vector-effect="non-scaling-stroke"
          />
          <polyline
            v-if="points.length > 1"
            :points="chart.path"
            fill="none"
            stroke="var(--accent)"
            stroke-width="1.5"
            stroke-linejoin="round"
            vector-effect="non-scaling-stroke"
          />
          <line
            v-if="activeIndex !== null"
            :x1="marker[0]"
            :x2="marker[0]"
            y1="20"
            y2="190"
            stroke="var(--muted)"
            stroke-dasharray="3 4"
            vector-effect="non-scaling-stroke"
          />
          <circle
            :cx="marker[0]"
            :cy="marker[1]"
            r="3.5"
            fill="var(--accent)"
            stroke="var(--panel)"
            stroke-width="2"
            vector-effect="non-scaling-stroke"
          />
        </svg>
        <div class="equity-dates">
          <span>{{ String(points[0][labelKey]).slice(0, 10) }}</span
          ><span v-if="points.length > 1">{{
            String(points[points.length - 1][labelKey]).slice(0, 10)
          }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
<style scoped>
.equity-chart {
  display: flex;
  flex-direction: column;
  padding: 10px 14px 8px;
}
.equity-readout {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  min-height: 22px;
  color: var(--text-secondary);
  font-size: 12px;
}
.equity-readout small {
  margin-left: 8px;
  color: var(--muted);
  font-size: 10px;
  font-weight: 400;
}
.equity-readout strong {
  font-size: 14px;
  font-weight: 500;
}
.equity-plot {
  display: flex;
  flex: 1;
  min-height: 0;
  gap: 10px;
}
.equity-axis {
  position: relative;
  flex: 0 0 11ch;
  color: var(--muted);
  font-size: 10px;
  margin-bottom: 20px;
}
.equity-axis span {
  position: absolute;
  top: 9.52381%;
  transform: translateY(-50%);
  white-space: nowrap;
}
.equity-axis span:nth-child(2) {
  top: 47.61905%;
}
.equity-axis span:nth-child(3) {
  top: 85.71429%;
}
.equity-canvas {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
}
.equity-canvas svg {
  flex: 1;
  min-height: 0;
  width: 100%;
  height: 0;
  overflow: visible;
  cursor: crosshair;
  touch-action: pan-y;
  border-radius: 2px;
}
.equity-canvas svg:focus-visible {
  outline: 1px solid var(--accent);
  outline-offset: 3px;
}
.equity-dates {
  display: flex;
  justify-content: space-between;
  height: 20px;
  padding-top: 4px;
  font-size: 10px;
  color: var(--muted);
}
@media (max-width: 700px) {
  .equity-chart {
    padding: 10px 12px;
  }
  .equity-readout {
    font-size: 11px;
  }
  .equity-readout strong {
    font-size: 15px;
  }
  .equity-readout > span small {
    display: none;
  }
  .equity-plot {
    gap: 4px;
  }
  .equity-axis {
    flex-basis: 10ch;
    font-size: 9px;
  }
}
</style>
