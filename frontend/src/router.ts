import { createRouter, createWebHistory } from 'vue-router'

// the map is the app: signed out, it opens with the connect modal
export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/map', name: 'map', component: () => import('@/views/MapView.vue') },
    // the landing and connect pages, and where /connect used to go, are now the map
    { path: '/:pathMatch(.*)*', redirect: '/map' },
  ],
})
