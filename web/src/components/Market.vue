<script setup>
import { displayCode } from '../display-code.js'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  api,
  state,
  notify,
  refresh,
  categories,
  money,
  pct,
  dateTime,
  signedMoney,
  changeClass,
} from '../state'
import Icon from './Icon.vue'
import Empty from './Empty.vue'
import { useTradeHistory } from '../use-trade-history.js'
import { useLiveCandle } from '../use-live-candle.js'
import CandlestickChart from './CandlestickChart.vue'
import IntradayView from './IntradayView.vue'
import AddEtfDialog from './AddEtfDialog.vue'
import WorkbenchDialog from './WorkbenchDialog.vue'
import { useMobile } from '../mobile'
const mobile = useMobile()
const filtersOpen = ref(false),
  marketPanel = ref(null),
  pendingRemoval = ref(null)
const activeFilters = computed(
  () => Number(category.value !== 'all') + Number(tradable.value) + Number(recentTriggered.value),
)
const route = useRoute(),
  router = useRouter()
const data = ref({ items: [], total: 0 }),
  q = ref(''),
  category = ref('all'),
  sort = ref('change'),
  tradable = ref(false),
  recentTriggered = ref(false),
  offset = ref(0),
  error = ref(''),
  loading = ref(false),
  detail = ref(null),
  detailPanel = ref(null),
  chartMode = ref('daily')
const tradeHistory = useTradeHistory(computed(() => detail.value?.symbol))
const liveCandle = useLiveCandle(
  computed(() => (detail.value?.history_only ? null : detail.value?.symbol)),
  computed(() => chartMode.value === 'daily'),
)
watch(pendingRemoval, (item) => {
  if (item) error.value = ''
})
const lastQuoteAt = computed(() =>
  data.value.items
    .map((item) => item.quote?.at)
    .filter(Boolean)
    .sort()
    .at(-1),
)
function selectSort(key) {
  sort.value = sort.value === key ? `${key}_asc` : key
}
function sortDirection(key) {
  return sort.value === key ? 'descending' : sort.value === `${key}_asc` ? 'ascending' : 'none'
}
function sortLabel(key) {
  return sort.value === key ? '↓' : sort.value === `${key}_asc` ? '↑' : '↕'
}
function resetFilters() {
  q.value = ''
  category.value = 'all'
  tradable.value = false
  recentTriggered.value = false
  sort.value = 'change'
}
const performanceColumns = [
  { key: 'low_change_pct', label: '今日最低', description: '当日最低价相对昨收的涨跌幅' },
  { key: 'high_change_pct', label: '今日最高', description: '当日最高价相对昨收的涨跌幅' },
  ...[5, 10, 20].map((days) => ({
    key: `return${days}`,
    label: `${days} 日涨跌幅`,
    description: `最近 ${days} 个交易日累计涨跌幅，前复权口径，包含当日行情`,
  })),
]
function performanceTitle(item, column) {
  const performance = item.performance
  return [
    column.description,
    performance?.as_of && `指标日期：${performance.as_of}`,
    performance?.reasons?.[column.key],
  ]
    .filter(Boolean)
    .join('；')
}
watch(
  () => route.query.symbol,
  (symbol) => {
    if (typeof symbol === 'string') open(symbol)
  },
)
let timer,
  debounce,
  returnFocus,
  previousOverflow,
  requestId = 0,
  detailRequest = 0
watch(detail, async (value, previous) => {
  if (value && !previous) {
    returnFocus = document.activeElement
    previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    await nextTick()
    detailPanel.value?.querySelector('button')?.focus()
  } else if (!value && previous) {
    document.body.style.overflow = previousOverflow ?? ''
    returnFocus?.focus()
  }
})
function detailKey(event) {
  // A nested chart dialog owns Escape and focus while it is open.
  if (event.target.closest('dialog[open]')) return
  if (event.key === 'Escape') {
    event.preventDefault()
    closeDetail()
  }
  if (event.key !== 'Tab') return
  const controls = Array.from(
    detailPanel.value.querySelectorAll(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), summary, a[href], [tabindex="0"]',
    ),
  ).filter((control) => control.getClientRects().length)
  const first = controls[0],
    last = controls.at(-1)
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}
async function load() {
  const id = ++requestId
  loading.value = true
  try {
    const result = await api(
      `/etfs?${new URLSearchParams({ q: q.value, category: category.value, sort: sort.value, tradable: tradable.value, recent_triggered: recentTriggered.value, offset: offset.value, limit: 30 })}`,
    )
    if (id === requestId) {
      data.value = result
      error.value = ''
    }
  } catch (e) {
    if (id === requestId) error.value = e.message
  } finally {
    if (id === requestId) loading.value = false
  }
}
watch([q, category, sort, tradable, recentTriggered], () => {
  offset.value = 0
  clearTimeout(debounce)
  debounce = setTimeout(load, 250)
})
watch(offset, async () => {
  await load()
  if (mobile.value) marketPanel.value?.scrollIntoView({ block: 'start' })
})
onMounted(() => {
  if (typeof route.query.symbol === 'string') open(route.query.symbol)
  load()
  timer = setInterval(() => {
    if (!document.hidden) {
      load()
      if (detail.value) refreshDetail()
    }
  }, 30000)
})
onUnmounted(() => {
  clearInterval(timer)
  clearTimeout(debounce)
  requestId++
  detailRequest++
  if (detail.value) document.body.style.overflow = previousOverflow ?? ''
})
async function open(symbol) {
  const id = ++detailRequest
  try {
    const result = await api(`/etfs/${symbol}`)
    if (id === detailRequest) {
      chartMode.value = 'daily'
      detail.value = result
    }
  } catch (e) {
    error.value = e.message
  }
}
async function refreshDetail() {
  const symbol = detail.value?.symbol
  if (!symbol) return
  const id = ++detailRequest
  try {
    const result = await api(`/etfs/${symbol}`)
    if (id !== detailRequest || detail.value?.symbol !== symbol) return
    if (JSON.stringify(result.bars) === JSON.stringify(detail.value.bars)) result.bars = detail.value.bars
    detail.value = result
  } catch {
    // Preserve the last dated review and candles during a temporary outage.
  }
}
function closeDetail() {
  detailRequest++
  detail.value = null
  if (route.query.symbol) {
    const query = { ...route.query }
    delete query.symbol
    router.replace({ query })
  }
}
const showAddEtf = ref(false),
  removing = ref('')
async function addedEtf(result) {
  showAddEtf.value = false
  resetFilters()
  offset.value = 0
  notify(result.message)
  await load()
  await refresh()
}
async function removeEtf(item) {
  if (removing.value) return
  removing.value = item.symbol
  try {
    const result = await api(`/etfs/${item.symbol}`, { method: 'DELETE' })
    pendingRemoval.value = null
    notify(`${item.name}：${result.message}`)
    if (detail.value?.symbol === item.symbol) closeDetail()
    if (data.value.items.length === 1 && offset.value > 0) offset.value -= 30
    await load()
    await refresh()
  } catch (e) {
    error.value = e.message
  } finally {
    removing.value = ''
  }
}
</script>
<template>
  <div class="market-stats market-overview">
    <div>
      <span>关注标的</span><strong>{{ money(state.status.catalog_count, 0) }}<small>只</small></strong>
    </div>
    <div>
      <span>可参与策略</span
      ><strong>{{ money(state.status.watched_tradable_count, 0) }}<small>只</small></strong>
    </div>
    <div>
      <span>有效行情</span><strong>{{ money(state.status.watched_quote_count, 0) }}<small>只</small></strong>
    </div>
    <div>
      <span>最新行情日期</span
      ><strong class="date-value">{{ lastQuoteAt?.slice(0, 10) || '暂无行情' }}</strong>
    </div>
  </div>
  <section ref="marketPanel" class="panel market-panel" :aria-busy="loading">
    <div class="panel-heading">
      <div>
        <h2>
          行情列表 <span class="count-badge">{{ data.total }}</span>
        </h2>
      </div>
      <div class="market-heading-tools">
        <button type="button" class="button primary compact" @click="showAddEtf = true">添加 ETF</button>
        <button
          class="icon-button"
          @click="load"
          :disabled="loading"
          aria-label="刷新行情"
          :title="loading ? '正在刷新' : '刷新行情'"
        >
          <Icon name="refresh" :size="17" />
        </button>
      </div>
    </div>
    <div class="market-toolbar">
      <div class="search-input">
        <Icon name="search" :size="18" /><input
          v-model="q"
          type="search"
          enterkeyhint="search"
          @keydown.enter="$event.target.blur()"
          placeholder="搜索代码、名称、指数"
          aria-label="搜索已添加 ETF"
        />
      </div>
      <button
        v-if="mobile"
        class="button secondary filter-toggle"
        :aria-expanded="filtersOpen"
        aria-controls="market-filters"
        @click="filtersOpen = !filtersOpen"
      >
        {{ filtersOpen ? '收起' : '筛选' }}{{ activeFilters ? ` · ${activeFilters}` : '' }}
        <Icon name="chevron" :size="14" />
      </button>
      <div id="market-filters" v-show="!mobile || filtersOpen" class="filter-controls">
        <button
          type="button"
          class="button compact"
          :class="recentTriggered ? 'primary' : 'secondary'"
          :aria-pressed="recentTriggered"
          :disabled="loading"
          title="筛选最近十个已收盘交易日出现买点复盘“触”标记的 ETF，数量随搜索和类别条件变化"
          @click="recentTriggered = !recentTriggered"
        >
          近十日触发 · {{ data.recent_triggers?.matched_count ?? '—' }}
        </button>
        <select v-model="category" aria-label="基金类别">
          <option value="all">全部类别</option>
          <option v-for="(label, value) in categories" :key="value" :value="value">
            {{ label }}
          </option></select
        ><select v-model="sort" aria-label="排序方式">
          <option value="change">今日涨幅优先 ↓</option>
          <option value="change_asc">今日跌幅优先 ↑</option>
          <template v-for="column in performanceColumns" :key="column.key">
            <option :value="column.key">{{ column.label }} ↓</option>
            <option :value="`${column.key}_asc`">{{ column.label }} ↑</option>
          </template>
          <option value="price">最新价 ↓</option>
          <option value="price_asc">最新价 ↑</option>
          <option value="symbol">代码排序</option></select
        ><label class="check-label"><input type="checkbox" v-model="tradable" />可自动交易</label>
        <button
          v-if="q || category !== 'all' || tradable || recentTriggered"
          class="text-button"
          @click="resetFilters"
        >
          重置
        </button>
      </div>
    </div>
    <p v-if="recentTriggered && data.recent_triggers" class="performance-note" role="status">
      {{ data.recent_triggers.window_start }} 至
      {{ data.recent_triggers.as_of }}：按当前设置复盘，展示近十个已收盘交易日出现“触”标记的买入候选。
      <template v-if="data.recent_triggers.pending_count">
        当前查询范围内还有 {{ data.recent_triggers.pending_count }} 只 ETF 的复盘尚未就绪。
      </template>
    </p>
    <p v-if="error" class="form-error padded" role="alert">{{ error }}</p>
    <div v-if="mobile && data.items.length" class="mobile-quotes">
      <div class="mobile-quote-columns">
        <span>名称 / 代码</span>
        <button
          @click="selectSort('price')"
          :aria-label="`按最新价排序，当前${sortDirection('price') === 'descending' ? '降序' : sortDirection('price') === 'ascending' ? '升序' : '未排序'}`"
        >
          最新价 {{ sortLabel('price') }}
        </button>
        <button
          @click="selectSort('change')"
          :aria-label="`按涨跌幅排序，当前${sortDirection('change') === 'descending' ? '降序' : sortDirection('change') === 'ascending' ? '升序' : '未排序'}`"
        >
          涨跌幅 {{ sortLabel('change') }}
        </button>
      </div>
      <ul>
        <li v-for="item in data.items" :key="item.symbol">
          <button
            class="mobile-quote-row"
            @click="open(item.symbol)"
            :aria-label="`查看${displayCode(item.name)}行情详情`"
          >
            <span class="mobile-security">
              <strong>{{ displayCode(item.name) }}</strong>
              <small
                >{{ displayCode(item.symbol) }}
                <span
                  v-if="!item.quote || item.quote.stale || item.quote.quality === 'unknown'"
                  class="amberText"
                  >· {{ !item.quote ? '暂无行情' : item.quote.stale ? '已过期' : '待核验' }}</span
                ></small
              >
            </span>
            <b class="mobile-quote-price" :class="changeClass(item.performance?.change_pct)">{{
              money(item.quote?.last, 3)
            }}</b>
            <b class="mobile-quote-change" :class="changeClass(item.performance?.change_pct)">{{
              pct(item.performance?.change_pct, true)
            }}</b>
          </button>
        </li>
      </ul>
      <p class="mobile-list-hint">点按标的查看图表、更多指标与管理操作</p>
    </div>
    <div
      class="table-scroll"
      v-else-if="data.items.length"
      tabindex="0"
      role="region"
      aria-label="ETF 行情，可横向滚动查看更多指标"
    >
      <table class="market-table">
        <thead>
          <tr>
            <th>ETF 名称 / 代码</th>
            <th class="number-cell" :aria-sort="sortDirection('price')">
              <button class="sort-button" @click="selectSort('price')">
                最新价 <span>{{ sortLabel('price') }}</span>
              </button>
            </th>
            <th class="number-cell" :aria-sort="sortDirection('change')">
              <button class="sort-button" @click="selectSort('change')">
                今日涨跌幅 <span>{{ sortLabel('change') }}</span>
              </button>
            </th>
            <th
              v-for="column in performanceColumns"
              :key="column.key"
              class="number-cell performance-cell"
              :aria-sort="sortDirection(column.key)"
              :title="column.description"
            >
              <button class="sort-button" @click="selectSort(column.key)">
                {{ column.label }} <span>{{ sortLabel(column.key) }}</span>
              </button>
            </th>
            <th>分时走势</th>
            <th>投资方向</th>
            <th>行情时间</th>
            <th>管理</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in data.items"
            :key="item.symbol"
            :class="{ 'selected-row': detail?.symbol === item.symbol }"
          >
            <td>
              <div class="security-cell">
                <button
                  class="table-name"
                  @click="open(item.symbol)"
                  :title="`查看${displayCode(item.name)}日 K 线`"
                >
                  {{ displayCode(item.name) }}
                </button>
                <div class="security-code">
                  <small>{{ displayCode(item.symbol) }}</small
                  ><button
                    class="kline-entry"
                    @click="open(item.symbol)"
                    :aria-label="`查看${displayCode(item.name)}日 K 线`"
                  >
                    日 K
                  </button>
                </div>
              </div>
            </td>
            <td class="number-cell quote-price" :class="changeClass(item.performance?.change_pct)">
              {{ money(item.quote?.last, 3) }}
            </td>
            <td
              class="number-cell quote-change"
              :class="changeClass(item.performance?.change_pct)"
              :title="performanceTitle(item, { key: 'change_pct', description: '当日相对昨收的涨跌幅' })"
            >
              {{ pct(item.performance?.change_pct, true) }}
            </td>
            <td
              v-for="column in performanceColumns"
              :key="column.key"
              class="number-cell performance-cell"
              :class="changeClass(item.performance?.[column.key])"
              :title="performanceTitle(item, column)"
            >
              {{ pct(item.performance?.[column.key], true) }}
            </td>
            <td>
              <IntradayView
                v-if="item.representative"
                :key="item.symbol"
                :symbol="item.symbol"
                :name="displayCode(item.name)"
                :open-label="`查看${displayCode(item.name)}日 K 详情`"
                compact
                @open="open(item.symbol)"
              />
              <span v-else class="muted">—</span>
            </td>
            <td
              class="focus-cell"
              :title="`${item.focus_category_label} · ${item.focus_market_label}；${item.focus_reason}`"
            >
              <span class="focus-label">{{ item.focus_label }}</span>
            </td>
            <td :title="`行情时间：${dateTime(item.quote?.at)}`">
              <span class="quote-time">{{
                item.quote?.at
                  ? `${item.quote.at.slice(5, 10)} ${item.quote.at.slice(11, 16)}`
                  : item.performance?.as_of || '—'
              }}</span>
              <small
                v-if="!item.quote || item.quote.stale || item.quote.quality === 'unknown'"
                :class="{ amberText: item.quote?.stale }"
                >{{
                  !item.quote
                    ? '暂无行情'
                    : item.quote.stale
                      ? '已过期 · 待更新'
                      : item.quote.quality === 'unknown'
                        ? '待核验'
                        : '有效行情'
                }}</small
              >
            </td>
            <td>
              <button
                class="text-button"
                :disabled="!!removing"
                @click="pendingRemoval = item"
                :aria-label="`移除${displayCode(item.name)}`"
              >
                {{ removing === item.symbol ? '移除中…' : '移除' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <Empty
      v-else
      :title="
        loading
          ? '正在读取 ETF 行情'
          : q || category !== 'all' || tradable || recentTriggered
            ? '没有匹配的 ETF'
            : '还没有添加 ETF'
      "
      :description="
        recentTriggered
          ? '当前条件下暂无已确认的近十日触发 ETF，可关闭筛选或重置查询条件。'
          : q || category !== 'all' || tradable
            ? '试试其他代码、名称或类别，或重置查询条件。'
            : '点击“添加 ETF”，选择并确认后开始关注，自动获取行情和日 K。'
      "
    />
    <p class="performance-note">涨跌幅含当日行情，按交易日计算；缺失数据以“—”表示。</p>
    <div class="pagination">
      <span>{{ loading ? '正在刷新…' : `共 ${data.total} 只 · 每页 30 只` }}</span>
      <div>
        <button :disabled="loading || offset === 0" @click="offset -= 30">上一页</button
        ><span>{{ Math.floor(offset / 30) + 1 }}</span
        ><button :disabled="loading || offset + 30 >= data.total" @click="offset += 30">下一页</button>
      </div>
    </div>
  </section>
  <AddEtfDialog v-if="showAddEtf" @close="showAddEtf = false" @added="addedEtf" />
  <div class="drawer-backdrop" v-if="detail" @click.self="closeDetail">
    <section
      class="drawer market-detail"
      ref="detailPanel"
      role="dialog"
      aria-modal="true"
      aria-labelledby="detail-title"
      @keydown="detailKey"
    >
      <header class="market-detail-heading">
        <div>
          <div class="section-kicker">{{ displayCode(detail.symbol) }}</div>
          <h2 id="detail-title">{{ displayCode(detail.name) }}</h2>
        </div>
        <button class="icon-button" @click="closeDetail" aria-label="关闭详情"><Icon name="close" /></button>
      </header>
      <div class="detail-price" :class="changeClass(detail.performance?.change_pct)">
        {{ money(detail.quote?.last, 3)
        }}<span :class="changeClass(detail.performance?.change_pct)">{{
          pct(detail.performance?.change_pct, true)
        }}</span>
      </div>
      <div class="detail-tags">
        <span class="chip">{{ categories[detail.category] }}</span
        ><span class="chip neutral">{{
          detail.watched ? '手动添加' : detail.history_only ? '历史委托' : '持仓跟踪'
        }}</span
        ><span class="chip amber" v-if="detail.quote?.stale">行情已陈旧</span>
      </div>
      <div v-if="chartMode === 'intraday'" class="quote-facts">
        <div>
          <span>涨跌额</span
          ><strong :class="changeClass(detail.quote?.change_pct)">{{
            detail.quote?.last != null && detail.quote?.previous_close > 0
              ? signedMoney(detail.quote.last - detail.quote.previous_close, 3)
              : '—'
          }}</strong>
        </div>
        <div>
          <span>昨收</span
          ><strong>{{
            detail.quote?.previous_close > 0 ? money(detail.quote.previous_close, 3) : '—'
          }}</strong>
        </div>
        <div>
          <span>今日最低涨跌幅</span
          ><strong :class="changeClass(detail.performance?.low_change_pct)">{{
            pct(detail.performance?.low_change_pct, true)
          }}</strong>
        </div>
        <div>
          <span>今日最高涨跌幅</span
          ><strong :class="changeClass(detail.performance?.high_change_pct)">{{
            pct(detail.performance?.high_change_pct, true)
          }}</strong>
        </div>
        <div>
          <span>买一价</span><strong>{{ detail.quote?.bid > 0 ? money(detail.quote.bid, 3) : '—' }}</strong>
        </div>
        <div>
          <span>卖一价</span><strong>{{ detail.quote?.ask > 0 ? money(detail.quote.ask, 3) : '—' }}</strong>
        </div>
        <div>
          <span>行情时间</span
          ><strong class="quote-time" :class="{ amberText: detail.quote?.stale }">{{
            detail.quote?.at ? `${detail.quote.at.slice(5, 10)} ${detail.quote.at.slice(11, 19)}` : '暂无行情'
          }}</strong>
        </div>
      </div>
      <div class="price-tabs" role="group" aria-label="价格走势类型">
        <button
          :class="{ selected: chartMode === 'daily' }"
          :aria-pressed="chartMode === 'daily'"
          @click="chartMode = 'daily'"
        >
          日 K
        </button>
        <button
          :class="{ selected: chartMode === 'intraday' }"
          :aria-pressed="chartMode === 'intraday'"
          @click="chartMode = 'intraday'"
        >
          分时
        </button>
      </div>
      <IntradayView
        v-if="chartMode === 'intraday'"
        :key="detail.symbol"
        :symbol="detail.symbol"
        :name="displayCode(detail.name)"
        :trade-history="tradeHistory"
      />
      <CandlestickChart
        v-else
        :key="detail.symbol"
        :points="detail.bars || []"
        :name="displayCode(detail.name)"
        :trade-history="tradeHistory"
        :buy-review="detail.buy_review"
        :live-candle="liveCandle"
        @show-intraday="chartMode = 'intraday'"
        :compact="mobile"
      />
      <details class="market-metadata">
        <summary>更多指标与基金资料 <Icon name="chevron" :size="14" /></summary>
        <dl class="detail-list">
          <dt>ETF 分类</dt>
          <dd>{{ detail.focus_category_label }} · {{ detail.focus_market_label }}</dd>
          <dt>分类依据</dt>
          <dd>{{ detail.focus_category_reason }}</dd>
          <dt>投资方向</dt>
          <dd>{{ detail.focus_label }}</dd>
          <template v-for="column in performanceColumns" :key="column.key">
            <dt>{{ column.label }}</dt>
            <dd
              :class="changeClass(detail.performance?.[column.key])"
              :title="performanceTitle(detail, column)"
            >
              {{ pct(detail.performance?.[column.key], true) }}
            </dd>
          </template>
          <dt>跟踪指数</dt>
          <dd>{{ displayCode(detail.index_id) || '尚未确认' }}</dd>
          <dt>投资区域</dt>
          <dd>{{ detail.region === 'CN' ? '境内' : detail.region === 'OVERSEAS' ? '境外' : '待核验' }}</dd>
          <dt>行情更新时间</dt>
          <dd>{{ dateTime(detail.quote?.at) }}</dd>
          <dt>资料更新时间</dt>
          <dd>{{ dateTime(detail.updated_at) }}</dd>
          <dt>交易机制</dt>
          <dd>{{ detail.verified ? `T+${detail.settlement}` : '待核验' }}</dd>
        </dl>
      </details>
      <div class="notice" v-if="detail.accounting_block">{{ detail.accounting_block }}</div>
      <button v-if="detail.watched" class="button secondary remove-etf" @click="pendingRemoval = detail">
        移除这只 ETF
      </button>
    </section>
  </div>
  <WorkbenchDialog
    :open="!!pendingRemoval"
    title="移除 ETF"
    :close-disabled="!!removing"
    @update:open="(open) => !open && (pendingRemoval = null)"
  >
    <template v-if="pendingRemoval">
      <p>
        确定从关注名单移除 <strong>{{ displayCode(pendingRemoval.name) }}</strong
        >（{{ displayCode(pendingRemoval.symbol) }}）？
      </p>
      <p class="muted">有持仓或待成交订单的 ETF 无法移除。</p>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <div class="dialog-actions">
        <button class="button secondary" :disabled="!!removing" @click="pendingRemoval = null">取消</button>
        <button class="button primary" :disabled="!!removing" @click="removeEtf(pendingRemoval)">
          {{ removing ? '移除中…' : '确认移除' }}
        </button>
      </div>
    </template>
  </WorkbenchDialog>
</template>
<style scoped>
.market-detail-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.market-detail-heading > div {
  min-width: 0;
}
.market-detail-heading .icon-button {
  flex-shrink: 0;
}
.market-metadata > summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 44px;
  margin-top: 12px;
  font-size: 12px;
  cursor: pointer;
}
.remove-etf {
  margin-top: 16px;
}
@media (max-width: 700px) {
  .market-detail-heading {
    position: sticky;
    top: -12px;
    z-index: 4;
    margin: -12px -12px 12px;
    padding: 10px 12px;
    background: var(--panel);
    border-bottom: 1px solid var(--line);
  }
  .market-detail-heading h2 {
    font-size: 17px;
    margin: 2px 0 0;
  }
  .market-detail-heading .section-kicker {
    font-size: 10px;
  }
  .market-panel .market-toolbar {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
  }
  .market-toolbar .search-input {
    min-width: 0;
    width: 100%;
    max-width: none;
  }
  .filter-controls {
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .filter-controls select {
    width: 100%;
    min-width: 0;
  }
  .filter-controls > button:first-child {
    grid-column: 1;
    grid-row: 2;
  }
  .filter-controls .check-label {
    grid-column: 2;
    grid-row: 2;
  }
  .filter-controls .text-button {
    justify-self: end;
    grid-column: 2;
  }
  .filter-controls .check-label {
    font-size: 12px;
  }
  .price-tabs {
    margin: 10px 0;
  }
  .price-tabs button {
    flex: 1;
    min-height: 44px;
  }
  .market-detail {
    padding: 12px;
  }
}

.market-panel {
  overflow: visible;
}
.manual-etf-note {
  padding: 14px 20px;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.7;
  border-top: 1px solid var(--line);
}
.price-tabs {
  display: flex;
  gap: 8px;
  margin: 20px 0 16px;
  border-bottom: 1px solid var(--line);
  padding-bottom: 10px;
}
.price-tabs button {
  border: 1px solid transparent;
  background: transparent;
  color: var(--muted);
  border-radius: var(--radius-control);
  padding: 7px 12px;
  font-size: 12px;
}
.price-tabs button.selected {
  color: var(--accent);
  background: var(--accent-soft);
  border-color: var(--line);
}

@media (max-width: 700px) {
  .market-detail .price-tabs {
    margin: 10px 0;
  }
}

.security-cell .table-name {
  display: block;
  min-height: 22px;
}
.security-code {
  display: flex;
  align-items: center;
  gap: 12px;
}
.security-code small {
  font: 10px var(--font-numeric);
  margin: 0;
}
.kline-entry {
  border: 1px solid var(--accent-border);
  color: var(--accent-text);
  background: var(--accent-soft);
  border-radius: 3px;
  font-size: 10px;
  padding: 1px 5px;
  min-height: 22px;
}
.kline-entry:hover {
  border-color: var(--accent);
}
.market-detail {
  width: min(820px, 100%);
}
@media (max-width: 600px) {
  .security-code {
    gap: 6px;
  }
}
</style>
