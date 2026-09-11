// Strategy selection and actual ownership are separate: an ETF can belong to both.
export function signalWatchlistGroups(plan = {}, positions = []) {
  const held = new Map(
    positions
      .filter(
        (position) =>
          position.symbol && Number.isFinite(Number(position.quantity)) && Number(position.quantity) > 0,
      )
      .map((position) => [position.symbol, position]),
  )
  const rows = (plan.rows || []).map((row) => ({ ...row, position: held.get(row.symbol) }))
  const bySymbol = new Map(rows.map((row) => [row.symbol, row]))
  const selected = (row) => (plan.mode === 'post_close' ? row.post_close_candidate : row.selected)
  return {
    candidates: rows.filter((row) => row.representative && !selected(row)),
    selected: rows.filter(selected),
    holdings: [...held.values()].map(
      (position) =>
        bySymbol.get(position.symbol) || {
          symbol: position.symbol,
          name: position.name || position.symbol,
          position,
        },
    ),
  }
}
