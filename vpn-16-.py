#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Borg Hybrid Overmind VPN Daemon (System + Browser VPN, Multi‑Signal Detection + Process Socket Mapping + QUIC + ASN Intelligence)
===============================================================================================================================

Major capabilities:

- Auto-elevation (Windows)
- Local DNS server with auto-fallback ports + auto-repair
- DNS forwarder on 127.0.0.1:53 → Borg DNS
- System DNS hybrid auto-sync (127.0.0.1 when appropriate)
- HTTP/HTTPS proxy on 127.0.0.1:8888
- Per-domain learning (VPN server, exit IP, trust/threat, fingerprint)
- ML-style scoring + decisions: allow / harden / block
- Threat intel (local + optional feed)
- Swarm sync via shared folder
- Plugin system (routing, scoring, firewall, DPI, fingerprint)
- REST API: /status, /domains, /traffic, /traffic/domain, /swarm, /dns_port, /system_dns, /public_ip
- Tkinter GUI with:
  - VPN status (system + browser)
  - Public IP + ASN + geo
  - DNS status + system DNS status
  - Domain table + overrides + preferred VPN server
  - Global + per-domain traffic graphs
  - Live logs
  - Stealth mode

Multi-signal browser VPN detection:
- Multi-source public IP (ipify, ifconfig.me, ipinfo, icanhazip, ident.me)
- ASN + ISP + geo lookup via ipinfo.io
- Geo-drift detection vs baseline
- TLS ClientHello fingerprinting (per CONNECT tunnel)
- DNS behavior hints (DoH endpoints)
- Latency-based hints (connect + TLS handshake time)
- Process-socket mapping (browser processes talking to VPN ASNs/IPs/UDP 443)
- QUIC tunnel detection (UDP 443 from browser processes + ASN check)
- Combined confidence score for browser_vpn_active
"""

import os
import sys
import json
import time
import socket
import threading
import subprocess
from datetime import datetime
from collections import deque

import socketserver

# === AUTO-ELEVATION CHECK (Windows) ===
if os.name == "nt":
    try:
        import ctypes

        def ensure_admin():
            try:
                if not ctypes.windll.shell32.IsUserAnAdmin():
                    script = os.path.abspath(sys.argv[0])
                    params = " ".join([f'"{arg}"' for arg in sys.argv[1:]])
                    ctypes.windll.shell32.ShellExecuteW(
                        None,
                        "runas",
                        sys.executable,
                        f'"{script}" {params}',
                        None,
                        1
                    )
                    sys.exit()
            except Exception as e:
                print(f"[Borg Daemon] Elevation failed: {e}")
                sys.exit()

        ensure_admin()
    except Exception:
        pass

# Tkinter GUI
try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    tk = None
    ttk = None
    messagebox = None

# REST API
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# =========================
# Global config
# =========================

UPSTREAM_DNS = ("8.8.8.8", 53)

PREFERRED_DNS_PORTS = [5300, 5454, 5533]
DNS_SCAN_RANGE = (5300, 5400)
DNS_BIND_RETRY_DELAY = 10

CURRENT_DNS_ADDR = ("127.0.0.1", None)

DNS_FORWARDER_ADDR = ("127.0.0.1", 53)
DNS_FORWARDER_RETRY_DELAY = 10

LISTEN_PROXY = ("127.0.0.1", 8888)
REST_API_ADDR = ("0.0.0.0", 8787)

SITE_DB_PATH = "site_profiles.json"
BORG_DB_PATH = "borg_memory.json"
LOG_PATH = "vpn_site_daemon.log"

VPN_ADAPTER_KEYWORDS = [
    "wireguard",
    "tap-windows",
    "mullvad",
    "proton",
    "openvpn",
    "nordlynx",
    "pia",
    "vpn"
]

FIREWALL_RULE_PREFIX = "VPN_SITE_DAEMON_"

SUSPICIOUS_TLDS = {".ru", ".cn", ".xyz", ".top", ".click", ".work", ".loan", ".gq", ".ml", ".cf"}

LOCAL_THREAT_DOMAINS = {"malware.test", "phishing.test"}
LOCAL_THREAT_IPS = {"1.2.3.4", "5.6.7.8"}
THREAT_FEED_URL = None
THREAT_FEED_REFRESH = 3600

SWARM_SYNC_DIR = "borg_swarm"
SWARM_SYNC_INTERVAL = 60

VPN_CLIENT_PATH = r"C:\Program Files\YourVPN\vpnclient.exe"
VPN_CLIENT_ARGS = ["--connect", "auto"]

DEFAULT_USER_AGENT = "BorgHybrid/2.4 (Windows; VPN-Overmind)"
FINGERPRINT_HEADER = "X-Borg-Fingerprint"

PLUGINS_DIR = "plugins"

ML_WEIGHTS = {
    "bias": 0.0,
    "visits": -0.02,
    "trust": -0.8,
    "threat": 1.0,
    "tld_risk": 0.5,
    "threat_feed": 1.2,
}

PUBLIC_IP_BASELINE_PATH = "public_ip_baseline.json"

PUBLIC_IP_PROVIDERS = [
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://ipinfo.io/ip",
    "https://icanhazip.com",
    "https://ident.me",
]

DOH_HOSTS = {
    "cloudflare-dns.com",
    "mozilla.cloudflare-dns.com",
    "dns.google",
    "security.cloudflare-dns.com",
    "one.one.one.one",
    "doh.opendns.com",
    "doh.cleanbrowsing.org",
}

# Known browser process names (Windows)
BROWSER_PROCESSES = {
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "opera.exe",
    "opera_browser.exe",
    "brave.exe",
    "vivaldi.exe",
}

# Known VPN ASNs / IP hints (extendable)
KNOWN_VPN_ASN_PREFIXES = [
    "AS9009",   # M247
    "AS16509",  # Amazon
    "AS13335",  # Cloudflare
    "AS14618",  # Amazon
    "AS16276",  # OVH
]

KNOWN_VPN_ISP_HINTS = [
    "M247",
    "Cloudflare",
    "OVH",
    "DigitalOcean",
    "Hetzner",
    "Leaseweb",
    "Linode",
    "Scaleway",
]

KNOWN_VPN_HOST_HINTS = [
    "mullvad",
    "protonvpn",
    "nordvpn",
    "pia",
    "surfshark",
    "expressvpn",
    "warp",
    "mozilla",
    "opera",
]

# =========================
# Simple logger
# =========================

LOG_LOCK = threading.Lock()
LOG_BUFFER = []
LOG_BUFFER_MAX = 500

def log(msg):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    with LOG_LOCK:
        print(line)
        LOG_BUFFER.append(line)
        if len(LOG_BUFFER) > LOG_BUFFER_MAX:
            LOG_BUFFER.pop(0)
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

def get_log_tail(n=200):
    with LOG_LOCK:
        return LOG_BUFFER[-n:]

# =========================
# Plugin system
# =========================

class PluginManager:
    def __init__(self, directory):
        self.directory = directory
        self.plugins = []
        self._load_plugins()

    def _load_plugins(self):
        if not os.path.isdir(self.directory):
            return
        sys.path.insert(0, os.path.abspath(self.directory))
        for fname in os.listdir(self.directory):
            if not fname.endswith(".py"):
                continue
            modname = os.path.splitext(fname)[0]
            try:
                mod = __import__(modname)
                self.plugins.append(mod)
                log(f"[PLUGIN] Loaded plugin: {modname}")
            except Exception as e:
                log(f"[PLUGIN] Failed to load {modname}: {e}")

    def call_hook(self, hook_name, *args, **kwargs):
        result = None
        for p in self.plugins:
            func = getattr(p, hook_name, None)
            if callable(func):
                try:
                    r = func(*args, **kwargs)
                    if r is not None:
                        result = r
                except Exception as e:
                    log(f"[PLUGIN] Error in {p.__name__}.{hook_name}: {e}")
        return result

PLUGIN_MANAGER = PluginManager(PLUGINS_DIR)

# =========================
# Site profile store
# =========================

class SiteProfileStore:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                log(f"[STORE] Loaded site profiles from {self.path} ({len(self.data)} entries)")
            except Exception as e:
                log(f"[STORE] Failed to load site profiles: {e}")
                self.data = {}
        else:
            self.data = {}

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    def update_site(self, domain, vpn_server_name, exit_ip):
        with self.lock:
            entry = self.data.get(domain, {})
            entry["vpn_server_name"] = vpn_server_name
            entry["last_exit_ip"] = exit_ip
            entry["last_seen"] = time.time()
            entry.setdefault("preferred_vpn_server", None)
            self.data[domain] = entry
            self._save()
        log(f"[STORE] {domain} → server={vpn_server_name}, ip={exit_ip}")

    def set_preferred_server(self, domain, server_name):
        with self.lock:
            entry = self.data.get(domain)
            if not entry:
                entry = {
                    "vpn_server_name": server_name,
                    "last_exit_ip": "",
                    "last_seen": time.time(),
                    "preferred_vpn_server": server_name
                }
            else:
                entry["preferred_vpn_server"] = server_name
            self.data[domain] = entry
            self._save()
        log(f"[STORE] Preferred VPN server for {domain} = {server_name}")

    def get_site(self, domain):
        with self.lock:
            return self.data.get(domain)

    def delete_site(self, domain):
        with self.lock:
            if domain in self.data:
                del self.data[domain]
                self._save()
                log(f"[STORE] Deleted site profile for {domain}")

    def all_sites(self):
        with self.lock:
            return dict(self.data)

SITE_STORE = SiteProfileStore(SITE_DB_PATH)

# =========================
# Borg Memory
# =========================

class BorgMemory:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {
            "domains": {},
            "servers": {}
        }
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                log(f"[BORG] Loaded memory from {self.path}")
            except Exception as e:
                log(f"[BORG] Failed to load memory: {e}")
                self.data = {"domains": {}, "servers": {}}
        else:
            self.data = {"domains": {}, "servers": {}}

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    def update_domain(self, domain, server_name, exit_ip, threat_delta=0.0, trust_delta=0.1):
        with self.lock:
            d = self.data["domains"].get(domain, {
                "visits": 0,
                "last_seen": 0,
                "server_name": server_name,
                "last_exit_ip": exit_ip,
                "threat_score": 0.0,
                "trust_score": 0.0,
                "decision_override": None,
                "fingerprint_id": None
            })
            d["visits"] += 1
            d["last_seen"] = time.time()
            d["server_name"] = server_name
            d["last_exit_ip"] = exit_ip
            d["threat_score"] = max(0.0, d["threat_score"] + threat_delta)
            d["trust_score"] = max(0.0, d["trust_score"] + trust_delta)
            if d.get("fingerprint_id") is None:
                d["fingerprint_id"] = f"fp-{abs(hash(domain)) & 0xFFFFFFFF:08x}"
            self.data["domains"][domain] = d
            self._save()

    def update_server(self, server_name, exit_ip, reliability_delta=0.1):
        with self.lock:
            s = self.data["servers"].get(server_name, {
                "usage_count": 0,
                "last_exit_ip": exit_ip,
                "last_seen": 0,
                "reliability_score": 0.0
            })
            s["usage_count"] += 1
            s["last_exit_ip"] = exit_ip
            s["last_seen"] = time.time()
            s["reliability_score"] = max(0.0, s["reliability_score"] + reliability_delta)
            self.data["servers"][server_name] = s
            self._save()

    def get_domain(self, domain):
        with self.lock:
            return self.data["domains"].get(domain)

    def get_server(self, server_name):
        with self.lock:
            return self.data["servers"].get(server_name)

    def all_domains(self):
        with self.lock:
            return dict(self.data["domains"])

    def all_servers(self):
        with self.lock:
            return dict(self.data["servers"])

    def set_override(self, domain, decision_or_none):
        with self.lock:
            d = self.data["domains"].get(domain)
            if not d:
                return
            d["decision_override"] = decision_or_none
            self.data["domains"][domain] = d
            self._save()
            log(f"[BORG] Override for {domain} set to {decision_or_none}")

    def purge_domain(self, domain):
        with self.lock:
            if domain in self.data["domains"]:
                del self.data["domains"][domain]
                self._save()
                log(f"[BORG] Purged domain memory for {domain}")

    def get_fingerprint(self, domain):
        with self.lock:
            d = self.data["domains"].get(domain)
            if not d:
                return None
            return d.get("fingerprint_id")

    def set_fingerprint(self, domain, fp):
        with self.lock:
            d = self.data["domains"].get(domain)
            if not d:
                return
            d["fingerprint_id"] = fp
            self.data["domains"][domain] = d
            self._save()

BORG_MEMORY = BorgMemory(BORG_DB_PATH)

# =========================
# Swarm sync
# =========================

def swarm_sync_worker():
    while True:
        try:
            if not os.path.isdir(SWARM_SYNC_DIR):
                os.makedirs(SWARM_SYNC_DIR, exist_ok=True)

            swarm_path = os.path.join(SWARM_SYNC_DIR, "borg_memory.json")

            if os.path.exists(swarm_path):
                try:
                    with open(swarm_path, "r", encoding="utf-8") as f:
                        swarm_data = json.load(f)
                    with BORG_MEMORY.lock:
                        BORG_MEMORY.data = swarm_data
                        BORG_MEMORY._save()
                    log("[SWARM] Pulled memory from swarm.")
                except Exception as e:
                    log(f"[SWARM] Failed to pull from swarm: {e}")

            try:
                with BORG_MEMORY.lock:
                    data = BORG_MEMORY.data
                with open(swarm_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                log("[SWARM] Pushed memory to swarm.")
            except Exception as e:
                log(f"[SWARM] Failed to push to swarm: {e}")

        except Exception as e:
            log(f"[SWARM] Worker error: {e}")

        time.sleep(SWARM_SYNC_INTERVAL)

# =========================
# Threat intel
# =========================

THREAT_DOMAINS = set(LOCAL_THREAT_DOMAINS)
THREAT_IPS = set(LOCAL_THREAT_IPS)

def threat_feed_worker():
    global THREAT_DOMAINS, THREAT_IPS
    while True:
        if THREAT_FEED_URL:
            try:
                import urllib.request
                with urllib.request.urlopen(THREAT_FEED_URL, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                domains = set(data.get("domains", []))
                ips = set(data.get("ips", []))
                THREAT_DOMAINS |= domains
                THREAT_IPS |= ips
                log(f"[THREAT] Updated from feed: +{len(domains)} domains, +{len(ips)} ips")
            except Exception as e:
                log(f"[THREAT] Feed update failed: {e}")
        time.sleep(THREAT_FEED_REFRESH)

# =========================
# Multi-signal public IP / Browser VPN detection
# =========================

PUBLIC_IP_LOCK = threading.Lock()
PUBLIC_IP_STATE = {
    "baseline_ip": None,
    "baseline_asn": None,
    "baseline_isp": None,
    "baseline_country": None,
    "baseline_region": None,
    "baseline_city": None,
    "current_ip": None,
    "current_asn": None,
    "current_isp": None,
    "current_country": None,
    "current_region": None,
    "current_city": None,
    "providers": {},
    "last_update": 0,
    "browser_vpn_active": False,
    "confidence": 0.0,
    "signals": {
        "ip_changed": False,
        "asn_changed": False,
        "geo_changed": False,
        "tls_fp_changed": False,
        "doh_activity": False,
        "latency_jump": False,
        "proc_vpn_activity": False,
        "quic_vpn_activity": False,
    },
    "tls_fingerprints": {},
    "last_latency_ms": None,
    "proc_vpn_details": [],
    "quic_vpn_details": [],
}

def _fetch_ip_from(url):
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.79.1"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            ip = resp.read().decode("utf-8").strip()
            socket.inet_aton(ip)
            return ip
    except Exception:
        return None

def _fetch_public_ip_multi():
    results = {}
    for u in PUBLIC_IP_PROVIDERS:
        ip = _fetch_ip_from(u)
        results[u] = ip
    ips = [ip for ip in results.values() if ip]
    if not ips:
        return None, results
    counts = {}
    for ip in ips:
        counts[ip] = counts.get(ip, 0) + 1
    best_ip = max(counts.items(), key=lambda x: x[1])[0]
    return best_ip, results

def _fetch_ipinfo(ip):
    try:
        import urllib.request
        url = f"https://ipinfo.io/{ip}/json"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        org = data.get("org", "")
        asn = None
        isp = None
        if org:
            parts = org.split(" ", 1)
            if parts:
                asn = parts[0]
                if len(parts) > 1:
                    isp = parts[1]
        loc = data.get("loc", "")
        country = data.get("country")
        region = data.get("region")
        city = data.get("city")
        return {
            "asn": asn,
            "isp": isp,
            "country": country,
            "region": region,
            "city": city,
            "loc": loc,
        }
    except Exception as e:
        log(f"[PUBIP] ipinfo lookup failed for {ip}: {e}")
        return {}

def _load_public_ip_baseline():
    if os.path.exists(PUBLIC_IP_BASELINE_PATH):
        try:
            with open(PUBLIC_IP_BASELINE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except Exception:
            return None
    return None

def _save_public_ip_baseline(state):
    try:
        with open(PUBLIC_IP_BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        log(f"[PUBIP] Baseline public IP state saved: {state.get('baseline_ip')}")
    except Exception as e:
        log(f"[PUBIP] Failed to save baseline IP state: {e}")

def public_ip_worker():
    while True:
        ip, providers = _fetch_public_ip_multi()
        now = time.time()
        with PUBLIC_IP_LOCK:
            baseline_state = _load_public_ip_baseline() if PUBLIC_IP_STATE["baseline_ip"] is None else None
            if baseline_state:
                PUBLIC_IP_STATE.update({
                    "baseline_ip": baseline_state.get("baseline_ip"),
                    "baseline_asn": baseline_state.get("baseline_asn"),
                    "baseline_isp": baseline_state.get("baseline_isp"),
                    "baseline_country": baseline_state.get("baseline_country"),
                    "baseline_region": baseline_state.get("baseline_region"),
                    "baseline_city": baseline_state.get("baseline_city"),
                })

            if PUBLIC_IP_STATE["baseline_ip"] is None and ip:
                info = _fetch_ipinfo(ip)
                PUBLIC_IP_STATE["baseline_ip"] = ip
                PUBLIC_IP_STATE["baseline_asn"] = info.get("asn")
                PUBLIC_IP_STATE["baseline_isp"] = info.get("isp")
                PUBLIC_IP_STATE["baseline_country"] = info.get("country")
                PUBLIC_IP_STATE["baseline_region"] = info.get("region")
                PUBLIC_IP_STATE["baseline_city"] = info.get("city")
                _save_public_ip_baseline({
                    "baseline_ip": PUBLIC_IP_STATE["baseline_ip"],
                    "baseline_asn": PUBLIC_IP_STATE["baseline_asn"],
                    "baseline_isp": PUBLIC_IP_STATE["baseline_isp"],
                    "baseline_country": PUBLIC_IP_STATE["baseline_country"],
                    "baseline_region": PUBLIC_IP_STATE["baseline_region"],
                    "baseline_city": PUBLIC_IP_STATE["baseline_city"],
                })

            PUBLIC_IP_STATE["current_ip"] = ip
            PUBLIC_IP_STATE["providers"] = providers
            PUBLIC_IP_STATE["last_update"] = now

            if ip:
                info = _fetch_ipinfo(ip)
                PUBLIC_IP_STATE["current_asn"] = info.get("asn")
                PUBLIC_IP_STATE["current_isp"] = info.get("isp")
                PUBLIC_IP_STATE["current_country"] = info.get("country")
                PUBLIC_IP_STATE["current_region"] = info.get("region")
                PUBLIC_IP_STATE["current_city"] = info.get("city")

            sig = PUBLIC_IP_STATE["signals"]
            sig["ip_changed"] = (
                PUBLIC_IP_STATE["baseline_ip"] is not None
                and ip is not None
                and ip != PUBLIC_IP_STATE["baseline_ip"]
            )
            sig["asn_changed"] = (
                PUBLIC_IP_STATE["baseline_asn"] is not None
                and PUBLIC_IP_STATE["current_asn"] is not None
                and PUBLIC_IP_STATE["baseline_asn"] != PUBLIC_IP_STATE["current_asn"]
            )
            sig["geo_changed"] = (
                PUBLIC_IP_STATE["baseline_country"] is not None
                and PUBLIC_IP_STATE["current_country"] is not None
                and (
                    PUBLIC_IP_STATE["baseline_country"] != PUBLIC_IP_STATE["current_country"]
                    or PUBLIC_IP_STATE["baseline_region"] != PUBLIC_IP_STATE["current_region"]
                )
            )

            confidence = 0.0
            if sig["ip_changed"]:
                confidence += 0.3
            if sig["asn_changed"]:
                confidence += 0.4
            if sig["geo_changed"]:
                confidence += 0.2
            if sig["tls_fp_changed"]:
                confidence += 0.2
            if sig["doh_activity"]:
                confidence += 0.1
            if sig["latency_jump"]:
                confidence += 0.1
            if sig["proc_vpn_activity"]:
                confidence += 0.6
            if sig["quic_vpn_activity"]:
                confidence += 0.6

            confidence = min(1.0, confidence)
            PUBLIC_IP_STATE["confidence"] = confidence
            PUBLIC_IP_STATE["browser_vpn_active"] = confidence >= 0.5

        time.sleep(10)

def get_public_ip_state():
    with PUBLIC_IP_LOCK:
        return json.loads(json.dumps(PUBLIC_IP_STATE))

def register_tls_fingerprint(domain, fp):
    if not fp:
        return
    with PUBLIC_IP_LOCK:
        fps = PUBLIC_IP_STATE["tls_fingerprints"]
        old = fps.get(domain)
        if old is None:
            fps[domain] = fp
        elif old != fp:
            PUBLIC_IP_STATE["signals"]["tls_fp_changed"] = True
            fps[domain] = fp

def register_doh_activity(domain):
    if not domain:
        return
    host = domain.lower()
    if any(h in host for h in DOH_HOSTS):
        with PUBLIC_IP_LOCK:
            PUBLIC_IP_STATE["signals"]["doh_activity"] = True

def register_latency_sample(lat_ms):
    if lat_ms is None:
        return
    with PUBLIC_IP_LOCK:
        last = PUBLIC_IP_STATE["last_latency_ms"]
        if last is not None and lat_ms > last * 1.5 and lat_ms - last > 40:
            PUBLIC_IP_STATE["signals"]["latency_jump"] = True
        PUBLIC_IP_STATE["last_latency_ms"] = lat_ms

# =========================
# Process-socket mapping + QUIC detection (Windows, with ASN/ISP intelligence)
# =========================

def _get_pid_to_name_map():
    procs = {}
    if os.name != "nt":
        return procs
    try:
        out = subprocess.check_output(["tasklist", "/FO", "CSV"], encoding="utf-8", errors="ignore")
        for line in out.splitlines()[1:]:
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2:
                name = parts[0]
                pid = parts[1]
                try:
                    pid_int = int(pid)
                except ValueError:
                    continue
                procs[pid_int] = name.lower()
    except Exception as e:
        log(f"[PROC] tasklist failed: {e}")
    return procs

def _parse_netstat():
    tcp = []
    udp = []
    if os.name != "nt":
        return tcp, udp
    try:
        out = subprocess.check_output(["netstat", "-ano"], encoding="utf-8", errors="ignore")
    except Exception as e:
        log(f"[PROC] netstat failed: {e}")
        return tcp, udp

    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        proto = parts[0].lower()
        if proto not in ("tcp", "udp"):
            continue
        local = parts[1]
        remote = parts[2]
        pid = parts[-1]
        try:
            pid_int = int(pid)
        except ValueError:
            continue
        tcp_or_udp = tcp if proto == "tcp" else udp
        tcp_or_udp.append((proto, local, remote, pid_int))
    return tcp, udp

def _extract_ip(remote):
    host, _, _ = remote.rpartition(":")
    return host.strip()

def _ipinfo_for_remote(ip):
    if not ip:
        return {}
    try:
        return _fetch_ipinfo(ip)
    except Exception:
        return {}

def _is_vpn_asn(asn, isp):
    if not asn and not isp:
        return False
    if asn and any(asn.startswith(prefix) for prefix in KNOWN_VPN_ASN_PREFIXES):
        return True
    if isp:
        for hint in KNOWN_VPN_ISP_HINTS:
            if hint.lower() in isp.lower():
                return True
    return False

def process_socket_worker():
    while True:
        if os.name != "nt":
            time.sleep(5)
            continue
        try:
            pid_map = _get_pid_to_name_map()
            tcp, udp = _parse_netstat()

            browser_hits = []
            quic_hits = []

            for proto, local, remote, pid in tcp + udp:
                pname = pid_map.get(pid, "")
                if pname not in BROWSER_PROCESSES:
                    continue

                ip = _extract_ip(remote)
                if not ip:
                    continue

                info = _ipinfo_for_remote(ip)
                asn = info.get("asn")
                isp = info.get("isp")

                hit_detail = {
                    "process": pname,
                    "pid": pid,
                    "proto": proto,
                    "remote": remote,
                    "ip": ip,
                    "asn": asn,
                    "isp": isp,
                }

                vpn_like = _is_vpn_asn(asn, isp) or any(h in ip.lower() for h in KNOWN_VPN_HOST_HINTS)

                if vpn_like:
                    browser_hits.append(hit_detail)

                if proto == "udp":
                    host, _, port = remote.rpartition(":")
                    port = port.strip()
                    if port == "443" and vpn_like:
                        quic_hits.append(hit_detail)

            with PUBLIC_IP_LOCK:
                PUBLIC_IP_STATE["signals"]["proc_vpn_activity"] = bool(browser_hits)
                PUBLIC_IP_STATE["signals"]["quic_vpn_activity"] = bool(quic_hits)
                PUBLIC_IP_STATE["proc_vpn_details"] = browser_hits
                PUBLIC_IP_STATE["quic_vpn_details"] = quic_hits

            if browser_hits:
                log(f"[PROC] Browser VPN activity via process sockets + ASN/ISP: {browser_hits}")
            if quic_hits:
                log(f"[PROC] QUIC VPN activity via UDP 443 + ASN/ISP: {quic_hits}")

        except Exception as e:
            log(f"[PROC] process_socket_worker error: {e}")

        time.sleep(5)

# =========================
# VPN status provider + auto-reconnect
# =========================

class VPNStatusProvider:
    def __init__(self):
        self.last_adapter_name = None
        self.last_exit_ip = None
        self.last_update = 0
        self.cache_ttl = 5
        self.last_active = False

    def _is_windows(self):
        return os.name == "nt"

    def _parse_ipconfig(self, text):
        adapters = []
        current_name = None
        current_ipv4 = None

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue

            if line.endswith(":") and "adapter" in line.lower():
                if current_name is not None:
                    adapters.append((current_name, current_ipv4))
                current_name = line.rstrip(":")
                current_ipv4 = None
            elif "IPv4 Address" in line or "IPv4-adress" in line or "IPv4-adresse" in line:
                parts = line.split(":")
                if len(parts) >= 2:
                    ip = parts[1].strip()
                    ip = ip.split("(")[0].strip()
                    current_ipv4 = ip

        if current_name is not None:
            adapters.append((current_name, current_ipv4))

        return adapters

    def _refresh_windows(self):
        try:
            out = subprocess.check_output(["ipconfig"], encoding="utf-8", errors="ignore")
        except Exception as e:
            log(f"[VPN] ipconfig failed: {e}")
            self.last_adapter_name = None
            self.last_exit_ip = None
            return

        adapters = self._parse_ipconfig(out)
        vpn_candidates = []
        for name, ip in adapters:
            if not ip:
                continue
            lname = name.lower()
            if any(k in lname for k in VPN_ADAPTER_KEYWORDS):
                vpn_candidates.append((name, ip))

        if vpn_candidates:
            name, ip = vpn_candidates[0]
            if name != self.last_adapter_name or ip != self.last_exit_ip:
                log(f"[VPN] Active VPN adapter: {name}, exit IP: {ip}")
            self.last_adapter_name = name
            self.last_exit_ip = ip
        else:
            if self.last_adapter_name or self.last_exit_ip:
                log("[VPN] VPN adapter lost")
            self.last_adapter_name = None
            self.last_exit_ip = None

    def _refresh(self):
        now = time.time()
        if now - self.last_update < self.cache_ttl:
            return
        self.last_update = now

        if self._is_windows():
            self._refresh_windows()
        else:
            self.last_adapter_name = None
            self.last_exit_ip = None

        active = self.last_adapter_name is not None and self.last_exit_ip is not None
        if self.last_active and not active:
            log("[VPN] Transition: ACTIVE → INACTIVE")
            BORG.guardian_on_vpn_drop()
            self._auto_reconnect()
        self.last_active = active

    def _auto_reconnect(self):
        if not VPN_CLIENT_PATH or not os.path.exists(VPN_CLIENT_PATH):
            log("[VPN] Auto-reconnect: VPN client path not configured or missing.")
            return
        try:
            cmd = [VPN_CLIENT_PATH] + VPN_CLIENT_ARGS
            log(f"[VPN] Auto-reconnect: launching client: {cmd}")
            subprocess.Popen(cmd)
        except Exception as e:
            log(f"[VPN] Auto-reconnect failed: {e}")

    def is_system_vpn_active(self) -> bool:
        self._refresh()
        return self.last_adapter_name is not None and self.last_exit_ip is not None

    def is_browser_vpn_active(self) -> bool:
        st = get_public_ip_state()
        return st.get("browser_vpn_active", False)

    def is_any_vpn_active(self) -> bool:
        return self.is_system_vpn_active() or self.is_browser_vpn_active()

    def get_current_vpn_server_name(self):
        self._refresh()
        return self.last_adapter_name

    def get_current_exit_ip(self):
        self._refresh()
        return self.last_exit_ip

    def get_exit_ip_for_server(self, server_name: str):
        self._refresh()
        if self.last_adapter_name == server_name:
            return self.last_exit_ip
        return None

VPN = VPNStatusProvider()

# =========================
# Firewall enforcement (Windows)
# =========================

class FirewallManager:
    def __init__(self):
        self.is_windows = (os.name == "nt")

    def _run(self, cmd):
        try:
            log(f"[FW] Running: {cmd}")
            subprocess.check_call(cmd, shell=True)
        except Exception as e:
            log(f"[FW] Command failed: {e}")

    def _rule_name_for_ip(self, ip):
        return f"{FIREWALL_RULE_PREFIX}{ip}"

    def ensure_block_rule_for_ip(self, ip):
        if not self.is_windows:
            return
        rule_name = self._rule_name_for_ip(ip)
        cmd = (
            f'netsh advfirewall firewall add rule '
            f'name="{rule_name}" '
            f'dir=out action=block remoteip={ip} '
            f'enable=yes'
        )
        override = PLUGIN_MANAGER.call_hook("firewall_rule_for_ip", ip, cmd)
        if override:
            cmd = override
        self._run(cmd)

    def remove_rule_for_ip(self, ip):
        if not self.is_windows:
            return
        rule_name = self._rule_name_for_ip(ip)
        cmd = f'netsh advfirewall firewall delete rule name="{rule_name}"'
        self._run(cmd)

FIREWALL = FirewallManager()

# =========================
# Borg AI + ML scoring
# =========================

class BorgAI:
    def __init__(self, memory: BorgMemory):
        self.memory = memory

    def _tld_threat_bonus(self, domain):
        domain_lower = domain.lower()
        for tld in SUSPICIOUS_TLDS:
            if domain_lower.endswith(tld):
                return 1.0
        return 0.0

    def _ml_score(self, features):
        s = ML_WEIGHTS["bias"]
        s += ML_WEIGHTS["visits"] * features.get("visits", 0)
        s += ML_WEIGHTS["trust"] * features.get("trust", 0.0)
        s += ML_WEIGHTS["threat"] * features.get("threat", 0.0)
        s += ML_WEIGHTS["tld_risk"] * features.get("tld_risk", 0.0)
        s += ML_WEIGHTS["threat_feed"] * features.get("threat_feed", 0.0)
        return s

    def score_domain(self, domain, server_name, exit_ip):
        d = self.memory.get_domain(domain)
        tld_risk = self._tld_threat_bonus(domain)
        threat_feed_flag = 1.0 if (domain in THREAT_DOMAINS or exit_ip in THREAT_IPS) else 0.0

        if not d:
            features = {
                "visits": 0,
                "trust": 0.0,
                "threat": 0.0,
                "tld_risk": tld_risk,
                "threat_feed": threat_feed_flag,
            }
        else:
            features = {
                "visits": d.get("visits", 0),
                "trust": d.get("trust_score", 0.0),
                "threat": d.get("threat_score", 0.0),
                "tld_risk": tld_risk,
                "threat_feed": threat_feed_flag,
            }

        ml_score = self._ml_score(features)
        threat = max(0.0, min(1.0, 0.5 + ml_score / 4.0))
        importance = min(1.0, 0.1 + features["visits"] * 0.05)
        trust = features["trust"]

        override_scores = PLUGIN_MANAGER.call_hook(
            "score_domain",
            domain,
            server_name,
            exit_ip,
            {"importance": importance, "trust": trust, "threat": threat}
        )
        if override_scores:
            importance = override_scores.get("importance", importance)
            trust = override_scores.get("trust", trust)
            threat = override_scores.get("threat", threat)

        return {
            "importance": importance,
            "trust": trust,
            "threat": threat,
            "ml_score": ml_score,
        }

    def decide_route(self, domain, server_name, exit_ip):
        d = self.memory.get_domain(domain)
        override = d.get("decision_override") if d else None

        scores = self.score_domain(domain, server_name, exit_ip)
        threat = scores["threat"]
        trust = scores["trust"]
        importance = scores["importance"]

        plugin_decision = PLUGIN_MANAGER.call_hook(
            "decide_route",
            domain,
            server_name,
            exit_ip,
            scores
        )
        if plugin_decision in ("allow", "harden", "block"):
            decision = plugin_decision
            log(f"[BORG:CORTEX] domain={domain} PLUGIN_DECISION={decision} "
                f"(importance={importance:.2f} trust={trust:.2f} threat={threat:.2f})")
            return decision, scores

        if override in ("allow", "harden", "block"):
            decision = override
            log(f"[BORG:CORTEX] domain={domain} OVERRIDE={override} "
                f"(importance={importance:.2f} trust={trust:.2f} threat={threat:.2f})")
            return decision, scores

        if threat > 0.7:
            decision = "block"
        elif threat > 0.4 or trust < 0.2:
            decision = "harden"
        else:
            decision = "allow"

        log(f"[BORG:CORTEX] domain={domain} server={server_name} ip={exit_ip} "
            f"importance={importance:.2f} trust={trust:.2f} threat={threat:.2f} decision={decision}")
        return decision, scores

    def guardian_on_vpn_drop(self):
        log("[BORG:GUARDIAN] VPN drop detected. Hunter mode: all learned domains considered unsafe until VPN returns.")

    def guardian_on_ip_mismatch(self, domain, expected_ip, actual_ip):
        log(f"[BORG:GUARDIAN] IP mismatch for {domain}: expected={expected_ip}, actual={actual_ip}. "
            "Marking domain as higher threat.")
        self.memory.update_domain(domain, server_name="", exit_ip=actual_ip or expected_ip, threat_delta=0.3, trust_delta=-0.1)

    def guardian_on_suspicious(self, domain, reason):
        log(f"[BORG:GUARDIAN] Suspicious activity on {domain}: {reason}")
        self.memory.update_domain(domain, server_name="", exit_ip="", threat_delta=0.2, trust_delta=-0.1)

BORG = BorgAI(BORG_MEMORY)

# =========================
# DNS server with auto-fallback
# =========================

DNS_STATUS_LOCK = threading.Lock()
DNS_STATUS = {
    "port": None,
    "ok": False,
    "last_error": None,
}

def parse_qname(data, offset):
    labels = []
    while True:
        if offset >= len(data):
            return "", offset
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if offset + 1 + length > len(data):
            return "", offset
        labels.append(data[offset+1:offset+1+length].decode("utf-8", errors="ignore"))
        offset += 1 + length
    return ".".join(labels), offset

def build_qname(domain):
    parts = domain.split(".")
    res = b""
    for p in parts:
        res += bytes([len(p)]) + p.encode("utf-8")
    res += b"\x00"
    return res

def forward_dns(data, addr, sock):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as upstream:
        upstream.settimeout(3)
        try:
            upstream.sendto(data, UPSTREAM_DNS)
            resp, _ = upstream.recvfrom(512)
            sock.sendto(resp, addr)
        except Exception as e:
            log(f"[DNS] Upstream error: {e}")

def handle_dns_query(data, addr, sock):
    if len(data) < 12:
        return
    tid = data[0:2]
    qdcount = int.from_bytes(data[4:6], "big")
    offset = 12
    if qdcount < 1:
        return

    qname, offset = parse_qname(data, offset)
    if not qname:
        forward_dns(data, addr, sock)
        return

    qtype = int.from_bytes(data[offset:offset+2], "big")
    qclass = int.from_bytes(data[offset+2:offset+4], "big")

    if qtype != 1 or qclass != 1:
        forward_dns(data, addr, sock)
        return

    profile = SITE_STORE.get_site(qname)
    if profile:
        preferred = profile.get("preferred_vpn_server")
        server_name = preferred or profile["vpn_server_name"]
        stored_ip = profile["last_exit_ip"]
        ip = VPN.get_exit_ip_for_server(server_name) or stored_ip

        try:
            ip_bytes = socket.inet_aton(ip)
        except OSError:
            log(f"[DNS] Invalid stored IP for {qname}: {ip}, forwarding upstream")
            forward_dns(data, addr, sock)
            return

        decision, scores = BORG.decide_route(qname, server_name, ip)
        if decision == "block":
            log(f"[DNS] Blocking {qname} due to Borg decision")
            resp_flags = b"\x81\x83"
            header = tid + resp_flags + (1).to_bytes(2, "big") + (0).to_bytes(2, "big") + (0).to_bytes(2, "big") + (0).to_bytes(2, "big")
            question = build_qname(qname) + qtype.to_bytes(2, "big") + qclass.to_bytes(2, "big")
            resp = header + question
            sock.sendto(resp, addr)
            return

        resp_flags = b"\x81\x80"
        qdcount_bytes = (1).to_bytes(2, "big")
        ancount_bytes = (1).to_bytes(2, "big")
        nscount_bytes = (0).to_bytes(2, "big")
        arcount_bytes = (0).to_bytes(2, "big")

        header = tid + resp_flags + qdcount_bytes + ancount_bytes + nscount_bytes + arcount_bytes
        question = build_qname(qname) + qtype.to_bytes(2, "big") + qclass.to_bytes(2, "big")

        name = build_qname(qname)
        atype = (1).to_bytes(2, "big")
        aclass = (1).to_bytes(2, "big")
        ttl = (60).to_bytes(4, "big")
        rdlength = (4).to_bytes(2, "big")
        rdata = ip_bytes

        answer = name + atype + aclass + ttl + rdlength + rdata
        resp = header + question + answer
        sock.sendto(resp, addr)
        log(f"[DNS] {qname} → {ip} (server={server_name}, decision={decision})")
    else:
        forward_dns(data, addr, sock)

def find_free_dns_port():
    for p in PREFERRED_DNS_PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue

    start, end = DNS_SCAN_RANGE
    for p in range(start, end + 1):
        if p in PREFERRED_DNS_PORTS:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue

    return None

def dns_server():
    global CURRENT_DNS_ADDR
    while True:
        try:
            port = find_free_dns_port()
            if port is None:
                with DNS_STATUS_LOCK:
                    DNS_STATUS["port"] = None
                    DNS_STATUS["ok"] = False
                    DNS_STATUS["last_error"] = "No free DNS port found"
                log("[DNS] No free port found, retrying later...")
                time.sleep(DNS_BIND_RETRY_DELAY)
                continue

            CURRENT_DNS_ADDR = ("127.0.0.1", port)
            with DNS_STATUS_LOCK:
                DNS_STATUS["port"] = port
                DNS_STATUS["ok"] = True
                DNS_STATUS["last_error"] = None

            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.bind(CURRENT_DNS_ADDR)
            except OSError as e:
                with DNS_STATUS_LOCK:
                    DNS_STATUS["ok"] = False
                    DNS_STATUS["last_error"] = str(e)
                log(f"[DNS] Bind failed on {CURRENT_DNS_ADDR[0]}:{port}: {e}, retrying...")
                s.close()
                time.sleep(DNS_BIND_RETRY_DELAY)
                continue

            log(f"[DNS] Listening on {CURRENT_DNS_ADDR[0]}:{port}")
            while True:
                try:
                    data, addr = s.recvfrom(512)
                    threading.Thread(target=handle_dns_query, args=(data, addr, s), daemon=True).start()
                except OSError as e:
                    with DNS_STATUS_LOCK:
                        DNS_STATUS["ok"] = False
                        DNS_STATUS["last_error"] = str(e)
                    log(f"[DNS] Socket error: {e}, restarting DNS server...")
                    s.close()
                    break
        except Exception as e:
            with DNS_STATUS_LOCK:
                DNS_STATUS["ok"] = False
                DNS_STATUS["last_error"] = str(e)
            log(f"[DNS] Fatal error in DNS loop: {e}, retrying...")
            time.sleep(DNS_BIND_RETRY_DELAY)

# =========================
# DNS forwarder (port 53 → CURRENT_DNS_ADDR)
# =========================

DNS_FORWARDER_STATUS_LOCK = threading.Lock()
DNS_FORWARDER_STATUS = {
    "ok": False,
    "last_error": None,
    "port": DNS_FORWARDER_ADDR[1],
}

def dns_forwarder():
    global CURRENT_DNS_ADDR
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.bind(DNS_FORWARDER_ADDR)
            except OSError as e:
                with DNS_FORWARDER_STATUS_LOCK:
                    DNS_FORWARDER_STATUS["ok"] = False
                    DNS_FORWARDER_STATUS["last_error"] = str(e)
                log(f"[DNS-FWD] Failed to bind 127.0.0.1:{DNS_FORWARDER_ADDR[1]}: {e}, retrying...")
                s.close()
                time.sleep(DNS_FORWARDER_RETRY_DELAY)
                continue

            with DNS_FORWARDER_STATUS_LOCK:
                DNS_FORWARDER_STATUS["ok"] = True
                DNS_FORWARDER_STATUS["last_error"] = None

            log(f"[DNS-FWD] Forwarder listening on 127.0.0.1:{DNS_FORWARDER_ADDR[1]}")

            while True:
                try:
                    data, addr = s.recvfrom(512)
                    target = CURRENT_DNS_ADDR
                    if target[1] is None:
                        log("[DNS-FWD] No active Borg DNS port, dropping packet.")
                        continue
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as upstream:
                        upstream.settimeout(3)
                        upstream.sendto(data, target)
                        try:
                            resp, _ = upstream.recvfrom(512)
                            s.sendto(resp, addr)
                        except socket.timeout:
                            log("[DNS-FWD] Timeout waiting for Borg DNS response.")
                except OSError as e:
                    with DNS_FORWARDER_STATUS_LOCK:
                        DNS_FORWARDER_STATUS["ok"] = False
                        DNS_FORWARDER_STATUS["last_error"] = str(e)
                    log(f"[DNS-FWD] Socket error: {e}, restarting forwarder...")
                    s.close()
                    break
        except Exception as e:
            with DNS_FORWARDER_STATUS_LOCK:
                DNS_FORWARDER_STATUS["ok"] = False
                DNS_FORWARDER_STATUS["last_error"] = str(e)
            log(f"[DNS-FWD] Fatal error in forwarder loop: {e}, retrying...")
            time.sleep(DNS_FORWARDER_RETRY_DELAY)

# =========================
# Traffic stats
# =========================

TRAFFIC_LOCK = threading.Lock()
TRAFFIC_POINTS = deque(maxlen=300)
DOMAIN_TRAFFIC = {}

def record_traffic(domain, bytes_in, bytes_out):
    now = time.time()
    with TRAFFIC_LOCK:
        TRAFFIC_POINTS.append((now, bytes_in, bytes_out))
        if domain:
            dq = DOMAIN_TRAFFIC.get(domain)
            if dq is None:
                dq = deque(maxlen=300)
                DOMAIN_TRAFFIC[domain] = dq
            dq.append((now, bytes_in, bytes_out))

def get_traffic_snapshot():
    with TRAFFIC_LOCK:
        return list(TRAFFIC_POINTS)

def get_domain_traffic_snapshot(domain):
    with TRAFFIC_LOCK:
        dq = DOMAIN_TRAFFIC.get(domain)
        if not dq:
            return []
        return list(dq)

# =========================
# HTTP/HTTPS proxy with TLS fingerprint + latency + DoH hints
# =========================

def _peek_tls_client_hello(sock, max_bytes=512):
    sock.settimeout(3)
    try:
        data = sock.recv(max_bytes, socket.MSG_PEEK)
        if not data:
            return None
        fp = hash(data[:64])
        return f"ja3-lite-{fp & 0xFFFFFFFF:08x}"
    except Exception:
        return None

class ProxyHandler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            line = self.rfile.readline().decode("utf-8", errors="ignore")
            if not line:
                return

            parts = line.strip().split()
            if len(parts) < 2:
                return

            method, target = parts[0], parts[1]

            if method.upper() == "CONNECT":
                host, _, port = target.partition(":")
                port = int(port) if port else 443
                domain = host
                self.learn_domain(domain)
                self.handle_connect(host, port, domain)
            else:
                headers = {}
                raw_headers = [line]
                while True:
                    hline = self.rfile.readline().decode("utf-8", errors="ignore")
                    raw_headers.append(hline)
                    if hline in ("\r\n", "\n", ""):
                        break
                    k, _, v = hline.partition(":")
                    headers[k.strip().lower()] = v.strip()

                host = headers.get("host")
                domain = None
                if host:
                    domain = host.split(":")[0]
                    self.learn_domain(domain)

                self.handle_http(method, target, headers, raw_headers, domain)
        except Exception as e:
            log(f"[PROXY] Error in handler: {e}")

    def learn_domain(self, domain):
        if not domain:
            return

        # REAL-TIME browser VPN detection: use live signals, not just the 10s worker flag
        browser_state = get_public_ip_state()
        realtime_browser_vpn = (
            browser_state["signals"].get("proc_vpn_activity", False) or
            browser_state["signals"].get("quic_vpn_activity", False)
        )

        system_vpn = VPN.is_system_vpn_active()
        any_vpn = system_vpn or realtime_browser_vpn

        if any_vpn:
            if system_vpn:
                server_name = VPN.get_current_vpn_server_name() or "system-vpn"
                exit_ip = VPN.get_current_exit_ip() or browser_state.get("current_ip") or "0.0.0.0"
            else:
                server_name = "browser-vpn"
                exit_ip = browser_state.get("current_ip") or "0.0.0.0"

            SITE_STORE.update_site(domain, server_name, exit_ip)
            BORG_MEMORY.update_domain(domain, server_name, exit_ip)
            BORG_MEMORY.update_server(server_name, exit_ip)
            if exit_ip != "0.0.0.0":
                FIREWALL.ensure_block_rule_for_ip(exit_ip)
            log(f"[LEARN] {domain} via {server_name} ({exit_ip}) [system_vpn={system_vpn}, browser_vpn_rt={realtime_browser_vpn}]")
        else:
            log(f"[LEARN] Skipping {domain}, no VPN (system or browser) active (rt signals false)")
            BORG.guardian_on_vpn_drop()

    def _apply_fingerprint_headers(self, domain, headers_list):
        if not domain:
            return headers_list
        fp = BORG_MEMORY.get_fingerprint(domain)
        if fp is None:
            fp = f"fp-{abs(hash(domain)) & 0xFFFFFFFF:08x}"
            BORG_MEMORY.set_fingerprint(domain, fp)

        new_headers = []
        ua_set = False
        for h in headers_list:
            if h.lower().startswith("user-agent:"):
                new_headers.append(f"User-Agent: {DEFAULT_USER_AGENT}\r\n")
                ua_set = True
            else:
                new_headers.append(h)
        if not ua_set:
            new_headers.insert(1, f"User-Agent: {DEFAULT_USER_AGENT}\r\n")

        new_headers.insert(1, f"{FINGERPRINT_HEADER}: {fp}\r\n")

        override = PLUGIN_MANAGER.call_hook("fingerprint_headers", domain, new_headers)
        if override:
            new_headers = override
        return new_headers

    def _dpi_http_request(self, method, target, headers, domain):
        path = target
        ua = headers.get("user-agent", "")
        if any(x in path.lower() for x in ["login", "auth", "password"]):
            BORG.guardian_on_suspicious(domain or "unknown", f"Sensitive path: {path}")
        if "curl" in ua.lower() or "python-requests" in ua.lower():
            BORG.guardian_on_suspicious(domain or "unknown", f"Scripted UA: {ua}")
        if domain:
            register_doh_activity(domain)
        PLUGIN_MANAGER.call_hook("dpi_http_request", method, target, headers, domain)

    def handle_connect(self, host, port, domain):
        start = time.time()
        try:
            upstream = socket.create_connection((host, port))
        except Exception as e:
            log(f"[PROXY] CONNECT to {host}:{port} failed: {e}")
            self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        connect_latency = (time.time() - start) * 1000.0
        register_latency_sample(connect_latency)

        fp = _peek_tls_client_hello(self.connection)
        if fp:
            register_tls_fingerprint(domain or host, fp)

        self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")

        def pipe(src, dst, direction, dom):
            total = 0
            try:
                while True:
                    data = src.recv(4096)
                    if not data:
                        break
                    total += len(data)
                    dst.sendall(data)
            except Exception:
                pass
            finally:
                if direction == "in":
                    record_traffic(dom, total, 0)
                else:
                    record_traffic(dom, 0, total)
                try:
                    dst.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass

        t1 = threading.Thread(target=pipe, args=(self.connection, upstream, "out", domain), daemon=True)
        t2 = threading.Thread(target=pipe, args=(upstream, self.connection, "in", domain), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    def handle_http(self, method, target, headers, raw_headers, domain):
        host = headers.get("host")
        if not host:
            self.wfile.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            return

        host_name, _, host_port = host.partition(":")
        port = int(host_port) if host_port else 80

        self._dpi_http_request(method, target, headers, domain)

        start = time.time()
        try:
            upstream = socket.create_connection((host_name, port))
        except Exception as e:
            log(f"[PROXY] HTTP connect to {host_name}:{port} failed: {e}")
            self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        connect_latency = (time.time() - start) * 1000.0
        register_latency_sample(connect_latency)

        first_line = f"{method} {target} HTTP/1.1\r\n"
        try:
            headers_only = raw_headers[1:]
            headers_only = self._apply_fingerprint_headers(domain, headers_only)

            upstream.sendall(first_line.encode("utf-8"))
            for hline in headers_only:
                upstream.sendall(hline.encode("utf-8"))
        except Exception as e:
            log(f"[PROXY] Failed to send request upstream: {e}")
            upstream.close()
            return

        def pipe(src, dst, direction, dom):
            total = 0
            try:
                while True:
                    data = src.recv(4096)
                    if not data:
                        break
                    total += len(data)
                    dst.sendall(data)
            except Exception:
                pass
            finally:
                if direction == "in":
                    record_traffic(dom, total, 0)
                else:
                    record_traffic(dom, 0, total)
                try:
                    dst.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass

        t1 = threading.Thread(target=pipe, args=(self.connection, upstream, "out", domain), daemon=True)
        t2 = threading.Thread(target=pipe, args=(upstream, self.connection, "in", domain), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

def start_proxy():
    server = socketserver.ThreadingTCPServer(LISTEN_PROXY, ProxyHandler)
    log(f"[PROXY] Listening on {LISTEN_PROXY[0]}:{LISTEN_PROXY[1]}")
    server.serve_forever()

# =========================
# System DNS Hybrid Auto-Sync
# =========================

SYSTEM_DNS_STATUS_LOCK = threading.Lock()
SYSTEM_DNS_STATUS = {
    "adapter": None,
    "dns_set": False,
    "dns_value": None,
    "last_error": None,
}

def _get_active_adapter_name_windows():
    try:
        out = subprocess.check_output(
            ["netsh", "interface", "show", "interface"],
            encoding="utf-8",
            errors="ignore"
        )
    except Exception as e:
        log(f"[DNS-SYNC] netsh show interface failed: {e}")
        return None

    lines = out.splitlines()
    for line in lines:
        if "Connected" in line and "Loopback" not in line:
            parts = line.split()
            if len(parts) >= 4:
                name = " ".join(parts[3:])
                return name
    return None

def _get_current_dns_for_adapter(adapter_name):
    try:
        out = subprocess.check_output(
            ["netsh", "interface", "ip", "show", "dns", f"name={adapter_name}"],
            encoding="utf-8",
            errors="ignore"
        )
    except Exception:
        return None

    for line in out.splitlines():
        line = line.strip()
        if line.lower().startswith("statistically configured dns servers"):
            continue
        if line and line[0].isdigit():
            return line.strip()
    return None

def _set_dns_for_adapter(adapter_name, ip):
    try:
        cmd = [
            "netsh", "interface", "ip", "set", "dns",
            f"name={adapter_name}",
            "static", ip, "primary"
        ]
        log(f"[DNS-SYNC] Setting DNS for '{adapter_name}' to {ip}")
        subprocess.check_call(cmd, shell=False)
        return True
    except Exception as e:
        log(f"[DNS-SYNC] Failed to set DNS for '{adapter_name}' to {ip}: {e}")
        return False

def dns_sync_worker():
    while True:
        if os.name != "nt":
            time.sleep(30)
            continue

        try:
            adapter = _get_active_adapter_name_windows()
            if not adapter:
                with SYSTEM_DNS_STATUS_LOCK:
                    SYSTEM_DNS_STATUS["adapter"] = None
                    SYSTEM_DNS_STATUS["dns_set"] = False
                    SYSTEM_DNS_STATUS["dns_value"] = None
                    SYSTEM_DNS_STATUS["last_error"] = "No active adapter"
                time.sleep(10)
                continue

            current_dns = _get_current_dns_for_adapter(adapter)
            vpn_active = VPN.is_system_vpn_active()
            with DNS_STATUS_LOCK:
                dns_ok = DNS_STATUS["ok"]

            desired_dns = "127.0.0.1"

            need_set = False
            if dns_ok and vpn_active:
                if current_dns != desired_dns:
                    need_set = True
            else:
                if current_dns not in (desired_dns, None, ""):
                    need_set = True

            if need_set:
                ok = _set_dns_for_adapter(adapter, desired_dns)
                with SYSTEM_DNS_STATUS_LOCK:
                    SYSTEM_DNS_STATUS["adapter"] = adapter
                    SYSTEM_DNS_STATUS["dns_set"] = ok
                    SYSTEM_DNS_STATUS["dns_value"] = desired_dns if ok else current_dns
                    SYSTEM_DNS_STATUS["last_error"] = None if ok else "Failed to set DNS"
            else:
                with SYSTEM_DNS_STATUS_LOCK:
                    SYSTEM_DNS_STATUS["adapter"] = adapter
                    SYSTEM_DNS_STATUS["dns_set"] = (current_dns == desired_dns)
                    SYSTEM_DNS_STATUS["dns_value"] = current_dns
                    SYSTEM_DNS_STATUS["last_error"] = None

        except Exception as e:
            log(f"[DNS-SYNC] Worker error: {e}")
            with SYSTEM_DNS_STATUS_LOCK:
                SYSTEM_DNS_STATUS["last_error"] = str(e)

        time.sleep(10)

# =========================
# REST API
# =========================

class BorgAPIHandler(BaseHTTPRequestHandler):
    def _send_json(self, obj, code=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/status":
            self.handle_status()
        elif parsed.path == "/domains":
            self.handle_domains()
        elif parsed.path == "/traffic":
            self.handle_traffic()
        elif parsed.path == "/traffic/domain":
            self.handle_domain_traffic(parsed)
        elif parsed.path == "/swarm":
            self.handle_swarm()
        elif parsed.path == "/dns_port":
            self.handle_dns_port()
        elif parsed.path == "/system_dns":
            self.handle_system_dns()
        elif parsed.path == "/public_ip":
            self.handle_public_ip()
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/override":
            self.handle_override()
        elif parsed.path == "/preferred_server":
            self.handle_preferred_server()
        elif parsed.path == "/purge":
            self.handle_purge()
        else:
            self._send_json({"error": "not found"}, 404)

    def handle_status(self):
        sys_active = VPN.is_system_vpn_active()
        browser_active = VPN.is_browser_vpn_active()
        any_active = VPN.is_any_vpn_active()
        server = VPN.get_current_vpn_server_name()
        ip = VPN.get_current_exit_ip()
        with DNS_STATUS_LOCK:
            dns_port = DNS_STATUS["port"]
            dns_ok = DNS_STATUS["ok"]
            dns_err = DNS_STATUS["last_error"]
        with SYSTEM_DNS_STATUS_LOCK:
            sys_dns = SYSTEM_DNS_STATUS.copy()
        with DNS_FORWARDER_STATUS_LOCK:
            fwd = DNS_FORWARDER_STATUS.copy()
        pub = get_public_ip_state()
        self._send_json({
            "system_vpn_active": sys_active,
            "browser_vpn_active": browser_active,
            "any_vpn_active": any_active,
            "vpn_server": server,
            "vpn_exit_ip": ip,
            "dns_port": dns_port,
            "dns_ok": dns_ok,
            "dns_error": dns_err,
            "system_dns": sys_dns,
            "dns_forwarder": fwd,
            "public_ip": pub,
        })

    def handle_domains(self):
        domains = BORG_MEMORY.all_domains()
        out = {}
        for d, info in domains.items():
            server = info.get("server_name", "")
            ip = info.get("last_exit_ip", "")
            visits = info.get("visits", 0)
            trust = info.get("trust_score", 0.0)
            threat = info.get("threat_score", 0.0)
            override = info.get("decision_override")
            profile = SITE_STORE.get_site(d) or {}
            preferred = profile.get("preferred_vpn_server")

            decision, scores = BORG.decide_route(d, server, ip or "0.0.0.0")
            out[d] = {
                "server": server,
                "ip": ip,
                "visits": visits,
                "trust": trust,
                "threat": threat,
                "decision": decision,
                "override": override,
                "preferred_server": preferred,
                "scores": scores,
            }
        self._send_json(out)

    def handle_traffic(self):
        points = get_traffic_snapshot()
        self._send_json({"points": points})

    def handle_domain_traffic(self, parsed):
        qs = parse_qs(parsed.query)
        domain = qs.get("name", [None])[0]
        if not domain:
            self._send_json({"error": "missing name"}, 400)
            return
        points = get_domain_traffic_snapshot(domain)
        self._send_json({"domain": domain, "points": points})

    def handle_swarm(self):
        with BORG_MEMORY.lock:
            borg_data = BORG_MEMORY.data
        sites = SITE_STORE.all_sites()
        self._send_json({
            "borg_memory": borg_data,
            "site_profiles": sites,
        })

    def handle_override(self):
        body = self._read_json()
        domain = body.get("domain")
        decision = body.get("decision")
        if not domain:
            self._send_json({"error": "missing domain"}, 400)
            return
        if decision not in (None, "allow", "harden", "block"):
            self._send_json({"error": "invalid decision"}, 400)
            return
        BORG_MEMORY.set_override(domain, decision)
        self._send_json({"status": "ok", "domain": domain, "decision": decision})

    def handle_preferred_server(self):
        body = self._read_json()
        domain = body.get("domain")
        server = body.get("server")
        if not domain or not server:
            self._send_json({"error": "missing domain or server"}, 400)
            return
        SITE_STORE.set_preferred_server(domain, server)
        self._send_json({"status": "ok", "domain": domain, "server": server})

    def handle_purge(self):
        body = self._read_json()
        domain = body.get("domain")
        if not domain:
            self._send_json({"error": "missing domain"}, 400)
            return
        BORG_MEMORY.purge_domain(domain)
        SITE_STORE.delete_site(domain)
        self._send_json({"status": "ok", "domain": domain})

    def handle_dns_port(self):
        with DNS_STATUS_LOCK:
            port = DNS_STATUS["port"]
            ok = DNS_STATUS["ok"]
            err = DNS_STATUS["last_error"]
        self._send_json({
            "dns_port": port,
            "dns_ok": ok,
            "dns_error": err,
        })

    def handle_system_dns(self):
        with SYSTEM_DNS_STATUS_LOCK:
            sys_dns = SYSTEM_DNS_STATUS.copy()
        with DNS_FORWARDER_STATUS_LOCK:
            fwd = DNS_FORWARDER_STATUS.copy()
        self._send_json({
            "system_dns": sys_dns,
            "dns_forwarder": fwd,
        })

    def handle_public_ip(self):
        self._send_json(get_public_ip_state())

def start_rest_api():
    server = HTTPServer(REST_API_ADDR, BorgAPIHandler)
    log(f"[REST] Listening on http://{REST_API_ADDR[0]}:{REST_API_ADDR[1]}")
    server.serve_forever()

# =========================
# Tkinter GUI
# =========================

class BorgGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Borg VPN Overmind - System + Browser VPN (Multi-Signal + Process/QUIC + ASN)")
        self.root.geometry("1450x880")

        self.stealth = False
        self.pref_server_var = tk.StringVar(value="")

        self._build_layout()
        self._schedule_update()

    def _build_layout(self):
        self.root.rowconfigure(0, weight=0)
        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)

        top = ttk.Frame(self.root)
        top.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        top.columnconfigure(7, weight=1)

        self.vpn_status_label = ttk.Label(top, text="VPN: Unknown", font=("Consolas", 11, "bold"))
        self.vpn_status_label.grid(row=0, column=0, sticky="w", padx=5)

        self.vpn_ip_label = ttk.Label(top, text="Exit IP: -", font=("Consolas", 11))
        self.vpn_ip_label.grid(row=0, column=1, sticky="w", padx=5)

        self.browser_vpn_label = ttk.Label(top, text="Browser VPN: Unknown", font=("Consolas", 11))
        self.browser_vpn_label.grid(row=0, column=2, sticky="w", padx=5)

        self.public_ip_label = ttk.Label(top, text="Public IP: -", font=("Consolas", 11))
        self.public_ip_label.grid(row=0, column=3, sticky="w", padx=5)

        self.public_asn_label = ttk.Label(top, text="ASN/ISP: -", font=("Consolas", 11))
        self.public_asn_label.grid(row=0, column=4, sticky="w", padx=5)

        self.public_geo_label = ttk.Label(top, text="Geo: -", font=("Consolas", 11))
        self.public_geo_label.grid(row=0, column=5, sticky="w", padx=5)

        self.dns_status_label = ttk.Label(top, text="DNS: Unknown", font=("Consolas", 11))
        self.dns_status_label.grid(row=1, column=0, sticky="w", padx=5)

        self.system_dns_status_label = ttk.Label(top, text="System DNS: Unknown", font=("Consolas", 11))
        self.system_dns_status_label.grid(row=1, column=1, sticky="w", padx=5)

        self.stealth_button = ttk.Button(top, text="Enter Stealth Mode", command=self.toggle_stealth)
        self.stealth_button.grid(row=0, column=7, sticky="e", padx=5)

        middle = ttk.Panedwindow(self.root, orient="horizontal")
        middle.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)

        domains_frame = ttk.Frame(middle)
        domains_frame.rowconfigure(4, weight=1)
        domains_frame.columnconfigure(0, weight=1)

        controls_frame = ttk.Frame(domains_frame)
        controls_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        controls_frame.columnconfigure(0, weight=1)

        self.override_label = ttk.Label(controls_frame, text="Selected domain override:")
        self.override_label.grid(row=0, column=0, sticky="w", padx=2)

        self.btn_allow = ttk.Button(controls_frame, text="ALLOW", command=lambda: self.set_override("allow"))
        self.btn_allow.grid(row=0, column=1, padx=2)

        self.btn_harden = ttk.Button(controls_frame, text="HARDEN", command=lambda: self.set_override("harden"))
        self.btn_harden.grid(row=0, column=2, padx=2)

        self.btn_block = ttk.Button(controls_frame, text="BLOCK", command=lambda: self.set_override("block"))
        self.btn_block.grid(row=0, column=3, padx=2)

        self.btn_clear = ttk.Button(controls_frame, text="CLEAR OVERRIDE", command=lambda: self.set_override(None))
        self.btn_clear.grid(row=0, column=4, padx=2)

        self.btn_purge = ttk.Button(controls_frame, text="PURGE DOMAIN", command=self.purge_domain)
        self.btn_purge.grid(row=0, column=5, padx=2)

        pref_frame = ttk.Frame(domains_frame)
        pref_frame.grid(row=1, column=0, sticky="ew", pady=(0, 5))
        pref_frame.columnconfigure(1, weight=1)

        ttk.Label(pref_frame, text="Preferred VPN server for selected domain:").grid(row=0, column=0, sticky="w", padx=2)
        self.pref_server_combo = ttk.Combobox(pref_frame, textvariable=self.pref_server_var, state="readonly")
        self.pref_server_combo.grid(row=0, column=1, sticky="ew", padx=2)
        self.btn_apply_pref = ttk.Button(pref_frame, text="APPLY", command=self.apply_preferred_server)
        self.btn_apply_pref.grid(row=0, column=2, padx=2)

        columns = ("domain", "server", "pref_server", "ip", "visits", "trust", "threat", "decision", "override")
        self.domains_tree = ttk.Treeview(domains_frame, columns=columns, show="headings", height=16)
        for col in columns:
            self.domains_tree.heading(col, text=col.upper())
        self.domains_tree.column("domain", width=220)
        self.domains_tree.column("server", width=220)
        self.domains_tree.column("pref_server", width=220)
        self.domains_tree.column("ip", width=120)
        self.domains_tree.column("visits", width=60, anchor="e")
        self.domains_tree.column("trust", width=70, anchor="e")
        self.domains_tree.column("threat", width=70, anchor="e")
        self.domains_tree.column("decision", width=90, anchor="center")
        self.domains_tree.column("override", width=110, anchor="center")

        self.domains_tree.grid(row=2, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(domains_frame, orient="vertical", command=self.domains_tree.yview)
        self.domains_tree.configure(yscroll=scrollbar.set)
        scrollbar.grid(row=2, column=1, sticky="ns")

        graphs_frame = ttk.Frame(domains_frame)
        graphs_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(5, 0))
        graphs_frame.rowconfigure(0, weight=1)
        graphs_frame.rowconfigure(1, weight=1)
        graphs_frame.columnconfigure(0, weight=1)

        self.graph_canvas_global = tk.Canvas(graphs_frame, height=150, bg="black")
        self.graph_canvas_global.grid(row=0, column=0, sticky="ew")

        self.graph_canvas_domain = tk.Canvas(graphs_frame, height=150, bg="black")
        self.graph_canvas_domain.grid(row=1, column=0, sticky="ew", pady=(5, 0))

        middle.add(domains_frame, weight=3)

        logs_frame = ttk.Frame(middle)
        logs_frame.rowconfigure(0, weight=1)
        logs_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(logs_frame, wrap="none", font=("Consolas", 9))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(logs_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.grid(row=0, column=1, sticky="ns")

        middle.add(logs_frame, weight=2)

        self.domains_tree.bind("<<TreeviewSelect>>", lambda e: self._update_domain_graph())

    def toggle_stealth(self):
        if not self.stealth:
            self.stealth = True
            self.stealth_button.config(text="Exit Stealth Mode")
            self.root.withdraw()
            log("[GUI] Entered stealth mode (window hidden, daemon running).")
        else:
            self.stealth = False
            self.stealth_button.config(text="Enter Stealth Mode")
            self.root.deiconify()
            log("[GUI] Exited stealth mode (window visible).")

    def _update_vpn_status(self):
        sys_active = VPN.is_system_vpn_active()
        browser_state = get_public_ip_state()
        browser_active = browser_state.get("browser_vpn_active", False)
        confidence = browser_state.get("confidence", 0.0)
        server = VPN.get_current_vpn_server_name()
        ip = VPN.get_current_exit_ip()

        if sys_active:
            self.vpn_status_label.config(text=f"System VPN: ACTIVE ({server})", foreground="green")
            self.vpn_ip_label.config(text=f"Exit IP: {ip}")
        else:
            self.vpn_status_label.config(text="System VPN: INACTIVE", foreground="red")
            self.vpn_ip_label.config(text="Exit IP: -")

        if browser_active:
            self.browser_vpn_label.config(
                text=f"Browser VPN: ACTIVE (confidence={confidence:.2f})",
                foreground="green"
            )
        else:
            self.browser_vpn_label.config(
                text=f"Browser VPN: INACTIVE (confidence={confidence:.2f})",
                foreground="orange"
            )

        baseline_ip = browser_state.get("baseline_ip")
        current_ip = browser_state.get("current_ip")
        self.public_ip_label.config(
            text=f"Public IP: {current_ip or '-'} (baseline: {baseline_ip or '-'})"
        )

        asn = browser_state.get("current_asn") or "-"
        isp = browser_state.get("current_isp") or "-"
        self.public_asn_label.config(
            text=f"ASN/ISP: {asn} / {isp}"
        )

        country = browser_state.get("current_country") or "-"
        region = browser_state.get("current_region") or "-"
        city = browser_state.get("current_city") or "-"
        self.public_geo_label.config(
            text=f"Geo: {country}, {region}, {city}"
        )

    def _update_dns_status(self):
        with DNS_STATUS_LOCK:
            port = DNS_STATUS["port"]
            ok = DNS_STATUS["ok"]
            err = DNS_STATUS["last_error"]

        with DNS_FORWARDER_STATUS_LOCK:
            fwd_ok = DNS_FORWARDER_STATUS["ok"]
            fwd_err = DNS_FORWARDER_STATUS["last_error"]
            fwd_port = DNS_FORWARDER_STATUS["port"]

        if ok and port:
            text = f"DNS: OK (Borg 127.0.0.1:{port})"
            color = "green"
        else:
            text = f"DNS: ERROR ({err})" if err else "DNS: NOT READY"
            color = "red"

        if fwd_ok:
            text += f" | FWD: 127.0.0.1:{fwd_port}"
        else:
            if fwd_err:
                text += f" | FWD ERR: {fwd_err}"

        self.dns_status_label.config(text=text, foreground=color)

    def _update_system_dns_status(self):
        with SYSTEM_DNS_STATUS_LOCK:
            st = SYSTEM_DNS_STATUS.copy()

        adapter = st.get("adapter")
        dns_set = st.get("dns_set")
        dns_value = st.get("dns_value")
        err = st.get("last_error")

        if adapter is None:
            self.system_dns_status_label.config(
                text="System DNS: No active adapter",
                foreground="red"
            )
            return

        if dns_set and dns_value == "127.0.0.1":
            self.system_dns_status_label.config(
                text=f"System DNS: OK ({adapter} → 127.0.0.1)",
                foreground="green"
            )
        else:
            msg = f"System DNS: {adapter} → {dns_value or 'unknown'}"
            if err:
                msg += f" (err: {err})"
            self.system_dns_status_label.config(
                text=msg,
                foreground="orange"
            )

    def _update_domains_table(self):
        for item in self.domains_tree.get_children():
            self.domains_tree.delete(item)

        domains = BORG_MEMORY.all_domains()
        for domain, info in domains.items():
            server = info.get("server_name", "")
            ip = info.get("last_exit_ip", "")
            visits = info.get("visits", 0)
            trust = info.get("trust_score", 0.0)
            threat = info.get("threat_score", 0.0)
            override = info.get("decision_override")
            profile = SITE_STORE.get_site(domain) or {}
            pref_server = profile.get("preferred_vpn_server", "")

            decision, _ = BORG.decide_route(domain, server, ip or "0.0.0.0")

            self.domains_tree.insert(
                "",
                "end",
                values=(
                    domain,
                    server,
                    pref_server,
                    ip,
                    visits,
                    f"{trust:.2f}",
                    f"{threat:.2f}",
                    decision.upper(),
                    (override.upper() if override else "")
                )
            )

        servers = list(BORG_MEMORY.all_servers().keys())
        current_vpn = VPN.get_current_vpn_server_name()
        if current_vpn and current_vpn not in servers:
            servers.append(current_vpn)
        servers = sorted(set(servers))
        self.pref_server_combo["values"] = servers

        domain = self._get_selected_domain()
        if domain:
            profile = SITE_STORE.get_site(domain) or {}
            pref = profile.get("preferred_vpn_server") or ""
            if pref in servers:
                self.pref_server_var.set(pref)
            else:
                self.pref_server_var.set("")

    def _update_logs(self):
        tail = get_log_tail(200)
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, "\n".join(tail))
        self.log_text.see(tk.END)

    def _draw_graph(self, canvas, points, title):
        canvas.delete("all")
        w = int(canvas.winfo_width() or 1)
        h = int(canvas.winfo_height() or 1)

        if not points:
            canvas.create_text(10, 10, anchor="nw", fill="white",
                               text=f"{title} (no data)", font=("Consolas", 8))
            return

        t0 = points[0][0]
        t1 = points[-1][0]
        dt = max(1.0, t1 - t0)

        max_bytes = max(max(p[1], p[2]) for p in points) or 1

        last_x = None
        last_y_in = None
        last_y_out = None

        for ts, bin_, bout in points:
            x = int((ts - t0) / dt * (w - 10)) + 5
            y_in = h - int(bin_ / max_bytes * (h - 10)) - 5
            y_out = h - int(bout / max_bytes * (h - 10)) - 5

            if last_x is not None:
                canvas.create_line(last_x, last_y_in, x, y_in, fill="lime")
                canvas.create_line(last_x, last_y_out, x, y_out, fill="cyan")

            last_x, last_y_in, last_y_out = x, y_in, y_out

        canvas.create_text(10, 10, anchor="nw", fill="white",
                           text=f"{title} (green=in, cyan=out)", font=("Consolas", 8))

    def _update_global_graph(self):
        points = get_traffic_snapshot()
        self._draw_graph(self.graph_canvas_global, points, "Global Traffic")

    def _update_domain_graph(self):
        domain = self._get_selected_domain()
        if not domain:
            self._draw_graph(self.graph_canvas_domain, [], "Domain Traffic (none selected)")
            return
        points = get_domain_traffic_snapshot(domain)
        self._draw_graph(self.graph_canvas_domain, points, f"Domain Traffic: {domain}")

    def _update_all(self):
        self._update_vpn_status()
        self._update_dns_status()
        self._update_system_dns_status()
        self._update_domains_table()
        self._update_logs()
        self._update_global_graph()
        self._update_domain_graph()

    def _schedule_update(self):
        self._update_all()
        self.root.after(1000, self._schedule_update)

    def _get_selected_domain(self):
        sel = self.domains_tree.selection()
        if not sel:
            return None
        item = self.domains_tree.item(sel[0])
        vals = item.get("values", [])
        if not vals:
            return None
        return vals[0]

    def set_override(self, decision_or_none):
        domain = self._get_selected_domain()
        if not domain:
            if messagebox:
                messagebox.showinfo("Borg Override", "Select a domain first.")
            return
        BORG_MEMORY.set_override(domain, decision_or_none)

    def purge_domain(self):
        domain = self._get_selected_domain()
        if not domain:
            if messagebox:
                messagebox.showinfo("Purge Domain", "Select a domain first.")
            return
        if messagebox:
            if not messagebox.askyesno("Purge Domain", f"Really purge all memory and profile for:\n{domain}?"):
                return
        BORG_MEMORY.purge_domain(domain)
        SITE_STORE.delete_site(domain)

    def apply_preferred_server(self):
        domain = self._get_selected_domain()
        if not domain:
            if messagebox:
                messagebox.showinfo("Preferred Server", "Select a domain first.")
            return
        server = self.pref_server_var.get().strip()
        if not server:
            if messagebox:
                messagebox.showinfo("Preferred Server", "Select a VPN server from the dropdown.")
            return
        SITE_STORE.set_preferred_server(domain, server)
        log(f"[GUI] Preferred VPN server for {domain} set to {server}")

# =========================
# Windows service stubs
# =========================

def install_windows_service():
    if os.name != "nt":
        log("[SERVICE] Install requested but OS is not Windows.")
        return
    log("[SERVICE] Stub: here you would create a Windows service using sc.exe or pywin32.")

def remove_windows_service():
    if os.name != "nt":
        log("[SERVICE] Remove requested but OS is not Windows.")
        return
    log("[SERVICE] Stub: here you would delete the Windows service.")

def run_service_mode():
    log("[SERVICE] Running in service mode (headless loop).")
    while True:
        time.sleep(60)

# =========================
# Daemon main
# =========================

def start_background_services():
    threading.Thread(target=dns_server, daemon=True).start()
    threading.Thread(target=dns_forwarder, daemon=True).start()
    threading.Thread(target=start_proxy, daemon=True).start()
    threading.Thread(target=swarm_sync_worker, daemon=True).start()
    threading.Thread(target=threat_feed_worker, daemon=True).start()
    threading.Thread(target=start_rest_api, daemon=True).start()
    threading.Thread(target=dns_sync_worker, daemon=True).start()
    threading.Thread(target=public_ip_worker, daemon=True).start()
    threading.Thread(target=process_socket_worker, daemon=True).start()

    for domain, profile in SITE_STORE.all_sites().items():
        ip = profile.get("last_exit_ip")
        server_name = profile.get("vpn_server_name")
        if ip and server_name:
            FIREWALL.ensure_block_rule_for_ip(ip)
            BORG_MEMORY.update_domain(domain, server_name, ip, threat_delta=0.0, trust_delta=0.05)
            BORG_MEMORY.update_server(server_name, ip, reliability_delta=0.05)

def run_gui_mode():
    if tk is None or ttk is None:
        log("[GUI] Tkinter not available. Running headless.")
        while True:
            time.sleep(60)
    else:
        root = tk.Tk()
        app = BorgGUI(root)
        root.mainloop()

def main():
    log("=== Borg VPN Overmind starting (System + Browser VPN, Multi-Signal + Process/QUIC + ASN Detection, RT Browser Saving) ===")
    log(f"Python: {sys.version}")
    log(f"OS: {os.name}")
    log(f"Site DB: {SITE_DB_PATH}")
    log(f"Borg DB: {BORG_DB_PATH}")
    log(f"Log file: {LOG_PATH}")

    if "--install-service" in sys.argv:
        install_windows_service()
        return
    if "--remove-service" in sys.argv:
        remove_windows_service()
        return

    start_background_services()

    if "--service" in sys.argv:
        run_service_mode()
    else:
        run_gui_mode()

if __name__ == "__main__":
    main()
