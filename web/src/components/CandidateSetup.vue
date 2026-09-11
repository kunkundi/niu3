<script setup>
import { money } from '../state'
defineProps({ candidate: Object })
</script>
<template>
  <section v-if="candidate" class="candidate-setup" aria-label="次日候选入选依据">
    <div class="candidate-heading">
      <strong><span aria-hidden="true">候</span> {{ candidate.setup }}</strong>
      <span>{{ candidate.signal_day }} 形成 · {{ candidate.execute_day }} 待触发</span>
      <span>{{ candidate.trend }} · {{ candidate.context }} · 前复权</span>
    </div>
    <dl>
      <div
        v-for="level in [
          { id: 'entry', label: '入场触发' },
          { id: 'entry_stop', label: '入场失效' },
          { id: 'target', label: '止盈目标' },
          { id: 'entry_ceiling', label: '入场价格上限' },
        ]"
        :key="level.id"
      >
        <dt>{{ level.label }}</dt>
        <dd>{{ money(candidate[level.id], 3) }}</dd>
      </div>
      <div>
        <dt>入场线潜在盈亏比</dt>
        <dd>{{ money(candidate.reward_risk) }}</dd>
      </div>
      <div>
        <dt>盈亏比过滤</dt>
        <dd>{{ candidate.rr_enabled ? `开启 ≥ ${money(candidate.minimum_rr)}` : '已关闭' }}</dd>
      </div>
    </dl>
    <ul>
      <li v-for="reason in candidate.reasons" :key="reason">{{ reason }}</li>
    </ul>
    <p>“候”定位形成入场结构的 K 线，等待下一交易日触发；十日买点复盘统计过去的触价记录。</p>
  </section>
</template>
<style scoped>
.candidate-setup {
  flex-shrink: 0;
  margin: 8px 0;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-left: 3px solid var(--yellow);
  border-radius: var(--radius-control);
  background: var(--panel2);
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.6;
}
.candidate-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 14px;
}
.candidate-heading strong {
  color: var(--yellow);
  font-size: 13px;
}
.candidate-heading > span {
  color: var(--muted);
}
dl {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 24px;
  margin: 8px 0;
}
dt {
  color: var(--muted);
  font-size: 10px;
}
dd {
  margin: 2px 0 0;
  font: 14px var(--font-numeric);
  color: var(--text);
}
ul {
  margin: 8px 0;
  padding-left: 18px;
}
p {
  margin: 6px 0 0;
  color: var(--muted);
}
@media (max-width: 700px) {
  .candidate-setup {
    padding: 10px;
    font-size: 12px;
  }
  dl {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
}
</style>
