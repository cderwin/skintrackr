<script setup lang="ts">
import { ChartLine, MapIcon } from '@lucide/vue'
</script>

<template>
  <nav class="app-links">
    <RouterLink class="big-button button-primary" to="/stats"><ChartLine />Season stats</RouterLink>
    <RouterLink class="big-button button-secondary" to="/map"><MapIcon />3D map</RouterLink>
  </nav>
</template>

<style scoped>
.app-links {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
</style>
