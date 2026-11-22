<script setup lang="ts">
// The card the map's modals are laid out in: icon, title and subtitle, then the modal's own content. With an
// `aside` slot it's wider, in two columns (the aside on the right), down to one column on narrow screens.
import type { Component } from 'vue'

defineProps<{
  /** A Lucide icon. */
  icon: Component
  title: string
  subtitle: string
}>()
</script>

<template>
  <main class="card" :class="{ split: $slots.aside }">
    <div class="primary">
      <div class="icon"><component :is="icon" :size="40" :stroke-width="1.75" /></div>
      <h1>{{ title }}</h1>
      <p class="subtitle">{{ subtitle }}</p>
      <slot />
    </div>
    <aside v-if="$slots.aside" class="aside"><slot name="aside" /></aside>
  </main>
</template>

<style scoped>
.card {
  background: rgba(255, 255, 255, 0.97);
  backdrop-filter: blur(20px);
  border-radius: 24px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(255, 255, 255, 0.1);
  max-width: 480px;
  width: 100%;
  padding: 48px 40px;
  text-align: center;
  position: relative;
  z-index: 10;
  animation: slide-up 0.6s ease-out;
}

.card.split {
  max-width: 860px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 40px;
  align-items: start;
}

.aside {
  text-align: left;
}

@media (max-width: 900px) {
  .card.split {
    max-width: 480px;
    grid-template-columns: 1fr;
    gap: 32px;
  }
}

@keyframes slide-up {
  from {
    opacity: 0;
    transform: translateY(30px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.icon {
  width: 80px;
  height: 80px;
  margin: 0 auto 24px;
  background: linear-gradient(135deg, #1e3a5f 0%, #2d5a7b 100%);
  border-radius: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
}

h1 {
  font-size: 32px;
  font-weight: 700;
  color: #0f172a;
  margin-bottom: 12px;
  letter-spacing: -0.5px;
}

.subtitle {
  font-size: 16px;
  color: #475569;
  margin-bottom: 32px;
  line-height: 1.6;
}

@media (max-width: 520px) {
  .card {
    padding: 36px 28px;
  }

  h1 {
    font-size: 28px;
  }

  .subtitle {
    font-size: 15px;
  }
}
</style>
