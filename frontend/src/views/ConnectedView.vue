<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageCard from '@/components/PageCard.vue'

const features = [
  "We'll notify you when new activities are uploaded",
  'Your ski stats are being tracked automatically',
  "You're all set for the season!",
]

// The server passes the token in the URL fragment: read it, then drop it from the address bar and history.
const route = useRoute()
const router = useRouter()
const params = new URLSearchParams(route.hash.slice(1))
const accessToken = params.get('access_token')
const expiresAt = Number(params.get('expires_at'))
const expiry = expiresAt
  ? new Date(expiresAt * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
  : ''
onMounted(() => router.replace({ path: route.path }))

const tokenInput = ref<HTMLInputElement | null>(null)
const revealed = ref(false)
const copyLabel = ref('Copy')

async function copyToken(): Promise<void> {
  try {
    await navigator.clipboard.writeText(accessToken ?? '')
    copyLabel.value = 'Copied!'
  } catch {
    revealed.value = true
    tokenInput.value?.select()
    copyLabel.value = 'Select & copy'
  }
  setTimeout(() => (copyLabel.value = 'Copy'), 2000)
}
</script>

<template>
  <PageCard
    icon="✓"
    title="Successfully Connected!"
    subtitle="Your Strava account has been linked to Backcountry Ski Tracker"
    :features="features"
    success
  >
    <section v-if="accessToken" class="token-section">
      <h2>Your Strava token</h2>
      <div class="token-row">
        <input
          ref="tokenInput"
          class="token-value"
          :type="revealed ? 'text' : 'password'"
          :value="accessToken"
          readonly
          aria-label="Strava access token"
        />
        <button class="token-button" type="button" @click="revealed = !revealed">
          {{ revealed ? 'Hide' : 'Show' }}
        </button>
        <button class="token-button button-primary" type="button" @click="copyToken">{{ copyLabel }}</button>
      </div>
      <p class="token-meta">
        <template v-if="expiry">Expires {{ expiry }}<br /></template>
        <span class="token-warning">
          Treat this token like a password. It won't be shown again after you leave this page.
        </span>
      </p>
    </section>

    <section v-else class="token-section">
      <p class="token-meta">
        Your token is no longer shown on this page. <RouterLink class="brand-link" to="/">Reconnect</RouterLink> to
        see it again.
      </p>
    </section>

    <template #disclaimer>
      You can revoke this app's access at any time by visiting your
      <a class="brand-link" href="https://www.strava.com/settings/apps" target="_blank">Strava settings</a> page.
    </template>
  </PageCard>
</template>

<style scoped>
.token-section {
  text-align: left;
  margin-bottom: 24px;
  padding: 20px;
  background: #f1f5f9;
  border-radius: 16px;
}

.token-section h2 {
  font-size: 15px;
  font-weight: 600;
  color: #0f172a;
  margin-bottom: 12px;
}

.token-row {
  display: flex;
  gap: 8px;
}

.token-value {
  flex: 1;
  min-width: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  color: #334155;
  background: white;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 10px 12px;
}

.token-button {
  border-radius: 8px;
  padding: 0 14px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

.token-button:not(.button-primary) {
  background: white;
  color: #334155;
  border: 1px solid #cbd5e1;
}

.token-meta {
  margin-top: 12px;
  font-size: 13px;
  color: #475569;
  line-height: 1.5;
}

.token-warning {
  color: #b45309;
}
</style>
