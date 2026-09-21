import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: () => import('@/views/Dashboard.vue') },
    { path: '/case/new', name: 'single', component: () => import('@/views/SingleCase.vue') },
    { path: '/case/batch', name: 'batch', component: () => import('@/views/BatchImport.vue') },
    { path: '/result/:id', name: 'result', component: () => import('@/views/ResultDetail.vue') },
    { path: '/rules', name: 'rules', component: () => import('@/views/RuleBrowser.vue') },
  ],
})

export default router
