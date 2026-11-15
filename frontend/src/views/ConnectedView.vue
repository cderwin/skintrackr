<script setup lang="ts">
import { INTERVALS_SETTINGS_URL } from '@/api'
import AppLinks from '@/components/AppLinks.vue'
import ImportPanel from '@/components/ImportPanel.vue'
import PageCard from '@/components/PageCard.vue'

const features = [
  'Import your history below to see your season stats',
  'New activities sync automatically',
  "You're all set for the season!",
]
</script>

<template>
  <PageCard
    icon="✓"
    title="Successfully Connected!"
    subtitle="Your intervals.icu account is connected to Backcountry Ski Tracker"
    :features="features"
    success
  >
    <AppLinks />
    <ImportPanel />

    <template #disclaimer>
      You can disconnect at any time from Settings on the stats page, which deletes everything stored for you. To
      revoke the key itself, generate a new one in your
      <a class="brand-link" :href="INTERVALS_SETTINGS_URL" target="_blank" rel="noopener">intervals.icu settings</a>.
    </template>
  </PageCard>
</template>
