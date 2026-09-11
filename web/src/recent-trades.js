import { displayCode } from './display-code.js'
import { exchangeTime, tradeEffect } from './trade-observation.js'

// Keep separate orders separate, while making a partially filled order readable as one event.
export function recentTradeEvents(items = []) {
  const events = new Map()
  for (const item of items) {
    const when = exchangeTime(item.at)
    if (!when || !['BUY', 'SELL'].includes(item.side)) continue
    const id = `${item.symbol}:${when.day}:${item.side}:${item.order_id ?? `fill-${item.id}`}`
    if (!events.has(id))
      events.set(id, { id, symbol: item.symbol, day: when.day, side: item.side, items: [] })
    events.get(id).items.push({ ...item, when })
  }
  return [...events.values()]
    .map((event) => {
      event.items.sort((a, b) => a.when.timestamp - b.when.timestamp || a.id - b.id)
      const first = event.items[0],
        last = event.items.at(-1)
      const quantity = event.items.reduce((sum, item) => sum + Number(item.quantity), 0)
      const gross = event.items.reduce((sum, item) => sum + Number(item.gross), 0)
      return {
        ...event,
        name: displayCode(last.name || first.name || event.symbol),
        start: first.when.time,
        end: last.when.time,
        timestamp: last.when.timestamp,
        lastId: last.id,
        effect: tradeEffect(last).label,
        quantity,
        gross,
        price: quantity > 0 ? gross / quantity : null,
      }
    })
    .sort((a, b) => b.timestamp - a.timestamp || b.lastId - a.lastId)
}

export function tradedSecurities(events = []) {
  const securities = new Map()
  for (const event of events) {
    if (!securities.has(event.symbol))
      securities.set(event.symbol, { symbol: event.symbol, name: event.name, events: [] })
    securities.get(event.symbol).events.push(event)
  }
  return [...securities.values()]
}
