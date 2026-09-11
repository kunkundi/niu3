<script setup>
import { money, pct, signedMoney, changeClass } from '../state'
defineProps({ account: { type: Object, required: true } })
</script>
<template>
  <div class="metrics-grid account-metrics" aria-label="账户资产摘要">
    <section class="metric-card main-metric">
      <div class="metric-label">总资产 <span class="metric-unit">CNY</span></div>
      <div class="big-number">{{ money(account.nav) }}</div>
      <div class="metric-bottom">
        <span>累计收益率</span
        ><strong :class="changeClass(account.return_pct)">{{ pct(account.return_pct, true) }}</strong>
      </div>
    </section>
    <section class="metric-card profit-metric">
      <div class="metric-label">持仓浮动盈亏</div>
      <div class="metric-number" :class="changeClass(account.unrealized)">
        {{ signedMoney(account.unrealized) }}
      </div>
      <div class="metric-bottom">
        <span>已实现盈亏</span
        ><strong :class="changeClass(account.realized)">{{ signedMoney(account.realized) }}</strong>
      </div>
    </section>
    <section class="metric-card">
      <div class="metric-label">
        持仓市值 <span class="metric-unit">{{ account.positions.length }} 只持仓</span>
      </div>
      <div class="metric-number">{{ money(account.market_value) }}</div>
      <div class="metric-bottom exposure-summary">
        <span>总仓位</span><strong>{{ pct(account.exposure) }}</strong>
      </div>
    </section>
    <section class="metric-card">
      <div class="metric-label">可用资金</div>
      <div class="metric-number">{{ money(account.cash) }}</div>
      <div class="metric-bottom">
        <span>现金占比</span><strong>{{ pct(account.nav ? account.cash / account.nav : 0) }}</strong>
      </div>
    </section>
  </div>
</template>
<style scoped>
.account-metrics {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0;
  margin-bottom: 0;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 0;
}
.account-metrics .metric-card {
  padding: 6px 12px;
  background: transparent;
  border: 0;
  border-right: 1px solid var(--line);
  border-radius: 0;
  box-shadow: none;
}
.account-metrics .metric-card:last-child {
  border-right: 0;
}
.account-metrics .metric-label {
  margin-bottom: 4px;
  font-size: 11px;
  line-height: 16px;
}
.account-metrics .metric-unit {
  font-size: 10px;
}
.account-metrics .big-number,
.account-metrics .metric-number {
  font-size: clamp(22px, 2vw, 26px);
  font-weight: 500;
  line-height: 1.2;
  letter-spacing: 0;
}
.account-metrics .big-number {
  font-weight: 550;
}
.account-metrics .metric-bottom {
  min-height: 16px;
  margin-top: 4px;
  gap: 4px 6px;
  font-size: 11px;
  line-height: 16px;
}
@media (max-width: 700px) {
  .account-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .account-metrics .metric-card {
    padding: 8px 10px;
  }
  .account-metrics .metric-card:nth-child(2) {
    border-right: 0;
  }
  .account-metrics .metric-card:nth-child(-n + 2) {
    border-bottom: 1px solid var(--line);
  }
  .account-metrics .big-number,
  .account-metrics .metric-number {
    font-size: clamp(20px, 5.6vw, 24px);
  }
  .account-metrics .metric-label {
    gap: 4px;
  }
  .account-metrics .metric-bottom {
    font-size: 10px;
  }
}
</style>
