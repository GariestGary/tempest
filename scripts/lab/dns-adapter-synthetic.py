"""Loopback-only sing-box DNS/DoT/SOCKS experiment; no system configuration changes."""
import datetime
import json
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def readn(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise EOFError()
        data += chunk
    return data


def main():
    binary = sys.argv[1]
    with tempfile.TemporaryDirectory(prefix="tempest-dns-") as tmp:
        root = Path(tmp)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dns.test")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("dns.test")]), False)
                .sign(key, hashes.SHA256()))
        (root / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (root / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(root / "cert.pem", root / "key.pem")
        seen = {"auth": 0, "accepted": 0, "target": [], "sni": []}
        tls.set_servername_callback(lambda sock, server_name, ctx: seen["sni"].append(server_name))
        stop = threading.Event()
        deny = threading.Event()
        upstream_down = threading.Event()

        def listener():
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sock.listen()
            sock.settimeout(.2)
            return sock

        upstream = listener()
        proxy = listener()
        upstream_port = upstream.getsockname()[1]
        proxy_port = proxy.getsockname()[1]

        def serve_upstream():
            while not stop.is_set():
                try:
                    conn, _ = upstream.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                seen["accepted"] += 1
                def handle(conn):
                    try:
                        with tls.wrap_socket(conn, server_side=True) as s:
                            msg = readn(s, struct.unpack("!H", readn(s, 2))[0])
                            # A syntactically valid DNSKEY RR with a deliberately large key.
                            rdata = b"\x01\x01\x03\x08" + b"K" * 700
                            response = (msg[:2] + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"
                                        + msg[12:] + b"\xc0\x0c\x00\x30\x00\x01\x00\x00\x00\x00"
                                        + struct.pack("!H", len(rdata)) + rdata)
                            s.sendall(struct.pack("!H", len(response)) + response)
                    except (OSError, EOFError, ssl.SSLError):
                        pass
                threading.Thread(target=handle, args=(conn,), daemon=True).start()

        def serve_proxy():
            while not stop.is_set():
                try:
                    conn, _ = proxy.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                def handle(conn):
                    remote = None
                    try:
                        conn.settimeout(3)
                        head = readn(conn, 2)
                        methods = readn(conn, head[1])
                        if deny.is_set() or 2 not in methods:
                            conn.sendall(b"\x05\xff")
                            return
                        conn.sendall(b"\x05\x02")
                        auth = readn(conn, 2)
                        user = readn(conn, auth[1])
                        password = readn(conn, readn(conn, 1)[0])
                        if user != b"test-user" or password != b"test-password":
                            conn.sendall(b"\x01\x01")
                            return
                        seen["auth"] += 1
                        conn.sendall(b"\x01\x00")
                        req = readn(conn, 4)
                        if req[:2] != b"\x05\x01" or req[3] != 1:
                            return
                        address = socket.inet_ntoa(readn(conn, 4))
                        port = struct.unpack("!H", readn(conn, 2))[0]
                        seen["target"].append((address, port))
                        if (address, port) != ("127.0.0.1", upstream_port):
                            return
                        if upstream_down.is_set():
                            conn.sendall(b"\x05\x05\x00\x01\x7f\x00\x00\x01\x00\x00")
                            return
                        remote = socket.create_connection((address, port), timeout=3)
                        conn.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
                        def copy(a, b):
                            try:
                                while chunk := a.recv(65536):
                                    b.sendall(chunk)
                            except OSError:
                                pass
                            finally:
                                try:
                                    b.shutdown(socket.SHUT_WR)
                                except OSError:
                                    pass
                        threading.Thread(target=copy, args=(remote, conn), daemon=True).start()
                        copy(conn, remote)
                    except (OSError, EOFError):
                        pass
                    finally:
                        conn.close()
                        if remote:
                            remote.close()
                threading.Thread(target=handle, args=(conn,), daemon=True).start()

        threading.Thread(target=serve_upstream, daemon=True).start()
        threading.Thread(target=serve_proxy, daemon=True).start()
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        dns_port = probe.getsockname()[1]
        probe.close()
        config = {"log": {"disabled": True},
                  "dns": {"disable_cache": True, "servers": [{"type": "tls", "tag": "dot",
                    "server": "127.0.0.1", "server_port": upstream_port, "detour": "socks",
                    "tls": {"enabled": True, "server_name": "dns.test", "certificate_path": str(root / "cert.pem")}}], "final": "dot"},
                  "inbounds": [{"type": "direct", "tag": "dns", "listen": "127.0.0.1", "listen_port": dns_port}],
                  "outbounds": [{"type": "socks", "tag": "socks", "server": "127.0.0.1", "server_port": proxy_port,
                                "username": "test-user", "password": "test-password"}],
                  "route": {"rules": [{"inbound": "dns", "action": "hijack-dns"}]}}
        path = root / "config.json"
        proc = None

        def start():
            path.write_text(json.dumps(config), encoding="utf-8")
            result = subprocess.run([binary, "check", "-c", str(path)], capture_output=True, timeout=10)
            if result.returncode:
                raise RuntimeError("sing-box config rejected: " + result.stderr.decode(errors="replace")[-800:])
            process = subprocess.Popen([binary, "run", "-c", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(50):
                if process.poll() is not None:
                    raise RuntimeError("sing-box exited during startup")
                try:
                    with socket.create_connection(("127.0.0.1", dns_port), .1):
                        return process
                except OSError:
                    time.sleep(.1)
            process.terminate()
            raise RuntimeError("listener not ready")

        def query(proto, label):
            question = bytes([len(label)]) + label.encode() + b"\x04test\x00\x00\x30\x00\x01"
            msg = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + question
            with socket.socket(socket.AF_INET, proto) as s:
                s.settimeout(3)
                s.connect(("127.0.0.1", dns_port))
                s.sendall((struct.pack("!H", len(msg)) if proto == socket.SOCK_STREAM else b"") + msg)
                if proto == socket.SOCK_STREAM:
                    return readn(s, struct.unpack("!H", readn(s, 2))[0])
                return s.recv(65535)

        try:
            proc = start()
            tcp = query(socket.SOCK_STREAM, "large")
            udp = query(socket.SOCK_DGRAM, "small")
            assert len(tcp) > 700 and not (tcp[2] & 2) and tcp[6:8] == b"\x00\x01", "TCP DNSKEY truncated or missing"
            assert udp[:2] == b"\x12\x34" and udp[3] & 15 == 0, "UDP failed"
            assert seen["auth"] >= 2 and all(t == ("127.0.0.1", upstream_port) for t in seen["target"])
            assert "dns.test" in seen["sni"]
            print("PASS: TCP DNSKEY, UDP, authenticated SOCKS, numeric target, TLS SNI")
            deny.set()
            before = len(seen["target"])
            try:
                reply = query(socket.SOCK_STREAM, "deny")
                assert reply[3] & 15 != 0, "SOCKS failure returned success"
            except (OSError, EOFError):
                pass
            assert len(seen["target"]) == before, "SOCKS denial reached target"
            print("PASS: SOCKS authentication failure produces no successful DNS response")
            proc.terminate()
            proc.wait(timeout=5)
            proc = None
            deny.clear()
            config["dns"]["servers"][0]["tls"]["server_name"] = "wrong.test"
            proc = start()
            try:
                reply = query(socket.SOCK_STREAM, "wrong")
                assert reply[3] & 15 != 0, "TLS name mismatch returned success"
            except (OSError, EOFError):
                pass
            print("PASS: TLS identity mismatch produces no successful DNS response")
            proc.terminate()
            proc.wait(timeout=5)
            proc = None
            config["dns"]["servers"][0]["tls"]["server_name"] = "dns.test"
            del config["dns"]["servers"][0]["tls"]["certificate_path"]
            proc = start()
            try:
                reply = query(socket.SOCK_STREAM, "untrusted")
                assert reply[3] & 15 != 0, "untrusted certificate returned success"
            except (OSError, EOFError):
                pass
            assert seen["accepted"] == len(seen["target"]), "unexpected direct TLS connection"
            upstream_down.set()
            before = seen["accepted"]
            try:
                reply = query(socket.SOCK_STREAM, "upstreamdown")
                assert reply[3] & 15 != 0, "unreachable TLS upstream returned success"
            except (OSError, EOFError):
                pass
            assert seen["accepted"] == before, "TLS failure caused direct TLS connection"
            proxy.close()
            time.sleep(.3)
            before = seen["accepted"]
            try:
                reply = query(socket.SOCK_STREAM, "offline")
                assert reply[3] & 15 != 0, "stopped SOCKS returned success"
            except (OSError, EOFError):
                pass
            assert seen["accepted"] == before, "stopped SOCKS caused direct TLS connection"
            print("PASS: untrusted certificate rejected; unreachable TLS and stopped SOCKS have no direct fallback")
        finally:
            if proc is not None:
                proc.terminate()
                proc.wait(timeout=5)
            stop.set()
            upstream.close()
            proxy.close()


if __name__ == "__main__":
    main()
