-- wrk cross-check script. Emits the same JSON shape as churngen so the two can
-- be diffed directly by nb/analyze.py.
done = function(summary, latency, requests)
   io.write(string.format(
     '{"tool":"wrk","duration_s":%.3f,"requests_ok":%d,"requests_err":%d,' ..
     '"achieved_rps":%.2f,"p50_ms":%.3f,"p95_ms":%.3f,"p99_ms":%.3f,' ..
     '"p999_ms":%.3f,"max_ms":%.3f}\n',
     summary.duration / 1e6,
     summary.requests,
     summary.errors.connect + summary.errors.read +
       summary.errors.write + summary.errors.status + summary.errors.timeout,
     summary.requests / (summary.duration / 1e6),
     latency:percentile(50.0) / 1000.0,
     latency:percentile(95.0) / 1000.0,
     latency:percentile(99.0) / 1000.0,
     latency:percentile(99.9) / 1000.0,
     latency.max / 1000.0))
end
