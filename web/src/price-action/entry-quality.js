// Fixed, causal research policies. Thresholds are not optimized per ticker.
export const ENTRY_POLICIES = ['legacy', 'valid', 'signal-quality', 'trend-aligned', 'retest-only']

export function entryRejection(candidate, bars, trend, tick, policy) {
  if (!ENTRY_POLICIES.includes(policy)) throw new Error('Unknown price-action entry policy')
  if (policy === 'legacy') return null
  if (bars.slice(candidate.index + 1).some((bar) => bar.close <= candidate.low - tick + 1e-10))
    return '信号形成后已有完整日 K 收盘跌破失效线，等待新形态'
  if (policy === 'valid') return null
  if (policy === 'trend-aligned')
    return trend === 'up' || (candidate.type === 'structure' && candidate.followThrough === 'strong')
      ? null
      : '当前非上升结构，等待强突破跟进'
  if (policy === 'retest-only')
    return candidate.type === 'structure' && candidate.retested && trend !== 'down'
      ? null
      : '等待已确认结构突破后的回测'
  if (candidate.type === 'structure')
    return candidate.followThrough === 'strong' ? null : '突破仅有弱跟进，等待更强确认'
  const signal = bars[candidate.type === 'inside-bar' ? candidate.index - 1 : candidate.index]
  const upperThird = signal.low + (signal.high - signal.low) * (2 / 3)
  return signal.close >= upperThird && (candidate.type === 'pin-bar' || signal.close > signal.open)
    ? null
    : '信号 K 收盘未处于上三分之一，买盘确认不足'
}
