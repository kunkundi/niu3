<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { drawPriceActionLayers } from '../price-action/renderer.js'
import { candlePlotLayout } from '../chart-layout.js'

const props = defineProps({
  analysis: Object,
  bars: Array,
  visibleCount: Number,
  layers: Object,
  offset: Number,
  low: Number,
  high: Number,
  top: Number,
  bottom: Number,
  height: Number,
})
const canvas = ref(null)
let sizeObserver, themeObserver
function draw() {
  const element = canvas.value
  if (!element) return
  const { width, height } = element.getBoundingClientRect()
  if (!width || !height) return
  const ratio = window.devicePixelRatio || 1
  element.width = Math.round(width * ratio)
  element.height = Math.round(height * ratio)
  const context = element.getContext('2d')
  if (!context) return
  context.setTransform(ratio, 0, 0, ratio, 0, 0)
  context.clearRect(0, 0, width, height)
  if (!props.analysis || !props.bars?.length) return
  const styles = getComputedStyle(element)
  const color = (key) => styles.getPropertyValue(key).trim()
  const layout = candlePlotLayout(width, props.visibleCount || props.bars.length)
  const barWidth = layout.barWidth
  const plotRight = Math.min(layout.right, layout.left + barWidth * props.bars.length)
  context.save()
  context.beginPath()
  context.rect(layout.left, 0, plotRight - layout.left, height)
  context.clip()
  drawPriceActionLayers(
    context,
    props.bars,
    props.analysis,
    {
      width,
      height,
      plotLeft: layout.left,
      plotRight,
      plotTop: (props.top / props.height) * height,
      plotBottom: (props.bottom / props.height) * height,
      priceMin: props.low,
      priceMax: props.high,
      barWidth,
      barOriginX: layout.left,
    },
    document.documentElement.dataset.theme === 'light' ? 'light' : 'dark',
    props.offset,
    { ...props.layers, ema20: false, volume: false },
    {
      rise: color('--red'),
      fall: color('--green'),
      background: color('--panel'),
    },
  )
  context.restore()
}
watch(
  () => [props.analysis, props.bars, props.layers, props.low, props.high, props.offset, props.visibleCount],
  draw,
  {
    deep: true,
    flush: 'post',
  },
)
onMounted(() => {
  sizeObserver = new ResizeObserver(draw)
  sizeObserver.observe(canvas.value)
  themeObserver = new MutationObserver(draw)
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  draw()
  // Canvas labels need a redraw after the bundled numeric font finishes loading.
  document.fonts?.ready.then(draw)
})
onBeforeUnmount(() => {
  sizeObserver?.disconnect()
  themeObserver?.disconnect()
})
</script>
<template><canvas ref="canvas" class="pa-overlay" aria-hidden="true" /></template>
<style scoped>
.pa-overlay {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
</style>
