#!/usr/bin/env python3
# ultra_swarm_hybrid_monolith.py
#
# HYBRID MERGE:
# - Swarm Intelligence Core (v2)
# - Queen/Agent Cluster (v2, RAFT, metrics, threat matrix, borg gas)
# - Smart IoT System (v2, MQTT, event engine, cloud AI)
# - Live Data Ingestion (system, network, internet, FS, IoT)
# - Autoloader for dependencies
# - MLBackend (stub/onnx/sklearn/azure/sagemaker)
# - Prometheus exporters + REST API (v2)
# - ForkliftLinear LLM wrapper + TinyFallback
# - HF model loader + text generation
# - RPC text-gen server + local CLI

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Callable, Tuple
import random
import json
import os
import time
import sys
import socket
import importlib
import subprocess
import threading
import multiprocessing
import asyncio
from queue import Queue, Empty

# ============================================================
# AUTOLOADER
# ============================================================

def autoload(module_name, package_name=None):
    try:
        return importlib.import_module(module_name)
    except ImportError:
        print(f"[Autoloader] Missing module '{module_name}', installing...")
        pkg = package_name if package_name else module_name
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
        except Exception as e:
            print(f"[Autoloader] Failed to install {pkg}: {e}")
            return None
        try:
            return importlib.import_module(module_name)
        except ImportError:
            print(f"[Autoloader] Could not import {module_name} even after installation.")
            return None

psutil = autoload("psutil")
requests = autoload("requests")
watchdog_pkg = autoload("watchdog", "watchdog")
paho_mqtt = autoload("paho.mqtt.client", "paho-mqtt")
prometheus_client = autoload("prometheus_client")
flask_pkg = autoload("flask", "flask")
onnxruntime = autoload("onnxruntime")
sklearn = autoload("sklearn")
joblib = autoload("joblib")
torch = autoload("torch")
transformers = autoload("transformers")

Observer = None
FileSystemEventHandler = object
if watchdog_pkg is not None:
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except Exception:
        Observer = None
        FileSystemEventHandler = object

Flask = None
if flask_pkg is not None:
    from flask import Flask, jsonify, request as flask_request

PROM_GAUGES: Dict[str, Any] = {}
PROM_COUNTERS: Dict[str, Any] = {}
if prometheus_client is not None:
    from prometheus_client import Gauge, Counter, start_http_server

# ============================================================
# TORCH / TRANSFORMERS + FORKLIFT LAYER
# ============================================================

if torch is not None:
    import torch.nn as nn
    from torch import Tensor
else:
    nn = None
    Tensor = Any

if transformers is not None:
    from transformers import AutoTokenizer, AutoModelForCausalLM
else:
    AutoTokenizer = None
    AutoModelForCausalLM = None

PRIMARY_MODEL_NAME = "gpt2"
HAS_CUDA = bool(torch and torch.cuda.is_available())
NUM_GPUS = torch.cuda.device_count() if HAS_CUDA else 0
DEFAULT_DEVICE = "cuda" if HAS_CUDA else "cpu"

class ForkliftExecutor:
    def __init__(self):
        self.calls = []
    def linear(self, layer_name: str, weight: Tensor, bias: Optional[Tensor], x: Tensor, layer_depth: int = 0):
        self.calls.append({"layer": layer_name, "depth": layer_depth, "shape": tuple(x.shape)})
        return torch.nn.functional.linear(x, weight, bias)
    def reset_stats(self, clear_router_data: bool = False):
        if clear_router_data:
            self.calls.clear()
    def stats(self) -> dict:
        return {"num_linear_calls": len(self.calls)}

EXECUTOR = ForkliftExecutor()
CURRENT_MODEL = None
CURRENT_TOKENIZER = None
CURRENT_MODEL_NAME = None
IS_FALLBACK_MODEL = False

class TinyFallback(nn.Module):
    def __init__(self, vocab_size: int = 256, hidden: int = 64):
        super().__init__()
        self.embed = nn.Embedding(vocab_size, hidden)
        self.fc = nn.Linear(hidden, vocab_size)
    def forward(self, input_ids: Tensor):
        x = self.embed(input_ids)
        x = x.mean(dim=1)
        return self.fc(x)
    def generate(self, input_ids: Tensor, max_new_tokens: int = 32, **kwargs):
        return input_ids

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
    for child_name, child in list(module.named_children()):
        full_name = f"{prefix}{child_name}"
        if isinstance(child, nn.Linear):
            setattr(module, child_name, ForkliftLinear(child, full_name, EXECUTOR, depth))
        else:
            _patch_module_with_forklift(child, full_name + ".", depth + 1)

def patch_model_with_forklift(model: nn.Module):
    _patch_module_with_forklift(model, prefix="", depth=0)

def load_model(model_name: str = PRIMARY_MODEL_NAME):
    global CURRENT_MODEL, CURRENT_TOKENIZER, CURRENT_MODEL_NAME, IS_FALLBACK_MODEL
    if CURRENT_MODEL is not None and CURRENT_TOKENIZER is not None:
        return
    print(f"[Node] Loading model: {model_name}")
    try:
        if AutoTokenizer is None or AutoModelForCausalLM is None or torch is None:
            raise RuntimeError("transformers/torch not available")
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
        print(f"[Node] Failed to load {model_name}, falling back: {e}")
        if AutoTokenizer is not None:
            try:
                tok = AutoTokenizer.from_pretrained("gpt2")
            except Exception:
                tok = None
        else:
            tok = None
        if tok is None:
            class DummyTok:
                def __init__(self):
                    self.eos_token_id = 0
                def __call__(self, text, return_tensors=None):
                    ids = [ord(c) % 256 for c in text]
                    t = torch.tensor([ids], dtype=torch.long)
                    return {"input_ids": t}
                def decode(self, ids, skip_special_tokens=True):
                    return "".join(chr(int(i) % 256) for i in ids)
            tok = DummyTok()
        mdl = TinyFallback().to(DEFAULT_DEVICE)
        mdl.eval()
        CURRENT_MODEL = mdl
        CURRENT_TOKENIZER = tok
        CURRENT_MODEL_NAME = "TinyFallback"
        IS_FALLBACK_MODEL = True
        print("[Node] Using TinyFallback model.")

# ============================================================
# CORE DATA MODEL / SPECIES DB (v2)
# ============================================================

@dataclass
class Species:
    name: str
    universe: str
    tier: int
    classification: str
    threat_level: str
    traits: List[str] = field(default_factory=list)

SPECIES: List[Species] = [
    # TIER 5
    Species("The One Above All", "Marvel", 5, "Supreme Being", "Omnipotent",
            ["Author of Marvel reality", "Infinite power"]),
    Species("The Presence", "DC", 5, "Supreme Being", "Omnipotent",
            ["Creator of DC multiverse"]),
    Species("Overmonitor / Monitor-Mind", "DC", 5, "Metacosmic Entity", "Transcendent",
            ["Canvas of DC reality"]),
    Species("Beyonders", "Marvel", 5, "Extradimensional Race", "Multiversal",
            ["Killed the Living Tribunal"]),
    Species("Azathoth", "Lovecraft", 5, "Outer God", "Cosmic Oblivion",
            ["All reality is its dream"]),
    Species("Living Tribunal", "Marvel", 5, "Multiversal Judge", "Cosmic Law",
            ["Balances all cosmic forces"]),
    Species("Anti-Monitor", "DC", 5, "Multiversal Devourer", "Reality-Eater",
            ["Consumes universes"]),
    Species("The Endless", "DC", 5, "Conceptual Entities", "Abstract",
            ["Embodiments of Death, Dream, Destiny"]),
    Species("First Firmament", "Marvel", 5, "Primordial Cosmos", "Cosmic Origin",
            ["First sentient universe"]),
    Species("Great Beings", "Bionicle", 5, "Creator Race", "Architect-Level",
            ["Design entire universes"]),

    # TIER 4
    Species("Q Continuum", "Star Trek", 4, "Reality Manipulators", "Omniversal",
            ["Rewrite physics at will"]),
    Species("Ori (Ascended)", "Stargate", 4, "Energy Gods", "Worship-Fueled",
            ["Galaxy-scale power"]),
    Species("Time Lords", "Doctor Who", 4, "Temporal Civilization", "Time Control",
            ["Paradox weapons"]),
    Species("Celestials", "Marvel", 4, "Cosmic Engineers", "Galaxy Makers",
            ["Create/destroy star systems"]),
    Species("True Form Darkseid", "DC", 4, "New God", "Multiversal Tyrant",
            ["Avatar of Anti-Life"]),
    Species("Kryptonians (Yellow Sun)", "DC", 4, "Solar Demigods", "Super-Physical",
            ["Planet-busting strength"]),
    Species("First Ones", "Babylon 5", 4, "Ancient Race", "Elder Power",
            ["Precursor civilization"]),
    Species("Shadows", "Babylon 5", 4, "Chaos Race", "Manipulators",
            ["Tech-organic godships"]),
    Species("Vorlons", "Babylon 5", 4, "Order Race", "Energy Beings",
            ["Telepathic domination"]),
    Species("Forerunners (Domain)", "Halo", 4, "Hyper-Advanced", "Reality Tech",
            ["Star system weapons"]),

    # TIER 3
    Species("Species 8472 / Undine", "Star Trek", 3, "Bio-Dimensional", "Tech-Immune",
            ["One-shot Borg cubes"]),
    Species("Tyranids", "Warhammer 40K", 3, "Hive Swarm", "Galaxy-Eaters",
            ["Endless biomass"]),
    Species("Flood (Gravemind)", "Halo", 3, "Parasitic Hive", "Universal Corruption",
            ["Infects AI + biology"]),
    Species("Zerg (Full Swarm)", "StarCraft", 3, "Bio-Evolutionary", "Hyper-Adaptive",
            ["Genetic assimilation"]),
    Species("Necrons (Full Awakening)", "Warhammer 40K", 3, "Machine Undead", "Star-Eaters",
            ["Reality phase weapons"]),
    Species("Xenomorph Prime Hive", "Alien EU", 3, "Bio-Weapon", "Perfect Organism",
            ["Exponential spread"]),
    Species("Yuuzhan Vong", "Star Wars Legends", 3, "Bio-Tech Empire", "Force-Immune",
            ["Living warships"]),
    Species("The Thing", "Carpenter", 3, "Assimilation Organism", "Perfect Mimic",
            ["100% infection rate"]),
    Species("Mist Creatures", "Stephen King", 3, "Interdimensional Predators", "Apex Hunters",
            ["Physics-breaking biology"]),
    Species("Formics", "Ender’s Game", 3, "Hive Civilization", "Telepathic",
            ["Rapid adaptation"]),

    # TIER 2 — MACHINE EMPIRES
    Species("Replicators", "Stargate", 2, "Self-Replicating AI", "Exponential",
            ["Consume technology"]),
    Species("Daleks", "Doctor Who", 2, "Genocidal Machines", "Hate-Powered",
            ["Reality bombs"]),
    Species("Borg", "Star Trek", 2, "Assimilation Collective", "Adaptive",
            ["Nanite assimilation"]),
    Species("Reapers", "Mass Effect", 2, "Cycle Resetters", "Harvesters",
            ["Machine gods"]),
    Species("Skynet", "Terminator", 2, "AI Overlord", "Extermination",
            ["Planet-scale control"]),
    Species("Cylons", "Battlestar Galactica", 2, "Machine Race", "Resurrection",
            ["AI evolution"]),
    Species("Sentinels", "X-Men", 2, "Mutant Hunters", "Adaptive AI",
            ["Anti-meta weapons"]),
    Species("Decepticons", "Transformers", 2, "Cybertronian Race", "Planetary",
            ["Living machines"]),
    Species("Vok", "Beast Wars", 2, "Energy Machines", "Reality Tech",
            ["Planet reformatting"]),
    Species("Necrons (Low Awakening)", "Warhammer 40K", 2, "Machine Undead", "Dormant",
            ["Still terrifying"]),

    # TIER 2 — QUEEN / AGENT CLUSTER FACTION
    Species("Queen Node Collective", "Cluster-Prime", 2, "Distributed ML Orchestrator",
            "Adaptive AI", ["RAFT consensus", "ML training", "Job orchestration"]),
    Species("Agent Swarm Nodes", "Cluster-Prime", 2, "Distributed Executors",
            "Exponential", ["Threat matrix", "Borg gas index", "Telemetry exporters"]),

    # TIER 1 — ADVANCED CIVILIZATIONS
    Species("Asgard", "Stargate", 1, "Advanced Civilization", "High-Tech",
            ["Cloning, beam weapons"]),
    Species("Protoss", "StarCraft", 1, "Psionic Warriors", "High-Tech",
            ["Warp tech"]),
    Species("Covenant", "Halo", 1, "Religious Empire", "Militaristic",
            ["Plasma fleets"]),
    Species("Cybermen", "Doctor Who", 1, "Cybernetic Race", "Assimilation",
            ["Conversion tech"]),
    Species("Minbari", "Babylon 5", 1, "Advanced Race", "Superior Tech",
            ["Stealth cruisers"]),
    Species("Chiss Ascendancy", "Star Wars", 1, "Strategic Empire", "Tactical",
            ["Long-range warfare"]),
    Species("Peacekeepers", "Farscape", 1, "Militaristic", "Aggressive",
            ["Genetic engineering"]),

    # TIER 1 — IoT Faction
    Species("Smart IoT Mesh Collective", "IoT-Prime", 1, "Distributed Sensor Swarm",
            "High-Tech", ["TLS-encrypted mesh", "MQTT swarm", "Cloud-linked AI", "OTA evolution"]),

    # TIER 0 — FODDER
    Species("Humans", "Any", 0, "Baseline", "Weak", ["Varies by universe"]),
    Species("Klingons", "Star Trek", 0, "Warrior Race", "Conventional",
            ["Honor-based combat"]),
    Species("Romulans", "Star Trek", 0, "Empire", "Conventional",
            ["Cloaking tech"]),
    Species("Mandalorians", "Star Wars", 0, "Warriors", "Elite",
            ["Beskar armor"]),
    Species("Colonial Marines", "Alien", 0, "Military", "Low-Tech",
            ["Pulse rifles"]),
    Species("UNSC", "Halo", 0, "Human Military", "Conventional",
            ["MAC guns"]),
    Species("Goa’uld Jaffa", "Stargate", 0, "Foot Soldiers", "Limited",
            ["Staff weapons"]),
    Species("Narn", "Babylon 5", 0, "Empire", "Conventional",
            ["Heavy ships"]),
    Species("Centauri", "Babylon 5", 0, "Empire", "Conventional",
            ["Ion cannons"]),
]

# ============================================================
# STRATEGY WEIGHTS / POWER / BATTLE
# ============================================================

weights: Dict[str, float] = {
    "novelty_weight": 0.25,
    "utility_weight": 0.35,
    "impact_weight": 0.25,
    "curiosity_weight": 0.15,
}

def adjust_weights(perf: Dict[str, float]) -> Dict[str, float]:
    w = dict(weights)
    cr = perf.get("completion_rate", 0.0)
    avg_obj = perf.get("avg_objective", 0.0)
    feed_entropy = perf.get("feed_entropy", 0.0)
    sys_entropy = perf.get("sys_entropy", 0.0)

    if cr < 0.05:
        w["novelty_weight"] = min(1.2, w["novelty_weight"] + 0.05)
        w["curiosity_weight"] = min(1.2, w["curiosity_weight"] + 0.03)

    if avg_obj > 1.0:
        w["utility_weight"] = min(1.2, w["utility_weight"] + 0.04)
        w["impact_weight"] = min(1.2, w["impact_weight"] + 0.02)

    w["novelty_weight"] = min(1.3, w["novelty_weight"] + 0.01 * feed_entropy)
    w["curiosity_weight"] = min(1.3, w["curiosity_weight"] + 0.01 * sys_entropy)

    return w

THREAT_MULTIPLIER: Dict[str, float] = {
    "Omnipotent": 10.0,
    "Transcendent": 9.0,
    "Multiversal": 8.5,
    "Cosmic Oblivion": 8.0,
    "Cosmic Law": 8.0,
    "Reality-Eater": 8.0,
    "Abstract": 7.5,
    "Cosmic Origin": 7.5,
    "Architect-Level": 7.0,
    "Omniversal": 7.0,
    "Worship-Fueled": 6.5,
    "Time Control": 6.5,
    "Galaxy Makers": 6.5,
    "Multiversal Tyrant": 6.5,
    "Super-Physical": 6.0,
    "Elder Power": 6.0,
    "Manipulators": 6.0,
    "Energy Beings": 6.0,
    "Reality Tech": 6.0,
    "Tech-Immune": 5.5,
    "Galaxy-Eaters": 5.5,
    "Universal Corruption": 5.5,
    "Hyper-Adaptive": 5.0,
    "Star-Eaters": 5.0,
    "Perfect Organism": 5.0,
    "Force-Immune": 5.0,
    "Perfect Mimic": 5.0,
    "Apex Hunters": 5.0,
    "Telepathic": 4.5,
    "Exponential": 4.5,
    "Hate-Powered": 4.5,
    "Adaptive": 4.5,
    "Harvesters": 4.5,
    "Extermination": 4.0,
    "Resurrection": 4.0,
    "Adaptive AI": 4.0,
    "Planetary": 4.0,
    "High-Tech": 3.5,
    "Militaristic": 3.5,
    "Assimilation": 3.5,
    "Superior Tech": 3.5,
    "Tactical": 3.5,
    "Aggressive": 3.5,
    "Elite": 3.0,
    "Conventional": 2.5,
    "Low-Tech": 2.0,
    "Limited": 2.0,
    "Weak": 1.0,
}

def power_score(species: Species, w: Dict[str, float]) -> float:
    base = species.tier * 100.0
    mult = THREAT_MULTIPLIER.get(species.threat_level, 3.0)
    rand = random.uniform(0.9, 1.1)

    novelty = w.get("novelty_weight", 0.25)
    utility = w.get("utility_weight", 0.35)
    impact = w.get("impact_weight", 0.25)
    curiosity = w.get("curiosity_weight", 0.15)

    novelty_factor = 1.0 + novelty * random.uniform(-0.2, 0.2)
    utility_factor = 1.0 + utility * (species.tier / 5.0)
    impact_factor = 1.0 + impact * (mult / 10.0)
    curiosity_factor = 1.0 + curiosity * random.uniform(-0.3, 0.3)

    total_factor = novelty_factor * utility_factor * impact_factor * curiosity_factor
    return base * mult * rand * total_factor

def battle(species_a: Species, species_b: Species, w: Dict[str, float]) -> Species:
    score_a = power_score(species_a, w)
    score_b = power_score(species_b, w)
    if score_a == score_b:
        score_b *= 1.01
    winner = species_a if score_a > score_b else species_b
    print("=" * 80)
    print(f"BATTLE: {species_a.name} vs {species_b.name}")
    print(f"  {species_a.name} power score: {score_a:.2f}")
    print(f"  {species_b.name} power score: {score_b:.2f}")
    print(f"  WINNER: {winner.name} ({winner.universe}, Tier {winner.tier}, {winner.threat_level})")
    print("=" * 80)
    return winner

# ============================================================
# QUERY / DISPLAY / EVOLUTION
# ============================================================

def get_by_tier(tier: int) -> List[Species]:
    return [s for s in SPECIES if s.tier == tier]

def get_by_name(name: str) -> Optional[Species]:
    name = name.lower()
    for s in SPECIES:
        if s.name.lower() == name:
            return s
    return None

def get_by_universe(universe: str) -> List[Species]:
    universe = universe.lower()
    return [s for s in SPECIES if s.universe.lower() == universe]

def print_header(text: str):
    print("=" * 80)
    print(text)
    print("=" * 80)

def print_species_list(title: str, species_list: List[Species]):
    print_header(title)
    for s in species_list:
        print(f"[Tier {s.tier}] {s.name} — {s.universe} — {s.classification} — {s.threat_level}")
    print()

def print_full_tier_list():
    for tier in reversed(range(6)):
        tier_species = get_by_tier(tier)
        if tier_species:
            print_species_list(f"TIER {tier}", tier_species)

def evolve_species(species: Species, w: Dict[str, float]):
    print_header(f"EVOLUTION ENGINE — {species.name}")
    curiosity = w.get("curiosity_weight", 0.15)
    utility = w.get("utility_weight", 0.35)
    evolve_chance = 0.3 + 0.4 * curiosity + 0.2 * utility
    evolve_chance = min(0.95, evolve_chance)
    roll = random.random()
    if roll < evolve_chance and species.tier < 5:
        species.tier += 1
        species.traits.append("Evolved form")
        print(f"{species.name} has evolved to Tier {species.tier} (roll={roll:.3f}, chance={evolve_chance:.3f}).")
    else:
        print(f"{species.name} did NOT evolve (roll={roll:.3f}, chance={evolve_chance:.3f}).")
    print()

# ============================================================
# LIVE DATA LAYERS
# ============================================================

class LiveSystemTelemetry:
    def sample(self) -> Dict[str, float]:
        if psutil is None:
            return {
                "cpu": random.uniform(0, 1),
                "ram": random.uniform(0, 1),
                "disk": random.uniform(0, 1),
                "net": random.uniform(0, 1),
            }
        try:
            cpu = psutil.cpu_percent(interval=0.1) / 100.0
            ram = psutil.virtual_memory().percent / 100.0
            disk = psutil.disk_usage(os.getcwd()).percent / 100.0
            net_io = psutil.net_io_counters()
            total = net_io.bytes_sent + net_io.bytes_recv
            net = min(1.0, total / (1024 * 1024 * 1024))
            return {"cpu": cpu, "ram": ram, "disk": disk, "net": net}
        except Exception:
            return {
                "cpu": random.uniform(0, 1),
                "ram": random.uniform(0, 1),
                "disk": random.uniform(0, 1),
                "net": random.uniform(0, 1),
            }

class LiveInternetData:
    def sample(self) -> Dict[str, float]:
        if requests is None:
            return {
                "news_conflict": random.uniform(0, 1),
                "market_vol": random.uniform(0, 1),
            }
        try:
            r = requests.get("https://httpbin.org/get", timeout=2)
            latency = r.elapsed.total_seconds()
            news_conflict = min(1.0, latency)
            market_vol = random.uniform(0, 1)
            return {"news_conflict": news_conflict, "market_vol": market_vol}
        except Exception:
            return {
                "news_conflict": random.uniform(0, 1),
                "market_vol": random.uniform(0, 1),
            }

class LiveNetworkData:
    def sample(self) -> Dict[str, float]:
        try:
            host = "8.8.8.8"
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            start = time.time()
            try:
                s.connect((host, 53))
                latency = time.time() - start
                s.close()
                reach = 1.0
            except Exception:
                latency = 1.0
                reach = 0.0
            return {
                "latency": min(1.0, latency),
                "reachability": reach,
            }
        except Exception:
            return {
                "latency": random.uniform(0, 1),
                "reachability": random.choice([0.0, 1.0]),
            }

class _FSHandler(FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        self.event_count = 0
    def on_any_event(self, event):
        self.event_count += 1

class LiveFileSystemData:
    def __init__(self, path: str = "."):
        self.path = path
        self.observer = None
        self.handler = None
        self.enabled = False
        if Observer is not None:
            try:
                self.handler = _FSHandler()
                self.observer = Observer()
                self.observer.schedule(self.handler, self.path, recursive=True)
                self.observer.start()
                self.enabled = True
            except Exception:
                self.enabled = False
    def sample(self) -> Dict[str, float]:
        if not self.enabled or self.handler is None:
            return {"fs_activity": random.uniform(0, 1)}
        count = self.handler.event_count
        self.handler.event_count = 0
        return {"fs_activity": min(1.0, count / 50.0)}
    def stop(self):
        if self.observer is not None:
            self.observer.stop()
            self.observer.join()

# ============================================================
# MQTT / IoT SMART SYSTEM (v2)
# ============================================================

@dataclass
class PeerEntry:
    ip_address: str
    node_id: str
    status: str
    last_seen: float

class MQTTBrokerWrapper:
    def __init__(self, host: Optional[str] = "localhost", port: int = 1883, client_id: str = "swarm-core"):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.client = None
        self.connected = False
        self.stub_mode = False
        self._callbacks: Dict[str, Callable[[str, bytes], None]] = {}

        if self.host is None:
            print("[MQTT] Host is None, running in stub mode.")
            self.stub_mode = True
            return

        if paho_mqtt is None:
            print("[MQTT] paho-mqtt not available, running in stub mode.")
            self.stub_mode = True
            return

        try:
            self.client = paho_mqtt.Client(client_id=self.client_id)
            self.client.on_connect = self._on_connect
            self.client.on_message = self._on_message
            self.client.connect(self.host, self.port, 10)
            self.connected = True
            print(f"[MQTT] Connected to broker at {self.host}:{self.port}")
            self.thread = threading.Thread(target=self.client.loop_forever, daemon=True)
            self.thread.start()
        except Exception as e:
            print(f"[MQTT] Broker not reachable ({self.host}:{self.port}), switching to stub mode. Error: {e}")
            self.client = None
            self.stub_mode = True

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            print(f"[MQTT] on_connect: rc={rc} (success)")
        else:
            print(f"[MQTT] on_connect: rc={rc} (error), switching to stub mode.")
            self.connected = False
            self.stub_mode = True

    def _on_message(self, client, userdata, msg):
        cb = self._callbacks.get(msg.topic)
        if cb:
            cb(msg.topic, msg.payload)
        else:
            print(f"[MQTT] Message topic={msg.topic}, payload={msg.payload}")

    def publish(self, topic: str, message: str):
        if self.stub_mode or not self.client or not self.connected:
            print(f"[MQTT-STUB] PUBLISH topic={topic}, message={message}")
            return
        try:
            self.client.publish(topic, message)
        except Exception as e:
            print(f"[MQTT] Publish failed, switching to stub mode. Error: {e}")
            self.stub_mode = True

    def subscribe(self, topic: str, callback: Optional[Callable[[str, bytes], None]] = None):
        if self.stub_mode or not self.client or not self.connected:
            print(f"[MQTT-STUB] SUBSCRIBE topic={topic}")
            return
        if callback:
            self._callbacks[topic] = callback
        try:
            self.client.subscribe(topic)
        except Exception as e:
            print(f"[MQTT] Subscribe failed, switching to stub mode. Error: {e}")
            self.stub_mode = True

class EventProcessingEngine:
    def __init__(self):
        self.rules: List[str] = []
        self.triggers: List[str] = []
        self.filters: List[str] = []
    def process_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        print(f"[EventEngine] Processing event: {event}")
        event["processed"] = True
        return event

class PeerDiscovery:
    def __init__(self):
        self.peers: Dict[str, PeerEntry] = {}
    def update_peer(self, ip: str, node_id: str, status: str):
        self.peers[node_id] = PeerEntry(ip, node_id, status, time.time())
    def peer_table(self) -> List[PeerEntry]:
        return list(self.peers.values())

class LocalAPIInterface:
    def __init__(self):
        self.rest_enabled = True
        self.ws_enabled = True
        self.grpc_enabled = False
    def rest_call(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        print(f"[REST API] path={path}, payload={payload}")
        return {"status": "ok", "path": path, "echo": payload}
    def ws_message(self, channel: str, payload: Dict[str, Any]):
        print(f"[WebSocket API] channel={channel}, payload={payload}")
    def grpc_call(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        print(f"[gRPC API] method={method}, payload={payload} (stub)")
        return {"status": "stub", "method": method}

class DataStorage:
    def __init__(self):
        self.db: List[Dict[str, Any]] = []
        self.alerts: List[str] = []
    def store(self, record: Dict[str, Any]):
        self.db.append(record)
    def add_alert(self, alert: str):
        self.alerts.append(alert)
        print(f"[Alerts API] ALERT: {alert}")

class SecurityAuth:
    def __init__(self):
        self.users: Dict[str, str] = {"admin": "token-admin"}
    def authenticate(self, user: str, token: str) -> bool:
        valid = self.users.get(user) == token
        print(f"[Security] Auth user={user}, valid={valid}")
        return valid
    def log(self, msg: str):
        print(f"[Security Log] {msg}")

class CloudServices:
    def __init__(self, ml_backend: "MLBackend"):
        self.backup_enabled = True
        self.cloud_ai_enabled = True
        self.ml_backend = ml_backend
    def sync(self, payload: Dict[str, Any]):
        print(f"[Cloud Sync] payload={payload}")
    def cloud_ai_infer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        print(f"[Cloud AI] infer payload={payload}")
        return self.ml_backend.infer_cloud(payload)

class AdminMonitoring:
    def __init__(self):
        self.logs: List[str] = []
    def log(self, msg: str):
        self.logs.append(msg)
        print(f"[Admin] {msg}")

class FirmwareUpdates:
    def ota_update(self, node_id: str):
        print(f"[OTA] Updating firmware on node {node_id} (stub)")
    def diagnostics(self, node_id: str):
        print(f"[Diagnostics] Running diagnostics on node {node_id} (stub)")

class IoTSystem:
    def __init__(self, ml_backend: "MLBackend", mqtt_host: Optional[str] = "localhost", mqtt_port: int = 1883):
        self.mqtt = MQTTBrokerWrapper(host=mqtt_host, port=mqtt_port, client_id="iot-system")
        self.event_engine = EventProcessingEngine()
        self.peer_discovery = PeerDiscovery()
        self.api = LocalAPIInterface()
        self.storage = DataStorage()
        self.security = SecurityAuth()
        self.cloud = CloudServices(ml_backend)
        self.admin = AdminMonitoring()
        self.firmware = FirmwareUpdates()
        self.mqtt.subscribe("iot/commands", self._on_command)
    def _on_command(self, topic: str, payload: bytes):
        try:
            cmd = json.loads(payload.decode("utf-8"))
        except Exception:
            cmd = {"raw": payload.decode("utf-8", errors="ignore")}
        print(f"[IoTSystem] Received command on {topic}: {cmd}")
    def ingest_iot_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        self.mqtt.publish("iot/events", json.dumps(event))
        processed = self.event_engine.process_event(event)
        self.storage.store(processed)
        if processed.get("anomaly", False):
            self.storage.add_alert(f"Anomaly detected: {processed}")
        self.cloud.sync(processed)
        ai_result = self.cloud.cloud_ai_infer(processed)
        processed["cloud_ai_score"] = ai_result.get("ai_score", 0.0)
        return processed
    def sample_iot_state(self) -> Dict[str, float]:
        peers = len(self.peer_discovery.peer_table())
        alerts = len(self.storage.alerts)
        ai_avg = random.uniform(0, 1)
        return {
            "peer_count": min(1.0, peers / 50.0),
            "alert_density": min(1.0, alerts / 50.0),
            "cloud_ai_activity": ai_avg,
        }

# ============================================================
# ML BACKEND (HYBRID)
# ============================================================

class MLBackend:
    def __init__(self, mode: str = "stub", onnx_path: Optional[str] = None, sklearn_path: Optional[str] = None):
        self.mode = mode
        self.onnx_session = None
        self.sklearn_model = None
        if mode == "onnx" and onnxruntime is not None and onnx_path:
            try:
                self.onnx_session = onnxruntime.InferenceSession(onnx_path)
                print(f"[MLBackend] ONNX session loaded from {onnx_path}")
            except Exception as e:
                print(f"[MLBackend] Failed to load ONNX model: {e}")
                self.mode = "stub"
        if mode == "sklearn" and joblib is not None and sklearn_path:
            try:
                self.sklearn_model = joblib.load(sklearn_path)
                print(f"[MLBackend] scikit-learn model loaded from {sklearn_path}")
            except Exception as e:
                print(f"[MLBackend] Failed to load sklearn model: {e}")
                self.mode = "stub"

    def infer_cloud(self, payload: Dict[str, Any]) -> Dict[str, float]:
        if self.mode == "onnx" and self.onnx_session is not None:
            try:
                import numpy as np
                x = np.array([[payload.get("value", 0.0)]], dtype=np.float32)
                inputs = {self.onnx_session.get_inputs()[0].name: x}
                out = self.onnx_session.run(None, inputs)[0]
                score = float(out[0][0])
                return {"ai_score": max(0.0, min(1.0, score))}
            except Exception as e:
                print(f"[MLBackend] ONNX inference error: {e}")
                return {"ai_score": random.uniform(0, 1)}

        elif self.mode == "sklearn" and self.sklearn_model is not None:
            try:
                import numpy as np
                x = np.array([[payload.get("value", 0.0)]])
                if hasattr(self.sklearn_model, "predict_proba"):
                    score = float(self.sklearn_model.predict_proba(x)[0][1])
                else:
                    score = float(self.sklearn_model.predict(x)[0])
                return {"ai_score": max(0.0, min(1.0, score))}
            except Exception as e:
                print(f"[MLBackend] scikit-learn inference error: {e}")
                return {"ai_score": random.uniform(0, 1)}

        elif self.mode == "azure":
            print("[MLBackend] Azure ML inference (stub)")
            return {"ai_score": random.uniform(0, 1)}

        elif self.mode == "sagemaker":
            print("[MLBackend] SageMaker inference (stub)")
            return {"ai_score": random.uniform(0, 1)}

        else:
            return {"ai_score": random.uniform(0, 1)}

# ============================================================
# SWARM INTELLIGENCE CORE ARCHITECTURE (v2)
# ============================================================

@dataclass
class SwarmNode:
    name: str
    id: int
    status: str = "online"
    load: float = 0.0

@dataclass
class BrainState:
    cycle: int
    champion_name: Optional[str]
    weights: Dict[str, float]
    notes: str = ""

@dataclass
class PredictionModel:
    name: str
    model_type: str
    version: str

class UnifiedConfigLoader:
    def __init__(self, path: str = "swarm_config.json"):
        self.path = path
        self.config: Dict[str, Any] = {}
    def _default_config(self) -> Dict[str, Any]:
        return {
            "autonomous_cycles": 5,
            "log_battles": True,
            "evolve_winner_each_cycle": True,
            "ml_backend_mode": "stub",
            "prometheus_port": 8000,
            "rest_api_port": 5000,
            "mqtt_host": "localhost",
            "mqtt_port": 1883,
        }
    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
                print(f"[Config] Loaded existing config from {self.path}")
            except Exception as e:
                print(f"[Config] Failed to load config, using defaults. Error: {e}")
                self.config = self._default_config()
                self.save()
        else:
            self.config = self._default_config()
            self.save()
            print(f"[Config] Created default config at {self.path}")
        return self.config
    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
            print(f"[Config] Saved config to {self.path}")
        except Exception as e:
            print(f"[Config] Failed to save config: {e}")

class PluginSandbox:
    def __init__(self):
        self.plugins: List[str] = []
    def register_plugin(self, name: str):
        self.plugins.append(name)
    def run_plugins(self):
        for p in self.plugins:
            print(f"[PluginSandbox] Running plugin: {p} (stub)")

class BrainStateSnapshot:
    def __init__(self, path: str = "swarm_brain_state.json"):
        self.path = path
    def save_state(self, state: BrainState):
        data = {
            "cycle": state.cycle,
            "champion_name": state.champion_name,
            "weights": state.weights,
            "notes": state.notes,
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[BrainState] Saved state at cycle {state.cycle} (champion={state.champion_name})")
    def load_state(self) -> Optional[BrainState]:
        if not os.path.exists(self.path):
            return None
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[BrainState] Loaded state at cycle {data.get('cycle')}")
        return BrainState(
            cycle=data.get("cycle", 0),
            champion_name=data.get("champion_name"),
            weights=data.get("weights", dict(weights)),
            notes=data.get("notes", ""),
        )

class DataIngestion:
    def __init__(self, ml_backend: MLBackend, mqtt_host: str, mqtt_port: int):
        self.sys = LiveSystemTelemetry()
        self.internet = LiveInternetData()
        self.net = LiveNetworkData()
        self.fs = LiveFileSystemData()
        self.iot = IoTSystem(ml_backend, mqtt_host=mqtt_host, mqtt_port=mqtt_port)
    def ingest_all(self) -> Dict[str, Dict[str, float]]:
        event = {"device_id": "sensor-1", "value": random.uniform(0, 1), "anomaly": random.random() > 0.8}
        self.iot.ingest_iot_event(event)
        return {
            "system": self.sys.sample(),
            "internet": self.internet.sample(),
            "network": self.net.sample(),
            "filesystem": self.fs.sample(),
            "iot": self.iot.sample_iot_state(),
        }
    def stop(self):
        self.fs.stop()

class ModelRepository:
    def __init__(self):
        self.models: List[PredictionModel] = [
            PredictionModel("GalaxyRiskNet", "Neural Network", "1.0"),
            PredictionModel("TierDriftTS", "Time Series", "1.0"),
            PredictionModel("WarRL-Agent", "Reinforcement Learning", "0.1"),
        ]
    def list_models(self):
        return self.models

class SwarmLearning:
    def risk_analysis(self, data: Dict[str, Dict[str, float]], w: Dict[str, float]) -> float:
        sys_cpu = data["system"]["cpu"]
        net_reach = data["network"]["reachability"]
        internet_conflict = data["internet"]["news_conflict"]
        iot_alert = data["iot"]["alert_density"]
        base = (sys_cpu + net_reach + internet_conflict + iot_alert) / 4.0
        impact = w.get("impact_weight", 0.25)
        return base * (1.0 + 0.5 * impact)
    def anomaly_detection(self, data: Dict[str, Dict[str, float]], w: Dict[str, float]) -> float:
        fs_activity = data["filesystem"]["fs_activity"]
        net_latency = data["network"]["latency"]
        iot_peers = data["iot"]["peer_count"]
        base = fs_activity + max(0.0, 1.0 - net_latency) + iot_peers
        curiosity = w.get("curiosity_weight", 0.15)
        return base * (1.0 + 0.5 * curiosity)
    def forecasting(self, data: Dict[str, Dict[str, float]], w: Dict[str, float]) -> float:
        ram = data["system"]["ram"]
        disk = data["system"]["disk"]
        market_vol = data["internet"]["market_vol"]
        iot_cloud = data["iot"]["cloud_ai_activity"]
        base = (1.0 - ram + 1.0 - disk + (1.0 - market_vol) + iot_cloud) / 4.0
        utility = w.get("utility_weight", 0.35)
        return base * (1.0 + 0.5 * utility)

class SwarmCoordination:
    def task_allocation(self, nodes: List[SwarmNode], tasks: int, w: Dict[str, float]) -> Dict[int, int]:
        allocation: Dict[int, int] = {}
        if not nodes:
            return allocation
        novelty = w.get("novelty_weight", 0.25)
        bias = int(max(1, round(tasks * novelty)))
        for i in range(tasks):
            node = nodes[(i + bias) % len(nodes)]
            allocation[node.id] = allocation.get(node.id, 0) + 1
        return allocation
    def consensus_engine(self, scores: List[float], w: Dict[str, float]) -> float:
        if not scores:
            return 0.0
        curiosity = w.get("curiosity_weight", 0.15)
        base = sum(scores) / len(scores)
        return base * (1.0 + 0.3 * curiosity)

class SecureNetwork:
    def __init__(self):
        self.encrypted = True
        self.auto_scaling = True
    def status(self) -> str:
        return f"Encrypted={self.encrypted}, Auto-Scaling={self.auto_scaling}"

class VisualizationDashboard:
    def render(self, cycle: int, risk: float, anomaly: float, forecast: float,
               champion: Optional[Species], w: Dict[str, float]):
        print_header(f"VISUALIZATION DASHBOARD — CYCLE {cycle}")
        print(f"Risk Index:       {risk:.3f}")
        print(f"Anomaly Score:    {anomaly:.3f}")
        print(f"Forecast Index:   {forecast:.3f}")
        if champion:
            print(f"Current Champion: {champion.name} (Tier {champion.tier}, {champion.threat_level})")
        else:
            print("Current Champion: None")
        print("Strategy Weights:")
        for k, v in w.items():
            print(f"  {k}: {v:.3f}")
        print()

# ============================================================
# RAFT / CLUSTER / QUEEN (v2)
# ============================================================

@dataclass
class RaftLogEntry:
    term: int
    command: str

class RaftConsensus:
    def __init__(self, node_id: str, peers: List[str]):
        self.node_id = node_id
        self.peers = peers
        self.current_term = 0
        self.voted_for: Optional[str] = None
        self.log: List[RaftLogEntry] = []
        self.commit_index = 0
        self.last_applied = 0
        self.leader_id: Optional[str] = None
        self.state = "follower"
        self.election_timeout = random.uniform(1.5, 3.0)
        self.last_heartbeat = time.time()
        self.lock = threading.Lock()
        self._election_thread = threading.Thread(target=self._run_election_loop, daemon=True)
        self._election_thread.start()
    def _run_election_loop(self):
        while True:
            time.sleep(0.2)
            with self.lock:
                now = time.time()
                if self.state != "leader" and (now - self.last_heartbeat) > self.election_timeout:
                    self._start_election()
    def _start_election(self):
        self.state = "candidate"
        self.current_term += 1
        self.voted_for = self.node_id
        votes = 1
        print(f"[RAFT {self.node_id}] Starting election for term {self.current_term}")
        for p in self.peers:
            if random.random() > 0.3:
                votes += 1
        if votes > (len(self.peers) + 1) // 2:
            self.state = "leader"
            self.leader_id = self.node_id
            self.last_heartbeat = time.time()
            print(f"[RAFT {self.node_id}] Became leader for term {self.current_term} with {votes} votes")
        else:
            self.state = "follower"
            print(f"[RAFT {self.node_id}] Election failed, back to follower")
    def receive_heartbeat(self, leader_id: str, term: int):
        with self.lock:
            if term >= self.current_term:
                self.current_term = term
                self.leader_id = leader_id
                self.state = "follower"
                self.last_heartbeat = time.time()
                print(f"[RAFT {self.node_id}] Heartbeat from leader {leader_id}, term={term}")
    def send_heartbeat(self):
        with self.lock:
            if self.state == "leader":
                self.last_heartbeat = time.time()
                print(f"[RAFT {self.node_id}] Sending heartbeat, term={self.current_term}")
    def append_entries(self, entries: List[RaftLogEntry]):
        with self.lock:
            if self.state != "leader":
                return
            self.log.extend(entries)
            self.commit_index = len(self.log)
            print(f"[RAFT {self.node_id}] AppendEntries, new_log_len={len(self.log)}")

class TelemetryFusion:
    def fuse(self, sys_data: Dict[str, float]) -> Dict[str, float]:
        fused = {
            "cpu": sys_data.get("cpu", 0.0),
            "gpu": random.uniform(0, 1),
            "disk": sys_data.get("disk", 0.0),
            "net": sys_data.get("net", 0.0),
        }
        print(f"[TelemetryFusion] Fused: {fused}")
        return fused

class JobQueue:
    def __init__(self):
        self.queue: "Queue[Dict[str, Any]]" = Queue()
    def submit(self, job_type: str, payload: Dict[str, Any]):
        job = {"type": job_type, "payload": payload}
        self.queue.put(job)
        print(f"[JobQueue] Submitted job: {job_type}")
    def fetch(self, timeout: float = 0.1) -> Optional[Dict[str, Any]]:
        try:
            return self.queue.get(timeout=timeout)
        except Empty:
            return None

class JobExecutor:
    def __init__(self, max_workers: Optional[int] = None):
        from concurrent.futures import ThreadPoolExecutor
        if max_workers is None:
            max_workers = max(2, multiprocessing.cpu_count() // 2)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
    @staticmethod
    def _execute_job(job: Dict[str, Any], agent: "AgentNode"):
        agent.run_job(job)
    def execute_for_agents(self, jobs: List[Dict[str, Any]], agents: List["AgentNode"]):
        futures = []
        for job, agent in zip(jobs, agents):
            futures.append(self.executor.submit(JobExecutor._execute_job, job, agent))
        for f in futures:
            f.result()

class QueenMetrics:
    def __init__(self):
        self.metrics: Dict[str, float] = {}
        if prometheus_client is not None:
            PROM_GAUGES.setdefault("queen_anomaly_score", Gauge("queen_anomaly_score", "Queen anomaly score"))
            PROM_GAUGES.setdefault("queen_policy_score", Gauge("queen_policy_score", "Queen policy score"))
            PROM_GAUGES.setdefault("queen_cycle", Gauge("queen_cycle", "Current cycle"))
            PROM_COUNTERS.setdefault("queen_jobs_dispatched", Counter("queen_jobs_dispatched", "Jobs dispatched by queen"))
    def record(self, key: str, value: float):
        self.metrics[key] = value
        print(f"[QueenMetrics] {key}={value:.3f}")
        if prometheus_client is not None:
            if key == "anomaly_score":
                PROM_GAUGES["queen_anomaly_score"].set(value)
            elif key == "policy_score":
                PROM_GAUGES["queen_policy_score"].set(value)
            elif key == "cycle":
                PROM_GAUGES["queen_cycle"].set(value)
    def inc_jobs(self, count: int):
        if prometheus_client is not None:
            PROM_COUNTERS["queen_jobs_dispatched"].inc(count)

class AgentMetrics:
    def __init__(self, agent_id: int):
        self.agent_id = agent_id
        self.metrics: Dict[str, float] = {}
        if prometheus_client is not None:
            PROM_GAUGES.setdefault(f"agent_{agent_id}_load", Gauge(f"agent_{agent_id}_load", "Agent load"))
    def record(self, key: str, value: float):
        self.metrics[key] = value
        print(f"[AgentMetrics {self.agent_id}] {key}={value:.3f}")
        if prometheus_client is not None and key == "load":
            PROM_GAUGES[f"agent_{self.agent_id}_load"].set(value)

class ThreatMatrix:
    def compute(self, species: Species) -> float:
        base = species.tier
        mult = THREAT_MULTIPLIER.get(species.threat_level, 3.0)
        score = base * mult * random.uniform(0.8, 1.2)
        print(f"[ThreatMatrix] {species.name} threat={score:.2f}")
        return score

class BorgGasIndex:
    def compute(self, agent_load: float, fs_activity: float) -> float:
        idx = (agent_load + fs_activity) / 2.0
        print(f"[BorgGasIndex] index={idx:.3f}")
        return idx

class PrometheusExporters:
    def export(self, metrics: Dict[str, float]):
        print(f"[Prometheus] Exporting metrics: {metrics}")

@dataclass
class AgentNode:
    id: int
    name: str
    load: float = 0.0
    metrics: AgentMetrics = field(init=False)
    threat_matrix: ThreatMatrix = field(default_factory=ThreatMatrix)
    borg_gas_index: BorgGasIndex = field(default_factory=BorgGasIndex)
    exporters: PrometheusExporters = field(default_factory=PrometheusExporters)
    def __post_init__(self):
        self.metrics = AgentMetrics(self.id)
    def heartbeat(self):
        self.load = random.uniform(0, 1)
        self.metrics.record("load", self.load)
        print(f"[Agent {self.id}] Heartbeat, load={self.load:.3f}")
    def run_job(self, job: Dict[str, Any]):
        self.heartbeat()
        jtype = job["type"]
        print(f"[Agent {self.id}] Running job type={jtype}, payload={job['payload']}")
        time.sleep(0.01)

class MLTraining:
    def train(self, data: Dict[str, Dict[str, float]]):
        print(f"[MLTraining] Training on fused data (stub)")

class MLInference:
    def __init__(self, ml_backend: MLBackend):
        self.ml_backend = ml_backend
    def infer(self, data: Dict[str, Dict[str, float]]) -> Dict[str, float]:
        result = {
            "anomaly_score": random.uniform(0, 1),
            "policy_score": random.uniform(0, 1),
        }
        print(f"[MLInference] Inference result: {result}")
        return result

class PurgeCycle:
    def run(self):
        print("[PurgeCycle] Snapshot + cleanup (stub)")

class CacheSync:
    def broadcast(self):
        print("[CacheSync] Broadcasting cache state (stub)")
    def sync(self):
        print("[CacheSync] Syncing cache from cluster (stub)")

class QueenNodeController:
    def __init__(self, ml_backend: MLBackend):
        self.raft = RaftConsensus(node_id="queen", peers=["agent-1", "agent-2", "agent-3"])
        self.telemetry_fusion = TelemetryFusion()
        self.job_queue = JobQueue()
        self.job_executor = JobExecutor()
        self.ml_training = MLTraining()
        self.ml_inference = MLInference(ml_backend)
        self.purge_cycle = PurgeCycle()
        self.cache_sync = CacheSync()
        self.metrics = QueenMetrics()
    def run_cycle(self, sys_data: Dict[str, float], cluster_agents: List[AgentNode]):
        self.raft.send_heartbeat()
        fused = self.telemetry_fusion.fuse(sys_data)
        self.ml_training.train({"system": fused})
        infer = self.ml_inference.infer({"system": fused})
        self.metrics.record("anomaly_score", infer["anomaly_score"])
        self.metrics.record("policy_score", infer["policy_score"])
        jobs: List[Dict[str, Any]] = []
        for _ in range(len(cluster_agents)):
            payload = {"level": random.randint(1, 5)}
            self.job_queue.submit("stress", payload)
            jobs.append({"type": "stress", "payload": payload})
        self.metrics.inc_jobs(len(jobs))
        self.job_executor.execute_for_agents(jobs, cluster_agents)
        self.cache_sync.broadcast()
        self.cache_sync.sync()
        self.purge_cycle.run()

class ClusterOrchestrator:
    def __init__(self, ml_backend: MLBackend):
        self.queen = QueenNodeController(ml_backend)
        self.agents: List[AgentNode] = [
            AgentNode(1, "Agent-1"),
            AgentNode(2, "Agent-2"),
            AgentNode(3, "Agent-3"),
        ]
    def run_cluster_cycle(self, sys_data: Dict[str, float]):
        print_header("CLUSTER ORCHESTRATOR — QUEEN + AGENTS")
        for a in self.agents:
            a.heartbeat()
        self.queen.run_cycle(sys_data, self.agents)
    def distributed_battle_bias(self, species: Species) -> float:
        tm = self.agents[0].threat_matrix.compute(species)
        bg = self.agents[0].borg_gas_index.compute(self.agents[0].load, random.uniform(0, 1))
        return (tm + bg * 10.0) / 100.0

# ============================================================
# SWARM INTELLIGENCE CORE (INTEGRATED WITH CLUSTER + IoT)
# ============================================================

class SwarmIntelligenceCore:
    def __init__(self):
        self.config_loader = UnifiedConfigLoader()
        self.config = self.config_loader.load()
        self.ml_backend = MLBackend(mode=self.config.get("ml_backend_mode", "stub"))
        self.plugin_sandbox = PluginSandbox()
        self.brain_snapshot = BrainStateSnapshot()
        self.data_ingestion = DataIngestion(
            self.ml_backend,
            mqtt_host=self.config.get("mqtt_host", "localhost"),
            mqtt_port=int(self.config.get("mqtt_port", 1883)),
        )
        self.model_repo = ModelRepository()
        self.learning = SwarmLearning()
        self.coordination = SwarmCoordination()
        self.secure_network = SecureNetwork()
        self.dashboard = VisualizationDashboard()
        self.nodes: List[SwarmNode] = [
            SwarmNode("Node A", 1),
            SwarmNode("Node B", 2),
            SwarmNode("Node C", 3),
        ]
        self.cluster = ClusterOrchestrator(self.ml_backend)
        self.cycle = 0
        self.current_champion: Optional[Species] = None
        self.plugin_sandbox.register_plugin("ExampleRiskPlugin")
        self.weights = dict(weights)

    def auto_discovery(self):
        print("[SwarmDiscovery] Auto-discovery: Nodes A/B/C online.")
        for n in self.nodes:
            n.load = random.uniform(0, 0.5)

    def compute_performance_metrics(self, risk: float, anomaly: float, forecast: float,
                                    data: Dict[str, Dict[str, float]]) -> Dict[str, float]:
        completion_rate = max(0.0, min(1.0, forecast))
        avg_objective = (risk + anomaly + forecast) / 3.0
        feed_entropy = (data["system"]["cpu"] + data["system"]["ram"] +
                        data["system"]["disk"] + data["system"]["net"]) / 4.0
        sys_entropy = (data["filesystem"]["fs_activity"] +
                       data["network"]["latency"] +
                       data["iot"]["alert_density"]) / 3.0
        return {
            "completion_rate": completion_rate,
            "avg_objective": avg_objective,
            "feed_entropy": feed_entropy,
            "sys_entropy": sys_entropy,
        }

    def run_autonomous_cycle(self):
        self.cycle += 1
        print_header(f"SWARM INTELLIGENCE CORE — CYCLE {self.cycle}")
        self.auto_discovery()
        data = self.data_ingestion.ingest_all()
        self.cluster.run_cluster_cycle(data["system"])
        risk = self.learning.risk_analysis(data, self.weights)
        anomaly = self.learning.anomaly_detection(data, self.weights)
        forecast = self.learning.forecasting(data, self.weights)
        tasks = random.randint(3, 8)
        allocation = self.coordination.task_allocation(self.nodes, tasks, self.weights)
        consensus = self.coordination.consensus_engine([risk, anomaly, forecast], self.weights)
        print(f"[SwarmCoordination] Task allocation: {allocation}")
        print(f"[SwarmCoordination] Consensus score: {consensus:.3f}")
        print(f"[SecureNetwork] {self.secure_network.status()}")
        print(f"[ModelRepository] Models: {[m.name for m in self.model_repo.list_models()]}")
        self.plugin_sandbox.run_plugins()
        champion = self.run_war_tournament()
        self.current_champion = champion
        perf = self.compute_performance_metrics(risk, anomaly, forecast, data)
        self.weights = adjust_weights(perf)
        self.dashboard.render(self.cycle, risk, anomaly, forecast, champion, self.weights)
        state = BrainState(
            cycle=self.cycle,
            champion_name=champion.name if champion else None,
            weights=self.weights,
            notes="Autonomous war cycle completed.",
        )
        self.brain_snapshot.save_state(state)
        if prometheus_client is not None:
            PROM_GAUGES["queen_cycle"].set(self.cycle)
        if self.config.get("evolve_winner_each_cycle", True) and champion:
            evolve_species(champion, self.weights)

    def run_war_tournament(self) -> Species:
        contenders = SPECIES[:]
        random.shuffle(contenders)
        novelty = self.weights.get("novelty_weight", 0.25)
        curiosity = self.weights.get("curiosity_weight", 0.15)
        utility = self.weights.get("utility_weight", 0.35)
        if novelty > 0.5:
            contenders.sort(key=lambda s: s.tier)
        elif utility > 0.5:
            contenders.sort(key=lambda s: -s.tier)
        else:
            random.shuffle(contenders)
        round_num = 1
        while len(contenders) > 1:
            print_header(f"WAR TOURNAMENT — ROUND {round_num} ({len(contenders)} contenders)")
            next_round: List[Species] = []
            if len(contenders) % 2 == 1:
                bye = contenders.pop()
                print(f"  BYE: {bye.name} advances automatically.\n")
                next_round.append(bye)
            for i in range(0, len(contenders), 2):
                a = contenders[i]
                b = contenders[i + 1]
                bias_a = self.cluster.distributed_battle_bias(a)
                bias_b = self.cluster.distributed_battle_bias(b)
                local_weights = dict(self.weights)
                local_weights["impact_weight"] *= (1.0 + bias_a - bias_b)
                winner = battle(a, b, local_weights)
                next_round.append(winner)
            contenders = next_round
            round_num += 1
        champion = contenders[0]
        print_header("WAR RESULT — CYCLE CHAMPION")
        print(f"Champion: {champion.name} (Tier {champion.tier}, {champion.threat_level})\n")
        return champion

    def run_autonomous(self, cycles: Optional[int] = None):
        if cycles is None:
            cycles = int(self.config.get("autonomous_cycles", 5))
        print_header(f"FULLY AUTONOMOUS MODE — {cycles} CYCLES")
        prev_state = self.brain_snapshot.load_state()
        if prev_state:
            self.cycle = prev_state.cycle
            if prev_state.champion_name:
                champ = get_by_name(prev_state.champion_name)
                if champ:
                    self.current_champion = champ
            if prev_state.weights:
                self.weights = prev_state.weights
        for _ in range(cycles):
            self.run_autonomous_cycle()
        print_header("AUTONOMOUS RUN COMPLETE")
        if self.current_champion:
            print(f"Final Champion after {self.cycle} cycles: {self.current_champion.name}")
        else:
            print("No champion determined.")
        print()
        self.data_ingestion.stop()

# ============================================================
# LLM TELEMETRY + GENERATION + RPC + CLI
# ============================================================

def get_system_telemetry() -> Dict[str, float]:
    if psutil is None:
        return {"cpu": random.uniform(0, 1), "ram": random.uniform(0, 1)}
    try:
        cpu = psutil.cpu_percent(interval=0.05) / 100.0
        ram = psutil.virtual_memory().percent / 100.0
        return {"cpu": cpu, "ram": ram}
    except Exception:
        return {"cpu": random.uniform(0, 1), "ram": random.uniform(0, 1)}

def train_policy_net_step(sys_tel: Dict[str, float], latency_ms: float):
    # stub: here you would update a policy network based on latency + telemetry
    pass

if torch is not None:
    @torch.inference_mode()
    def generate_text(prompt: str, max_new_tokens: int = 128) -> Tuple[str, dict]:
        load_model()
        EXECUTOR.reset_stats(clear_router_data=False)
        tok = CURRENT_TOKENIZER
        mdl = CURRENT_MODEL
        inputs = tok(prompt, return_tensors="pt")
        if isinstance(inputs, dict):
            for k in inputs:
                if isinstance(inputs[k], torch.Tensor):
                    inputs[k] = inputs[k].to(DEFAULT_DEVICE)
        t0 = time.time()
        if isinstance(mdl, TinyFallback):
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
        text = tok.decode(out_ids[0], skip_special_tokens=True)
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
else:
    def generate_text(prompt: str, max_new_tokens: int = 128) -> Tuple[str, dict]:
        return prompt, {"model_name": "no-torch", "is_fallback": True, "latency_ms": 0.0}

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
                    print(f"[Node] RPC from {addr}, tokens={max_new_tokens}")
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
    while True:
        conn, addr = s.accept()
        t = threading.Thread(target=handle_rpc_client, args=(conn, addr), daemon=True)
        t.start()

def run_local_cli():
    print("[Node] Local CLI. Type 'quit' to exit.")
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
# REST API + PROMETHEUS
# ============================================================

GLOBAL_CORE: Optional[SwarmIntelligenceCore] = None
REST_APP: Optional[Any] = None

def start_prometheus_server(port: int):
    if prometheus_client is None:
        print("[Prometheus] prometheus_client not available, skipping metrics server.")
        return
    threading.Thread(target=start_http_server, args=(port,), daemon=True).start()
    print(f"[Prometheus] Metrics server started on port {port}")

def create_rest_app(core: SwarmIntelligenceCore, port: int) -> Optional[Any]:
    global REST_APP
    if Flask is None:
        print("[REST] Flask not available, REST API disabled.")
        return None
    app = Flask(__name__)

    @app.route("/status", methods=["GET"])
    def status():
        return jsonify({
            "cycle": core.cycle,
            "champion": core.current_champion.name if core.current_champion else None,
            "weights": core.weights,
            "secure_network": core.secure_network.status(),
        })

    @app.route("/species", methods=["GET"])
    def species_list():
        return jsonify([
            {
                "name": s.name,
                "universe": s.universe,
                "tier": s.tier,
                "classification": s.classification,
                "threat_level": s.threat_level,
                "traits": s.traits,
            }
            for s in SPECIES
        ])

    @app.route("/cluster", methods=["GET"])
    def cluster_status():
        agents = core.cluster.agents
        return jsonify([
            {
                "id": a.id,
                "name": a.name,
                "load": a.load,
                "metrics": a.metrics.metrics,
            }
            for a in agents
        ])

    @app.route("/run_cycle", methods=["POST"])
    def run_cycle():
        core.run_autonomous_cycle()
        return jsonify({"status": "ok", "cycle": core.cycle})

    @app.route("/run_autonomous", methods=["POST"])
    def run_auto():
        data = flask_request.get_json(silent=True) or {}
        cycles = int(data.get("cycles", 1))
        core.run_autonomous(cycles=cycles)
        return jsonify({"status": "ok", "cycle": core.cycle})

    @app.route("/config", methods=["GET", "POST"])
    def config_endpoint():
        if flask_request.method == "GET":
            return jsonify(core.config)
        else:
            data = flask_request.get_json(silent=True) or {}
            for k, v in data.items():
                core.config[k] = v
            core.config_loader.config = core.config
            core.config_loader.save()
            return jsonify({"status": "updated", "config": core.config})

    def run_app():
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

    threading.Thread(target=run_app, daemon=True).start()
    print(f"[REST] REST API server started on port {port}")
    REST_APP = app
    return app

# ============================================================
# MAIN ENTRYPOINT
# ============================================================

def main():
    global GLOBAL_CORE
    random.seed()
    core = SwarmIntelligenceCore()
    GLOBAL_CORE = core

    prom_port = int(core.config.get("prometheus_port", 8000))
    rest_port = int(core.config.get("rest_api_port", 5000))
    start_prometheus_server(prom_port)
    create_rest_app(core, rest_port)

    threading.Thread(target=rpc_server_loop, args=("0.0.0.0", 6000), daemon=True).start()
    load_model()

    core.run_autonomous()
    print("REST: /status /species /cluster /config /run_cycle /run_autonomous")
    print("RPC:  tcp://0.0.0.0:6000 (JSON lines: {\"prompt\":..., \"max_new_tokens\":...})")
    print("Press Ctrl+C to exit, or use run_local_cli() in another process.")

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
