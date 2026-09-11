import { createPriceActionEngine } from './engine.js'
import { dailyStructureOptions } from './daily-policy.js'

// NiuTwo defaults to the 0.01 stock tick. ETF quotes here use a 0.001 tick.
export const etfEngine = createPriceActionEngine(0.001)
export const PA_GROUPS = [
  {
    label: '结构与通道',
    options: [
      ['support', '支撑区域', '摆动低点聚类形成的支撑价与区域'],
      ['resistance', '压力区域', '摆动高点聚类形成的压力价与区域'],
      ['trendLines', '趋势线', '带确认时点、主次评分和失效状态的摆动连线'],
      ['trendChannels', '趋势通道线', '趋势线平移至对侧极值，包含触碰、超越 OS、未及 US 等事件'],
      ['microChannels', '微型通道', '至少 3 根连续高低点推进，以及通道中断标记'],
      ['swings', '摆动点 HH/HL', '左右各 3 根确认；HH/HL/LH/LL/EH/EL，M· 表示主要摆动'],
      ['legs', '价格腿', '连接交替摆动；区分主要、次要和上行、下行腿'],
      ['tradingRanges', '交易区间', '交易区间、紧密区间、三角形及中线、失败突破'],
    ],
  },
  {
    label: '突破与缺口',
    options: [
      ['bos', '结构突破 BOS', '顺结构突破及跟进 FT、回测 RT、延续 CT、失败 FB'],
      ['choch', '结构转向 CHoCH', '逆原结构突破，以及其跟进和失败状态'],
      ['barCounts', '回调计数 H1–H4/L1–L4', '多空回调计数、入场价触发与跟进状态'],
      ['gaps', '缺口与 EMA Gap', '开盘、K 线、实体、突破、测量缺口，以及 EMA Gap Bar / 20 Gap Bars'],
    ],
  },
  {
    label: 'K 线与形态',
    options: [
      ['barClassifications', 'K 线分类', '趋势 K、区间 K、十字星 Doji 和反转 K 的规则分类'],
      ['pinBar', 'Pin Bar', '长影线拒绝形态，图中 PB'],
      ['insideBar', 'Inside Bar', '内包 K 线，图中 IB'],
      ['engulfing', '吞没形态', '实体吞没，图中 ENG'],
      ['outsideBar', 'Outside Bar', '外包 K 线，图中 OB'],
      ['trendPatterns', '趋势形态', '急涨急跌与通道 S&C、小回调趋势 SPT、宽通道 BC'],
      [
        'reversalPatterns',
        '反转形态',
        '双顶底 DT/DB、突破回调 DTP/DBP、楔形 WT/WB/WP、扩张三角 ET、主要反转 MTR、最终旗形 FF、高潮反转 CR',
      ],
    ],
  },
  {
    label: '目标与背景',
    options: [
      ['measuredMoves', '测量目标与磁吸位', '价格腿、区间、缺口和形态的测量目标，以及来源合流的磁吸位'],
      ['invalidation', '结构失效线', '当前结构的数值失效参考与是否已越过'],
      [
        'higherTimeframe',
        '高周期背景（周 K）',
        '由已有日 K 合成周 K，展示周线支撑、压力和 EMA20；剔除首尾边界周',
      ],
    ],
  },
].map((group) => ({
  ...group,
  options: group.options.map(([id, label, description]) => ({ id, label, description })),
}))
export const PA_OPTIONS = PA_GROUPS.flatMap((group) => group.options)
export const PA_DEFAULTS = Object.fromEntries(PA_OPTIONS.map(({ id }) => [id, false]))
export const PA_COMMON = ['support', 'resistance', 'trendLines', 'trendChannels']

const weekKey = (day) => {
  const date = new Date(`${day}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7))
  return date.toISOString().slice(0, 10)
}
export function aggregateCompletedWeeks(bars) {
  const groups = new Map()
  for (const bar of bars) {
    const key = weekKey(bar.date)
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(bar)
  }
  // The fetched history may start midweek; the trailing week may still be forming.
  // Dropping both boundary groups is deliberately conservative, including holidays.
  return [...groups.values()].slice(1, -1).map((group) => ({
    date: group.at(-1).date,
    open: group[0].open,
    close: group.at(-1).close,
    high: Math.max(...group.map((bar) => bar.high)),
    low: Math.min(...group.map((bar) => bar.low)),
    volume: group.reduce((sum, bar) => sum + (bar.volume ?? 0), 0),
    closed: true,
  }))
}

export function preparePriceAction(candles, omitted = 0, preset = 'market', tickSize = 0.001) {
  const engine = tickSize === 0.001 ? etfEngine : createPriceActionEngine(tickSize)
  const bars = candles.map((bar) => ({ ...bar, date: bar.day, closed: true, volume: bar.volume ?? 0 }))
  if (omitted) return { bars, analysis: null, error: '历史中有异常开高低收，结构分析暂不可用。' }
  if (bars.length < 20) return { bars, analysis: null, error: '价格行为分析至少需要 20 根完整日 K。' }
  // Display preferences must not change the structure horizon or current state.
  const analysis = engine.analyzePriceAction(bars, [], dailyStructureOptions)
  const weeks = aggregateCompletedWeeks(bars)
  analysis.higherTimeframe =
    weeks.length >= 20
      ? engine.summarizeHigherTimeframe(
          engine.analyzePriceAction(weeks, [], { period: 'week' }),
          'week',
          '周 K',
          weeks.at(-1).date,
        )
      : engine.unavailableHigherTimeframe(
          'week',
          '周 K',
          `已合成 ${weeks.length} 根完整周 K，至少需要 20 根。`,
        )
  return { bars, analysis, error: '', weeklyCount: weeks.length }
}

// Used for the visible-window evidence list; objects retain their actual confirmation date.
export function layerItems(analysis, id) {
  if (!analysis) return []
  if (id === 'support' || id === 'resistance') return analysis.levels.filter((item) => item.type === id)
  if (id === 'bos' || id === 'choch')
    return analysis.breakouts.filter((item) => item.structureLabel === (id === 'bos' ? 'BOS' : 'CHoCH'))
  const signalTypes = {
    pinBar: 'pin-bar',
    insideBar: 'inside-bar',
    engulfing: 'engulfing',
    outsideBar: 'outside-bar',
  }
  if (signalTypes[id]) return analysis.signals.filter((item) => item.type === signalTypes[id])
  if (id === 'trendChannels') return [...analysis.channelLines, ...analysis.channelEvents]
  if (id === 'microChannels') return [...analysis.microChannels, ...analysis.microChannelBreaks]
  if (id === 'invalidation')
    return analysis.invalidationPrice == null
      ? []
      : [{ label: analysis.invalidation, price: analysis.invalidationPrice }]
  if (id === 'higherTimeframe')
    return analysis.higherTimeframe?.available
      ? [
          { label: '周 K EMA20', price: analysis.higherTimeframe.ema20Value },
          ...analysis.higherTimeframe.levels,
        ]
      : []
  if (id === 'measuredMoves') return [...analysis.measuredMoves, ...analysis.magnets]
  return analysis[id] || []
}
