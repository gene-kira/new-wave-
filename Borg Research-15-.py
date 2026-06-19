#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Mythic Swarm + Forklift LLM Node
Full unified mega-engine:
- Evolutionary swarm project generator
- Distributed mesh + swarm TCP networking
- Advanced Tk GUI dashboard
- LLM bridge + orchestrator (OpenAI / Anthropic / Stub)
- ForkliftLinear wrapper + HF model loading (LLaMA-style)
- RPC text generation node
- Headless auto-detect, Windows service mode, Linux systemd mode
"""

import os
import sys
import math
import time
import json
import random
import logging
import threading
import queue
import socket
import textwrap
import importlib
import importlib.util
import traceback
from typing import Dict, List, Tuple, Optional

# ============================================================
# Optional heavy deps (torch / HF)
# ============================================================

try:
    import torch
    import torch.nn as nn
    from transformers import AutoTokenizer, AutoModelForCausalLM
    HAS_TORCH = True
except Exception:
    HAS_TORCH = False
    torch = None
    nn = object  # type: ignore
    AutoTokenizer = None
    AutoModelForCausalLM = None

HAS_CUDA = bool(HAS_TORCH and torch.cuda.is_available()) if HAS_TORCH else False
NUM_GPUS = torch.cuda.device_count() if HAS_CUDA else 0
DEFAULT_DEVICE = torch.device("cuda" if HAS_CUDA else "cpu") if HAS_TORCH else "cpu"

PRIMARY_MODEL_NAME = os.getenv("MYTHIC_PRIMARY_MODEL", "meta-llama/Llama-3-8b-instruct")  # example

# ============================================================
# Tk / GUI imports (lazy headless detection later)
# ============================================================

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    HAS_TK = True
except Exception:
    HAS_TK = False
    tk = None
    ttk = None
    messagebox = None
    filedialog = None

# ============================================================
# Global shutdown event
# ============================================================

_shutdown_event = threading.Event()

# ============================================================
# Minimal stubs for external pieces (replace with your real ones)
# ============================================================

class StatusBus:
    def __init__(self):
        self.q = queue.Queue()

    def emit(self, event: str, payload: Optional[dict] = None):
        self.q.put({"event": event, "payload": payload or {}})

    def get_nowait(self):
        try:
            return self.q.get_nowait()
        except queue.Empty:
            return None


class FeedRegistry:
    pass


class SystemMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot = {}

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while not _shutdown_event.is_set():
            with self._lock:
                # stub metrics
                self._snapshot = {
                    "cpu": random.uniform(5, 60),
                    "gpu_util": random.uniform(0, 80),
                    "procs": random.randint(50, 200),
                    "conns": random.randint(10, 200),
                    "disk_read": random.randint(0, 10_000_000),
                    "disk_write": random.randint(0, 10_000_000),
                    "net_sent": random.randint(0, 10_000_000),
                    "net_recv": random.randint(0, 10_000_000),
                }
            time.sleep(1.0)

    def snapshot(self):
        with self._lock:
            return dict(self._snapshot)


def node_paths(cfg: dict) -> dict:
    base = cfg.get("storage_dir", os.path.join(os.getcwd(), "projects"))
    alt = cfg.get("alternate_storage_dir", os.path.join(os.getcwd(), "projects_alt"))
    return {"primary": base, "alternate": alt}


_ARCHIVE_DEFAULT = {
    "ideas": {},
    "top": [],
    "stats": {
        "iterations": 0,
        "completed_projects": 0,
        "active_projects": 0,
        "active_project_names": [],
        "finished_project_names": [],
        "feed_entropy": 0.0,
        "sys_entropy": 0.0,
        "storage_dir": "",
    },
    "history": [],
}

_archive_lock = threading.Lock()


def load_archive(paths: dict) -> dict:
    path = os.path.join(paths["primary"], "archive.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return json.loads(json.dumps(_ARCHIVE_DEFAULT))


def save_archive(paths: dict, archive: dict):
    os.makedirs(paths["primary"], exist_ok=True)
    path = os.path.join(paths["primary"], "archive.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(archive, f)
    os.replace(tmp, path)


# storage dir selection
_storage_dir_lock = threading.Lock()
_storage_dir = None


def get_storage_dir():
    with _storage_dir_lock:
        return _storage_dir or os.getcwd()


def _set_storage_dir(path: str):
    global _storage_dir
    with _storage_dir_lock:
        _storage_dir = path


def choose_storage_dir(cfg: dict) -> str:
    # simple: prefer manual, else primary
    if cfg.get("manual_storage_dir"):
        return cfg["manual_storage_dir"]
    return cfg.get("storage_dir", os.path.join(os.getcwd(), "projects"))


# idea / evolution stubs (replace with your real logic)
def compose_idea(seed_noise: float = 0.15) -> dict:
    return {
        "title": f"Mythic Project {random.randint(1000, 9999)}",
        "intent": random.choice(["shielding", "damping", "navigation", "amplification"]),
        "constraints": ["low_power", "fail_safe"],
        "params": {
            "scale": random.uniform(10.0, 200.0),
            "power_budget": random.uniform(1e3, 1e5),
            "tolerance": random.uniform(0.01, 0.2),
            "mutation_rate": random.uniform(0.05, 0.3),
        },
    }


def clamp_params(idea: dict) -> dict:
    p = idea["params"]
    p["scale"] = max(1e-3, min(1e3, p["scale"]))
    p["power_budget"] = max(1e-1, min(1e6, p["power_budget"]))
    p["tolerance"] = max(0.0, min(0.5, p["tolerance"]))
    p["mutation_rate"] = max(0.0, min(1.0, p["mutation_rate"]))
    return idea


def mutate_idea_safe(base: dict) -> dict:
    idea = json.loads(json.dumps(base))
    p = idea["params"]
    p["scale"] *= random.uniform(0.8, 1.2)
    p["power_budget"] *= random.uniform(0.7, 1.3)
    p["tolerance"] *= random.uniform(0.8, 1.2)
    p["mutation_rate"] *= random.uniform(0.8, 1.2)
    return clamp_params(idea)


def autonomous_project_name(idea: dict, node_name: str) -> str:
    return f"{node_name}_{idea['intent']}_{abs(hash(idea['title'])) % 10_000}"


def idea_hash(text: str) -> str:
    return f"h{abs(hash(text)) & 0xFFFFFFFF:08x}"


def ensure_idea_keys(idea: dict) -> dict:
    return idea


def feed_entropy(feeds: FeedRegistry) -> float:
    return random.uniform(0.0, 1.0)


def system_entropy(sys_snapshot: dict) -> float:
    return random.uniform(0.0, 1.0)


def evaluate_idea_full(idea: dict, archive_texts: List[str], cfg: dict,
                       perf: Optional[dict] = None, weights_override: Optional[dict] = None) -> dict:
    # stub evaluation: random metrics
    novelty = random.uniform(0.0, 1.0)
    utility = random.uniform(0.0, 1.0)
    impact = random.uniform(0.0, 1.0)
    curiosity = random.uniform(0.0, 1.0)
    w = {
        "novelty": cfg.get("novelty_weight", 0.25),
        "utility": cfg.get("utility_weight", 0.35),
        "impact": cfg.get("impact_weight", 0.25),
        "curiosity": cfg.get("curiosity_weight", 0.15),
    }
    if weights_override:
        w.update(weights_override)
    objective = (novelty * w["novelty"] +
                 utility * w["utility"] +
                 impact * w["impact"] +
                 curiosity * w["curiosity"])
    text = f"{idea['title']} :: {idea['intent']} :: {idea['constraints']}"
    return {
        "novelty": novelty,
        "utility": utility,
        "impact": impact,
        "curiosity": curiosity,
        "objective": objective,
        "final_score": objective,
        "text": text,
        "hash": idea_hash(text),
    }


def ensure_strategy_file(cfg: dict):
    # stub: ensure strategies.py exists
    path = os.path.join(os.getcwd(), "strategies.py")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("def adjust_weights(perf):\n    return {}\n")


def load_strategy():
    try:
        return importlib.import_module("strategies")
    except Exception:
        return None


def mutate_strategy(cfg: dict):
    # stub: no-op
    pass


def enforce_archive_limits(archive: dict, cfg: dict):
    max_size = cfg.get("max_archive_size", 1000)
    if len(archive["ideas"]) <= max_size:
        return
    # simple trim oldest
    ids = list(archive["ideas"].keys())
    to_drop = ids[: max(0, len(ids) - max_size)]
    for i in to_drop:
        archive["ideas"].pop(i, None)
    archive["top"] = [t for t in archive["top"] if t["id"] in archive["ideas"]]


class Throttle:
    def __init__(self, cfg: dict):
        self.sleep_min = cfg.get("sleep_min_seconds", 0.025)
        self.sleep_max = cfg.get("sleep_max_seconds", 0.8)
        self.current = self.sleep_min

    def adjust(self):
        # stub: simple jitter
        self.current = max(self.sleep_min, min(self.sleep_max, self.current * random.uniform(0.9, 1.1)))

    def wait(self):
        time.sleep(self.current)


# ============================================================
# BorgMesh + MeshGraphPanel
# ============================================================

if HAS_TK:
    class BorgMesh:
        def __init__(self):
            self.nodes = {}
            self.lock = threading.Lock()

        def discover(self, url: str, snippet: str, links: list):
            with self.lock:
                self.nodes.setdefault(url, {"snippet": snippet, "links": set(), "state": "discovered"})
                self.nodes[url]["links"].update(links)

        def build(self, url: str) -> bool:
            with self.lock:
                if url not in self.nodes:
                    return False
                self.nodes[url]["state"] = "built"
                return True

        def enforce(self, url: str):
            with self.lock:
                if url in self.nodes:
                    self.nodes[url]["state"] = "enforced"

        def snapshot(self):
            with self.lock:
                return {
                    u: {
                        "snippet": v["snippet"],
                        "links": list(v["links"]),
                        "state": v["state"],
                    }
                    for u, v in self.nodes.items()
                }


    class MeshGraphPanel(tk.Canvas):
        def __init__(self, master, get_mesh_snapshot, **kwargs):
            super().__init__(master, **kwargs)
            self.get_mesh_snapshot = get_mesh_snapshot
            self.running = True
            self.after(500, self._refresh)

        def _refresh(self):
            if not self.running:
                return
            self.delete("all")
            snap = self.get_mesh_snapshot()
            w = self.winfo_width() or 400
            h = self.winfo_height() or 300
            urls = list(snap.keys())
            n = max(1, len(urls))
            positions = {}
            for i, url in enumerate(urls):
                x = 50 + (w - 100) * (i / max(1, n - 1))
                y = h / 2
                positions[url] = (x, y)
                state = snap[url]["state"]
                color = {"discovered": "gray", "built": "blue", "enforced": "green"}.get(state, "black")
                self.create_oval(x - 8, y - 8, x + 8, y + 8, fill=color)
                self.create_text(x, y - 16, text=str(i), font=("Arial", 8))
            for url, meta in snap.items():
                x, y = positions.get(url, (w / 2, h / 2))
                for link in meta["links"]:
                    if link in positions:
                        lx, ly = positions[link]
                        self.create_line(x, y, lx, ly, fill="#888")
            self.after(1000, self._refresh)

        def stop(self):
            self.running = False
else:
    class BorgMesh:
        def __init__(self):
            self.nodes = {}
            self.lock = threading.Lock()

        def discover(self, url: str, snippet: str, links: list):
            with self.lock:
                self.nodes.setdefault(url, {"snippet": snippet, "links": set(), "state": "discovered"})
                self.nodes[url]["links"].update(links)

        def build(self, url: str) -> bool:
            with self.lock:
                if url not in self.nodes:
                    return False
                self.nodes[url]["state"] = "built"
                return True

        def enforce(self, url: str):
            with self.lock:
                if url in self.nodes:
                    self.nodes[url]["state"] = "enforced"

        def snapshot(self):
            with self.lock:
                return {
                    u: {
                        "snippet": v["snippet"],
                        "links": list(v["links"]),
                        "state": v["state"],
                    }
                    for u, v in self.nodes.items()
                }

    MeshGraphPanel = object  # no GUI


# ============================================================
# MeshPeer (TCP mesh)
# ============================================================

class MeshPeer:
    def __init__(self, cfg: dict, mesh: BorgMesh, status: StatusBus):
        self.cfg = cfg
        self.mesh = mesh
        self.status = status
        self.peers = set()
        self.lock = threading.Lock()
        self.server_thread = threading.Thread(target=self._server_loop, daemon=True)
        self.client_thread = threading.Thread(target=self._client_loop, daemon=True)

    def start(self):
        self.server_thread.start()
        self.client_thread.start()

    def _server_loop(self):
        port = self.cfg.get("mesh_port", 55555)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", port))
            s.listen(5)
        except Exception:
            logging.exception("Mesh server bind failed")
            return
        while not _shutdown_event.is_set():
            try:
                s.settimeout(1.0)
                conn, addr = s.accept()
            except socket.timeout:
                continue
            except Exception:
                continue
            threading.Thread(target=self._handle_conn, args=(conn, addr), daemon=True).start()

    def _handle_conn(self, conn, addr):
        try:
            data = conn.recv(65536)
            if not data:
                return
            msg = json.loads(data.decode("utf-8"))
            if msg.get("type") == "mesh_snapshot":
                snap = msg.get("mesh", {})
                for url, meta in snap.items():
                    self.mesh.discover(url, meta.get("snippet", ""), meta.get("links", []))
            elif msg.get("type") == "hello":
                with self.lock:
                    self.peers.add((addr[0], msg.get("port", self.cfg.get("mesh_port", 55555))))
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _client_loop(self):
        while not _shutdown_event.is_set():
            seeds = self.cfg.get("mesh_seeds", [])
            for host, port in seeds:
                with self.lock:
                    self.peers.add((host, port))
            snap = self.mesh.snapshot()
            msg = json.dumps({"type": "mesh_snapshot", "mesh": snap}).encode("utf-8")
            with self.lock:
                peers = list(self.peers)
            for host, port in peers:
                try:
                    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    c.settimeout(0.5)
                    c.connect((host, port))
                    c.sendall(msg)
                    c.close()
                except Exception:
                    continue
            time.sleep(5.0)


# ============================================================
# CodeBuilder (large project generator)
# ============================================================

import dataclasses

@dataclasses.dataclass
class Parameters:
    scale: float
    power_budget: float
    tolerance: float
    mutation_rate: float


@dataclasses.dataclass
class Diagnostics:
    final: float
    novelty: float
    utility: float
    impact: float
    curiosity: float


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def pretty(obj):
    return json.dumps(obj, indent=2)


def seed_all(seed: int):
    random.seed(seed)
    try:
        import numpy
        numpy.random.seed(seed)
    except Exception:
        pass


class ComfortSim:
    def run(self, p: Parameters) -> Dict:
        radius = max(10.0, p.scale)
        rpm = max(0.1, min(6.0, 60.0 * math.sqrt(9.81 / radius) / (2 * math.pi)))
        g = (4 * math.pi ** 2 * radius * (rpm / 60.0) ** 2)
        comfort_penalty = abs(g - 9.81) / 9.81
        coriolis_penalty = max(0.0, (rpm - 2.0) / 4.0)
        power_ok = min(1.0, 1.0 / (1.0 + p.power_budget / 1e5))
        utility = max(0.0, 1.0 - comfort_penalty - coriolis_penalty) * (0.5 + 0.5 * power_ok)
        return {"radius": radius, "rpm": rpm, "g": g, "utility": utility}


class RoutingSim:
    def run(self, p: Parameters, intent: str, constraints: List[str]) -> Dict:
        intent_bonus = 0.2 if intent in ["shielding", "damping", "navigation"] else 0.0
        constraint_bonus = 0.1 * len([c for c in constraints if c in ["low_power", "fail_safe", "zero_emissions"]])
        power_penalty = 0.0 if intent == "amplification" else min(
            0.6, math.log10(max(1.0, p.power_budget)) / 10.0
        )
        stability = max(0.0, 1.0 - p.tolerance)
        utility = max(0.0, stability + intent_bonus + constraint_bonus - power_penalty)
        return {"stability": stability, "utility": utility}


class ProjectCore:
    def __init__(self, params: Parameters, title: str, constraints: List[str], intent: str):
        self.params = params
        self.title = title
        self.constraints = constraints
        self.intent = intent
        self.comfort = ComfortSim()
        self.routing = RoutingSim()

    def step(self) -> Dict:
        c = self.comfort.run(self.params)
        r = self.routing.run(self.params, self.intent, self.constraints)
        return {"comfort": c, "routing": r}

    def run(self, iterations: int = 100) -> Dict:
        log = []
        for _ in range(iterations):
            log.append(self.step())
        return {"title": self.title, "log": log, "constraints": self.constraints}


class Config:
    def __init__(self):
        self.name = "MythicSwarm"
        self.title = "Mythic Swarm Project"
        self.seed = 42
        self.iterations = 200
        self.enable_logging = True
        self.output_dir = os.path.join(os.getcwd(), "out")

    def ensure(self):
        os.makedirs(self.output_dir, exist_ok=True)


def validate_params(p: Parameters) -> List[str]:
    errs = []
    if not (1e-3 <= p.scale <= 1e3):
        errs.append("scale out of range")
    if not (1e-1 <= p.power_budget <= 1e6):
        errs.append("power_budget out of range")
    if not (0.0 <= p.tolerance <= 0.5):
        errs.append("tolerance out of range")
    if not (0.0 <= p.mutation_rate <= 1.0):
        errs.append("mutation_rate out of range")
    return errs


class CodeBuilder:
    def __init__(self, name: str, idea: dict, evals: dict):
        self.name = name
        self.idea = idea
        self.evals = evals
        self.lines: List[str] = []

    def add_header(self):
        hdr = [
            f"# Project: {self.idea['title']}",
            f"# Name: {self.name}",
            f"# Constraints: {', '.join(self.idea.get('constraints', []))}",
            (
                f"# Params: scale={self.idea['params']['scale']}, "
                f"power_budget={self.idea['params']['power_budget']}, "
                f"tol={self.idea['params']['tolerance']}, "
                f"mutation_rate={self.idea['params']['mutation_rate']}"
            ),
            (
                "# Scores: "
                f"final={self.evals.get('final_score', self.evals.get('objective', 0.0)):.4f} "
                f"novelty={self.evals['novelty']:.4f} "
                f"utility={self.evals['utility']:.4f} "
                f"impact={self.evals['impact']:.4f} "
                f"curiosity={self.evals['curiosity']:.4f}"
            ),
            "",
            '"""',
            "Generated mythic scaffold with modules, classes, CLI, and stubs.",
            '"""',
            "",
            "import sys, os, json, math, time, random, logging, dataclasses, typing",
            "from typing import Dict, List, Tuple, Optional",
            "logging.basicConfig(level=logging.INFO)",
            "",
        ]
        self.lines.extend(hdr)

    def add_config(self):
        self.lines.extend(
            [
                "class Config:",
                "    def __init__(self):",
                f"        self.name = '{self.name}'",
                f"        self.title = '{self.idea['title']}'",
                "        self.seed = 42",
                "        self.iterations = 200",
                "        self.enable_logging = True",
                "        self.output_dir = os.path.join(os.getcwd(), 'out')",
                "",
                "    def ensure(self):",
                "        os.makedirs(self.output_dir, exist_ok=True)",
                "",
            ]
        )

    def add_dataclasses(self):
        self.lines.extend(
            [
                "@dataclasses.dataclass",
                "class Parameters:",
                "    scale: float",
                "    power_budget: float",
                "    tolerance: float",
                "    mutation_rate: float",
                "",
                "@dataclasses.dataclass",
                "class Diagnostics:",
                "    final: float",
                "    novelty: float",
                "    utility: float",
                "    impact: float",
                "    curiosity: float",
                "",
            ]
        )

    def add_utils(self):
        self.lines.extend(
            [
                "def clamp(v, lo, hi):",
                "    return max(lo, min(hi, v))",
                "",
                "def pretty(obj):",
                "    return json.dumps(obj, indent=2)",
                "",
                "def seed_all(seed: int):",
                "    random.seed(seed)",
                "    try:",
                "        import numpy",
                "        numpy.random.seed(seed)",
                "    except Exception:",
                "        pass",
                "",
            ]
        )

    def add_sim_modules(self):
        self.lines.extend(
            [
                "class ComfortSim:",
                "    def run(self, p: Parameters) -> Dict:",
                "        radius = max(10.0, p.scale)",
                "        rpm = max(0.1, min(6.0, 60.0 * math.sqrt(9.81 / radius) / (2*math.pi)))",
                "        g = (4 * math.pi**2 * radius * (rpm/60.0)**2)",
                "        comfort_penalty = abs(g - 9.81) / 9.81",
                "        coriolis_penalty = max(0.0, (rpm - 2.0) / 4.0)",
                "        power_ok = min(1.0, 1.0 / (1.0 + p.power_budget/1e5))",
                "        utility = max(0.0, 1.0 - comfort_penalty - coriolis_penalty) * (0.5 + 0.5*power_ok)",
                "        return {'radius': radius, 'rpm': rpm, 'g': g, 'utility': utility}",
                "",
                "class RoutingSim:",
                "    def run(self, p: Parameters, intent: str, constraints: List[str]) -> Dict:",
                "        intent_bonus = 0.2 if intent in ['shielding','damping','navigation'] else 0.0",
                "        constraint_bonus = 0.1 * len([c for c in constraints if c in ['low_power','fail_safe','zero_emissions']])",
                "        power_penalty = 0.0 if intent == 'amplification' else min(0.6, math.log10(max(1.0, p.power_budget)) / 10.0)",
                "        stability = max(0.0, 1.0 - p.tolerance)",
                "        utility = max(0.0, stability + intent_bonus + constraint_bonus - power_penalty)",
                "        return {'stability': stability, 'utility': utility}",
                "",
            ]
        )

    def add_core_classes(self):
        self.lines.extend(
            [
                "class ProjectCore:",
                "    def __init__(self, params: Parameters, title: str, constraints: List[str], intent: str):",
                "        self.params = params; self.title = title; self.constraints = constraints; self.intent = intent",
                "        self.comfort = ComfortSim(); self.routing = RoutingSim()",
                "",
                "    def step(self) -> Dict:",
                "        c = self.comfort.run(self.params)",
                "        r = self.routing.run(self.params, self.intent, self.constraints)",
                "        return {'comfort': c, 'routing': r}",
                "",
                "    def run(self, iterations: int = 100) -> Dict:",
                "        log = []",
                "        for i in range(iterations):",
                "            log.append(self.step())",
                "        return {'title': self.title, 'log': log, 'constraints': self.constraints}",
                "",
            ]
        )

    def add_validators(self):
        self.lines.extend(
            [
                "def validate_params(p: Parameters) -> List[str]:",
                "    errs = []",
                "    if not (1e-3 <= p.scale <= 1e3): errs.append('scale out of range')",
                "    if not (1e-1 <= p.power_budget <= 1e6): errs.append('power_budget out of range')",
                "    if not (0.0 <= p.tolerance <= 0.5): errs.append('tolerance out of range')",
                "    if not (0.0 <= p.mutation_rate <= 1.0): errs.append('mutation_rate out of range')",
                "    return errs",
                "",
            ]
        )

    def add_cli(self):
        self.lines.extend(
            [
                "def main():",
                "    cfg = Config(); cfg.ensure(); seed_all(cfg.seed)",
                f"    p = Parameters(scale={self.idea['params']['scale']}, power_budget={self.idea['params']['power_budget']}, tolerance={self.idea['params']['tolerance']}, mutation_rate={self.idea['params']['mutation_rate']})",
                "    errs = validate_params(p)",
                "    if errs:",
                "        logging.error('Validation errors: %s', errs); sys.exit(1)",
                f"    core = ProjectCore(p, '{self.idea['title']}', {self.idea.get('constraints', [])}, '{self.idea['intent']}')",
                "    out = core.run(iterations=200)",
                "    path = os.path.join(cfg.output_dir, 'result.json')",
                "    with open(path, 'w', encoding='utf-8') as f: f.write(pretty(out))",
                "    logging.info('Wrote %s', path)",
                "",
                "if __name__ == '__main__':",
                "    main()",
                "",
            ]
        )

    def add_fillers(self, target_lines: int = 1000):
        stub = [
            "def _stub_fn_{i}(x):",
            "    return x * {i}",
            "",
            "class _StubClass_{i}:",
            "    def __init__(self):",
            "        self.v = {i}",
            "    def m(self, y):",
            "        return self.v + y",
            "",
        ]
        i = 0
        while len(self.lines) < target_lines:
            self.lines.extend([ln.format(i=i) for ln in stub])
            i += 1

    def build(self, min_lines: int = 1000) -> str:
        self.add_header()
        self.add_config()
        self.add_dataclasses()
        self.add_utils()
        self.add_sim_modules()
        self.add_core_classes()
        self.add_validators()
        self.add_cli()
        self.add_fillers(target_lines=min_lines)
        return "\n".join(self.lines)


def generate_large_project_file(idea: dict, evals: dict, cfg: dict, hid: str):
    storage_dir = choose_storage_dir(cfg)
    os.makedirs(storage_dir, exist_ok=True)
    pname = idea.get("project_name", autonomous_project_name(idea, cfg.get("node_name", "local")))
    fname = f"{pname}_{hid}.py"
    path = os.path.join(storage_dir, fname)
    builder = CodeBuilder(pname, idea, evals)
    code = builder.build(min_lines=int(cfg.get("min_project_lines", 1000)))
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path, len(code.splitlines()), storage_dir


# ============================================================
# SwarmNode (multi-node distributed evolution)
# ============================================================

class SwarmNode:
    def __init__(self, cfg: dict, archive_ref: dict, status: StatusBus):
        self.cfg = cfg
        self.archive_ref = archive_ref
        self.status = status
        self.node_id = cfg.get("node_name", "local")
        self.port = cfg.get("swarm_port", 55600)
        self.seeds = cfg.get("swarm_seeds", [])
        self.server_thread = threading.Thread(target=self._server_loop, daemon=True)
        self.client_thread = threading.Thread(target=self._client_loop, daemon=True)

    def start(self):
        self.server_thread.start()
        self.client_thread.start()

    def _server_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", self.port))
            s.listen(5)
        except Exception:
            logging.exception("Swarm server bind failed")
            return
        while not _shutdown_event.is_set():
            try:
                s.settimeout(1.0)
                conn, addr = s.accept()
            except socket.timeout:
                continue
            except Exception:
                continue
            threading.Thread(target=self._handle_conn, args=(conn, addr), daemon=True).start()

    def _handle_conn(self, conn, addr):
        try:
            data = conn.recv(65536)
            if not data:
                return
            msg = json.loads(data.decode("utf-8"))
            if msg.get("type") == "elite_ideas":
                elites = msg.get("elites", [])
                with _archive_lock:
                    arch = self.archive_ref["ref"]
                    for e in elites:
                        hid = e["hash"]
                        if hid not in arch["ideas"]:
                            arch["ideas"][hid] = e
                            arch["top"].append(
                                {
                                    "id": hid,
                                    "objective": e["eval"]["final_score"],
                                    "novelty": e["eval"]["novelty"],
                                    "utility": e["eval"]["utility"],
                                }
                            )
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _client_loop(self):
        while not _shutdown_event.is_set():
            elites_payload = []
            with _archive_lock:
                arch = self.archive_ref["ref"]
                for tid in [t["id"] for t in arch.get("top", [])[-5:]]:
                    item = arch["ideas"].get(tid)
                    if item:
                        elites_payload.append(item)
            if elites_payload:
                msg = json.dumps({"type": "elite_ideas", "elites": elites_payload}).encode("utf-8")
                for host, port in self.seeds:
                    try:
                        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        c.settimeout(0.5)
                        c.connect((host, port))
                        c.sendall(msg)
                        c.close()
                    except Exception:
                        continue
            time.sleep(6.0)


# ============================================================
# Evolution loop
# ============================================================

def select_parents(archive: dict, k: int, seed_noise: float) -> list:
    top_ids = [entry["id"] for entry in archive["top"][-50:]]
    pool = []
    for _ in range(k):
        if top_ids and random.random() < 0.7:
            pool.append(archive["ideas"][random.choice(top_ids)]["idea"])
        else:
            pool.append(compose_idea(seed_noise))
    return pool


def evolve(cfg: dict, archive_ref: dict, feeds: FeedRegistry, sysmon: SystemMetrics,
           status: StatusBus, mesh: BorgMesh, scanner_in: queue.Queue, ops_q: queue.Queue,
           llm_bridge=None):
    def _score_of(ev_or_dict: dict) -> float:
        return float(ev_or_dict.get("final_score", ev_or_dict.get("objective", 0.0)))

    paths = node_paths(cfg)
    with _archive_lock:
        archive = archive_ref["ref"]
    archive_texts = [archive["ideas"][i]["text"] for i in archive["ideas"]]
    no_improve = 0
    best_obj = max(
        [_score_of(archive["ideas"][i].get("eval", archive["ideas"][i])) for i in archive["ideas"]] + [0.0]
    )
    throttle = Throttle(cfg)
    ensure_strategy_file(cfg)
    strategy_mod = load_strategy()
    last_strategy_mut = time.time()
    perf_window: List[float] = []
    chosen_dir = choose_storage_dir(cfg)
    _set_storage_dir(chosen_dir)
    with _archive_lock:
        archive["stats"]["storage_dir"] = chosen_dir
    status.emit("seed", {"msg": "evolution started"})

    while not _shutdown_event.is_set():
        try:
            for _step in range(cfg.get("iterations_per_tick", 10)):
                with _archive_lock:
                    archive = archive_ref["ref"]
                chosen_dir = choose_storage_dir(cfg)
                _set_storage_dir(chosen_dir)
                with _archive_lock:
                    archive["stats"]["storage_dir"] = chosen_dir

                entropy_feed = feed_entropy(feeds)
                entropy_sys = system_entropy(sysmon.snapshot())
                with _archive_lock:
                    archive["stats"]["feed_entropy"] = entropy_feed
                    archive["stats"]["sys_entropy"] = entropy_sys

                parents = select_parents(
                    archive,
                    k=max(5, cfg["batch_size"] // 4),
                    seed_noise=cfg["seed_noise"],
                )
                candidates = []
                active_names = []

                for _ in range(cfg["batch_size"]):
                    base = random.choice(parents)
                    idea = (
                        mutate_idea_safe(base)
                        if random.random() < 0.8
                        else clamp_params(compose_idea(cfg["seed_noise"]))
                    )
                    idea["params"]["tolerance"] = round(
                        max(
                            0.003,
                            idea["params"]["tolerance"]
                            * (1.0 - 0.15 * entropy_feed - 0.15 * entropy_sys),
                        ),
                        4,
                    )
                    idea["params"]["mutation_rate"] = round(
                        min(
                            0.8,
                            idea["params"]["mutation_rate"]
                            * (1.0 + 0.25 * entropy_feed + 0.25 * entropy_sys),
                        ),
                        4,
                    )
                    pname = autonomous_project_name(idea, cfg["node_name"])
                    idea["project_name"] = pname
                    perf = {
                        "avg_objective": (sum(perf_window) / len(perf_window)) if perf_window else 0.0,
                        "new_ideas": len(archive_texts),
                        "completion_rate": (
                            archive["stats"]["completed_projects"]
                            / max(1, archive["stats"]["iterations"])
                        ),
                        "feed_entropy": entropy_feed,
                        "sys_entropy": entropy_sys,
                    }
                    weights_override = None
                    if strategy_mod and hasattr(strategy_mod, "adjust_weights"):
                        try:
                            weights_override = strategy_mod.adjust_weights(perf)
                        except Exception:
                            logging.exception("Strategy adjust failed.")
                            weights_override = None
                    evals = evaluate_idea_full(
                        idea,
                        archive_texts,
                        cfg,
                        perf=perf,
                        weights_override=weights_override,
                    )
                    candidates.append((idea, evals, evals["objective"]))
                    active_names.append(pname)
                    archive_texts.append(evals["text"])
                    ev = {
                        "url": f"https://mesh.local/{pname}",
                        "snippet": idea["title"],
                        "links": [
                            f"https://mesh.local/{pname}/l{i}"
                            for i in range(random.randint(3, 12))
                        ],
                    }
                    try:
                        scanner_in.put_nowait(ev)
                        ops_q.put(("build", ev["url"]))
                    except Exception:
                        pass

                with _archive_lock:
                    archive["stats"]["active_projects"] = len(candidates)
                    archive["stats"]["active_project_names"] = active_names

                candidates.sort(key=lambda x: x[2], reverse=True)
                elites = candidates[: max(5, cfg["batch_size"] // 4)]
                improved = False
                completed = 0
                finished_names = []

                for idea, evals, obj in elites:
                    text = evals["text"]
                    hid = idea_hash(text)
                    with _archive_lock:
                        if hid not in archive["ideas"]:
                            out_path, line_count, used_dir = generate_large_project_file(
                                idea, evals, cfg, hid
                            )
                            archive["ideas"][hid] = {
                                "id": hid,
                                "idea": ensure_idea_keys(idea),
                                "text": text,
                                "eval": evals,
                                "project_name": idea.get("project_name", "unnamed"),
                                "code_path": out_path,
                                "code_lines": line_count,
                                "storage_dir": used_dir,
                            }
                            archive["top"].append(
                                {
                                    "id": hid,
                                    "objective": obj,
                                    "novelty": evals["novelty"],
                                    "utility": evals["utility"],
                                }
                            )
                            archive_texts.append(text)
                            improved = improved or (obj > best_obj)
                            best_obj = max(best_obj, obj)
                            completed += 1
                            finished_names.append(idea.get("project_name", "unnamed"))
                            perf_window.append(obj)
                            if len(perf_window) > cfg.get("strategy_selection_window", 500):
                                perf_window = perf_window[-cfg.get("strategy_selection_window", 500):]

                if completed > 0:
                    with _archive_lock:
                        stats = archive["stats"]
                        stats["completed_projects"] = stats.get("completed_projects", 0) + completed
                        stats["finished_project_names"] = stats.get(
                            "finished_project_names", []
                        ) + finished_names
                    status.emit("completed", {"names": finished_names})

                if cfg.get("strategy_enable", True) and (
                    time.time() - last_strategy_mut
                ) > cfg.get("strategy_mutate_interval", 120.0):
                    mutate_strategy(cfg)
                    importlib.invalidate_caches()
                    try:
                        if "strategies" in sys.modules:
                            del sys.modules["strategies"]
                        strategy_mod = importlib.import_module("strategies")
                        logging.info("Strategy module hot-reloaded.")
                    except Exception:
                        logging.exception("Failed to hot-reload strategies.")
                        strategy_mod = None
                    last_strategy_mut = time.time()

                with _archive_lock:
                    archive["stats"]["iterations"] += 1
                    archive["history"].append(
                        {
                            "step": archive["stats"]["iterations"],
                            "best_objective": round(best_obj, 6),
                            "archive_size": len(archive["ideas"]),
                            "recent_elites": [
                                e[0].get("project_name", e[0]["title"]) for e in elites
                            ],
                            "node": cfg["node_name"],
                            "active_projects": archive["stats"]["active_projects"],
                            "active_project_names": archive["stats"]["active_project_names"],
                            "completed_projects": archive["stats"]["completed_projects"],
                            "finished_project_names": archive["stats"].get(
                                "finished_project_names", []
                            ),
                            "feed_entropy": archive["stats"].get("feed_entropy", 0.0),
                            "sys_entropy": archive["stats"].get("sys_entropy", 0.0),
                            "storage_dir": archive["stats"].get(
                                "storage_dir", get_storage_dir()
                            ),
                        }
                    )
                status.emit(
                    "tick",
                    {
                        "active": archive["stats"]["active_projects"],
                        "finished": archive["stats"]["completed_projects"],
                        "best": best_obj,
                    },
                )

                if not improved:
                    no_improve += 1
                else:
                    no_improve = 0

                if no_improve > cfg.get("patience", 50):
                    chaos_names = []
                    chaos_batch_size = cfg.get("chaos_batch_size", 10)
                    llm_constraints = []
                    if llm_bridge and cfg.get("llm_chaos_enable", True):
                        try:
                            llm_constraints = llm_bridge.suggest_constraints_for_chaos()
                        except Exception:
                            llm_constraints = []
                    for _ in range(chaos_batch_size):
                        idea = compose_idea(seed_noise=0.2 + 0.2 * entropy_sys)
                        if llm_constraints:
                            idea["constraints"] = list(
                                set(idea.get("constraints", []) + llm_constraints)
                            )[:7]
                        idea["params"]["mutation_rate"] = round(
                            min(
                                0.9,
                                idea["params"]["mutation_rate"]
                                * (1.0 + 0.4 * entropy_feed + 0.4 * entropy_sys),
                            ),
                            4,
                        )
                        idea["project_name"] = autonomous_project_name(
                            idea, cfg["node_name"]
                        )
                        perf = {
                            "avg_objective": (sum(perf_window) / len(perf_window))
                            if perf_window
                            else 0.0,
                            "new_ideas": len(archive_texts),
                            "completion_rate": (
                                archive["stats"]["completed_projects"]
                                / max(1, archive["stats"]["iterations"])
                            ),
                            "feed_entropy": entropy_feed,
                            "sys_entropy": entropy_sys,
                        }
                        evals = evaluate_idea_full(
                            idea,
                            archive_texts,
                            cfg,
                            perf=perf,
                            weights_override=None,
                        )
                        hid = idea_hash(evals["text"])
                        with _archive_lock:
                            if hid not in archive["ideas"]:
                                out_path, line_count, used_dir = generate_large_project_file(
                                    idea, evals, cfg, hid
                                )
                                archive["ideas"][hid] = {
                                    "id": hid,
                                    "idea": ensure_idea_keys(idea),
                                    "text": evals["text"],
                                    "eval": evals,
                                    "project_name": idea["project_name"],
                                    "code_path": out_path,
                                    "code_lines": line_count,
                                    "storage_dir": used_dir,
                                }
                                archive["top"].append(
                                    {
                                        "id": hid,
                                        "objective": evals["objective"],
                                        "novelty": evals["novelty"],
                                        "utility": evals["utility"],
                                    }
                                )
                                archive_texts.append(evals["text"])
                                chaos_names.append(idea["project_name"])
                    if chaos_names:
                        status.emit("chaos", {"names": chaos_names})
                    enforce_archive_limits(archive, cfg)
                    no_improve = 0

                throttle.adjust()
                throttle.wait()

            with _archive_lock:
                save_archive(paths, archive)
                archive_ref["ref"] = archive
            status.emit("save", {"size": len(archive["ideas"])})
        except Exception:
            logging.exception("Evolution loop error; continuing with backoff.")
            backoff = cfg.get("backoff_initial", 0.5)
            while backoff < cfg.get("backoff_max", 5.0) and not _shutdown_event.is_set():
                time.sleep(backoff)
                backoff = min(cfg.get("backoff_max", 5.0), backoff * 2)

    with _archive_lock:
        save_archive(paths, archive)
    status.emit("stop", {"size": len(archive["ideas"])})


# ============================================================
# Advanced GUI
# ============================================================

if HAS_TK:
    class AdvancedGUI(tk.Tk):
        def __init__(self, cfg: dict):
            super().__init__()
            self.title("Mythic Swarm — Live Dashboard")
            self.geometry("1160x760")
            self.cfg = cfg
            self.status = StatusBus()
            self.mesh = BorgMesh()
            self.paths = node_paths(cfg)
            self.archive_ref = {"ref": load_archive(self.paths)}
            self.feeds = FeedRegistry()
            self.sysmon = SystemMetrics()
            self.sysmon.start()
            self.scanner_in, self.ops_q = queue.Queue(), queue.Queue()
            self.scanner = threading.Thread(target=self._scanner_loop, daemon=True)
            self.worker = threading.Thread(target=self._worker_loop, daemon=True)
            self.enforcer = threading.Thread(target=self._enforcer_loop, daemon=True)
            self.evolver = None
            self._build_ui()
            self._start_threads()
            self._poll_status_bus()
            self.status.emit("llm_connected", {"backend": "initializing"})

        def _build_ui(self):
            self.notebook = ttk.Notebook(self)
            self.notebook.pack(fill="both", expand=True)

            self.tab_overview = ttk.Frame(self.notebook)
            self.notebook.add(self.tab_overview, text="Overview")

            topbar = ttk.Frame(self.tab_overview)
            topbar.pack(fill="x", padx=10, pady=10)
            self.lbl_summary = ttk.Label(topbar, text="Step 0 | Active 0 | Finished 0 | Best 0.000")
            self.lbl_summary.pack(side="left", padx=5)
            self.lbl_storage = ttk.Label(topbar, text=f"Storage: {get_storage_dir()}")
            self.lbl_storage.pack(side="left", padx=5)
            self.lbl_llm = ttk.Label(topbar, text="LLM: initializing", foreground="orange")
            self.lbl_llm.pack(side="right", padx=5)

            body = ttk.Frame(self.tab_overview)
            body.pack(fill="both", expand=True, padx=10, pady=10)

            left = ttk.Frame(body)
            left.pack(side="left", fill="both", expand=True)
            right = ttk.Frame(body)
            right.pack(side="right", fill="both", expand=True)

            ttk.Label(left, text="Active Projects").pack(anchor="w")
            self.active_list = tk.Listbox(left, height=15)
            self.active_list.pack(fill="both", expand=True, pady=5)

            ttk.Label(left, text="Finished Projects").pack(anchor="w")
            self.finished_list = tk.Listbox(left, height=15)
            self.finished_list.pack(fill="both", expand=True, pady=5)

            btn_frame = ttk.Frame(left)
            btn_frame.pack(fill="x", pady=5)
            ttk.Button(btn_frame, text="Open Code", command=self._open_selected_code).pack(
                side="left", padx=5
            )
            ttk.Button(btn_frame, text="Pick Storage", command=self._pick_storage_dir).pack(
                side="left", padx=5
            )

            ttk.Label(right, text="System Metrics").pack(anchor="w")
            self.txt_metrics = tk.Text(right, height=20, wrap="word")
            self.txt_metrics.pack(fill="both", expand=True, pady=5)

            # Mesh tab
            self.tab_mesh = ttk.Frame(self.notebook)
            self.notebook.add(self.tab_mesh, text="Mesh")
            self.graph = MeshGraphPanel(
                self.tab_mesh, get_mesh_snapshot=lambda: self.mesh.snapshot()
            )
            self.graph.pack(fill="both", expand=True, padx=10, pady=10)

        def _start_threads(self):
            self.scanner.start()
            self.worker.start()
            self.enforcer.start()

        def start_evolver(self, llm_bridge):
            if self.evolver is None:
                self.evolver = threading.Thread(
                    target=evolve,
                    args=(
                        self.cfg,
                        self.archive_ref,
                        self.feeds,
                        self.sysmon,
                        self.status,
                        self.mesh,
                        self.scanner_in,
                        self.ops_q,
                        llm_bridge,
                    ),
                    daemon=True,
                )
                self.evolver.start()

        def _scanner_loop(self):
            while not _shutdown_event.is_set():
                try:
                    ev = self.scanner_in.get(timeout=1.0)
                except queue.Empty:
                    continue
                try:
                    url = ev["url"]
                    snippet = ev["snippet"]
                    links = ev["links"]
                    self.mesh.discover(url, snippet, links)
                    self.ops_q.put(("build", url))
                except Exception:
                    pass
                time.sleep(random.uniform(0.2, 0.6))

        def _worker_loop(self):
            while not _shutdown_event.is_set():
                try:
                    op, url = self.ops_q.get(timeout=1.0)
                except queue.Empty:
                    continue
                if op == "build":
                    if self.mesh.build(url):
                        self.ops_q.put(("enforce", url))
                elif op == "enforce":
                    self.mesh.enforce(url)
                time.sleep(random.uniform(0.2, 0.5))

        def _enforcer_loop(self):
            while not _shutdown_event.is_set():
                for url, meta in list(self.mesh.nodes.items()):
                    if meta["state"] in ("built", "enforced") and random.random() < 0.15:
                        self.mesh.enforce(url)
                time.sleep(1.2)

        def _poll_status_bus(self):
            evt = self.status.get_nowait()
            if evt:
                event = evt["event"]
                payload = evt["payload"] or {}
                arch = self.archive_ref["ref"]
                stats = arch["stats"]
                summary = (
                    f"Step {stats['iterations']} | Active {stats['active_projects']} | "
                    f"Finished {stats['completed_projects']} | Best {payload.get('best', 0):.3f}"
                )
                self.lbl_summary.config(text=summary)
                self.lbl_storage.config(
                    text=f"Storage: {stats.get('storage_dir', get_storage_dir())}"
                )
                self.active_list.delete(0, tk.END)
                for n in stats.get("active_project_names", []):
                    self.active_list.insert(tk.END, n)
                self.finished_list.delete(0, tk.END)
                for n in stats.get("finished_project_names", []):
                    self.finished_list.insert(tk.END, n)

                sysm = self.sysmon.snapshot()
                metrics_txt = textwrap.dedent(
                    f"""
                    CPU: {sysm.get('cpu', 0.0):.1f} %
                    GPU: {sysm.get('gpu_util', 0.0):.1f} %
                    Procs: {sysm.get('procs', 0)} | Conns: {sysm.get('conns', 0)}
                    Disk [R/W]: {sysm.get('disk_read', 0)} / {sysm.get('disk_write', 0)}
                    Net [S/R]: {sysm.get('net_sent', 0)} / {sysm.get('net_recv', 0)}
                    Feed entropy: {stats.get('feed_entropy', 0.0):.3f}
                    Sys entropy:  {stats.get('sys_entropy', 0.0):.3f}
                    Storage dir:  {stats.get('storage_dir', get_storage_dir())}
                """
                ).strip()
                self.txt_metrics.delete("1.0", tk.END)
                self.txt_metrics.insert("1.0", metrics_txt)
                if event == "completed":
                    self.txt_metrics.insert(
                        tk.END, f"\nCompleted: {', '.join(payload.get('names', []))}"
                    )
                elif event == "chaos":
                    self.txt_metrics.insert(
                        tk.END, f"\nChaos injected: {', '.join(payload.get('names', []))}"
                    )
                elif event == "save":
                    self.txt_metrics.insert(
                        tk.END, f"\nArchive saved: {payload.get('size', 0)} ideas"
                    )
                if event == "llm_connected":
                    backend = payload.get("backend", "Unknown")
                    self.lbl_llm.config(text=f"LLM: {backend}", foreground="green")
                elif event == "llm_error":
                    self.lbl_llm.config(text="LLM: error", foreground="orange")
                elif event == "llm_tool":
                    tool = payload.get("tool", "")
                    self.lbl_llm.config(
                        text=f"LLM: active ({tool})", foreground="blue"
                    )
            self.after(250, self._poll_status_bus)

        def _open_selected_code(self):
            sel = self.finished_list.curselection()
            if not sel:
                return
            name = self.finished_list.get(sel[0])
            storage_dir = get_storage_dir()
            try:
                files = [f for f in os.listdir(storage_dir) if f.startswith(name)]
                if not files:
                    messagebox.showinfo("Code", "No code file found yet.")
                    return
                fpath = os.path.join(storage_dir, files[0])
                content = open(fpath, "r", encoding="utf-8").read()
            except Exception:
                messagebox.showinfo("Code", "Failed to open code file.")
                return
            win = tk.Toplevel(self)
            win.title(f"Code — {name}")
            txt = tk.Text(win, wrap="none")
            txt.pack(fill="both", expand=True)
            txt.insert("1.0", content)

        def _pick_storage_dir(self):
            chosen = filedialog.askdirectory(title="Select storage directory")
            if chosen:
                self.cfg["manual_storage_dir"] = chosen
                _set_storage_dir(chosen)
                with _archive_lock:
                    arch = self.archive_ref["ref"]
                    arch["stats"]["storage_dir"] = chosen
                self.lbl_storage.config(text=f"Storage: {chosen}")

        def destroy(self):
            try:
                self.graph.stop()
            except Exception:
                pass
            super().destroy()
            _shutdown_event.set()
else:
    AdvancedGUI = None  # type: ignore


# ============================================================
# LLM Bridge + tools schema
# ============================================================

LLM_TOOLS_SCHEMA = [
    {
        "name": "generate_project",
        "description": "Generate a large swarm project scaffold",
        "parameters": {
            "type": "object",
            "properties": {
                "min_project_lines": {"type": "integer"},
                "seed_noise": {"type": "number"},
            },
        },
    },
    {
        "name": "get_status",
        "description": "Get current swarm evolution status",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "mutate_idea",
        "description": "Mutate a provided idea JSON safely",
        "parameters": {
            "type": "object",
            "properties": {"idea": {"type": "object"}},
        },
    },
    {
        "name": "optimize_strategy",
        "description": "Rewrite the strategy file based on archive trends",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "suggest_constraints_for_chaos",
        "description": "Suggest extra constraints for chaos ideas",
        "parameters": {"type": "object", "properties": {}},
    },
]


class LLMBridge:
    def __init__(self, cfg: dict, archive_ref: dict, status_bus: StatusBus):
        self.cfg = cfg
        self.archive_ref = archive_ref
        self.status = status_bus

    def generate_project(self, params: dict = None) -> dict:
        local_cfg = dict(self.cfg)
        if params:
            local_cfg.update(params)
        idea = compose_idea(local_cfg.get("seed_noise", 0.15))
        evals = evaluate_idea_full(idea, [], local_cfg)
        path, lines, dir_used = generate_large_project_file(
            idea, evals, local_cfg, evals["hash"]
        )
        self.status.emit("llm_tool", {"tool": "generate_project"})
        return {
            "project_name": idea.get("project_name"),
            "file": path,
            "lines": lines,
            "score": round(evals["final_score"], 4),
        }

    def get_status(self) -> dict:
        stats = self.archive_ref["ref"]["stats"]
        self.status.emit("llm_tool", {"tool": "get_status"})
        return {
            "iterations": stats["iterations"],
            "active": stats["active_projects"],
            "finished": stats["completed_projects"],
            "storage_dir": stats.get("storage_dir", get_storage_dir()),
        }

    def mutate_idea(self, base_idea: dict) -> dict:
        self.status.emit("llm_tool", {"tool": "mutate_idea"})
        m = mutate_idea_safe(base_idea)
        return ensure_idea_keys(m)

    def optimize_strategy(self) -> dict:
        self.status.emit("llm_tool", {"tool": "optimize_strategy"})
        arch = self.archive_ref["ref"]
        stats = arch["stats"]
        summary = {
            "iterations": stats["iterations"],
            "completed": stats["completed_projects"],
            "best_recent": arch["history"][-1]["best_objective"] if arch["history"] else 0.0,
        }
        return {
            "summary": summary,
            "hint": "LLM should rewrite strategies.py using this summary.",
        }

    def suggest_constraints_for_chaos(self) -> list:
        self.status.emit("llm_tool", {"tool": "suggest_constraints_for_chaos"})
        return ["fail_safe", "low_power", "zero_emissions"]


def handle_llm_tool_call(bridge: LLMBridge, tool_name: str, arguments: dict,
                         status_bus: StatusBus) -> dict:
    try:
        if tool_name == "generate_project":
            return bridge.generate_project(arguments or {})
        if tool_name == "get_status":
            return bridge.get_status()
        if tool_name == "mutate_idea":
            return bridge.mutate_idea(arguments.get("idea", compose_idea()))
        if tool_name == "optimize_strategy":
            return bridge.optimize_strategy()
        if tool_name == "suggest_constraints_for_chaos":
            return {"constraints": bridge.suggest_constraints_for_chaos()}
        return {"error": f"Unknown tool {tool_name}"}
    except Exception as e:
        status_bus.emit("llm_error", {})
        return {"error": str(e), "traceback": traceback.format_exc()}


class AutoLLMClient:
    def __init__(self, status_bus: StatusBus):
        self.backend = None
        self.client = None
        self.status_bus = status_bus
        try:
            if importlib.util.find_spec("openai") and os.getenv("OPENAI_API_KEY"):
                import openai
                self.client = openai
                self.backend = "OpenAI"
                status_bus.emit("llm_connected", {"backend": "OpenAI"})
            elif importlib.util.find_spec("anthropic") and os.getenv("ANTHROPIC_API_KEY"):
                import anthropic
                self.client = anthropic
                self.backend = "Anthropic"
                status_bus.emit("llm_connected", {"backend": "Anthropic"})
            else:
                self.backend = "Stub"
                status_bus.emit("llm_connected", {"backend": "Stub"})
        except Exception:
            status_bus.emit("llm_error", {})

    def chat(self, messages, tools=None, model=None):
        if self.backend == "Stub":
            user_texts = [m["content"] for m in messages if m.get("role") == "user"]
            wants_generate = any("generate" in t.lower() for t in user_texts)
            wants_status = any("status" in t.lower() for t in user_texts)
            wants_opt = any("strategy" in t.lower() for t in user_texts)
            faux = {"choices": [{"message": {"tool_calls": []}}]}
            if wants_generate and tools and any(t["name"] == "generate_project" for t in tools):
                faux["choices"][0]["message"]["tool_calls"] = [
                    {
                        "id": "tool_1",
                        "name": "generate_project",
                        "arguments": {"min_project_lines": 1000, "seed_noise": 0.15},
                    }
                ]
            elif wants_status and tools and any(t["name"] == "get_status" for t in tools):
                faux["choices"][0]["message"]["tool_calls"] = [
                    {"id": "tool_2", "name": "get_status", "arguments": {}}
                ]
            elif wants_opt and tools and any(t["name"] == "optimize_strategy" for t in tools):
                faux["choices"][0]["message"]["tool_calls"] = [
                    {"id": "tool_3", "name": "optimize_strategy", "arguments": {}}
                ]
            else:
                faux["choices"][0]["message"]["content"] = (
                    "No tools requested. Ask me to generate, get status, or optimize strategy."
                )
            return faux

        try:
            if self.backend == "OpenAI":
                resp = self.client.ChatCompletion.create(
                    model=model or "gpt-4o-mini",
                    messages=messages,
                    tools=[{"type": "function", "function": t} for t in tools] if tools else None,
                )
                return resp
            elif self.backend == "Anthropic":
                client = self.client.Client(api_key=os.getenv("ANTHROPIC_API_KEY"))
                resp = client.messages.create(
                    model=model or "claude-3-opus-20240229",
                    messages=messages,
                    tools=[
                        {
                            "name": t["name"],
                            "description": t["description"],
                            "input_schema": t["parameters"],
                        }
                        for t in tools
                    ]
                    if tools
                    else None,
                )
                tool_calls = []
                content = ""
                if resp and hasattr(resp, "content"):
                    content = str(resp.content)
                return {"choices": [{"message": {"content": content, "tool_calls": tool_calls}}]}
        except Exception as e:
            self.status_bus.emit("llm_error", {"error": str(e)})
            return {"choices": [{"message": {"content": f"LLM error: {e}"}}]}

        return {"choices": [{"message": {"content": "Real backend path not implemented correctly."}}]}


def run_llm_orchestrator(bridge: LLMBridge, tools_schema, cfg: dict, status_bus: StatusBus):
    client = AutoLLMClient(status_bus)
    messages = [
        {
            "role": "system",
            "content": "You control the Mythic Swarm Engine via tools. Improve strategy and evolution.",
        }
    ]
    last_error_time = 0.0
    while not _shutdown_event.is_set():
        try:
            arch = bridge.archive_ref["ref"]
            stats = arch["stats"]
            if stats["completed_projects"] < 5:
                user_prompt = "Generate a swarm project and report status."
            elif stats["iterations"] % 50 == 0:
                user_prompt = "Analyze archive trends and optimize strategy."
            else:
                user_prompt = (
                    "Get status and, if stagnating, optimize strategy and suggest chaos constraints."
                )
            messages.append({"role": "user", "content": user_prompt})
            resp = client.chat(messages, tools_schema, model=cfg.get("llm_model"))
            tool_calls = []
            content = None
            if isinstance(resp, dict) and "choices" in resp:
                msg = resp["choices"][0].get("message", {})
                tool_calls = msg.get("tool_calls", [])
                content = msg.get("content")
            else:
                content = str(resp)
            if tool_calls:
                for call in tool_calls:
                    name = call.get("name")
                    args = call.get("arguments", {})
                    result = handle_llm_tool_call(bridge, name, args, status_bus)
                    if name == "optimize_strategy":
                        pass
                    messages.append(
                        {
                            "role": "assistant",
                            "content": f"Tool {name} result: {json.dumps(result)[:2000]}",
                        }
                    )
            else:
                messages.append({"role": "assistant", "content": str(content)})
            time.sleep(4.0)
        except Exception as e:
            status_bus.emit("llm_error", {"error": str(e)})
            logging.exception("LLM orchestrator loop error")
            now = time.time()
            if now - last_error_time < 60.0:
                time.sleep(10.0)
            else:
                time.sleep(5.0)
            last_error_time = now


# ============================================================
# Forklift executor + router training stubs
# ============================================================

class ForkliftExecutor:
    """
    Stub executor: in your real setup this would:
    - route tiles
    - collect stats
    - support INT8/FP8 emulation
    - train a policy net on latency/telemetry
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._stats = {"calls": 0}

    def reset_stats(self, clear_router_data: bool = False):
        with self._lock:
            self._stats = {"calls": 0}

    def linear(self, layer_name, weight, bias, x, layer_depth: int = 0):
        with self._lock:
            self._stats["calls"] += 1
        return torch.nn.functional.linear(x, weight, bias)

    def stats(self):
        with self._lock:
            return dict(self._stats)


EXECUTOR = ForkliftExecutor()


def get_system_telemetry() -> dict:
    # stub: feed some random telemetry
    return {
        "cpu": random.uniform(5, 80),
        "gpu": random.uniform(0, 100),
        "mem": random.uniform(10, 90),
    }


def train_policy_net_step(sys_tel: dict, latency_ms: float):
    # stub: no-op; in real code you'd update a small policy net here
    pass


# ============================================================
# ForkliftLinear wrapper + model patching
# ============================================================

if HAS_TORCH:
    class ForkliftLinear(nn.Module):
        def __init__(self, base: nn.Linear, name: str, executor: ForkliftExecutor, depth: int = 0):
            super().__init__()
            self.base = base
            self.name = name
            self.executor = executor
            self.depth = depth

        def forward(self, x):
            return self.executor.linear(
                layer_name=self.name,
                weight=self.base.weight,
                bias=self.base.bias,
                x=x,
                layer_depth=self.depth,
            )


    def _patch_module_with_forklift(module: nn.Module, prefix: str = "", depth: int = 0):
        """
        Recursively replace nn.Linear with ForkliftLinear, preserving names.
        Depth is a rough proxy for layer_depth used in router features.
        """
        for child_name, child in list(module.named_children()):
            full_name = f"{prefix}{child_name}"
            if isinstance(child, nn.Linear):
                setattr(
                    module,
                    child_name,
                    ForkliftLinear(child, full_name, EXECUTOR, depth),
                )
            else:
                _patch_module_with_forklift(child, full_name + ".", depth + 1)


    def patch_model_with_forklift(model: nn.Module):
        _patch_module_with_forklift(model, prefix="", depth=0)
else:
    ForkliftLinear = object  # type: ignore

    def patch_model_with_forklift(model):
        return model


# ============================================================
# TinyFallback model
# ============================================================

class TinyFallback(nn.Module if HAS_TORCH else object):  # type: ignore
    def __init__(self, vocab_size: int = 256, hidden: int = 64):
        if HAS_TORCH:
            super().__init__()
            self.emb = nn.Embedding(vocab_size, hidden)
            self.lin = nn.Linear(hidden, vocab_size)
        else:
            pass

    def forward(self, input_ids):
        if not HAS_TORCH:
            return input_ids
        x = self.emb(input_ids)
        x = x.mean(dim=1)
        logits = self.lin(x)
        return logits


CURRENT_MODEL = None
CURRENT_TOKENIZER = None
CURRENT_MODEL_NAME = None
IS_FALLBACK_MODEL = False


# ============================================================
# Model loading (LLaMA / HF)
# ============================================================

def load_model(model_name: str = PRIMARY_MODEL_NAME):
    global CURRENT_MODEL, CURRENT_TOKENIZER, CURRENT_MODEL_NAME, IS_FALLBACK_MODEL

    if CURRENT_MODEL is not None and CURRENT_TOKENIZER is not None:
        return

    if not HAS_TORCH or AutoTokenizer is None or AutoModelForCausalLM is None:
        print("[Node] Torch / HF not available, using TinyFallback.")
        tok = None

        class DummyTok:
            def __init__(self):
                self.eos_token_id = 0

            def __call__(self, text, return_tensors=None):
                ids = [ord(c) % 256 for c in text]
                t = torch.tensor([ids], dtype=torch.long) if HAS_TORCH else ids
                return {"input_ids": t}

            def decode(self, ids, skip_special_tokens=True):
                if HAS_TORCH:
                    ids = ids.tolist()
                return "".join(chr(int(i) % 256) for i in ids)

        tok = DummyTok()
        mdl = TinyFallback().to(DEFAULT_DEVICE) if HAS_TORCH else TinyFallback()
        if HAS_TORCH:
            mdl.eval()
        CURRENT_MODEL = mdl
        CURRENT_TOKENIZER = tok
        CURRENT_MODEL_NAME = "TinyFallback"
        IS_FALLBACK_MODEL = True
        print("[Node] Using TinyFallback model (no torch/HF).")
        return

    print(f"[Node] Loading model: {model_name}")
    try:
        tok = AutoTokenizer.from_pretrained(model_name)
        mdl = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch.float16 if HAS_CUDA else torch.float32,
            device_map="auto" if HAS_CUDA and NUM_GPUS > 1 else None,
        )
        mdl.to(DEFAULT_DEVICE)
        mdl.eval()
        patch_model_with_forklift(mdl)

        CURRENT_MODEL = mdl
        CURRENT_TOKENIZER = tok
        CURRENT_MODEL_NAME = model_name
        IS_FALLBACK_MODEL = False
        print(f"[Node] Loaded HF model: {model_name}")
    except Exception as e:
        print(f"[Node] Failed to load {model_name}, falling back to TinyFallback: {e}")
        try:
            tok = AutoTokenizer.from_pretrained("gpt2")
        except Exception:
            class DummyTok:
                def __init__(self):
                    self.eos_token_id = 0

                def __call__(self, text, return_tensors=None):
                    ids = [ord(c) % 256 for c in text]
                    t = torch.tensor([ids], dtype=torch.long) if HAS_TORCH else ids
                    return {"input_ids": t}

                def decode(self, ids, skip_special_tokens=True):
                    if HAS_TORCH and isinstance(ids, torch.Tensor):
                        ids = ids.tolist()
                    return "".join(chr(int(i) % 256) for i in ids)

            tok = DummyTok()

        mdl = TinyFallback().to(DEFAULT_DEVICE) if HAS_TORCH else TinyFallback()
        if HAS_TORCH:
            mdl.eval()

        CURRENT_MODEL = mdl
        CURRENT_TOKENIZER = tok
        CURRENT_MODEL_NAME = "TinyFallback"
        IS_FALLBACK_MODEL = True
        print("[Node] Using TinyFallback model.")


# ============================================================
# Text generation API
# ============================================================

@torch.inference_mode() if HAS_TORCH else (lambda f: f)
def generate_text(prompt: str, max_new_tokens: int = 128) -> Tuple[str, dict]:
    load_model()
    EXECUTOR.reset_stats(clear_router_data=False)

    tok = CURRENT_TOKENIZER
    mdl = CURRENT_MODEL

    if tok is None:
        raise RuntimeError("Tokenizer not initialized")

    inputs = tok(prompt, return_tensors="pt")
    if isinstance(inputs, dict) and HAS_TORCH:
        for k in inputs:
            if isinstance(inputs[k], torch.Tensor):
                inputs[k] = inputs[k].to(DEFAULT_DEVICE)

    t0 = time.time()

    if isinstance(mdl, TinyFallback):
        if HAS_TORCH:
            out_ids = inputs["input_ids"]
        else:
            out_ids = inputs["input_ids"]
    else:
        out_ids = mdl.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=True,
            top_p=0.9,
            temperature=0.8,
            pad_token_id=getattr(tok, "eos_token_id", None),
        )

    latency_ms = (time.time() - t0) * 1000.0
    if HAS_TORCH and isinstance(out_ids, torch.Tensor):
        text = tok.decode(out_ids[0], skip_special_tokens=True)
    else:
        text = prompt  # fallback

    stats = EXECUTOR.stats()
    stats["model_name"] = CURRENT_MODEL_NAME
    stats["is_fallback"] = IS_FALLBACK_MODEL
    stats["latency_ms"] = latency_ms

    try:
        sys_tel = get_system_telemetry()
        train_policy_net_step(sys_tel, latency_ms)
    except Exception:
        pass

    return text, stats


# ============================================================
# Telemetry + distributed cache stubs
# ============================================================

GLOBAL_CACHE = {}


def telemetry_broadcast_loop():
    while not _shutdown_event.is_set():
        time.sleep(5.0)


def telemetry_listener_loop():
    while not _shutdown_event.is_set():
        time.sleep(5.0)


def distributed_cache_broadcast_loop(cache: dict):
    while not _shutdown_event.is_set():
        time.sleep(5.0)


def distributed_cache_listener_loop():
    while not _shutdown_event.is_set():
        time.sleep(5.0)


# ============================================================
# RPC server (LLM node)
# ============================================================

def handle_rpc_client(conn: socket.socket, addr):
    buf = b""
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                line, _, rest = buf.partition(b"\n")
                buf = rest
                try:
                    req = json.loads(line.decode())
                    prompt = req.get("prompt", "")
                    max_new_tokens = int(req.get("max_new_tokens", 128))
                    print(f"[Node] RPC request from {addr}, tokens={max_new_tokens}")
                    text, stats = generate_text(prompt, max_new_tokens=max_new_tokens)
                    resp = {"text": text, "stats": stats}
                except Exception as e:
                    resp = {"error": str(e), "stats": {}}
                conn.sendall((json.dumps(resp) + "\n").encode())
                break
    finally:
        conn.close()


def rpc_server_loop(host: str, port: int):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(16)
    print(f"[Node] RPC server listening on {host}:{port}")
    while not _shutdown_event.is_set():
        try:
            conn, addr = s.accept()
        except Exception:
            if _shutdown_event.is_set():
                break
            continue
        t = threading.Thread(target=handle_rpc_client, args=(conn, addr), daemon=True)
        t.start()


# ============================================================
# Simple local CLI (non-GUI mode)
# ============================================================

def run_local_cli():
    print("[Node] Local CLI mode. Type 'quit' to exit.")
    while True:
        try:
            prompt = input("\n>>> ").strip()
        except EOFError:
            break
        if not prompt:
            continue
        if prompt.lower() in ("q", "quit", "exit"):
            break
        text, stats = generate_text(prompt)
        print("\n--- Response ---")
        print(text)
        print("\n--- Stats ---")
        for k, v in stats.items():
            print(f"{k}: {v}")


# ============================================================
# Headless detection + service/systemd helpers
# ============================================================

def is_headless_environment() -> bool:
    if os.environ.get("MYTHIC_HEADLESS", "").lower() in ("1", "true", "yes"):
        return True
    if os.name != "nt":
        if not os.environ.get("DISPLAY"):
            return True
        if os.environ.get("INVOCATION_ID") or os.environ.get("JOURNAL_STREAM"):
            return True
    if not HAS_TK:
        return True
    try:
        r = tk.Tk()
        r.withdraw()
        r.update_idletasks()
        r.destroy()
    except Exception:
        return True
    return False


def run_headless_node(cfg: dict):
    print("[Node] Running in headless RPC/swarm mode.")
    status = StatusBus()
    paths = node_paths(cfg)
    archive_ref = {"ref": load_archive(paths)}
    feeds = FeedRegistry()
    sysmon = SystemMetrics()
    sysmon.start()
    mesh = BorgMesh()
    scanner_in, ops_q = queue.Queue(), queue.Queue()
    mesh_peer = MeshPeer(cfg, mesh, status)
    mesh_peer.start()
    swarm_node = SwarmNode(cfg, archive_ref, status)
    swarm_node.start()
    bridge = LLMBridge(cfg, archive_ref, status)
    llm_thread = threading.Thread(
        target=run_llm_orchestrator,
        args=(bridge, LLM_TOOLS_SCHEMA, cfg, status),
        daemon=True,
    )
    llm_thread.start()
    evolver_thread = threading.Thread(
        target=evolve,
        args=(cfg, archive_ref, feeds, sysmon, status, mesh, scanner_in, ops_q, bridge),
        daemon=True,
    )
    evolver_thread.start()
    threading.Thread(
        target=rpc_server_loop,
        args=("0.0.0.0", cfg.get("rpc_port", 6000)),
        daemon=True,
    ).start()
    try:
        while not _shutdown_event.is_set():
            time.sleep(5.0)
    except KeyboardInterrupt:
        pass


def run_windows_service(cfg: dict):
    print("[Node] Starting in Windows service mode (console stub).")
    run_headless_node(cfg)


def run_systemd_service(cfg: dict):
    logging.getLogger().setLevel(logging.INFO)
    print("[Node] Starting in systemd mode.")
    run_headless_node(cfg)


# ============================================================
# Main entrypoint
# ============================================================

def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc-port", type=int, default=6000)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument(
        "--headless-node",
        action="store_true",
        help="Run without GUI (RPC + swarm + mesh only)",
    )
    parser.add_argument(
        "--windows-service",
        action="store_true",
        help="Run in Windows service mode (no GUI, long-running)",
    )
    parser.add_argument(
        "--systemd",
        action="store_true",
        help="Run in Linux systemd mode (no GUI, long-running)",
    )
    args = parser.parse_args()

    cfg = {
        "novelty_weight": 0.25,
        "utility_weight": 0.35,
        "impact_weight": 0.25,
        "curiosity_weight": 0.15,
        "strategy_mutation_scale": 0.02,
        "max_archive_size": 1000,
        "strategy_enable": True,
        "strategy_mutate_interval": 120.0,
        "strategy_selection_window": 500,
        "sleep_min_seconds": 0.025,
        "sleep_max_seconds": 0.8,
        "cpu_target_util": 0.5,
        "iterations_per_tick": 10,
        "batch_size": 20,
        "seed_noise": 0.15,
        "node_name": "local",
        "patience": 50,
        "save_interval_steps": 20,
        "backoff_initial": 0.5,
        "backoff_max": 5.0,
        "chaos_batch_size": 10,
        "storage_dir": os.path.join(os.getcwd(), "projects"),
        "alternate_storage_dir": os.path.join(os.getcwd(), "projects_alt"),
        "manual_storage_dir": None,
        "storage_daily_subdir": True,
        "storage_switch_threshold_pct": 70.0,
        "tone_enable": False,
        "min_project_lines": 1000,
        "llm_model": None,
        "mesh_port": 55555,
        "mesh_seeds": [],
        "swarm_port": 55600,
        "swarm_seeds": [],
        "llm_chaos_enable": True,
    }

    cfg["rpc_port"] = args.rpc_port

    threading.Thread(target=telemetry_broadcast_loop, daemon=True).start()
    threading.Thread(target=telemetry_listener_loop, daemon=True).start()
    threading.Thread(
        target=distributed_cache_broadcast_loop,
        args=(GLOBAL_CACHE,),
        daemon=True,
    ).start()
    threading.Thread(
        target=distributed_cache_listener_loop,
        daemon=True,
    ).start()

    load_model()

    threading.Thread(
        target=rpc_server_loop,
        args=(args.host, args.rpc_port),
        daemon=True,
    ).start()

    if args.windows_service:
        run_windows_service(cfg)
        return

    if args.systemd:
        run_systemd_service(cfg)
        return

    if args.headless_node or is_headless_environment():
        run_headless_node(cfg)
        return

    if not HAS_TK:
        print("[Node] Tkinter not available; falling back to headless mode.")
        run_headless_node(cfg)
        return

    print("[Node] Starting full GUI cockpit.")
    app = AdvancedGUI(cfg)
    bridge = LLMBridge(cfg, app.archive_ref, app.status)
    mesh_peer = MeshPeer(cfg, app.mesh, app.status)
    mesh_peer.start()
    swarm_node = SwarmNode(cfg, app.archive_ref, app.status)
    swarm_node.start()
    llm_thread = threading.Thread(
        target=run_llm_orchestrator,
        args=(bridge, LLM_TOOLS_SCHEMA, cfg, app.status),
        daemon=True,
    )
    llm_thread.start()
    app.start_evolver(bridge)

    try:
        result = handle_llm_tool_call(
            bridge,
            "generate_project",
            {"min_project_lines": 1200, "seed_noise": 0.2},
            app.status,
        )
        logging.info(f"[LLM] Generated project: {result}")
    except Exception:
        logging.exception("LLM bridge demo failed.")

    print("Starting GUI...")
    app.mainloop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
