#!/usr/bin/env python3
# ULTRABORG HYBRID TEACHER (DQN + PPO) - SINGLE FILE GOD MODE

import os
import sys
import time
import math
import random
import platform
import subprocess

# ---------------------------------------------------------------------------
# AUTOLOADER FOR LIBRARIES
# ---------------------------------------------------------------------------

REQUIRED_LIBS = [
    "psutil",
    "numpy",
    "torch",
]

OPTIONAL_LIBS = [
    "pynvml",   # GPU metrics (NVIDIA)
    "requests"  # if you later add web API
]

def ensure_lib(name):
    try:
        __import__(name)
        return True
    except ImportError:
        print(f"[ULTRABORG] Missing library: {name}. Attempting install via pip...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", name])
            __import__(name)
            print(f"[ULTRABORG] Installed {name}.")
            return True
        except Exception as e:
            print(f"[ULTRABORG] Failed to install {name}: {e}")
            return False

for lib in REQUIRED_LIBS:
    ensure_lib(lib)

for lib in OPTIONAL_LIBS:
    ensure_lib(lib)

import psutil
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

# Optional imports guarded
try:
    import pynvml
    NVML_AVAILABLE = True
    pynvml.nvmlInit()
except Exception:
    NVML_AVAILABLE = False

# ---------------------------------------------------------------------------
# SENSOR LAYER (CPU / RAM / GPU / FPS / TEMPS)
# ---------------------------------------------------------------------------

def read_cpu_usage():
    return psutil.cpu_percent(interval=0.1)

def read_ram_usage():
    mem = psutil.virtual_memory()
    return mem.percent

def read_gpu_usage():
    if not NVML_AVAILABLE:
        return 0.0
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        return float(util.gpu)
    except Exception:
        return 0.0

def read_gpu_temp():
    if not NVML_AVAILABLE:
        return 0.0
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        return float(temp)
    except Exception:
        return 0.0

def read_fps_overlay():
    """
    Placeholder: integrate with your real FPS overlay / game hook.
    For now, we simulate FPS with a noisy value.
    """
    base_fps = 90.0
    noise = np.random.normal(0, 5)
    return max(10.0, base_fps + noise)

def read_system_state():
    """
    Returns a normalized state vector:
    [cpu, ram, gpu, gpu_temp, fps]
    All scaled to roughly 0..1 for RL.
    """
    cpu = read_cpu_usage() / 100.0
    ram = read_ram_usage() / 100.0
    gpu = read_gpu_usage() / 100.0
    gpu_temp = read_gpu_temp() / 100.0  # assume 0..100°C
    fps = read_fps_overlay() / 240.0    # assume 240 FPS max target

    return np.array([cpu, ram, gpu, gpu_temp, fps], dtype=np.float32)

# ---------------------------------------------------------------------------
# MODES: FLOW / DEEP WORK / RECOVERY
# ---------------------------------------------------------------------------

MODE_FLOW = 0
MODE_DEEP_WORK = 1
MODE_RECOVERY = 2

def mode_name(mode):
    return {MODE_FLOW: "FLOW", MODE_DEEP_WORK: "DEEP_WORK", MODE_RECOVERY: "RECOVERY"}.get(mode, "UNKNOWN")

def compute_reward(state, mode):
    """
    Reward philosophy:
    - FLOW: stable FPS, moderate CPU/GPU, safe temps.
    - DEEP_WORK: prioritize stability + low interruptions (low spikes).
    - RECOVERY: low load, temps cooling down.
    """
    cpu, ram, gpu, gpu_temp, fps = state

    # Basic penalties
    temp_penalty = max(0.0, gpu_temp - 0.7) * 2.0  # punish >70°C
    high_cpu_penalty = max(0.0, cpu - 0.85) * 1.5
    high_ram_penalty = max(0.0, ram - 0.90) * 1.5

    # FPS stability: reward being near target band
    target_fps = 90.0 / 240.0
    fps_diff = abs(fps - target_fps)
    fps_reward = max(0.0, 0.3 - fps_diff) * 3.0  # reward if within ±0.3 normalized

    # Mode-specific weighting
    if mode == MODE_FLOW:
        reward = fps_reward - temp_penalty - high_cpu_penalty - high_ram_penalty
    elif mode == MODE_DEEP_WORK:
        # emphasize stability and low spikes
        reward = fps_reward * 0.5 - temp_penalty * 1.5 - high_cpu_penalty * 1.5 - high_ram_penalty
    elif mode == MODE_RECOVERY:
        # reward low load and cooling
        reward = (1.0 - cpu) + (1.0 - gpu) + (1.0 - gpu_temp) - high_ram_penalty
    else:
        reward = -1.0

    return float(reward)

# ---------------------------------------------------------------------------
# ACTION SPACE: SYSTEM TUNING (ABSTRACTED)
# ---------------------------------------------------------------------------

ACTIONS = [
    "NO_OP",
    "THROTTLE_BACKGROUND_PROCESSES",
    "BOOST_FOREGROUND_PRIORITY",
    "LOWER_GPU_POWER_LIMIT",
    "RAISE_GPU_POWER_LIMIT",
    "LOWER_GRAPHICS_QUALITY",
    "RAISE_GRAPHICS_QUALITY",
]

ACTION_COUNT = len(ACTIONS)

def apply_action(action_idx):
    """
    Placeholder for real system hooks:
    - Process priority changes
    - GPU power limit adjustments
    - Graphics quality toggles via game APIs
    For now, we just log.
    """
    action_name = ACTIONS[action_idx]
    print(f"[ULTRABORG] Applying action: {action_name}")
    # TODO: integrate with real OS hooks, game APIs, etc.

# ---------------------------------------------------------------------------
# HYBRID NETWORK: SHARED BODY + DQN HEAD + PPO HEAD
# ---------------------------------------------------------------------------

class SharedBody(nn.Module):
    def __init__(self, state_dim, hidden_dim=128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
        )

    def forward(self, x):
        return self.net(x)

class DQNHead(nn.Module):
    def __init__(self, hidden_dim, action_dim):
        super().__init__()
        self.q = nn.Linear(hidden_dim, action_dim)

    def forward(self, features):
        return self.q(features)

class PPOHead(nn.Module):
    def __init__(self, hidden_dim, action_dim):
        super().__init__()
        self.policy = nn.Linear(hidden_dim, action_dim)
        self.value = nn.Linear(hidden_dim, 1)

    def forward(self, features):
        logits = self.policy(features)
        value = self.value(features)
        return logits, value

class HybridAgent(nn.Module):
    def __init__(self, state_dim, action_dim):
        super().__init__()
        self.body = SharedBody(state_dim)
        self.dqn_head = DQNHead(128, action_dim)
        self.ppo_head = PPOHead(128, action_dim)

    def forward(self, state_tensor):
        features = self.body(state_tensor)
        q_values = self.dqn_head(features)
        logits, value = self.ppo_head(features)
        return q_values, logits, value

# ---------------------------------------------------------------------------
# RL UTILITIES
# ---------------------------------------------------------------------------

def select_action(agent, state, epsilon=0.1):
    """
    Hybrid selection:
    - Use DQN Q-values with epsilon-greedy.
    - PPO logits can be used to bias exploration later.
    """
    state_t = torch.from_numpy(state).float().unsqueeze(0)
    with torch.no_grad():
        q_values, logits, _ = agent(state_t)
    if random.random() < epsilon:
        return random.randint(0, ACTION_COUNT - 1)
    else:
        return int(torch.argmax(q_values, dim=1).item())

# Simple replay buffer for DQN
class ReplayBuffer:
    def __init__(self, capacity=10000):
        self.capacity = capacity
        self.buffer = []
        self.pos = 0

    def push(self, s, a, r, ns, done):
        if len(self.buffer) < self.capacity:
            self.buffer.append(None)
        self.buffer[self.pos] = (s, a, r, ns, done)
        self.pos = (self.pos + 1) % self.capacity

    def sample(self, batch_size):
        batch = random.sample(self.buffer, batch_size)
        s, a, r, ns, d = map(np.array, zip(*batch))
        return s, a, r, ns, d

    def __len__(self):
        return len(self.buffer)

# ---------------------------------------------------------------------------
# TRAINING LOOP (SIMPLIFIED, EXTENDABLE)
# ---------------------------------------------------------------------------

def train_hybrid_agent(
    episodes=1000,
    max_steps=200,
    mode=MODE_FLOW,
    gamma=0.99,
    dqn_lr=1e-3,
    ppo_lr=1e-4,
    batch_size=64
):
    state_dim = 5
    action_dim = ACTION_COUNT

    agent = HybridAgent(state_dim, action_dim)
    dqn_optimizer = optim.Adam(list(agent.body.parameters()) + list(agent.dqn_head.parameters()), lr=dqn_lr)
    ppo_optimizer = optim.Adam(agent.ppo_head.parameters(), lr=ppo_lr)

    replay = ReplayBuffer(capacity=50000)

    for ep in range(episodes):
        state = read_system_state()
        ep_reward = 0.0

        for step in range(max_steps):
            action_idx = select_action(agent, state, epsilon=max(0.05, 0.3 - ep * 0.0002))
            apply_action(action_idx)

            next_state = read_system_state()
            reward = compute_reward(next_state, mode)
            done = False  # you can define terminal conditions (e.g., overheating)

            replay.push(state, action_idx, reward, next_state, done)
            ep_reward += reward
            state = next_state

            # DQN update
            if len(replay) >= batch_size:
                s, a, r, ns, d = replay.sample(batch_size)
                s_t = torch.from_numpy(s).float()
                ns_t = torch.from_numpy(ns).float()
                a_t = torch.from_numpy(a).long()
                r_t = torch.from_numpy(r).float()
                d_t = torch.from_numpy(d.astype(np.float32)).float()

                q_values, _, _ = agent(s_t)
                q_value = q_values.gather(1, a_t.unsqueeze(1)).squeeze(1)

                with torch.no_grad():
                    next_q_values, _, _ = agent(ns_t)
                    next_q = next_q_values.max(1)[0]
                    target = r_t + gamma * next_q * (1.0 - d_t)

                dqn_loss = nn.MSELoss()(q_value, target)
                dqn_optimizer.zero_grad()
                dqn_loss.backward()
                dqn_optimizer.step()

            # PPO update (stub: you can add full trajectory-based PPO later)
            # Here we just do a simple policy gradient-like step for demonstration.
            # For real PPO, you need advantages, old logits, clipping, etc.
            # This is intentionally lightweight.
            # ------------------------------------------------------------

        print(f"[ULTRABORG] Episode {ep+1}/{episodes} | Mode={mode_name(mode)} | Reward={ep_reward:.3f}")

    return agent

# ---------------------------------------------------------------------------
# LIVE CONTROL LOOP (RUNTIME GOVERNOR)
# ---------------------------------------------------------------------------

def live_governor(agent, mode=MODE_FLOW, interval=1.0):
    print(f"[ULTRABORG] Live governor started in mode: {mode_name(mode)}")
    try:
        while True:
            state = read_system_state()
            action_idx = select_action(agent, state, epsilon=0.05)
            apply_action(action_idx)
            reward = compute_reward(state, mode)
            print(f"[ULTRABORG] State={state}, Reward={reward:.3f}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("[ULTRABORG] Live governor stopped by user.")

# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

def main():
    print("[ULTRABORG] OS:", platform.system(), platform.release())
    print("[ULTRABORG] NVML available:", NVML_AVAILABLE)

    # Choose initial mode (you can wire this to your UI / CLI)
    mode = MODE_FLOW

    # Train agent (or load from disk if you have a saved model)
    agent = train_hybrid_agent(
        episodes=50,      # keep small for testing; increase later
        max_steps=50,
        mode=mode
    )

    # Start live governor
    live_governor(agent, mode=mode, interval=2.0)

if __name__ == "__main__":
    main()
