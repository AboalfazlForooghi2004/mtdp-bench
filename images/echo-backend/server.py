#!/usr/bin/env python3
"""MTDP-Bench echo backend.

A deliberately boring HTTP server. Its only jobs are:
  * answer /healthz so the readiness probe passes,
  * answer / with a fixed-size payload so that goodput is a function of the
    datapath and not of application logic,
  * do a fixed, tiny amount of CPU work per request so the application is not
    perfectly free (a zero-cost backend exaggerates datapath differences).

It is intentionally single-purpose: any per-request variance introduced here
is variance the paper would wrongly attribute to the datapath.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import signal
import socket
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STARTED = threading.Event()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive; T-Web relies on this
    payload = b"x" * 1024
    tenant = "unknown"
    work_rounds = 64

    def log_message(self, fmt, *args):  # noqa: A003 - silence per-request logs
        pass

    def _cpu_work(self) -> None:
        # Fixed, small, and constant across datapaths.
        h = hashlib.sha256()
        for _ in range(self.work_rounds):
            h.update(b"mtdp")
        h.digest()

    def do_GET(self):  # noqa: N802 - stdlib naming
        if self.path.startswith("/healthz"):
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self._cpu_work()
        body = self.payload
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Mtdp-Tenant", self.tenant)
        self.send_header("X-Mtdp-Node", os.environ.get("MTDP_NODE", ""))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 1024

    def server_bind(self):
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        super().server_bind()


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--listen", default=":8080")
    p.add_argument("--payload-bytes", type=int, default=1024)
    p.add_argument("--tenant", default="unknown")
    p.add_argument("--work-rounds", type=int, default=64)
    a = p.parse_args(argv)

    host, _, port = a.listen.rpartition(":")
    Handler.payload = b"x" * a.payload_bytes
    Handler.tenant = a.tenant
    Handler.work_rounds = a.work_rounds

    srv = Server((host or "0.0.0.0", int(port)), Handler)

    def _stop(*_):
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    print(f"mtdp-echo-backend tenant={a.tenant} listen={a.listen} "
          f"payload={a.payload_bytes}B", flush=True)
    STARTED.set()
    srv.serve_forever(poll_interval=0.2)
    srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
