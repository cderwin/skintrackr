<script setup lang="ts">
// Uploading the athlete's Strava export, which gives their activities their Strava names: choose the zip (or its
// activities.csv), and see how many activities matched.
import { ref, useTemplateRef } from 'vue'
import { api, type StravaImport } from '@/api'
import { STRAVA_EXPORT_URL, stravaActivitiesCsv } from '@/stravaExport'

const emit = defineEmits<{ imported: [] }>()

const input = useTemplateRef<HTMLInputElement>('file')
const step = ref<'reading' | 'uploading' | null>(null)
const result = ref<StravaImport | null>(null)
const error = ref('')

async function upload(): Promise<void> {
  const file = input.value?.files?.[0]
  if (!file) return
  error.value = ''
  result.value = null
  try {
    step.value = 'reading'
    const csv = await stravaActivitiesCsv(file)
    step.value = 'uploading'
    result.value = await api.stravaImport(csv)
    emit('imported')
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    step.value = null
    // choosing the same file again uploads it again
    if (input.value) input.value.value = ''
  }
}
</script>

<template>
  <section class="strava">
    <p>
      Use your activities' names from Strava. Request your data export from Strava's
      <a class="brand-link" :href="STRAVA_EXPORT_URL" target="_blank" rel="noopener">Download or Delete Your Account</a>
      page, then choose the zip it emails you. Only its list of activities is uploaded.
    </p>
    <input ref="file" type="file" accept=".zip,.csv" hidden @change="upload" />
    <button class="small-button" type="button" :disabled="step !== null" @click="input?.click()">
      {{ step === 'reading' ? 'Reading export…' : step === 'uploading' ? 'Matching activities…' : 'Choose Strava export' }}
    </button>
    <small v-if="result" role="status">
      Matched {{ result.matched.toLocaleString() }} of your {{ result.activities.toLocaleString() }} activities with
      Strava's ({{ result.strava_activities.toLocaleString() }} in the export). They now show their Strava names.
    </small>
    <small v-if="error" class="failed" role="alert">{{ error }}</small>
  </section>
</template>

<style scoped>
.strava {
  padding: 16px;
  background: #f1f5f9;
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  font-weight: 400;
  color: #334155;
  line-height: 1.5;
}

small {
  font-size: 12px;
  color: #64748b;
  text-align: center;
}

.failed {
  color: #b91c1c;
}

button:disabled {
  opacity: 0.6;
  cursor: progress;
}
</style>
