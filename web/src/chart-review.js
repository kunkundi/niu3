import { prepareCandles } from './candles.js'
import { preparePriceAction } from './price-action/index.js'
import { priceActionLevels } from './price-action/strategy.js'

// Filter raw input first: even a malformed future candle must not affect a
// historical indicator, its warmup, or the historical data-quality warning.
export function prepareChartReview(
  points,
  asOf,
  { preset = 'market', tick = 0.001, minimumBars = 120, layers = true } = {},
) {
  const prepared = prepareCandles(points.filter((point) => point.day <= asOf))
  const priceAction = layers ? preparePriceAction(prepared.bars, prepared.omitted, preset, tick) : null
  const strategy =
    preset === 'strategy' && !prepared.omitted
      ? priceActionLevels(
          prepared.bars.map((bar) => ({ ...bar, date: bar.day })),
          asOf,
          tick,
          { minimumBars },
        )
      : null
  return { prepared, priceAction, strategy }
}

export function reviewMarkersAsOf(markers, asOf) {
  return markers.filter(
    (marker) => marker.day <= asOf && (marker.known_through ?? marker.confirmation_day ?? marker.day) <= asOf,
  )
}

// Fixed 16 × 20 px trigger buttons. Keep the latest trigger nearest its candle,
// then place older buttons in the nearest free space without changing their dates.
export function layoutBuyMarkers(markers, width, height) {
  if (width < 20 || height < 24) return []
  const placed = []
  const output = new Array(markers.length)
  const clamp = (value, max) => Math.max(2, Math.min(max, value))
  for (let index = markers.length - 1; index >= 0; index--) {
    const marker = markers[index]
    const left = clamp(marker.x - 8, width - 18)
    const top = clamp(marker.y - 5, height - 22)
    const nearest = (values, preferred, max) =>
      [...new Set(values.map((value) => clamp(value, max)))].sort(
        (a, b) => Math.abs(a - preferred) - Math.abs(b - preferred),
      )
    const columns = nearest([left, ...placed.flatMap((p) => [p.left - 18, p.left + 18])], left, width - 18)
    const rows = nearest([top, ...placed.flatMap((p) => [p.top - 22, p.top + 22])], top, height - 22)
    const candidates = columns.flatMap((x) => rows.map((y) => ({ left: x, top: y })))
    const position = candidates.find((candidate) =>
      placed.every((p) => Math.abs(candidate.left - p.left) >= 18 || Math.abs(candidate.top - p.top) >= 22),
    )
    if (position) {
      placed.push(position)
      output[index] = { ...marker, ...position }
    }
  }
  return output.filter(Boolean)
}
