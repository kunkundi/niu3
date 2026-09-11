// Exchange windows come from the server's verified calendar, including holidays.
export function intradayPolling(polling, now) {
  const windows = (polling?.windows || [])
    .map((window) => ({ ...window, startAt: Date.parse(window.start), endAt: Date.parse(window.end) }))
    .filter((window) => Number.isFinite(window.startAt) && Number.isFinite(window.endAt))
  const active = windows.find((window) => window.startAt <= now && now < window.endAt)
  const next = windows.find((window) => window.startAt > now)
  const ended = windows.filter((window) => window.endAt <= now).at(-1)
  const day = new Date(now + 8 * 3600000).toISOString().slice(0, 10)
  const message = active
    ? `盘中 · 每 ${polling.interval_seconds} 秒检查`
    : next?.start.startsWith(`${day}T13:`)
      ? '午间休市 · 已暂停获取'
      : next?.start.startsWith(`${day}T09:`)
        ? '等待开盘 · 已暂停获取'
        : polling
          ? '休市 · 已暂停获取'
          : '等待交易时段信息'
  return {
    active: !!active,
    nextChangeAt: active?.endAt ?? next?.startAt ?? null,
    snapshotKey: active ? null : (ended?.end ?? polling?.snapshot_key ?? null),
    message,
  }
}

export function needsIntradaySnapshot(state, completedKey) {
  return !state.active && !!state.snapshotKey && state.snapshotKey !== completedKey
}
