#!/usr/bin/env python3
"""
ULTRABORG TEACHER GOVERNOR
Hybrid DQN + PPO, cross-platform sensors, flow modes, watchdog.
Single-file "god-like" core.

Now with:
- Real graphics tuning hooks (Windows registry / driver / game config stubs)
- Dream Teacher (offline imagination / replay)
- Anomaly autoencoder for sensor-state health monitoring
"""

import os
import sys
import json
import time
import math
import random
import threading
from dataclasses import dataclass, field
from typing import Dict, Any, List, Tuple, Optional

# ---------------------------------------------------------------------
# Auto-loader for libraries (psutil, pynvml, torch, numpy)
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

# ---------------------------------------------------------------------
# Config: Flow / Deep Work / Recovery profiles (JSON)
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
                "thermal_safety": 1.0
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
                "thermal_safety": 1.0
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
                "thermal_safety": 1.5
            }
        }
    },
    "rl": {
        "gamma": 0.99,
        "dqn_lr": 1e-4,
        "ppo_lr": 3e-4,
        "ppo_clip": 0.2,
        "entropy_coef": 0.01,
        "value_coef": 0.5,
        "batch_size": 64,
        "trajectory_len": 256
    },
    "watchdog": {
        "max_temp_c": 85.0,
        "max_crash_count": 3,
        "check_interval_sec": 5.0
    },
    "graphics": {
        "registry_paths": {
            "nvidia_quality": r"HKCU\\Software\\NVIDIA Corporation\\Global\\Quality",
            "amd_quality": r"HKCU\\Software\\AMD\\Global\\Quality"
        },
        "game_config_root": os.path.expanduser("~\\Documents\\MyGames"),
        "default_game_profile": {
            "resolution_scale_step": 0.05,
            "shadow_quality_step": 1,
            "postfx_step": 1
        }
    },
    "dream": {
        "enabled": True,
        "interval_steps": 5000,
        "episodes": 10,
        "length": 128
    },
    "anomaly": {
        "latent_dim": 3,
        "train_buffer_size": 5000,
        "train_interval_steps": 2000,
        "threshold": 0.02
    }
}

CONFIG_PATH = "borg_config.json"


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
# Sensor abstraction (CPU, RAM, GPU, FPS placeholder, thermals)
# ---------------------------------------------------------------------

@dataclass
class SensorSnapshot:
    cpu_usage: float
    ram_usage: float
    gpu_usage: float
    gpu_temp: float
    fps: float


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

    def read_cpu_ram(self) -> Tuple[float, float]:
        cpu = psutil.cpu_percent(interval=None) / 100.0
        ram = psutil.virtual_memory().percent / 100.0
        return cpu, ram

    def read_gpu(self) -> Tuple[float, float]:
        if not self.has_nvml:
            return 0.0, 40.0  # fallback
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle)
            temp = pynvml.nvmlDeviceGetTemperature(
                self.gpu_handle, pynvml.NVML_TEMPERATURE_GPU
            )
            return util.gpu / 100.0, float(temp)
        except Exception:
            return 0.0, 40.0

    def read_fps(self) -> float:
        # TODO: wire real FPS overlay / game hook
        # For now, simulate a noisy FPS around some baseline.
        return 60.0 + random.uniform(-10.0, 10.0)

    def snapshot(self) -> SensorSnapshot:
        cpu, ram = self.read_cpu_ram()
        gpu, temp = self.read_gpu()
        fps = self.read_fps()
        return SensorSnapshot(cpu_usage=cpu, ram_usage=ram,
                              gpu_usage=gpu, gpu_temp=temp, fps=fps)


SENSORS = SensorHub()

# ---------------------------------------------------------------------
# Graphics tuning hooks (Windows / driver / game configs)
# ---------------------------------------------------------------------

class GraphicsTuner:
    """
    Abstracts real tuning actions.
    For now: safe stubs with logging, ready for real wiring.
    """

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.registry_paths = cfg["registry_paths"]
        self.game_root = cfg["game_config_root"]
        self.profile = cfg["default_game_profile"]

    # --- Registry / driver stubs ---

    def _set_registry_value(self, path: str, name: str, value: Any):
        # TODO: use winreg to actually write values
        print(f"[GFX] Registry set {path}\\{name} = {value}")

    def lower_global_quality(self):
        print("[GFX] Lowering global graphics quality (driver-level)")
        self._set_registry_value(self.registry_paths["nvidia_quality"], "QualityLevel", "Low")
        self._set_registry_value(self.registry_paths["amd_quality"], "QualityLevel", "Low")

    def raise_global_quality(self):
        print("[GFX] Raising global graphics quality (driver-level)")
        self._set_registry_value(self.registry_paths["nvidia_quality"], "QualityLevel", "High")
        self._set_registry_value(self.registry_paths["amd_quality"], "QualityLevel", "High")

    # --- Game config stubs ---

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
        # TODO: parse INI/JSON and adjust resolution scale, shadows, postfx
        # For now, just log.

    def lower_game_quality(self):
        print("[GFX] Lowering per-game graphics quality")
        for cfg_path in self._find_game_configs():
            self._patch_game_config(cfg_path, lower=True)

    def raise_game_quality(self):
        print("[GFX] Raising per-game graphics quality")
        for cfg_path in self._find_game_configs():
            self._patch_game_config(cfg_path, lower=False)

    # --- Background throttling stubs ---

    def throttle_background_processes(self):
        print("[GFX] Throttling background processes")
        # Example: lower priority of known heavy processes
        for proc in psutil.process_iter(attrs=["pid", "name", "cpu_percent"]):
            name = proc.info["name"] or ""
            if any(k in name.lower() for k in ["chrome", "edge", "discord", "obs"]):
                try:
                    p = psutil.Process(proc.info["pid"])
                    p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                    print(f"[GFX] Lowered priority for {name} ({proc.info['pid']})")
                except Exception:
                    pass


GFX = GraphicsTuner(CONFIG["graphics"])

# ---------------------------------------------------------------------
# Flow environment: state, actions, reward
# ---------------------------------------------------------------------

@dataclass
class BorgState:
    snapshot: SensorSnapshot
    mode_name: str


class BorgEnv:
    """
    RL environment: state = sensors + mode, action = tuning decision.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.mode_name = "flow"
        self.mode_cfg = self.config["modes"][self.mode_name]
        self.last_fps = None

    def set_mode(self, mode_name: str):
        if mode_name in self.config["modes"]:
            self.mode_name = mode_name
            self.mode_cfg = self.config["modes"][mode_name]
            print(f"[ENV] Switched mode to {mode_name}")
        else:
            print(f"[WARN] Unknown mode: {mode_name}")

    def get_state_vector(self, snap: SensorSnapshot) -> np.ndarray:
        # [cpu, ram, gpu, temp_norm, fps_norm]
        temp_norm = snap.gpu_temp / 100.0
        fps_norm = snap.fps / max(self.mode_cfg["target_fps"], 1)
        return np.array(
            [snap.cpu_usage, snap.ram_usage, snap.gpu_usage, temp_norm, fps_norm],
            dtype=np.float32,
        )

    def reward(self, snap: SensorSnapshot) -> float:
        w = self.mode_cfg["reward_weights"]
        target_fps = self.mode_cfg["target_fps"]

        fps_stability = -abs(snap.fps - target_fps) / max(target_fps, 1)
        cpu_headroom = 1.0 - snap.cpu_usage
        gpu_headroom = 1.0 - snap.gpu_usage
        thermal_penalty = max(0.0, (snap.gpu_temp - CONFIG["watchdog"]["max_temp_c"]) / 20.0)

        r = (
            w["fps_stability"] * fps_stability
            + w["cpu_headroom"] * cpu_headroom
            + w["gpu_headroom"] * gpu_headroom
            - w["thermal_safety"] * thermal_penalty
        )
        return float(r)

    def apply_action(self, action: int):
        """
        0: no-op
        1: lower graphics (driver + game configs)
        2: raise graphics
        3: throttle background
        """
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
        else:
            print(f"[ENV] Unknown action {action}")

    def step(self, action: int) -> Tuple[np.ndarray, float]:
        # Apply tuning
        self.apply_action(action)

        # Read new sensors
        snap = SENSORS.snapshot()
        r = self.reward(snap)
        s_vec = self.get_state_vector(snap)
        return s_vec, r

    def reset(self) -> np.ndarray:
        snap = SENSORS.snapshot()
        return self.get_state_vector(snap)


ENV = BorgEnv(CONFIG)

# ---------------------------------------------------------------------
# Hybrid RL agent: DQN + PPO
# ---------------------------------------------------------------------

class HybridAgent(torch.nn.Module):
    def __init__(self, state_dim: int, action_dim: int, cfg: Dict[str, Any]):
        super().__init__()
        self.cfg = cfg
        hidden = 128

        # Shared body
        self.body = torch.nn.Sequential(
            torch.nn.Linear(state_dim, hidden),
            torch.nn.ReLU(),
            torch.nn.Linear(hidden, hidden),
            torch.nn.ReLU(),
        )

        # DQN head (Q-values)
        self.q_head = torch.nn.Linear(hidden, action_dim)

        # PPO heads (policy + value)
        self.pi_head = torch.nn.Linear(hidden, action_dim)
        self.v_head = torch.nn.Linear(hidden, 1)

        self.dqn_opt = torch.optim.Adam(
            list(self.body.parameters()) + list(self.q_head.parameters()),
            lr=self.cfg["dqn_lr"],
        )
        self.ppo_opt = torch.optim.Adam(
            list(self.body.parameters())
            + list(self.pi_head.parameters())
            + list(self.v_head.parameters()),
            lr=self.cfg["ppo_lr"],
        )

    def forward(self, x: torch.Tensor) -> Dict[str, torch.Tensor]:
        h = self.body(x)
        q = self.q_head(h)
        logits = self.pi_head(h)
        v = self.v_head(h).squeeze(-1)
        return {"q": q, "logits": logits, "v": v}

    def act(self, state: np.ndarray, epsilon: float = 0.1) -> int:
        s = torch.tensor(state, dtype=torch.float32).unsqueeze(0)
        out = self.forward(s)
        q = out["q"].detach().cpu().numpy()[0]
        if random.random() < epsilon:
            return random.randint(0, q.shape[0] - 1)
        return int(np.argmax(q))

    # ---------------- DQN update ----------------

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
        return float(loss.item())

    # ---------------- PPO update ----------------

    def ppo_update(self, traj):
        """
        traj: dict with s, a, r, logp_old, v_old
        Implements advantages, clipping, entropy bonus.
        """
        s = torch.tensor(traj["s"], dtype=torch.float32)
        a = torch.tensor(traj["a"], dtype=torch.int64)
        r = torch.tensor(traj["r"], dtype=torch.float32)
        logp_old = torch.tensor(traj["logp"], dtype=torch.float32)
        v_old = torch.tensor(traj["v"], dtype=torch.float32)

        with torch.no_grad():
            # simple GAE-like advantage (no bootstrapping for brevity)
            adv = r - v_old
            adv = (adv - adv.mean()) / (adv.std() + 1e-8)
            returns = adv + v_old

        out = self.forward(s)
        logits = out["logits"]
        v = out["v"]

        logp = torch.nn.functional.log_softmax(logits, dim=-1)
        logp_a = logp.gather(1, a.unsqueeze(1)).squeeze(1)
        ratio = torch.exp(logp_a - logp_old)

        clip = self.cfg["ppo_clip"]
        obj1 = ratio * adv
        obj2 = torch.clamp(ratio, 1.0 - clip, 1.0 + clip) * adv
        policy_loss = -torch.min(obj1, obj2).mean()

        value_loss = torch.nn.functional.mse_loss(v, returns)

        entropy = -(logp * torch.exp(logp)).sum(dim=1).mean()

        loss = (
            policy_loss
            + self.cfg["value_coef"] * value_loss
            - self.cfg["entropy_coef"] * entropy
        )

        self.ppo_opt.zero_grad()
        loss.backward()
        self.ppo_opt.step()
        return {
            "policy_loss": float(policy_loss.item()),
            "value_loss": float(value_loss.item()),
            "entropy": float(entropy.item()),
        }

# ---------------------------------------------------------------------
# Anomaly autoencoder for sensor states
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
        return float(loss.item())

    def anomaly_score(self, x: np.ndarray) -> float:
        with torch.no_grad():
            t = torch.tensor(x, dtype=torch.float32).unsqueeze(0)
            recon = self.forward(t)
            loss = torch.nn.functional.mse_loss(recon, t)
        return float(loss.item())


ANOM_CFG = CONFIG["anomaly"]
AUTOENC = AnomalyAutoencoder(input_dim=5, latent_dim=ANOM_CFG["latent_dim"])
ANOM_BUFFER: List[np.ndarray] = []

# ---------------------------------------------------------------------
# Teacher / Shadow / Dream Teacher
# ---------------------------------------------------------------------

@dataclass
class TeacherBrain:
    agent: HybridAgent
    name: str = "Teacher"
    crash_count: int = 0
    active: bool = True
    dream_mode: bool = False


@dataclass
class ShadowBrain:
    agent: HybridAgent
    name: str = "Shadow"
    active: bool = False
    anomaly_active: bool = False


TEACHER = TeacherBrain(agent=HybridAgent(5, 4, CONFIG["rl"]), name="Teacher")
SHADOW = ShadowBrain(agent=HybridAgent(5, 4, CONFIG["rl"]), name="Shadow")

# ---------------------------------------------------------------------
# Watchdog with anomaly integration
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
            if snap.gpu_temp > self.cfg["max_temp_c"]:
                print(f"[WATCHDOG] High temp {snap.gpu_temp:.1f}C — consider throttling or pausing RL.")
            if TEACHER.crash_count >= self.cfg["max_crash_count"]:
                print("[WATCHDOG] Teacher crash limit reached — activating Shadow Teacher.")
                TEACHER.active = False
                SHADOW.active = True

            # anomaly check
            s_vec = ENV.get_state_vector(snap)
            score = AUTOENC.anomaly_score(s_vec)
            if score > ANOM_CFG["threshold"]:
                print(f"[WATCHDOG] Anomaly score {score:.4f} > {ANOM_CFG['threshold']:.4f} — Shadow anomaly mode.")
                SHADOW.anomaly_active = True
                TEACHER.active = False
                SHADOW.active = True

            time.sleep(self.cfg["check_interval_sec"])


WATCHDOG = Watchdog(CONFIG["watchdog"])

# ---------------------------------------------------------------------
# Dream Teacher: offline imagination / replay
# ---------------------------------------------------------------------

def run_dream_teacher():
    if not CONFIG["dream"]["enabled"]:
        return

    print("[DREAM] Dream Teacher starting offline imagination episodes")
    episodes = CONFIG["dream"]["episodes"]
    length = CONFIG["dream"]["length"]

    for ep in range(episodes):
        s = ENV.reset()
        traj = {"s": [], "a": [], "r": [], "logp": [], "v": []}
        for t in range(length):
            a = TEACHER.agent.act(s, epsilon=0.2)
            s2, r = ENV.step(a)

            s_t = torch.tensor(s, dtype=torch.float32).unsqueeze(0)
            out = TEACHER.agent.forward(s_t)
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
        print(
            f"[DREAM] ep={ep} policy={stats['policy_loss']:.4f} "
            f"value={stats['value_loss']:.4f} entropy={stats['entropy']:.4f}"
        )

# ---------------------------------------------------------------------
# Main training / control loop (simplified)
# ---------------------------------------------------------------------

def run_borg_loop():
    WATCHDOG.start()
    mode_cycle = ["flow", "deep_work", "recovery"]
    mode_idx = 0

    # Simple replay buffer for DQN
    replay: List[Dict[str, Any]] = []

    # PPO trajectory buffer
    traj = {"s": [], "a": [], "r": [], "logp": [], "v": []}

    s = ENV.reset()
    step_count = 0

    while True:
        # Mode cycling example (could be user-controlled)
        if step_count % 1000 == 0 and step_count > 0:
            mode_idx = (mode_idx + 1) % len(mode_cycle)
            ENV.set_mode(mode_cycle[mode_idx])

        # Dream Teacher trigger
        if CONFIG["dream"]["enabled"] and step_count > 0 and step_count % CONFIG["dream"]["interval_steps"] == 0:
            TEACHER.dream_mode = True
            print("[BORG] Entering Dream Teacher phase")
            run_dream_teacher()
            TEACHER.dream_mode = False
            print("[BORG] Exiting Dream Teacher phase")

        # Choose which brain is active
        brain = TEACHER if TEACHER.active else SHADOW

        # DQN-style action selection
        a = brain.agent.act(s, epsilon=0.1)

        # Step environment
        s2, r = ENV.step(a)

        # DQN replay
        replay.append({"s": s, "a": a, "r": r, "s2": s2, "done": 0.0})
        if len(replay) > 10000:
            replay.pop(0)

        # PPO trajectory
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

        # Anomaly buffer for autoencoder
        ANOM_BUFFER.append(s)
        if len(ANOM_BUFFER) > ANOM_CFG["train_buffer_size"]:
            ANOM_BUFFER.pop(0)

        # DQN update
        if len(replay) >= CONFIG["rl"]["batch_size"]:
            batch = random.sample(replay, CONFIG["rl"]["batch_size"])
            batch_dict = {
                "s": np.stack([b["s"] for b in batch]),
                "a": np.array([b["a"] for b in batch], dtype=np.int64),
                "r": np.array([b["r"] for b in batch], dtype=np.float32),
                "s2": np.stack([b["s2"] for b in batch]),
                "done": np.array([b["done"] for b in batch], dtype=np.float32),
            }
            dqn_loss = brain.agent.dqn_update(batch_dict)

        # PPO update
        if len(traj["s"]) >= CONFIG["rl"]["trajectory_len"]:
            stats = brain.agent.ppo_update(traj)
            print(
                f"[PPO] policy={stats['policy_loss']:.4f} "
                f"value={stats['value_loss']:.4f} "
                f"entropy={stats['entropy']:.4f}"
            )
            traj = {"s": [], "a": [], "r": [], "logp": [], "v": []}

        # Autoencoder training
        if len(ANOM_BUFFER) >= 256 and step_count % ANOM_CFG["train_interval_steps"] == 0:
            batch = np.stack(random.sample(ANOM_BUFFER, 256))
            loss = AUTOENC.train_batch(batch)
            print(f"[ANOM] Autoencoder train loss={loss:.6f}")

        s = s2
        step_count += 1
        time.sleep(0.05)  # keep it gentle


if __name__ == "__main__":
    print("[BORG] ULTRABORG Teacher Governor starting...")
    try:
        run_borg_loop()
    except KeyboardInterrupt:
        print("\n[BORG] Stopped by user.")
    except Exception as e:
        print(f"[BORG] Crash in main loop: {e}")
        TEACHER.crash_count += 1
