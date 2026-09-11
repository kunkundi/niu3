import { prepareCandles } from './candles.js'

// The current session is display data; completed history remains the strategy input.
export function mergeLiveCandle(points, live) {
  if (!live) return points
  const last = prepareCandles(points).bars.at(-1)
  if (
    !last ||
    live.day <= last.day ||
    live.basis_day !== last.day ||
    Math.abs(Number(live.basis_close) - last.close) > 1e-9 ||
    !Number.isFinite(Number(live.basis_close)) ||
    prepareCandles([live]).bars.length !== 1
  )
    return points
  return [...points, { ...live, live: true }]
}
