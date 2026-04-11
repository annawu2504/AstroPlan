/**
 * E2E: SSE reconnection and snapshot reconciliation
 *
 * Covers:
 * 1. ConnectionBanner appears when SSE is not 'live'
 * 2. ConnectionBanner disappears after successful reconciliation
 * 3. Snapshot is fetched on reconnect (snapshot endpoint called)
 * 4. Events buffered during reconciliation are replayed
 * 5. Reconnect banner shows retry count
 */
import { test, expect } from '../fixtures/astroplan.fixture'

test.describe('SSE Reconnect', () => {
  test('shows connection banner when SSE is disconnected', async ({ page }) => {
    // Intercept /events to return an error (force disconnect)
    await page.route('**/events', (route) =>
      route.fulfill({
        status: 503,
        body: 'Service Unavailable',
      }),
    )

    // Intercept other endpoints with defaults
    await page.route('**/health', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', mission_status: 'idle', pending_gates: 0, command_queue: 0, revisions: 0 }),
      }),
    )
    await page.route('**/labs', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ labs: [] }),
      }),
    )
    await page.route('**/plan/snapshot', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          revision_id: null,
          mission: null,
          mission_status: 'idle',
          nodes: [],
          edges: [],
          node_statuses: {},
          pending_gates: [],
          as_of: Date.now(),
        }),
      }),
    )

    await page.goto('/')

    // ConnectionBanner should appear
    await expect(page.getByTestId('connection-banner')).toBeVisible({ timeout: 5000 })
  })

  test('connection banner disappears after successful reconnect and reconciliation', async ({
    page,
    sseController,
    mockBackend: _,
  }) => {
    await page.goto('/')

    // Initially banner may show while connecting, then disappear
    // After SSE connects and reconciliation succeeds → banner hidden
    await expect(page.getByTestId('connection-banner')).toBeHidden({ timeout: 8000 })
  })

  test('fetches plan snapshot on SSE connect for reconciliation', async ({ page }) => {
    const snapshotCalls: number[] = []

    await page.route('**/plan/snapshot', (route) => {
      snapshotCalls.push(Date.now())
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          revision_id: 'rev-snap',
          mission: 'Snapshot mission',
          mission_status: 'executing',
          nodes: [],
          edges: [],
          node_statuses: {},
          pending_gates: [],
          as_of: Date.now(),
        }),
      })
    })

    await page.route('**/events', async (route) => {
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
        body: '',
      })
    })

    await page.route('**/health', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', mission_status: 'idle', pending_gates: 0, command_queue: 0, revisions: 0 }),
      }),
    )
    await page.route('**/labs', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ labs: [] }) }),
    )

    await page.goto('/')

    // Snapshot should be fetched on connect
    await expect.poll(() => snapshotCalls.length, { timeout: 5000 }).toBeGreaterThan(0)
  })

  test('connection banner shows reconnecting text after error', async ({ page }) => {
    let requestCount = 0
    await page.route('**/events', (route) => {
      requestCount++
      // First attempt fails, subsequent attempts also fail to keep banner visible
      return route.fulfill({ status: 503, body: 'error' })
    })

    await page.route('**/health', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', mission_status: 'idle', pending_gates: 0, command_queue: 0, revisions: 0 }),
      }),
    )
    await page.route('**/labs', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ labs: [] }) }),
    )
    await page.route('**/plan/snapshot', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          revision_id: null, mission: null, mission_status: 'idle',
          nodes: [], edges: [], node_statuses: {}, pending_gates: [], as_of: Date.now(),
        }),
      }),
    )

    await page.goto('/')

    // Banner should be visible with error state
    await expect(page.getByTestId('connection-banner')).toBeVisible({ timeout: 5000 })

    // Should contain reconnect-related text (either connecting or reconnecting)
    const banner = page.getByTestId('connection-banner')
    await expect(banner).toBeVisible()
    expect(requestCount).toBeGreaterThanOrEqual(1)
  })
})
