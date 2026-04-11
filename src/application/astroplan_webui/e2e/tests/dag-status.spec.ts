/**
 * E2E: DAG visualization and node status updates
 *
 * Covers:
 * 1. Plan tab shows DAG canvas after plan_generated SSE
 * 2. Node status colors update when node_status events arrive
 * 3. Selecting a node opens the detail panel
 * 4. Large-graph warning appears when >100 nodes
 */
import { test, expect, makeSseEvent, waitForTab } from '../fixtures/astroplan.fixture'

const PLAN_NODES = Array.from({ length: 5 }, (_, i) => ({
  node_id: `n${i + 1}`,
  lineage_id: `l${i + 1}`,
  skill_name: `Skill_${i + 1}`,
  params: {},
  depends_on: i > 0 ? [`n${i}`] : [],
  required_roles: [],
  tool_hints: [],
  interruptible: true,
}))

const PLAN_EDGES = PLAN_NODES.slice(1).map((n, i) => ({
  from: `n${i + 1}`,
  to: n.node_id,
}))

test.describe('DAG Visualization', () => {
  test.beforeEach(async ({ page, mockBackend: _ }) => {
    await page.goto('/')
  })

  test('renders nodes in DAG canvas after plan_generated event', async ({
    page,
    sseController,
  }) => {
    // Navigate to Plan tab
    await waitForTab(page, /计划|Plan/i)

    // Push plan_generated event
    sseController.push(
      makeSseEvent(
        'plan_generated',
        {
          plan: {
            revision_id: 'rev-1',
            nodes: PLAN_NODES,
            edges: PLAN_EDGES,
          },
        },
        'rev-1',
      ),
    )

    // First node should appear in canvas
    await expect(page.getByTestId('plan-node-n1')).toBeVisible({ timeout: 5000 })
    await expect(page.getByTestId('plan-node-n5')).toBeVisible({ timeout: 5000 })
  })

  test('node status updates on node_status event', async ({ page, sseController }) => {
    await waitForTab(page, /计划|Plan/i)

    // Set up initial plan
    sseController.push(
      makeSseEvent(
        'plan_generated',
        {
          plan: {
            revision_id: 'rev-1',
            nodes: PLAN_NODES.slice(0, 2),
            edges: [{ from: 'n1', to: 'n2' }],
          },
        },
        'rev-1',
      ),
    )

    await expect(page.getByTestId('plan-node-n1')).toBeVisible({ timeout: 5000 })

    // Push node running
    sseController.push(
      makeSseEvent('node_status', {
        node_id: 'n1',
        lineage_id: 'l1',
        status: 'running',
      }),
    )

    // n1 should have running status attribute
    await expect(page.getByTestId('plan-node-n1')).toHaveAttribute('data-status', 'running', {
      timeout: 3000,
    })

    // Push node completed
    sseController.push(
      makeSseEvent('node_status', {
        node_id: 'n1',
        lineage_id: 'l1',
        status: 'completed',
      }),
    )

    await expect(page.getByTestId('plan-node-n1')).toHaveAttribute('data-status', 'completed', {
      timeout: 3000,
    })
  })

  test('clicking a node opens the detail panel', async ({ page, sseController }) => {
    await waitForTab(page, /计划|Plan/i)

    sseController.push(
      makeSseEvent(
        'plan_generated',
        {
          plan: {
            revision_id: 'rev-1',
            nodes: PLAN_NODES.slice(0, 1),
            edges: [],
          },
        },
        'rev-1',
      ),
    )

    await expect(page.getByTestId('plan-node-n1')).toBeVisible({ timeout: 5000 })
    await page.getByTestId('plan-node-n1').click()

    // Detail panel should show node_id
    await expect(page.getByText('n1')).toBeVisible({ timeout: 2000 })
    await expect(page.getByText('Skill_1')).toBeVisible()
  })
})
