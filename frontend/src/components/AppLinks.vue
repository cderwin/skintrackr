<template>
  <nav class="app-links">
    <RouterLink class="big-button button-primary" to="/stats">📈 Season stats</RouterLink>
    <RouterLink class="big-button button-secondary" to="/map">🗺️ 3D map</RouterLink>
  </nav>
</template>

<style scoped>
.app-links {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
</style>
