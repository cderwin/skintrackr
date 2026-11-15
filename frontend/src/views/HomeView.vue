<script setup lang="ts">
import { useSignedIn } from '@/api'
import AppLinks from '@/components/AppLinks.vue'
import ConnectButton from '@/components/ConnectButton.vue'
import ImportPanel from '@/components/ImportPanel.vue'
import PageCard from '@/components/PageCard.vue'

const signedIn = useSignedIn()
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
    <template v-if="signedIn">
      <AppLinks />
      <ImportPanel />
    </template>
    <ConnectButton v-else-if="signedIn === false" />

    <template #disclaimer>
      By connecting, you authorize this app to access your <span class="strava">Strava</span> activity data. We
      only read your activities - we never post or modify anything.
    </template>
  </PageCard>
</template>

<style scoped>
.strava {
  font-weight: 600;
  color: #fc4c02;
}
</style>
