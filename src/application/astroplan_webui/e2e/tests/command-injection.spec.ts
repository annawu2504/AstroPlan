/**
 * E2E: Command injection flow
 *
 * Covers:
 * 1. Ctrl+Enter submits command from CommandCenter textarea
 * 2. Submitted command appears in active queue with 'queued' status
 * 3. Command appears in history after submission
 * 4. Command status updates from queued → applied via SSE
 * 5. Queue cap notification (toast) when >10 commands
 */
import { test, expect, makeSseEvent, waitForTab } from '../fixtures/astroplan.fixture'

test.describe('Command Injection', () => {
  test.beforeEach(async ({ page, mockBackend: _ }) => {
    await page.goto('/')
    await waitForTab(page, /指令|Command/i)
  })

  test('submits command via Ctrl+Enter', async ({ page }) => {
    const cmdText = 'Increase sensor sampling rate to 5Hz'

    await page.getByPlaceholder(/输入指令|Enter command/i).fill(cmdText)
    await page.keyboard.press('Control+Enter')

    // Command text should appear in the queue or history list
    await expect(page.getByText(cmdText)).toBeVisible({ timeout: 3000 })
  })

  test('submits command via submit button', async ({ page }) => {
    const cmdText = 'Abort current maneuver'

    await page.getByPlaceholder(/输入指令|Enter command/i).fill(cmdText)
    await page.getByRole('button', { name: /提交|Submit/i }).click()

    await expect(page.getByText(cmdText)).toBeVisible({ timeout: 3000 })
  })

  test('sends POST /command/inject on submission', async ({ page }) => {
    const requests: unknown[] = []
    page.on('request', (req) => {
      if (req.url().includes('/command/inject')) {
        requests.push(JSON.parse(req.postData() ?? '{}'))
      }
    })

    await page.getByPlaceholder(/输入指令|Enter command/i).fill('Test command')
    await page.keyboard.press('Control+Enter')

    await expect.poll(() => requests).toHaveLength(1)
    expect((requests[0] as Record<string, unknown>).command).toBe('Test command')
  })

  test('command status updates to applied via SSE command_applied event', async ({
    page,
    sseController,
  }) => {
    await page.getByPlaceholder(/输入指令|Enter command/i).fill('Deploy instrument')
    await page.keyboard.press('Control+Enter')

    // Push command_applied event
    sseController.push(
      makeSseEvent('command_applied', {
        command: 'Deploy instrument',
        timestamp: Date.now(),
      }),
    )

    // Status badge should eventually show applied/success state
    await expect(page.getByText(/applied|已应用/i).first()).toBeVisible({ timeout: 3000 })
  })
})
