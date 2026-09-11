<script setup>
import { displayCode, displayCodeText } from '../display-code.js'
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { api, state, money, pct, dateTime } from '../state'
import Icon from './Icon.vue'
import LineChart from './LineChart.vue'
import Empty from './Empty.vue'
import HistoryProgress from './HistoryProgress.vue'
import AccountMetrics from './AccountMetrics.vue'
import AccountRecords from './AccountRecords.vue'
import AccountActions from './AccountActions.vue'
import RecentTrades from './RecentTrades.vue'
import { overviewSignal } from '../overview-signal.js'
import { holdingWarning } from '../holding-warning.js'
const account = computed(() => state.account)
const records = ref(null)
async function showAllTrades() {
  records.value?.showTrades()
  await nextTick()
  const section = document.getElementById('account-records')
  section?.focus({ preventScroll: true })
  section?.scrollIntoView({ block: 'start' })
}
const status = computed(() => state.status)
const holdingWarnings = computed(() =>
  (account.value?.positions || []).flatMap((p) => {
    const warning = holdingWarning(p, status.value)
    return warning ? [`${p.name}：${warning}`] : []
  }),
)
const plan = ref(null),
  planError = ref(''),
  detailsOpen = ref(false)
const signal = computed(() => overviewSignal(plan.value))
const equityRange = ref(0)
const equityPoints = computed(() => {
  const points = account.value?.equity || []
  return equityRange.value ? points.slice(-equityRange.value) : points
})
let timer,
  active = true,
  loading = false
async function loadPlan() {
  if (loading || document.hidden) return
  loading = true
  try {
    const result = await api('/signals')
    if (active) {
      plan.value = result
      planError.value = ''
    }
  } catch {
    if (active) planError.value = '计划更新失败，请前往策略信号查看'
  } finally {
    loading = false
  }
}
onMounted(() => {
  loadPlan()
  timer = setInterval(loadPlan, 30000)
  document.addEventListener('visibilitychange', loadPlan)
})
onUnmounted(() => {
  active = false
  clearInterval(timer)
  document.removeEventListener('visibilitychange', loadPlan)
})
</script>
<template>
  <div class="overview" v-if="account">
    <RecentTrades :positions="account.positions" @view-all="showAllTrades" />
    <AccountMetrics :account="account" />
    <details
      id="account-details"
      class="overview-risk"
      :open="detailsOpen"
      @toggle="detailsOpen = $event.currentTarget.open"
    >
      <summary>
        账户详情
        <span
          >当前回撤 <b>{{ pct(account.drawdown) }}</b></span
        ><span v-if="holdingWarnings.length" class="amberText" :title="holdingWarnings.join('；')"
          >持仓数据待确认</span
        ><Icon name="chevron" :size="14" />
      </summary>
      <div class="risk-strip">
        <span
          >当前回撤 <b>{{ pct(account.drawdown) }}</b></span
        >
        <span
          >累计费用 <b>{{ money(account.fees) }} 元</b></span
        >
        <span
          >可卖标的 / 当前持仓
          <b
            >{{ account.positions.filter((p) => p.available > 0).length }} /
            {{ account.positions.length }} 只</b
          ></span
        >
        <span
          >已收 / 应收分红 <b>{{ money(account.dividends) }} / {{ money(account.receivable) }} 元</b></span
        >
        <RouterLink to="/settings#strategy-parameters" class="text-link"
          >风险参数<Icon name="arrow" :size="14"
        /></RouterLink>
      </div>
      <AccountActions v-if="detailsOpen" />
    </details>
    <div class="notice" v-if="status.reasons?.length">
      <Icon name="clock" :size="18" />
      <div>
        <strong>自动买入等待数据就绪</strong>
        <p>{{ displayCodeText(status.reasons.join(' · ')) }}</p>
      </div>
      <RouterLink to="/settings#system-runtime" class="text-link"
        >查看状态<Icon name="arrow" :size="16"
      /></RouterLink>
    </div>
    <div class="overview-workspace">
      <section class="panel equity-panel">
        <div class="panel-heading">
          <div>
            <h2>资产走势</h2>
            <span class="equity-caption">{{ equityPoints.length }} 个记录日 · 含费用与分红</span>
          </div>
          <div class="equity-ranges" role="group" aria-label="资产走势记录范围">
            <button
              v-for="range in [
                { value: 20, label: '20 日' },
                { value: 60, label: '60 日' },
                { value: 0, label: '全部' },
              ]"
              :key="range.value"
              :aria-pressed="equityRange === range.value"
              :class="{ selected: equityRange === range.value }"
              @click="equityRange = range.value"
              :title="range.value ? `最近 ${range.value} 个记录日` : '全部历史记录'"
            >
              {{ range.label }}
            </button>
          </div>
        </div>
        <LineChart :points="equityPoints" />
        <Empty
          v-if="!account.equity?.length"
          title="等待首个权益快照"
          description="暂无历史净值记录。"
          icon="signals"
        />
      </section>
      <section class="panel execution-panel">
        <div class="panel-heading">
          <h2>{{ signal.title }}</h2>
          <RouterLink to="/signals" class="text-link">查看信号<Icon name="arrow" :size="14" /></RouterLink>
        </div>
        <div class="execution-body">
          <dl class="execution-facts">
            <div>
              <dt>{{ signal.dateLabel }}</dt>
              <dd>{{ signal.date }}</dd>
            </div>
            <div>
              <dt>日 K 基准</dt>
              <dd>{{ plan?.as_of || '—' }}</dd>
            </div>
            <div>
              <dt>{{ signal.countLabel }}</dt>
              <dd>{{ plan?.id ? signal.rows.length + ' 只' : '—' }}</dd>
            </div>
          </dl>
          <p v-if="planError" class="amberText" role="status">{{ planError }}</p>
          <p v-else-if="!plan?.id">{{ plan?.message || '正在读取策略信号…' }}</p>
          <template v-else>
            <div class="plan-targets" v-if="signal.rows.length">
              <RouterLink
                v-for="target in signal.rows"
                :key="target.symbol"
                :to="{ path: '/signals', query: { symbol: target.symbol } }"
              >
                <span class="target-security"
                  >{{ displayCode(target.name) }}<small>{{ displayCode(target.symbol) }}</small></span
                >
                <b :class="{ 'target-pending': signal.postClose }">{{
                  signal.postClose ? '待触发' : pct(target.target_weight)
                }}</b>
              </RouterLink>
            </div>
            <p v-else>{{ signal.postClose ? '暂无次日候选' : '暂无目标信号' }}</p>
            <p v-if="plan.stale" class="amberText">信号待更新 · 当前为上次计算结果</p>
            <div class="execution-note">
              {{
                signal.postClose
                  ? '盘后候选须在下一交易日盘中确认。'
                  : plan.execution?.message || plan.execution_message || '成交结果以账户记录为准。'
              }}
            </div>
          </template>
        </div>
      </section>
    </div>
    <AccountRecords ref="records" :account="account" :status="status" />
    <details class="system-details">
      <summary>
        数据与运行
        <span
          >{{ status.ready ? '数据就绪' : '数据准备中' }} · 日 K {{ status.history_count ?? 0 }}/{{
            status.tradable_count ?? 0
          }}</span
        >
        <Icon name="chevron" :size="15" />
      </summary>
      <div class="engine-note">
        运行心跳 {{ dateTime(status.worker_at) }}
        <RouterLink to="/settings#system-runtime" class="text-link"
          >运行设置<Icon name="arrow" :size="14"
        /></RouterLink>
      </div>
      <HistoryProgress />
    </details>
  </div>
  <div v-else class="panel loading">正在加载账户…</div>
</template>

<style scoped>
.overview-risk {
  margin-bottom: 4px;
}
.overview-risk > summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  min-height: 26px;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
}
.overview-risk > summary > svg:last-child {
  margin-left: auto;
  transition: transform 0.15s;
}
.overview-risk[open] > summary > svg:last-child {
  transform: rotate(90deg);
}
.overview-risk > summary b {
  color: var(--text-secondary);
  font-weight: 500;
  margin-left: 4px;
}
.risk-strip {
  padding: 8px 0;
  gap: 8px 20px;
  margin-bottom: 8px;
  font-size: 12px;
}
.risk-strip b {
  margin-left: 5px;
}
.overview-workspace {
  align-items: stretch;
  gap: 8px;
  margin-bottom: 8px;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
}
.overview-workspace > section {
  height: 180px;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.overview-workspace .panel-heading {
  min-height: 32px;
  padding: 0 10px;
  flex-shrink: 0;
}
.equity-panel .panel-heading > div:first-child {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px 12px;
}
.equity-panel .panel-heading p {
  margin: 0;
  font-size: 11px;
}
.equity-ranges {
  display: flex;
  gap: 3px;
  border: 0;
  padding: 0;
  border-radius: 0;
  background: transparent;
}
.equity-ranges button {
  border: 0;
  border-bottom: 1px solid transparent;
  border-radius: 0;
  background: transparent;
  color: var(--muted);
  padding: 5px 10px;
  font-size: 11px;
  white-space: nowrap;
}
.equity-ranges button:hover {
  color: var(--ink);
  background: var(--panel2);
}
.equity-ranges button.selected {
  color: var(--ink);
  border-bottom-color: var(--accent);
  background: transparent;
}
.equity-caption {
  font-size: 10px;
  color: var(--muted);
}
.equity-panel :deep(.equity-chart),
.equity-panel :deep(.empty) {
  flex: 1;
  min-height: 0;
}
.equity-panel :deep(.equity-chart) {
  padding: 6px 10px;
}
.equity-panel :deep(.equity-readout) {
  min-height: 20px;
  margin-bottom: 4px;
}
.execution-panel .execution-body {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  padding: 6px 10px;
}
.execution-panel .execution-facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 4px 12px;
  font-size: 10px;
  margin: 0;
}
.execution-panel .execution-facts > div {
  flex-direction: column;
  gap: 2px;
  padding: 0;
}
.execution-panel .execution-facts dd {
  white-space: nowrap;
  font-size: 12px;
}
.execution-panel .execution-facts,
.execution-panel .execution-note {
  flex-shrink: 0;
}
.execution-panel .plan-targets {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-content: start;
  column-gap: 16px;
  flex: 1;
  min-height: 0;
  max-height: none;
  margin: 4px 0 0;
}
.execution-panel .plan-targets a {
  padding: 3px 0;
  font-size: 12px;
  line-height: 18px;
  gap: 6px;
  min-width: 0;
}
.execution-panel .plan-targets a:hover {
  background: var(--table-hover);
}
.target-security {
  display: flex;
  gap: 2px 6px;
  align-items: baseline;
  flex-wrap: wrap;
  color: var(--text-secondary);
}
.target-security small {
  font-size: 10px;
  color: var(--muted);
}
.plan-targets b {
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 500;
}
.plan-targets .target-pending {
  padding: 0;
  border-radius: 0;
  background: transparent;
  color: var(--muted);
  font-size: 10px;
}
.execution-panel .execution-note {
  padding: 3px 0 0;
  margin-top: 3px;
  font-size: 10px;
  line-height: 1.5;
}
@media (max-width: 1100px) {
  .overview-workspace {
    grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
  }
  .execution-panel .plan-targets {
    grid-template-columns: minmax(0, 1fr);
  }
  .equity-panel .panel-heading > div:first-child {
    gap: 2px 8px;
  }
}
@media (max-width: 850px) {
  .equity-panel .panel-heading > div:first-child {
    flex-direction: column;
  }
  .overview-workspace .panel-heading {
    min-height: 42px;
  }
  .equity-ranges button {
    padding-inline: 5px;
  }
}
@media (max-width: 700px) {
  .overview {
    display: flex;
    flex-direction: column;
  }
  .overview > :deep(.account-panel) {
    order: 3;
  }
  .overview > :deep(.footnote) {
    order: 4;
  }
  .overview-workspace {
    order: 5;
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
    margin: 8px 0 0;
  }
  .overview > .system-details {
    order: 6;
  }
  .overview > .notice {
    order: 2;
  }
  .overview-risk {
    order: 1;
    margin-bottom: 0;
  }
  .overview-risk > summary {
    min-height: 44px;
    font-size: 11px;
    gap: 7px;
  }
  .overview-workspace > section {
    height: 200px;
  }
  .overview-workspace > .execution-panel {
    height: auto;
  }
  .overview-workspace .panel-heading {
    min-height: 44px;
    padding: 0 10px;
  }
  .overview-workspace .panel-heading p {
    display: none;
  }
  .equity-ranges button {
    min-width: 44px;
    min-height: 44px;
    padding: 5px 7px;
  }
  .execution-panel .plan-targets {
    flex: none;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    max-height: 264px;
    column-gap: 12px;
  }
  .execution-panel .plan-targets a {
    min-height: 44px;
    font-size: 12px;
  }
  .target-security {
    flex-direction: column;
    gap: 0;
  }
}
</style>
