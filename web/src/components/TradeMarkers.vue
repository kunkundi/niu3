<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { layoutTradeMarkers } from '../trade-observation.js'
const props = defineProps({ anchors: { type: Array, default: () => [] } })
defineEmits(['select'])
const root = ref(null),
  size = ref({ width: 0, height: 0 })
let observer
watch(
  root,
  (element) => {
    observer?.disconnect()
    if (!element) return
    observer = new ResizeObserver(([entry]) => {
      size.value = entry.contentRect
    })
    observer.observe(element)
  },
  { flush: 'post' },
)
onBeforeUnmount(() => observer?.disconnect())
const placed = computed(() =>
  layoutTradeMarkers(
    props.anchors.map((a) => ({ ...a, x: a.x * size.value.width, y: a.y * size.value.height })),
    size.value.width,
    size.value.height,
  ),
)
function connector(marker) {
  const cx = marker.left + marker.labelWidth / 2,
    cy = marker.top + marker.labelHeight / 2,
    dx = marker.x - cx,
    dy = marker.y - cy,
    scale = Math.max(Math.abs(dx) / (marker.labelWidth / 2), Math.abs(dy) / (marker.labelHeight / 2))
  // Stop at the label's edge so transparent letters are not crossed by their own leader.
  return scale > 1 ? `M${marker.x} ${marker.y}L${cx + dx / scale} ${cy + dy / scale}` : ''
}
</script>
<template>
  <div ref="root" class="trade-markers" aria-label="图中买卖标记">
    <svg :viewBox="`0 0 ${size.width || 1} ${size.height || 1}`" aria-hidden="true">
      <g v-for="m in [...placed].reverse()" :key="m.id" :class="[m.kind, m.effectTone]">
        <path
          :d="connector(m)"
          fill="none"
          stroke="currentColor"
          :stroke-width="m.kind === 'candidate' ? 1 : 1.3"
          :stroke-dasharray="m.leaderDash || (m.kind === 'candidate' ? '3 3' : undefined)"
        />
        <circle
          :cx="m.x"
          :cy="m.y"
          :r="m.kind === 'candidate' ? 2 : 3"
          :fill="m.kind === 'candidate' ? 'var(--panel)' : 'currentColor'"
          :stroke="m.kind === 'candidate' ? 'currentColor' : 'var(--panel)'"
          :stroke-width="m.kind === 'candidate' ? 1 : 2"
        />
      </g>
    </svg>
    <button
      v-for="m in placed"
      :key="m.id"
      :class="[m.kind, m.effectTone, { detailed: m.labelDetails?.length }]"
      :style="{
        left: `${m.left}px`,
        top: `${m.top}px`,
        width: `${m.labelWidth}px`,
        height: `${m.labelHeight}px`,
      }"
      :data-trade-marker="m.kind"
      :data-trade-day="m.day"
      :data-trade-effect="m.effect"
      :title="m.title"
      :aria-label="m.title"
      @pointerdown.stop
      @pointerup.stop
      @pointermove.stop
      @click.stop="$emit('select', m)"
    >
      <span>{{ m.label }}</span>
      <small v-for="(line, index) in m.labelDetails" :key="index">{{ line }}</small>
    </button>
  </div>
</template>
<style scoped>
.trade-markers {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 3;
}
svg {
  position: absolute;
  width: 100%;
  height: 100%;
  overflow: visible;
}
.fill {
  color: var(--trade-color, var(--muted));
}
.candidate {
  color: var(--muted);
}
.setup {
  color: var(--yellow);
}
button {
  position: absolute;
  min-height: 0;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid currentColor;
  border-radius: 3px;
  background: var(--panel);
  font-size: 12px;
  font-weight: 800;
  line-height: 1;
  white-space: nowrap;
  pointer-events: auto;
  box-shadow: 0 0 0 1px var(--panel);
  z-index: 1;
}
button.fill {
  background: transparent;
  color: var(--trade-color, var(--muted));
  border: 0;
  box-shadow: none;
}
button.detailed {
  flex-direction: column;
  gap: 2px;
  padding: 2px 0;
  background: var(--panel);
}
button.detailed small {
  font-family: var(--font-numeric);
  font-size: 10px;
  font-weight: 500;
  line-height: 1;
}
button.candidate {
  border-style: dashed;
  border-color: var(--field-border);
  border-radius: 3px;
  font-size: 10px;
  font-weight: 500;
  box-shadow: none;
  z-index: 0;
}
button:hover {
  opacity: 0.8;
  z-index: 2;
}
button:focus-visible {
  outline: 2px solid var(--ink);
  outline-offset: 2px;
  z-index: 2;
}
</style>
