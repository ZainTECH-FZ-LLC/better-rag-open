/**
 * k6 smoke test — quick sanity check before full load test
 *
 * Runs 5 VUs for 30 seconds to verify all endpoints respond.
 *
 * Run:
 *   k6 run --env BASE_URL=https://rag.contoso.com tests/k6/smoke_test.js
 */

import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const API_TOKEN = __ENV.API_TOKEN || "test-token";

export const options = {
  vus: 5,
  duration: "30s",
  thresholds: {
    "http_req_duration": ["p(95)<5000"],
    "http_req_failed": ["rate<0.05"],
  },
};

const HEADERS = {
  "Authorization": `Bearer ${API_TOKEN}`,
  "Content-Type": "application/json",
};

export default function () {
  // Health check
  {
    const r = http.get(`${BASE_URL}/health`);
    check(r, { "health 200": (res) => res.status === 200 });
  }

  // Simple query
  {
    const r = http.post(
      `${BASE_URL}/api/v1/query`,
      JSON.stringify({ query: "What is the PTO policy?", stream: false }),
      { headers: HEADERS, timeout: "20s" }
    );
    check(r, {
      "query 200": (res) => res.status === 200,
      "query has body": (res) => res.body && res.body.length > 0,
    });
  }

  sleep(1);
}
