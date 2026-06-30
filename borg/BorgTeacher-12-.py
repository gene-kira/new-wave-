#!/usr/bin/env python3
"""
BORG QUEEN — SWARM TEACHER

- Cross-platform sensors (psutil + optional NVML)
- Config-driven modes: flow / deep_work / recovery
- HybridAgent skeleton (DQN+PPO style interface)
- BorgStudent wrapper for pre-trained student models
- Consensus logic: Queen + swarm voting
- Per-student performance tracking
- Promotion path: strong student -> Shadow Teacher
- No GUI, no process killing: observe, learn, recommend only.
"""

import os
import sys
import json
import time
import math
import threading
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Tuple

# -----------------------------------------------------------------------------


def safe_import(name: str, pip_name: Optional[str] = None):
    try:
        return __import__(name)
    except ImportError:
        print(f"[BORG-QUEEN] Missing library: {name}"
              f"{' (pip install ' + pip_name + ')' if pip_name else ''}")
        return None


torch = safe_import("torch", "torch")
np = safe_import("numpy", "numpy")
psutil = safe_import("psutil", "psutil")
pynvml = safe_import("pynvml", "nvidia-ml-py3")

# -----------------------------------------------------------------------------


@dataclass
class SystemSensors:
    use_nvml: bool = field(default=False)
    nvml_initialized: bool = field(default=False)

    def __post_init__(self):
        if pynvml is not None:
            try:
                pynvml.nvmlInit()
                self.nvml_initialized = True
                self.use_nvml = True
                print("[BORG-QUEEN] NVML initialized for GPU telemetry.")
            except Exception as e:
                print(f"[BORG-QUEEN] NVML init failed: {e}")
                self.use_nvml = False

    def read_cpu(self) -> float:
        if psutil is None:
            return 0.0
        return psutil.cpu_percent(interval=None)

    def read_ram(self) -> float:
        if psutil is None:
            return 0.0
        mem = psutil.virtual_memory()
        return mem.percent

    def read_gpu(self) -> float:
        if not self.use_nvml or pynvml is None:
            return 0.0
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            return float(util.gpu)
        except Exception:
            return 0.0

    def read_temp(self) -> float:
        if psutil is None:
            return 0.0
        try:
            temps = psutil.sensors_temperatures()
            for _, entries in temps.items():
                if entries:
                    return float(entries[0].current)
        except Exception:
            pass
        return 0.0

    def read_fps(self) -> float:
        # Placeholder: wire real FPS overlay / game hook here
        return 0.0

    def read_all(self) -> Dict[str, float]:
        return {
            "cpu": self.read_cpu(),
            "ram": self.read_ram(),
            "gpu": self.read_gpu(),
            "temp": self.read_temp(),
            "fps": self.read_fps(),
        }


DEFAULT_CONFIG = {
    "mode": "flow",
    "profiles": {
        "flow": {
            "target_fps": 90,
            "max_temp": 80,
            "cpu_soft_cap": 85,
            "ram_soft_cap": 85,
            "reward_weights": {
                "fps": 1.0,
                "stability": 0.7,
                "thermal": 0.8,
                "smoothness": 0.5,
            },
        },
        "deep_work": {
            "target_fps": 60,
            "max_temp": 75,
            "cpu_soft_cap": 95,
            "ram_soft_cap": 90,
            "reward_weights": {
                "fps": 0.4,
                "stability": 1.0,
                "thermal": 0.9,
                "smoothness": 0.8,
            },
        },
        "recovery": {
            "target_fps": 30,
            "max_temp": 70,
            "cpu_soft_cap": 60,
            "ram_soft_cap": 60,
            "reward_weights": {
                "fps": 0.2,
                "stability": 0.6,
                "thermal": 1.0,
                "smoothness": 0.9,
            },
        },
    },
    "promotion": {
        "window": 100,          # steps to evaluate
        "min_avg_reward": 0.7,  # threshold for promotion
    },
}

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "borg_config.json")


def load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            print("[BORG-QUEEN] Loaded config from borg_config.json")
            return cfg
        except Exception as e:
            print(f"[BORG-QUEEN] Failed to load config, using default: {e}")
    else:
        print("[BORG-QUEEN] No borg_config.json found, using default config.")
    return DEFAULT_CONFIG


@dataclass
class BorgState:
    cpu: float
    ram: float
    gpu: float
    temp: float
    fps: float
    mode: str


def compute_reward(state: BorgState, profile: Dict[str, Any]) -> float:
    w = profile["reward_weights"]
    target_fps = profile["target_fps"]
    max_temp = profile["max_temp"]
    cpu_cap = profile["cpu_soft_cap"]
    ram_cap = profile["ram_soft_cap"]

    fps_diff = abs(state.fps - target_fps)
    fps_reward = math.exp(-fps_diff / max(target_fps, 1))

    thermal_penalty = max(0.0, (state.temp - max_temp) / max_temp)
    thermal_reward = math.exp(-thermal_penalty * 5.0)

    cpu_penalty = max(0.0, (state.cpu - cpu_cap) / 100.0)
    ram_penalty = max(0.0, (state.ram - ram_cap) / 100.0)
    stability_reward = math.exp(-(cpu_penalty + ram_penalty) * 4.0)

    smoothness_reward = 1.0  # placeholder

    reward = (
        w["fps"] * fps_reward
        + w["thermal"] * thermal_reward
        + w["stability"] * stability_reward
        + w["smoothness"] * smoothness_reward
    )
    return float(reward)


class HybridAgent:
    def __init__(self, state_dim: int, action_dim: int, device: Optional[str] = None):
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.device = device or ("cuda" if torch and torch.cuda.is_available() else "cpu")

        if torch is None:
            print("[BORG-QUEEN] Torch not available, HybridAgent inert.")
            self.policy_net = None
            return

        self.policy_net = torch.nn.Sequential(
            torch.nn.Linear(state_dim, 128),
            torch.nn.ReLU(),
            torch.nn.Linear(128, action_dim),
        ).to(self.device)

        self.policy_opt = torch.optim.Adam(self.policy_net.parameters(), lr=3e-4)
        print(f"[BORG-QUEEN] HybridAgent initialized on {self.device}.")

    def select_action(self, state_vec: List[float]) -> int:
        if torch is None or self.policy_net is None:
            return 0
        s = torch.tensor(state_vec, dtype=torch.float32, device=self.device).unsqueeze(0)
        logits = self.policy_net(s)
        probs = torch.softmax(logits, dim=-1)
        dist = torch.distributions.Categorical(probs)
        action = dist.sample().item()
        return int(action)


class BorgStudent:
    def __init__(self, checkpoint_path: str, action_dim: int, device: Optional[str] = None, role: str = "generic"):
        self.checkpoint_path = checkpoint_path
        self.action_dim = action_dim
        self.device = device or ("cuda" if torch and torch.cuda.is_available() else "cpu")
        self.model = None
        self.role = role  # e.g., "flow", "deep_work", "recovery"

        if torch is None:
            print("[BORG-STUDENT] Torch not available, student inert.")
            return

        self._load_model()

    def _load_model(self):
        try:
            state = torch.load(self.checkpoint_path, map_location=self.device)
            if isinstance(state, torch.nn.Module):
                self.model = state.to(self.device)
                print(f"[BORG-STUDENT] Loaded module from {self.checkpoint_path}")
            elif isinstance(state, dict):
                print("[BORG-STUDENT] Loaded state_dict; define matching net to use it.")
                self.model = None
            else:
                print("[BORG-STUDENT] Unknown checkpoint format.")
                self.model = None
        except Exception as e:
            print(f"[BORG-STUDENT] Failed to load student: {e}")
            self.model = None

    def suggest_action(self, state_vec: List[float]) -> Optional[int]:
        if self.model is None or torch is None:
            return None
        s = torch.tensor(state_vec, dtype=torch.float32, device=self.device).unsqueeze(0)
        with torch.no_grad():
            out = self.model(s)
        if out.ndim == 2 and out.shape[1] >= self.action_dim:
            action = int(torch.argmax(out, dim=-1).item())
            return action
        return None


@dataclass
class StudentStats:
    role: str
    rewards: List[float] = field(default_factory=list)

    def add_reward(self, r: float, window: int):
        self.rewards.append(r)
        if len(self.rewards) > window:
            self.rewards.pop(0)

    def avg_reward(self) -> float:
        if not self.rewards:
            return 0.0
        return sum(self.rewards) / len(self.rewards)


class BorgTeacher:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.mode = config.get("mode", "flow")
        self.sensors = SystemSensors()
        self.profile = config["profiles"][self.mode]

        self.action_dim = 4
        self.state_dim = 5

        self.agent = HybridAgent(self.state_dim, self.action_dim)
        self.shadow_agent = HybridAgent(self.state_dim, self.action_dim)

        self.students: List[BorgStudent] = []
        self.student_stats: Dict[int, StudentStats] = {}

        base_dir = os.path.dirname(__file__)
        student_dir = os.path.join(base_dir, "student")
        student_ckpt = os.path.join(student_dir, "data.pkl")

        self.students.append(BorgStudent(student_ckpt, self.action_dim, role="flow"))
        self.student_stats[0] = StudentStats(role="flow")

        self.running = False

    def set_mode(self, mode: str):
        if mode in self.config["profiles"]:
            self.mode = mode
            self.profile = self.config["profiles"][mode]
            print(f"[BORG-QUEEN] Mode switched to: {mode}")
        else:
            print(f"[BORG-QUEEN] Unknown mode: {mode}, keeping {self.mode}")

    def build_state(self) -> BorgState:
        metrics = self.sensors.read_all()
        return BorgState(
            cpu=metrics["cpu"],
            ram=metrics["ram"],
            gpu=metrics["gpu"],
            temp=metrics["temp"],
            fps=metrics["fps"],
            mode=self.mode,
        )

    def recommend_action(self, action_id: int) -> str:
        mapping = {
            0: "Maintain current settings.",
            1: "Recommend lowering graphics quality / resolution.",
            2: "Recommend enabling performance / high-power profile.",
            3: "Recommend short recovery break to reduce thermal / load.",
        }
        return mapping.get(action_id, "No recommendation.")

    def consensus_action(self, queen_action: int, student_actions: List[Tuple[int, Optional[int]]]) -> int:
        votes: Dict[int, float] = {}
        votes[queen_action] = votes.get(queen_action, 0.0) + 2.0

        for idx, a in student_actions:
            if a is None:
                continue
            weight = 1.0
            role = self.student_stats.get(idx, StudentStats(role="generic")).role
            if role == self.mode:
                weight = 1.5
            votes[a] = votes.get(a, 0.0) + weight

        best_action = queen_action
        best_votes = votes.get(queen_action, 0.0)
        for a, v in votes.items():
            if v > best_votes:
                best_action = a
                best_votes = v
        return best_action

    def update_student_stats(self, reward: float, student_actions: List[Tuple[int, Optional[int]]]):
        window = self.config["promotion"]["window"]
        for idx, a in student_actions:
            if a is None:
                continue
            stats = self.student_stats.get(idx)
            if stats is None:
                stats = StudentStats(role="generic")
                self.student_stats[idx] = stats
            stats.add_reward(reward, window)

    def try_promotion(self):
        threshold = self.config["promotion"]["min_avg_reward"]
        best_idx = None
        best_score = threshold

        for idx, stats in self.student_stats.items():
            avg = stats.avg_reward()
            if avg > best_score:
                best_score = avg
                best_idx = idx

        if best_idx is not None:
            stu = self.students[best_idx]
            if stu.model is not None and torch is not None:
                print(f"[BORG-QUEEN] Student {best_idx} (role={stu.role}) promoted to Shadow Teacher.")
                self.shadow_agent.policy_net = stu.model

    def loop_step(self):
        state = self.build_state()
        reward = compute_reward(state, self.profile)
        state_vec = [state.cpu, state.ram, state.gpu, state.temp, state.fps]

        queen_action = self.agent.select_action(state_vec)
        queen_rec = self.recommend_action(queen_action)

        student_actions: List[Tuple[int, Optional[int]]] = []
        for i, stu in enumerate(self.students):
            a = stu.suggest_action(state_vec)
            student_actions.append((i, a))

        consensus = self.consensus_action(queen_action, student_actions)
        consensus_rec = self.recommend_action(consensus)

        self.update_student_stats(reward, student_actions)
        self.try_promotion()

        swarm_str = ", ".join(
            f"S{i}={a}" if a is not None else f"S{i}=None"
            for i, a in student_actions
        )

        print(
            f"[BORG-QUEEN] Mode={state.mode} "
            f"CPU={state.cpu:.1f}% RAM={state.ram:.1f}% GPU={state.gpu:.1f}% "
            f"TEMP={state.temp:.1f}°C FPS={state.fps:.1f} | "
            f"Reward={reward:.3f} | Queen={queen_action} -> {queen_rec} | "
            f"Swarm[{swarm_str}] | Consensus={consensus} -> {consensus_rec}"
        )

    def start(self, interval: float = 2.0):
        if self.running:
            return
        self.running = True

        def main_loop():
            print("[BORG-QUEEN] Teacher loop started.")
            while self.running:
                try:
                    self.loop_step()
                except Exception as e:
                    print(f"[BORG-QUEEN] Error in loop_step: {e}")
                time.sleep(interval)

        def watchdog_loop():
            print("[BORG-QUEEN] Watchdog online.")
            while self.running:
                time.sleep(10.0)
                print("[BORG-QUEEN] Watchdog heartbeat: system monitored.")

        t_main = threading.Thread(target=main_loop, daemon=True)
        t_watch = threading.Thread(target=watchdog_loop, daemon=True)
        t_main.start()
        t_watch.start()

    def stop(self):
        self.running = False
        print("[BORG-QUEEN] Teacher loop stopped.")


def main():
    cfg = load_config()
    queen = BorgTeacher(cfg)
    queen.start(interval=2.0)

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        queen.stop()


if __name__ == "__main__":
    main()
