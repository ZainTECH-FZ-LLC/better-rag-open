/**
 * k6 load test — BetterRAG 800-concurrent-user simulation
 *
 * Stages:
 *   1. Ramp up to 800 VUs over 5 minutes
 *   2. Sustain 800 VUs for 15 minutes
 *   3. Ramp down to 0 over 3 minutes
 *
 * Run:
 *   k6 run --env BASE_URL=https://rag.contoso.com tests/k6/load_test.js
 */

import http from "k6/http";
import { check, sleep, group } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";
import { SharedArray } from "k6/data";

// ── Configuration ─────────────────────────────────────────────────────────────

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const API_TOKEN = __ENV.API_TOKEN || "test-token";

export const options = {
  stages: [
    { duration: "2m",  target: 100 },   // warm-up
    { duration: "3m",  target: 400 },   // ramp up
    { duration: "5m",  target: 800 },   // ramp to peak
    { duration: "15m", target: 800 },   // sustain peak
    { duration: "3m",  target: 200 },   // ramp down
    { duration: "2m",  target: 0 },     // cool down
  ],
  thresholds: {
    // p95 latency under 8s (plan SLA)
    "http_req_duration{route:query}": ["p(95)<8000"],
    // p99 latency under 15s
    "http_req_duration{route:query}": ["p(99)<15000"],
    // error rate below 1%
    "http_req_failed": ["rate<0.01"],
    // query success rate above 99%
    "query_success_rate": ["rate>0.99"],
  },
};

// ── Custom metrics ────────────────────────────────────────────────────────────

const querySuccessRate = new Rate("query_success_rate");
const queryDuration = new Trend("query_duration_ms", true);
const docGenCount = new Counter("doc_gen_requests");
const searchCount = new Counter("search_requests");

// ── Test data ─────────────────────────────────────────────────────────────────

const QUERIES = new SharedArray("queries", () => [
  // HR queries
  { query: "What is the parental leave policy?", department: "hr" },
  { query: "How many PTO days do I get?", department: "hr" },
  { query: "What is the remote work policy?", department: "hr" },
  { query: "What are the performance review criteria?", department: "hr" },
  // Finance queries
  { query: "What was Q3 revenue?", department: "finance" },
  { query: "What is the expense reimbursement limit?", department: "finance" },
  { query: "Show me the budget variance for marketing", department: "finance" },
  { query: "What is the vendor payment terms policy?", department: "finance" },
  // Sales queries
  { query: "What is our current pipeline value?", department: "sales" },
  { query: "What are the Q4 quota targets?", department: "sales" },
  { query: "Who are our top accounts by ARR?", department: "sales" },
  // Marketing queries
  { query: "What was the ROI on the Q3 campaign?", department: "marketing" },
  { query: "What are our brand guidelines?", department: "marketing" },
  // General queries
  { query: "What are the company values?", department: "general" },
  { query: "How do I request IT support?", department: "general" },
]);

const USERS = new SharedArray("users", () => {
  // Simulate 50 distinct users cycling across VUs
  const users = [];
  for (let i = 1; i <= 50; i++) {
    users.push({ user_id: `test_user_${i}`, token: API_TOKEN });
  }
  return users;
});

// ── Helpers ───────────────────────────────────────────────────────────────────

function randomItem(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function authHeaders(user) {
  return {
    "Authorization": `Bearer ${user.token}`,
    "Content-Type": "application/json",
    "X-User-ID": user.user_id,
  };
}

// ── Scenarios ─────────────────────────────────────────────────────────────────

function doQuery(user) {
  const q = randomItem(QUERIES);
  const payload = JSON.stringify({
    query: q.query,
    department_hint: q.department,
    stream: false,
  });

  const start = Date.now();
  const res = http.post(`${BASE_URL}/api/v1/query`, payload, {
    headers: authHeaders(user),
    tags: { route: "query" },
    timeout: "30s",
  });
  queryDuration.add(Date.now() - start);

  const ok = check(res, {
    "query status 200": (r) => r.status === 200,
    "query has answer": (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.answer && body.answer.length > 10;
      } catch { return false; }
    },
    "query has citations": (r) => {
      try {
        const body = JSON.parse(r.body);
        return Array.isArray(body.citations);
      } catch { return false; }
    },
  });

  querySuccessRate.add(ok);
  searchCount.add(1);
}

function doDocGen(user) {
  const payload = JSON.stringify({
    query: "Create a summary presentation of our Q3 performance",
    document_type: "pptx",
    department: "finance",
  });

  const res = http.post(`${BASE_URL}/api/v1/query`, payload, {
    headers: authHeaders(user),
    tags: { route: "docgen" },
    timeout: "60s",
  });

  check(res, {
    "docgen status 200": (r) => r.status === 200,
    "docgen has filename": (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.documents && body.documents.length > 0;
      } catch { return false; }
    },
  });

  docGenCount.add(1);
}

function doHealthCheck() {
  const res = http.get(`${BASE_URL}/health`, {
    tags: { route: "health" },
  });
  check(res, { "health 200": (r) => r.status === 200 });
}

// ── Main VU function ──────────────────────────────────────────────────────────

export default function () {
  const user = randomItem(USERS);
  const rand = Math.random();

  if (rand < 0.02) {
    // 2% doc gen requests
    group("document_generation", () => {
      doDocGen(user);
    });
    sleep(3 + Math.random() * 5);
  } else if (rand < 0.04) {
    // 2% health checks
    doHealthCheck();
    sleep(1);
  } else {
    // 96% standard queries
    group("query", () => {
      doQuery(user);
    });
    sleep(1 + Math.random() * 3);
  }
}

// ── Setup / Teardown ──────────────────────────────────────────────────────────

export function setup() {
  // Verify the API is reachable before starting load
  const res = http.get(`${BASE_URL}/health`);
  if (res.status !== 200) {
    throw new Error(`API health check failed: ${res.status} — ${BASE_URL}/health`);
  }
  console.log(`Load test starting against: ${BASE_URL}`);
  return { base_url: BASE_URL };
}

export function teardown(data) {
  console.log(`Load test complete. Target: ${data.base_url}`);
}
