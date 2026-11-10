# BDIX-Tester 🇧🇩

Test BDIX (Bangladesh Internet Exchange) FTP / HTTP mirror servers — from your own machine, with no ads, no trackers, no sign-ups.

**Web version:** [bdix.link/test/server](https://bdix.link/test/server) · **Self-hosted version (this repo, ad-free):** `python bdix_tester.py web`

> **বাংলায় এক কথায়:** এ প্রজেক্টে বাংলাদেশের সব BDIX FTP/মুভি/টিভি সার্ভার টেস্ট করার টুল + তাদের তালিকা আছে, এবং **বাংলাদেশের বাইরের দেশ থেকে কীভাবে BDIX ব্যবহার করবেন** তার সম্পূর্ণ গাইডও আছে — পাশাপাশি রয়েছে একটি বিজ্ঞাপন-মুক্ত লোকাল ওয়েব ড্যাশবোর্ড।

---

## What is BDIX?

[BDIX](https://bdix.net/) is the neutral internet exchange where Bangladeshi ISPs and hosting providers peer locally instead of hairpinning through overseas carriers. To keep local traffic fast and cheap, most ISPs run **open, anonymous mirror servers** on BDIX — Linux ISOs, game clients, ISP file servers, TV streams. These are hosted inside Bangladesh, which means from abroad they are usually unreachable, slow, or geo/ASN-filtered.

This repo contains:

| Component | What it does |
|---|---|
| `bdix_tester.py` | Dependency-free Python CLI that probes every server and reports UP / BLOCKED / DOWN with latency + FTP banner |
| `servers.json` | 134 curated servers (from a 2026 snapshot, ~40 responsive from outside BD) |
| `community-servers.json` | 525 community-listed servers (from [BDIX-TESTER-BY-BORANNO](https://github.com/boranno/BDIX-TESTER-BY-BORANNO), BSD-3-Clause) — use `--community` |
| `web/index.html` | Ad-free **local web dashboard** — a self-hosted alternative to the bdix.link page, no AdSense |
| `SERVERS.md` | Browsable list of all curated servers |
| `--via-ssh` | Built-in support for testing through a **Bangladesh VPS**, so you can verify/use BDIX from anywhere |

## Requirements

- Python **3.7+** (Windows, macOS, Linux) — standard library only, nothing to `pip install`
- For `--via-ssh`: an OpenSSH client (`ssh`) and a Bangladesh-based VPS with key-based login

## Quick start

```bash
git clone https://github.com/iamtanzir/BDIX-Tester.git
cd BDIX-Tester

python bdix_tester.py                 # test all 134 curated servers
python bdix_tester.py --type ftp      # only FTP servers
python bdix_tester.py web             # ad-free local dashboard → http://127.0.0.1:8080
```

## The CLI

```text
python bdix_tester.py [command] [options]

Commands:
  test     probe servers and print a table (default)
  list     list servers (use --markdown to regenerate SERVERS.md)
  info     show data-file statistics
  web      serve the ad-free web dashboard locally

Options:
  --type all|movie|tv|ftp   only servers of a category
  --name REGEX              only servers matching a name/URL pattern
  --timeout SEC             per-request timeout (default 6)
  --workers N               concurrent probes (default 24; >= 8 recommended)
  --community               use community-servers.json instead of servers.json
  --via-ssh USER@HOST       probe *through* a Bangladesh VPS via SSH (see below)
  --insecure-https          (advanced) skip TLS verification for HTTPS over --via-ssh
  --ftp-login               also attempt anonymous FTP login (default: banner only)
  --no-ftp                  skip the port-21 probe
  --json FILE | --csv FILE | --html FILE   save a report
  --fail-under N            exit code 1 if fewer than N servers are UP (CI)
  --verbose / -v            print per-server error details
  --no-color                disable ANSI colors
```

### Example output (this machine, 2026-11-10, non-BD network)

```text
#   Name                        Category  Result   HTTP        ms  URL
1   My BDIX                     Movie     UP                200       1547  http://mybdix.com/
2   Bit Net FTP                 FTP       BLOCKED           401       1837  http://dl.bitnetbd.com/
...
Total: 134   Up: 40   Blocked: 3   Down: 91   Avg HTTP latency: 2288 ms
```

Results depend entirely on where you run the test — from a BD network you'll see far more UP. From abroad, expect: public-mirror servers = UP, ISP-only routes = timeout, private IPs = permanently unreachable (see below).

## 🇧🇩 → 🌍 Using BDIX from outside Bangladesh

**Why servers don't answer from abroad:** BDIX routes exist inside Bangladeshi networks. Many BDIX servers (a) listen on ISP-internal addresses, (b) accept connections only from Bangladeshi source IPs/ASNs, or (c) are reachable globally but only via BDIX peering. A tool on your foreign machine cannot change any of that on its own — what you *can* do is put a cheap exit node **inside Bangladesh** and use it. This is exactly how the bdix.link tester works (their probe runs server-side from a host in BD).

### Option A — Bangladesh VPS + VPN (best for everyday use, incl. FileZilla) ✅ recommended

1. Get any Bangladesh-based VPS and **confirm it has BDIX peering** (ask the provider explicitly: "BDIX routed VPS" or cross-check that traffic to BDIX servers is fast). Local BD hosting providers sell these; it's a normal paid VPS, your own exit node — nothing else needed. Typical entry plans are a few hundred taka/month.
2. Install WireGuard (or OpenVPN) on it, connect from your PC/phone.
3. That's it. Run `python bdix_tester.py` locally, or point **any** client (browser, FileZilla, FoxyProxy) at it — everything now sees a Bangladesh IP with BDIX routes.

### Option B — Built-in SSH testing (no VPN setup, for users comfortable with ssh keys)

```bash
python bdix_tester.py --via-ssh root@your-bd-vps.example.com
python bdix_tester.py web --via-ssh root@your-bd-vps.example.com   # dashboard proxied via BD
```

For each server the tool opens an `ssh -L` local forward to your VPS and probes through it — so results are exactly what a Bangladeshi machine would see. Notes:

- Key-based ssh auth required (`BatchMode`); add `-i keyfile` to your `~/.ssh/config` or use `ssh-agent`.
- HTTPS through the tunnel is still **verified** against the original hostname; `--insecure-https` exists for odd certs but check first.
- FTP banner checks work over the tunnel; an interactive PASV-mode FTP **session** does not follow one `ssh -L` port. For real FTP browsing use Option A or `ssh -D` below.
- On Linux/macOS a shared ControlMaster connection is reused, so it's fast; on Windows each probe opens its own `ssh` process (slower but functional).

### Option C — SOCKS proxy (good for browsers + FileZilla, one command)

```bash
ssh -N -D 1080 root@your-bd-vps.example.com
```

Then configure your app to route through SOCKS5 `127.0.0.1:1080` **with remote DNS** (important — the server's vhost must match the hostname):

- **Firefox:** Settings → Network Settings → Manual proxy → SOCKS v5 → `127.0.0.1:1080` → tick *Proxy DNS when using SOCKS v5*
- **Chrome/Edge:** launch with `--proxy-server="socks5://127.0.0.1:1080"`, or use an extension like FoxyProxy
- **FileZilla:** Edit → Settings → Connection → FTP → FTP Proxy → select SOCKS5, `127.0.0.1:1080`
- **curl:** `curl --socks5-hostname 127.0.0.1:1080 http://example-bdix-server/`

### Servers that can *never* work from outside

These entries point at RFC-1918 / private addresses inside one ISP's LAN. No proxy/VPN trick makes them meaningful — you must be inside that ISP's network:

`10.100.100.2` (Ekushe Net), `10.16.100.244` (ICC Server/TV), `172.22.22.101`, `172.16.50.4/5/6` (SAM Online locals), `172.27.27.1` (Starplex), … (run `python bdix_tester.py info` to list them all; the community list has 27 more).

### Please

BDIX capacity is donated by local ISPs. These mirrors are for **personal, lawful** use by local users — don't torrent/redistribute copyrighted movies in bulk, don't hammer servers with crawlers, and respect each provider's terms. From a BD VPS you are renting a legitimate presence; abuse is on you.

## The ad-free local dashboard

```bash
python bdix_tester.py web            # http://127.0.0.1:8080
python bdix_tester.py web --via-ssh root@bd-vps   # same, but results come from inside BD
python bdix_tester.py web --port 9000 --open
```

One click tests every server with live progress, Open/Closed/Failed counters, search and filters — **no ads, no analytics, no account**. It is an original implementation inspired by the public [bdix.link tester](https://bdix.link/test/server); it uses this repo's data and never calls bdix.link's API.

## Contributing

- Servers come and go constantly. To add/fix one, edit `servers.json` (keep alphabetical per category) and PR.
- Regenerate a flat Markdown table with `python bdix_tester.py list --markdown` after editing `servers.json`. (`SERVERS.md` in this repo is grouped by category.)
- The community list is generated from Boranno's BSD-3-Clause repo — see `tools/import_community_list.py`.
- CI-friendly: `python bdix_tester.py --fail-under 1` exits 1 if nothing is reachable.

## Credits & data sources

- [BDIX.Link](https://bdix.link/test/server) — original public web tester; our `servers.json` snapshot is derived from its public server data. No bdix.link code is included in this repo.
- [BDIX-TESTER-BY-BORANNO](https://github.com/boranno/BDIX-TESTER-BY-BORANNO) (BSD-3-Clause, © 2022 Boranno Golder) — `community-servers.json` (~525 URLs). The BSD-3-Clause text is retained in full in `NOTICE` (required for redistribution); MIT does **not** cover that file. See `tools/import_community_list.py` for provenance.
- [BDIX](https://bdix.net/) — the exchange itself.

## License

MIT © 2026 A.K.M. Tanzir Ahmed ([@iamtanzir](https://github.com/iamtanzir)) — see [LICENSE](LICENSE). Third-party data/code retain their own licenses as noted above; see [NOTICE](NOTICE).

## Disclaimer

Availability of every listed server changes daily; entries may be stale, mirrored or removed without notice. This tool only sends small read-only requests (one HTTP GET + a TCP banner probe each) — it is a polite tester, not a scraper. Use at your own risk and in line with the hosting ISP's terms.
