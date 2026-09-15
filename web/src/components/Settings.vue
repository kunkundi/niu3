<script setup>
import { displayCodeText } from '../display-code.js'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, state, notify, dateTime, refresh } from '../state'
import Icon from './Icon.vue'
import Empty from './Empty.vue'
import NotificationSettings from './NotificationSettings.vue'
import HistoryProgress from './HistoryProgress.vue'
import AdminSecurity from './AdminSecurity.vue'

const route = useRoute(),
  router = useRouter()
const config = ref(null),
  form = ref({}),
  baseline = ref({})
const runs = ref({ items: [], sources: [] }),
  runPage = ref(1),
  loadingRuns = ref(false)
const saving = ref(''),
  error = ref(''),
  saveErrors = ref({}),
  runError = ref(''),
  notificationsVisited = ref(false)
const tabButtons = {}
const tabs = [
  { key: 'strategy', label: '策略与风控', hash: '#strategy-parameters' },
  { key: 'notifications', label: '交易通知', hash: '#trade-notifications' },
  { key: 'security', label: '管理密钥', hash: '#admin-security' },
  { key: 'runtime', label: '数据与运行', hash: '#system-runtime' },
]
const active = computed(() => tabs.find((tab) => tab.hash === route.hash) || tabs[0])
const percentFields = new Set([
  'max_weight',
  'max_exposure',
  'stop_loss',
  'trailing_stop',
  'commission_rate',
  'participation',
  'coverage_required',
  'intraday_drift',
  'intraday_t_trigger',
  'intraday_t_fraction',
])
const groups = [
  {
    title: '盘中自动执行',
    description:
      '日线管理底仓；裸 K 策略可用 5 分钟 K 确认压力转弱卖出、支撑企稳买回。新买份额按各 ETF 的 T+0／T+1 交易属性计算可卖日。',
    keys: [
      'strategy_model',
      'execution_mode',
      'pa_rr_enabled',
      'pa_min_rr',
      'intraday_confirmations',
      'intraday_min_interval',
      'intraday_drift',
      'intraday_max_orders',
      'intraday_order_ttl',
      'intraday_t_enabled',
      'intraday_t_model',
      'intraday_t_trigger',
      'intraday_t_fraction',
      'intraday_t_cycles',
    ],
  },
  {
    title: '账户与仓位',
    description: '设置资金规模、持仓数量与仓位上限。',
    keys: ['initial_cash', 'max_positions', 'max_weight', 'max_exposure'],
  },
  {
    title: '信号与保护',
    description: '设置历史数据门槛与退出条件。组合回撤仅作统计，不触发保护或拦截交易。',
    keys: ['minimum_bars', 'retain_rank', 'stop_loss', 'trailing_stop'],
  },
  {
    title: '成交费用与撮合',
    description: '按卖一价买入、买一价卖出，不额外叠加滑点。印花税和单列过户费为 0。',
    advanced: true,
    keys: ['commission_rate', 'minimum_commission', 'participation'],
  },
  {
    title: '数据与调度',
    description:
      '分时大图与缩略图共用获取间隔；休市停止轮询，补齐一次分时快照后固定展示，开市后自动恢复。陈旧或不完整的数据不会触发买入。',
    keys: ['intraday_interval', 'quote_max_age', 'coverage_required', 'market_interval', 'holding_interval'],
  },
]
const sectionKeys = {
  strategy: groups.slice(0, -1).flatMap((group) => group.keys),
  runtime: groups.at(-1).keys,
}
const saveLabels = { strategy: '保存策略', runtime: '保存调度' }
const parameterGroups = computed(() =>
  (active.value.key === 'runtime' ? [groups.at(-1)] : groups.slice(0, -1)).map((group) => ({
    ...group,
    keys: group.keys.filter((key) =>
      form.value.strategy_model === 'price_action'
        ? !['retain_rank', 'stop_loss', 'trailing_stop', 'intraday_drift', 'intraday_t_trigger'].includes(key)
        : !['pa_rr_enabled', 'pa_min_rr', 'intraday_t_model'].includes(key),
    ),
  })),
)
watch(
  () => form.value.strategy_model,
  (model) => {
    if (model === 'price_action') form.value.execution_mode = 'intraday'
  },
)
const runPages = computed(() => Math.max(1, Math.ceil(runs.value.items.length / 10)))
const visibleRuns = computed(() => runs.value.items.slice((runPage.value - 1) * 10, runPage.value * 10))
function dirty(key) {
  return (
    !!config.value &&
    (sectionKeys[key] || []).some(
      (field) => JSON.stringify(form.value[field]) !== JSON.stringify(baseline.value[field]),
    )
  )
}
function normalize(values) {
  return Object.fromEntries(
    Object.entries(values).map(([key, value]) => [
      key,
      Array.isArray(value)
        ? [...value]
        : ['execution_mode', 'strategy_model', 'intraday_t_model'].includes(key) || typeof value === 'boolean'
          ? value
          : percentFields.has(key)
            ? Number(value) * 100
            : Number(value),
    ]),
  )
}
async function loadConfig() {
  try {
    const result = await api('/config')
    config.value = result
    form.value = normalize(result.values)
    baseline.value = normalize(result.values)
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
}
async function loadRuns() {
  loadingRuns.value = true
  try {
    runs.value = await api('/runs')
    runPage.value = 1
    runError.value = ''
  } catch (e) {
    runError.value = e.message
  } finally {
    loadingRuns.value = false
  }
}
onMounted(loadConfig)
watch(
  () => active.value.key,
  (key) => {
    if (key === 'notifications') notificationsVisited.value = true
    if (key === 'runtime') loadRuns()
  },
  { immediate: true },
)
function selectTab(tab) {
  router.replace({ hash: tab.hash })
}
async function tabKey(event, index) {
  const next =
    event.key === 'ArrowRight'
      ? (index + 1) % tabs.length
      : event.key === 'ArrowLeft'
        ? (index + tabs.length - 1) % tabs.length
        : event.key === 'Home'
          ? 0
          : event.key === 'End'
            ? tabs.length - 1
            : null
  if (next == null) return
  event.preventDefault()
  await router.replace({ hash: tabs[next].hash })
  await nextTick()
  tabButtons[tabs[next].key]?.focus()
}
async function save() {
  const key = active.value.key
  if (saving.value || !dirty(key)) return
  saving.value = key
  saveErrors.value[key] = ''
  // Submit only changed fields in this category, never drafts hidden in another tab.
  const changed = sectionKeys[key].filter(
    (field) => JSON.stringify(form.value[field]) !== JSON.stringify(baseline.value[field]),
  )
  const values = Object.fromEntries(
    changed.map((field) => [
      field,
      Array.isArray(form.value[field]) ? [...form.value[field]] : form.value[field],
    ]),
  )
  const payload = Object.fromEntries(
    changed.map((field) => [
      field,
      percentFields.has(field) ? String(Number(values[field]) / 100) : values[field],
    ]),
  )
  try {
    const result = await api('/config', { method: 'PATCH', body: JSON.stringify(payload) })
    // A successful save remains acknowledged even if a later status request fails.
    for (const field of changed) baseline.value[field] = values[field]
    config.value.version = result.version
    notify(`${active.value.key === key ? '当前设置' : tabs.find((tab) => tab.key === key).label}已保存`)
    await refresh()
  } catch (e) {
    saveErrors.value[key] = e.message
  } finally {
    saving.value = ''
  }
}
function showInvalid(event) {
  const details = event.target.closest('details')
  if (details) details.open = true
}
</script>

<template>
  <div class="settings-tabs" role="tablist" aria-label="设置分类">
    <button
      v-for="(tab, index) in tabs"
      :key="tab.key"
      :ref="
        (el) => {
          tabButtons[tab.key] = el
        }
      "
      type="button"
      role="tab"
      :id="`settings-tab-${tab.key}`"
      :aria-selected="active.key === tab.key"
      :aria-controls="`settings-pane-${tab.key}`"
      :tabindex="active.key === tab.key ? 0 : -1"
      :class="{ selected: active.key === tab.key }"
      @click="selectTab(tab)"
      @keydown="tabKey($event, index)"
    >
      {{ tab.label
      }}<i v-if="dirty(tab.key)" class="draft-dot" title="有未保存修改" aria-label="有未保存修改"></i>
    </button>
  </div>

  <div
    v-if="sectionKeys[active.key]"
    :id="`settings-pane-${active.key}`"
    role="tabpanel"
    :aria-labelledby="`settings-tab-${active.key}`"
  >
    <template v-if="active.key === 'runtime'">
      <div class="runtime-strip">
        <div>
          <strong>Worker</strong><span>{{ dateTime(state.status.worker_at) }}</span>
        </div>
        <div>
          <Icon name="clock" :size="16" /><span>日历至 {{ state.status.calendar_end || '—' }}</span>
        </div>
        <div>
          <Icon name="shield" :size="16" /><span>{{
            runs.reconciliation?.errors?.length
              ? '账本核对异常'
              : runs.reconciliation?.at
                ? '账本核对通过'
                : '等待核对'
          }}</span>
        </div>
      </div>
      <div class="notice" v-if="state.status.reasons?.length">
        <Icon name="warning" :size="18" />
        <div>
          <strong>就绪检查</strong>
          <p>{{ displayCodeText(state.status.reasons.join(' · ')) }}</p>
        </div>
      </div>
      <HistoryProgress />
    </template>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <p v-if="saveErrors[active.key]" class="form-error" role="alert">{{ saveErrors[active.key] }}</p>
    <form
      v-if="config"
      @submit.prevent="save"
      @invalid.capture="showInvalid"
      @input="saveErrors[active.key] = ''"
    >
      <fieldset class="settings-form-body" :disabled="!!saving">
        <component
          :is="group.advanced ? 'details' : 'section'"
          v-for="group in parameterGroups"
          :key="group.title"
          class="panel parameter-group"
          :class="{ advanced: group.advanced }"
        >
          <summary v-if="group.advanced">{{ group.title }}<Icon name="chevron" :size="15" /></summary>
          <div class="parameter-body">
            <div class="settings-description">
              <h2 v-if="!group.advanced">{{ group.title }}</h2>
              <p>{{ group.description }}</p>
            </div>
            <div class="settings-fields">
              <label v-for="key in group.keys" :key="key"
                ><span>{{ config.labels[key] }}{{ percentFields.has(key) ? '（%）' : '' }}</span>
                <select v-if="key === 'execution_mode'" v-model="form[key]">
                  <option value="intraday">盘中自动交易</option>
                  <option value="daily" :disabled="form.strategy_model === 'price_action'">日频调仓</option>
                </select>
                <select v-else-if="key === 'strategy_model'" v-model="form[key]">
                  <option value="price_action">裸 K 价格行为 · 日 K 结构 / 盘中触发</option>
                  <option value="momentum">趋势动量轮动</option>
                </select>
                <select v-else-if="key === 'intraday_t_enabled'" v-model="form[key]">
                  <option :value="true">启用：底仓卖出后回落买回</option>
                  <option :value="false">关闭做 T，保留盘中调仓</option>
                </select>
                <select
                  v-else-if="key === 'intraday_t_model'"
                  v-model="form[key]"
                  :disabled="!form.intraday_t_enabled"
                >
                  <option value="minute5">5 分钟 K · 压力转弱 / 支撑企稳</option>
                  <option value="daily">日 K · 触及支撑压力</option>
                </select>
                <select v-else-if="key === 'pa_rr_enabled'" v-model="form[key]">
                  <option :value="true">开启：检查最低盈亏比</option>
                  <option :value="false">关闭：不限制最低盈亏比</option>
                </select>
                <input
                  v-else
                  type="number"
                  v-model="form[key]"
                  :min="key === 'intraday_interval' ? 5 : key === 'pa_min_rr' ? 1 : undefined"
                  :max="key === 'intraday_interval' ? 60 : key === 'pa_min_rr' ? 5 : undefined"
                  :step="
                    percentFields.has(key)
                      ? '0.01'
                      : ['minimum_commission', 'pa_min_rr'].includes(key)
                        ? '0.1'
                        : '1'
                  "
                  :disabled="
                    (key === 'initial_cash' && config.initial_cash_locked) ||
                    (key === 'pa_min_rr' && !form.pa_rr_enabled)
                  "
                  required
                />
                <small v-if="key === 'initial_cash' && config.initial_cash_locked"
                  >已有订单，初始资金已锁定</small
                >
                <small v-if="key === 'intraday_interval'"
                  >5～60 秒，默认 10 秒；保存后生效，不影响交易策略和订单。休市优先读取当天分时缓存。</small
                >
                <small v-if="key === 'pa_rr_enabled'"
                  >用于裸 K 普通建仓及买点复盘。关闭后仍保留形态、入场价、追价上限和止损条件。</small
                >
                <small v-if="key === 'intraday_t_model'"
                  >仅用已完成的真实 5 分钟 K；当日至少 7
                  根。每轮冻结支撑，失效后结束买回等待，成交价差须覆盖费用。</small
                >
                <small v-if="key === 'pa_min_rr'">{{
                  form.pa_rr_enabled
                    ? '范围 1～5，默认 1.5；潜在收益与风险之比低于此值时不买入。'
                    : '过滤已关闭；保留此数值，重新开启后生效。'
                }}</small>
              </label>
            </div>
          </div>
        </component>
      </fieldset>
      <div class="settings-save">
        <span>配置 v{{ config.version }} · {{ dirty(active.key) ? '有未保存修改' : '已保存' }}</span>
        <button class="button primary compact" :disabled="!!saving || !dirty(active.key)">
          <Icon name="check" :size="16" />{{ saving === active.key ? '保存中…' : saveLabels[active.key] }}
        </button>
      </div>
    </form>
    <div v-else class="panel loading">
      <template v-if="error"
        ><button class="button secondary" @click="loadConfig">重新加载设置</button></template
      ><template v-else>正在加载设置…</template>
    </div>

    <section v-if="active.key === 'runtime'" class="panel runs-panel" aria-labelledby="runs-title">
      <div class="panel-heading">
        <div>
          <h2 id="runs-title">运行记录</h2>
          <p>最近 {{ runs.items.length }} 条 · 每页 10 条</p>
        </div>
        <button class="button secondary compact" @click="loadRuns" :disabled="loadingRuns">
          <Icon name="refresh" :size="16" />{{ loadingRuns ? '刷新中…' : '刷新' }}
        </button>
      </div>
      <p v-if="runError" class="form-error padded" role="alert">{{ runError }}</p>
      <div class="table-scroll" v-if="runs.items.length">
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>任务</th>
              <th>状态</th>
              <th>详情</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="run in visibleRuns" :key="run.id">
              <td>{{ dateTime(run.at) }}</td>
              <td class="task-cell">{{ displayCodeText(run.task) }}</td>
              <td>
                <span class="chip" :class="run.status === 'error' ? 'amber' : 'neutral'">{{
                  run.status === 'error' ? '异常' : '完成'
                }}</span>
              </td>
              <td class="reason-cell">{{ displayCodeText(run.detail) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <Empty
        v-else
        :title="loadingRuns ? '正在读取记录…' : '暂无运行记录'"
        description="采集、策略与配置更新记录会显示在这里。"
      />
      <div v-if="runPages > 1" class="runs-pagination">
        <span>{{ runPage }} / {{ runPages }}</span
        ><button type="button" class="button secondary compact" :disabled="runPage === 1" @click="runPage--">
          上一页</button
        ><button
          type="button"
          class="button secondary compact"
          :disabled="runPage === runPages"
          @click="runPage++"
        >
          下一页
        </button>
      </div>
    </section>
  </div>
  <div
    v-if="notificationsVisited"
    v-show="active.key === 'notifications'"
    id="settings-pane-notifications"
    role="tabpanel"
    aria-labelledby="settings-tab-notifications"
  >
    <NotificationSettings :active="active.key === 'notifications'" />
  </div>
  <div
    v-if="active.key === 'security'"
    id="settings-pane-security"
    role="tabpanel"
    aria-labelledby="settings-tab-security"
  >
    <AdminSecurity />
  </div>
</template>

<style scoped>
.settings-tabs {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 4px;
  margin-bottom: 18px;
  border-bottom: 1px solid var(--line);
}
.settings-tabs button {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-width: 0;
  padding: 12px 6px;
  border: 0;
  border-bottom: 2px solid transparent;
  color: var(--muted);
  background: transparent;
  font-size: 12px;
  cursor: pointer;
}
.settings-tabs button:hover {
  color: var(--ink);
  background: var(--panel);
}
.settings-tabs button.selected {
  color: var(--accent);
  background: transparent;
  border-bottom-color: var(--accent);
}
.settings-tabs button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
.draft-dot {
  width: 5px;
  height: 5px;
  flex: 0 0 5px;
  border-radius: 50%;
  background: var(--yellow);
}
.settings-form-body,
.scope-fieldset {
  min-width: 0;
  margin: 0;
  border: 0;
  padding: 0;
}
.scope-body {
  padding: 14px 20px 18px;
}
.scope-fieldset {
  margin-bottom: 20px;
}
.scope-fieldset legend {
  margin-bottom: 10px;
  font-size: 12px;
  font-weight: 600;
}
.scope-fieldset legend span {
  margin-left: 10px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 400;
}
.scope-choice-row {
  display: flex;
  align-items: flex-start;
  gap: 16px;
}
.scope-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  flex: 1;
  min-width: 0;
}
.scope-option {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 12px;
}
.scope-option.selected {
  border-color: var(--accent-border);
  background: var(--accent-soft);
}
.scope-option:focus-within {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.scope-option input {
  width: 14px;
  height: 14px;
  margin: 0;
  accent-color: var(--accent);
}
.scope-actions {
  display: flex;
  gap: 10px;
  padding-top: 7px;
  flex-shrink: 0;
}
.scope-actions button {
  border: 0;
  background: transparent;
  padding: 4px 0;
  cursor: pointer;
  font-size: 11px;
}
.scope-filters {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  padding-top: 18px;
  border-top: 1px solid var(--line);
}
.scope-filters label {
  display: grid;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
}
.scope-filters input {
  width: 100%;
  min-width: 0;
}
.scope-filters small,
.scope-note {
  color: var(--muted);
  font-size: 11px;
  line-height: 1.7;
}
.scope-note {
  margin-top: 14px;
}
.scope-body > .form-error {
  margin-top: 14px;
}
.settings-help {
  margin-top: 14px;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.8;
}
.settings-help summary,
.parameter-group > summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  cursor: pointer;
  list-style: none;
}
summary::-webkit-details-marker {
  display: none;
}
details[open] > summary svg {
  transform: rotate(90deg);
}
.settings-help p {
  margin-top: 10px;
}
.settings-help dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 5px 12px;
  margin: 12px 0 0;
}
.settings-help dd {
  margin: 0;
}
.parameter-group {
  margin-bottom: 12px;
}
.parameter-body {
  display: grid;
  grid-template-columns: 170px minmax(0, 1fr);
  gap: 20px;
  padding: 14px 16px;
}
.parameter-group > summary {
  padding: 14px 20px;
  font-size: 12px;
  font-weight: 600;
}
.parameter-group.advanced .parameter-body {
  border-top: 1px solid var(--line);
}
.settings-save {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 16px 0 20px;
}
.settings-save > span {
  color: var(--muted);
  font-size: 11px;
}
.runs-pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--line);
}
.runs-pagination > span {
  margin-right: auto;
  color: var(--muted);
  font-size: 11px;
}
@media (max-width: 700px) {
  .settings-tabs {
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 3px;
    margin-bottom: 14px;
  }
  .settings-tabs button {
    padding: 10px 3px;
    font-size: 11px;
  }
  .scope-body {
    padding: 14px 14px 16px;
  }
  .scope-choice-row {
    flex-direction: column;
    gap: 6px;
  }
  .scope-actions {
    padding-top: 2px;
  }
  .scope-options {
    gap: 6px;
  }
  .scope-option {
    padding: 7px 8px;
    font-size: 11px;
  }
  .scope-filters {
    grid-template-columns: 1fr;
    gap: 16px;
  }
  .parameter-body {
    grid-template-columns: 1fr;
    gap: 14px;
    padding: 16px 14px;
  }
  .settings-description p {
    max-width: none;
    margin-top: 4px;
  }
  .settings-help dl {
    grid-template-columns: 1fr;
    gap: 2px;
  }
  .settings-help dt {
    margin-top: 6px;
    color: var(--text-secondary);
  }
}
@media (max-width: 700px) {
  .settings-tabs {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 4px;
  }
  .settings-tabs button {
    min-height: 44px;
    padding: 8px;
    font-size: 12px;
  }
  .settings-save {
    position: sticky;
    bottom: calc(var(--mobile-nav-height) + env(safe-area-inset-bottom));
    padding: 10px;
    background: var(--panel);
    border-top: 1px solid var(--line);
    z-index: 5;
  }
  .settings-save > span {
    font-size: 11px;
  }
}
</style>
