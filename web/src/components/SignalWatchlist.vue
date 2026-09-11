<script setup>
import { displayCode } from '../display-code.js'
import { computed, nextTick, ref, watch } from 'vue'
import { tradeIntent } from '../trade-observation.js'
import { money, pct, changeClass } from '../state'
import Icon from './Icon.vue'
const props = defineProps({
  title: String,
  plan: Object,
  rows: { type: Array, default: () => [] },
  contextRow: Object,
  symbol: String,
  loading: Boolean,
  emptyMessage: String,
  compact: Boolean,
  expandable: Boolean,
  tabs: { type: Array, default: () => [] },
  activeTab: String,
})
const emit = defineEmits(['select', 'update:activeTab'])
const displayRows = computed(() => (props.contextRow ? [props.contextRow, ...props.rows] : props.rows))
async function selectRow(row, event) {
  const button = event.currentTarget
  const top = button.getBoundingClientRect().top
  emit('select', row.symbol)
  if (!props.expandable) return
  await nextTick()
  // Offset the collapsed chart's height as far as the remaining page scroll range allows.
  if (button.isConnected)
    window.scrollBy({ top: button.getBoundingClientRect().top - top, behavior: 'instant' })
}
const items = ref(null)
const scrollPositions = new Map()
watch(
  () => props.activeTab,
  async (tab, previous) => {
    scrollPositions.set(previous, items.value?.scrollTop || 0)
    await nextTick()
    if (items.value && props.activeTab === tab) items.value.scrollTop = scrollPositions.get(tab) || 0
  },
)
</script>

<template>
  <section
    class="panel watchlist"
    :class="{ 'watchlist-compact': compact, 'watchlist-expandable': expandable }"
    :aria-label="title"
  >
    <div class="panel-heading">
      <div v-if="tabs.length" class="tabs watchlist-tabs" role="group" aria-label="标的列表切换">
        <button
          v-for="tab in tabs"
          :key="tab.value"
          :class="{ selected: activeTab === tab.value }"
          :aria-pressed="activeTab === tab.value"
          @click="$emit('update:activeTab', tab.value)"
        >
          {{ tab.label }} <small>{{ tab.count }}</small>
        </button>
      </div>
      <h2 v-else>
        {{ title }} <small>{{ rows.length }} 只</small>
      </h2>
    </div>
    <div v-if="displayRows.length" class="watchlist-columns" aria-hidden="true">
      <span>{{ expandable ? '标的 · 点按展开 / 收起' : '标的 / 结构' }}</span
      ><span>最新价</span><span>涨跌幅</span><span v-if="expandable"></span>
    </div>
    <ul v-if="displayRows.length" ref="items" class="watchlist-items">
      <li v-for="row in displayRows" :key="row.symbol">
        <p v-if="row === contextRow" class="watchlist-context">
          当前查看 · {{ row.symbol === 'sh000001' ? '大盘参考' : '分类外标的' }}
        </p>
        <button
          :id="`signal-row-${row.symbol}`"
          class="watchlist-row"
          :class="{ active: symbol === row.symbol }"
          :aria-pressed="expandable ? undefined : symbol === row.symbol"
          :aria-expanded="expandable ? symbol === row.symbol : undefined"
          :aria-controls="expandable && symbol === row.symbol ? `signal-chart-${row.symbol}` : undefined"
          :aria-label="`${expandable ? (symbol === row.symbol ? '收起' : '展开') : '查看'}${displayCode(row.name)}的图表`"
          @click="selectRow(row, $event)"
        >
          <span class="watchlist-name">
            <span
              v-if="plan && row.symbol !== 'sh000001'"
              class="intent-badge"
              :class="tradeIntent(plan, row).tone"
              >{{ tradeIntent(plan, row).title }}</span
            >
            <strong :title="displayCode(row.name)">{{ displayCode(row.name) }}</strong>
            <small
              >{{ displayCode(row.symbol) }} ·
              {{ row.post_close_candidate ? '待触发 · ' : '' }}
              {{
                row.symbol === 'sh000001'
                  ? '大盘参考'
                  : row.pa?.trend || (row.selected ? '已入选' : '等待条件')
              }}</small
            >
            <small v-if="activeTab === 'holdings' && row.position">
              持仓 {{ money(row.position.quantity, 0) }} 份 · 可卖 {{ money(row.position.available, 0) }}
            </small>
          </span>
          <span class="watchlist-number"
            >{{ money(row.quote?.last, 3) }}<small v-if="row.quote?.stale">已过期</small></span
          >
          <span class="watchlist-number" :class="changeClass(row.quote?.change_pct)">{{
            pct(row.quote?.change_pct, true)
          }}</span>
          <Icon v-if="expandable" name="chevron" :size="14" class="watchlist-chevron" />
        </button>
        <div
          v-if="expandable && symbol === row.symbol"
          :id="`signal-chart-${row.symbol}`"
          class="watchlist-expanded"
          role="region"
          :aria-labelledby="`signal-row-${row.symbol}`"
        >
          <slot name="expanded" :row="row" />
        </div>
      </li>
    </ul>
    <p v-else class="watchlist-empty" role="status">
      {{ loading ? '正在同步标的…' : emptyMessage || '暂无符合条件的标的' }}
    </p>
  </section>
</template>

<style scoped>
.intent-badge {
  display: inline-block;
  font-size: 10px;
  padding: 0;
  background: transparent;
  color: var(--muted);
  margin-bottom: 4px;
}
.intent-badge.buy {
  color: var(--red);
}
.intent-badge.sell {
  color: var(--green);
}
.intent-badge.candidate {
  color: var(--yellow);
}

.watchlist {
  min-width: 0;
  overflow: hidden;
}
.panel-heading {
  gap: 12px;
}
.panel-heading h2 small,
.panel-heading > span {
  color: var(--muted);
  font-size: 11px;
  font-weight: 400;
}
.panel-heading h2 small {
  margin-left: 8px;
}
.watchlist-tabs button {
  padding: 4px 12px;
  font-size: 12px;
}
.watchlist-tabs small {
  margin-left: 5px;
  font-size: 10px;
}
.watchlist-columns,
.watchlist-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 82px 82px;
  align-items: center;
  gap: 10px;
  padding: 12px 18px;
}
.watchlist-columns {
  color: var(--muted);
  font-size: 10px;
  border-bottom: 1px solid var(--line);
}
.watchlist-columns span:not(:first-child) {
  text-align: right;
}
.watchlist-items {
  list-style: none;
  padding: 0;
  margin: 0;
  max-height: 350px;
  overflow-y: auto;
}
.watchlist-row {
  width: 100%;
  border: 0;
  border-bottom: 1px solid var(--line);
  border-radius: 0;
  background: transparent;
  text-align: left;
  color: var(--text-secondary);
}
.watchlist-row:hover,
.watchlist-row.active {
  background: var(--accent-soft);
}
.watchlist-row.active {
  box-shadow: inset 3px 0 var(--accent);
}
.watchlist-name {
  min-width: 0;
}
.watchlist-name strong {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}
.watchlist-row small {
  display: block;
  color: var(--muted);
  font-size: 10px;
  margin-top: 6px;
}
.watchlist-number {
  text-align: right;
  font: 12px var(--font-numeric);
}
.watchlist-empty {
  padding: 24px 18px;
  color: var(--muted);
  font-size: 12px;
}
@media (max-width: 600px) {
  .watchlist-columns,
  .watchlist-row {
    grid-template-columns: minmax(0, 1fr) 62px 65px;
    gap: 6px;
    padding: 12px;
  }
  .watchlist-items {
    max-height: 280px;
  }
  .panel-heading > span {
    display: none;
  }
}
.watchlist-compact {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}
.watchlist-compact .panel-heading {
  min-height: 32px;
  padding: 6px 10px;
  flex-shrink: 0;
}
.watchlist-compact .panel-heading h2 {
  font-size: 12px;
  gap: 4px;
}
.watchlist-compact .watchlist-columns,
.watchlist-compact .watchlist-row {
  grid-template-columns: minmax(0, 1fr) 64px 64px;
  gap: 8px;
}
.watchlist-compact .watchlist-columns {
  padding: 4px 10px;
  flex-shrink: 0;
}
.watchlist-compact .watchlist-items {
  flex: 1;
  min-height: 0;
  max-height: none;
  overscroll-behavior: contain;
}
.watchlist-compact .watchlist-row {
  padding: 6px 10px;
}
.watchlist-compact .watchlist-row small {
  margin-top: 2px;
}
.watchlist-compact .watchlist-empty {
  padding: 12px 10px;
  margin: 0;
}
@media (max-width: 700px) {
  .watchlist-compact .panel-heading {
    padding: 4px 10px;
  }
  .watchlist-compact .watchlist-tabs {
    flex: 1;
    display: flex;
  }
  .watchlist-compact .watchlist-tabs button {
    flex: 1;
    min-width: 0;
    min-height: var(--mobile-control-height);
    padding: 5px 6px;
    font-size: 12px;
  }
  .watchlist-compact .watchlist-columns,
  .watchlist-compact .watchlist-row {
    grid-template-columns: minmax(0, 1fr) 66px 72px;
    gap: 8px;
    padding: 9px 10px;
  }
  .watchlist-compact .watchlist-columns {
    padding-block: 8px;
  }
  .watchlist-compact .watchlist-name strong {
    font-size: 14px;
  }
  .watchlist-compact .watchlist-name small {
    white-space: normal;
    line-height: 1.5;
    margin-top: 4px;
    font-size: 10px;
  }
  .watchlist-compact .watchlist-number {
    font-size: 16px;
  }
  .watchlist-compact .watchlist-items {
    max-height: none;
    overflow: visible;
    flex: none;
  }
  .watchlist-expandable .watchlist-columns,
  .watchlist-expandable .watchlist-row {
    grid-template-columns: minmax(0, 1fr) 60px 65px 14px;
    gap: 6px;
  }
}
.watchlist-chevron {
  color: var(--muted);
  transform: rotate(90deg);
}
.watchlist-row[aria-expanded='true'] .watchlist-chevron {
  transform: rotate(-90deg);
}
.watchlist-expanded {
  min-width: 0;
  border-bottom: 1px solid var(--line);
}
.watchlist-context {
  margin: 0;
  padding: 8px 10px;
  color: var(--muted);
  font-size: 11px;
  border-bottom: 1px solid var(--line);
}
.watchlist-expanded > :deep(.selected-chart) {
  border: 0;
  border-radius: 0;
}
</style>
