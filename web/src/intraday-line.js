import { exchangeTime } from './trade-observation.js'

function pointTime(point, day) {
  if (point.at != null) {
    if (typeof point.at !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/.test(point.at)) return null
    return exchangeTime(point.at)
  }
  if (point.time != null) {
    if (!/^\d{2}:\d{2}(:\d{2})?$/.test(point.time)) return null
    return exchangeTime(`${day}T${point.time.length === 5 ? `${point.time}:00` : point.time}+08:00`)
  }
  const minute = Number(point.minute)
  if (point.minute == null || !Number.isFinite(minute) || minute < 0 || minute > 240) return null
  const seconds = Math.round((minute + (minute < 120 ? 570 : 660)) * 60)
  const time = [Math.floor(seconds / 3600), Math.floor((seconds % 3600) / 60), seconds % 60]
    .map((part) => String(part).padStart(2, '0'))
    .join(':')
  return exchangeTime(`${day}T${time}+08:00`)
}

// Retained quotes carry second-precision timestamps; minute-only feeds retain
// their stated resolution. Both use the same exchange clock as the trade events.
export function intradayLinePoints(data) {
  let previous = null,
    broken = true
  return (data?.points || []).flatMap((point) => {
    const when = pointTime(point, data.day),
      price = Number(point.price)
    if (
      !when ||
      when.day !== data.day ||
      when.minute === null ||
      !Number.isFinite(price) ||
      price <= 0 ||
      (previous && when.timestamp <= previous.timestamp)
    ) {
      broken = true
      return []
    }
    const sample = {
      ...point,
      ...when,
      price,
      // Raw quotes and minute feeds share a gap tolerance in trading minutes;
      // all valid subminute quotes retain their original positions on the line.
      segmentStart: broken || Math.floor(when.minute) - Math.floor(previous.minute) > 2,
    }
    previous = sample
    broken = false
    return [sample]
  })
}

// Interpolate only the rendered segment. This is a display coordinate, never
// an execution price, quote sample, or input to the trading ledger.
export function intradayLinePrice(points, timestamp) {
  if (!Number.isFinite(timestamp)) return null
  let previous = null
  for (const point of points) {
    if (point.timestamp === timestamp) return point.price
    if (point.timestamp > timestamp) {
      if (!previous || point.segmentStart) return null
      const when = exchangeTime(new Date(timestamp).toISOString()),
        duration = point.minute - previous.minute
      if (!when || when.day !== point.day || when.minute === null || duration <= 0) return null
      // Use the same compressed trading clock as the line's x-coordinate.
      // The 11:30 and 13:00 endpoints may share x; only their exact times match.
      const fraction = (when.minute - previous.minute) / duration
      return previous.price + fraction * (point.price - previous.price)
    }
    previous = point
  }
  return null
}

export function intradayLinePath(points) {
  return points.map((point) => `${point.segmentStart ? 'M' : 'L'}${point.x},${point.y}`).join(' ')
}
