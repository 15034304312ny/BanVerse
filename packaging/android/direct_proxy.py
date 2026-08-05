"""供 WSL 构建临时使用的最小本机 HTTP/HTTPS CONNECT 直连代理。"""

from __future__ import annotations

import argparse
import select
import socket
import socketserver
from urllib.parse import urlsplit

BUFFER_SIZE = 128 * 1024


def _relay(client: socket.socket, upstream: socket.socket) -> None:
    sockets = (client, upstream)
    while True:
        readable, _, _ = select.select(sockets, (), (), 60)
        if not readable:
            continue
        for source in readable:
            data = source.recv(BUFFER_SIZE)
            if not data:
                return
            target = upstream if source is client else client
            target.sendall(data)


class ProxyHandler(socketserver.StreamRequestHandler):
    timeout = 120

    def handle(self) -> None:
        received = b""
        while b"\r\n\r\n" not in received:
            chunk = self.connection.recv(65_536)
            if not chunk:
                return
            received += chunk
            if len(received) > 1_048_576:
                return
        raw_headers, buffered_body = received.split(b"\r\n\r\n", 1)
        lines = raw_headers.split(b"\r\n")
        if not lines:
            return
        try:
            method, target, version = lines[0].decode(
                "iso-8859-1"
            ).strip().split(" ", 2)
        except ValueError:
            return
        headers: list[tuple[str, str]] = []
        for line in lines[1:]:
            name, separator, value = line.decode(
                "iso-8859-1"
            ).partition(":")
            if separator:
                headers.append((name.strip(), value.strip()))

        if method.upper() == "CONNECT":
            host, separator, raw_port = target.rpartition(":")
            if not separator:
                host, raw_port = target, "443"
            with socket.create_connection(
                (host.strip("[]"), int(raw_port)),
                timeout=self.timeout,
            ) as upstream:
                self.connection.sendall(
                    b"HTTP/1.1 200 Connection Established\r\n\r\n"
                )
                if buffered_body:
                    upstream.sendall(buffered_body)
                _relay(self.connection, upstream)
            return

        parsed = urlsplit(target)
        host_header = next(
            (value for name, value in headers if name.lower() == "host"),
            "",
        )
        host_value = parsed.netloc or host_header
        host, separator, raw_port = host_value.rpartition(":")
        if not separator:
            host, raw_port = host_value, "80"
        path = (
            parsed.path
            + (f"?{parsed.query}" if parsed.query else "")
            if parsed.scheme
            else target
        )
        with socket.create_connection(
            (host.strip("[]"), int(raw_port)),
            timeout=self.timeout,
        ) as upstream:
            upstream.sendall(
                f"{method} {path or '/'} {version}\r\n".encode(
                    "iso-8859-1"
                )
            )
            for name, value in headers:
                if name.lower() not in {
                    "proxy-connection",
                    "connection",
                }:
                    upstream.sendall(
                        f"{name}: {value}\r\n".encode("iso-8859-1")
                    )
            upstream.sendall(b"Connection: close\r\n\r\n")
            if buffered_body:
                upstream.sendall(buffered_body)
            _relay(self.connection, upstream)


class ThreadingProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17_892)
    arguments = parser.parse_args()
    with ThreadingProxy(
        (arguments.host, arguments.port),
        ProxyHandler,
    ) as server:
        print(
            f"direct proxy listening on "
            f"{arguments.host}:{arguments.port}",
            flush=True,
        )
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
