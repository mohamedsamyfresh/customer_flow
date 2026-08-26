# Customer Flow & Waiting Time Analytics Backend
## Comprehensive System Architecture, API Contract & Integration Documentation

---

## 1. Executive Summary & Architecture Overview

`customer-flow-backend` is an enterprise-grade backend service built with **FastAPI**, **SQLAlchemy (Async)**, **PostgreSQL**, **Redis**, and **gRPC**. It processes high-throughput Computer Vision / AI detections from surveillance feeds, tracks customer journeys and queue dwell times, computes occupancy and emotional sentiment analytics, and delivers real-time live updates to frontend dashboards via a secure, two-step WebSocket ticket architecture.

### End-to-End System Architecture

```mermaid
flowchart TD
    subgraph Ingestion["ML & Computer Vision Ingestion (Port 50051)"]
        ML["AI / CV Detection Models"] -->|gRPC DetectionStream| GRPC["gRPC Server (app.grpc.grpc_server)"]
        GRPC -->|Process Detections| DS["DetectionService (app.services)"]
    end

    subgraph Persistence["Persistence & Event Broker"]
        DS -->|1. Commit Transaction| DB[("PostgreSQL 15 DB\n(entries, waiting_times)")]
        DS -->|2. Post-Commit Trigger| Pub["Redis Publisher (app.websocket.publisher)"]
        Pub -->|PUBLISH customer_flow:events:*| Redis[("Redis 7 Broker\n- Tickets (Key-Value)\n- Pub/Sub Channels")]
    end

    subgraph BackendAPI["FastAPI Backend (Port 8000)"]
        AUTH["freshfamily-auth\n(JWT / JWKS RS256)"]
        REST["REST API Endpoints\n- /api/v1/entries\n- /api/v1/waiting-times\n- /api/v1/analytics/*\n- /api/v1/dashboard/ws/ticket"]
        WS["WebSocket Stream Endpoints\n- /ws/dashboard\n- /ws/dashboard/{branch_id}"]
    end

    subgraph FrontendClients["Frontend Clients (Browser / React / Next.js / Vue)"]
        FE_REST["REST Queries\n(Axios / TanStack Query)"]
        FE_WS["Live Dashboard Stream\n(WebSocket)"]
    end

    AUTH --> REST
    AUTH --> WS
    REST --> DB
    REST --> Redis
    WS --> Redis
    PubSubChannel["Redis Pub/Sub\ncustomer_flow:events:branch:{id}\ncustomer_flow:events:global"] --- Redis
    Redis -->|Redis GETDEL & Listen| WS

    FE_REST -->|1. Authenticated HTTP + Bearer JWT| REST
    REST -->|Mint Opaque Single-Use Ticket| FE_REST
    FE_WS -->|2. WS Connect ?ticket=<ticket>| WS
    WS -->|Initial Snapshot + Real-Time Events| FE_WS
```

### Key Ports & Container Services
| Service | Docker Container Name | Host Port | Internal Port | Description |
| :--- | :--- | :--- | :--- | :--- |
| **API Backend** | `customer_flow_api` | `8000` | `8000` | FastAPI REST & WebSocket server |
| **gRPC Server** | `customer_flow_grpc` | `50051` | `50051` | gRPC AI detection ingestion stream |
| **PostgreSQL DB** | `customer_flow_db` | `5433` | `5432` | Primary relational database |
| **Redis Broker** | `customer_flow_redis` | `6379` | `6379` | Tickets cache & Pub/Sub event broker |

---

## 2. Authentication, Authorization & Security Model

The system utilizes `freshfamily-auth` to secure all REST endpoints and WebSocket handshakes using industry-standard RS256 JWT tokens verified against Identity Provider JWKS endpoints.

### Roles & Permissions Inventory
Defined in `app/core/permissions.py`:

* **Permissions**:
  * `entries:read`: Permission to list customer entries and demographic records.
  * `waiting_times:read`: Permission to list queue sessions and wait duration records.
  * `analytics:read`: Permission to view dashboard KPIs, occupancy timelines, emotion transitions, and mint WebSocket streaming tickets.
  * `admin:all`: Superadmin full access.
* **Roles**:
  * `admin`: Administrator.
  * `manager`: Branch or store manager.
  * `viewer`: Read-only reporting access.
  * `admin-dev`: Role permitted for authorization bypass in `AUTH_APP_ENV=development` mode.

---

## 3. Real-Time WebSocket Architecture (Two-Step Ticket Flow)

### Why Two-Step Ticket Authentication?
Browser WebSocket APIs (`new WebSocket(url)`) do not allow custom `Authorization: Bearer` headers during connection establishment. Passing raw JWTs in query parameters exposes tokens to browser histories, reverse proxy logs, and referrer headers.

The two-step ticket flow eliminates this vulnerability by minting a single-use opaque ticket over standard authenticated HTTPS before establishing the WebSocket connection.

### WebSocket Ticket Authentication Protocol

```mermaid
sequenceDiagram
    autonumber
    actor Client as Frontend Client
    participant API as FastAPI REST API
    participant Redis as Redis Broker
    participant WS as WebSocket Handler
    participant DB as PostgreSQL DB
    participant PubSub as Redis Pub/Sub

    Note over Client,API: Step 1: Mint Opaque Ticket over Authenticated HTTP
    Client->>API: POST /api/v1/dashboard/{branch_id}/ws/ticket<br/>Authorization: Bearer <JWT>
    API->>API: 1. Validate JWT signature & claims (freshfamily-auth)
    API->>API: 2. Verify 'analytics:read' permission
    API->>API: 3. Verify branch identifier eligibility
    API->>API: 4. Generate cryptographically random token (secrets.token_urlsafe(32))
    API->>Redis: 5. SET customer_flow:ws:ticket:<ticket> -> {"resource_id": "branch-1", ...} (EX: 30s)
    API-->>Client: HTTP 201 Created {"ticket": "<opaque-ticket>"}

    Note over Client,WS: Step 2: Establish WebSocket Connection
    Client->>WS: WS /ws/dashboard/{branch_id}?ticket=<ticket><br/>Origin: http://localhost:3000
    WS->>WS: 1. Validate Origin header against WEBSOCKET_ALLOWED_ORIGINS
    WS->>Redis: 2. GETDEL customer_flow:ws:ticket:<ticket> (Atomic single-use)
    alt Ticket missing, expired, invalid, or replayed
        WS-->>Client: WS Close 1008 (Policy Violation)
    else Ticket resource mismatch (e.g. ticket for branch-A, requested branch-B)
        WS-->>Client: WS Close 1008 (Policy Violation)
    else Ticket Valid & Authorized
        WS->>DB: 3. Query initial snapshot (short-lived DB session)
        DB-->>WS: DashboardMetrics state
        WS->>WS: 4. Close & release DB session back to pool
        WS->>Client: 5. Accept WebSocket & Send initial snapshot JSON
        WS->>PubSub: 6. SUBSCRIBE customer_flow:events:branch:{branch_id}
        
        loop Real-Time Streaming
            PubSub-->>WS: Live event published
            WS-->>Client: Forward update JSON frame
        end
    end
```

### WebSocket RFC 6455 Close Codes
* `1008 (Policy Violation)`: Used for all pre-acceptance rejections (missing ticket, invalid ticket, expired ticket, reused ticket, unauthorized resource mismatch, disallowed Origin).
* `1013 (Try Again Later)`: Used for post-acceptance broker disconnects or unexpected infrastructure failures.
* `1000 (Normal Closure)`: Used when client component unmounts or disconnects intentionally.

---

## 4. Complete REST API Specification & Contract

**Base URL**: `http://localhost:8000` (or configured API domain)  
**Standard Headers**:
```http
Authorization: Bearer <JWT_ACCESS_TOKEN>
Content-Type: application/json
```

---

### 4.1 Health Probe
* **Endpoint**: `GET /health`
* **Access**: Public (No JWT required)
* **Response (HTTP 200 OK)**:
```json
{
  "status": "ok"
}
```

---

### 4.2 Mint WebSocket Streaming Ticket
* **Endpoint**: `POST /api/v1/dashboard/ws/ticket` (Global)
* **Endpoint**: `POST /api/v1/dashboard/{branch_id}/ws/ticket` (Branch-specific)
* **Aliases**: `POST /api/v1/branches/{branch_id}/ws/ticket`, `POST /dashboard/{branch_id}/ws/ticket`
* **Access**: Protected (`analytics:read` permission)
* **Path Parameters**:
  * `branch_id` *(optional)*: Branch identifier to scope real-time updates.
* **Request Body** *(optional)*:
```json
{
  "branch_id": "branch-1"
}
```
* **Response (HTTP 201 Created)**:
```json
{
  "ticket": "DB1ApzK9nH_e7z0g3FhY8u-2qWxNvMj1K7b..."
}
```
* **Error Responses**:
  * `401 Unauthorized`: Missing or expired JWT.
  * `403 Forbidden`: Token lacks `analytics:read` permission.
  * `400 Bad Request`: Invalid or oversized branch identifier.

---

### 4.3 Live Dashboard KPI Metrics & Summary
* **Endpoint**: `GET /api/dashboard/metrics`
* **Endpoint**: `GET /api/v1/analytics/summary`
* **Access**: Protected (`analytics:read` permission)
* **Query Parameters**:
  * `branch_id` *(optional, string)*: Filter by branch identifier.
  * `date` *(optional, YYYY-MM-DD)*: Target date (defaults to current UTC date).
  * `bucket` *(optional, string, default: "1h")*: Time bucket for peak occupancy (`5m`, `15m`, `30m`, `1h`).
* **Response (HTTP 200 OK)**:
```json
{
  "people_in_store": 8,
  "total_entries_today": 142,
  "total_exits_today": 134,
  "emotion_transitions": {
    "natural_to_angry": 2,
    "angry_to_natural": 5,
    "natural_to_natural": 120,
    "angry_to_angry": 7
  },
  "longest_stay": {
    "entry_time": "2026-08-26T09:15:00",
    "exit_time": "2026-08-26T10:45:00",
    "duration_seconds": 5400.0,
    "customer_id": "8c5e2194-d2e8-4687-9bb3-5a02e6eec285",
    "entry_count": 104,
    "branch_id": "branch-1",
    "camera_id": "cam-entry-north"
  },
  "highest_occupancy_period": {
    "start": "2026-08-26T13:00:00",
    "end": "2026-08-26T14:00:00",
    "occupancy": 32
  }
}
```

---

### 4.4 Occupancy Timeline & Peak Traffic Analysis
* **Endpoint**: `GET /api/v1/analytics/occupancy`
* **Access**: Protected (`analytics:read` permission)
* **Query Parameters**:
  * `branch_id` *(optional, string)*: Filter by branch identifier.
  * `date` *(optional, string)*: Target date in `YYYY-MM-DD` format.
  * `bucket` *(optional, string, default: "1h")*: Interval bucket (`5m`, `15m`, `30m`, `1h`).
* **Response (HTTP 200 OK)**:
```json
{
  "bucket": "1h",
  "date": "2026-08-26",
  "branch_id": "branch-1",
  "peak_period": {
    "start": "2026-08-26T14:00:00",
    "end": "2026-08-26T15:00:00",
    "occupancy": 28
  },
  "timeline": [
    {
      "start": "2026-08-26T08:00:00",
      "end": "2026-08-26T09:00:00",
      "occupancy": 6
    },
    {
      "start": "2026-08-26T09:00:00",
      "end": "2026-08-26T10:00:00",
      "occupancy": 15
    },
    {
      "start": "2026-08-26T14:00:00",
      "end": "2026-08-26T15:00:00",
      "occupancy": 28
    }
  ]
}
```

---

### 4.5 Customer Emotion Sentiment Transitions
* **Endpoint**: `GET /api/v1/analytics/emotions`
* **Access**: Protected (`analytics:read` permission)
* **Query Parameters**:
  * `branch_id` *(optional, string)*: Branch identifier.
  * `date` *(optional, string)*: Target date in `YYYY-MM-DD` format.
* **Response (HTTP 200 OK)**:
```json
{
  "natural_to_angry": 2,
  "angry_to_natural": 6,
  "natural_to_natural": 115,
  "angry_to_angry": 4
}
```

---

### 4.6 Paginated Customer Entries / Journey Logs
* **Endpoint**: `GET /api/v1/entries`
* **Access**: Protected (`entries:read` permission)
* **Query Parameters**:
  * `page` *(default: 1, ge: 1)*: Page number.
  * `limit` *(default: 20, ge: 1, le: 100)*: Items per page.
  * `status` *(default: "all")*: `"inside"` | `"exited"` | `"all"`.
  * `gender` *(optional)*: `"Male"` | `"Female"`.
  * `branch_id` *(optional)*: Filter by branch ID.
  * `date_from` *(optional, ISO 8601)*: Start timestamp filter.
  * `date_to` *(optional, ISO 8601)*: End timestamp filter.
* **Response (HTTP 200 OK)**:
```json
{
  "total": 150,
  "page": 1,
  "limit": 20,
  "data": [
    {
      "uuid": "8c5e2194-d2e8-4687-9bb3-5a02e6eec285",
      "entry_time": "2026-08-26T08:30:15",
      "entry_count": 101,
      "age_class": "26-35",
      "gender": "Female",
      "gender_conf": 0.96,
      "enter_emotion": "natural",
      "enter_emotion_conf": 0.89,
      "entry_face_box": "[0.12, 0.35, 0.45, 0.65]",
      "entry_face_vector": "[0.015, -0.042, 0.187, ...]",
      "exit_time": "2026-08-26T08:47:30",
      "exit_count": 98,
      "exit_emotion": "happy",
      "exit_emotion_conf": 0.92,
      "exit_face_box": "[0.14, 0.36, 0.46, 0.67]",
      "exit_face_vector": "[0.016, -0.040, 0.185, ...]",
      "face_match_score": 0.94,
      "branch_id": "branch-1",
      "camera_id": "cam-north"
    }
  ]
}
```

---

### 4.7 Paginated Queue Waiting Times
* **Endpoint**: `GET /api/v1/waiting-times`
* **Access**: Protected (`waiting_times:read` permission)
* **Query Parameters**:
  * `page` *(default: 1, ge: 1)*: Page number.
  * `limit` *(default: 20, ge: 1, le: 100)*: Items per page.
  * `min_duration_s` *(optional, float)*: Filter sessions exceeding duration threshold in seconds.
* **Response (HTTP 200 OK)**:
```json
{
  "total": 85,
  "page": 1,
  "limit": 20,
  "data": [
    {
      "uuid": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "id": 12,
      "entry_frame": 1500,
      "exit_frame": 4200,
      "entry_time": "09:15:00",
      "exit_time": "09:18:45",
      "duration": "00:03:45",
      "duration_s": 225.0
    }
  ]
}
```

---

## 5. Complete WebSocket Real-Time Event Specification

### 5.1 Connection Endpoints
* **Global Dashboard Stream**: `WS /ws/dashboard?ticket=<TICKET>`
* **Branch-Specific Stream**: `WS /ws/dashboard/{branch_id}?ticket=<TICKET>`

### 5.2 Handshake Message Sequence
1. **Initial Snapshot (`dashboard_snapshot`)**:
   Sent by server immediately upon accepting the connection:
```json
{
  "type": "dashboard_snapshot",
  "timestamp": "2026-08-26T12:50:00Z",
  "branch_id": "branch-1",
  "data": {
    "people_in_store": 8,
    "total_entries_today": 142,
    "total_exits_today": 134,
    "emotion_transitions": {
      "natural_to_angry": 2,
      "angry_to_natural": 5,
      "natural_to_natural": 120,
      "angry_to_angry": 7
    },
    "longest_stay": {
      "entry_time": "2026-08-26T09:15:00",
      "exit_time": "2026-08-26T10:45:00",
      "duration_seconds": 5400.0,
      "customer_id": "8c5e2194-d2e8-4687-9bb3-5a02e6eec285",
      "entry_count": 104,
      "branch_id": "branch-1",
      "camera_id": "cam-entry-north"
    },
    "highest_occupancy_period": {
      "start": "2026-08-26T13:00:00",
      "end": "2026-08-26T14:00:00",
      "occupancy": 32
    }
  }
}
```

2. **Live Real-Time Update Event (`dashboard_update`)**:
   Broadcasted when an ML detection is stored in PostgreSQL:
```json
{
  "type": "dashboard_update",
  "timestamp": "2026-08-26T12:50:02Z",
  "branch_id": "branch-1",
  "data": {
    "people_in_store": 9,
    "total_entries_today": 143,
    "total_exits_today": 134,
    "emotion_transitions": {
      "natural_to_angry": 2,
      "angry_to_natural": 5,
      "natural_to_natural": 121,
      "angry_to_angry": 7
    },
    "longest_stay": { ... },
    "highest_occupancy_period": { ... }
  }
}
```

3. **Client Heartbeat (Ping / Pong)**:
   * Client sends plain text frame: `"ping"`
   * Server immediately responds: `"pong"`

---

## 6. Frontend TypeScript Interfaces & Integration Guide

### 6.1 TypeScript Definitions (`types/analytics.ts`)

```typescript
export interface EmotionTransitions {
  natural_to_angry: number;
  angry_to_natural: number;
  natural_to_natural: number;
  angry_to_angry: number;
}

export interface LongestStay {
  entry_time: string | null;
  exit_time: string | null;
  duration_seconds: number | null;
  customer_id: string | null;
  entry_count: number | null;
  branch_id: string | null;
  camera_id: string | null;
}

export interface HighestOccupancyPeriod {
  start: string | null;
  end: string | null;
  occupancy: number;
}

export interface DashboardMetrics {
  people_in_store: number;
  total_entries_today: number;
  total_exits_today: number;
  emotion_transitions: EmotionTransitions;
  longest_stay: LongestStay | null;
  highest_occupancy_period: HighestOccupancyPeriod | null;
}

export interface DashboardEvent {
  type: 'dashboard_snapshot' | 'dashboard_update';
  timestamp: string;
  branch_id: string | null;
  data: DashboardMetrics;
}

export interface CustomerEntry {
  uuid: string;
  entry_time: string | null;
  entry_count: number | null;
  age_class: string | null;
  gender: 'Male' | 'Female' | string | null;
  gender_conf: number | null;
  enter_emotion: string | null;
  enter_emotion_conf: number | null;
  entry_face_box?: string | null;
  entry_face_vector?: string | null;
  exit_time: string | null;
  exit_count: number | null;
  exit_emotion: string | null;
  exit_emotion_conf: number | null;
  face_match_score: number | null;
  branch_id: string | null;
  camera_id: string | null;
}

export interface PaginatedResponse<T> {
  total: number;
  page: number;
  limit: number;
  data: T[];
}

export interface WaitingTimeSession {
  uuid: string;
  id: number | null;
  entry_frame: number | null;
  exit_frame: number | null;
  entry_time: string | null;
  exit_time: string | null;
  duration: string | null;
  duration_s: number | null;
}
```

### 6.2 Axios HTTP API Client (`lib/api.ts`)

```typescript
import axios from 'axios';

export const apiClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',
  headers: {
    'Content-Type': 'application/json',
  },
});

// Interceptor to attach JWT token to all HTTP requests
apiClient.interceptors.request.use((config) => {
  const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});
```

### 6.3 Production-Ready Real-Time WebSocket Hook (`hooks/useRealtimeDashboard.ts`)

```typescript
import { useEffect, useRef, useState } from 'react';
import { apiClient } from '@/lib/api';
import { DashboardEvent, DashboardMetrics } from '@/types/analytics';

/**
 * Production-ready WebSocket hook implementing Two-Step Ticket Authentication:
 * 1. Mints an opaque short-lived ticket via authenticated POST /ws/ticket.
 * 2. Connects to WebSocket endpoint with ticket parameter.
 * 3. Handles initial snapshot, real-time live events, and pings.
 * 4. Automatically reconnects on network disconnects by minting a FRESH ticket.
 */
export function useRealtimeDashboard(branchId?: string) {
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    let isMounted = true;

    async function establishConnection() {
      try {
        setError(null);

        // Step 1: Mint a fresh single-use ticket via authenticated HTTP
        const ticketPath = branchId
          ? `/api/v1/dashboard/${branchId}/ws/ticket`
          : `/api/v1/dashboard/ws/ticket`;

        const { data } = await apiClient.post<{ ticket: string }>(ticketPath);
        if (!isMounted) return;

        // Step 2: Formulate WebSocket URL
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsBase = process.env.NEXT_PUBLIC_WS_URL || `${wsProtocol}//${window.location.host}`;
        const wsPath = branchId ? `/ws/dashboard/${branchId}` : `/ws/dashboard`;
        const wsUrl = `${wsBase}${wsPath}?ticket=${encodeURIComponent(data.ticket)}`;

        const socket = new WebSocket(wsUrl);
        wsRef.current = socket;

        socket.onopen = () => {
          if (isMounted) {
            setIsConnected(true);
            setError(null);
          }
        };

        socket.onmessage = (event) => {
          try {
            const payload: DashboardEvent = JSON.parse(event.data);
            if (payload && payload.data && isMounted) {
              setMetrics(payload.data);
            }
          } catch (parseErr) {
            console.error('Failed to parse WebSocket message frame:', parseErr);
          }
        };

        socket.onerror = (evt) => {
          console.warn('WebSocket connection encountered an error:', evt);
        };

        socket.onclose = (event) => {
          if (!isMounted) return;
          setIsConnected(false);
          wsRef.current = null;

          // If closed abnormally (e.g. 1013 Try Again Later or network loss),
          // reconnect by requesting a BRAND NEW ticket
          if (event.code !== 1000) {
            console.info(`WebSocket closed (code: ${event.code}). Reconnecting in 3s with a fresh ticket...`);
            reconnectTimerRef.current = setTimeout(establishConnection, 3000);
          }
        };
      } catch (err: any) {
        if (isMounted) {
          setError(err?.response?.data?.detail || 'Failed to authenticate WebSocket ticket');
          reconnectTimerRef.current = setTimeout(establishConnection, 5000);
        }
      }
    }

    establishConnection();

    return () => {
      isMounted = false;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (wsRef.current) {
        wsRef.current.close(1000, 'Component unmounted');
        wsRef.current = null;
      }
    };
  }, [branchId]);

  return { metrics, isConnected, error };
}
```

---

## 7. Configuration & Environment Variables

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | `string` | *Required* | SQLAlchemy async PostgreSQL DSN (`postgresql+asyncpg://user:pass@host:5432/db`) |
| `REDIS_URL` | `string` | `redis://redis:6379/0` | Redis connection URL |
| `REDIS_KEY_NAMESPACE` | `string` | `customer_flow` | Prefix for Redis ticket keys and Pub/Sub channels |
| `WEBSOCKET_TICKET_TTL_SECONDS` | `int` | `30` | Single-use opaque ticket expiration in seconds |
| `WEBSOCKET_ALLOWED_ORIGINS` | `list[str]` | `["http://localhost:3000", ...]` | Allowed Origin headers for WebSocket handshakes |
| `JWKS_URL` | `string` | `None` | URL to Identity Provider JWKS endpoint |
| `AUTH_ISSUER` | `string` | `None` | Expected JWT `iss` claim |
| `AUTH_AUDIENCE` | `string` | `None` | Expected JWT `aud` claim |
| `AUTH_APP_ENV` | `string` | `production` | Environment mode (`production` or `development`) |
| `AUTH_DEV_BYPASS_ROLE` | `string` | `admin-dev` | Role allowed to bypass permission gates in development mode |
| `AUTH_LEEWAY` | `float` | `30.0` | Clock skew tolerance in seconds |

---

## 8. Testing & Verification Guide

### 8.1 Running Test Suite in Docker
```bash
docker compose exec api pytest -v
```

### 8.2 End-to-End Verification with `curl` and `wscat`

#### 1. Mint Single-Use WebSocket Ticket
```bash
curl -X POST http://localhost:8000/api/v1/dashboard/branch-1/ws/ticket \
  -H "Authorization: Bearer <YOUR_JWT_TOKEN>" \
  -H "Content-Type: application/json"
```
**Response (HTTP 201 Created):**
```json
{
  "ticket": "DB1ApzK9nH_e7z0g3FhY8u-2qWxNvMj1K7b..."
}
```

#### 2. Establish WebSocket Stream
```bash
wscat -c "ws://localhost:8000/ws/dashboard/branch-1?ticket=DB1ApzK9nH_e7z0g3FhY8u-2qWxNvMj1K7b..." \
  --header "Origin: http://localhost:3000"
```
* **Step A**: Server immediately returns `dashboard_snapshot` with initial metrics.
* **Step B**: Type `ping` and press enter -> Server replies `pong`.
* **Step C**: When ML detections arrive, server automatically pushes `dashboard_update` frames.

#### 3. Verify Ticket Replay Immunity
Attempt to connect a second time with the already-consumed ticket:
```bash
wscat -c "ws://localhost:8000/ws/dashboard/branch-1?ticket=DB1ApzK9nH_e7z0g3FhY8u-2qWxNvMj1K7b..." \
  --header "Origin: http://localhost:3000"
```
* **Result**: Closed immediately with **`1008 (Policy Violation: Invalid or expired ticket)`**.
