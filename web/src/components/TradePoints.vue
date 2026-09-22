<script setup>
import { computed, ref, useId } from 'vue'

const props = defineProps({
  anchors: { type: Array, default: () => [] },
  width: { type: Number, required: true },
  height: { type: Number, required: true },
})
const emit = defineEmits(['select'])
const activeId = ref(null)
const tooltipId = useId()
const active = computed(() => props.anchors.find((point) => point.id === activeId.value))
const nearby = computed(() =>
  active.value
    ? props.anchors
        .filter(
          (point) =>
            Math.hypot((point.x - active.value.x) * props.width, (point.y - active.value.y) * props.height) <=
            12,
        )
        .sort((a, b) =>
          a.id === activeId.value ? -1 : b.id === activeId.value ? 1 : a.timestamp - b.timestamp,
        )
    : [],
)
const tooltipStyle = computed(() => {
  if (!active.value) return {}
  const width = Math.min(220, props.width)
  return {
    width: `${width}px`,
    left: `${Math.max(0, Math.min(props.width - width, active.value.x * props.width - width / 2))}px`,
    top: `${active.value.y * props.height + 12}px`,
  }
})
function select(point) {
  activeId.value = null
  emit('select', point)
}
</script>

<template>
  <div
    class="trade-points"
    aria-label="图中买卖点"
    @pointerleave="activeId = null"
    @focusout="!$event.currentTarget.contains($event.relatedTarget) && (activeId = null)"
    @keydown.esc.stop="activeId = null"
  >
    <button
      v-for="point in anchors"
      :key="point.id"
      type="button"
      class="trade-point"
      :class="point.side === 'BUY' ? 'buy' : 'sell'"
      :style="{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }"
      data-trade-marker="fill"
      :data-trade-day="point.day"
      :data-trade-effect="point.effect"
      :aria-label="point.title"
      :aria-describedby="activeId === point.id ? tooltipId : undefined"
      @pointerenter="activeId = point.id"
      @focus="activeId = point.id"
      @pointerdown.stop
      @pointerup.stop
      @pointermove.stop
      @click.stop="select(point)"
    >
      <svg viewBox="0 0 12 12" aria-hidden="true">
        <path :d="point.side === 'BUY' ? 'M6 1L11 10H1Z' : 'M1 2H11L6 11Z'" />
      </svg>
    </button>
    <div
      v-if="active"
      :id="tooltipId"
      class="trade-point-tooltip"
      role="tooltip"
      :style="tooltipStyle"
      @pointerdown.stop
      @pointerup.stop
      @pointermove.stop
      @click.stop
    >
      <p>
        {{ active.day }}<span v-if="nearby.length > 1"> · 附近 {{ nearby.length }} 个成交点</span>
      </p>
      <button v-for="point in nearby" :key="point.id" type="button" @click="select(point)">
        <strong :class="point.side === 'BUY' ? 'buy' : 'sell'">{{ point.label }}</strong>
        <span>{{ point.labelDetails[0] }}</span>
        <span>成交份额 {{ point.labelDetails[1] }}</span>
        <span>成交金额 {{ point.labelDetails[2] }}</span>
        <small>查看成交详情 ↗</small>
      </button>
    </div>
  </div>
</template>

<style scoped>
.trade-points {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 3;
}
.buy {
  color: var(--red);
}
.sell {
  color: var(--green);
}
.trade-point {
  position: absolute;
  display: grid;
  place-items: center;
  transform: translate(-50%, -50%);
  width: 16px;
  height: 16px;
  min-height: 0;
  padding: 0;
  border: 0;
  background: transparent;
  pointer-events: auto;
  cursor: pointer;
}
.trade-point svg {
  width: 12px;
  height: 12px;
  overflow: visible;
}
.trade-point path {
  fill: currentColor;
  stroke: var(--panel);
  stroke-width: 1;
  stroke-linejoin: round;
}
.trade-point:hover,
.trade-point:focus-visible {
  z-index: 1;
  outline: 1px solid currentColor;
  outline-offset: 1px;
  border-radius: 50%;
}
.trade-point-tooltip {
  position: absolute;
  max-height: 240px;
  overflow: auto;
  overscroll-behavior: contain;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--panel);
  color: var(--ink);
  box-shadow: 0 4px 16px rgb(0 0 0 / 16%);
  font-size: 11px;
  pointer-events: auto;
  z-index: 2;
}
.trade-point-tooltip p {
  margin: 0 0 6px;
  color: var(--muted);
  font-size: 10px;
}
.trade-point-tooltip button {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  width: 100%;
  padding: 7px 0;
  border: 0;
  border-top: 1px solid var(--line);
  background: transparent;
  color: inherit;
  text-align: left;
  font-size: 11px;
  line-height: 1.4;
}
.trade-point-tooltip button:hover {
  background: var(--table-hover);
}
.trade-point-tooltip span {
  font-family: var(--font-numeric);
}
.trade-point-tooltip small {
  color: var(--muted);
  font-size: 10px;
}
</style>
