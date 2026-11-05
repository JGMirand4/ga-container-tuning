import http from 'k6/http';
import { check } from 'k6';

// MODE=rate: open model, fixed arrival rate (measures latency)
// MODE=sat : closed model, many VUs (measures max throughput)
const mode = __ENV.MODE || 'rate';
const rate = parseInt(__ENV.RATE || '50');
const dur = __ENV.DURATION || '30s';
const vus = parseInt(__ENV.VUS || '64');

export const options = {
  summaryTrendStats: ['avg', 'p(95)', 'p(99)'],
  scenarios: {
    main:
      mode === 'rate'
        ? { executor: 'constant-arrival-rate', rate, timeUnit: '1s', duration: dur, preAllocatedVUs: 50, maxVUs: 500 }
        : { executor: 'constant-vus', vus, duration: dur },
  },
};

export default function () {
  const r = http.get(`${__ENV.TARGET}/${__ENV.ENDPOINT}`, { timeout: '10s' });
  check(r, { ok: (x) => x.status === 200 });
}

export function handleSummary(d) {
  const m = d.metrics;
  const dropped = m.dropped_iterations ? m.dropped_iterations.values.count : 0;
  const total = m.http_reqs.values.count + dropped;
  const out = {
    p95: m.http_req_duration.values['p(95)'],
    p99: m.http_req_duration.values['p(99)'],
    rps: m.http_reqs.values.rate,
    fail: m.http_req_failed.values.rate,
    dropped_frac: total > 0 ? dropped / total : 0,
  };
  return { '/out/summary.json': JSON.stringify(out) };
}
