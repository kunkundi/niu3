<script setup>
import { money } from '../state'
defineProps({
  groups: { type: Array, default: () => [] },
  lastDay: String,
  today: String,
  error: String,
})
defineEmits(['select', 'show-intraday'])
</script>

<template>
  <section v-if="groups.length" class="pending-daily-trades" aria-label="尚未绘入日 K 的已成交记录">
    <div class="pending-heading">
      <strong>最新模拟成交</strong>
      <span>日 K {{ lastDay ? `截至 ${lastDay}` : '尚未就绪' }}，以下成交已记录</span>
      <button type="button" class="intraday-link" @click="$emit('show-intraday')">分时查看买卖点 →</button>
    </div>
    <p v-if="error" class="pending-warning" role="status">{{ error }}</p>
    <div class="pending-events">
      <button
        v-for="group in groups"
        :key="group.id"
        type="button"
        class="pending-event"
        :class="group.effectTone"
        :aria-label="`${group.day} ${group.label} ${group.actionLabel} ${group.items.length} 笔，共 ${group.quantity} 份，查看成交明细`"
        @click="$emit('select', group)"
      >
        <div class="event-heading">
          <strong class="event-badge">
            {{ group.label }} {{ group.day === today ? '今日' : group.day.slice(5) }}{{ group.actionLabel }}
          </strong>
          <b>{{ group.items.length }} 笔 · {{ money(group.quantity, 0) }} 份</b>
          <span class="event-link">明细 ›</span>
        </div>
        <div class="event-detail">
          <span
            >成交{{ group.items.length > 1 ? '均' : '' }}价 <b>{{ money(group.price, 3) }}</b> 元</span
          >
          <span>{{ group.day }} · 最近 {{ group.time.slice(0, 5) }}</span>
        </div>
      </button>
    </div>
    <p class="pending-note">对应日 K 更新后会标在 K 线上；当前可点击卡片看明细，或切换分时查看成交位置。</p>
  </section>
</template>

<style scoped>
.pending-daily-trades {
  flex-shrink: 0;
  min-width: 0;
  margin: 3px 0 8px;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: var(--panel2);
}
.pending-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px 10px;
  font-size: 11px;
}
.pending-heading > strong {
  color: var(--ink);
}
.pending-heading > span,
.pending-note {
  color: var(--muted);
}
.intraday-link {
  margin-left: auto;
  padding: 4px 0;
  border: 0;
  background: transparent;
  color: var(--ink);
  font-size: 11px;
}
.pending-events {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 7px;
}
.pending-event {
  --event-color: var(--trade-color, var(--muted));
  --event-border: color-mix(in srgb, var(--event-color) 35%, transparent);
  --event-background: color-mix(in srgb, var(--event-color) 7%, var(--panel));
  flex: 1 1 280px;
  min-width: 0;
  padding: 8px;
  border: 1px solid var(--event-border);
  border-left: 3px solid var(--event-color);
  border-radius: 4px;
  background: var(--event-background);
  color: var(--ink);
  text-align: left;
}
.event-heading,
.event-detail {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
}
.event-heading {
  font-size: 12px;
}
.event-badge {
  color: var(--event-color);
  font-size: 13px;
}
.event-link {
  margin-left: auto;
  color: var(--event-color);
  font-size: 11px;
}
.event-detail {
  margin-top: 7px;
  justify-content: space-between;
  color: var(--text-secondary);
  font-size: 11px;
}
.pending-note,
.pending-warning {
  margin: 7px 0 0;
  font-size: 10px;
  line-height: 1.5;
}
.pending-warning {
  color: var(--yellow);
}
.pending-event:hover {
  border-color: var(--event-color);
}
button:focus-visible {
  outline: 2px solid var(--ink);
  outline-offset: 2px;
}
@media (max-width: 700px) {
  .pending-daily-trades {
    padding: 8px;
  }
  .pending-heading > span {
    flex-basis: 100%;
    order: 1;
  }
  .intraday-link {
    min-height: 44px;
  }
}
</style>
