<script setup lang="ts">
import AppLinks from '@/components/AppLinks.vue'
import PageCard from '@/components/PageCard.vue'

const features = [
  "We'll notify you when new activities are uploaded",
  'Your ski stats are being tracked automatically',
  "You're all set for the season!",
]
</script>

<template>
  <PageCard
    icon="✓"
    title="Successfully Connected!"
    subtitle="Your Strava account has been linked to Backcountry Ski Tracker"
    :features="features"
    success
  >
    <AppLinks />

    <template #disclaimer>
      You can revoke this app's access at any time by visiting your
      <a class="brand-link" href="https://www.strava.com/settings/apps" target="_blank">Strava settings</a> page.
    </template>
  </PageCard>
</template>
