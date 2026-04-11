// Base URL for the FastAPI backend. In dev, Vite proxies these paths.
export const BACKEND_BASE = ''

export const SSE_URL = `${BACKEND_BASE}/events`
export const SNAPSHOT_URL = `${BACKEND_BASE}/plan/snapshot`
export const HEALTH_URL = `${BACKEND_BASE}/health`
export const LABS_URL = `${BACKEND_BASE}/labs`
export const MISSION_START_URL = `${BACKEND_BASE}/mission/start`
export const MISSION_STOP_URL = `${BACKEND_BASE}/mission/stop`
export const HITL_RESPOND_URL = `${BACKEND_BASE}/hitl/respond`
export const COMMAND_INJECT_URL = `${BACKEND_BASE}/command/inject`

// DAG performance thresholds
export const DAG_LARGE_GRAPH_THRESHOLD = 100
export const COMMAND_QUEUE_CAP = 10

// Reconnect backoff schedule (seconds)
export const RECONNECT_BACKOFF = [1, 2, 4, 8, 16, 30]

// Layout
export const MIN_CANVAS_WIDTH = 900
export const MIN_CANVAS_HEIGHT = 600
