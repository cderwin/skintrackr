<script setup lang="ts">
import PageCard from '@/components/PageCard.vue'

const features = [
  'Instant notifications for new ski activities',
  'Track your vertical gain and distance',
  'Build your season statistics',
]
</script>

<template>
  <PageCard
    icon="⛷️"
    title="Backcountry Ski Tracker"
    subtitle="Automatically track your backcountry ski adventures from Strava"
    :features="features"
  >
    <!-- a server route (the OAuth redirect), not an app route -->
    <a class="connect-button button-primary" href="/oauth2/connect">
      <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path
          d="M15.387 17.944l-2.089-4.116h-3.065L15.387 24l5.15-10.172h-3.066m-7.008-5.599l2.836 5.598h4.172L10.463 0l-7 13.828h4.169"
        />
      </svg>
      Connect with Strava
    </a>

    <template #disclaimer>
      By connecting, you authorize this app to access your <span class="strava">Strava</span> activity data. We
      only read your activities - we never post or modify anything.
    </template>
  </PageCard>
</template>

<style scoped>
.connect-button {
  padding: 18px 36px;
  font-size: 18px;
  font-weight: 600;
  border-radius: 12px;
  width: 100%;
  transition: all 0.3s ease;
  box-shadow: 0 4px 16px rgba(255, 107, 53, 0.4);
  letter-spacing: 0.3px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  text-decoration: none;
}

.connect-button svg {
  width: 24px;
  height: 24px;
}

.connect-button:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 24px rgba(255, 107, 53, 0.6);
}

.connect-button:active {
  transform: translateY(0);
}

.strava {
  font-weight: 600;
  color: #fc4c02;
}
</style>
