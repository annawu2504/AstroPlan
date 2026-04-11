/**
 * Shared Playwright fixtures for AstroPlan E2E tests.
 *
 * Provides:
 * - mockSse: helper to push SSE events via mocked EventSource routes
 * - mockBackend: sets up all API route mocks with sensible defaults
 * - sseController: controls SSE stream lifecycle
 */
import { test as base, type Page, type Route } from '@playwright/test'

// ---------------------------------------------------------------------------
// Types mirrored from backend schemas
// ---------------------------------------------------------------------------

export interface MockPlanNode {
  node_id: string
  lineage_id: string
  skill_name: string
  params: Record<string, unknown>
  depends_on: string[]
  required_roles: string[]
  tool_hints: string[]
  interruptible: boolean
}

export interface MockHitlGate {
  gate_id: string
  skill_name: string
  reason: string
  critical_state: string
  params: Record<string, unknown>
  timeout_s: number
  created_at: number
}

export interface MockSseEvent {
  event: string
  revision_id?: string
  timestamp: number
  payload: Record<string, unknown>
}

// ---------------------------------------------------------------------------
// SSE controller — lets tests push events into the mocked stream
// ---------------------------------------------------------------------------

export class SseController {
  private _writer: ((data: string) => void) | null = null
  private _pendingEvents: string[] = []

  register(writer: (data: string) => void) {
    this._writer = writer
    // Flush any events queued before connection was established
    for (const ev of this._pendingEvents) writer(ev)
    this._pendingEvents = []
  }

  push(event: MockSseEvent) {
    const line = `data: ${JSON.stringify(event)}\n\n`
    if (this._writer) {
      this._writer(line)
    } else {
      this._pendingEvents.push(line)
    }
  }
}

// ---------------------------------------------------------------------------
// Default mock data
// ---------------------------------------------------------------------------

export const DEFAULT_NODES: MockPlanNode[] = [
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
  {
    node_id: 'n2',
    lineage_id: 'l2',
    skill_name: 'CollectData',
    params: { samples: 10 },
    depends_on: ['n1'],
    required_roles: [],
    tool_hints: [],
    interruptible: true,
  },
]

export const DEFAULT_SNAPSHOT = {
  revision_id: 'rev-1',
  mission: 'Test mission',
  mission_status: 'executing',
  nodes: DEFAULT_NODES,
  edges: [{ from: 'n1', to: 'n2' }],
  node_statuses: { n1: 'pending', n2: 'pending' },
  pending_gates: [],
  as_of: Date.now(),
}

// ---------------------------------------------------------------------------
// Fixture type
// ---------------------------------------------------------------------------

type AstroplanFixtures = {
  sseController: SseController
  mockBackend: void
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

export const test = base.extend<AstroplanFixtures>({
  sseController: async ({}, use) => {
    const controller = new SseController()
    await use(controller)
  },

  mockBackend: [
    async ({ page, sseController }, use) => {
      // Health
      await page.route('**/health', (route: Route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'ok',
            mission_status: 'idle',
            pending_gates: 0,
            command_queue: 0,
            revisions: 0,
          }),
        }),
      )

      // Labs
      await page.route('**/labs', (route: Route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ labs: ['ISS', 'Mars-1', 'Lunar-Outpost'] }),
        }),
      )

      // Plan snapshot
      await page.route('**/plan/snapshot', (route: Route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(DEFAULT_SNAPSHOT),
        }),
      )

      // Mission start
      await page.route('**/mission/start', (route: Route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ ok: true, message: 'started' }),
        }),
      )

      // Mission stop
      await page.route('**/mission/stop', (route: Route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ ok: true, message: 'stopped' }),
        }),
      )

      // HITL respond
      await page.route('**/hitl/respond', (route: Route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ ok: true }),
        }),
      )

      // Command inject
      await page.route('**/command/inject', (route: Route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ ok: true, queued: false }),
        }),
      )

      // SSE stream — uses ReadableStream to push events on demand
      await page.route('**/events', async (route: Route) => {
        const stream = new ReadableStream({
          start(controller) {
            sseController.register((data) => {
              const encoder = new TextEncoder()
              controller.enqueue(encoder.encode(data))
            })
          },
        })

        await route.fulfill({
          status: 200,
          headers: {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            Connection: 'keep-alive',
          },
          body: stream as unknown as string,
        })
      })

      await use()
    },
    { auto: false },
  ],
})

export const expect = test.expect

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export async function waitForTab(page: Page, tabName: string) {
  await page.getByRole('tab', { name: tabName }).click()
}

export function makeSseEvent(
  event: string,
  payload: Record<string, unknown>,
  revisionId?: string,
): MockSseEvent {
  return {
    event,
    revision_id: revisionId,
    timestamp: Date.now(),
    payload,
  }
}
