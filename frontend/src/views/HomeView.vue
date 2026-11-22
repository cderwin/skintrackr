<script setup lang="ts">
import { useSignedIn } from '@/api'
import { Skier } from '@/icons'
import AppLinks from '@/components/AppLinks.vue'
import ConnectButton from '@/components/ConnectButton.vue'
import ImportPanel from '@/components/ImportPanel.vue'
import PageCard from '@/components/PageCard.vue'

const signedIn = useSignedIn()
const features = [
  'New activities sync automatically',
  'Track your vertical gain and distance',
  'Build your season statistics',
]
</script>

<template>
  <PageCard
    :icon="Skier"
    title="Backcountry Ski Tracker"
    subtitle="Automatically track your backcountry ski adventures from intervals.icu"
    :features="features"
  >
    <template v-if="signedIn">
      <AppLinks />
      <ImportPanel />
    </template>
    <ConnectButton v-else-if="signedIn === false" />

    <template #disclaimer>
      Connecting gives this app read access to your intervals.icu activities with your API key. We only read
      your activities - we never post or modify anything.
    </template>
  </PageCard>
</template>
