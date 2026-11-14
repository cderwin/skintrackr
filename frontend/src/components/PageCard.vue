<script setup lang="ts">
// The card every page is laid out in: icon, title, subtitle, a ❄️ feature list, then the page's own content.
defineProps<{
  icon: string
  title: string
  subtitle: string
  features: string[]
  success?: boolean
}>()
</script>

<template>
  <main class="card">
    <div class="icon" :class="{ success }">{{ icon }}</div>
    <h1>{{ title }}</h1>
    <p class="subtitle">{{ subtitle }}</p>

    <ul class="features">
      <li v-for="feature in features" :key="feature">{{ feature }}</li>
    </ul>

    <slot />

    <p class="disclaimer"><slot name="disclaimer" /></p>
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
  font-size: 40px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
}

.icon.success {
  background: linear-gradient(135deg, #10b981 0%, #059669 100%);
  box-shadow: 0 4px 12px rgba(16, 185, 129, 0.4);
  color: white;
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

.features {
  list-style: none;
  text-align: left;
  margin-bottom: 32px;
  padding: 0 8px;
}

.features li {
  display: flex;
  align-items: center;
  margin-bottom: 16px;
  font-size: 15px;
  color: #334155;
}

.features li::before {
  content: '❄️';
  margin-right: 12px;
  font-size: 20px;
}

.disclaimer {
  margin-top: 24px;
  font-size: 13px;
  color: #64748b;
  line-height: 1.5;
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
