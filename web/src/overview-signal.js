// Overview labels must follow the current signal mode, including overnight observations.
export function overviewSignal(plan) {
  const postClose = plan?.mode === 'post_close'
  const live = plan?.mode === 'live'
  return {
    postClose,
    title: postClose ? '盘后观察' : live ? '盘中信号' : '策略计划',
    dateLabel: postClose ? '下一交易日' : live ? '计算时间' : '执行日期',
    date: (live ? plan?.created_at?.slice(5, 19).replace('T', ' ') : plan?.execute_day) || '—',
    countLabel: postClose ? '次日候选' : '目标标的',
    rows: (plan?.rows || []).filter((row) => (postClose ? row.post_close_candidate : row.selected)),
  }
}
