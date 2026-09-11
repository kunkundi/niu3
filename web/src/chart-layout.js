// Leave room for a 16px trigger marker plus 2px clearance at each edge.
// SVG candles, Canvas overlays and pointer selection share this pixel geometry.
export function candlePlotLayout(width, count) {
  const left = Math.min(10, width / 4)
  const barWidth = (width - 2 * left) / Math.max(1, count)
  return { left, right: width - left, barWidth, bodyWidth: Math.min(10, barWidth * 0.62) }
}

export function candleIndexAt(x, width, count) {
  if (width <= 0 || count <= 0) return -1
  const layout = candlePlotLayout(width, count)
  return Math.max(0, Math.min(count - 1, Math.floor((x - layout.left) / layout.barWidth)))
}
