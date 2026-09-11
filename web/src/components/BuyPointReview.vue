<script setup>
import { computed, ref } from 'vue'
import { money } from '../state'
import WorkbenchDialog from './WorkbenchDialog.vue'

const props = defineProps({ review: Object, day: String, visible: Boolean })
const emit = defineEmits(['select', 'toggle', 'recent'])
const open = ref(false)
const activeDay = ref(null)
const markers = computed(() => props.review?.markers || [])
const focused = computed(() => markers.value.find((m) => m.day === (activeDay.value || props.day)))
function show(day) {
  activeDay.value = day || null
  open.value = true
}
function select(day) {
  activeDay.value = day
  emit('select', day)
  open.value = false
}
defineExpose({ show })
</script>
<template>
  <section v-if="review" class="buy-review" aria-label="十日买点复盘">
    <details>
      <summary>
        买点复盘 <span>{{ review.ready ? `${markers.length} 个候选` : '等待数据' }}</span>
      </summary>
      <div class="review-bar">
        <b>十日买点复盘</b>
        <span>{{ review.as_of }} 盘后</span>
        <template v-if="review.ready">
          <button class="text-button" @click="emit('toggle')" :aria-pressed="visible">
            {{ visible ? '隐藏' : '显示' }}买点 · {{ markers.length }}
          </button>
          <button class="text-button" @click="emit('recent')">看近十日</button>
          <button class="text-button" @click="show(day)">买入理由与逐日核验</button>
        </template>
      </div>
      <p v-if="!review.ready || !markers.length || review.evaluated_days < 10">{{ review.reason }}</p>
    </details>
    <WorkbenchDialog v-model:open="open" title="近十个交易日 · 买点复盘">
      <p class="review-method">
        {{ review.window_start }} 至 {{ review.as_of }}，按每日前一交易日及更早的完整日 K 核验买入条件。
        图中灰色“m”表示触价候选，不代表实际成交；价格为前复权。
      </p>
      <p>{{ review.reason }}</p>
      <p v-if="review.rr_enabled === false" class="review-method">
        本次复盘已关闭最低盈亏比过滤，仍核验形态、入场价和追价上限。
      </p>
      <div v-if="focused" class="review-focused">当前查看：{{ focused.day }} · {{ focused.setup }}</div>
      <article
        v-for="marker in markers"
        :key="marker.id"
        class="review-card"
        :class="{ focused: focused?.id === marker.id }"
      >
        <div class="review-title">
          <button class="text-button" @click="select(marker.day)">{{ marker.day }} 买点 ↗</button>
          <b>{{ marker.setup }}</b>
          <span>依据截至 {{ marker.known_through }}</span>
        </div>
        <dl>
          <div>
            <dt>入场参考</dt>
            <dd>{{ money(marker.entry, 3) }}</dd>
          </div>
          <div>
            <dt>失效价</dt>
            <dd>{{ money(marker.stop, 3) }}</dd>
          </div>
          <div>
            <dt>目标价</dt>
            <dd>{{ money(marker.target, 3) }}</dd>
          </div>
          <div>
            <dt>潜在盈亏比</dt>
            <dd>{{ money(marker.reward_risk) }}</dd>
          </div>
        </dl>
        <p class="review-method">
          {{ review.rr_enabled === false ? '入场价格上限' : '计入最低盈亏比后的入场上限' }}：{{
            money(marker.entry_ceiling, 3)
          }}（交易时另按实际报价、最小价位及滑点复核）。
        </p>
        <ul>
          <li v-for="reason in marker.reasons" :key="reason">{{ reason }}</li>
        </ul>
        <p class="review-outcome">{{ marker.outcome_day || review.as_of }} · {{ marker.outcome }}</p>
      </article>
      <details>
        <summary>十个交易日逐日核验</summary>
        <p v-for="check in review.checks" :key="check.day" class="review-check">
          <b>{{ check.day }}</b> {{ check.reason }}
        </p>
      </details>
    </WorkbenchDialog>
  </section>
</template>
<style scoped>
.buy-review {
  flex-shrink: 0;
  border-top: 1px solid var(--line);
  padding: 6px 0;
  font-size: 11px;
  color: var(--muted);
}
.review-bar,
.review-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 12px;
}
.review-bar b,
.review-title b {
  color: var(--text-secondary);
  font-weight: 500;
}
.review-bar .text-button {
  font-size: 11px;
}
.buy-review details > p {
  margin: 5px 0 0;
  line-height: 1.5;
}
.review-method,
.review-check {
  font-size: 12px;
  line-height: 1.8;
  color: var(--muted);
}
.review-focused {
  color: var(--accent-text);
  margin: 12px 0;
}
.review-card {
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  padding: 14px;
  margin: 12px 0;
}
.review-card.focused {
  border-color: var(--accent-border);
  background: var(--accent-soft);
}
.review-title span {
  color: var(--muted);
  font-size: 11px;
}
.review-card dl {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  font-size: 12px;
}
.review-card dt {
  color: var(--muted);
}
.review-card dd {
  margin: 5px 0 0;
  font-family: var(--font-numeric);
}
.review-card ul {
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.8;
}
.review-outcome {
  font-size: 11px;
  color: var(--muted);
}
summary {
  cursor: pointer;
}
.buy-review > details > summary {
  padding: 4px 0;
  width: fit-content;
}
.buy-review > details > summary span {
  margin-left: 8px;
  font-size: 10px;
}
.buy-review > details[open] > summary {
  margin-bottom: 8px;
}
@media (max-width: 700px) {
  .buy-review > details > summary {
    min-height: 44px;
    align-content: center;
    font-size: 12px;
  }
}
@media (max-width: 480px) {
  .review-card dl {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
