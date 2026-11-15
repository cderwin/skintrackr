<script setup lang="ts">
// A secret shown masked, with Show/Hide and Copy.
import { ref } from 'vue'

const props = defineProps<{ token: string; label: string }>()

const input = ref<HTMLInputElement | null>(null)
const revealed = ref(false)
const copyLabel = ref('Copy')

async function copy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.token)
    copyLabel.value = 'Copied!'
  } catch {
    revealed.value = true
    input.value?.select()
    copyLabel.value = 'Select & copy'
  }
  setTimeout(() => (copyLabel.value = 'Copy'), 2000)
}
</script>

<template>
  <div class="token-row">
    <input
      ref="input"
      class="token-value"
      :type="revealed ? 'text' : 'password'"
      :value="token"
      readonly
      :aria-label="label"
    />
    <button class="small-button" type="button" @click="revealed = !revealed">{{ revealed ? 'Hide' : 'Show' }}</button>
    <button class="small-button button-primary" type="button" @click="copy">{{ copyLabel }}</button>
  </div>
</template>

<style scoped>
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
</style>
