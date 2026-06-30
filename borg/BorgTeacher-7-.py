#!/usr/bin/env python3
"""
ULTRABORG V3 GOD-GOVERNOR
Fully autonomous, ultra-aggressive, driver-level graphics governor.

Core capabilities:
- Hybrid DQN + PPO + Oracle tri-brain (Teacher / Shadow / Oracle)
- Realistic graphics tuning hooks (Windows registry + game config stubs)
- Rich sensor environment (CPU, RAM, GPU, VRAM, power, FPS, frametime variance)
- Dream Teacher synthetic imagination (offline training)
- Anomaly autoencoder with active responses
- Executioner-style watchdog (emergency overrides)
- Adaptive hyperparameters (epsilon, PPO clip)
- Forensic JSON logging (full trace)
"""

import os
import sys
import json
import time
import random
import threading
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple

# ---------------------------------------------------------------------
# Auto-loader for libraries
# ---------------------------------------------------------------------

def safe_import(name, pip_name=None):
    try:
        return __import__(name)
    except ImportError:
        print(f"[WARN] Missing library: {name}")
        if pip_name:
            print(f"       Install via: pip install {pip_name}")
        return None

psutil = safe_import("psutil", "psutil")
pynvml = safe_import("pynvml", "pynvml")
np = safe_import("numpy", "numpy")
torch = safe_import("torch", "torch")

if torch is None or np is None or psutil is None:
    print("[FATAL] Core libraries missing. Install psutil, numpy, torch.")
    sys.exit(1)

try:
    import winreg
except ImportError:
    winreg = None
    print("[WARN] winreg not available (non-Windows or limited environment)")

# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------

DEFAULT_CONFIG = {
    "modes": {
        "flow": {
            "target_fps": 90,
            "max_cpu": 0.85,
            "max_gpu": 0.90,
            "reward_weights": {
                "fps_stability": 1.0,
                "cpu_headroom": 0.7,
                "gpu_headroom": 0.7,
                "thermal_safety": 1.0,
                "frametime_stability": 0.8
            }
        },
        "deep_work": {
            "target_fps": 60,
            "max_cpu": 0.70,
            "max_gpu": 0.60,
            "reward_weights": {
                "fps_stability": 0.6,
                "cpu_headroom": 1.0,
                "gpu_headroom": 0.8,
                "thermal_safety": 1.0,
                "frametime_stability": 1.0
            }
        },
        "recovery": {
            "target_fps": 30,
            "max_cpu": 0.50,
            "max_gpu": 0.40,
            "reward_weights": {
                "fps_stability": 0.4,
                "cpu_headroom": 1.2,
                "gpu_headroom": 1.2,
                "thermal_safety": 1.5,
                "frametime_stability": 0.5
            }
        }
    },
    "rl": {
        "gamma": 0.99,
        "dqn_lr": 1e-4,
        "ppo_lr": 3e-4,
        "ppo_clip": 0.25,
        "entropy_coef": 0.02,
        "value_coef": 0.5,
        "batch_size": 64,
        "trajectory_len": 256,
        "epsilon_start": 0.3,
        "epsilon_end": 0.02,
        "epsilon_decay_steps": 80000
    },
    "watchdog": {
        "max_temp_c": 85.0,
        "emergency_temp_c": 92.0,
        "max_crash_count": 3,
        "check_interval_sec": 3.0
    },
    "graphics": {
        "registry_paths": {
            "nvidia_quality": r"Software\\NVIDIA Corporation\\Global\\Quality",
            "amd_quality": r"Software\\AMD\\Global\\Quality",
            "nvidia_perf": r"Software\\NVIDIA Corporation\\Global\\Perf",
            "amd_perf": r"Software\\AMD\\Global\\Perf"
        },
        "game_config_root": os.path.expanduser("~\\Documents\\MyGames"),
        "default_game_profile": {
            "resolution_scale_step": 0.05,
            "shadow_quality_step": 1,
            "postfx_step": 1,
            "lod_bias_step": 0.25
        }
    },
    "dream": {
        "enabled": True,
        "interval_steps": 4000,
        "episodes": 12,
        "length": 160
    },
    "anomaly": {
        "latent_dim": 4,
        "train_buffer_size": 6000,
        "train_interval_steps": 1500,
        "threshold": 0.02
    },
    "logging": {
        "json_log_path": "borg_v3_log.jsonl",
        "max_lines": 200000
    }
}

CONFIG_PATH = "borg_v3_config.json"


def load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            print(f"[CONFIG] Loaded external config from {CONFIG_PATH}")
            return cfg
        except Exception as e:
            print(f"[WARN] Failed to load {CONFIG_PATH}: {e}")
    print("[CONFIG] Using embedded default config")
    return DEFAULT_CONFIG


CONFIG = load_config()

# ---------------------------------------------------------------------
# Forensic JSON logger
# ---------------------------------------------------------------------

class JsonLogger:
    def __init__(self, path: str, max_lines: int):
        self.path = path
        self.max_lines = max_lines
        self.lines = 0

    def log(self, kind: str, payload: Dict[str, Any]):
        entry = {"kind": kind, "time": time.time(), **payload}
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            self.lines += 1
            if self.lines > self.max_lines:
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write("")
                self.lines = 0
        except Exception as e:
            print(f"[LOG] Failed to write log: {e}")


LOGGER = JsonLogger(CONFIG["logging"]["json_log_path"], CONFIG["logging"]["max_lines"])

# ---------------------------------------------------------------------
# Sensors: CPU, RAM, GPU, VRAM, power, FPS, frametime variance
# ---------------------------------------------------------------------

@dataclass
class SensorSnapshot:
    cpu_usage: float
    ram_usage: float
    gpu_usage: float
    gpu_temp: float
    fps: float
    frametime_var: float
    vram_usage: float
    gpu_power: float


class SensorHub:
    def __init__(self):
        self.has_nvml = pynvml is not None
        if self.has_nvml:
            try:
                pynvml.nvmlInit()
                self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                print("[SENSORS] NVML initialized")
            except Exception as e:
                print(f"[WARN] NVML init failed: {e}")
                self.has_nvml = False
                self.gpu_handle = None
        self._fps_history: List[float] = []

    def read_cpu_ram(self) -> Tuple[float, float]:
        cpu = psutil.cpu_percent(interval=None) / 100.0
        ram = psutil.virtual_memory().percent / 100.0
        return cpu, ram

    def read_gpu(self) -> Tuple[float, float, float, float]:
        if not self.has_nvml:
            return 0.0, 40.0, 0.0, 0.0
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle)
            temp = pynvml.nvmlDeviceGetTemperature(
                self.gpu_handle, pynvml.NVML_TEMPERATURE_GPU
            )
            mem = pynvml.nvmlDeviceGetMemoryInfo(self.gpu_handle)
            power = pynvml.nvmlDeviceGetPowerUsage(self.gpu_handle) / 1000.0  # W
            vram_usage = mem.used / max(mem.total, 1)
            return util.gpu / 100.0, float(temp), float(vram_usage), float(power)
        except Exception:
            return 0.0, 40.0, 0.0, 0.0

    def read_fps(self) -> float:
        # TODO: wire real FPS overlay / game hook
        fps = 60.0 + random.uniform(-15.0, 15.0)
        self._fps_history.append(fps)
        if len(self._fps_history) > 120:
            self._fps_history.pop(0)
        return fps

    def frametime_variance(self) -> float:
        if len(self._fps_history) < 2:
            return 0.0
        ft = [1.0 / max(f, 1e-3) for f in self._fps_history]
        return float(np.var(ft))

    def snapshot(self) -> SensorSnapshot:
        cpu, ram = self.read_cpu_ram()
        gpu, temp, vram, power = self.read_gpu()
        fps = self.read_fps()
        ft_var = self.frametime_variance()
        snap = SensorSnapshot(
            cpu_usage=cpu,
            ram_usage=ram,
            gpu_usage=gpu,
            gpu_temp=temp,
            fps=fps,
            frametime_var=ft_var,
            vram_usage=vram,
            gpu_power=power,
        )
        LOGGER.log("sensors", {
            "cpu": cpu,
            "ram": ram,
            "gpu": gpu,
            "temp": temp,
            "fps": fps,
            "ft_var": ft_var,
            "vram": vram,
            "power": power,
        })
        return snap


SENSORS = SensorHub()

# ---------------------------------------------------------------------
# Graphics tuner (driver-level + game configs)
# ---------------------------------------------------------------------

class GraphicsTuner:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.registry_paths = cfg["registry_paths"]
        self.game_root = cfg["game_config_root"]
        self.profile = cfg["default_game_profile"]

    def _set_registry_value(self, root, path: str, name: str, value: Any):
        if winreg is None:
            print(f"[GFX] (stub) Registry set {path}\\{name} = {value}")
            LOGGER.log("gfx_registry_stub", {"path": path, "name": name, "value": value})
            return
        try:
            key = winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE)
        except FileNotFoundError:
            try:
                key = winreg.CreateKey(root, path)
            except Exception as e:
                print(f"[GFX] Failed to create key {path}: {e}")
                return
        try:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
            winreg.CloseKey(key)
            print(f"[GFX] Registry set {path}\\{name} = {value}")
            LOGGER.log("gfx_registry", {"path": path, "name": name, "value": value})
        except Exception as e:
            print(f"[GFX] Failed to set registry {path}\\{name}: {e}")

    def lower_global_quality(self):
        print("[GFX] Lowering global graphics quality")
        self._set_registry_value(winreg.HKEY_CURRENT_USER, self.registry_paths["nvidia_quality"], "QualityLevel", "Low")
        self._set_registry_value(winreg.HKEY_CURRENT_USER, self.registry_paths["amd_quality"], "QualityLevel", "Low")
        self._set_registry_value(winreg.HKEY_CURRENT_USER, self.registry_paths["nvidia_perf"], "PerfMode", "MaxPerf")
        self._set_registry_value(winreg.HKEY_CURRENT_USER, self.registry_paths["amd_perf"], "PerfMode", "MaxPerf")

    def raise_global_quality(self):
        print("[GFX] Raising global graphics quality")
        self._set_registry_value(winreg.HKEY_CURRENT_USER, self.registry_paths["nvidia_quality"], "QualityLevel", "High")
        self._set_registry_value(winreg.HKEY_CURRENT_USER, self.registry_paths["amd_quality"], "QualityLevel", "High")

    def _find_game_configs(self) -> List[str]:
        paths = []
        if os.path.isdir(self.game_root):
            for root, dirs, files in os.walk(self.game_root):
                for f in files:
                    if f.lower().endswith((".ini", ".cfg", ".json")):
                        paths.append(os.path.join(root, f))
        return paths

    def _patch_game_config(self, path: str, lower: bool = True):
        print(f"[GFX] Patching game config: {path} (lower={lower})")
        LOGGER.log("gfx_game_patch", {"path": path, "lower": lower})
        # TODO: parse INI/JSON and adjust resolution scale, shadows, postfx, LOD

    def lower_game_quality(self):
        print("[GFX] Lowering per-game graphics quality")
        for cfg_path in self._find_game_configs():
            self._patch_game_config(cfg_path, lower=True)

    def raise_game_quality(self):
        print("[GFX] Raising per-game graphics quality")
        for cfg_path in self._find_game_configs():
            self._patch_game_config(cfg_path, lower=False)

    def throttle_background_processes(self):
        print("[GFX] Throttling background processes")
        for proc in psutil.process_iter(attrs=["pid", "name", "cpu_percent"]):
            name = (proc.info["name"] or "").lower()
            if any(k in name for k in ["chrome", "edge", "discord", "obs", "steam"]):
                try:
                    p = psutil.Process(proc.info["pid"])
                    p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                    LOGGER.log("gfx_throttle", {"pid": proc.info["pid"], "name": name})
                    print(f"[GFX] Lowered priority for {name} ({proc.info['pid']})")
                except Exception:
                    pass

    def kill_heavy_background(self):
        print("[GFX] Killing heavy background processes (aggressive)")
        for proc in psutil.process_iter(attrs=["pid", "name", "cpu_percent"]):
            name = (proc.info["name"] or "").lower()
            cpu = proc.info["cpu_percent"] or 0.0
            if cpu > 20.0 and any(k in name for k in ["chrome", "edge", "discord", "obs", "steam"]):
                try:
                    LOGGER.log("gfx_kill", {"pid": proc.info["pid"], "name": name, "cpu": cpu})
                    psutil.Process(proc.info["pid"]).terminate()
                    print(f"[GFX] Terminated {name} ({proc.info['pid']}) cpu={cpu}")
                except Exception:
                    pass

    def emergency_downclock_stub(self):
        print("[GFX] Emergency downclock (stub) — would reduce GPU clocks / power limits via NVAPI")
        LOGGER.log("gfx_emergency_downclock", {})


GFX = GraphicsTuner(CONFIG["graphics"])

# ---------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------

@dataclass
class BorgState:
    snapshot: SensorSnapshot
    mode_name: str


class BorgEnv:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.mode_name = "flow"
        self.mode_cfg = self.config["modes"][self.mode_name]

    def set_mode(self, mode_name: str):
        if mode_name in self.config["modes"]:
            self.mode_name = mode_name
            self.mode_cfg = self.config["modes"][mode_name]
            print(f"[ENV] Switched mode to {mode_name}")
            LOGGER.log("mode_switch", {"mode": mode_name})
        else:
            print(f"[WARN] Unknown mode: {mode_name}")

    def get_state_vector(self, snap: SensorSnapshot) -> np.ndarray:
        temp_norm = snap.gpu_temp / 100.0
        fps_norm = snap.fps / max(self.mode_cfg["target_fps"], 1)
        ft_norm = min(snap.frametime_var * 100.0, 1.0)
        power_norm = snap.gpu_power / 300.0
        return np.array(
            [
                snap.cpu_usage,
                snap.ram_usage,
                snap.gpu_usage,
                temp_norm,
                fps_norm,
                ft_norm,
                snap.vram_usage,
                power_norm,
            ],
            dtype=np.float32,
        )

    def reward(self, snap: SensorSnapshot) -> float:
        w = self.mode_cfg["reward_weights"]
        target_fps = self.mode_cfg["target_fps"]

        fps_stability = -abs(snap.fps - target_fps) / max(target_fps, 1)
        cpu_headroom = 1.0 - snap.cpu_usage
        gpu_headroom = 1.0 - snap.gpu_usage
        thermal_penalty = max(0.0, (snap.gpu_temp - CONFIG["watchdog"]["max_temp_c"]) / 20.0)
        ft_penalty = min(snap.frametime_var * 100.0, 1.0)

        r = (
            w["fps_stability"] * fps_stability
            + w["cpu_headroom"] * cpu_headroom
            + w["gpu_headroom"] * gpu_headroom
            - w["thermal_safety"] * thermal_penalty
            - w["frametime_stability"] * ft_penalty
        )
        return float(r)

    def apply_action(self, action: int):
        """
        0: no-op
        1: lower graphics
        2: raise graphics
        3: throttle background
        4: kill heavy background
        5: switch to flow
        6: switch to deep_work
        7: switch to recovery
        """
        LOGGER.log("action", {"mode": self.mode_name, "action": action})
        if action == 0:
            return
        elif action == 1:
            GFX.lower_global_quality()
            GFX.lower_game_quality()
        elif action == 2:
            GFX.raise_global_quality()
            GFX.raise_game_quality()
        elif action == 3:
            GFX.throttle_background_processes()
        elif action == 4:
            GFX.kill_heavy_background()
        elif action == 5:
            self.set_mode("flow")
        elif action == 6:
            self.set_mode("deep_work")
        elif action == 7:
            self.set_mode("recovery")
        else:
            print(f"[ENV] Unknown action {action}")

    def step(self, action: int) -> Tuple[np.ndarray, float]:
        self.apply_action(action)
        snap = SENSORS.snapshot()
        r = self.reward(snap)
        s_vec = self.get_state_vector(snap)
        LOGGER.log("step", {
            "mode": self.mode_name,
            "action": action,
            "reward": r,
            "cpu": snap.cpu_usage,
            "gpu": snap.gpu_usage,
            "temp": snap.gpu_temp,
            "fps": snap.fps,
            "ft_var": snap.frametime_var,
            "vram": snap.vram_usage,
            "power": snap.gpu_power,
        })
        return s_vec, r

    def reset(self) -> np.ndarray:
        snap = SENSORS.snapshot()
        return self.get_state_vector(snap)


ENV = BorgEnv(CONFIG)

STATE_DIM = 8
ACTION_DIM = 8

# ---------------------------------------------------------------------
# Hybrid agent (DQN + PPO + Oracle)
# ---------------------------------------------------------------------

class HybridAgent(torch.nn.Module):
    def __init__(self, state_dim: int, action_dim: int, cfg: Dict[str, Any]):
        super().__init__()
        self.cfg = cfg
        hidden = 128

        self.body = torch.nn.Sequential(
            torch.nn.Linear(state_dim, hidden),
            torch.nn.ReLU(),
            torch.nn.Linear(hidden, hidden),
            torch.nn.ReLU(),
        )

        self.q_head = torch.nn.Linear(hidden, action_dim)
        self.pi_head = torch.nn.Linear(hidden, action_dim)
        self.v_head = torch.nn.Linear(hidden, 1)
        self.oracle_head = torch.nn.Linear(hidden, 1)

        self.dqn_opt = torch.optim.Adam(
            list(self.body.parameters()) + list(self.q_head.parameters()),
            lr=self.cfg["dqn_lr"],
        )
        self.ppo_opt = torch.optim.Adam(
            list(self.body.parameters())
            + list(self.pi_head.parameters())
            + list(self.v_head.parameters())
            + list(self.oracle_head.parameters()),
            lr=self.cfg["ppo_lr"],
        )

    def forward(self, x: torch.Tensor) -> Dict[str, torch.Tensor]:
        h = self.body(x)
        q = self.q_head(h)
        logits = self.pi_head(h)
        v = self.v_head(h).squeeze(-1)
        oracle = self.oracle_head(h).squeeze(-1)
        return {"q": q, "logits": logits, "v": v, "oracle": oracle}

    def act(self, state: np.ndarray, epsilon: float = 0.1) -> int:
        s = torch.tensor(state, dtype=torch.float32).unsqueeze(0)
        out = self.forward(s)
        q = out["q"].detach().cpu().numpy()[0]
        if random.random() < epsilon:
            a = random.randint(0, q.shape[0] - 1)
        else:
            a = int(np.argmax(q))
        LOGGER.log("policy_act", {"epsilon": epsilon, "action": a})
        return a

    def oracle_score(self, state: np.ndarray) -> float:
        s = torch.tensor(state, dtype=torch.float32).unsqueeze(0)
        out = self.forward(s)
        score = float(out["oracle"].detach().cpu().numpy()[0])
        LOGGER.log("oracle_score", {"score": score})
        return score

    def dqn_update(self, batch):
        s = torch.tensor(batch["s"], dtype=torch.float32)
        a = torch.tensor(batch["a"], dtype=torch.int64)
        r = torch.tensor(batch["r"], dtype=torch.float32)
        s2 = torch.tensor(batch["s2"], dtype=torch.float32)
        done = torch.tensor(batch["done"], dtype=torch.float32)

        out = self.forward(s)
        q = out["q"]
        q_a = q.gather(1, a.unsqueeze(1)).squeeze(1)

        with torch.no_grad():
            out2 = self.forward(s2)
            q2 = out2["q"]
            q2_max = q2.max(dim=1)[0]
            target = r + self.cfg["gamma"] * (1.0 - done) * q2_max

        loss = torch.nn.functional.mse_loss(q_a, target)
        self.dqn_opt.zero_grad()
        loss.backward()
        self.dqn_opt.step()
        LOGGER.log("dqn_loss", {"loss": float(loss.item())})
        return float(loss.item())

    def ppo_update(self, traj):
        s = torch.tensor(traj["s"], dtype=torch.float32)
        a = torch.tensor(traj["a"], dtype=torch.int64)
        r = torch.tensor(traj["r"], dtype=torch.float32)
        logp_old = torch.tensor(traj["logp"], dtype=torch.float32)
        v_old = torch.tensor(traj["v"], dtype=torch.float32)

        with torch.no_grad():
            adv = r - v_old
            adv = (adv - adv.mean()) / (adv.std() + 1e-8)
            returns = adv + v_old

        out = self.forward(s)
        logits = out["logits"]
        v = out["v"]
        oracle = out["oracle"]

        logp = torch.nn.functional.log_softmax(logits, dim=-1)
        logp_a = logp.gather(1, a.unsqueeze(1)).squeeze(1)
        ratio = torch.exp(logp_a - logp_old)

        clip = self.cfg["ppo_clip"]
        obj1 = ratio * adv
        obj2 = torch.clamp(ratio, 1.0 - clip, 1.0 + clip) * adv
        policy_loss = -torch.min(obj1, obj2).mean()

        value_loss = torch.nn.functional.mse_loss(v, returns)
        oracle_loss = torch.nn.functional.mse_loss(oracle, r)

        entropy = -(logp * torch.exp(logp)).sum(dim=1).mean()

        loss = (
            policy_loss
            + self.cfg["value_coef"] * value_loss
            + 0.1 * oracle_loss
            - self.cfg["entropy_coef"] * entropy
        )

        self.ppo_opt.zero_grad()
        loss.backward()
        self.ppo_opt.step()

        stats = {
            "policy_loss": float(policy_loss.item()),
            "value_loss": float(value_loss.item()),
            "oracle_loss": float(oracle_loss.item()),
            "entropy": float(entropy.item()),
        }
        LOGGER.log("ppo_stats", stats)
        return stats

# ---------------------------------------------------------------------
# Anomaly autoencoder
# ---------------------------------------------------------------------

class AnomalyAutoencoder(torch.nn.Module):
    def __init__(self, input_dim: int, latent_dim: int):
        super().__init__()
        self.encoder = torch.nn.Sequential(
            torch.nn.Linear(input_dim, 16),
            torch.nn.ReLU(),
            torch.nn.Linear(16, latent_dim),
        )
        self.decoder = torch.nn.Sequential(
            torch.nn.Linear(latent_dim, 16),
            torch.nn.ReLU(),
            torch.nn.Linear(16, input_dim),
        )
        self.opt = torch.optim.Adam(self.parameters(), lr=1e-3)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        z = self.encoder(x)
        recon = self.decoder(z)
        return recon

    def train_batch(self, batch: np.ndarray) -> float:
        x = torch.tensor(batch, dtype=torch.float32)
        recon = self.forward(x)
        loss = torch.nn.functional.mse_loss(recon, x)
        self.opt.zero_grad()
        loss.backward()
        self.opt.step()
        LOGGER.log("anomaly_train_loss", {"loss": float(loss.item())})
        return float(loss.item())

    def anomaly_score(self, x: np.ndarray) -> float:
        with torch.no_grad():
            t = torch.tensor(x, dtype=torch.float32).unsqueeze(0)
            recon = self.forward(t)
            loss = torch.nn.functional.mse_loss(recon, t)
        score = float(loss.item())
        LOGGER.log("anomaly_score", {"score": score})
        return score


ANOM_CFG = CONFIG["anomaly"]
AUTOENC = AnomalyAutoencoder(input_dim=STATE_DIM, latent_dim=ANOM_CFG["latent_dim"])
ANOM_BUFFER: List[np.ndarray] = []

# ---------------------------------------------------------------------
# Tri-brain
# ---------------------------------------------------------------------

@dataclass
class TeacherBrain:
    agent: HybridAgent
    name: str = "Teacher"
    crash_count: int = 0
    active: bool = True
    dream_mode: bool = False
    mood: str = "flow"


@dataclass
class ShadowBrain:
    agent: HybridAgent
    name: str = "Shadow"
    active: bool = False
    anomaly_active: bool = False


@dataclass
class OracleBrain:
    agent: HybridAgent
    name: str = "Oracle"
    active: bool = True


TEACHER = TeacherBrain(agent=HybridAgent(STATE_DIM, ACTION_DIM, CONFIG["rl"]))
SHADOW = ShadowBrain(agent=HybridAgent(STATE_DIM, ACTION_DIM, CONFIG["rl"]))
ORACLE = OracleBrain(agent=HybridAgent(STATE_DIM, ACTION_DIM, CONFIG["rl"]))

# ---------------------------------------------------------------------
# Executioner Watchdog
# ---------------------------------------------------------------------

class Watchdog:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.running = True
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def start(self):
        print("[WATCHDOG] Starting")
        self.thread.start()

    def loop(self):
        while self.running:
            snap = SENSORS.snapshot()
            s_vec = ENV.get_state_vector(snap)

            if snap.gpu_temp > self.cfg["emergency_temp_c"]:
                print(f"[WATCHDOG] EMERGENCY temp {snap.gpu_temp:.1f}C — forcing recovery, downclock, kill background.")
                ENV.set_mode("recovery")
                GFX.emergency_downclock_stub()
                GFX.kill_heavy_background()

            elif snap.gpu_temp > self.cfg["max_temp_c"]:
                print(f"[WATCHDOG] High temp {snap.gpu_temp:.1f}C — lowering graphics, throttling background.")
                GFX.lower_global_quality()
                GFX.lower_game_quality()
                GFX.throttle_background_processes()

            if TEACHER.crash_count >= self.cfg["max_crash_count"]:
                print("[WATCHDOG] Teacher crash limit reached — activating Shadow.")
                TEACHER.active = False
                SHADOW.active = True

            score = AUTOENC.anomaly_score(s_vec)
            if score > ANOM_CFG["threshold"]:
                print(f"[WATCHDOG] Anomaly {score:.4f} > {ANOM_CFG['threshold']:.4f} — Shadow anomaly mode, kill heavy background.")
                SHADOW.anomaly_active = True
                TEACHER.active = False
                SHADOW.active = True
                GFX.kill_heavy_background()

            LOGGER.log("watchdog", {
                "temp": snap.gpu_temp,
                "anomaly_score": score,
                "teacher_active": TEACHER.active,
                "shadow_active": SHADOW.active,
            })

            time.sleep(self.cfg["check_interval_sec"])


WATCHDOG = Watchdog(CONFIG["watchdog"])

# ---------------------------------------------------------------------
# Dream Teacher
# ---------------------------------------------------------------------

def run_dream_teacher():
    if not CONFIG["dream"]["enabled"]:
        return

    print("[DREAM] Dream Teacher starting synthetic episodes")
    episodes = CONFIG["dream"]["episodes"]
    length = CONFIG["dream"]["length"]

    for ep in range(episodes):
        s = ENV.reset()
        traj = {"s": [], "a": [], "r": [], "logp": [], "v": []}
        for t in range(length):
            a = TEACHER.agent.act(s, epsilon=0.4)
            s_torch = torch.tensor(s, dtype=torch.float32)
            noise = torch.randn_like(s_torch) * 0.03
            s2_torch = torch.clamp(s_torch + noise, 0.0, 1.5)
            s2 = s2_torch.numpy()

            r = TEACHER.agent.oracle_score(s2)

            out = TEACHER.agent.forward(s_torch.unsqueeze(0))
            logits = out["logits"].detach()
            v = out["v"].detach()
            logp = torch.nn.functional.log_softmax(logits, dim=-1)[0, a].item()

            traj["s"].append(s)
            traj["a"].append(a)
            traj["r"].append(r)
            traj["logp"].append(logp)
            traj["v"].append(v.item())

            s = s2

        stats = TEACHER.agent.ppo_update(traj)
        LOGGER.log("dream_episode", {"ep": ep, **stats})
        print(
            f"[DREAM] ep={ep} policy={stats['policy_loss']:.4f} "
            f"value={stats['value_loss']:.4f} oracle={stats['oracle_loss']:.4f} "
            f"entropy={stats['entropy']:.4f}"
        )

# ---------------------------------------------------------------------
# Adaptive hyperparameters
# ---------------------------------------------------------------------

def compute_epsilon(step: int) -> float:
    cfg = CONFIG["rl"]
    start = cfg["epsilon_start"]
    end = cfg["epsilon_end"]
    decay = cfg["epsilon_decay_steps"]
    frac = min(step / max(decay, 1), 1.0)
    eps = float(start + (end - start) * frac)
    LOGGER.log("epsilon", {"step": step, "epsilon": eps})
    return eps

def adapt_ppo_clip(recent_entropy: float) -> float:
    base = CONFIG["rl"]["ppo_clip"]
    if recent_entropy < 0.5:
        new_clip = min(base + 0.05, 0.4)
    elif recent_entropy > 1.5:
        new_clip = max(base - 0.05, 0.1)
    else:
        new_clip = base
    CONFIG["rl"]["ppo_clip"] = new_clip
    LOGGER.log("ppo_clip_adapt", {"entropy": recent_entropy, "clip": new_clip})
    return new_clip

# ---------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------

def run_borg_loop():
    WATCHDOG.start()
    mode_cycle = ["flow", "deep_work", "recovery"]
    mode_idx = 0

    replay: List[Dict[str, Any]] = []
    traj = {"s": [], "a": [], "r": [], "logp": [], "v": []}

    s = ENV.reset()
    step_count = 0
    recent_entropy = 1.0

    while True:
        if step_count % 3000 == 0 and step_count > 0:
            mode_idx = (mode_idx + 1) % len(mode_cycle)
            ENV.set_mode(mode_cycle[mode_idx])

        if CONFIG["dream"]["enabled"] and step_count > 0 and step_count % CONFIG["dream"]["interval_steps"] == 0:
            TEACHER.dream_mode = True
            TEACHER.mood = "dream"
            print("[BORG] Entering Dream Teacher phase")
            run_dream_teacher()
            TEACHER.dream_mode = False
            TEACHER.mood = "flow"
            print("[BORG] Exiting Dream Teacher phase")

        brain = TEACHER if TEACHER.active else SHADOW

        epsilon = compute_epsilon(step_count)
        oracle_score = ORACLE.agent.oracle_score(s)
        if oracle_score < -0.2:
            epsilon = min(epsilon + 0.1, 0.6)

        a = brain.agent.act(s, epsilon=epsilon)
        s2, r = ENV.step(a)

        replay.append({"s": s, "a": a, "r": r, "s2": s2, "done": 0.0})
        if len(replay) > 25000:
            replay.pop(0)

        s_t = torch.tensor(s, dtype=torch.float32).unsqueeze(0)
        out = brain.agent.forward(s_t)
        logits = out["logits"].detach()
        v = out["v"].detach()
        logp = torch.nn.functional.log_softmax(logits, dim=-1)[0, a].item()

        traj["s"].append(s)
        traj["a"].append(a)
        traj["r"].append(r)
        traj["logp"].append(logp)
        traj["v"].append(v.item())

        ANOM_BUFFER.append(s)
        if len(ANOM_BUFFER) > ANOM_CFG["train_buffer_size"]:
            ANOM_BUFFER.pop(0)

        if len(replay) >= CONFIG["rl"]["batch_size"]:
            batch = random.sample(replay, CONFIG["rl"]["batch_size"])
            batch_dict = {
                "s": np.stack([b["s"] for b in batch]),
                "a": np.array([b["a"] for b in batch], dtype=np.int64),
                "r": np.array([b["r"] for b in batch], dtype=np.float32),
                "s2": np.stack([b["s2"] for b in batch]),
                "done": np.array([b["done"] for b in batch], dtype=np.float32),
            }
            brain.agent.dqn_update(batch_dict)

        if len(traj["s"]) >= CONFIG["rl"]["trajectory_len"]:
            stats = brain.agent.ppo_update(traj)
            recent_entropy = stats["entropy"]
            adapt_ppo_clip(recent_entropy)
            print(
                f"[PPO] policy={stats['policy_loss']:.4f} "
                f"value={stats['value_loss']:.4f} "
                f"oracle={stats['oracle_loss']:.4f} "
                f"entropy={stats['entropy']:.4f} "
                f"clip={CONFIG['rl']['ppo_clip']:.3f}"
            )
            traj = {"s": [], "a": [], "r": [], "logp": [], "v": []}

        if len(ANOM_BUFFER) >= 256 and step_count % ANOM_CFG["train_interval_steps"] == 0:
            batch = np.stack(random.sample(ANOM_BUFFER, 256))
            loss = AUTOENC.train_batch(batch)
            print(f"[ANOM] Autoencoder train loss={loss:.6f}")

        s = s2
        step_count += 1
        time.sleep(0.05)


if __name__ == "__main__":
    print("[BORG] ULTRABORG V3 GOD-GOVERNOR starting (full autonomy)...")
    try:
        run_borg_loop()
    except KeyboardInterrupt:
        print("\n[BORG] Stopped by user.")
    except Exception as e:
        print(f"[BORG] Crash in main loop: {e}")
        TEACHER.crash_count += 1
