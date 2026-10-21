#!/usr/bin/env python3
"""NUC Center privileged helper.

A tiny root daemon listening on a Unix socket.  The GUI sends validated,
high-level requests (set a profile, set an LED colour, ...) and the daemon
performs the actual sysfs/EC writes.

Run it manually with ``sudo python3 nucd.py --debug`` or let systemd start it.
"""

from __future__ import annotations

import argparse
import grp
import json
import logging
import os
import signal
import socketserver
import sys
import threading
from pathlib import Path

from nuc_center.util import APP_VERSION
from nuc_center.priv import PrivilegedBackend

LOG = logging.getLogger("nucd")

DEFAULT_SOCKET = Path("/run/nuc-center/helper.sock")


class HelperHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.connection.settimeout(15.0)
        try:
            line = self.rfile.readline(65536)
            if not line:
                return
            request = json.loads(line.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            command = request.pop("cmd", None)
            if not isinstance(command, str) or not command:
                raise ValueError("missing command")
            LOG.debug("request: %s %s", command, request)
            with self.server.request_lock:  # type: ignore[attr-defined]
                data = self.server.backend.dispatch(command, request)  # type: ignore[attr-defined]
            response = {"ok": True, "data": data, "version": APP_VERSION}
        except Exception as exc:  # noqa: BLE001 - never kill the daemon
            LOG.warning("request failed: %s", exc)
            response = {"ok": False, "error": str(exc), "version": APP_VERSION}
        try:
            self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")
            self.wfile.flush()
        except OSError:
            pass


class HelperServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: str, backend: PrivilegedBackend) -> None:
        self.backend = backend
        self.request_lock = threading.Lock()
        super().__init__(address, HelperHandler)


def prepare_socket(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o755)
    except OSError:
        pass
    if path.exists() or path.is_symlink():
        try:
            path.unlink()
        except OSError:
            pass


def set_socket_owner(path: Path) -> None:
    try:
        gid = grp.getgrnam("wheel").gr_gid
    except KeyError:
        gid = 0
    try:
        os.chown(path, 0, gid)
    except OSError as exc:
        LOG.warning("cannot change socket group: %s", exc)
    try:
        os.chmod(path, 0o660)
    except OSError as exc:
        LOG.warning("cannot change socket mode: %s", exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NUC Center privileged helper")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    parser.add_argument("--once", action="store_true", help="print state and exit")
    parser.add_argument(
        "--socket",
        type=Path,
        default=Path(os.environ.get("NUC_CENTER_SOCKET", DEFAULT_SOCKET)),
        help="Unix socket path",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if os.geteuid() != 0:
        LOG.warning("not running as root; write operations will fail")

    backend = PrivilegedBackend()
    caps = backend.capabilities()
    LOG.info("capabilities: %s", json.dumps(caps, default=str))
    if args.once:
        print(json.dumps(backend.state(), indent=2, default=str))
        return 0

    prepare_socket(args.socket)
    server = HelperServer(str(args.socket), backend)
    set_socket_owner(args.socket)

    def _terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)

    LOG.info("listening on %s", args.socket)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        LOG.info("shutting down")
    finally:
        try:
            backend.shutdown()
        except Exception:
            LOG.exception("backend shutdown failed")
        server.server_close()
        try:
            args.socket.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
