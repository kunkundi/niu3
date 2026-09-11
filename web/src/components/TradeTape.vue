<script setup>
import { displayCodeText } from '../display-code.js'
import { computed, ref } from 'vue'
import { money } from '../state'
import { exchangeTime, tradeEffect } from '../trade-observation.js'
import WorkbenchDialog from './WorkbenchDialog.vue'
const props = defineProps({
  groups: { type: Array, default: () => [] },
  history: Object,
  mode: String,
  day: String,
})
const emit = defineEmits(['focus'])
const selected = ref(null),
  open = ref(false)
const recent = computed(() => [...props.groups].reverse())
function show(group) {
  selected.value = group
  open.value = true
  emit('focus', group)
}
defineExpose({ show })
</script>
<template>
  <div class="trade-tape" aria-label="模拟成交时间轴">
    <div class="tape-heading">
      <strong>模拟成交</strong>
      <span class="buy"
        >↑ 买入 {{ groups.filter((g) => g.side === 'BUY').reduce((n, g) => n + g.items.length, 0) }} 笔</span
      >
      <span class="sell"
        >↓ 卖出 {{ groups.filter((g) => g.side === 'SELL').reduce((n, g) => n + g.items.length, 0) }} 笔</span
      >
      <small>{{
        mode === 'intraday' ? `${day || ''} · 当日` : day ? `回顾截至 ${day}` : '最近成交 · 同日同类合并'
      }}</small>
    </div>
    <p v-if="history?.error" class="tape-note amberText" role="status">{{ history.error }}</p>
    <div v-if="recent.length" class="tape-events">
      <button
        v-for="group in recent"
        :key="group.id"
        :class="group.effectTone"
        @click="show(group)"
        :aria-label="`${group.day} ${group.time} ${group.label} ${group.actionLabel}模拟成交详情`"
      >
        <b
          >{{ group.label }} {{ group.actionLabel }} <span>{{ money(group.price, 3) }}</span></b
        >
        <small
          >{{ group.day.slice(5) }} {{ group.time.slice(0, 5) }} · {{ group.items.length }} 笔{{
            group.items.length > 1 ? '均价' : ''
          }}</small
        >
      </button>
    </div>
    <p v-else class="tape-note" role="status">
      {{
        history?.loading
          ? '正在加载成交…'
          : history?.error
            ? '成交暂不可用，稍后自动重试'
            : mode === 'intraday'
              ? '该交易日暂无模拟成交，成交后将在分时线上标出买卖点。'
              : day
                ? '当前回顾日期之前暂无模拟成交。'
                : '该 ETF 暂无模拟成交；灰色“m”仅表示复盘触价候选。'
      }}
    </p>
    <p v-if="history?.total > history?.items?.length" class="tape-note">
      展示最近 {{ history.items.length }} 笔，共 {{ history.total }} 笔；完整明细见投资总览。
    </p>
    <WorkbenchDialog v-model:open="open" title="模拟成交 · 买卖依据">
      <template v-if="selected">
        <p>
          <strong class="effect-label" :class="selected.effectTone"
            >{{ selected.label }} {{ selected.actionLabel }}</strong
          >
          · {{ selected.day }} · 共 {{ selected.items.length }} 笔
        </p>
        <p>
          成交{{ selected.items.length > 1 ? '均' : '' }}价 {{ money(selected.price, 3) }} 元 ·
          {{ money(selected.quantity, 0) }} 份
        </p>
        <p class="tape-note">
          {{
            mode === 'daily'
              ? '日 K 按成交日定位，K 线为前复权价；此处显示原始交易价。分时图可查看每笔成交的时间与价格位置。'
              : '图中按成交时间与交易价定位，同一分钟同类成交合并；圆点为成交价，标签通过连线指向原始位置。'
          }}
        </p>
        <div v-for="item in selected.items" :key="item.id" class="fill-detail">
          <strong
            >{{ exchangeTime(item.at)?.time }} · {{ money(item.price, 3) }} 元 ·
            {{ money(item.quantity, 0) }} 份</strong
          >
          <p>{{ displayCodeText(item.reason || '暂无记录原因') }}</p>
          <p class="tape-note">
            {{ tradeEffect(item).label }} ·
            <template v-if="item.position_before != null && item.position_after != null">
              持仓 {{ money(item.position_before, 0) }} → {{ money(item.position_after, 0) }} 份
            </template>
            <template v-else>持仓流水不足，暂未分类</template>
          </p>
          <small>订单 #{{ item.order_id }} · 成交 #{{ item.id }} · 费用 {{ money(item.fee) }} 元</small>
        </div>
      </template>
    </WorkbenchDialog>
  </div>
</template>
<style scoped>
.trade-tape {
  flex-shrink: 0;
  min-width: 0;
  border-top: 1px solid var(--line);
  padding-top: 7px;
  margin-top: 7px;
}
.tape-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 12px;
  font-size: 11px;
}
.tape-heading small {
  margin-left: auto;
  color: var(--muted);
  font-size: 10px;
}
.buy {
  color: var(--red);
}
.sell {
  color: var(--green);
}
.tape-events {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding: 6px 1px 4px;
}
.tape-events button {
  color: var(--trade-color, var(--muted));
  flex-shrink: 0;
  background: var(--panel2);
  border: 1px solid var(--line);
  text-align: left;
  padding: 6px 10px;
  border-radius: 5px;
  min-height: 44px;
}
.effect-label {
  color: var(--trade-color, var(--muted));
}
.tape-events button:hover {
  background: var(--accent-soft);
}
.tape-events b {
  display: flex;
  gap: 14px;
  font-size: 12px;
}
.tape-events b span {
  font-family: var(--font-numeric);
}
.tape-events small {
  display: block;
  color: var(--muted);
  font-size: 10px;
  margin-top: 4px;
}
.tape-note {
  color: var(--muted);
  font-size: 11px;
  margin: 6px 0;
  line-height: 1.6;
}
.fill-detail {
  border-top: 1px solid var(--line);
  padding: 12px 0;
  font-size: 13px;
}
.fill-detail p {
  line-height: 1.7;
}
.fill-detail small {
  color: var(--muted);
}
</style>
