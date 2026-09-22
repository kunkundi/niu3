<script setup>
import { displayCode, displayCodeText } from '../display-code.js'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, money, signedMoney, changeClass, state } from '../state'
import { recentTradeEvents } from '../recent-trades.js'
import { tradedSecurities } from '../recent-trades.js'
import { tradeEffect, exchangeTime } from '../trade-observation.js'
import { minuteTSecurities } from '../minute-t-levels.js'
import { useMinuteT } from '../use-minute-t.js'
import Icon from './Icon.vue'
import WorkbenchDialog from './WorkbenchDialog.vue'
import TradeIntradayCard from './TradeIntradayCard.vue'

defineEmits(['view-all'])
const props = defineProps({ positions: { type: Array, default: () => [] } })
const items = ref([]),
  total = ref(0),
  loading = ref(false),
  error = ref(''),
  updatedAt = ref(null)
const limit = ref(5),
  selectedId = ref(null),
  detailsOpen = ref(false)
const events = computed(() => recentTradeEvents(items.value))
const view = ref('charts')
const minuteT = useMinuteT()
const displayDay = computed(
  () =>
    state.status.display_day ||
    minuteT.value?.day ||
    exchangeTime(state.status.intraday_polling?.server_at || updatedAt.value || new Date().toISOString())
      ?.day,
)
const displayDayLabel = computed(() =>
  displayDay.value === exchangeTime(state.status.at || updatedAt.value)?.day
    ? '今日'
    : displayDay.value || '最近交易日',
)
const securities = computed(() =>
  minuteTSecurities(tradedSecurities(events.value), minuteT.value, props.positions, displayDay.value),
)
const visible = computed(() => events.value.slice(0, limit.value))
const selected = computed(() => events.value.find((event) => event.id === selectedId.value))
let active = true,
  timer
async function load() {
  if (loading.value || document.hidden) return
  loading.value = true
  try {
    const data = await api('/trades?limit=200')
    if (!active) return
    items.value = data.items
    total.value = data.total
    updatedAt.value = new Date().toISOString()
    error.value = ''
  } catch {
    if (active)
      error.value = updatedAt.value
        ? '成交更新失败，以下为上次成功读取的记录。'
        : '暂时无法读取成交记录，请重试。'
  } finally {
    if (active) loading.value = false
  }
}
function showDetails(event) {
  selectedId.value = event.id
  detailsOpen.value = true
}
onMounted(() => {
  load()
  timer = setInterval(load, 30000)
  document.addEventListener('visibilitychange', load)
})
onUnmounted(() => {
  active = false
  clearInterval(timer)
  document.removeEventListener('visibilitychange', load)
})
</script>

<template>
  <section class="panel recent-trades" aria-labelledby="recent-trades-title">
    <header class="recent-heading">
      <div>
        <h2 id="recent-trades-title">最近成交</h2>
        <span class="simulation-label">模拟交易</span>
        <small>买卖发生在哪一段行情</small>
      </div>
      <button class="text-link" @click="$emit('view-all')">
        全部成交明细<Icon name="arrow" :size="15" />
      </button>
    </header>
    <div v-if="error" class="recent-error" role="status">
      <span>{{ error }}</span>
      <button class="text-button" :disabled="loading" @click="load">重试</button>
    </div>
    <div v-if="securities.length || events.length" class="recent-viewbar">
      <div class="recent-views" role="group" aria-label="成交展示方式">
        <button :aria-pressed="view === 'charts'" @click="view = 'charts'">分时买卖点</button>
        <button :aria-pressed="view === 'timeline'" @click="view = 'timeline'">成交时间线</button>
      </div>
      <span v-if="view === 'charts'" class="chart-key"
        ><b class="buy-text">▲ 买入</b><b class="sell-text">▼ 卖出</b>悬停查看交易数据 · 点击查看详情</span
      >
      <span v-else class="chart-key">最新在前 · 北京时间</span>
    </div>
    <div v-if="securities.length && view === 'charts'" class="trade-intraday-grid">
      <TradeIntradayCard
        v-for="security in securities"
        :key="security.symbol"
        :security="security"
        :minute-t="minuteT"
        @select="showDetails"
      />
    </div>
    <ol
      v-else-if="view === 'timeline' && visible.length"
      class="trade-timeline"
      aria-label="最近 ETF 成交时间线"
    >
      <li v-for="(event, index) in visible" :key="event.id">
        <div v-if="index === 0 || event.day !== visible[index - 1].day" class="trade-day">
          <time :datetime="event.day">{{ event.day }}</time>
        </div>
        <div class="trade-event" :class="event.side === 'BUY' ? 'is-buy' : 'is-sell'">
          <div class="event-time">
            <strong>{{ event.end }}</strong>
            <small v-if="event.start !== event.end">{{ event.start }} 起</small>
          </div>
          <div class="event-action">
            <strong>{{ event.side === 'BUY' ? '买入' : '卖出' }}</strong>
            <small v-if="!['买入', '卖出'].includes(event.effect)">{{ event.effect }}</small>
          </div>
          <RouterLink class="event-security" :to="{ path: '/market', query: { symbol: event.symbol } }">
            <strong>{{ displayCode(event.name) }}</strong>
            <small>{{ displayCode(event.symbol) }}<span class="chart-link">查看图表 ↗</span></small>
          </RouterLink>
          <div class="event-size">
            <strong>{{ money(event.quantity, 0) }} <span>份</span></strong>
            <small>{{ event.items.length > 1 ? '均价' : '成交价' }} {{ money(event.price, 3) }} 元</small>
          </div>
          <div class="event-amount">
            <strong>{{ money(event.gross) }} <span>元</span></strong>
            <small>成交金额</small>
          </div>
          <button
            class="event-details"
            @click="showDetails(event)"
            :aria-label="`${event.day} ${event.end} ${event.side === 'BUY' ? '买入' : '卖出'}${displayCode(event.name)}成交详情`"
          >
            {{ event.items.length > 1 ? `${event.items.length} 笔明细` : '成交详情' }}
            <Icon name="chevron" :size="14" />
          </button>
        </div>
      </li>
    </ol>
    <div v-else-if="!error" class="recent-empty" role="status">
      <Icon name="clock" :size="22" />
      <div>
        <template v-if="view === 'charts'">
          <strong>暂无持仓或{{ displayDayLabel }}卖出记录</strong>
          <p>这里展示当前持仓及{{ displayDayLabel }}已卖出 ETF 的分时买卖点。</p>
        </template>
        <template v-else>
          <strong>{{ !updatedAt ? '正在读取成交记录…' : '尚未发生模拟成交' }}</strong>
          <p>成交后，这里会展示交易记录。</p>
        </template>
      </div>
    </div>
    <footer v-if="securities.length && view === 'charts'" class="recent-footer">
      <span
        >当前持仓及{{ displayDayLabel }}卖出 · 可切换历史成交日 · 标记按北京时间贴合分时线，实际成交价见明细
        <span v-if="total > items.length"> · 仅含最近 {{ items.length }} 笔成交</span>
      </span>
    </footer>
    <footer v-if="events.length && view === 'timeline'" class="recent-footer">
      <span
        >显示最近 {{ visible.length }} 次交易 · 同一委托当日分笔成交合并
        <span v-if="total > items.length"> · 汇总仅含最近 {{ items.length }} 笔成交</span>
      </span>
      <button v-if="events.length > limit" class="text-button" @click="limit += 5">
        再看 {{ Math.min(5, events.length - limit) }} 次交易
      </button>
      <button v-else-if="limit > 5" class="text-button" @click="limit = 5">收起</button>
    </footer>
    <WorkbenchDialog
      v-model:open="detailsOpen"
      :title="selected ? `${displayCode(selected.name)} · 成交详情` : '成交详情'"
    >
      <template v-if="selected">
        <div class="detail-summary">
          <strong :class="selected.side === 'BUY' ? 'buy-text' : 'sell-text'">
            {{ selected.side === 'BUY' ? '买入' : '卖出' }} {{ money(selected.quantity, 0) }} 份
          </strong>
          <span>{{ displayCode(selected.symbol) }} · {{ selected.day }} · 北京时间</span>
          <span
            >成交{{ selected.items.length > 1 ? '均' : '' }}价 {{ money(selected.price, 3) }} 元 · 合计
            {{ money(selected.gross) }} 元</span
          >
        </div>
        <article v-for="item in selected.items" :key="item.id" class="event-fill">
          <div>
            <strong>{{ item.when.time }}</strong
            ><span>{{ tradeEffect(item).label }}</span>
          </div>
          <p>
            {{ money(item.price, 3) }} 元 × {{ money(item.quantity, 0) }} 份 · 成交金额
            {{ money(item.gross) }} 元
          </p>
          <p>{{ displayCodeText(item.reason || '暂无记录原因') }}</p>
          <p v-if="item.position_before != null && item.position_after != null">
            持仓 {{ money(item.position_before, 0) }} → {{ money(item.position_after, 0) }} 份
          </p>
          <p v-if="item.side === 'SELL'">
            已实现盈亏 <span :class="changeClass(item.realized)">{{ signedMoney(item.realized) }} 元</span>
          </p>
          <small>订单 #{{ item.order_id }} · 成交 #{{ item.id }} · 费用 {{ money(item.fee) }} 元</small>
        </article>
        <RouterLink class="text-link" :to="{ path: '/market', query: { symbol: selected.symbol } }"
          >查看这只 ETF 的行情与买卖点<Icon name="arrow" :size="15"
        /></RouterLink>
      </template>
    </WorkbenchDialog>
  </section>
</template>

<style scoped>
.recent-trades {
  margin-bottom: 12px;
  overflow: visible;
}
.recent-viewbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px 16px;
  padding: 8px 16px;
}
.recent-views {
  display: flex;
  gap: 4px;
}
.recent-views button {
  background: transparent;
  border: 1px solid transparent;
  padding: 6px 12px;
  color: var(--muted);
  font-size: 12px;
}
.recent-views button[aria-pressed='true'] {
  background: var(--panel2);
  border-color: var(--line);
  color: var(--ink);
}
.chart-key {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 11px;
  color: var(--muted);
}
.chart-key b {
  font-weight: 550;
}
.trade-intraday-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  grid-auto-rows: 1fr;
  gap: 12px;
  padding: 0 16px 12px;
  align-items: stretch;
}
@media (max-width: 1099px) {
  .trade-intraday-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 559px) {
  .trade-intraday-grid {
    grid-template-columns: minmax(0, 1fr);
    padding: 0 10px 10px;
    gap: 10px;
  }
  .recent-viewbar {
    padding: 8px 10px;
  }
  .chart-key {
    font-size: 10px;
  }
}
.recent-heading,
.recent-heading > div,
.recent-footer,
.recent-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px 16px;
}
.recent-heading {
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
}
.recent-heading > div {
  justify-content: flex-start;
  flex-wrap: wrap;
  gap: 6px 12px;
}
.recent-heading h2 {
  font-size: 17px;
  margin: 0;
}
.recent-heading small,
.recent-footer,
.event-fill small {
  color: var(--muted);
  font-size: 11px;
}
.simulation-label {
  font-size: 10px;
  border: 1px solid var(--line);
  padding: 2px 5px;
  color: var(--muted);
}
.recent-heading .text-link {
  flex-shrink: 0;
  font-size: 12px;
  background: transparent;
  border: 0;
  padding: 4px 0;
}
.trade-timeline {
  padding: 0 16px;
  margin: 0;
  list-style: none;
}
.trade-day {
  padding: 10px 0 3px;
  color: var(--text-secondary);
  font-family: var(--font-numeric);
  font-size: 12px;
}
.trade-event {
  --event-color: var(--green);
  display: grid;
  grid-template-columns: 98px 64px minmax(160px, 1fr) minmax(115px, 0.5fr) minmax(125px, 0.5fr) 94px;
  align-items: center;
  gap: 16px;
  padding: 10px 0 10px 16px;
  position: relative;
  border-left: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}
.trade-event.is-buy {
  --event-color: var(--red);
}
.trade-event::before {
  content: '';
  position: absolute;
  left: -4px;
  top: 22px;
  height: 7px;
  width: 7px;
  border-radius: 50%;
  background: var(--event-color);
}
.trade-timeline > li:last-child .trade-event {
  border-bottom: 0;
}
.trade-event strong {
  display: block;
  font-size: 15px;
  font-weight: 550;
  line-height: 1.5;
}
.trade-event small {
  display: block;
  font-size: 11px;
  color: var(--muted);
  margin-top: 2px;
  line-height: 1.5;
}
.event-time,
.event-size,
.event-amount {
  font-family: var(--font-numeric);
}
.event-time strong {
  font-size: 14px;
}
.event-action strong {
  color: var(--event-color);
  font-size: 14px;
  width: fit-content;
  padding: 2px 9px;
  background: color-mix(in srgb, var(--event-color) 10%, transparent);
}
.event-action small {
  padding-left: 9px;
}
.event-security {
  min-width: 0;
  color: var(--ink);
  text-decoration: none;
}
.event-security strong {
  overflow-wrap: anywhere;
}
.event-security small {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
}
.chart-link {
  color: var(--muted);
  font-size: 10px;
}
.event-security:hover strong,
.event-security:hover .chart-link {
  color: var(--accent);
}
.event-size,
.event-amount {
  text-align: right;
}
.event-size strong span,
.event-amount strong span {
  font-size: 11px;
  font-weight: 400;
  color: var(--muted);
}
.event-details {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 5px;
  color: var(--text-secondary);
  background: transparent;
  border: 0;
  font-size: 11px;
  min-height: 36px;
  padding: 6px 0;
}
.event-details:hover {
  color: var(--ink);
}
.recent-footer {
  border-top: 1px solid var(--line);
  padding: 6px 16px;
  min-height: 36px;
  flex-wrap: wrap;
  line-height: 1.6;
}
.recent-footer .text-button {
  padding: 4px 0;
  min-height: 28px;
  font-size: 11px;
}
.recent-empty {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 26px 16px;
  color: var(--muted);
}
.recent-empty strong {
  color: var(--ink);
  font-size: 14px;
}
.recent-empty p {
  font-size: 12px;
  margin: 6px 0 0;
}
.recent-error {
  padding: 8px 16px;
  font-size: 12px;
  color: var(--amber);
}
.detail-summary {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 13px;
  padding-bottom: 14px;
}
.detail-summary strong {
  font-size: 18px;
}
.buy-text {
  color: var(--red);
}
.sell-text {
  color: var(--green);
}
.event-fill {
  border-top: 1px solid var(--line);
  padding: 12px 0;
  font-size: 13px;
}
.event-fill > div {
  display: flex;
  align-items: center;
  gap: 12px;
}
.event-fill p {
  margin: 7px 0;
  line-height: 1.6;
}
@media (max-width: 1000px) and (min-width: 701px) {
  .trade-event {
    grid-template-columns: 86px 52px minmax(140px, 1fr) 108px 86px;
    gap: 12px;
  }
  .event-amount {
    display: none;
  }
}
@media (max-width: 700px) {
  .recent-trades {
    margin-bottom: 10px;
  }
  .recent-heading {
    padding: 8px 12px;
    gap: 8px;
  }
  .recent-heading h2 {
    font-size: 16px;
  }
  .recent-heading > div {
    gap: 4px 8px;
  }
  .recent-heading small {
    flex-basis: 100%;
    font-size: 10px;
  }
  .recent-heading .text-link {
    font-size: 11px;
  }
  .trade-timeline {
    padding: 0 12px;
  }
  .trade-day {
    font-size: 11px;
    padding-top: 10px;
  }
  .trade-event {
    grid-template-columns: 52px minmax(0, 1fr) auto;
    gap: 6px 8px;
    padding: 9px 0 9px 12px;
  }
  .event-time {
    grid-column: 1 / -1;
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  .event-time strong {
    font-size: 12px;
  }
  .event-time small {
    margin-top: 0;
    font-size: 10px;
  }
  .trade-event::before {
    top: 14px;
  }
  .event-action {
    grid-column: 1;
    grid-row: 2;
    align-self: start;
  }
  .event-action strong {
    font-size: 13px;
    padding: 2px 7px;
  }
  .event-action small {
    padding-left: 7px;
    font-size: 10px;
  }
  .event-security {
    grid-column: 2 / -1;
    grid-row: 2;
  }
  .event-security strong {
    font-size: 15px;
  }
  .event-size {
    grid-column: 2;
    grid-row: 3;
    text-align: left;
  }
  .event-size strong {
    font-size: 13px;
  }
  .event-size small {
    font-size: 10px;
  }
  .event-amount {
    display: none;
  }
  .event-details {
    grid-column: 3;
    grid-row: 3;
  }
  .recent-footer {
    padding: 6px 12px;
    gap: 2px 8px;
    font-size: 10px;
  }
}
</style>
