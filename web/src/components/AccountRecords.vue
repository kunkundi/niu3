<script setup>
import { displayCode, displayCodeText } from '../display-code.js'
import { useMobile } from '../mobile'
const mobile = useMobile()
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, money, pct, dateTime, orderStatuses, signedMoney, changeClass } from '../state'
import { holdingWarning } from '../holding-warning.js'
import Empty from './Empty.vue'
import Icon from './Icon.vue'
const props = defineProps({
  account: { type: Object, required: true },
  status: { type: Object, default: () => ({}) },
})
const positions = computed(() =>
  props.account.positions.map((p) => ({ ...p, warning: holdingWarning(p, props.status) })),
)
const dailyLabel = computed(() =>
  props.account.daily_return?.is_today === false ? '最近交易日收益' : '今日收益',
)
const tab = ref('positions'),
  items = ref([]),
  error = ref(''),
  offset = ref(0),
  total = ref(0),
  loading = ref(false)
let requestId = 0,
  timer
async function load() {
  const id = ++requestId
  if (tab.value === 'positions') {
    loading.value = false
    return
  }
  loading.value = true
  try {
    const data = await api(`/${tab.value}?offset=${offset.value}&limit=50`)
    if (id !== requestId) return
    items.value = data.items
    total.value = data.total ?? data.items.length
    error.value = ''
  } catch (e) {
    if (id === requestId) error.value = e.message
  } finally {
    if (id === requestId) loading.value = false
  }
}
watch(tab, () => {
  offset.value = 0
  items.value = []
  total.value = 0
  error.value = ''
  load()
})
watch(offset, load)
onMounted(() => {
  load()
  timer = setInterval(() => {
    if (!document.hidden) load()
  }, 30000)
})
onUnmounted(() => {
  requestId++
  clearInterval(timer)
})
defineExpose({
  showTrades() {
    if (tab.value === 'trades' && offset.value === 0) load()
    else if (tab.value === 'trades') offset.value = 0
    else tab.value = 'trades'
  },
})
</script>
<template>
  <section id="account-records" class="panel account-panel" tabindex="-1" aria-label="模拟交易账户记录">
    <div class="panel-heading">
      <div class="tabs" role="group" aria-label="账户记录类型">
        <button
          v-for="item in [
            { key: 'positions', label: '当前持仓' },
            { key: 'orders', label: '委托记录' },
            { key: 'trades', label: '成交明细' },
          ]"
          :key="item.key"
          :class="{ selected: tab === item.key }"
          :aria-pressed="tab === item.key"
          @click="tab = item.key"
        >
          {{ item.label }}
          <span v-if="item.key === 'positions'" class="count-badge">{{ account.positions.length }}</span>
        </button>
      </div>
      <button
        class="icon-button"
        v-if="tab !== 'positions'"
        @click="load"
        :disabled="loading"
        aria-label="刷新账户记录"
      >
        <Icon name="refresh" :size="17" />
      </button>
    </div>
    <p v-if="error" class="form-error padded">{{ error }}</p>
    <div v-if="mobile && tab === 'positions' && account.positions.length" class="mobile-holdings">
      <details v-for="p in positions" :key="p.symbol" class="mobile-holding">
        <summary>
          <span class="mobile-security"
            ><strong>{{ displayCode(p.name) }}</strong
            ><small>{{ displayCode(p.symbol) }} · 最新 {{ money(p.price, 3) }}</small></span
          >
          <span class="holding-profit" :class="changeClass(p.pnl)"
            ><small class="profit-label">浮动盈亏</small><b>{{ signedMoney(p.pnl) }}</b
            ><small>{{ pct(p.pnl_pct, true) }}</small></span
          >
          <Icon name="chevron" :size="14" />
          <span class="holding-daily-profit">
            <span>{{ dailyLabel }}</span>
            <b :class="changeClass(p.daily_pnl)">{{ signedMoney(p.daily_pnl) }}</b>
            <small v-if="p.daily_pnl_warning" class="amberText">{{ p.daily_pnl_warning }}</small>
          </span>
          <span v-if="p.warning" class="holding-warning amberText">{{ p.warning }}</span>
        </summary>
        <dl class="mobile-facts">
          <div>
            <dt>成本价</dt>
            <dd>{{ money(p.average_cost, 3) }}</dd>
          </div>
          <div>
            <dt>市值 / 仓位</dt>
            <dd>{{ money(p.market_value) }} / {{ pct(account.nav ? p.market_value / account.nav : 0) }}</dd>
          </div>
          <div>
            <dt>持有份额</dt>
            <dd>{{ money(p.quantity, 0) }}</dd>
          </div>
          <div>
            <dt>可卖份额</dt>
            <dd>
              {{ money(p.available, 0) }}
              <small v-if="p.available < p.quantity" class="holding-warning"
                >T+1 待解锁 {{ money(p.quantity - p.available, 0) }} 份</small
              >
            </dd>
          </div>
          <div>
            <dt>行情时间</dt>
            <dd>{{ dateTime(p.quote_at) }}</dd>
          </div>
        </dl>
        <RouterLink class="button secondary" :to="{ path: '/market', query: { symbol: p.symbol } }"
          >查看行情与图表</RouterLink
        >
      </details>
    </div>
    <div
      class="table-scroll"
      v-else-if="tab === 'positions' && account?.positions.length"
      tabindex="0"
      role="region"
      aria-label="账户持仓，可横向滚动"
    >
      <table class="holdings-table">
        <thead>
          <tr>
            <th>名称 / 代码</th>
            <th class="number-cell">最新价 / 成本价</th>
            <th class="number-cell">{{ dailyLabel }}</th>
            <th class="number-cell">浮动盈亏 / 收益率</th>
            <th class="number-cell">市值 / 仓位</th>
            <th class="number-cell">持有 / 可卖份额</th>
            <th>行情时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in positions" :key="p.symbol">
            <td>
              <RouterLink class="table-name" :to="{ path: '/market', query: { symbol: p.symbol } }"
                >{{ displayCode(p.name) }}<small>{{ displayCode(p.symbol) }}</small></RouterLink
              >
              <small v-if="p.accounting_block" class="holding-warning amberText">{{
                p.accounting_block
              }}</small>
            </td>
            <td class="number-cell">
              {{ money(p.price, 3) }}<small>{{ money(p.average_cost, 3) }}</small>
            </td>
            <td class="number-cell" :class="changeClass(p.daily_pnl)">
              {{ signedMoney(p.daily_pnl) }}
              <small v-if="p.daily_pnl_warning" class="amberText">{{ p.daily_pnl_warning }}</small>
            </td>
            <td class="number-cell" :class="changeClass(p.pnl)">
              {{ signedMoney(p.pnl) }}<small class="inherit-color">{{ pct(p.pnl_pct, true) }}</small>
            </td>
            <td class="number-cell">
              {{ money(p.market_value)
              }}<small>{{ pct(account.nav ? p.market_value / account.nav : 0) }}</small>
            </td>
            <td class="number-cell">
              {{ money(p.quantity, 0)
              }}<small :class="{ amberText: p.available < p.quantity }"
                >可卖 {{ money(p.available, 0) }}</small
              >
              <small v-if="p.available < p.quantity"
                >T+1 待解锁 {{ money(p.quantity - p.available, 0) }} 份</small
              >
            </td>
            <td>
              <span class="quote-time">{{ p.quote_at?.slice(5, 10) || '—' }}</span
              ><small>{{ p.quote_at?.slice(11, 19) || '—' }}</small>
              <small v-if="p.warning && !p.accounting_block" class="amberText">{{ p.warning }}</small>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div
      class="table-scroll"
      v-else-if="tab !== 'positions' && items.length"
      tabindex="0"
      role="region"
      aria-label="账户记录，可横向滚动"
    >
      <table class="mobile-record-table" v-if="tab === 'orders'">
        <thead>
          <tr>
            <th>创建时间 / 订单</th>
            <th>ETF / 方向</th>
            <th>委托 / 已成交</th>
            <th>状态</th>
            <th>依据 / 等待原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in items" :key="r.id">
            <td data-label="创建时间 / 订单">
              {{ dateTime(r.created_at) }}<small>#{{ r.id }} · 参数 v{{ r.config_id }}</small>
            </td>
            <td data-label="ETF / 方向">
              <RouterLink
                class="table-name"
                :to="{ path: '/market', query: { symbol: r.symbol } }"
                :aria-label="`查看${displayCode(r.name)} ${displayCode(r.symbol)}的日 K 线，${r.side === 'BUY' ? '买入' : '卖出'}`"
                title="查看日 K 线"
                >{{ displayCode(r.name) || '名称待补全' }}
                <small class="record-symbol-meta">
                  <span>{{ displayCode(r.symbol) }}</span>
                  <span class="trade-side" :class="r.side === 'BUY' ? 'buy' : 'sell'">{{
                    r.side === 'BUY' ? '买入' : '卖出'
                  }}</span>
                </small>
              </RouterLink>
            </td>
            <td data-label="委托 / 已成交">
              {{ money(r.quantity, 0) }}<small>已成交 {{ money(r.filled, 0) }}</small>
            </td>
            <td data-label="状态">
              <span class="chip" :class="r.status === 'filled' ? '' : 'neutral'">{{
                orderStatuses[r.status] || r.status
              }}</span>
            </td>
            <td data-label="依据 / 等待原因" class="reason-cell">
              {{ displayCodeText(r.reason) }}<small>{{ displayCodeText(r.blocked_reason) }}</small>
            </td>
          </tr>
        </tbody>
      </table>
      <table class="mobile-record-table" v-else-if="tab === 'trades'">
        <thead>
          <tr>
            <th>成交时间</th>
            <th>ETF / 方向</th>
            <th>价格 / 份额</th>
            <th>金额 / 费用</th>
            <th>已实现盈亏</th>
            <th>决策依据</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in items" :key="r.id">
            <td data-label="成交时间">
              {{ dateTime(r.at) }}<small>订单 #{{ r.order_id }}</small>
            </td>
            <td data-label="ETF / 方向">
              <RouterLink
                class="table-name"
                :to="{ path: '/market', query: { symbol: r.symbol } }"
                :aria-label="`查看${displayCode(r.name)} ${displayCode(r.symbol)}的日 K 线，${r.side === 'BUY' ? '买入' : '卖出'}`"
                title="查看日 K 线"
                >{{ displayCode(r.name) || '名称待补全' }}
                <small class="record-symbol-meta">
                  <span>{{ displayCode(r.symbol) }}</span>
                  <span class="trade-side" :class="r.side === 'BUY' ? 'buy' : 'sell'">{{
                    r.side === 'BUY' ? '买入' : '卖出'
                  }}</span>
                </small>
              </RouterLink>
            </td>
            <td data-label="价格 / 份额">
              {{ money(r.price, 3) }}<small>{{ money(r.quantity, 0) }} 份</small>
            </td>
            <td data-label="金额 / 费用">
              {{ money(r.gross) }}<small>费用 {{ money(r.fee) }}</small>
            </td>
            <td data-label="已实现盈亏" :class="r.realized > 0 ? 'up' : r.realized < 0 ? 'down' : ''">
              {{ r.side === 'BUY' ? '—' : signedMoney(r.realized) }}
            </td>
            <td data-label="决策依据" class="reason-cell">{{ displayCodeText(r.reason) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else-if="loading" class="loading" role="status">正在读取账户记录…</div>
    <Empty
      v-else
      :title="tab === 'positions' ? '当前没有持仓' : '暂无相关记录'"
      description="成交后将更新账户记录。"
      icon="account"
    />
    <div class="pagination" v-if="tab !== 'positions'">
      <span>共 {{ total }} 条</span>
      <div>
        <button :disabled="offset === 0" @click="offset -= 50">上一页</button
        ><span>{{ offset / 50 + 1 }}</span
        ><button :disabled="offset + 50 >= total" @click="offset += 50">下一页</button>
      </div>
    </div>
  </section>
  <p class="footnote">模拟交易 · 金额单位：元。可卖份额按 T+0／T+1 规则计算，收益已计入模拟费用。</p>
  <p v-if="account.daily_return?.day" class="footnote">
    收益日期：{{
      account.daily_return.day
    }}。当日收益以昨收为基准，计入当日买卖、费用与分红；总收益包含已清仓标的。
  </p>
</template>
<style scoped>
.account-panel {
  scroll-margin-top: 74px;
}
.holding-daily-profit {
  grid-column: 1 / -1;
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px 8px;
  font-size: 12px;
  color: var(--muted);
}
.holding-daily-profit b {
  margin-left: auto;
  font: 500 16px var(--font-numeric);
}
.holding-profit .profit-label {
  color: var(--muted);
  font-size: 10px;
  margin: 0 0 3px;
}
.record-symbol-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}
.record-symbol-meta .trade-side {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 400;
}
</style>
