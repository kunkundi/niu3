// Display freshness follows exchange sessions; execution still uses the server's stale flag.
export function holdingWarning(position, status = {}) {
  if (position.accounting_block) return position.accounting_block
  const quoteAt = Date.parse(position.quote_at)
  if (!Number.isFinite(quoteAt) || !Number.isFinite(Number(position.price)) || Number(position.price) <= 0)
    return '行情缺失'
  if (!position.stale) return ''
  if (status.session_open === true) return '行情延迟'
  if (status.session_open !== false) return '行情待确认'

  // The verified calendar supplies the last lunch/close cutoff, including holidays.
  // Allow the maximum quote age (90 seconds) at the end of that session.
  const cutoff = Date.parse(status.intraday_polling?.snapshot_key)
  if (!Number.isFinite(cutoff)) return '行情待确认'
  return quoteAt < cutoff - 90_000 ? '行情未更新' : ''
}
