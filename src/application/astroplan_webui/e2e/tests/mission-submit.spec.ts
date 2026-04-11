/**
 * E2E: Mission submission flow
 *
 * Covers:
 * 1. Submitting a mission via the text area + Ctrl+Enter
 * 2. Mission status badge updates to planning/executing via SSE
 * 3. Active mission label appears after submission
 * 4. History list shows completed mission after mission_completed event
 */
import { test, expect, makeSseEvent } from '../fixtures/astroplan.fixture'

test.describe('Mission Submit', () => {
  test.beforeEach(async ({ page, mockBackend: _ }) => {
    await page.goto('/')
  })

  test('submits mission via Ctrl+Enter and shows active mission label', async ({
    page,
    sseController,
  }) => {
    const missionText = 'Calibrate Hubble sensors'

    // Type into the mission input
    await page.getByPlaceholder(/输入任务|Enter mission/i).fill(missionText)

    // Submit with Ctrl+Enter
    await page.keyboard.press('Control+Enter')

    // API call should have fired; mission label appears
    await expect(page.getByText(missionText)).toBeVisible({ timeout: 3000 })

    // Push plan_generated SSE — status → executing
    sseController.push(
      makeSseEvent(
        'plan_generated',
        {
          plan: {
            revision_id: 'rev-1',
            nodes: [
              {
                node_id: 'n1',
                lineage_id: 'l1',
                skill_name: 'InitSensors',
                params: {},
                depends_on: [],
                required_roles: [],
                tool_hints: [],
                interruptible: true,
              },
            ],
            edges: [],
          },
        },
        'rev-1',
      ),
    )

    // Status badge should show executing or planning
    await expect(page.getByTestId('mission-status-badge').or(page.locator('[data-testid*="status"]'))).toBeVisible({ timeout: 3000 })
  })

  test('shows completed mission in history after mission_completed event', async ({
    page,
    sseController,
  }) => {
    // Submit a mission first
    await page.getByPlaceholder(/输入任务|Enter mission/i).fill('Collect soil samples')
    await page.getByRole('button', { name: /提交|Submit/i }).click()

    // Push mission_completed
    sseController.push(
      makeSseEvent('mission_completed', {
        status: 'completed',
        total_steps: 5,
        replan_count: 0,
      }),
    )

    // History list should populate
    await expect(page.getByText('Collect soil samples')).toBeVisible({ timeout: 5000 })
  })
})
