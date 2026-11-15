import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    // The server's OAuth callback signs the browser in and redirects here
    { path: '/connected', name: 'connected', component: () => import('@/views/ConnectedView.vue') },
    { path: '/stats', name: 'stats', component: () => import('@/views/StatsView.vue') },
    { path: '/map', name: 'map', component: () => import('@/views/MapView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
