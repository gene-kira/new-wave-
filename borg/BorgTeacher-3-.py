#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ULTRABORG TEACHER – Hybrid DQN + PPO Performance Governor
God-like AI autopilot for system performance and flow states.

Conceptual features:
- Sensor fusion: CPU, RAM, GPU (NVML), placeholder FPS/game hooks.
- Flow / Deep Work / Recovery profiles via config.
- Hybrid agent: shared body + DQN head + PPO head.
- PPO trajectories: advantages, clipping, entropy bonus (simplified).
- Cross-OS best-effort behavior.
"""

import os
import sys
import time
import math
import json
import platform
import random
from collections import deque, namedtuple

# --- Safe imports with fallbacks ------------------------------------------------

def safe_import(name):
    try:
        return __import__(name)
    except ImportError:
        return None

psutil = safe_import("psutil")
torch = safe_import("torch")
nn = torch.nn if torch is not None else None
optim = torch.optim if torch is not None else None
np = safe_import("numpy")

# NVML (GPU stats)
pynvml = safe_import("pynvml")
if pynvml is not None:
    try:
        pynvml.nvmlInit()
        NVML_AVAILABLE = True
    except Exception:
        NVML_AVAILABLE = False
else:
    NVML_AVAILABLE = False

# --- Simple logging -------------------------------------------------------------

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# --- Config: Flow / Deep Work / Recovery profiles ------------------------------

DEFAULT_CONFIG = {
    "profiles": {
        "flow": {
            "name": "Flow Mode",
            "target_fps": 90,
            "max_cpu": 0.75,
            "max_gpu": 0.85,
            "reward_weights": {
                "fps": 1.0,
                "stability": 0.7,
                "thermal": 0.5,
                "latency": 0.3,
            },
        },
        "deep_work": {
            "name": "Deep Work",
            "target_fps": 60,
            "max_cpu": 0.90,
            "max_gpu": 0.90,
            "reward_weights": {
                "fps": 0.6,
                "stability": 1.0,
                "thermal": 0.7,
                "latency": 0.5,
            },
        },
        "recovery": {
            "name": "Recovery Rest",
            "target_fps": 30,
            "max_cpu": 0.50,
            "max_gpu": 0.50,
            "reward_weights": {
                "fps": 0.3,
                "stability": 0.8,
                "thermal": 1.0,
                "latency": 0.2,
            },
        },
    },
    "ppo": {
        "gamma": 0.99,
        "lambda": 0.95,
        "clip_eps": 0.2,
        "entropy_coef": 0.01,
        "value_coef": 0.5,
        "lr": 3e-4,
        "batch_size": 64,
        "epochs": 4,
    },
    "dqn": {
        "gamma": 0.99,
        "lr": 1e-4,
        "replay_size": 10000,
        "batch_size": 64,
        "epsilon_start": 1.0,
        "epsilon_end": 0.05,
        "epsilon_decay": 5000,
    },
    "general": {
        "sensor_interval": 0.5,
        "max_steps": 100000,
        "profile": "flow",  # default profile
    },
}

# --- Sensor layer --------------------------------------------------------------

class SensorSnapshot(namedtuple("SensorSnapshot", [
    "cpu_usage",
    "ram_usage",
    "gpu_usage",
    "gpu_temp",
    "fps",
    "thermal_pressure",
])):
    __slots__ = ()

def read_cpu_ram():
    if psutil is None:
        return 0.0, 0.0
    cpu = psutil.cpu_percent(interval=None) / 100.0
    ram = psutil.virtual_memory().percent / 100.0
    return cpu, ram

def read_gpu():
    if not NVML_AVAILABLE:
        return 0.0, 0.0
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu / 100.0
        temp = pynvml.nvmlDeviceGetTemperature(
            handle, pynvml.NVML_TEMPERATURE_GPU
        )
        return util, float(temp)
    except Exception:
        return 0.0, 0.0

def read_fps_placeholder():
    # Placeholder: in real system, hook into FPS overlay / game API.
    # For now, simulate FPS around a baseline with noise.
    base_fps = 60.0
    noise = random.uniform(-10.0, 10.0)
    return max(5.0, base_fps + noise)

def compute_thermal_pressure(cpu, gpu, temp):
    # Simple "fluid pressure" metaphor: combine load and temperature.
    # Normalized temp: assume 100°C is max.
    temp_norm = min(1.0, temp / 100.0) if temp > 0 else 0.0
    return (cpu + gpu + temp_norm) / 3.0

def read_sensors():
    cpu, ram = read_cpu_ram()
    gpu, temp = read_gpu()
    fps = read_fps_placeholder()
    thermal_pressure = compute_thermal_pressure(cpu, gpu, temp)
    return SensorSnapshot(cpu, ram, gpu, temp, fps, thermal_pressure)

# --- Action space: graphics / throttling / power modes -------------------------

ACTION_SPACE = [
    "graphics_low",
    "graphics_medium",
    "graphics_high",
    "throttle_cpu",
    "throttle_gpu",
    "boost_cpu",
    "boost_gpu",
    "power_saver",
    "balanced",
    "performance",
]

def apply_action_placeholder(action):
    # In real system, this would:
    # - Adjust graphics settings via game APIs / driver hooks.
    # - Change power plans / CPU governor / GPU clocks.
    # Here we just log the intent.
    log(f"[ACTION] {action} (placeholder – wire real hooks here)")

# --- Reward function -----------------------------------------------------------

def compute_reward(snapshot: SensorSnapshot, profile_cfg: dict):
    target_fps = profile_cfg["target_fps"]
    max_cpu = profile_cfg["max_cpu"]
    max_gpu = profile_cfg["max_gpu"]
    w = profile_cfg["reward_weights"]

    # FPS term: closer to target is better, but penalize too low.
    fps_ratio = snapshot.fps / max(target_fps, 1.0)
    fps_term = max(0.0, min(1.5, fps_ratio))

    # Stability term: penalize high thermal pressure.
    stability_term = 1.0 - snapshot.thermal_pressure

    # Thermal term: lower temperature is better.
    temp_norm = min(1.0, snapshot.gpu_temp / 100.0) if snapshot.gpu_temp > 0 else 0.0
    thermal_term = 1.0 - temp_norm

    # Latency term: here we approximate latency by CPU/RAM saturation.
    latency_term = 1.0 - max(snapshot.cpu_usage, snapshot.ram_usage)

    # Hard constraints: if CPU/GPU exceed profile limits, penalize heavily.
    overload_penalty = 0.0
    if snapshot.cpu_usage > max_cpu or snapshot.gpu_usage > max_gpu:
        overload_penalty = -1.0

    reward = (
        w["fps"] * fps_term +
        w["stability"] * stability_term +
        w["thermal"] * thermal_term +
        w["latency"] * latency_term +
        overload_penalty
    )

    return float(reward)

# --- Neural network: shared body + DQN head + PPO head -------------------------

if torch is not None:

    class HybridNet(nn.Module):
        def __init__(self, state_dim, action_dim):
            super().__init__()
            self.shared = nn.Sequential(
                nn.Linear(state_dim, 128),
                nn.ReLU(),
                nn.Linear(128, 128),
                nn.ReLU(),
            )
            # DQN head (Q-values)
            self.q_head = nn.Linear(128, action_dim)
            # PPO heads (policy logits + value)
            self.pi_head = nn.Linear(128, action_dim)
            self.v_head = nn.Linear(128, 1)

        def forward(self, x):
            z = self.shared(x)
            q = self.q_head(z)
            logits = self.pi_head(z)
            value = self.v_head(z)
            return q, logits, value

else:
    HybridNet = None

# --- PPO trajectory utilities --------------------------------------------------

Trajectory = namedtuple("Trajectory", [
    "states", "actions", "log_probs", "rewards", "values", "dones"
])

def compute_gae(rewards, values, dones, gamma, lam):
    advantages = []
    gae = 0.0
    values = values + [0.0]  # bootstrap
    for t in reversed(range(len(rewards))):
        delta = rewards[t] + gamma * values[t + 1] * (1 - dones[t]) - values[t]
        gae = delta + gamma * lam * (1 - dones[t]) * gae
        advantages.insert(0, gae)
    returns = [adv + v for adv, v in zip(advantages, values[:-1])]
    return advantages, returns

# --- DQN replay buffer ---------------------------------------------------------

class ReplayBuffer:
    def __init__(self, capacity):
        self.capacity = capacity
        self.buffer = deque(maxlen=capacity)

    def push(self, state, action, reward, next_state, done):
        self.buffer.append((state, action, reward, next_state, done))

    def sample(self, batch_size):
        batch = random.sample(self.buffer, batch_size)
        states, actions, rewards, next_states, dones = zip(*batch)
        return states, actions, rewards, next_states, dones

    def __len__(self):
        return len(self.buffer)

# --- Hybrid Agent --------------------------------------------------------------

class HybridAgent:
    def __init__(self, config):
        if torch is None or np is None:
            raise RuntimeError("torch and numpy are required for the hybrid agent.")

        self.cfg_ppo = config["ppo"]
        self.cfg_dqn = config["dqn"]
        self.general = config["general"]

        self.action_dim = len(ACTION_SPACE)
        self.state_dim = 6  # cpu, ram, gpu, temp, fps, thermal_pressure

        self.net = HybridNet(self.state_dim, self.action_dim)
        self.optimizer = optim.Adam(self.net.parameters(), lr=self.cfg_ppo["lr"])

        self.replay = ReplayBuffer(self.cfg_dqn["replay_size"])
        self.step_count = 0

        self.epsilon_start = self.cfg_dqn["epsilon_start"]
        self.epsilon_end = self.cfg_dqn["epsilon_end"]
        self.epsilon_decay = self.cfg_dqn["epsilon_decay"]

    def state_to_tensor(self, snapshot: SensorSnapshot):
        arr = np.array([
            snapshot.cpu_usage,
            snapshot.ram_usage,
            snapshot.gpu_usage,
            snapshot.gpu_temp / 100.0 if snapshot.gpu_temp > 0 else 0.0,
            snapshot.fps / 120.0,  # normalize to 120 FPS
            snapshot.thermal_pressure,
        ], dtype=np.float32)
        return torch.from_numpy(arr).unsqueeze(0)

    def select_action(self, snapshot: SensorSnapshot, use_dqn=True):
        state_t = self.state_to_tensor(snapshot)
        with torch.no_grad():
            q, logits, value = self.net(state_t)

        if use_dqn:
            # epsilon-greedy over Q-values
            eps = self.epsilon_end + (self.epsilon_start - self.epsilon_end) * \
                  math.exp(-1.0 * self.step_count / self.epsilon_decay)
            if random.random() < eps:
                action_idx = random.randrange(self.action_dim)
            else:
                action_idx = int(torch.argmax(q, dim=1).item())
            log_prob = None  # DQN doesn't use log_prob
        else:
            # PPO policy sampling
            probs = torch.softmax(logits, dim=1)
            dist = torch.distributions.Categorical(probs)
            action_idx = int(dist.sample().item())
            log_prob = float(dist.log_prob(torch.tensor(action_idx)).item())

        value_scalar = float(value.item())
        return action_idx, log_prob, value_scalar

    def store_dqn_transition(self, s, a, r, s_next, done):
        self.replay.push(s, a, r, s_next, done)

    def dqn_update(self):
        if len(self.replay) < self.cfg_dqn["batch_size"]:
            return
        states, actions, rewards, next_states, dones = self.replay.sample(self.cfg_dqn["batch_size"])

        states_t = torch.stack(states)
        next_states_t = torch.stack(next_states)
        actions_t = torch.tensor(actions, dtype=torch.long)
        rewards_t = torch.tensor(rewards, dtype=torch.float32)
        dones_t = torch.tensor(dones, dtype=torch.float32)

        q, _, _ = self.net(states_t)
        q_next, _, _ = self.net(next_states_t)

        q_values = q.gather(1, actions_t.unsqueeze(1)).squeeze(1)
        q_next_max = q_next.max(dim=1)[0]
        target = rewards_t + self.cfg_dqn["gamma"] * q_next_max * (1 - dones_t)

        loss = torch.mean((q_values - target.detach()) ** 2)

        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

    def ppo_update(self, trajectory: Trajectory):
        states_t = torch.stack(trajectory.states)
        actions_t = torch.tensor(trajectory.actions, dtype=torch.long)
        old_log_probs_t = torch.tensor(trajectory.log_probs, dtype=torch.float32)
        rewards = trajectory.rewards
        values = trajectory.values
        dones = trajectory.dones

        adv, ret = compute_gae(
            rewards, values, dones,
            self.cfg_ppo["gamma"], self.cfg_ppo["lambda"]
        )
        adv_t = torch.tensor(adv, dtype=torch.float32)
        ret_t = torch.tensor(ret, dtype=torch.float32)

        # Normalize advantages
        adv_t = (adv_t - adv_t.mean()) / (adv_t.std() + 1e-8)

        for _ in range(self.cfg_ppo["epochs"]):
            q, logits, value_t = self.net(states_t)
            probs = torch.softmax(logits, dim=1)
            dist = torch.distributions.Categorical(probs)

            new_log_probs_t = dist.log_prob(actions_t)
            entropy = dist.entropy().mean()

            ratio = torch.exp(new_log_probs_t - old_log_probs_t)
            surr1 = ratio * adv_t
            surr2 = torch.clamp(ratio, 1.0 - self.cfg_ppo["clip_eps"],
                                1.0 + self.cfg_ppo["clip_eps"]) * adv_t
            policy_loss = -torch.min(surr1, surr2).mean()

            value_loss = (ret_t - value_t.squeeze(1)).pow(2).mean()

            loss = policy_loss + self.cfg_ppo["value_coef"] * value_loss - \
                   self.cfg_ppo["entropy_coef"] * entropy

            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()

# --- Main loop: Teacher running in chosen profile ------------------------------

def main(config_path=None):
    # Load config (optional external file)
    cfg = DEFAULT_CONFIG.copy()
    if config_path and os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            # shallow merge
            for k in user_cfg:
                if k in cfg and isinstance(cfg[k], dict):
                    cfg[k].update(user_cfg[k])
                else:
                    cfg[k] = user_cfg[k]
            log(f"Loaded config from {config_path}")
        except Exception as e:
            log(f"Failed to load config: {e}")

    profile_name = cfg["general"]["profile"]
    profile_cfg = cfg["profiles"].get(profile_name, cfg["profiles"]["flow"])
    log(f"Starting ULTRABORG TEACHER in profile: {profile_name} ({profile_cfg['name']})")
    log(f"OS: {platform.system()} {platform.release()}")

    if torch is None or np is None:
        log("torch or numpy not available – cannot run hybrid agent.")
        sys.exit(1)

    agent = HybridAgent(cfg)

    sensor_interval = cfg["general"]["sensor_interval"]
    max_steps = cfg["general"]["max_steps"]

    # PPO trajectory buffer
    traj_states = []
    traj_actions = []
    traj_log_probs = []
    traj_rewards = []
    traj_values = []
    traj_dones = []

    snapshot = read_sensors()
    done = False

    for step in range(max_steps):
        agent.step_count += 1

        # Alternate between DQN and PPO decisions (hybrid flavor)
        use_dqn = (step % 2 == 0)
        action_idx, log_prob, value = agent.select_action(snapshot, use_dqn=use_dqn)
        action_name = ACTION_SPACE[action_idx]

        apply_action_placeholder(action_name)

        time.sleep(sensor_interval)
        next_snapshot = read_sensors()

        reward = compute_reward(next_snapshot, profile_cfg)
        done = False  # continuous task; you can add episode logic if desired

        # DQN transition
        s_t = agent.state_to_tensor(snapshot).squeeze(0)
        s_next_t = agent.state_to_tensor(next_snapshot).squeeze(0)
        agent.store_dqn_transition(s_t, action_idx, reward, s_next_t, done)
        agent.dqn_update()

        # PPO trajectory (only when using PPO)
        if not use_dqn and log_prob is not None:
            traj_states.append(agent.state_to_tensor(snapshot).squeeze(0))
            traj_actions.append(action_idx)
            traj_log_probs.append(log_prob)
            traj_rewards.append(reward)
            traj_values.append(value)
            traj_dones.append(0.0 if not done else 1.0)

        # Periodically run PPO update
        if len(traj_states) >= cfg["ppo"]["batch_size"]:
            trajectory = Trajectory(
                states=traj_states,
                actions=traj_actions,
                log_probs=traj_log_probs,
                rewards=traj_rewards,
                values=traj_values,
                dones=traj_dones,
            )
            agent.ppo_update(trajectory)
            traj_states.clear()
            traj_actions.clear()
            traj_log_probs.clear()
            traj_rewards.clear()
            traj_values.clear()
            traj_dones.clear()
            log("[PPO] Updated policy from trajectory batch.")

        snapshot = next_snapshot

        if step % 50 == 0:
            log(f"Step {step}: reward={reward:.3f}, fps={snapshot.fps:.1f}, "
                f"cpu={snapshot.cpu_usage:.2f}, gpu={snapshot.gpu_usage:.2f}, "
                f"temp={snapshot.gpu_temp:.1f}°C, thermal={snapshot.thermal_pressure:.2f}")

    log("ULTRABORG TEACHER finished main loop.")

if __name__ == "__main__":
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else None
    main(cfg_path)
