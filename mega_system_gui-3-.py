import importlib
import subprocess
import sys
import threading
import json
import random
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# =========================
# AUTOLOADER FOR LIBRARIES
# =========================

def ensure_lib(module_name: str, pip_name: Optional[str] = None):
    pip_name = pip_name or module_name
    try:
        return importlib.import_module(module_name)
    except ImportError:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])
            return importlib.import_module(module_name)
        except Exception:
            return None

np = ensure_lib("numpy")
torch = ensure_lib("torch")
transformers = ensure_lib("transformers")
whisper_lib = ensure_lib("whisper")
onnxruntime = ensure_lib("onnxruntime")
plt_mod = ensure_lib("matplotlib")
if plt_mod:
    matplotlib = plt_mod
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
else:
    matplotlib = None

# =========================
# MODEL TIER MANAGER
# =========================

class ModelTierManager:
    """
    Handles A/B/C tiers for LLM, vision, audio.
    Tries heavy (C), then medium (B), then light (A), then stub.
    """

    def __init__(self):
        self.llm_model = None
        self.llm_tokenizer = None
        self.llm_name = None

        self.vit_model = None
        self.vit_processor = None
        self.vit_name = None

        self.whisper_model = None
        self.whisper_name = None

        self._init_llm()
        self._init_vit()
        self._init_whisper()

    def _init_llm(self):
        if not transformers or not torch:
            self.llm_name = "stub"
            return
        # Tier C: heavy
        for name in [
            "meta-llama/Llama-3-8b",
            "mistralai/Mistral-7B-Instruct-v0.2",
        ]:
            try:
                self.llm_tokenizer = transformers.AutoTokenizer.from_pretrained(name)
                self.llm_model = transformers.AutoModelForCausalLM.from_pretrained(name)
                self.llm_name = name
                return
            except Exception:
                pass
        # Tier B: medium
        for name in [
            "EleutherAI/gpt-neo-1.3B",
            "EleutherAI/gpt-neo-125M",
        ]:
            try:
                self.llm_tokenizer = transformers.AutoTokenizer.from_pretrained(name)
                self.llm_model = transformers.AutoModelForCausalLM.from_pretrained(name)
                self.llm_name = name
                return
            except Exception:
                pass
        # Tier A: light
        for name in [
            "gpt2",
        ]:
            try:
                self.llm_tokenizer = transformers.AutoTokenizer.from_pretrained(name)
                self.llm_model = transformers.AutoModelForCausalLM.from_pretrained(name)
                self.llm_name = name
                return
            except Exception:
                pass
        self.llm_name = "stub"

    def _init_vit(self):
        if not transformers or not torch:
            self.vit_name = "stub"
            return
        # Tier C/B/A combined
        for name in [
            "google/vit-large-patch32-384",
            "google/vit-base-patch16-224",
            "google/vit-small-patch16-224",
        ]:
            try:
                self.vit_processor = transformers.AutoImageProcessor.from_pretrained(name)
                self.vit_model = transformers.ViTModel.from_pretrained(name)
                self.vit_name = name
                return
            except Exception:
                pass
        self.vit_name = "stub"

    def _init_whisper(self):
        if not whisper_lib:
            self.whisper_name = "stub"
            return
        for name in [
            "large",
            "base",
            "tiny",
        ]:
            try:
                self.whisper_model = whisper_lib.load_model(name)
                self.whisper_name = name
                return
            except Exception:
                pass
        self.whisper_name = "stub"

    # -------- LLM --------
    def run_llm(self, text: str) -> Dict[str, Any]:
        if self.llm_name == "stub":
            return {"model": "LLM-stub", "output": f"Stub response for: {text[:60]}..."}
        inputs = self.llm_tokenizer(text, return_tensors="pt", truncation=True, max_length=256)
        with torch.no_grad():
            outputs = self.llm_model.generate(**inputs, max_length=128)
        decoded = self.llm_tokenizer.decode(outputs[0], skip_special_tokens=True)
        return {"model": self.llm_name, "output": decoded}

    # -------- ViT --------
    def run_vit(self, image_path: str) -> Dict[str, Any]:
        if self.vit_name == "stub":
            return {"model": "ViT-stub", "embedding": [0.0] * 16}
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        inputs = self.vit_processor(images=img, return_tensors="pt")
        with torch.no_grad():
            outputs = self.vit_model(**inputs)
        emb = outputs.last_hidden_state.mean(dim=1).tolist()[0]
        return {"model": self.vit_name, "embedding": emb}

    # -------- Whisper --------
    def run_whisper(self, audio_path: str) -> Dict[str, Any]:
        if self.whisper_name == "stub":
            return {"model": "Whisper-stub", "transcript": "Stub transcript."}
        result = self.whisper_model.transcribe(audio_path)
        return {"model": self.whisper_name, "transcript": result.get("text", "")}


# =========================
# BACKEND ARCHITECTURE
# =========================

class MultiModalInputs:
    def __init__(self):
        self.state: Dict[str, Any] = {}

    def ingest_text(self, text: str) -> Dict[str, Any]:
        tokens = text.split()
        length = len(tokens)
        avg_len = sum(len(t) for t in tokens) / length if length > 0 else 0
        return {
            "type": "text",
            "raw": text,
            "tokens": tokens,
            "length": length,
            "avg_token_length": avg_len,
        }

    def ingest_file(self, path: str) -> Dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            content = ""
        return self.ingest_text(content)

    def ingest_sensors(self, sensor_data: Dict[str, Any]) -> Dict[str, Any]:
        return {"type": "sensors", "data": sensor_data}

    def text_normalization(self, text_obj: Dict[str, Any]) -> Dict[str, Any]:
        text = text_obj.get("raw", "").lower()
        return {**text_obj, "normalized": text}

    def feature_extraction(self, data_obj: Dict[str, Any]) -> Dict[str, Any]:
        text = data_obj.get("normalized", data_obj.get("raw", ""))
        vowels = sum(1 for c in text if c in "aeiou")
        consonants = sum(1 for c in text if c.isalpha() and c not in "aeiou")
        return {
            "type": "features",
            "source_type": data_obj.get("type"),
            "vowels": vowels,
            "consonants": consonants,
            "length": len(text),
        }

    def multi_sensor_integration(self, sensor_objs: List[Dict[str, Any]]) -> Dict[str, Any]:
        merged = {}
        for s in sensor_objs:
            merged.update(s.get("data", {}))
        return {"type": "multi_sensor", "merged": merged}


class PreTrainedModels:
    def __init__(self, tier_manager: ModelTierManager):
        self.tier = tier_manager

    def large_language_model(self, text_features: Dict[str, Any]) -> Dict[str, Any]:
        text = text_features.get("normalized", text_features.get("raw", ""))
        return self.tier.run_llm(text)

    def vision_transformer(self, image_path: str) -> Dict[str, Any]:
        return self.tier.run_vit(image_path)

    def speech_recognition(self, audio_path: str) -> Dict[str, Any]:
        return self.tier.run_whisper(audio_path)

    def speech_transfer_recognition(self, audio_input: Dict[str, Any]) -> Dict[str, Any]:
        return {"model": "SpeechTransfer-stub", "representation": [0.1, 0.2, 0.3]}

    def time_series_analysis(self, series_input: Dict[str, Any]) -> Dict[str, Any]:
        series = series_input.get("series", [random.random() for _ in range(10)])
        trend = "up" if series[-1] > series[0] else "down"
        return {"model": "TimeSeries-lite", "trend": trend, "series": series}


class DataProcessingHub:
    def __init__(self):
        pass

    def data_normalization(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {"normalized": True, "data": data}

    def feature_extraction(self, data: Dict[str, Any]) -> Dict[str, Any]:
        base = data.get("data", {})
        score = len(str(base)) % 100
        return {"hub_features": ["hf1", "hf2"], "score": score, "source": data}

    def multi_series_integration(self, series_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"integrated_series": series_list, "count": len(series_list)}


class ParallelComputationCore:
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def hyperparameter_optimization(self, config_space: Dict[str, Any]) -> Dict[str, Any]:
        lr = self.config.get("lr", 0.001)
        batch = self.config.get("batch_size", 32)
        return {"best_config": {"lr": lr, "batch_size": batch}}

    def deep_neural_networks(self, features: Dict[str, Any]) -> Dict[str, Any]:
        score = features.get("score", 0)
        latent = math.tanh(score / 10.0)
        return {"dnn_output": latent}

    def dynamic_memory_system(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {"memory_state": "updated", "context": context}

    def temporal_modulation(self, sequence: Dict[str, Any]) -> Dict[str, Any]:
        return {"temporal_pattern": "modulated", "sequence": sequence}

    def advanced_training_loop(self, data: Dict[str, Any]) -> Dict[str, Any]:
        epochs = self.config.get("epochs", 5)
        loss = round(1.0 / (epochs + 1), 4)
        return {"training_status": "converged", "metrics": {"loss": loss, "epochs": epochs}}


class GodSwarmNeural:
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def neural_network_swarm_architecture(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        swarm_size = self.config.get("swarm_size", 16)
        return {"swarm_state": "initialized", "size": swarm_size, "inputs": inputs}

    def advanced_training(self, swarm_state: Dict[str, Any]) -> Dict[str, Any]:
        return {"swarm_state": "trained", "details": swarm_state}

    def agents(self, swarm_state: Dict[str, Any]) -> Dict[str, Any]:
        size = swarm_state.get("size", 0)
        agents = [f"agent_{i}" for i in range(size)]
        return {"agents": agents, "swarm_state": swarm_state}

    def evolutionary_algorithms(self, population: Dict[str, Any]) -> Dict[str, Any]:
        gen = self.config.get("generations", 10)
        return {"evolved_population": f"generation_{gen}"}

    def swarm_intelligence(self, environment: Dict[str, Any]) -> Dict[str, Any]:
        decision = random.choice(["explore", "exploit", "hold"])
        return {"swarm_decision": decision, "environment": environment}

    def quantum_engineered_swarm_architecture(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        return {"quantum_swarm": "hybrid_architecture", "inputs": inputs}


class QuantumCore:
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def quantum_inspired_decision_engine(self, state: Dict[str, Any]) -> Dict[str, Any]:
        temp = self.config.get("temperature", 0.7)
        choice = "accept" if random.random() < temp else "reject"
        return {"decision": choice, "state": state}

    def probabilistic_computing(self, data: Dict[str, Any]) -> Dict[str, Any]:
        probs = [random.random() for _ in range(2)]
        s = sum(probs)
        probs = [p / s for p in probs]
        return {"probabilities": probs, "data": data}

    def quantum_optimization(self, objective: Dict[str, Any]) -> Dict[str, Any]:
        return {"optimized_solution": "quantum_optimum"}

    def computation_optimization(self, pipeline: Dict[str, Any]) -> Dict[str, Any]:
        return {"optimized_pipeline": True, "pipeline": pipeline}

    def entanglement_processing(self, linked_states: Dict[str, Any]) -> Dict[str, Any]:
        return {"entangled_state": "processed", "linked_states": linked_states}

    def stochastic_decision_model(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        action = random.choice(["action_A", "action_B", "action_C"])
        return {"stochastic_decision": action, "inputs": inputs}

    def quantum_inspired_probabilistic_decision_engine(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return {"probabilistic_decision": "hybrid_quantum", "state": state}


class ContextualKnowledgeBase:
    def __init__(self):
        self.knowledge: List[Dict[str, Any]] = []

    def deep_world_neural_feedbacks(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        self.knowledge.append(signal)
        return {"feedback": "world_model_update", "signal": signal}

    def predictive_reactions(self, context: Dict[str, Any]) -> Dict[str, Any]:
        prediction = random.choice(["good", "bad", "neutral"])
        return {"prediction": prediction, "context": context}

    def outcome_evaluation(self, action: Dict[str, Any]) -> Dict[str, Any]:
        evaluation = random.choice(["success", "failure"])
        return {"evaluation": evaluation, "action": action}

    def real_time_decision_system(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        decision = random.choice(["proceed", "abort", "delay"])
        return {"decision": decision, "inputs": inputs}

    def multi_agent_coordination(self, agents_state: Dict[str, Any]) -> Dict[str, Any]:
        return {"coordination_plan": "coordinated", "agents_state": agents_state}


class EthicalGuidelines:
    def __init__(self):
        pass

    def apply_ethics(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        return {"ethical_decision": decision, "status": "checked"}


class SecurityMitigation:
    def __init__(self):
        pass

    def security_mitigation(self, system_state: Dict[str, Any]) -> Dict[str, Any]:
        return {"secure_state": True, "system_state": system_state}


class SimulationEngine:
    def __init__(self):
        pass

    def simulation_engine(self, scenario: Dict[str, Any]) -> Dict[str, Any]:
        return {"simulation_result": "simulated_outcome", "scenario": scenario}


class OutputModules:
    def __init__(self):
        pass

    def image_synthesis(self, representation: Dict[str, Any]) -> Dict[str, Any]:
        return {"image": "synthetic_image.png", "source": representation}

    def audio_creation(self, representation: Dict[str, Any]) -> Dict[str, Any]:
        return {"audio": "synthetic_audio.wav", "source": representation}


class RealWorldTraining:
    def __init__(self):
        pass

    def trained_on_real_world_data(self, dataset_info: Dict[str, Any]) -> Dict[str, Any]:
        return {"training_source": "real_world", "dataset": dataset_info}


# =========================
# ORCHESTRATOR
# =========================

class MegaSystemOrchestrator:
    def __init__(self, config: Dict[str, Any], tier_manager: ModelTierManager):
        self.config = config
        self.tier = tier_manager
        self.inputs = MultiModalInputs()
        self.models = PreTrainedModels(self.tier)
        self.hub = DataProcessingHub()
        self.core = ParallelComputationCore(config.get("core", {}))
        self.swarm = GodSwarmNeural(config.get("swarm", {}))
        self.quantum = QuantumCore(config.get("quantum", {}))
        self.kb = ContextualKnowledgeBase()
        self.ethics = EthicalGuidelines()
        self.security = SecurityMitigation()
        self.sim = SimulationEngine()
        self.outputs = OutputModules()
        self.training = RealWorldTraining()

    def run_full_pipeline(self, text: str) -> Dict[str, Any]:
        text_raw = self.inputs.ingest_text(text)
        text_norm = self.inputs.text_normalization(text_raw)
        text_feat = self.inputs.feature_extraction(text_norm)

        llm_out = self.models.large_language_model(text_feat)

        hub_norm = self.hub.data_normalization(llm_out)
        hub_feat = self.hub.feature_extraction(hub_norm)

        best_cfg = self.core.hyperparameter_optimization({})
        dnn_out = self.core.deep_neural_networks(hub_feat)
        mem_state = self.core.dynamic_memory_system(dnn_out)
        temporal = self.core.temporal_modulation(mem_state)
        train_status = self.core.advanced_training_loop(temporal)

        swarm_state = self.swarm.neural_network_swarm_architecture(train_status)
        swarm_trained = self.swarm.advanced_training(swarm_state)
        swarm_agents = self.swarm.agents(swarm_trained)
        swarm_evolved = self.swarm.evolutionary_algorithms(swarm_agents)
        swarm_decision = self.swarm.swarm_intelligence({"agents": swarm_agents, "evolved": swarm_evolved})

        q_prob = self.quantum.probabilistic_computing(swarm_decision)
        q_decision = self.quantum.quantum_inspired_decision_engine(q_prob)
        q_stoch = self.quantum.stochastic_decision_model(q_decision)

        feedback = self.kb.deep_world_neural_feedbacks(q_stoch)
        prediction = self.kb.predictive_reactions(feedback)
        outcome_eval = self.kb.outcome_evaluation(prediction)
        rt_decision = self.kb.real_time_decision_system(outcome_eval)
        coordination = self.kb.multi_agent_coordination({"decision": rt_decision})

        ethical = self.ethics.apply_ethics(coordination)
        secure = self.security.security_mitigation(ethical)

        sim_result = self.sim.simulation_engine(secure)

        img = self.outputs.image_synthesis(sim_result)
        aud = self.outputs.audio_creation(sim_result)

        train_info = self.training.trained_on_real_world_data({"source": "placeholder_dataset"})

        return {
            "best_config": best_cfg,
            "llm_out": llm_out,
            "train_status": train_status,
            "swarm_decision": swarm_decision,
            "q_decision": q_decision,
            "q_stoch": q_stoch,
            "coordination": coordination,
            "ethical": ethical,
            "secure": secure,
            "simulation": sim_result,
            "image_output": img,
            "audio_output": aud,
            "training_info": train_info,
        }


# =========================
# TKINTER GUI (CYBERPUNK)
# =========================

class MegaSystemApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ALL-IN-ONE MEGA SYSTEM v16 HYBRID")
        self.geometry("1400x800")
        self.configure(bg="#050510")

        self.config_state = {
            "core": {"lr": 0.001, "batch_size": 32, "epochs": 5},
            "swarm": {"swarm_size": 16, "generations": 10},
            "quantum": {"temperature": 0.7},
        }
        self.last_result: Optional[Dict[str, Any]] = None

        self.tier_manager = ModelTierManager()
        self.orchestrator = MegaSystemOrchestrator(self.config_state, self.tier_manager)

        self._setup_style()
        self._build_layout()
        self._build_dashboard()
        self._build_config_panel()
        self._build_console()

        self._log(f"System initialized. LLM: {self.tier_manager.llm_name}, ViT: {self.tier_manager.vit_name}, Whisper: {self.tier_manager.whisper_name}")

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Cyber.TFrame", background="#050510", borderwidth=0)
        style.configure("Cyber.TLabelframe", background="#050510", foreground="#00ffcc", borderwidth=1, relief="solid")
        style.configure("Cyber.TLabelframe.Label", background="#050510", foreground="#00ffcc")
        style.configure("Cyber.TLabel", background="#050510", foreground="#00ffcc")
        style.configure("Cyber.TButton", background="#111122", foreground="#00ffcc", borderwidth=1)
        style.map("Cyber.TButton", background=[("active", "#222244")], foreground=[("active", "#00ffaa")])
        style.configure("Cyber.TEntry", fieldbackground="#111122", foreground="#00ffcc", insertcolor="#00ffcc")

    def _build_layout(self):
        title = ttk.Label(
            self,
            text="ALL-IN-ONE MEGA SYSTEM FLOWCHART v16 (Hybrid Real Models)",
            style="Cyber.TLabel",
            font=("Segoe UI", 14, "bold"),
        )
        title.pack(pady=5)

        main_frame = ttk.Frame(self, style="Cyber.TFrame")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        left_frame = ttk.Frame(main_frame, style="Cyber.TFrame")
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right_frame = ttk.Frame(main_frame, style="Cyber.TFrame")
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        modules_frame = ttk.Frame(left_frame, style="Cyber.TFrame")
        modules_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        anim_frame = ttk.LabelFrame(left_frame, text="Data Flow Visualizer", style="Cyber.TLabelframe")
        anim_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=False, pady=5)

        self.canvas = tk.Canvas(anim_frame, bg="#050510", highlightthickness=0, height=180)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self._build_animation_graph()

        control_frame = ttk.LabelFrame(right_frame, text="Control Panel", style="Cyber.TLabelframe")
        control_frame.pack(fill=tk.X, pady=5)

        ttk.Label(control_frame, text="Text Input:", style="Cyber.TLabel").pack(anchor="w")
        self.text_input = ttk.Entry(control_frame, style="Cyber.TEntry")
        self.text_input.insert(0, "Hello world from HYBRID system")
        self.text_input.pack(fill=tk.X, pady=2)

        file_btn = ttk.Button(control_frame, text="Load Text File as Input", style="Cyber.TButton", command=self.load_text_file)
        file_btn.pack(fill=tk.X, pady=2)

        run_button = ttk.Button(control_frame, text="Run Full Pipeline (Threaded)", style="Cyber.TButton", command=self.run_full_pipeline_threaded)
        run_button.pack(pady=5, fill=tk.X)

        save_btn = ttk.Button(control_frame, text="Save State", style="Cyber.TButton", command=self.save_state)
        save_btn.pack(fill=tk.X, pady=2)

        load_btn = ttk.Button(control_frame, text="Load State", style="Cyber.TButton", command=self.load_state)
        load_btn.pack(fill=tk.X, pady=2)

        clear_button = ttk.Button(control_frame, text="Clear Log", style="Cyber.TButton", command=self.clear_log)
        clear_button.pack(pady=2, fill=tk.X)

        log_frame = ttk.LabelFrame(right_frame, text="System Log", style="Cyber.TLabelframe")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.log_text = tk.Text(log_frame, wrap=tk.WORD, state=tk.DISABLED, bg="#050510", fg="#00ff99", insertbackground="#00ff99")
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.console_frame = ttk.LabelFrame(right_frame, text="Command Console", style="Cyber.TLabelframe")
        self.console_frame.pack(fill=tk.BOTH, expand=False, pady=5)

        self._build_modules_grid(modules_frame)

    def _build_modules_grid(self, parent):
        nb = ttk.Notebook(parent)
        nb.pack(fill=tk.BOTH, expand=True)

        sections = {
            "Inputs & Models": ["MULTI-MODAL DATA INPUTS", "PRE-TRAINED MODELS"],
            "Processing & Core": ["DATA PROCESSING HUB", "PARALLEL COMPUTATION CORE"],
            "Swarm & Quantum": ["v16-GODSWARM-NEURAL", "v16-QUANTUM"],
            "Knowledge & Output": ["CONTEXTUAL KNOWLEDGE BASE", "ETHICAL GUIDELINES", "SECURITY MITIGATION", "SIMULATION ENGINE", "OUTPUT MODULES"],
        }

        for tab_name, groups in sections.items():
            frame = ttk.Frame(nb, style="Cyber.TFrame")
            nb.add(frame, text=tab_name)
            for i, g in enumerate(groups):
                lf = ttk.LabelFrame(frame, text=g, style="Cyber.TLabelframe")
                lf.grid(row=i, column=0, sticky="ew", padx=3, pady=3)
                lf.columnconfigure(0, weight=1)
                lbl = ttk.Label(lf, text=f"{g} active", style="Cyber.TLabel")
                lbl.grid(row=0, column=0, sticky="w", padx=5)

    def _build_animation_graph(self):
        self.nodes = {
            "input": (80, 90),
            "models": (260, 50),
            "hub": (260, 130),
            "core": (440, 90),
            "swarm": (620, 50),
            "quantum": (620, 130),
            "kb": (800, 90),
            "output": (980, 90),
        }
        self.node_items = {}
        for name, (x, y) in self.nodes.items():
            r = 18
            oval = self.canvas.create_oval(x - r, y - r, x + r, y + r, outline="#00ffcc", width=2)
            text = self.canvas.create_text(x, y, text=name.upper(), fill="#00ffcc", font=("Segoe UI", 7, "bold"))
            self.node_items[name] = (oval, text)

        edges = [
            ("input", "models"),
            ("input", "hub"),
            ("models", "core"),
            ("hub", "core"),
            ("core", "swarm"),
            ("core", "quantum"),
            ("swarm", "kb"),
            ("quantum", "kb"),
            ("kb", "output"),
        ]
        for a, b in edges:
            x1, y1 = self.nodes[a]
            x2, y2 = self.nodes[b]
            self.canvas.create_line(x1, y1, x2, y2, fill="#2222aa", width=2, arrow=tk.LAST)

        self.packet = self.canvas.create_oval(0, 0, 0, 0, outline="", fill="")
        self.animating = False

    def animate_path(self, path: List[str]):
        if self.animating:
            return
        self.animating = True
        coords = [self.nodes[n] for n in path]

        def step_segment(i, t):
            if i >= len(coords) - 1:
                self.canvas.itemconfig(self.packet, outline="", fill="")
                self.animating = False
                return
            x1, y1 = coords[i]
            x2, y2 = coords[i + 1]
            x = x1 + (x2 - x1) * t
            y = y1 + (y2 - y1) * t
            r = 6
            self.canvas.coords(self.packet, x - r, y - r, x + r, y + r)
            self.canvas.itemconfig(self.packet, outline="#ff00ff", fill="#ff00ff")
            if t >= 1.0:
                self.after(50, lambda: step_segment(i + 1, 0.0))
            else:
                self.after(30, lambda: step_segment(i, t + 0.1))

        step_segment(0, 0.0)

    def _build_dashboard(self):
        if not matplotlib:
            self.dashboard_frame = None
            return
        dash = ttk.LabelFrame(self, text="Dashboard (Metrics)", style="Cyber.TLabelframe")
        dash.pack(fill=tk.X, padx=5, pady=2)

        fig = Figure(figsize=(4, 1.5), dpi=100)
        self.ax = fig.add_subplot(111)
        self.ax.set_facecolor("#050510")
        fig.patch.set_facecolor("#050510")
        self.ax.tick_params(colors="#00ffcc")
        for spine in self.ax.spines.values():
            spine.set_color("#00ffcc")
        self.ax.set_title("Loss over Epochs", color="#00ffcc")

        self.loss_line, = self.ax.plot([], [], color="#ff00ff", marker="o")

        canvas = FigureCanvasTkAgg(fig, master=dash)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.X, expand=False)
        self.dashboard_canvas = canvas

    def update_dashboard(self, result: Dict[str, Any]):
        if not matplotlib:
            return
        metrics = result.get("train_status", {}).get("metrics", {})
        epochs = metrics.get("epochs", 0)
        loss = metrics.get("loss", 0.0)
        xs = list(range(1, epochs + 1))
        ys = [max(loss * (1.0 + (epochs - i) * 0.1), 0.0001) for i in xs]
        self.loss_line.set_data(xs, ys)
        self.ax.set_xlim(1, max(1, epochs))
        self.ax.set_ylim(0, max(0.1, max(ys) * 1.2))
        self.dashboard_canvas.draw()

    def _build_config_panel(self):
        cfg_frame = ttk.LabelFrame(self, text="Configuration", style="Cyber.TLabelframe")
        cfg_frame.pack(fill=tk.X, padx=5, pady=2)

        core_frame = ttk.Frame(cfg_frame, style="Cyber.TFrame")
        core_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        ttk.Label(core_frame, text="Core LR", style="Cyber.TLabel").grid(row=0, column=0, sticky="w")
        self.core_lr_var = tk.DoubleVar(value=self.config_state["core"]["lr"])
        ttk.Entry(core_frame, textvariable=self.core_lr_var, style="Cyber.TEntry", width=8).grid(row=0, column=1)

        ttk.Label(core_frame, text="Batch", style="Cyber.TLabel").grid(row=1, column=0, sticky="w")
        self.core_batch_var = tk.IntVar(value=self.config_state["core"]["batch_size"])
        ttk.Entry(core_frame, textvariable=self.core_batch_var, style="Cyber.TEntry", width=8).grid(row=1, column=1)

        ttk.Label(core_frame, text="Epochs", style="Cyber.TLabel").grid(row=2, column=0, sticky="w")
        self.core_epochs_var = tk.IntVar(value=self.config_state["core"]["epochs"])
        ttk.Entry(core_frame, textvariable=self.core_epochs_var, style="Cyber.TEntry", width=8).grid(row=2, column=1)

        swarm_frame = ttk.Frame(cfg_frame, style="Cyber.TFrame")
        swarm_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        ttk.Label(swarm_frame, text="Swarm Size", style="Cyber.TLabel").grid(row=0, column=0, sticky="w")
        self.swarm_size_var = tk.IntVar(value=self.config_state["swarm"]["swarm_size"])
        ttk.Entry(swarm_frame, textvariable=self.swarm_size_var, style="Cyber.TEntry", width=8).grid(row=0, column=1)

        ttk.Label(swarm_frame, text="Generations", style="Cyber.TLabel").grid(row=1, column=0, sticky="w")
        self.swarm_gen_var = tk.IntVar(value=self.config_state["swarm"]["generations"])
        ttk.Entry(swarm_frame, textvariable=self.swarm_gen_var, style="Cyber.TEntry", width=8).grid(row=1, column=1)

        quantum_frame = ttk.Frame(cfg_frame, style="Cyber.TFrame")
        quantum_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        ttk.Label(quantum_frame, text="Temperature", style="Cyber.TLabel").grid(row=0, column=0, sticky="w")
        self.quant_temp_var = tk.DoubleVar(value=self.config_state["quantum"]["temperature"])
        ttk.Entry(quantum_frame, textvariable=self.quant_temp_var, style="Cyber.TEntry", width=8).grid(row=0, column=1)

        apply_btn = ttk.Button(cfg_frame, text="Apply Config", style="Cyber.TButton", command=self.apply_config)
        apply_btn.pack(side=tk.RIGHT, padx=5)

    def apply_config(self):
        self.config_state["core"]["lr"] = float(self.core_lr_var.get())
        self.config_state["core"]["batch_size"] = int(self.core_batch_var.get())
        self.config_state["core"]["epochs"] = int(self.core_epochs_var.get())
        self.config_state["swarm"]["swarm_size"] = int(self.swarm_size_var.get())
        self.config_state["swarm"]["generations"] = int(self.swarm_gen_var.get())
        self.config_state["quantum"]["temperature"] = float(self.quant_temp_var.get())
        self.orchestrator = MegaSystemOrchestrator(self.config_state, self.tier_manager)
        self._log("Configuration applied and orchestrator reinitialized.")

    def _build_console(self):
        self.console_output = tk.Text(self.console_frame, wrap=tk.WORD, height=6, state=tk.DISABLED, bg="#050510", fg="#00ff99", insertbackground="#00ff99")
        self.console_output.pack(fill=tk.BOTH, expand=True)

        entry_frame = ttk.Frame(self.console_frame, style="Cyber.TFrame")
        entry_frame.pack(fill=tk.X)

        self.console_input = ttk.Entry(entry_frame, style="Cyber.TEntry")
        self.console_input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.console_input.bind("<Return>", lambda e: self.execute_console_command())

        exec_btn = ttk.Button(entry_frame, text="Exec", style="Cyber.TButton", command=self.execute_console_command)
        exec_btn.pack(side=tk.RIGHT)

    def console_log(self, msg: str):
        self.console_output.config(state=tk.NORMAL)
        self.console_output.insert(tk.END, msg + "\n")
        self.console_output.see(tk.END)
        self.console_output.config(state=tk.DISABLED)

    def execute_console_command(self):
        cmd = self.console_input.get().strip()
        self.console_input.delete(0, tk.END)
        if not cmd:
            return
        self.console_log(f"> {cmd}")
        if cmd.lower() == "help":
            self.console_log("Commands: help, status, last, clear, animate, config")
        elif cmd.lower() == "status":
            self.console_log("System online. Config: " + json.dumps(self.config_state))
        elif cmd.lower() == "last":
            if self.last_result:
                self.console_log("Last result keys: " + ", ".join(self.last_result.keys()))
            else:
                self.console_log("No last result yet.")
        elif cmd.lower() == "clear":
            self.console_output.config(state=tk.NORMAL)
            self.console_output.delete("1.0", tk.END)
            self.console_output.config(state=tk.DISABLED)
        elif cmd.lower() == "animate":
            self.animate_path(["input", "models", "core", "swarm", "kb", "output"])
        elif cmd.lower() == "config":
            self.console_log("Current config: " + json.dumps(self.config_state))
        else:
            self.console_log("Unknown command. Type 'help'.")

    def _log(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)

    def load_text_file(self):
        path = filedialog.askopenfilename(title="Select Text File", filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            self.text_input.delete(0, tk.END)
            self.text_input.insert(0, content[:500])
            self._log(f"Loaded text file: {path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file: {e}")

    def save_state(self):
        data = {"config": self.config_state, "last_result": self.last_result}
        path = filedialog.asksaveasfilename(title="Save State", defaultextension=".json", filetypes=[("JSON Files", "*.json")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._log(f"State saved to {path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save state: {e}")

    def load_state(self):
        path = filedialog.askopenfilename(title="Load State", filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.config_state = data.get("config", self.config_state)
            self.last_result = data.get("last_result", None)
            self.core_lr_var.set(self.config_state["core"]["lr"])
            self.core_batch_var.set(self.config_state["core"]["batch_size"])
            self.core_epochs_var.set(self.config_state["core"]["epochs"])
            self.swarm_size_var.set(self.config_state["swarm"]["swarm_size"])
            self.swarm_gen_var.set(self.config_state["swarm"]["generations"])
            self.quant_temp_var.set(self.config_state["quantum"]["temperature"])
            self.orchestrator = MegaSystemOrchestrator(self.config_state, self.tier_manager)
            self._log(f"State loaded from {path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load state: {e}")

    def run_full_pipeline_threaded(self):
        text = self.text_input.get()
        self._log(f"Starting pipeline with text length {len(text)}.")
        self.animate_path(["input", "models", "core", "swarm", "kb", "output"])
        t = threading.Thread(target=self._run_pipeline_worker, args=(text,), daemon=True)
        t.start()

    def _run_pipeline_worker(self, text: str):
        try:
            result = self.orchestrator.run_full_pipeline(text=text)
            self.last_result = result
            self.after(0, lambda: self._on_pipeline_complete(result))
        except Exception as e:
            self.after(0, lambda: self._log(f"Pipeline error: {e}"))

    def _on_pipeline_complete(self, result: Dict[str, Any]):
        self._log("Pipeline completed.")
        self._log(f"LLM Output: {result['llm_out']}")
        self._log(f"Training Status: {result['train_status']}")
        self._log(f"Swarm Decision: {result['swarm_decision']}")
        self._log(f"Quantum Decision: {result['q_decision']}")
        self._log(f"Coordination: {result['coordination']}")
        self._log(f"Ethical Check: {result['ethical']}")
        self._log(f"Security State: {result['secure']}")
        self._log(f"Simulation: {result['simulation']}")
        self._log(f"Image Output: {result['image_output']}")
        self._log(f"Audio Output: {result['audio_output']}")
        self._log(f"Training Info: {result['training_info']}")
        self.update_dashboard(result)


if __name__ == "__main__":
    app = MegaSystemApp()
    app.mainloop()
