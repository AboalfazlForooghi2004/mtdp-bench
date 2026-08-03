#!/usr/bin/env python3
"""MTDP-Bench connection-churn / load generator.

This is the component that actually stresses the datapath. It drives three
distinct pressures that the paper treats separately:

  1. keep-alive request load  (T-Web, T-Latency)  -> per-request tail latency
  2. connection churn         (T-Churn)           -> conntrack insert/evict rate
  3. bulk transfer            (T-Bulk)            -> softirq / bytes pressure

Every connection is opened from this process, so the conntrack pressure we
report is pressure we caused, not ambient cluster noise.

Latency is recorded as raw samples in a reservoir, not as a pre-aggregated
average: the paper's headline metrics are p99s, and you cannot recover a p99
from averages. The reservoir is bounded so a 600 s run has bounded memory.

Output: one JSON object on stdout at exit, plus optional periodic line-protocol
to InfluxDB.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import signal
import sys
import time
from dataclasses import dataclass, field

RESERVOIR_MAX = 200_000


@dataclass
class Stats:
    """Per-tenant counters. Plain ints/floats so it JSON-serialises directly."""

    tenant: str = ""
    archetype: str = ""
    role: str = "neutral"
    requests_ok: int = 0
    requests_err: int = 0
    conns_opened: int = 0
    conns_failed: int = 0
    conn_errors: dict = field(default_factory=dict)
    bytes_read: int = 0
    latencies_ms: list = field(default_factory=list)
    conn_setup_ms: list = field(default_factory=list)
    started_at: float = 0.0
    ended_at: float = 0.0

    def record_latency(self, ms: float) -> None:
        # Reservoir sampling keeps the sample unbiased once we hit the cap.
        r = self.latencies_ms
        if len(r) < RESERVOIR_MAX:
            r.append(ms)
        else:
            j = random.randrange(self.requests_ok + 1)
            if j < RESERVOIR_MAX:
                r[j] = ms

    def note_conn_error(self, exc: BaseException) -> None:
        self.conns_failed += 1
        key = type(exc).__name__
        if isinstance(exc, OSError) and exc.errno is not None:
            key = f"{key}:{exc.errno}"
        self.conn_errors[key] = self.conn_errors.get(key, 0) + 1


async def _read_http_response(reader: asyncio.StreamReader) -> int:
    """Read one HTTP/1.1 response, honouring Content-Length. Returns body size."""
    header = await reader.readuntil(b"\r\n\r\n")
    length = 0
    for line in header.split(b"\r\n"):
        if line[:15].lower() == b"content-length:":
            length = int(line.split(b":", 1)[1])
            break
    if length:
        await reader.readexactly(length)
    return length


async def keepalive_worker(host, port, rps, stop, st: Stats) -> None:
    """One long-lived connection issuing requests at a fixed rate.

    Uses an absolute schedule rather than sleep(1/rps) so that a slow response
    does not silently reduce the offered load. This matters: if the generator
    backs off under stress, the datapath under test looks better than it is
    (closed-loop bias). We keep the load open-loop and let latency grow.
    """
    interval = 1.0 / rps if rps > 0 else 0.0
    req = (f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: keep-alive\r\n\r\n"
           ).encode()
    reader = writer = None
    next_at = time.perf_counter()
    try:
        while not stop.is_set():
            if writer is None:
                t0 = time.perf_counter()
                try:
                    reader, writer = await asyncio.open_connection(host, port)
                except (OSError, asyncio.TimeoutError) as e:
                    st.note_conn_error(e)
                    await asyncio.sleep(0.05)
                    continue
                st.conns_opened += 1
                st.conn_setup_ms.append((time.perf_counter() - t0) * 1e3)

            if interval:
                next_at += interval
                delay = next_at - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)
                elif delay < -1.0:
                    # We are more than 1 s behind schedule; resynchronise so the
                    # generator does not enter an unbounded catch-up spiral.
                    next_at = time.perf_counter()

            t0 = time.perf_counter()
            try:
                writer.write(req)
                await writer.drain()
                n = await _read_http_response(reader)
            except (OSError, asyncio.IncompleteReadError,
                    asyncio.LimitOverrunError) as e:
                st.requests_err += 1
                st.note_conn_error(e)
                try:
                    writer.close()
                except Exception:
                    pass
                reader = writer = None
                continue
            st.record_latency((time.perf_counter() - t0) * 1e3)
            st.requests_ok += 1
            st.bytes_read += n
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass


async def churn_worker(host, port, rate, active_s, stop, st: Stats) -> None:
    """Open a connection, do one request, close it. This is the conntrack bomb.

    Each completed cycle leaves a TIME_WAIT entry behind, which is exactly the
    pressure the paper's conntrack-utilisation and eviction-rate results are
    about. We deliberately do NOT set SO_LINGER 0: forcing RST would skip
    TIME_WAIT and erase the effect we are trying to measure.
    """
    interval = 1.0 / rate if rate > 0 else 0.0
    req = (f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
           ).encode()
    next_at = time.perf_counter()
    while not stop.is_set():
        if interval:
            next_at += interval
            delay = next_at - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            elif delay < -1.0:
                next_at = time.perf_counter()
        t0 = time.perf_counter()
        try:
            reader, writer = await asyncio.open_connection(host, port)
        except (OSError, asyncio.TimeoutError) as e:
            st.note_conn_error(e)
            continue
        st.conns_opened += 1
        st.conn_setup_ms.append((time.perf_counter() - t0) * 1e3)
        try:
            writer.write(req)
            await writer.drain()
            n = await _read_http_response(reader)
            st.record_latency((time.perf_counter() - t0) * 1e3)
            st.requests_ok += 1
            st.bytes_read += n
        except (OSError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError) as e:
            st.requests_err += 1
            st.note_conn_error(e)
        finally:
            try:
                writer.close()
            except Exception:
                pass
        if active_s:
            await asyncio.sleep(active_s)


async def bulk_worker(host, port, target_gbps, stop, st: Stats) -> None:
    """Sustained transfer, rate-limited to a target Gb/s.

    The aggressor is a *constant* bit rate, not a share of capacity. An
    aggressor that scales with tenant count confounds density with pressure,
    which was a real defect in an earlier version of this harness.
    """
    req = (f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: keep-alive\r\n\r\n"
           ).encode()
    target_bps = target_gbps * 1e9 / 8.0
    reader = writer = None
    t_start = time.perf_counter()
    sent = 0
    try:
        while not stop.is_set():
            if writer is None:
                try:
                    reader, writer = await asyncio.open_connection(host, port)
                except (OSError, asyncio.TimeoutError) as e:
                    st.note_conn_error(e)
                    await asyncio.sleep(0.05)
                    continue
                st.conns_opened += 1
            try:
                writer.write(req)
                await writer.drain()
                n = await _read_http_response(reader)
            except (OSError, asyncio.IncompleteReadError,
                    asyncio.LimitOverrunError) as e:
                st.requests_err += 1
                st.note_conn_error(e)
                try:
                    writer.close()
                except Exception:
                    pass
                reader = writer = None
                continue
            st.requests_ok += 1
            st.bytes_read += n
            sent += n
            if target_bps > 0:
                elapsed = time.perf_counter() - t_start
                ahead = sent / target_bps - elapsed
                if ahead > 0:
                    await asyncio.sleep(min(ahead, 0.25))
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass


def percentile(xs, q):
    """Linear-interpolation percentile. q in [0,100]."""
    if not xs:
        return None
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (q / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarise(st: Stats) -> dict:
    dur = max(st.ended_at - st.started_at, 1e-9)
    lat = st.latencies_ms
    total_conn = st.conns_opened + st.conns_failed
    return {
        "tenant": st.tenant,
        "archetype": st.archetype,
        "role": st.role,
        "duration_s": round(dur, 3),
        "requests_ok": st.requests_ok,
        "requests_err": st.requests_err,
        "achieved_rps": round(st.requests_ok / dur, 2),
        "conns_opened": st.conns_opened,
        "conns_failed": st.conns_failed,
        "conn_fail_pct": round(100.0 * st.conns_failed / total_conn, 4)
                         if total_conn else 0.0,
        "conn_errors": st.conn_errors,
        "goodput_gbps": round(st.bytes_read * 8 / dur / 1e9, 6),
        "lat_samples": len(lat),
        "p50_ms": percentile(lat, 50),
        "p95_ms": percentile(lat, 95),
        "p99_ms": percentile(lat, 99),
        "p999_ms": percentile(lat, 99.9),
        "max_ms": max(lat) if lat else None,
        "conn_setup_p99_ms": percentile(st.conn_setup_ms, 99),
    }


async def run(a) -> dict:
    stop = asyncio.Event()
    st = Stats(tenant=a.tenant, archetype=a.archetype, role=a.role)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    tasks = []
    # Warm-up is handled by the orchestrator discarding the first N seconds,
    # not by delaying start: the datapath must be under load during warm-up.
    st.started_at = time.perf_counter()

    if a.keepalive_conns > 0:
        per_conn_rps = a.rps / a.keepalive_conns if a.rps else 0
        for _ in range(a.keepalive_conns):
            tasks.append(asyncio.create_task(
                keepalive_worker(a.host, a.port, per_conn_rps, stop, st)))
    if a.new_conns_per_s > 0:
        # Fan the churn rate across a pool so a single coroutine is not the
        # bottleneck at 8000 conn/s (the W4 setting).
        pool = max(1, min(256, a.new_conns_per_s // 50))
        for _ in range(pool):
            tasks.append(asyncio.create_task(
                churn_worker(a.host, a.port, a.new_conns_per_s / pool,
                             a.active_lifetime_s, stop, st)))
    if a.bulk_gbps > 0:
        pool = max(1, a.bulk_conns)
        for _ in range(pool):
            tasks.append(asyncio.create_task(
                bulk_worker(a.host, a.port, a.bulk_gbps / pool, stop, st)))

    if not tasks:
        raise SystemExit("churngen: no load configured; refusing to run")

    try:
        await asyncio.wait_for(stop.wait(), timeout=a.duration_s)
    except asyncio.TimeoutError:
        stop.set()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    st.ended_at = time.perf_counter()
    return summarise(st)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="MTDP-Bench load generator")
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=80)
    p.add_argument("--tenant", default=os.environ.get("MTDP_TENANT", "t0"))
    p.add_argument("--archetype", default=os.environ.get("MTDP_ARCHETYPE", "T-Web"))
    p.add_argument("--role", default=os.environ.get("MTDP_ROLE", "neutral"))
    p.add_argument("--duration-s", type=float, default=600.0)
    p.add_argument("--rps", type=float, default=0.0)
    p.add_argument("--keepalive-conns", type=int, default=0)
    p.add_argument("--new-conns-per-s", type=int, default=0)
    p.add_argument("--active-lifetime-s", type=float, default=0.0)
    p.add_argument("--bulk-gbps", type=float, default=0.0)
    p.add_argument("--bulk-conns", type=int, default=8)
    p.add_argument("--out", default="-")
    a = p.parse_args(argv)

    result = asyncio.run(run(a))
    blob = json.dumps(result, indent=2)
    if a.out == "-":
        print(blob, flush=True)
    else:
        with open(a.out, "w") as f:
            f.write(blob)
    return 0


if __name__ == "__main__":
    sys.exit(main())
