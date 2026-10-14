# Performance Benchmark Methodology

Reference material for the `performance-benchmarker` agent. Moved out of the
agent body to keep the role file compact.

## Core Web Vitals Targets

| Metric | Good | Needs Improvement | Poor |
|--------|------|--------------------|------|
| Largest Contentful Paint (LCP) | < 2.5s | 2.5s - 4s | > 4s |
| First Input Delay (FID) | < 100ms | 100ms - 300ms | > 300ms |
| Cumulative Layout Shift (CLS) | < 0.1 | 0.1 - 0.25 | > 0.25 |

Optimize with code splitting, lazy loading, CDN asset delivery, and Real User
Monitoring (RUM) data cross-checked against synthetic runs.

## k6 Load Test Skeleton

```javascript
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('errors');
const responseTimeTrend = new Trend('response_time');

export const options = {
  stages: [
    { duration: '2m', target: 10 },   // warm up
    { duration: '5m', target: 50 },   // normal load
    { duration: '2m', target: 100 },  // peak load
    { duration: '5m', target: 100 },  // sustained peak
    { duration: '2m', target: 200 },  // stress test
    { duration: '3m', target: 0 },    // cool down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],
    http_req_failed: ['rate<0.01'],
  },
};

export default function () {
  const res = http.get(`${__ENV.BASE_URL}/api/dashboard`);
  check(res, { 'status is 200': (r) => r.status === 200 });
  errorRate.add(res.status !== 200);
  responseTimeTrend.add(res.timings.duration);
  sleep(1);
}
```

## Testing Types

- **Load**: normal expected traffic, confirm SLA compliance
- **Stress**: push past capacity to find the breaking point and recovery behavior
- **Spike**: sudden traffic surge, confirm auto-scaling reacts in time
- **Endurance**: sustained load over hours, catch memory leaks and degradation

## Bottleneck Analysis Categories

- Database: slow queries, missing indexes, connection pool exhaustion
- Application layer: hot code paths, synchronous work that should be queued
- Infrastructure: server/network/CDN latency, cold starts
- Third-party services: external dependency latency and timeout budget

## Deliverable Skeleton

```markdown
# [System] Performance Analysis Report

## Test Results
Load / Stress / Spike / Endurance - key metrics per run

## Core Web Vitals
LCP / FID / CLS with optimization recommendations

## Bottleneck Analysis
Database / Application / Infrastructure / Third-party

## Optimization Recommendations
High-priority / Medium-priority / Long-term / Monitoring

## Verdict
MEETS / FAILS SLA, with reasoning; scalability assessment (ready / needs work for projected growth)
```

## Reporting Style

Always quantify: "p95 response time improved from 850ms to 180ms through query
optimization", not "performance improved". Pair every number with the
before/after baseline it was measured against.
