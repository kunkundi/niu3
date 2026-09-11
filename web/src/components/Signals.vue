<script setup>
import { displayCode, displayCodeText } from '../display-code.js'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, state, money, pct, amount, dateTime, changeClass } from '../state'
import Empty from './Empty.vue'
import Icon from './Icon.vue'
import SelectedEtfChart from './SelectedEtfChart.vue'
import SignalWatchlist from './SignalWatchlist.vue'
import WorkbenchDialog from './WorkbenchDialog.vue'
import SignalHistory from './SignalHistory.vue'
import { signalWatchlistGroups } from '../signal-watchlist.js'
import { useMobile } from '../mobile'
const mobile = useMobile()
const route = useRoute(),
  router = useRouter()
const expandedSymbol = ref(null)
const chartFocusSymbol = ref(null)
function focusSelectedChart(symbol) {
  if (chartFocusSymbol.value !== symbol) return
  chartFocusSymbol.value = null
  document.getElementById('selected-etf-chart')?.scrollIntoView({ block: 'start' })
}
async function selectChart(symbol) {
  if (mobile.value) {
    expandedSymbol.value = symbol
    alignMobileTab(symbol)
    await router.replace({ query: { ...route.query, symbol } })
    return
  }
  const changed = symbol !== chartSymbol.value
  chartFocusSymbol.value = window.matchMedia('(max-width: 900px)').matches ? symbol : null
  await router.replace({ query: { ...route.query, symbol } })
  if (!changed) {
    await nextTick()
    focusSelectedChart(symbol)
  }
}
function selectListChart(symbol) {
  if (mobile.value && expandedSymbol.value === symbol) expandedSymbol.value = null
  else selectChart(symbol)
}
function selectListTab(tab) {
  if (tab === listTab.value) return
  expandedSymbol.value = null
  listTab.value = tab
}
function alignMobileTab(symbol) {
  if (listRows.value.some((row) => row.symbol === symbol)) return
  listTab.value = holdingRows.value.some((row) => row.symbol === symbol)
    ? 'holdings'
    : selectedRows.value.some((row) => row.symbol === symbol)
      ? 'selected'
      : candidateRows.value.some((row) => row.symbol === symbol)
        ? 'candidates'
        : listTab.value
}
const detailsOpen = ref(false),
  historyOpen = ref(false),
  listTab = ref('candidates')
function selectDetailChart(symbol) {
  detailsOpen.value = false
  selectChart(symbol)
}
const data = ref({ rows: [], targets: {} }),
  error = ref(''),
  filter = ref('selected'),
  page = ref(0),
  loading = ref(false)
const mode = computed(
  () => data.value.mode || (state.status.execution_mode === 'intraday' ? 'live' : 'daily'),
)
const postClose = computed(() => mode.value === 'post_close')
const groups = computed(() => signalWatchlistGroups(data.value, state.account?.positions || []))
const candidateRows = computed(() => groups.value.candidates)
const selectedRows = computed(() => groups.value.selected)
const holdingRows = computed(() => groups.value.holdings)
const listRows = computed(() => groups.value[listTab.value])
const contextRow = computed(() => {
  const symbol = expandedSymbol.value
  if (!mobile.value || !symbol || listRows.value.some((row) => row.symbol === symbol)) return null
  return (
    data.value.rows.find((row) => row.symbol === symbol) || {
      symbol,
      name: symbol === 'sh000001' ? '上证指数' : displayCode(symbol),
    }
  )
})
const chartSymbol = computed(() =>
  typeof route.query.symbol === 'string'
    ? route.query.symbol
    : groups.value[listTab.value]?.[0]?.symbol ||
      holdingRows.value[0]?.symbol ||
      selectedRows.value[0]?.symbol ||
      'sh000001',
)
watch(
  () => route.query.symbol,
  (symbol) => {
    if (mobile.value) {
      expandedSymbol.value = typeof symbol === 'string' ? symbol : null
      if (expandedSymbol.value) alignMobileTab(symbol)
    }
  },
  { immediate: true },
)
watch(groups, () => {
  if (mobile.value && expandedSymbol.value) alignMobileTab(expandedSymbol.value)
})
watch(mobile, (value) => {
  chartFocusSymbol.value = null
  if (value) {
    expandedSymbol.value = chartSymbol.value
    alignMobileTab(chartSymbol.value)
  }
})
const targetExposure = computed(() =>
  Object.values(data.value.targets || {}).reduce((total, weight) => total + Number(weight), 0),
)
const isPa = computed(
  () =>
    data.value.strategy?.startsWith('price-action-') ||
    (!data.value.strategy && state.status.strategy_model === 'price_action'),
)
const strategyName = computed(() =>
  isPa.value
    ? `裸 K 价格行为 ${data.value?.strategy?.startsWith('price-action-') ? data.value.strategy.split('-').at(-1) : 'v2'}`
    : '趋势动量轮动 v1',
)
function level(row, key) {
  if (postClose.value) return row.pa?.[key] > 0 ? money(row.pa[key], 3) : '—'
  const value = row.pa?.raw?.[key]
  return value == null ? '—' : (value / 1e6).toFixed(3)
}
const filtered = computed(() =>
  data.value.rows.filter(
    (r) =>
      (filter.value === 'all' && r.representative) ||
      (filter.value === 'selected' && (postClose.value ? r.post_close_candidate : r.selected)) ||
      (filter.value === 'excluded' &&
        r.representative &&
        !(postClose.value ? r.post_close_candidate : r.selected)),
  ),
)
const visible = computed(() => filtered.value.slice(page.value * 30, (page.value + 1) * 30))
let timer,
  active = true
async function load() {
  if (loading.value || !active) return
  loading.value = true
  try {
    const result = await api('/signals?mode=auto')
    if (active) {
      data.value = result
      page.value = Math.min(page.value, Math.max(0, Math.ceil(filtered.value.length / 30) - 1))
      error.value = ''
    }
  } catch (e) {
    if (active) error.value = e.message
  } finally {
    loading.value = false
  }
}
function selectTab(value) {
  filter.value = value
  page.value = 0
}
function refreshVisible() {
  if (!document.hidden) load()
}
onMounted(() => {
  load()
  timer = setInterval(refreshVisible, 30000)
  document.addEventListener('visibilitychange', refreshVisible)
})
onUnmounted(() => {
  active = false
  clearInterval(timer)
  document.removeEventListener('visibilitychange', refreshVisible)
})
</script>
<template>
  <div class="signal-workbench">
    <div class="workbench-summary" aria-label="全局关键信息">
      <template v-if="postClose">
        <span><b>盘后参考</b> · 日 K {{ data.as_of }}</span>
        <span
          >下一交易日 <b>{{ data.execute_day }}</b></span
        >
      </template>
      <template v-else>
        <span
          >目标仓位 <b>{{ data.id ? pct(targetExposure) : '—' }}</b></span
        >
        <span
          >行情 <b>{{ data.quote_count ?? '—' }}/{{ data.representative_count ?? '—' }}</b></span
        >
        <span class="summary-updated" :class="{ amberText: data.stale }" :title="data.message"
          >{{ data.session_snapshot ? '休市快照' : data.stale ? '待更新' : '更新' }}
          {{
            data.session_snapshot
              ? data.created_at?.slice(5, 16).replace('T', ' ')
              : data.created_at?.slice(11, 19) || '—'
          }}</span
        >
      </template>
      <div class="summary-details">
        <button v-if="mobile" class="text-button" @click="selectChart('sh000001')">上证指数</button>
        <button class="text-button" @click="detailsOpen = true">运行详情</button
        ><button class="text-button" @click="historyOpen = true">历史与复盘</button>
      </div>
      <span v-if="error" class="form-error summary-error" role="alert">{{ error }}</span>
      <span v-if="data.post_close_pending" class="post-close-note amberText"
        >盘后分析待就绪，完成后自动更新</span
      >
    </div>
    <SignalWatchlist
      class="signal-watchlist"
      :title="listTab === 'holdings' ? '持仓 ETF' : listTab === 'selected' ? '入选 ETF' : '候选 ETF'"
      :rows="listRows"
      :context-row="contextRow"
      :plan="data"
      :symbol="mobile ? expandedSymbol : chartSymbol"
      :expandable="mobile"
      :loading="listTab === 'holdings' ? !state.account && !state.error : loading"
      :empty-message="
        listTab === 'holdings'
          ? state.account
            ? '当前暂无持仓'
            : state.error || '正在读取账户持仓…'
          : !data.id
            ? error || data.message || '等待生成当前交易信号'
            : listTab === 'selected'
              ? postClose
                ? '当前暂无满足条件的盘后入选 ETF'
                : '当前暂无策略入选 ETF'
              : '当前暂无未入选的候选 ETF'
      "
      :tabs="[
        {
          value: 'candidates',
          label: '候选',
          count: data.id ? candidateRows.length : '—',
        },
        {
          value: 'selected',
          label: '入选',
          count: data.id ? selectedRows.length : '—',
        },
        {
          value: 'holdings',
          label: '持仓',
          count: state.account ? holdingRows.length : '—',
        },
      ]"
      :active-tab="listTab"
      compact
      @update:active-tab="selectListTab"
      @select="selectListChart"
    >
      <template #expanded="{ row }">
        <SelectedEtfChart
          :plan="data"
          :symbol="row.symbol"
          :embedded="row.symbol !== 'sh000001' && !!row.quote"
          @select="selectChart"
          @refresh="load"
        />
      </template>
    </SignalWatchlist>
    <SelectedEtfChart
      v-if="!mobile"
      class="workbench-chart"
      :plan="data"
      :symbol="chartSymbol"
      @select="selectChart"
      @refresh="load"
      @ready="focusSelectedChart"
    />
    <SignalHistory v-model:open="historyOpen" />
    <WorkbenchDialog v-model:open="detailsOpen" title="策略与运行详情">
      <div class="signal-meta">
        <span>{{
          postClose
            ? '盘后结构随完整日 K 更新 · 开盘自动切回盘中信号'
            : mode === 'live'
              ? `后台每 ${data.refresh_seconds || 60} 秒重算 · 页面每 30 秒同步`
              : '交易日 15:30 起自动生成'
        }}</span>
      </div>
      <div class="notice" role="status" v-if="data.message">
        <Icon name="clock" :size="18" />
        <div>
          <strong>{{ data.message }}</strong>
          <p v-if="mode === 'live' && data.created_at">
            最近计算 {{ dateTime(data.created_at) }} · 有效行情 {{ data.quote_count }} /
            {{ data.representative_count }} 只
            <span v-if="data.missing_quotes" class="amberText">
              · {{ data.missing_quotes }} 只行情待齐，本轮未参与排名</span
            >
            <span v-if="data.stale" class="amberText"> · 当前展示上次计算结果</span>
          </p>
          <p v-if="postClose || mode === 'daily'">{{ data.execution_message }}</p>
          <p v-else-if="data.execution_mode === 'intraday'">
            {{ data.execution?.message }} · {{ data.t_enabled ? '底仓做 T 已启用' : '底仓做 T 已关闭' }}
          </p>
        </div>
      </div>
      <div class="signal-stats">
        <div>
          <span>{{ postClose ? '次日候选' : '当前入选' }}</span
          ><strong
            >{{ data.id ? (postClose ? selectedRows.length : Object.keys(data.targets).length) : '—'
            }}<small>只</small></strong
          >
        </div>
        <div>
          <span>{{ postClose ? '就绪结构' : '目标总仓位' }}</span
          ><strong>{{
            postClose
              ? `${data.structure_count} / ${data.representative_count}`
              : data.id
                ? pct(targetExposure)
                : '—'
          }}</strong>
        </div>
        <div>
          <span>{{
            postClose ? '下一交易日 · 盘中确认' : mode === 'live' ? '最近计算时间' : '计划执行日 · 09:35 起'
          }}</span
          ><strong class="signal-date">{{
            mode === 'live' ? data.created_at?.slice(11, 19) || '等待计算' : data.execute_day || '等待生成'
          }}</strong>
        </div>
      </div>
      <div class="signal-meta">
        <span
          >{{ mode === 'live' ? '日 K 基准' : '信号日期' }}
          <strong>{{ data.as_of || '等待生成' }}</strong></span
        ><span
          >策略模型 <strong>{{ strategyName }}</strong></span
        ><span
          >参数版本 <strong>{{ data.config_id ? 'v' + data.config_id : '—' }}</strong></span
        ><span>{{
          postClose ? '盘后观察参考' : mode === 'live' ? '当前信号自动更新' : '按当前日频计划执行'
        }}</span>
      </div>
      <details class="system-details signal-detail-table">
        <summary>策略明细 <span>候选、入选与排除原因</span><Icon name="chevron" :size="15" /></summary>
        <section class="panel signals-panel">
          <div class="panel-heading">
            <div class="tabs" role="group" aria-label="策略记录类型">
              <button
                v-for="tab in [
                  { value: 'selected', label: postClose ? '次日候选' : '当前入选' },
                  { value: 'excluded', label: postClose ? '等待条件' : '暂未入选' },
                  { value: 'all', label: '手动名单' },
                ]"
                :key="tab.value"
                :class="{ selected: filter === tab.value }"
                :aria-pressed="filter === tab.value"
                @click="selectTab(tab.value)"
              >
                {{ tab.label
                }}<span v-if="tab.value === 'selected'">{{
                  postClose ? selectedRows.length : Object.keys(data.targets).length
                }}</span>
              </button>
            </div>
            <button class="icon-button" @click="load" :disabled="loading" aria-label="刷新策略信号">
              <Icon name="refresh" :size="18" />
            </button>
          </div>
          <p class="form-error padded" v-if="error">{{ error }}</p>
          <div
            class="table-scroll"
            v-if="visible.length"
            tabindex="0"
            role="region"
            aria-label="策略信号，可横向滚动"
          >
            <table class="signals-table">
              <thead>
                <tr>
                  <th>排名 / ETF</th>
                  <th class="number-cell" title="最新价相对昨收的涨跌幅">实时涨幅</th>
                  <th class="number-cell">{{ postClose ? '参考状态' : '目标仓位' }}</th>
                  <th class="number-cell">
                    {{ isPa ? `入场 / 失效价${postClose ? '（前复权）' : ''}` : '动量评分' }}
                  </th>
                  <th class="number-cell">
                    {{ isPa ? `支撑 / 压力${postClose ? '（前复权）' : ''}` : '20 日 / 60 日收益' }}
                  </th>
                  <th>关注类型</th>
                  <th class="number-cell">20 日日均成交额</th>
                  <th class="number-cell">20 日日均换手率</th>
                  <th>入选依据 / 排除原因</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in visible" :key="row.symbol">
                  <td>
                    <div class="rank-name">
                      <span class="rank" :class="{ top: row.selected }">{{ row.rank || '—' }}</span>
                      <div>
                        <button
                          class="text-button"
                          @click="selectDetailChart(row.symbol)"
                          :aria-label="`查看${displayCode(row.name)}的图表`"
                        >
                          <strong>{{ displayCode(row.name) }}</strong>
                        </button>
                        <small>{{ displayCode(row.symbol) }}</small>
                      </div>
                    </div>
                  </td>
                  <td class="number-cell" :title="`行情时间：${dateTime(row.quote?.at)}`">
                    <span :class="changeClass(row.quote?.change_pct)">{{
                      pct(row.quote?.change_pct, true)
                    }}</span>
                    <small v-if="row.quote?.at">{{ row.quote.at.slice(5, 19).replace('T', ' ') }}</small>
                    <small v-if="!row.quote">暂无行情</small>
                    <small v-else-if="row.quote.stale" class="amberText">已过期 · 待更新</small>
                    <small v-else-if="row.quote.quality === 'unknown'" class="amberText">待核验</small>
                  </td>
                  <td class="number-cell">
                    <span :class="row.selected ? 'weight-pill' : ''">{{
                      postClose
                        ? row.post_close_candidate
                          ? '等待盘中确认'
                          : '等待条件'
                        : pct(row.target_weight)
                    }}</span>
                  </td>
                  <td v-if="isPa" class="number-cell">
                    {{ level(row, 'entry') }}<small>{{ level(row, 'entry_stop') }}</small>
                  </td>
                  <td v-else class="number-cell">
                    {{ row.score != null ? money(Number(row.score) * 100) : '—' }}
                  </td>
                  <td v-if="isPa" class="number-cell">
                    {{ level(row, 'support') }}<small>{{ level(row, 'resistance') }}</small>
                  </td>
                  <td v-else class="number-cell">
                    <span :class="changeClass(row.r20)">{{ pct(row.r20, true) }}</span
                    ><small :class="changeClass(row.r60)">{{ pct(row.r60, true) }}</small>
                  </td>
                  <td>
                    {{ row.focus_label
                    }}<small>{{ row.representative ? '手动添加' : row.focus_reason }}</small>
                    <small>{{ row.focus_category_label }} · {{ row.focus_market_label }}</small>
                  </td>
                  <td class="number-cell">{{ amount(row.amount20) }}</td>
                  <td class="number-cell" :title="row.turnover_reason">{{ pct(row.turnover20) }}</td>
                  <td class="reason-cell">
                    {{ row.reasons.length ? row.reasons.join('；') : '趋势与流动性达标，动量排名入选' }}
                    <small v-if="isPa && row.pa?.ready">
                      {{ row.pa.setup || '等待入场形态' }} · 可知日 {{ row.pa.signal_day || '—' }} · 目标
                      {{ level(row, 'target') }} · 摆动失效 {{ level(row, 'structural_stop') }}
                    </small>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else-if="loading" class="loading" role="status">正在读取策略信号…</div>
          <Empty
            v-else
            :title="
              data.id
                ? '当前没有符合条件的记录'
                : mode === 'live'
                  ? '等待自动计算交易信号'
                  : '等待生成当前策略计划'
            "
            :description="
              data.id
                ? '策略允许持有现金；未出现合格机会时不会强制买入。'
                : data.message || '等待日 K 数据与策略信号。'
            "
            icon="signals"
          />
          <div class="pagination">
            <span>共 {{ filtered.length }} 条</span>
            <div>
              <button :disabled="page === 0" @click="page--">上一页</button><span>{{ page + 1 }}</span
              ><button :disabled="(page + 1) * 30 >= filtered.length" @click="page++">下一页</button>
            </div>
          </div>
        </section>
      </details>
      <details
        v-if="mode === 'live' && data.execution_mode === 'intraday' && data.execution?.items?.length"
        class="filter-summary signal-rules"
      >
        <summary>
          <Icon name="clock" :size="14" /><strong>盘中执行详情</strong><span>买卖、做 T 与等待原因</span>
        </summary>
        <div>
          <p v-for="item in data.execution.items" :key="item.symbol">
            {{ displayCode(item.symbol) }} · {{ displayCodeText(item.message) }}
          </p>
        </div>
      </details>
      <details class="system-details strategy-details">
        <summary>
          策略说明 <span>{{ strategyName }} · 计算规则</span><Icon name="chevron" :size="15" />
        </summary>
        <section v-if="isPa" class="strategy-banner">
          <div>
            <div class="section-kicker">日 K 结构 / 盘中执行</div>
            <h2>{{ strategyName }}</h2>
            <p>依据 Pin Bar、吞没、Inside / Outside Bar、BOS / CHoCH、摆动点与支撑压力识别交易机会。</p>
            <p>已收盘日 K 形成条件，盘中突破触发；最低盈亏比与追价上限过滤入场，结构失效或到达目标退出。</p>
            <p>
              做 T：压力位卖出部分可卖底仓，回到支撑且未失效时买回；价差须覆盖费用。各 ETF
              的可卖份额、仓位上限和操作间隔继续生效。
            </p>
            <p>
              采用日 K，不生成未完成的日线或分钟 K；摆动点等待右侧 3 根 K
              线确认。无信号时持仓或持币，不按动量排名调仓。
            </p>
          </div>
        </section>
        <section v-else class="strategy-banner">
          <div>
            <div class="section-kicker">策略模型 / MOMENTUM</div>
            <h2>趋势动量轮动 <span>v1</span></h2>
            <p>手动 ETF 名单 → 交易资格与数据检查 → 趋势过滤 → 动量排名</p>
          </div>
          <div class="strategy-formula">
            <span>动量评分</span><strong>60% × R₂₀ + 40% × R₆₀</strong
            ><small>{{
              mode === 'live' ? '完整日 K + 最新价格；流动性使用完整交易日' : '仅使用已完成交易日数据'
            }}</small>
          </div>
        </section>
      </details>
      <div class="audit-note" v-if="data.input_sha256">
        <Icon name="shield" :size="17" />
        <div>
          {{ postClose ? '盘后结构输入' : '策略输入已冻结' }} · {{ dateTime(data.created_at)
          }}<small>证据指纹 {{ data.input_sha256 }}</small>
        </div>
      </div>
    </WorkbenchDialog>
  </div>
</template>

<style scoped>
.signal-watchlist {
  grid-area: lists;
  min-height: 0;
}
.signal-workbench {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: clamp(300px, 26vw, 380px) minmax(0, 1fr);
  grid-template-rows: auto minmax(0, 1fr);
  grid-template-areas: 'summary chart' 'lists chart';
  gap: 8px;
}
.workbench-chart {
  grid-area: chart;
}
.workbench-summary {
  grid-area: summary;
  display: flex;
  align-items: center;
  gap: 6px 12px;
  min-height: 28px;
  font-size: 11px;
  color: var(--muted);
  flex-wrap: wrap;
}
.workbench-summary b {
  color: var(--text-secondary);
  font-weight: 500;
}
.summary-details {
  display: flex;
  gap: 12px;
  margin-left: auto;
  white-space: nowrap;
}
.summary-details .text-button {
  font-size: 11px;
}
.summary-error {
  flex-basis: 100%;
  margin: 0;
  max-height: 32px;
  overflow: auto;
}
.post-close-note {
  flex-basis: 100%;
  font-size: 10px;
}
.signal-detail-table {
  margin-top: 16px;
}
@media (max-width: 900px) {
  .signal-workbench {
    flex: none;
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: auto auto clamp(200px, 30svh, 300px);
    grid-template-areas: 'summary' 'chart' 'lists';
    gap: 6px;
  }
}
@media (max-width: 700px) {
  .workbench-summary {
    gap: 4px 12px;
    font-size: 10px;
  }
  .summary-updated {
    display: none;
  }
  .summary-updated.amberText {
    display: inline;
  }
  .summary-details .text-button {
    font-size: 10px;
  }
}
@media (max-width: 700px) {
  .signal-workbench {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .workbench-summary {
    font-size: 11px;
  }
  .summary-details .text-button {
    font-size: 11px;
  }
  .signal-watchlist {
    height: auto;
  }
}
</style>
