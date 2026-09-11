<script setup>
import { computed } from 'vue'
import { money } from '../state'
import { tradeIntent, exchangeTime } from '../trade-observation.js'
const props = defineProps({ plan: Object, row: Object, reference: Object, history: Object, error: String })
const intent = computed(() =>
  props.error
    ? { tone: 'neutral', title: '信号待同步', message: props.error }
    : tradeIntent(props.plan, props.row),
)
const latest = computed(() => props.history?.items?.[0])
const prices = computed(() => {
  const r = props.reference,
    raw = r?.intraday_reference
  if (
    props.error ||
    props.plan?.stale ||
    props.plan?.session_snapshot ||
    props.row?.quote?.stale ||
    !r?.matched ||
    !raw?.session_day ||
    !(r.as_of < raw.session_day) ||
    raw.session_day !== props.row?.quote?.at?.slice(0, 10)
  )
    return []
  return [
    {
      id: intent.value.tone === 'sell' ? 'exit' : 'entry',
      label: intent.value.tone === 'sell' ? '卖出触发' : '买入触发',
    },
    { id: 'entry_stop', label: '入场失效' },
    { id: 'target', label: '止盈目标' },
  ]
    .filter((p) => raw.levels?.[p.id] > 0)
    .map((p) => ({ ...p, value: raw.levels[p.id] }))
})
</script>
<template>
  <div class="trade-signal-banner" :class="intent.tone" aria-label="当前买卖观察">
    <div class="signal-direction">
      <span v-if="intent.tone === 'buy' || intent.tone === 'sell'" aria-hidden="true">{{
        intent.tone === 'buy' ? '↗' : '↘'
      }}</span>
      <div>
        <small>当前策略</small><strong>{{ intent.title }}</strong>
      </div>
    </div>
    <div class="signal-context">
      <p :title="intent.message">{{ intent.message }}</p>
      <small v-if="latest"
        >最近{{ latest.side === 'BUY' ? '买入' : '卖出' }} <b>{{ money(latest.price, 3) }}</b> ·
        {{ exchangeTime(latest.at)?.day.slice(5) }} {{ exchangeTime(latest.at)?.time }} · 模拟成交{{
          history.error ? '（缓存）' : ''
        }}</small
      ><small v-else>信号满足执行条件后提交委托，成交记录单独展示</small>
    </div>
    <div v-if="prices.length" class="signal-prices">
      <div v-for="price in prices" :key="price.id">
        <small>{{ price.label }}</small
        ><b>{{ money(price.value, 3) }}</b>
      </div>
      <small class="price-basis">交易价</small>
    </div>
  </div>
</template>
<style scoped>
.trade-signal-banner {
  --signal-color: var(--muted);
  display: flex;
  align-items: center;
  gap: 14px;
  flex-shrink: 0;
  padding: 9px 12px;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}
.buy {
  --signal-color: var(--red);
}
.sell {
  --signal-color: var(--green);
}
.candidate {
  --signal-color: var(--yellow);
}
.signal-direction {
  display: flex;
  gap: 9px;
  align-items: center;
  color: var(--signal-color);
  flex-shrink: 0;
}
.signal-direction > span {
  font-size: 20px;
}
.signal-direction small {
  display: block;
  font-size: 10px;
  margin-bottom: 3px;
  color: var(--muted);
}
.signal-direction strong {
  font-size: 14px;
  font-weight: 600;
}
.signal-context {
  min-width: 0;
  flex: 1;
}
.signal-context p {
  margin: 0 0 5px;
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.signal-context small {
  color: var(--muted);
  font-size: 10px;
}
.signal-context b {
  color: var(--text);
}
.signal-prices {
  display: flex;
  gap: 15px;
  align-items: center;
}
.signal-prices small {
  display: block;
  font-size: 10px;
  color: var(--muted);
  margin-bottom: 4px;
}
.signal-prices b {
  display: block;
  font: 16px var(--font-numeric);
}
@container (max-width: 720px) {
  .signal-prices {
    display: none;
  }
}
@media (max-width: 700px) {
  .trade-signal-banner {
    padding: 12px 9px;
    gap: 10px;
    flex-wrap: wrap;
  }
  .signal-context {
    flex-basis: 50%;
  }
  .signal-context p {
    white-space: normal;
    line-height: 1.6;
  }
  .signal-direction strong {
    font-size: 15px;
  }
}
</style>
