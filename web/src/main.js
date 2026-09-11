import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import Overview from './components/Overview.vue'
import Market from './components/Market.vue'
import Signals from './components/Signals.vue'
import Settings from './components/Settings.vue'
import './style.css'
import './terminal.css'
import './touch.css'

const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: (to, from) => (to.path === '/signals' && to.path === from.path ? false : { top: 0 }),
  routes: [
    {
      path: '/',
      component: Overview,
      meta: { title: '投资总览', subtitle: '账户权益、持仓、交易记录与运行状态' },
    },
    {
      path: '/market',
      component: Market,
      meta: { title: '我的 ETF', subtitle: '手动管理名单，查看行情与走势' },
    },
    {
      path: '/signals',
      component: Signals,
      meta: { title: '当前交易信号', subtitle: '买卖条件、结构价位与目标持仓' },
    },
    {
      path: '/account',
      redirect: '/',
    },
    {
      path: '/settings',
      component: Settings,
      meta: { title: '设置与运行', subtitle: '策略、风控与数据运行参数' },
    },
  ],
})
createApp(App).use(router).mount('#app')
