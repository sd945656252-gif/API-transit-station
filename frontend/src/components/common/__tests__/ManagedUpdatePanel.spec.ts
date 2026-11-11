import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ManagedUpdatePanel from '../ManagedUpdatePanel.vue'
import { getUpdateStatus, performUpdate } from '@/api/admin/system'

vi.mock('@/api/admin/system', () => ({ getUpdateStatus: vi.fn(), performUpdate: vi.fn() }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))

let wrapper: ReturnType<typeof mount> | undefined

beforeEach(() => { vi.useFakeTimers(); vi.resetAllMocks() })
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.useRealTimers() })

describe('Managed update completion notifications', () => {
  it('displays historical success without repeatedly refreshing versions on mount', async () => {
    vi.mocked(getUpdateStatus).mockResolvedValue({ id: 'old', state: 'succeeded', stage: 'complete' })
    wrapper = mount(ManagedUpdatePanel, { props: { hasUpdate: false } })
    await flushPromises()
    expect(wrapper.text()).toContain('managedUpdates.stages.complete')
    expect(wrapper.emitted('updated')).toBeUndefined()
    await vi.advanceTimersByTimeAsync(15_000)
    expect(getUpdateStatus).toHaveBeenCalledTimes(2)
    expect(wrapper.emitted('updated')).toBeUndefined()
    wrapper.unmount()
    wrapper = mount(ManagedUpdatePanel, { props: { hasUpdate: false } })
    await flushPromises()
    expect(wrapper.emitted('updated')).toBeUndefined()
  })

  it('refreshes once when a newly submitted update actually completes', async () => {
    vi.mocked(getUpdateStatus).mockResolvedValueOnce({ state: 'idle', stage: 'idle' })
      .mockResolvedValue({ id: 'new', state: 'succeeded', stage: 'complete' })
    vi.mocked(performUpdate).mockResolvedValue({ managed: true, need_restart: false, message: 'accepted',
      job: { id: 'new', state: 'queued', stage: 'queued' } })
    wrapper = mount(ManagedUpdatePanel, { props: { hasUpdate: true } })
    await flushPromises()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('updated')).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(30_000)
    expect(wrapper.emitted('updated')).toHaveLength(1)
  })
})
