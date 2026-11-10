#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BDIX-Tester — Test access to BDIX (Bangladesh Internet Exchange) servers.

BDIX is the local internet exchange where Bangladeshi ISPs and hosting
providers peer with each other. Many ISPs run local FTP / HTTP mirrors
(Linux ISOs, movies, TV streams) that are fast and free for local users.

This tool checks, from YOUR location, which of those servers are reachable.
It can also test *through* a Bangladesh-based server/VPS (--via-ssh) so people
outside Bangladesh can see (and use) BDIX servers the same way locals do.

Only Python 3.7+ standard library is required. No pip install needed.

Examples
--------
  python bdix_tester.py                     # test every server
  python bdix_tester.py --type ftp          # only FTP servers
  python bdix_tester.py --name lanka        # only names matching "lanka"
  python bdix_tester.py --community         # use the ~525-server community list
  python bdix_tester.py --json out.json     # save a JSON report
  python bdix_tester.py --html report.html  # save a nice HTML report
  python bdix_tester.py --via-ssh root@bd-vps.example.com   # test from abroad
  python bdix_tester.py web                 # local ad-free web dashboard
  python bdix_tester.py list                # show the server list
  python bdix_tester.py info                # show data-file statistics
"""

import argparse
import concurrent.futures as cf
import csv
import datetime
import ftplib
import html
import http.server
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from urllib.parse import urlparse

APP_NAME = "BDIX-Tester"
APP_VERSION = "2.0.0"
USER_AGENT = f"{APP_NAME}/{APP_VERSION} (+https://github.com/iamtanzir/BDIX-Tester)"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_CANDIDATES = (
    os.path.join(BASE_DIR, "servers.json"),
    os.path.join(BASE_DIR, "BDIX-Servers"),
)
COMMUNITY_DATA = os.path.join(BASE_DIR, "community-servers.json")
WEB_DIR = os.path.join(BASE_DIR, "web")

# ---------------------------------------------------------------- utilities

def configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # py3.7+
        except Exception:
            pass


def supports_color(no_color_flag: bool) -> bool:
    if no_color_flag or os.environ.get("NO_COLOR") is not None:
        return False
    if not sys.stdout.isatty():
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            return True
        except Exception:
            return False
    return True


class Console:
    def __init__(self, color: bool):
        self.color = color

    def paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def green(self, t): return self.paint(t, "32")
    def red(self, t): return self.paint(t, "31")
    def yellow(self, t): return self.paint(t, "33")
    def cyan(self, t): return self.paint(t, "36")
    def bold(self, t): return self.paint(t, "1")


# ------------------------------------------------------------- data loading

def normalize_entry(raw: dict) -> dict:
    """Accept both the clean servers.json format and the original raw dump."""
    name = (raw.get("name") or raw.get("ServerName") or "").strip()
    url = (raw.get("url") or raw.get("Address") or "").strip()
    description = (raw.get("description") or raw.get("Description") or "").strip()
    category = (raw.get("category") or "").strip()
    if not category:
        d = description.lower()
        if "ftp" in d:
            category = "FTP"
        elif "tv" in d and "movie" in d:
            category = "Movie+TV"
        elif "tv" in d:
            category = "TV"
        elif "movie" in d:
            category = "Movie"
        else:
            category = "Other"
    if url and "://" not in url:
        url = "http://" + url
    return {"name": name, "url": url, "category": category, "description": description}


def load_servers(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    rows = data.get("Data") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError(f"{path}: unrecognised data layout")
    servers = [normalize_entry(r) for r in rows]
    servers = [s for s in servers if s["name"] and s["url"]]
    return servers


def default_data_path() -> str:
    for candidate in DEFAULT_DATA_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return DEFAULT_DATA_CANDIDATES[0]


def resolve_data_path(args) -> str:
    return COMMUNITY_DATA if getattr(args, "community", False) else args.data


# ------------------------------------------------------------------ testing

_HTTP_STATUS_RE = re.compile(rb"HTTP/1\.[01]\s+(\d{3})")


def _http_via_tunnel(original, timeout: float, via_local_port: int,
                     insecure_https: bool) -> dict:
    """Manual HTTP(S) request through a local SSH forward.

    The socket connects to 127.0.0.1:<port> but TLS and the Host header use
    the *original* host, so virtual hosting and certificate verification
    both work exactly as if the machine were inside the BDIX network.
    Redirects are not followed (a 3xx already proves reachability).
    """
    started = time.monotonic()
    raw = None
    try:
        raw = socket.create_connection(("127.0.0.1", via_local_port), timeout)
        sock = raw
        if original.scheme == "https":
            ctx = ssl.create_default_context()
            if insecure_https:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(raw, server_hostname=original.hostname)
        sock.settimeout(timeout)
        path = original.path or "/"
        if original.query:
            path += "?" + original.query
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {original.netloc}\r\n"
            f"User-Agent: {USER_AGENT}\r\n"
            f"Accept: */*\r\n"
            f"Range: bytes=0-4095\r\n"
            f"Connection: close\r\n\r\n")
        sock.sendall(request.encode("ascii", errors="ignore"))
        buf = b""
        while len(buf) < 16384:
            chunk = sock.recv(8192)
            if not chunk:
                break
            buf += chunk
        latency = (time.monotonic() - started) * 1000
        match = _HTTP_STATUS_RE.search(buf)
        if match:
            status = int(match.group(1))
            return {"ok": status < 400, "status": status,
                    "latency_ms": round(latency, 1),
                    "final_url": original.geturl(),
                    "error": None if status < 400 else f"HTTP {status}"}
        if buf:
            # Answered with something, but not a recognisable HTTP status line.
            return {"ok": True, "status": None,
                    "latency_ms": round(latency, 1),
                    "final_url": original.geturl(),
                    "error": "unparseable HTTP response (server answered)"}
        return {"ok": False, "status": None, "latency_ms": round(latency, 1),
                "final_url": original.geturl(), "error": "empty response"}
    except ssl.CertificateError as exc:
        latency = (time.monotonic() - started) * 1000
        return {"ok": False, "status": None, "latency_ms": round(latency, 1),
                "final_url": original.geturl(),
                "error": f"TLS certificate verification failed ({exc}); "
                         f"retry with --insecure-https if you trust this path"}
    except Exception as exc:
        latency = (time.monotonic() - started) * 1000
        return {"ok": False, "status": None, "latency_ms": round(latency, 1),
                "final_url": original.geturl(),
                "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if raw is not None:
            try:
                raw.close()
            except OSError:
                pass


def http_test(url: str, timeout: float, via_local_port: int = None,
              insecure_https: bool = False) -> dict:
    """Fetch a small slice of the page and record latency + HTTP status."""
    original = urlparse(url)

    if via_local_port is not None:
        return _http_via_tunnel(original, timeout, via_local_port,
                                insecure_https)

    target = url
    headers = {"User-Agent": USER_AGENT, "Connection": "close",
               "Range": "bytes=0-4095"}

    req = urllib.request.Request(target, headers=headers)
    context = None
    if target.lower().startswith("https://"):
        context = ssl.create_default_context()

    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            resp.read(4096)
            latency = (time.monotonic() - started) * 1000
            status = resp.getcode()
            return {"ok": status < 400, "status": status,
                    "latency_ms": round(latency, 1),
                    "final_url": url, "error": None}
    except urllib.error.HTTPError as exc:
        latency = (time.monotonic() - started) * 1000
        # The server *answered* — it is reachable, just not happy with GET.
        return {"ok": exc.code < 400, "status": exc.code,
                "latency_ms": round(latency, 1), "final_url": url,
                "error": f"HTTP {exc.code} {exc.reason}"}
    except Exception as exc:
        latency = (time.monotonic() - started) * 1000
        return {"ok": False, "status": None, "latency_ms": round(latency, 1),
                "final_url": url, "error": f"{type(exc).__name__}: {exc}"}


def ftp_test(host: str, port: int, timeout: float, via_local_port: int = None,
             ftp_login: bool = False) -> dict:
    """Lightweight FTP probe: connect and read the 220 banner. No login needed."""
    target_host = "127.0.0.1" if via_local_port else host
    target_port = via_local_port if via_local_port else port
    started = time.monotonic()
    sock = None
    try:
        sock = socket.create_connection((target_host, target_port), timeout=timeout)
        sock.settimeout(timeout)
        banner = b""
        try:
            while b"\n" not in banner and len(banner) < 512:
                chunk = sock.recv(256)
                if not chunk:
                    break
                banner += chunk
        except socket.timeout:
            pass
        latency = (time.monotonic() - started) * 1000
        text = banner.decode("utf-8", errors="replace").strip()
        ok = text.startswith("220") or bool(text)
        if ok and ftp_login and not via_local_port:
            try:
                ftp = ftplib.FTP()
                ftp.connect(host, port, timeout=timeout)
                ftp.login()  # anonymous
                ftp.quit()
            except Exception as exc:
                return {"ok": True, "banner": text, "latency_ms": round(latency, 1),
                        "anonymous_login": False,
                        "error": f"anonymous login failed: {exc}"}
            return {"ok": True, "banner": text, "latency_ms": round(latency, 1),
                    "anonymous_login": True, "error": None}
        return {"ok": ok, "banner": text or None, "latency_ms": round(latency, 1),
                "anonymous_login": None, "error": None if text else "no banner received"}
    except Exception as exc:
        latency = (time.monotonic() - started) * 1000
        return {"ok": False, "banner": None, "latency_ms": round(latency, 1),
                "anonymous_login": None,
                "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def split_host_port(url: str):
    parsed = urlparse(url)
    host = parsed.hostname
    if parsed.port:
        port = parsed.port
    elif parsed.scheme == "https":
        port = 443
    else:
        port = 80
    return host, port


def is_private_host(host: str) -> bool:
    import ipaddress
    try:
        return ipaddress.ip_address(host).is_private
    except (ValueError, TypeError):
        return False


class Tunnel:
    """One SSH local port-forward: 127.0.0.1:<local> -> <host>:<port> via a BD VPS."""

    def __init__(self, ssh_target: str, remote_host: str, remote_port: int):
        self.local_port = self._free_port()
        self.ssh_target = ssh_target
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.proc = None

    @staticmethod
    def _free_port() -> int:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def __enter__(self):
        cmd = [
            "ssh", "-N",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ExitOnForwardFailure=yes",
            "-L", f"127.0.0.1:{self.local_port}:{self.remote_host}:{self.remote_port}",
            self.ssh_target,
        ]
        if os.name != "nt":
            # Reuse one master connection for all forwards (much faster).
            ctrl_dir = os.path.join(tempfile.gettempdir(), f"bdix-ssh-{os.getuid()}")
            try:
                os.makedirs(ctrl_dir, exist_ok=True)
                cmd[1:1] = ["-o", "ControlMaster=auto",
                            "-o", f"ControlPath={ctrl_dir}/%C",
                            "-o", "ControlPersist=30"]
            except OSError:
                pass
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    "ssh tunnel failed to start — check that the VPS accepts "
                    "key-based logins (BatchMode) and that 'ssh' is installed")
            try:
                with socket.create_connection(("127.0.0.1", self.local_port), 0.4):
                    return self
            except OSError:
                time.sleep(0.15)
        raise RuntimeError("ssh tunnel did not open in time")

    def __exit__(self, exc_type, exc, tb):
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def test_server(server: dict, timeout: float, via_ssh: str = None,
                ftp_login: bool = False, skip_ftp: bool = False,
                insecure_https: bool = False) -> dict:
    result = dict(server)
    host, port = split_host_port(server["url"])
    result["host"] = host
    result["port"] = port
    result["private_ip"] = is_private_host(host) if host else False

    ftp_port = 21

    if via_ssh:
        try:
            with Tunnel(via_ssh, host, port) as tunnel:
                result["http"] = http_test(server["url"], timeout,
                                           via_local_port=tunnel.local_port,
                                           insecure_https=insecure_https)
        except Exception as exc:
            result["http"] = {"ok": False, "status": None, "latency_ms": None,
                              "final_url": server["url"],
                              "error": f"tunnel: {exc}"}
        if skip_ftp:
            result["ftp"] = {"ok": False, "skipped": True,
                             "error": "skipped (--no-ftp)"}
        else:
            try:
                with Tunnel(via_ssh, host, ftp_port) as tunnel:
                    result["ftp"] = ftp_test(host, ftp_port, timeout,
                                             via_local_port=tunnel.local_port)
            except Exception as exc:
                result["ftp"] = {"ok": False, "banner": None,
                                 "latency_ms": None, "error": f"tunnel: {exc}"}
    else:
        result["http"] = http_test(server["url"], timeout)
        if skip_ftp:
            result["ftp"] = {"ok": False, "skipped": True,
                             "error": "skipped (--no-ftp)"}
        else:
            result["ftp"] = ftp_test(host, ftp_port, timeout, ftp_login=ftp_login)

    http_ok = result["http"].get("ok")
    ftp_ok = result["ftp"].get("ok")
    http_status = result["http"].get("status")

    if http_ok or ftp_ok:
        result["verdict"] = "UP"
    elif http_status in (401, 403, 407):
        result["verdict"] = "BLOCKED"  # reachable, but refused us
    else:
        result["verdict"] = "DOWN"
    return result

# ------------------------------------------------------------------- report

def summarize(results):
    up = sum(1 for r in results if r["verdict"] == "UP")
    blocked = sum(1 for r in results if r["verdict"] == "BLOCKED")
    down = sum(1 for r in results if r["verdict"] == "DOWN")
    lat = [r["http"]["latency_ms"] for r in results
           if r.get("http") and r["http"].get("latency_ms") is not None]
    avg_lat = round(sum(lat) / len(lat), 1) if lat else None
    return {"total": len(results), "up": up, "blocked": blocked, "down": down,
            "avg_http_latency_ms": avg_lat,
            "tested_at": datetime.datetime.now().isoformat(timespec="seconds")}


def print_table(console: Console, results):
    header = f"{'#':<4}{'Name':<28}{'Category':<10}{'Result':<9}{'HTTP':<6}{'ms':>8}  URL"
    print(console.bold(header))
    print(console.bold("-" * 100))
    for i, r in enumerate(results, 1):
        verdict = r["verdict"]
        if verdict == "UP":
            badge = console.green("UP")
        elif verdict == "BLOCKED":
            badge = console.yellow("BLOCKED")
        else:
            badge = console.red("DOWN")
        status = r["http"].get("status") or "-"
        latency = r["http"].get("latency_ms")
        latency_s = f"{latency:.0f}" if latency is not None else "-"
        name = r["name"][:27]
        print(f"{i:<4}{name:<28}{r['category']:<10}{badge:<18}{str(status):<6}{latency_s:>8}  {r['url']}")


def write_json_report(path, summary, results):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "results": results}, fh,
                  ensure_ascii=False, indent=2)


def write_csv_report(path, results):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["name", "url", "category", "verdict",
                         "http_status", "http_latency_ms", "http_error",
                         "ftp_ok", "ftp_banner", "ftp_error"])
        for r in results:
            http = r.get("http", {})
            ftp = r.get("ftp", {})
            writer.writerow([r["name"], r["url"], r["category"], r["verdict"],
                             http.get("status"), http.get("latency_ms"),
                             http.get("error"), ftp.get("ok"),
                             ftp.get("banner"), ftp.get("error")])


def write_html_report(path, summary, results):
    rows = []
    for r in results:
        v = r["verdict"]
        color = {"UP": "#1a7f37", "BLOCKED": "#9a6700", "DOWN": "#cf222e"}[v]
        http = r.get("http", {})
        ftp = r.get("ftp", {})
        ftp_cell = html.escape(str(ftp.get("banner") or ftp.get("error") or "-"))
        err = html.escape(str(http.get("error") or ""))
        lat = http.get("latency_ms")
        rows.append(
            f"<tr><td>{html.escape(r['name'])}</td>"
            f"<td>{html.escape(r['category'])}</td>"
            f"<td><a href=\"{html.escape(r['url'])}\">{html.escape(r['url'])}</a></td>"
            f"<td style='color:{color};font-weight:700'>{v}</td>"
            f"<td>{http.get('status') or '-'}</td>"
            f"<td>{lat if lat is not None else '-'}</td>"
            f"<td title='{err}'>{html.escape(err[:80])}</td>"
            f"<td>{ftp_cell[:80]}</td></tr>")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{APP_NAME} report — {summary['tested_at']}</title>
<style>
 body{{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:2rem;background:#f6f8fa;color:#24292f}}
 h1{{margin-bottom:0}} .meta{{color:#57606a;margin-bottom:1.5rem}}
 .cards{{display:flex;gap:1rem;margin:1rem 0 1.5rem}}
 .card{{background:#fff;border:1px solid #d0d7de;border-radius:10px;padding:.8rem 1.4rem;text-align:center}}
 .card b{{font-size:1.6rem;display:block}}
 table{{border-collapse:collapse;width:100%;background:#fff;border:1px solid #d0d7de;border-radius:10px;overflow:hidden}}
 th,td{{padding:.45rem .6rem;border-bottom:1px solid #eaecef;font-size:.85rem;text-align:left}}
 th{{background:#eaeef2}} tr:hover td{{background:#f6f8fa}}
</style></head><body>
<h1>🌐 BDIX-Tester report</h1>
<div class="meta">Generated {html.escape(summary['tested_at'])} · {summary['total']} servers tested · avg HTTP latency {summary['avg_http_latency_ms']} ms</div>
<div class="cards">
 <div class="card"><b style="color:#1a7f37">{summary['up']}</b>UP</div>
 <div class="card"><b style="color:#9a6700">{summary['blocked']}</b>BLOCKED</div>
 <div class="card"><b style="color:#cf222e">{summary['down']}</b>DOWN</div>
</div>
<table><thead><tr><th>Server</th><th>Category</th><th>URL</th><th>Verdict</th><th>HTTP</th><th>ms</th><th>Error</th><th>FTP banner</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody></table>
<p class="meta">Generated by {APP_NAME} v{APP_VERSION} · results are valid only for the machine that ran the test, from its location and network.</p>
</body></html>"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)


# --------------------------------------------------------------------- cli

def filter_servers(servers, type_filter, name_regex):
    out = servers
    if type_filter and type_filter != "all":
        t = type_filter.lower()
        if t == "ftp":
            out = [s for s in out if "ftp" in s["category"].lower()]
        elif t == "tv":
            out = [s for s in out if "tv" in s["category"].lower()]
        elif t == "movie":
            out = [s for s in out if "movie" in s["category"].lower()]
        else:
            out = [s for s in out if t in s["category"].lower()]
    if name_regex:
        rx = re.compile(name_regex, re.IGNORECASE)
        out = [s for s in out if rx.search(s["name"]) or rx.search(s["url"])]
    return out


def cmd_test(args, console):
    servers = load_servers(resolve_data_path(args))
    servers = filter_servers(servers, args.type, args.name)
    if not servers:
        print("No servers match the given filters.", file=sys.stderr)
        return 2

    via = args.via_ssh
    if via:
        print(f"Testing {len(servers)} servers through SSH tunnel via {via} ...")
        print("(each server opens its own ssh -L forward; key-based auth required)\n")
    else:
        print(f"Testing {len(servers)} servers from this machine ...\n")

    started = time.monotonic()
    results = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(test_server, s, args.timeout, via,
                        args.ftp_login, args.no_ftp, args.insecure_https): s
            for s in servers
        }
        done = 0
        for future in cf.as_completed(futures):
            results.append(future.result())
            done += 1
            if args.progress:
                print(f"\r  tested {done}/{len(servers)} ...", end="", flush=True)

    if args.progress:
        print("\n")
    results.sort(key=lambda r: (r["category"], r["name"].lower()))
    summary = summarize(results)
    elapsed = time.monotonic() - started

    print_table(console, results)
    print()
    print(console.bold(
        f"Total: {summary['total']}   Up: {summary['up']}   "
        f"Blocked: {summary['blocked']}   Down: {summary['down']}   "
        f"Avg HTTP latency: {summary['avg_http_latency_ms']} ms   "
        f"({elapsed:.1f}s)"))

    if args.json:
        write_json_report(args.json, summary, results)
        print(f"JSON report written to {args.json}")
    if args.csv:
        write_csv_report(args.csv, results)
        print(f"CSV report written to {args.csv}")
    if args.html:
        write_html_report(args.html, summary, results)
        print(f"HTML report written to {args.html}")

    if args.fail_under is not None and summary["up"] < args.fail_under:
        print(f"FAIL: only {summary['up']} server(s) up "
              f"(--fail-under {args.fail_under})", file=sys.stderr)
        return 1
    if args.verbose:
        print("\nErrors:")
        for r in results:
            http_err = r.get("http", {}).get("error")
            ftp_err = r.get("ftp", {}).get("error")
            if http_err or ftp_err:
                print(f"  {r['name']}:")
                if http_err:
                    print(f"    http: {http_err}")
            if ftp_err and not ftp_err.startswith("skipped"):
                print(f"    ftp : {ftp_err}")
    return 0


def cmd_list(args, console):
    servers = load_servers(resolve_data_path(args))
    servers = filter_servers(servers, args.type, args.name)
    if args.markdown:
        print("| # | Server | Category | Link |")
        print("|---|--------|----------|------|")
        for i, s in enumerate(servers, 1):
            link = s["url"]
            print(f"| {i} | {s['name']} | {s['category']} | <{link}> |")
        return 0
    for i, s in enumerate(servers, 1):
        print(f"{i:>4}. [{s['category']:<8}] {s['name']:<30} {s['url']}")
    print(f"\n{len(servers)} server(s) listed.")
    return 0


def cmd_info(args, console):
    servers = load_servers(resolve_data_path(args))
    by_cat = {}
    hosts_ip, private = 0, []
    https = 0
    for s in servers:
        by_cat[s["category"]] = by_cat.get(s["category"], 0) + 1
        host, _ = split_host_port(s["url"])
        import ipaddress
        try:
            ip = ipaddress.ip_address(host)
            host_is_ip = True
            if ip.is_private:
                private.append(s)
        except (ValueError, TypeError):
            host_is_ip = False
        if host_is_ip:
            hosts_ip += 1
        if s["url"].lower().startswith("https://"):
            https += 1
    print(console.bold(f"{APP_NAME} — data file: {resolve_data_path(args)}"))
    print(f"Total servers : {len(servers)}")
    for cat in sorted(by_cat):
        print(f"  {cat:<10}: {by_cat[cat]}")
    print(f"HTTPS         : {https}")
    print(f"HTTP          : {len(servers) - https}")
    print(f"IP-literal    : {hosts_ip}")
    print(f"Private-IP    : {len(private)} (only reachable inside the owning ISP's network)")
    for s in private:
        print(f"    - {s['name']:<28} {s['url']}")
    return 0


# ----------------------------------------------------------- web dashboard

def cmd_web(args, console):
    """Serve the ad-free local web dashboard (bdix.link-style, fully self-hosted)."""
    data_path_used = resolve_data_path(args)
    servers = load_servers(data_path_used)
    servers = filter_servers(servers, args.type, args.name)
    # The dashboard identifies servers by name; keep the first of any duplicates.
    _seen = set()
    uniq = []
    for s in servers:
        if s["name"] in _seen:
            continue
        _seen.add(s["name"])
        uniq.append(s)
    servers = uniq
    by_name = {s["name"]: s for s in servers}

    index_file = os.path.join(WEB_DIR, "index.html")
    if not os.path.isfile(index_file):
        print(f"error: web dashboard not found at {index_file}", file=sys.stderr)
        return 2

    timeout = args.timeout
    via_ssh = args.via_ssh
    ftp_login = args.ftp_login
    insecure_https = args.insecure_https
    verbose = args.verbose

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *fmt_args):
            if verbose:
                super().log_message(fmt, *fmt_args)

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path, content_type):
            try:
                with open(path, "rb") as fh:
                    body = fh.read()
            except OSError:
                self.send_error(404, "not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path == "/" or path == "/index.html":
                self._file(index_file, "text/html; charset=utf-8")
            elif path == "/api/servers":
                self._json([{"name": s["name"], "url": s["url"],
                             "category": s["category"]} for s in servers])
            elif path == "/api/test":
                qs = urllib.parse.parse_qs(parsed.query)
                name = (qs.get("name") or [""])[0]
                server = by_name.get(name)
                if not server:
                    self._json({"error": f"unknown server: {name}"}, 404)
                    return
                result = test_server(server, timeout, via_ssh=via_ssh,
                                     ftp_login=ftp_login,
                                     skip_ftp=args.no_ftp,
                                     insecure_https=insecure_https)
                # Trim to what the dashboard needs.
                self._json({
                    "name": result["name"],
                    "verdict": result["verdict"],
                    "http": result.get("http"),
                    "ftp": result.get("ftp"),
                })
            else:
                self.send_error(404, "not found")

    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True

    server = Server(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(console.bold(f"BDIX-Tester web dashboard (ad-free, local)"))
    print(f"  Serving {len(servers)} servers from {data_path_used}")
    print(f"  Open {console.cyan(url)}   (Ctrl+C to stop)")
    if via_ssh:
        print(f"  Tests run THROUGH the Bangladesh VPS: {via_ssh}")
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        description="Test BDIX (Bangladesh Internet Exchange) server accessibility.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("command", nargs="?", default="test",
                   choices=["test", "list", "info", "web"],
                   help="test (default), list, info or web (local dashboard)")
    p.add_argument("--data", default=default_data_path(),
                   help="server data file (default: servers.json / BDIX-Servers)")
    p.add_argument("--community", action="store_true",
                   help="use community-servers.json (~525 more servers, "
                        "from BDIX-TESTER-BY-BORANNO, BSD-3-Clause)")
    p.add_argument("--type", default="all",
                   choices=["all", "movie", "tv", "ftp"],
                   help="only servers of a category")
    p.add_argument("--name", default=None,
                   help="regex; only servers whose name or URL matches")
    # test-specific
    p.add_argument("--timeout", type=float, default=6.0,
                   help="per-request timeout in seconds (default 6)")
    p.add_argument("--workers", type=int, default=24,
                   help="concurrent tests (default 24)")
    p.add_argument("--via-ssh", default=None, metavar="USER@HOST",
                   help="test through an SSH tunnel to a Bangladesh VPS "
                        "(e.g. --via-ssh root@1.2.3.4). Needs key-based ssh access.")
    p.add_argument("--insecure-https", action="store_true",
                   help="(advanced) skip TLS certificate verification for HTTPS "
                        "tests through --via-ssh. By default the certificate is "
                        "verified against the original hostname")
    p.add_argument("--ftp-login", action="store_true",
                   help="also try anonymous FTP login (default: banner only)")
    p.add_argument("--no-ftp", action="store_true",
                   help="skip the FTP port-21 probe")
    p.add_argument("--json", metavar="FILE", help="write JSON report to FILE")
    p.add_argument("--csv", metavar="FILE", help="write CSV report to FILE")
    p.add_argument("--html", metavar="FILE", help="write HTML report to FILE")
    p.add_argument("--fail-under", type=int, default=None, metavar="N",
                   help="exit with code 1 if fewer than N servers are UP")
    p.add_argument("--markdown", action="store_true",
                   help="(list) print a Markdown table")
    p.add_argument("--port", type=int, default=8080,
                   help="(web) dashboard port (default 8080)")
    p.add_argument("--open", action="store_true",
                   help="(web) open the dashboard in the browser automatically")
    p.add_argument("--progress", action="store_true",
                   help="show progress while testing")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="after the table, print per-server error details "
                        "(useful with --via-ssh)")
    p.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    return p


def main():
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args()
    console = Console(supports_color(args.no_color))

    if not os.path.isfile(resolve_data_path(args)):
        print(f"error: data file not found: {resolve_data_path(args)}", file=sys.stderr)
        return 2
    try:
        if args.command == "test":
            return cmd_test(args, console)
        if args.command == "list":
            return cmd_list(args, console)
        if args.command == "info":
            return cmd_info(args, console)
        if args.command == "web":
            return cmd_web(args, console)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
