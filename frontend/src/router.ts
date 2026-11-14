import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    // The server's OAuth callback redirects here, with the Strava token in the fragment
    { path: '/connected', name: 'connected', component: () => import('@/views/ConnectedView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
