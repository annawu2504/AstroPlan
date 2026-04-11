/**
 * E2E: HITL approval gate flow
 *
 * Covers:
 * 1. ApprovalDialog auto-opens on hitl_suspended event
 * 2. Escape key is blocked while gate is pending
 * 3. Approve button calls POST /hitl/respond with approved: true
 * 4. Reject button calls POST /hitl/respond with approved: false
 * 5. HITL tab badge shows count of pending gates
 * 6. Resolved gate appears in resolved history list
 */
import { test, expect, makeSseEvent } from '../fixtures/astroplan.fixture'

const MOCK_GATE = {
  gate_id: 'gate-001',
  skill_name: 'LaunchRocket',
  reason: 'Irreversible propulsion sequence requires manual approval',
  critical_state: 'PRE_LAUNCH',
  params: { thrust_level: 80, fuel_pct: 95 },
  timeout_s: 300,
  created_at: Math.floor(Date.now() / 1000),
}

test.describe('HITL Approval', () => {
  test.beforeEach(async ({ page, mockBackend: _ }) => {
    await page.goto('/')
  })

  test('auto-opens approval dialog on hitl_suspended event', async ({
    page,
    sseController,
  }) => {
    sseController.push(
      makeSseEvent('hitl_suspended', { gate: MOCK_GATE }),
    )

    // Dialog should appear
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText(MOCK_GATE.reason)).toBeVisible()
    await expect(page.getByText(MOCK_GATE.skill_name)).toBeVisible()
  })

  test('Escape key does not close dialog while gate is pending', async ({
    page,
    sseController,
  }) => {
    sseController.push(
      makeSseEvent('hitl_suspended', { gate: MOCK_GATE }),
    )

    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5000 })

    // Press Escape — dialog should remain open
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toBeVisible()
  })

  test('clicking Approve sends POST /hitl/respond with approved: true', async ({
    page,
    sseController,
  }) => {
    const requests: unknown[] = []
    page.on('request', (req) => {
      if (req.url().includes('/hitl/respond')) {
        requests.push(JSON.parse(req.postData() ?? '{}'))
      }
    })

    sseController.push(
      makeSseEvent('hitl_suspended', { gate: MOCK_GATE }),
    )

    await expect(page.getByTestId('hitl-approve-btn')).toBeVisible({ timeout: 5000 })
    await page.getByTestId('hitl-approve-btn').click()

    await expect.poll(() => requests).toHaveLength(1)
    expect((requests[0] as Record<string, unknown>).approved).toBe(true)
    expect((requests[0] as Record<string, unknown>).gate_id).toBe('gate-001')
  })

  test('clicking Reject sends POST /hitl/respond with approved: false', async ({
    page,
    sseController,
  }) => {
    const requests: unknown[] = []
    page.on('request', (req) => {
      if (req.url().includes('/hitl/respond')) {
        requests.push(JSON.parse(req.postData() ?? '{}'))
      }
    })

    sseController.push(
      makeSseEvent('hitl_suspended', { gate: MOCK_GATE }),
    )

    await expect(page.getByTestId('hitl-reject-btn')).toBeVisible({ timeout: 5000 })
    await page.getByTestId('hitl-reject-btn').click()

    await expect.poll(() => requests).toHaveLength(1)
    expect((requests[0] as Record<string, unknown>).approved).toBe(false)
  })

  test('HITL tab badge shows pending gate count', async ({ page, sseController }) => {
    sseController.push(
      makeSseEvent('hitl_suspended', { gate: MOCK_GATE }),
    )

    // Badge on HITL tab
    await expect(page.getByTestId('hitl-tab-badge')).toBeVisible({ timeout: 5000 })
    await expect(page.getByTestId('hitl-tab-badge')).toHaveText('1')
  })

  test('resolved gate appears in HITL console history after approval', async ({
    page,
    sseController,
  }) => {
    sseController.push(
      makeSseEvent('hitl_suspended', { gate: MOCK_GATE }),
    )

    await expect(page.getByTestId('hitl-approve-btn')).toBeVisible({ timeout: 5000 })
    await page.getByTestId('hitl-approve-btn').click()

    // Navigate to HITL console
    await page.getByRole('tab', { name: /审批|HITL/i }).click()

    // Resolved section should show the gate
    await expect(page.getByText(MOCK_GATE.skill_name)).toBeVisible({ timeout: 3000 })
  })
})
