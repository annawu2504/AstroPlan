/**
 * Headless documentation screenshot script.
 *
 * Captures every major UI state without a real backend.
 * Output: docs/screenshots/<name>.png  (relative to webui root)
 *
 * Usage:
 *   bun run screenshot:docs
 *   # or with a real backend already running on :8080:
 *   ASTROPLAN_REAL_BACKEND=1 bun run screenshot:docs
 */

import { chromium, type Page } from '@playwright/test'
import path from 'path'
import fs from 'fs'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const OUT_DIR = path.resolve(__dirname, '../docs/screenshots')

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fs.mkdirSync(OUT_DIR, { recursive: true })

async function shot(page: Page, name: string) {
  await page.screenshot({
    path: path.join(OUT_DIR, `${name}.png`),
    fullPage: false,
    animations: 'disabled',
  })
  console.log(`  ✓  ${name}.png`)
}

function sseEvent(event: string, payload: Record<string, unknown>, revisionId?: string) {
  return JSON.stringify({
    event,
    revision_id: revisionId,
    timestamp: Date.now(),
    payload,
  })
}

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const DEMO_NODES = [
  { node_id: 'n1', lineage_id: 'l1', skill_name: 'activate_pump',     params: {},           depends_on: [],     required_roles: [], tool_hints: [], interruptible: true },
  { node_id: 'n2', lineage_id: 'l2', skill_name: 'heat_to_40',        params: { temp: 40 }, depends_on: ['n1'], required_roles: [], tool_hints: [], interruptible: true },
  { node_id: 'n3', lineage_id: 'l3', skill_name: 'activate_camera',   params: {},           depends_on: ['n1'], required_roles: [], tool_hints: [], interruptible: true },
  { node_id: 'n4', lineage_id: 'l4', skill_name: 'record_datastream',  params: {},           depends_on: ['n2', 'n3'], required_roles: [], tool_hints: [], interruptible: true },
]

const DEMO_EDGES = [
  { from: 'n1', to: 'n2' },
  { from: 'n1', to: 'n3' },
  { from: 'n2', to: 'n4' },
  { from: 'n3', to: 'n4' },
]

const DEMO_SNAPSHOT = {
  revision_id: 'rev-001',
  mission: '进行流体实验：激活泵，加热至40°C，启动摄像头记录数据。',
  mission_status: 'executing',
  nodes: DEMO_NODES,
  edges: DEMO_EDGES,
  node_statuses: { n1: 'completed', n2: 'running', n3: 'running', n4: 'pending' },
  pending_gates: [],
  as_of: Date.now(),
}

const DEMO_HITL_GATE = {
  gate_id: 'gate-001',
  skill_name: 'execute_main_forming',
  reason: '不可逆操作：主成型流程需人工确认',
  critical_state: 'FORMING_ARMED',
  params: { pressure_bar: 120, duration_s: 30 },
  timeout_s: 60,
  created_at: Date.now(),
}

// ---------------------------------------------------------------------------
// Mock all API routes
// ---------------------------------------------------------------------------

async function mockRoutes(page: Page, sseQueue: { push: (data: string) => void }) {
  await page.route('**/health', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ status: 'ok', mission_status: 'executing', pending_gates: 1, command_queue: 0, revisions: 1 }) }),
  )
  await page.route('**/labs', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ labs: ['Fluid-Lab-Demo', 'fiber-composite-lab', 'microbio-sampling-lab'] }) }),
  )
  await page.route('**/plan/snapshot', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DEMO_SNAPSHOT) }),
  )
  await page.route('**/mission/start', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, message: 'started' }) }),
  )
  await page.route('**/mission/stop', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, message: 'stopped' }) }),
  )
  await page.route('**/hitl/respond', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) }),
  )
  await page.route('**/command/inject', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, queued: false }) }),
  )

  // SSE stream — WritableStream writer registered from ReadableStream pull
  let _writer: ((d: string) => void) | null = null
  const pending: string[] = []
  sseQueue.push = (data: string) => {
    if (_writer) _writer(data)
    else pending.push(data)
  }

  await page.route('**/events', async (route) => {
    const stream = new ReadableStream({
      start(ctrl) {
        _writer = (d) => ctrl.enqueue(new TextEncoder().encode(d))
        for (const p of pending) _writer(p)
        pending.length = 0
      },
    })
    await route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' },
      body: stream as unknown as string,
    })
  })
}

function pushSse(queue: { push: (d: string) => void }, event: string, payload: Record<string, unknown>, revisionId?: string) {
  queue.push(`data: ${sseEvent(event, payload, revisionId)}\n\n`)
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const BASE_URL = process.env.ASTROPLAN_REAL_BACKEND === '1'
  ? 'http://localhost:5173'
  : 'http://localhost:5173'

async function main() {
  console.log(`\nAstroPlan — documentation screenshots`)
  console.log(`Output: ${OUT_DIR}\n`)

  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
    colorScheme: 'dark',
  })

  // ── 01  Mission Control (idle) ──────────────────────────────────────────
  {
    const page = await context.newPage()
    const q = { push: (_: string) => {} }
    await mockRoutes(page, q)
    // override health for idle state
    await page.route('**/health', (r) =>
      r.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', mission_status: 'idle', pending_gates: 0, command_queue: 0, revisions: 0 }) }),
    )
    await page.goto(BASE_URL)
    await page.waitForSelector('[data-testid="mission-input"], textarea', { timeout: 5000 })
    await shot(page, '01_mission_control_idle')
    await page.close()
  }

  // ── 02  Mission Control (mission submitted, executing) ──────────────────
  {
    const page = await context.newPage()
    const q = { push: (_: string) => {} }
    await mockRoutes(page, q)
    await page.goto(BASE_URL)
    await page.waitForSelector('[data-testid="mission-input"], textarea', { timeout: 5000 })

    // fill and submit
    const input = page.locator('[data-testid="mission-input"], textarea').first()
    await input.fill('进行流体实验：激活泵，加热至40°C，启动摄像头记录数据。')
    await page.keyboard.press('Control+Enter')
    await page.waitForTimeout(400)

    // push plan_generated so status updates
    pushSse(q, 'plan_generated', { plan: { revision_id: 'rev-001', nodes: DEMO_NODES, edges: DEMO_EDGES } }, 'rev-001')
    await page.waitForTimeout(600)
    await shot(page, '02_mission_control_executing')
    await page.close()
  }

  // ── 03  Plan Viewer (DAG canvas with node statuses) ─────────────────────
  {
    const page = await context.newPage()
    const q = { push: (_: string) => {} }
    await mockRoutes(page, q)
    await page.goto(BASE_URL)
    await page.waitForTimeout(300)

    // push plan_generated before navigating to plan tab
    pushSse(q, 'plan_generated', { plan: { revision_id: 'rev-001', nodes: DEMO_NODES, edges: DEMO_EDGES } }, 'rev-001')
    await page.waitForTimeout(300)
    pushSse(q, 'node_status', { node_id: 'n1', lineage_id: 'l1', status: 'completed' })
    pushSse(q, 'node_status', { node_id: 'n2', lineage_id: 'l2', status: 'running' })
    pushSse(q, 'node_status', { node_id: 'n3', lineage_id: 'l3', status: 'running' })

    await page.getByRole('tab', { name: /计划|Plan/i }).click()
    await page.waitForTimeout(800)   // let Dagre layout settle
    await shot(page, '03_plan_viewer_dag')
    await page.close()
  }

  // ── 04  Plan Viewer (node detail panel) ─────────────────────────────────
  {
    const page = await context.newPage()
    const q = { push: (_: string) => {} }
    await mockRoutes(page, q)
    await page.goto(BASE_URL)
    await page.waitForTimeout(300)
    pushSse(q, 'plan_generated', { plan: { revision_id: 'rev-001', nodes: DEMO_NODES, edges: DEMO_EDGES } }, 'rev-001')
    await page.getByRole('tab', { name: /计划|Plan/i }).click()
    try {
      await page.waitForSelector('[data-testid="plan-node-n1"]', { timeout: 5000 })
      await page.getByTestId('plan-node-n1').click()
      await page.waitForTimeout(400)
    } catch {
      // node testid not found — fall back to generic click area
    }
    await shot(page, '04_plan_viewer_node_detail')
    await page.close()
  }

  // ── 05  HITL Console (pending gate) ─────────────────────────────────────
  {
    const page = await context.newPage()
    const q = { push: (_: string) => {} }
    await mockRoutes(page, q)
    await page.goto(BASE_URL)
    await page.waitForTimeout(300)
    pushSse(q, 'hitl_gate_opened', { gate: DEMO_HITL_GATE })
    await page.getByRole('tab', { name: /人机协同|HITL/i }).click()
    await page.waitForTimeout(600)
    await shot(page, '05_hitl_console_pending')
    await page.close()
  }

  // ── 06  Command Center ───────────────────────────────────────────────────
  {
    const page = await context.newPage()
    const q = { push: (_: string) => {} }
    await mockRoutes(page, q)
    await page.goto(BASE_URL)
    await page.getByRole('tab', { name: /指令|Command/i }).click()
    await page.waitForTimeout(400)
    // Pre-fill a command
    try {
      const cmd = page.locator('textarea, [data-testid="command-input"], input[placeholder]').last()
      await cmd.fill('ABORT_HEATING priority=high')
    } catch { /* ignore */ }
    await shot(page, '06_command_center')
    await page.close()
  }

  // ── 07  API Reference tab ────────────────────────────────────────────────
  {
    const page = await context.newPage()
    const q = { push: (_: string) => {} }
    await mockRoutes(page, q)
    await page.goto(BASE_URL)
    await page.getByRole('tab', { name: /接口|API|Reference/i }).click()
    await page.waitForTimeout(400)
    await shot(page, '07_api_reference')
    await page.close()
  }

  await browser.close()

  console.log(`\nDone — ${fs.readdirSync(OUT_DIR).filter(f => f.endsWith('.png')).length} screenshots saved to`)
  console.log(`  ${OUT_DIR}\n`)
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
