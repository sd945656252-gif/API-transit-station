<template>
  <div class="space-y-3" aria-live="polite">
    <p class="text-xs text-gray-500 dark:text-dark-400">{{ t('managedUpdates.description') }}</p>
    <p v-if="release" class="break-words text-xs text-gray-500 dark:text-dark-400">
      {{ t('managedUpdates.source') }}: {{ release.repository }}
      <span v-if="release.current_revision" class="block">{{ t('managedUpdates.installedRevision') }}: {{ release.current_revision.slice(0, 12) }}</span>
      <span v-if="release.revision" class="block">{{ t('managedUpdates.readyRevision') }}: {{ release.revision.slice(0, 12) }}</span>
    </p>
    <div v-if="release && release.preparation.state !== 'ready'" class="rounded-lg bg-amber-50 p-3 text-xs text-amber-800 dark:bg-amber-900/20 dark:text-amber-300">
      {{ t(`managedUpdates.preparation.${release.preparation.state}`) }}
      <span v-if="release.preparation.official_version"> · {{ t('managedUpdates.officialCore') }} v{{ release.preparation.official_version }}</span>
      <a v-if="release.preparation.url" :href="release.preparation.url" target="_blank" rel="noopener noreferrer" class="mt-1 block underline">{{ t('managedUpdates.viewBuild') }}</a>
    </div>
    <div v-if="active || job?.state === 'succeeded'" class="rounded-lg bg-blue-50 p-3 text-xs text-blue-700 dark:bg-blue-900/20 dark:text-blue-300">
      {{ t(`managedUpdates.stages.${job?.stage || 'queued'}`) }}
      <span v-if="job?.target_version"> · v{{ job.target_version }}</span>
      <p v-if="connectionError" class="mt-2 whitespace-normal break-words">{{ connectionError }}</p>
    </div>
    <div v-if="error" class="whitespace-pre-wrap break-words rounded-lg bg-red-50 p-3 text-xs text-red-700 dark:bg-red-900/20 dark:text-red-300">{{ error }}</div>
    <p v-if="job?.state === 'failed' && job?.github_published" class="text-xs text-amber-700 dark:text-amber-300">{{ t('managedUpdates.publishedButFailed') }}</p>
    <button v-if="hasUpdate || job?.state === 'failed'" @click="start" :disabled="active || starting"
      class="w-full rounded-lg bg-primary-500 px-3 py-2 text-sm font-medium text-white disabled:opacity-50">
      {{ active || starting ? t('managedUpdates.running') : t('managedUpdates.updateNow') }}
    </button>
    <p v-if="job?.state === 'failed'" class="text-xs text-gray-500 dark:text-dark-400">{{ t('managedUpdates.failureHelp') }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getUpdateStatus, performUpdate, type ManagedUpdateJob, type ManagedReleaseInfo } from '@/api/admin/system'

defineProps<{ hasUpdate: boolean; release?: ManagedReleaseInfo | null }>()
const emit = defineEmits<{ updated: [] }>()
const { t } = useI18n()
const job = ref<ManagedUpdateJob | null>(null)
const starting = ref(false)
const requestError = ref('')
const connectionError = ref('')
const active = computed(() => ['queued', 'checking', 'downloading', 'verifying', 'loading', 'deploying'].includes(job.value?.state || ''))
const error = computed(() => requestError.value || (job.value?.state === 'failed' ? job.value.error : '') || '')
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
let lastFinished = ''
let statusLoaded = false

function message(error: unknown): string {
  const value = error as { response?: { data?: { message?: string } }; message?: string }
  return value.response?.data?.message || value.message || t('managedUpdates.statusUnavailable')
}

async function poll() {
  if (timer) clearTimeout(timer)
  try {
    const status = await getUpdateStatus()
    if (disposed) return
    job.value = status
    if (requestError.value === connectionError.value) requestError.value = ''
    connectionError.value = ''
    if (status.state === 'succeeded' && lastFinished !== status.id) {
      lastFinished = status.id || ''
      // Loading historical success is not a new completion. Refreshing here
      // used to remount this panel and repeat the same notification forever.
      if (statusLoaded) emit('updated')
    }
    statusLoaded = true
  } catch (error) {
    if (disposed) return
    // A Docker replacement temporarily disconnects this application. Continue
    // querying persisted job state; connectivity alone is never success.
    connectionError.value = message(error)
    if (!active.value) requestError.value = connectionError.value
  } finally {
    if (!disposed) timer = setTimeout(poll, active.value ? 2000 : 15000)
  }
}

async function start() {
  if (active.value || starting.value) return
  starting.value = true
  requestError.value = ''
  try {
    const result = await performUpdate()
    if (!result.managed || !result.job) throw new Error(t('managedUpdates.statusUnavailable'))
    job.value = result.job
    statusLoaded = true
    await poll()
  } catch (error) {
    requestError.value = message(error)
    await poll()
  } finally {
    starting.value = false
  }
}

onMounted(poll)
onBeforeUnmount(() => { disposed = true; if (timer) clearTimeout(timer) })
</script>
