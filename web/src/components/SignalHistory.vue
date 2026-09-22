<script setup>
import { displayCode } from '../display-code.js'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, dateTime, money, pct } from '../state'
import WorkbenchDialog from './WorkbenchDialog.vue'

const props = defineProps({ open: Boolean })
const emit = defineEmits(['update:open'])
const records = ref([]),
  nextBefore = ref(null),
  recordId = ref(''),
  detail = ref(null)
const loading = ref(false),
  detailLoading = ref(false),
  error = ref(''),
  detailError = ref('')
const filter = ref('all'),
  page = ref(0)
const rows = computed(() =>
  (detail.value?.rows || []).filter((row) =>
    filter.value === 'selected'
      ? row.selected
      : filter.value === 'candidates'
        ? row.representative && !row.selected
        : row.representative || row.selected,
  ),
)
const visible = computed(() => rows.value.slice(page.value * 30, (page.value + 1) * 30))
const exposure = computed(() =>
  Object.values(detail.value?.targets || {}).reduce((total, value) => total + Number(value), 0),
)
const isPa = computed(() => detail.value?.strategy?.startsWith('price-action-'))
let listRequest = 0,
  detailRequest = 0
async function selectRecord(id) {
  recordId.value = String(id)
  detail.value = null
  detailError.value = ''
  detailLoading.value = true
  page.value = 0
  const token = ++detailRequest
  try {
    const result = await api(`/signals/history/${id}`)
    if (props.open && token === detailRequest) detail.value = result
  } catch (e) {
    if (props.open && token === detailRequest) detailError.value = e.message
  } finally {
    if (token === detailRequest) detailLoading.value = false
  }
}
async function loadRecords(reset = false) {
  const token = ++listRequest
  loading.value = true
  error.value = ''
  if (reset) {
    records.value = []
    nextBefore.value = null
    recordId.value = ''
    detail.value = null
    detailRequest++
    detailLoading.value = false
    detailError.value = ''
    filter.value = 'all'
  }
  try {
    const result = await api(
      `/signals/history${!reset && nextBefore.value ? `?before_id=${nextBefore.value}` : ''}`,
    )
    if (!props.open || token !== listRequest) return
    records.value = reset ? result.items : [...records.value, ...result.items]
    nextBefore.value = result.next_before_id
    if (!recordId.value && records.value.length) await selectRecord(records.value[0].id)
  } catch (e) {
    if (props.open && token === listRequest) error.value = e.message
  } finally {
    if (token === listRequest) loading.value = false
  }
}
watch(
  () => props.open,
  (open) => {
    if (open) loadRecords(true)
    else {
      listRequest++
      detailRequest++
      loading.value = false
      detailLoading.value = false
    }
  },
  { immediate: true },
)
onBeforeUnmount(() => {
  listRequest++
  detailRequest++
})
watch(filter, () => {
  page.value = 0
})
</script>

<template>
  <WorkbenchDialog :open="open" title="历史与复盘" @update:open="emit('update:open', $event)">
    <p class="history-note">
      收盘记录保存当时的日 K
      结构、目标与筛选原因。这里展示当时的数据，当前交易信号仍会自动更新；实际成交请查看「投资总览 →
      成交明细」。
    </p>
    <div class="history-controls">
      <label for="signal-history-record">收盘记录</label>
      <select
        id="signal-history-record"
        :value="recordId"
        :disabled="!records.length"
        @change="selectRecord(Number($event.target.value))"
      >
        <option v-if="!records.length" value="">{{ loading ? '正在读取…' : '暂无记录' }}</option>
        <option v-for="record in records" :key="record.id" :value="String(record.id)">
          {{ record.as_of }} · 参数 v{{ record.config_id }} · 入选 {{ record.selected_count }} 只
        </option>
      </select>
      <button v-if="nextBefore" class="button secondary compact" :disabled="loading" @click="loadRecords()">
        加载更早记录
      </button>
      <button class="text-button" :disabled="loading" @click="loadRecords(true)">刷新记录</button>
    </div>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <p v-if="detailError" class="form-error" role="alert">
      {{ detailError }} <button class="text-button" @click="selectRecord(Number(recordId))">重试</button>
    </p>
    <p v-if="loading || detailLoading" class="history-note" role="status">正在读取收盘记录…</p>
    <p v-else-if="!records.length && !error" class="history-note">
      暂无收盘记录。完整日 K 同步后，后台会自动保存。
    </p>
    <section v-if="detail" aria-label="收盘记录详情">
      <div class="history-meta">
        <strong>{{ detail.as_of }} · 收盘快照</strong>
        <span>入选 {{ Object.keys(detail.targets || {}).length }} 只 · 目标仓位 {{ pct(exposure) }}</span>
        <span
          >{{ isPa ? '裸 K 价格行为' : '历史策略（已停用）' }} · {{ detail.strategy || '—' }} · 参数 v{{
            detail.config_id
          }}</span
        >
        <span>保存时间 {{ dateTime(detail.created_at) }}</span>
      </div>
      <div class="tabs history-filters" role="group" aria-label="复盘记录筛选">
        <button
          v-for="tab in [
            { value: 'all', label: '候选与入选' },
            { value: 'selected', label: '当时入选' },
            { value: 'candidates', label: '当时未入选' },
          ]"
          :key="tab.value"
          :class="{ selected: filter === tab.value }"
          :aria-pressed="filter === tab.value"
          @click="filter = tab.value"
        >
          {{ tab.label }}
        </button>
      </div>
      <p class="history-note" v-if="isPa">下方结构价位使用当时保存的前复权口径。</p>
      <div
        v-if="visible.length"
        class="table-scroll history-table"
        tabindex="0"
        role="region"
        aria-label="历史筛选结果，可横向滚动"
      >
        <table>
          <thead>
            <tr>
              <th>ETF</th>
              <th>当时状态</th>
              <th class="number-cell">目标仓位</th>
              <th class="number-cell">入场 / 失效</th>
              <th v-if="isPa" class="number-cell">支撑 / 压力</th>
              <th>当时的筛选依据</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in visible" :key="row.symbol">
              <td>
                <strong>{{ displayCode(row.name) }}</strong
                ><small>{{ displayCode(row.symbol) }}</small>
              </td>
              <td>{{ row.selected ? '入选' : '未入选' }}</td>
              <td class="number-cell">{{ pct(row.target_weight) }}</td>
              <td class="number-cell" v-if="isPa">
                {{ money(row.pa?.entry, 3) }}<small>{{ money(row.pa?.entry_stop, 3) }}</small>
              </td>
              <td class="number-cell" v-else>—</td>
              <td class="number-cell" v-if="isPa">
                {{ money(row.pa?.support, 3) }}<small>{{ money(row.pa?.resistance, 3) }}</small>
              </td>
              <td class="history-reason">
                {{ row.reasons?.join('；') || '未记录筛选原因'
                }}<small v-if="row.pa?.ready">{{ row.pa.trend }} · {{ row.pa.setup || '等待形态' }}</small>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="history-note">这份记录中没有符合所选条件的标的。</p>
      <div class="pagination">
        <span>共 {{ rows.length }} 条</span>
        <div>
          <button :disabled="page === 0" @click="page--">上一页</button><span>{{ page + 1 }}</span
          ><button :disabled="(page + 1) * 30 >= rows.length" @click="page++">下一页</button>
        </div>
      </div>
      <details v-if="detail.input_sha256" class="history-evidence">
        <summary>记录依据</summary>
        <p>记录 #{{ detail.id }} · 输入指纹 {{ detail.input_sha256 }}</p>
      </details>
    </section>
  </WorkbenchDialog>
</template>

<style scoped>
.history-note {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
  margin: 0 0 12px;
}
.history-controls {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
  font-size: 12px;
}
.history-controls select {
  flex: 1;
  min-width: 0;
  max-width: 440px;
}
.history-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  font-size: 12px;
  margin-bottom: 14px;
}
.history-meta span {
  color: var(--muted);
}
.history-filters {
  margin-bottom: 10px;
}
.history-filters button {
  font-size: 11px;
}
.history-table {
  max-height: 45dvh;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-card);
}
.history-table table {
  min-width: 720px;
}
.history-table small {
  display: block;
  color: var(--muted);
  margin-top: 4px;
}
.history-reason {
  min-width: 250px;
  white-space: normal;
  line-height: 1.6;
}
.history-evidence {
  font-size: 11px;
  color: var(--muted);
  overflow-wrap: anywhere;
}
@media (max-width: 600px) {
  .history-controls label {
    flex-basis: 100%;
  }
  .history-controls select {
    flex-basis: 100%;
  }
}
</style>
