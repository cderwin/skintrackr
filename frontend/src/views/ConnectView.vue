<script setup lang="ts">
// Connecting an intervals.icu account by its API key, with why intervals.icu, how to set it up, and what syncs.
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, INTERVALS_SETTINGS_URL, SYNC_WINDOW_DAYS } from '@/api'
import FloatingButton from '@/components/FloatingButton.vue'
import PageCard from '@/components/PageCard.vue'

const router = useRouter()
const apiKey = ref('')
const connecting = ref(false)
const error = ref('')

async function submit(): Promise<void> {
  if (!apiKey.value.trim() || connecting.value) return
  connecting.value = true
  error.value = ''
  try {
    await api.connect(apiKey.value.trim())
    await router.push('/connected')
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    connecting.value = false
  }
}
</script>

<template>
  <FloatingButton side="left" to="/" label="Back">←</FloatingButton>

  <PageCard icon="🔑" title="Connect intervals.icu" subtitle="Paste your intervals.icu API key to start tracking your season.">
    <form class="connect" @submit.prevent="submit">
      <label for="api-key">API key</label>
      <input
        id="api-key"
        v-model="apiKey"
        type="password"
        autocomplete="off"
        spellcheck="false"
        placeholder="Your intervals.icu API key"
        required
      />
      <small>
        Find it in
        <a class="brand-link" :href="INTERVALS_SETTINGS_URL" target="_blank" rel="noopener">intervals.icu settings</a>
        under Developer Settings. It's stored encrypted, and disconnecting deletes it.
      </small>
      <button class="big-button button-primary" type="submit" :disabled="connecting || !apiKey.trim()">
        {{ connecting ? 'Connecting…' : 'Connect' }}
      </button>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
    </form>

    <section class="info">
      <h2>What syncs automatically</h2>
      <p>
        New activities, and edits to activities from the last {{ SYNC_WINDOW_DAYS }} days, sync about every 15
        minutes. If you add or edit an activity that started more than {{ SYNC_WINDOW_DAYS }} days ago, press
        <strong>Re-sync</strong> on the home page to pick it up.
      </p>

      <h2>Why intervals.icu?</h2>
      <p>
        Strava's API terms and rate limits restrict what apps like this one can do with your data: each app gets
        about a thousand requests a day, and the terms limit how apps may store and show your activities.
        intervals.icu offers a free, open API with generous limits, so Backcountry Ski Tracker can archive your
        full tracks and backfill your whole history in minutes rather than days.
      </p>

      <h2>Setting up intervals.icu</h2>
      <ol>
        <li>
          Create a free account at
          <a class="brand-link" href="https://intervals.icu" target="_blank" rel="noopener">intervals.icu</a>.
        </li>
        <li>
          Connect your watch's platform (Garmin, Coros, Suunto, Wahoo, Polar, …) in Settings → Connections, or upload
          your activity files. Activities that reach intervals.icu only through Strava aren't available through its
          API, so connect your device directly.
        </li>
        <li>Copy your API key from Settings → Developer Settings, and paste it above.</li>
      </ol>
    </section>
  </PageCard>
</template>

<style scoped>
/* room for the back button on narrow screens */
@media (max-width: 600px) {
  :deep(.card) {
    margin-top: 56px;
  }
}

.connect {
  display: flex;
  flex-direction: column;
  gap: 8px;
  text-align: left;
}

label {
  font-size: 14px;
  font-weight: 600;
  color: #334155;
}

input {
  font-size: 15px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  padding: 12px 14px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  min-width: 0;
}

small {
  font-size: 13px;
  color: #64748b;
  line-height: 1.5;
  margin-bottom: 12px;
}

button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
  transform: none;
}

.error {
  color: #b91c1c;
  font-size: 14px;
}

.info {
  margin-top: 32px;
  text-align: left;
  font-size: 14px;
  line-height: 1.6;
  color: #334155;
}

.info h2 {
  font-size: 16px;
  color: #0f172a;
  margin: 20px 0 6px;
}

.info h2:first-child {
  margin-top: 0;
}

.info ol {
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
</style>
