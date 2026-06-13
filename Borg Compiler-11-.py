import ast
import os
import socket
import argparse
import threading
import queue
import traceback
import difflib
import tkinter as tk
from tkinter import scrolledtext, messagebox, filedialog
import builtins as _builtins
import fnmatch
import types
import time
import random
from collections import deque
import json

# Optional JIT / ML stack
try:
    import llvmlite.ir as ll_ir
    import llvmlite.binding as ll_binding
except ImportError:
    ll_ir = None
    ll_binding = None

try:
    import numba
except ImportError:
    numba = None

try:
    import torch
    import torch.nn as nn
    from transformers import AutoTokenizer, AutoModelForCausalLM
except ImportError:
    torch = None
    nn = None
    AutoTokenizer = None
    AutoModelForCausalLM = None

from typing import Tuple, Dict, Any

# ============================================================
# GLOBAL CONFIG / TELEMETRY
# ============================================================
HAS_CUDA = bool(torch and torch.cuda.is_available())
NUM_GPUS = torch.cuda.device_count() if HAS_CUDA else 0
DEFAULT_DEVICE = torch.device("cuda" if HAS_CUDA else "cpu") if torch else "cpu"

PRIMARY_MODEL_NAME = os.environ.get(
    "BORG_PRIMARY_MODEL",
    "meta-llama/Llama-3-8B-Instruct"
)

CURRENT_MODEL = None
CURRENT_TOKENIZER = None
CURRENT_MODEL_NAME = None
IS_FALLBACK_MODEL = False

GLOBAL_CACHE: Dict[str, Any] = {}
GLOBAL_TELEMETRY = {
    "cpu_load": 0.0,
    "gpu_load": 0.0,
    "mem_used": 0.0,
    "net_latency_ms": 0.0,
}

CONFIG_PATH = os.environ.get("BORG_CONFIG_PATH", "borg_config.json")
GLOBAL_CONFIG = {
    "rpc_port": int(os.environ.get("BORG_RPC_PORT", "6000")),
    "rpc_host": os.environ.get("BORG_RPC_HOST", "0.0.0.0"),
    "headless_default": os.environ.get("BORG_HEADLESS_DEFAULT", "false").lower() == "true",
    "gui_default": os.environ.get("BORG_GUI_DEFAULT", "false").lower() == "true",
    "project_root_default": os.environ.get("BORG_PROJECT_ROOT", ""),
    "router_ckpt": os.environ.get("BORG_ROUTER_CKPT", "router_net_v84.pt"),
}


def load_dynamic_config():
    global GLOBAL_CONFIG
    try:
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                GLOBAL_CONFIG.update(data)
                print(f"[Config] Loaded dynamic config from {CONFIG_PATH}")
    except Exception as e:
        print(f"[Config] Failed to load config: {e}")


def hot_reload_config_loop():
    last_mtime = None
    while True:
        try:
            if os.path.isfile(CONFIG_PATH):
                mtime = os.path.getmtime(CONFIG_PATH)
                if last_mtime is None or mtime > last_mtime:
                    last_mtime = mtime
                    load_dynamic_config()
        except Exception:
            pass
        time.sleep(2.0)


# ============================================================
# Batch sender
# ============================================================
class BatchSender:
    def __init__(self, flush_interval=0.5, max_batch_size=64 * 1024):
        self.flush_interval = flush_interval
        self.max_batch_size = max_batch_size
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def send(self, data: str):
        self._queue.put(str(data))

    def _worker(self):
        buffer = []
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=self.flush_interval)
                buffer.append(item)
                total_size = sum(len(x) for x in buffer)
                if total_size >= self.max_batch_size:
                    self._flush(buffer)
                    buffer.clear()
            except queue.Empty:
                if buffer:
                    self._flush(buffer)
                    buffer.clear()

    def _flush(self, buffer):
        joined = "\n".join(buffer)
        print("[BATCH FLUSH]")
        print(joined)

    def stop(self):
        self._stop_event.set()
        self._thread.join(timeout=1.0)


_global_batch_sender = BatchSender()


def batch_send(data: str):
    _global_batch_sender.send(data)


# ============================================================
# Parallel helper
# ============================================================
def parallel_for(iterable, func):
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as executor:
        list(executor.map(func, iterable))


# ============================================================
# LibraryResolver / PluginManager
# ============================================================
class LibraryResolver:
    def __init__(self):
        self.builtins = set(dir(_builtins))

    def resolve_missing_imports(self, tree: ast.AST, existing_imports: set) -> set:
        defined = set()

        class DefVisitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                defined.add(node.name)
                for arg in node.args.args:
                    defined.add(arg.arg)
                if node.args.vararg:
                    defined.add(node.args.vararg.arg)
                if node.args.kwarg:
                    defined.add(node.args.kwarg.arg)
                self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node):
                self.visit_FunctionDef(node)

            def visit_ClassDef(self, node):
                defined.add(node.name)
                self.generic_visit(node)

            def visit_Assign(self, node):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        defined.add(t.id)
                self.generic_visit(node)

            def visit_AnnAssign(self, node):
                if isinstance(node.target, ast.Name):
                    defined.add(node.target.id)
                self.generic_visit(node)

            def visit_Import(self, node):
                for n in node.names:
                    defined.add(n.name.split(".")[0])

            def visit_ImportFrom(self, node):
                if node.module:
                    defined.add(node.module.split(".")[0])

        DefVisitor().visit(tree)

        used = set()

        class NameVisitor(ast.NodeVisitor):
            def visit_Name(self, node):
                used.add(node.id)

        NameVisitor().visit(tree)

        missing = set()
        for name in used:
            if name in self.builtins:
                continue
            if name in defined:
                continue
            if name in existing_imports:
                continue
            missing.add(name)

        return missing


class PluginManager:
    def __init__(self):
        self.plugins = []

    def register(self, plugin):
        if callable(plugin):
            self.plugins.append(plugin)

    def apply(self, tree: ast.AST) -> ast.AST:
        for plugin in self.plugins:
            try:
                result = plugin(tree)
                if isinstance(result, ast.AST):
                    tree = result
            except Exception:
                traceback.print_exc()
        return tree


# ============================================================
# CFG / Dominator / SSA / Data-flow
# ============================================================
class CFGNode:
    def __init__(self, ast_node, idx):
        self.ast_node = ast_node
        self.idx = idx
        self.next_nodes = []
        self.prev_nodes = []

    def add_edge(self, node):
        self.next_nodes.append(node)
        node.prev_nodes.append(self)


class ControlFlowGraph:
    def __init__(self, func_name):
        self.func_name = func_name
        self.nodes = []
        self.entry = None

    def add_node(self, node: CFGNode):
        if self.entry is None:
            self.entry = node
        self.nodes.append(node)


class DominatorTree:
    def __init__(self, cfg: ControlFlowGraph):
        self.cfg = cfg
        self.doms = {}
        self.frontier = {}

    def compute(self):
        if not self.cfg.nodes:
            return
        nodes = self.cfg.nodes
        entry = self.cfg.entry
        self.doms = {n: set(nodes) for n in nodes}
        self.doms[entry] = {entry}
        changed = True
        while changed:
            changed = False
            for n in nodes:
                if n is entry:
                    continue
                preds = n.prev_nodes
                if not preds:
                    continue
                new_dom = set(nodes)
                for p in preds:
                    new_dom &= self.doms[p]
                new_dom.add(n)
                if new_dom != self.doms[n]:
                    self.doms[n] = new_dom
                    changed = True
        self._compute_frontier()

    def _compute_frontier(self):
        frontier = {n: set() for n in self.cfg.nodes}
        for n in self.cfg.nodes:
            if len(n.prev_nodes) >= 2:
                for p in n.prev_nodes:
                    runner = p
                    while runner not in self.doms[n]:
                        frontier[runner].add(n)
                        if not runner.prev_nodes:
                            break
                        runner = runner.prev_nodes[0]
        self.frontier = frontier


class SSAForm:
    def __init__(self):
        self.versioned_vars = {}
        self.phi_nodes = {}


class DataFlowInfo:
    def __init__(self):
        self.defs = {}
        self.uses = {}
        self.liveness = {}
        self.reaching_defs = {}
        self.alias_graph = {}


# ============================================================
# Probabilistic / Queen / AttackChain
# ============================================================
class ProbabilisticField:
    def __init__(self, mean: float, var: float):
        self.mean = mean
        self.var = var

    def sample(self):
        return random.gauss(self.mean, self.var)

    def update(self, observation: float, weight: float = 1.0):
        self.mean = (self.mean + weight * observation) / (1.0 + weight)
        self.var = max(1e-6, self.var * 0.9)


class Queen:
    def __init__(self):
        self.nodes = {}

    def update(self, node, events):
        self.nodes[node] = events

    def global_risk(self):
        risk = {}
        for node, evts in self.nodes.items():
            for e in evts:
                risk[e["entity"]] = risk.get(e["entity"], 0) + e["score"]
        return {k: v for k, v in risk.items() if v > 1.5}


class AttackChainEngine:
    def __init__(self):
        self.events = deque()
        self.window = 120

    def add_event(self, event_type, data):
        now = time.time()
        self.events.append((now, event_type, data))
        self._cleanup(now)

    def _cleanup(self, now):
        cutoff = now - self.window
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()

    def detect(self):
        types = [e[1] for e in self.events]
        chains = []
        if all(x in types for x in ["proc_spawn", "powershell", "net_connect"]):
            chains.append(("LOLBIN_ATTACK", 0.9))
        if types.count("proc_spawn") > 5 and "net_connect" in types:
            chains.append(("PROCESS_STORM", 0.8))
        if "file_mod" in types and "net_connect" in types:
            chains.append(("PERSISTENCE_EXFIL", 0.85))
        return chains


class EventBus:
    def __init__(self):
        self.subscribers = []
        self.queue = deque()
        self._stop = False

    def publish(self, event):
        self.queue.append(event)

    def subscribe(self, fn):
        self.subscribers.append(fn)

    def run_forever(self):
        while not self._stop:
            if self.queue:
                evt = self.queue.popleft()
                for fn in self.subscribers:
                    try:
                        fn(evt)
                    except Exception:
                        traceback.print_exc()
            time.sleep(0.01)

    def stop(self):
        self._stop = True


class SecEvent:
    def __init__(self, etype, entity, meta=None):
        self.ts = time.time()
        self.type = etype
        self.entity = entity
        self.meta = meta or {}


# ============================================================
# Optimizer (compiler side)
# ============================================================
class Optimizer:
    def reduce_network_calls(self, tree: ast.AST) -> bool:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("send", "sendall"):
                    return True
        return False

    def minimize_memory_usage(self, tree: ast.AST) -> bool:
        for node in ast.walk(tree):
            if isinstance(node, ast.List) and len(node.elts) > 100:
                return True
        return False

    def parallelize_operations(self, tree: ast.AST) -> bool:
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                return True
        return False

    def dead_code_elimination(self, tree: ast.AST) -> bool:
        for node in ast.walk(tree):
            if isinstance(node, ast.Return):
                return True
        return False

    def constant_folding(self, tree: ast.AST) -> bool:
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant):
                return True
        return False

    def loop_unrolling(self, tree: ast.AST) -> bool:
        for node in ast.walk(tree):
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Call):
                if isinstance(node.iter.func, ast.Name) and node.iter.func.id == "range":
                    return True
        return False

    def type_inference(self, tree: ast.AST) -> bool:
        return True

    def ai_assisted_optimization(self, tree: ast.AST) -> bool:
        return True

    def bytecode_optimization(self, tree: ast.AST) -> bool:
        return True

    def ssa(self, tree: ast.AST) -> bool:
        return True

    def cfg(self, tree: ast.AST) -> bool:
        return True

    def data_flow(self, tree: ast.AST) -> bool:
        return True

    def dead_store_elimination(self, tree: ast.AST) -> bool:
        return True

    def peephole(self, tree: ast.AST) -> bool:
        return True

    def jit_hooks(self, tree: ast.AST) -> bool:
        return True

    def ml_ranking(self, tree: ast.AST) -> bool:
        return True

    def semantic_analysis(self, tree: ast.AST) -> bool:
        return True

    def cross_file_inlining(self, tree: ast.AST) -> bool:
        return True

    def dominator_tree(self, tree: ast.AST) -> bool:
        return True

    def liveness(self, tree: ast.AST) -> bool:
        return True

    def reaching_defs(self, tree: ast.AST) -> bool:
        return True

    def constant_propagation(self, tree: ast.AST) -> bool:
        return True

    def escape_analysis(self, tree: ast.AST) -> bool:
        return True

    def function_inlining(self, tree: ast.AST) -> bool:
        return True

    def interprocedural(self, tree: ast.AST) -> bool:
        return True

    def speculative(self, tree: ast.AST) -> bool:
        return True

    def pgo(self, tree: ast.AST) -> bool:
        return True

    def bytecode_rewriting(self, tree: ast.AST) -> bool:
        return True

    def jit_compilation(self, tree: ast.AST) -> bool:
        return True

    def llvm_ir(self, tree: ast.AST) -> bool:
        return True

    def control_flow_restructuring(self, tree: ast.AST) -> bool:
        return True

    def predictive_gap_detection(self, tree: ast.AST) -> bool:
        defined = set()
        used = set()

        class V(ast.NodeVisitor):
            def visit_Assign(self, node):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        defined.add(t.id)
                self.generic_visit(node)

            def visit_Name(self, node):
                if isinstance(node.ctx, ast.Load):
                    used.add(node.id)

        V().visit(tree)
        missing = [u for u in used if u not in defined and u not in dir(_builtins)]
        return len(missing) > 0


# ============================================================
# CodeGenerator (compiler + autopilot)
# ============================================================
class CodeGenerator:
    def __init__(self, optimizer: Optimizer):
        self.optimizer = optimizer
        self.optimization_rules = {k: True for k in [
            "reduce_network_calls", "minimize_memory_usage", "parallelize_operations",
            "dead_code_elimination", "constant_folding", "loop_unrolling",
            "type_inference", "ai_assisted_optimization", "bytecode_optimization",
            "ssa", "cfg", "data_flow", "dead_store_elimination", "peephole",
            "jit_hooks", "ml_ranking", "semantic_analysis", "cross_file_inlining",
            "dominator_tree", "liveness", "reaching_defs", "constant_propagation",
            "escape_analysis", "function_inlining", "interprocedural", "speculative",
            "pgo", "bytecode_rewriting", "jit_compilation", "llvm_ir",
            "control_flow_restructuring", "predictive_gap_detection",
        ]}
        self.imported_libraries = set()
        self.resolver = LibraryResolver()
        self.plugin_manager = PluginManager()
        self.type_info = {}
        self.cfgs = {}
        self.data_flow_info = {}
        self.ssa_forms = {}
        self.dom_trees = {}

        self.queen = Queen()
        self.attack_chain = AttackChainEngine()
        self.global_field = ProbabilisticField(mean=0.5, var=0.25)

    def predict_optimization(self, original_code: str) -> dict:
        tree = ast.parse(original_code)
        return {name: getattr(self.optimizer, name)(tree) for name in self.optimization_rules.keys()}

    def generate_optimized_code(self, original_code: str) -> str:
        try:
            tree = ast.parse(original_code)
        except SyntaxError:
            return original_code

        decisions = self.predict_optimization(original_code)

        self.imported_libraries.clear()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for name in node.names:
                    base = name.name.split(".")[0]
                    self.imported_libraries.add(base)

        missing_imports = self.resolver.resolve_missing_imports(
            tree, self.imported_libraries
        )
        self.imported_libraries.update(missing_imports)

        try:
            if decisions["type_inference"]:
                self._type_inference(tree)
            if decisions["cfg"]:
                self._build_cfg(tree)
            if decisions["dominator_tree"]:
                self._build_dominator_trees()
            if decisions["data_flow"]:
                self._data_flow_analysis(tree)
            if decisions["liveness"]:
                self._liveness_analysis()
            if decisions["reaching_defs"]:
                self._reaching_definitions()
            if decisions["ssa"]:
                tree = self._ssa_transform(tree)
            if decisions["constant_propagation"]:
                tree = self.constant_propagation(tree)

            if decisions["reduce_network_calls"]:
                tree = self.reduce_network_calls(tree)
            if decisions["minimize_memory_usage"]:
                tree = self.minimize_memory_usage(tree)
            if decisions["parallelize_operations"]:
                tree = self.parallelize_operations(tree)
            if decisions["dead_code_elimination"]:
                tree = self.dead_code_elimination(tree)
            if decisions["constant_folding"]:
                tree = self.constant_folding(tree)
            if decisions["loop_unrolling"]:
                tree = self.loop_unrolling(tree)
            if decisions["dead_store_elimination"]:
                tree = self.dead_store_elimination(tree)
            if decisions["peephole"]:
                tree = self.peephole(tree)
            if decisions["escape_analysis"]:
                tree = self.escape_analysis(tree)
            if decisions["function_inlining"]:
                tree = self.function_inlining(tree)
            if decisions["interprocedural"]:
                tree = self.interprocedural(tree)
            if decisions["speculative"]:
                tree = self.speculative(tree)
            if decisions["pgo"]:
                tree = self.pgo(tree)
            if decisions["ai_assisted_optimization"]:
                tree = self.ai_assisted_optimization(tree)
            if decisions["bytecode_optimization"]:
                tree = self.bytecode_optimization(tree)
            if decisions["bytecode_rewriting"]:
                tree = self.bytecode_rewriting(tree)
            if decisions["jit_hooks"]:
                tree = self.jit_hooks(tree)
            if decisions["ml_ranking"]:
                tree = self.ml_ranking(tree)
            if decisions["semantic_analysis"]:
                tree = self.semantic_analysis(tree)
            if decisions["cross_file_inlining"]:
                tree = self.cross_file_inlining(tree)
            if decisions["jit_compilation"]:
                tree = self.jit_compilation(tree)
            if decisions["llvm_ir"]:
                tree = self.llvm_ir(tree)
            if decisions["control_flow_restructuring"]:
                tree = self.control_flow_restructuring(tree)
            if decisions["predictive_gap_detection"]:
                tree = self.predictive_gap_annotate(tree)

            tree = self.plugin_manager.apply(tree)

        except Exception:
            traceback.print_exc()

        self.imported_libraries.update({"socket", "concurrent", "concurrent.futures", "ast"})

        try:
            optimized_code = ast.unparse(tree)
        except Exception:
            traceback.print_exc()
            return original_code

        import_lines = [f"import {lib}" for lib in sorted(self.imported_libraries)]
        header = "\n".join(import_lines)
        return f"{header}\n\n{optimized_code}"

    # ---------- AST transforms ----------
    def reduce_network_calls(self, tree: ast.AST) -> ast.AST:
        class T(ast.NodeTransformer):
            def visit_Call(self, node):
                node = self.generic_visit(node)
                if isinstance(node.func, ast.Attribute) and node.func.attr in ("send", "sendall"):
                    if node.args:
                        new_node = ast.Call(
                            func=ast.Name(id="batch_send", ctx=ast.Load()),
                            args=[node.args[0]],
                            keywords=node.keywords,
                        )
                        return ast.copy_location(new_node, node)
                return node
        return T().visit(tree)

    def minimize_memory_usage(self, tree: ast.AST) -> ast.AST:
        class T(ast.NodeTransformer):
            def visit_List(self, node):
                node = self.generic_visit(node)
                if len(node.elts) > 100:
                    gen = ast.GeneratorExp(
                        elt=node.elts[0],
                        generators=[
                            ast.comprehension(
                                target=ast.Name(id="_", ctx=ast.Store()),
                                iter=ast.List(elts=node.elts, ctx=ast.Load()),
                                ifs=[],
                                is_async=0,
                            )
                        ],
                    )
                    return ast.copy_location(gen, node)
                return node
        return T().visit(tree)

    def parallelize_operations(self, tree: ast.AST) -> ast.AST:
        class T(ast.NodeTransformer):
            def visit_For(self, node):
                node = self.generic_visit(node)
                if len(node.body) != 1:
                    return node
                body_stmt = node.body[0]
                if not isinstance(body_stmt, ast.Expr):
                    return node
                call = body_stmt.value
                if not isinstance(call, ast.Call):
                    return node
                if not isinstance(call.func, ast.Name):
                    return node
                func_name = call.func.id
                if not call.args:
                    return node
                first_arg = call.args[0]
                if not isinstance(first_arg, ast.Name):
                    return node
                if not isinstance(node.target, ast.Name):
                    return node
                if first_arg.id != node.target.id:
                    return node
                new_expr = ast.Expr(
                    value=ast.Call(
                        func=ast.Name(id="parallel_for", ctx=ast.Load()),
                        args=[node.iter, ast.Name(id=func_name, ctx=ast.Load())],
                        keywords=[],
                    )
                )
                return ast.copy_location(new_expr, node)
        return T().visit(tree)

    def dead_code_elimination(self, tree: ast.AST) -> ast.AST:
        class T(ast.NodeTransformer):
            def _clean_block(self, stmts):
                new_stmts = []
                reachable = True
                for stmt in stmts:
                    if not reachable:
                        continue
                    new_stmts.append(stmt)
                    if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                        reachable = False
                return new_stmts

            def visit_FunctionDef(self, node):
                self.generic_visit(node)
                node.body = self._clean_block(node.body)
                return node

            def visit_AsyncFunctionDef(self, node):
                return self.visit_FunctionDef(node)

            def visit_If(self, node):
                self.generic_visit(node)
                node.body = self._clean_block(node.body)
                node.orelse = self._clean_block(node.orelse)
                return node

            def visit_For(self, node):
                self.generic_visit(node)
                node.body = self._clean_block(node.body)
                node.orelse = self._clean_block(node.orelse)
                return node

            def visit_While(self, node):
                self.generic_visit(node)
                node.body = self._clean_block(node.body)
                node.orelse = self._clean_block(node.orelse)
                return node

            def visit_Try(self, node):
                self.generic_visit(node)
                node.body = self._clean_block(node.body)
                node.finalbody = self._clean_block(node.finalbody)
                for h in node.handlers:
                    h.body = self._clean_block(h.body)
                return node
        return T().visit(tree)

    def constant_folding(self, tree: ast.AST) -> ast.AST:
        class T(ast.NodeTransformer):
            def visit_BinOp(self, node):
                node = self.generic_visit(node)
                if isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant):
                    try:
                        value = eval(compile(ast.Expression(node), "<const_fold>", "eval"))
                        return ast.copy_location(ast.Constant(value=value), node)
                    except Exception:
                        return node
                return node
        return T().visit(tree)

    def loop_unrolling(self, tree: ast.AST) -> ast.AST:
        class T(ast.NodeTransformer):
            def visit_For(self, node):
                node = self.generic_visit(node)
                if not isinstance(node.iter, ast.Call):
                    return node
                if not isinstance(node.iter.func, ast.Name):
                    return node
                if node.iter.func.id != "range":
                    return node
                if len(node.iter.args) not in (1, 2, 3):
                    return node
                try:
                    rng = self._eval_range(node.iter)
                except Exception:
                    return node
                if rng is None or len(rng) > 4:
                    return node
                new_body = []
                for val in rng:
                    assign = ast.Assign(
                        targets=[node.target],
                        value=ast.Constant(value=val),
                    )
                    new_body.append(assign)
                    for stmt in node.body:
                        new_body.append(ast.fix_missing_locations(ast.copy_location(ast.parse(ast.unparse(stmt)).body[0], stmt)))
                return new_body

            def _eval_range(self, call_node):
                args = call_node.args
                if len(args) == 1 and isinstance(args[0], ast.Constant):
                    return list(range(args[0].value))
                if len(args) == 2 and all(isinstance(a, ast.Constant) for a in args):
                    return list(range(args[0].value, args[1].value))
                if len(args) == 3 and all(isinstance(a, ast.Constant) for a in args):
                    return list(range(args[0].value, args[1].value, args[2].value))
                return None
        return T().visit(tree)

    def _type_inference(self, tree: ast.AST):
        type_info = {}

        class V(ast.NodeVisitor):
            def visit_Assign(self, node):
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                    if isinstance(node.value, ast.Constant):
                        type_info[name] = type(node.value.value).__name__
                self.generic_visit(node)
        V().visit(tree)
        self.type_info = type_info

    def ai_assisted_optimization(self, tree: ast.AST) -> ast.AST:
        comment = ast.Expr(value=ast.Constant(value="# AI-assisted optimization placeholder"))
        if isinstance(tree, ast.Module):
            tree.body.insert(0, comment)
        return tree

    def bytecode_optimization(self, tree: ast.AST) -> ast.AST:
        try:
            code_obj = compile(tree, "<bytecode_opt>", "exec")
            if not isinstance(code_obj, types.CodeType):
                return tree
        except Exception:
            traceback.print_exc()
        return tree

    def _build_cfg(self, tree: ast.AST):
        cfgs = {}

        class B(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                cfg = ControlFlowGraph(node.name)
                idx = 0

                def build_block(stmts, prev_node=None):
                    nonlocal idx
                    for stmt in stmts:
                        n = CFGNode(stmt, idx)
                        cfg.add_node(n)
                        if prev_node is not None:
                            prev_node.add_edge(n)
                        prev_node = n
                        idx += 1
                        if isinstance(stmt, ast.If):
                            then_prev = n
                            build_block(stmt.body, then_prev)
                            else_prev = n
                            build_block(stmt.orelse, else_prev)
                        elif isinstance(stmt, (ast.For, ast.While)):
                            body_prev = n
                            build_block(stmt.body, body_prev)
                    return prev_node

                build_block(node.body, None)
                cfgs[node.name] = cfg
                self.generic_visit(node)
        B().visit(tree)
        self.cfgs = cfgs

    def _build_dominator_trees(self):
        doms = {}
        for name, cfg in self.cfgs.items():
            dt = DominatorTree(cfg)
            dt.compute()
            doms[name] = dt
        self.dom_trees = doms

    def _data_flow_analysis(self, tree: ast.AST):
        info = DataFlowInfo()

        class V(ast.NodeVisitor):
            def visit_Assign(self, node):
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                    info.defs.setdefault(name, []).append(node)
                self.generic_visit(node)

            def visit_Name(self, node):
                if isinstance(node.ctx, ast.Load):
                    info.uses.setdefault(node.id, []).append(node)
        V().visit(tree)
        info.alias_graph = {var: {var} for var in info.defs.keys()}
        self.data_flow_info = info

    def _liveness_analysis(self):
        info = self.data_flow_info
        for cfg in self.cfgs.values():
            live = set(info.uses.keys())
            for node in reversed(cfg.nodes):
                info.liveness[node] = set(live)
                assigned = []
                if isinstance(node.ast_node, ast.Assign):
                    for t in node.ast_node.targets:
                        if isinstance(t, ast.Name):
                            assigned.append(t.id)
                for a in assigned:
                    if a in live:
                        live.remove(a)
                for name in self._names_in_node(node.ast_node):
                    live.add(name)

    def _reaching_definitions(self):
        info = self.data_flow_info
        for cfg in self.cfgs.values():
            reaching = {}
            current_defs = {var: set(defs) for var, defs in info.defs.items()}
            for node in cfg.nodes:
                reaching[node] = {var: set(d) for var, d in current_defs.items()}
                if isinstance(node.ast_node, ast.Assign):
                    for t in node.ast_node.targets:
                        if isinstance(t, ast.Name):
                            name = t.id
                            current_defs.setdefault(name, set()).add(node.ast_node)
            info.reaching_defs = reaching

    def _ssa_transform(self, tree: ast.AST):
        ssa = SSAForm()

        class T(ast.NodeTransformer):
            def visit_Assign(self, node):
                node = self.generic_visit(node)
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                    new_name = ssa.versioned_vars.get(name, 0) + 1
                    ssa.versioned_vars[name] = new_name
                    node.targets[0].id = f"{name}_{new_name}"
                return node
        new_tree = T().visit(tree)
        self.ssa_forms = ssa.versioned_vars
        return new_tree

    def dead_store_elimination(self, tree: ast.AST) -> ast.AST:
        info = self.data_flow_info
        if not isinstance(info, DataFlowInfo):
            return tree
        live_vars = set(info.uses.keys())

        class T(ast.NodeTransformer):
            def visit_Assign(self, node):
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                    if name not in live_vars:
                        return None
                return self.generic_visit(node)
        return T().visit(tree)

    def peephole(self, tree: ast.AST) -> ast.AST:
        class T(ast.NodeTransformer):
            def visit_BinOp(self, node):
                node = self.generic_visit(node)
                if isinstance(node.op, ast.Add):
                    if isinstance(node.left, ast.Constant) and node.left.value == 0:
                        return node.right
                    if isinstance(node.right, ast.Constant) and node.right.value == 0:
                        return node.left
                if isinstance(node.op, ast.Mult):
                    if isinstance(node.left, ast.Constant) and node.left.value == 1:
                        return node.right
                    if isinstance(node.right, ast.Constant) and node.right.value == 1:
                        return node.left
                return node
        return T().visit(tree)

    def constant_propagation(self, tree: ast.AST) -> ast.AST:
        const_env = {}

        class T(ast.NodeTransformer):
            def visit_Assign(self, node):
                node = self.generic_visit(node)
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                    if isinstance(node.value, ast.Constant):
                        const_env[name] = node.value
                return node

            def visit_Name(self, node):
                if isinstance(node.ctx, ast.Load) and node.id in const_env:
                    return ast.copy_location(const_env[node.id], node)
                return node
        return T().visit(tree)

    def escape_analysis(self, tree: ast.AST) -> ast.AST:
        comment = ast.Expr(value=ast.Constant(value="# Real escape analysis with alias graph placeholder"))
        if isinstance(tree, ast.Module):
            tree.body.insert(0, comment)
        return tree

    def function_inlining(self, tree: ast.AST) -> ast.AST:
        comment = ast.Expr(value=ast.Constant(value="# Full interprocedural function inlining placeholder"))
        if isinstance(tree, ast.Module):
            tree.body.insert(0, comment)
        return tree

    def interprocedural(self, tree: ast.AST) -> ast.AST:
        comment = ast.Expr(value=ast.Constant(value="# Interprocedural optimization placeholder"))
        if isinstance(tree, ast.Module):
            tree.body.insert(0, comment)
        return tree

    def speculative(self, tree: ast.AST) -> ast.AST:
        comment = ast.Expr(value=ast.Constant(value="# Speculative execution with guards placeholder"))
        if isinstance(tree, ast.Module):
            tree.body.insert(0, comment)
        return tree

    def pgo(self, tree: ast.AST) -> ast.AST:
        comment = ast.Expr(value=ast.Constant(value="# Profile-guided optimization with counters placeholder"))
        if isinstance(tree, ast.Module):
            tree.body.insert(0, comment)
        return tree

    def jit_hooks(self, tree: ast.AST) -> ast.AST:
        comment = ast.Expr(value=ast.Constant(value="# JIT hook placeholder"))
        if isinstance(tree, ast.Module):
            tree.body.insert(0, comment)
        return tree

    def ml_ranking(self, tree: ast.AST) -> ast.AST:
        stats = EXECUTOR.stats()
        router_data = stats.get("router_data", {})
        summary = {
            "num_layers": len(router_data),
            "avg_calls": (
                sum(v["calls"] for v in router_data.values()) / max(1, len(router_data))
                if router_data else 0
            ),
        }
        comment = ast.Expr(
            value=ast.Constant(
                value=f"# ML router ranking active (v8.4 RL): {summary}, model={stats.get('router_model')}"
            )
        )
        if isinstance(tree, ast.Module):
            tree.body.insert(0, comment)
        return tree

    def semantic_analysis(self, tree: ast.AST) -> ast.AST:
        comment = ast.Expr(value=ast.Constant(value="# Full semantic analysis placeholder"))
        if isinstance(tree, ast.Module):
            tree.body.insert(0, comment)
        return tree

    def cross_file_inlining(self, tree: ast.AST) -> ast.AST:
        comment = ast.Expr(value=ast.Constant(value="# Cross-file inlining placeholder"))
        if isinstance(tree, ast.Module):
            tree.body.insert(0, comment)
        return tree

    def bytecode_rewriting(self, tree: ast.AST) -> ast.AST:
        try:
            code_obj = compile(tree, "<bytecode_rewrite>", "exec")
            if not isinstance(code_obj, types.CodeType):
                return tree
        except Exception:
            traceback.print_exc()
        return tree

    def jit_compilation(self, tree: ast.AST) -> ast.AST:
        comment_lines = []
        if numba is not None:
            comment_lines.append("# Numba JIT available (stub integration)")
        else:
            comment_lines.append("# Numba JIT not available")
        if ll_ir is not None and ll_binding is not None:
            comment_lines.append("# LLVM IR via llvmlite available (stub integration)")
        else:
            comment_lines.append("# LLVM IR via llvmlite not available")
        for c in reversed(comment_lines):
            expr = ast.Expr(value=ast.Constant(value=c))
            if isinstance(tree, ast.Module):
                tree.body.insert(0, expr)
        return tree

    def llvm_ir(self, tree: ast.AST) -> ast.AST:
        comment = ast.Expr(value=ast.Constant(value="# Real LLVM IR generation pipeline placeholder"))
        if isinstance(tree, ast.Module):
            tree.body.insert(0, comment)
        return tree

    def control_flow_restructuring(self, tree: ast.AST) -> ast.AST:
        comment = ast.Expr(value=ast.Constant(value="# Real control-flow restructuring placeholder"))
        if isinstance(tree, ast.Module):
            tree.body.insert(0, comment)
        return tree

    def predictive_gap_annotate(self, tree: ast.AST) -> ast.AST:
        missing_vars = []

        class V(ast.NodeVisitor):
            def visit_Name(self, node):
                if isinstance(node.ctx, ast.Load):
                    if node.id not in dir(_builtins):
                        missing_vars.append(node.id)
        V().visit(tree)
        missing_vars = list(set(missing_vars))

        events = []
        for mv in missing_vars:
            score = self.global_field.sample()
            events.append({"entity": mv, "score": score})

        self.queen.update("ast_missing", events)
        global_risk = self.queen.global_risk()

        comment_lines = [
            "# Predictive gap detection:",
            f"# Missing-like symbols: {missing_vars}",
            f"# Global risk (Bernoulli-ish field): {global_risk}",
        ]
        for c in reversed(comment_lines):
            expr = ast.Expr(value=ast.Constant(value=c))
            if isinstance(tree, ast.Module):
                tree.body.insert(0, expr)
        return tree

    def _names_in_node(self, node):
        names = []

        class V(ast.NodeVisitor):
            def visit_Name(self, n):
                names.append(n.id)
        V().visit(node)
        return names


# ============================================================
# Sample original code
# ============================================================
SAMPLE_ORIGINAL_CODE = """
import socket

def send_data(data, host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((host, port))
        sock.sendall(data.encode())

def process_item(x):
    send_data(str(x), '{host}', {port})

data_list = [str(i) for i in range(1000)]
for item in data_list:
    process_item(item)
"""


# ============================================================
# Diff helper
# ============================================================
def generate_diff(original: str, optimized: str) -> str:
    original_lines = original.splitlines(keepends=True)
    optimized_lines = optimized.splitlines(keepends=True)
    diff = difflib.unified_diff(
        original_lines, optimized_lines, fromfile="original", tofile="optimized"
    )
    return "".join(diff)


# ============================================================
# Router Metrics Dashboard (v8.4)
# ============================================================
class RouterDashboard(tk.Toplevel):
    def __init__(self, executor: "ForkliftExecutor"):
        super().__init__()
        self.title("Router Metrics Dashboard - v8.4")
        self.executor = executor

        self.geometry("700x600")

        top = tk.Frame(self)
        top.pack(fill=tk.X)

        self.lbl_summary = tk.Label(top, text="Router Summary", font=("Arial", 10, "bold"))
        self.lbl_summary.pack(side=tk.LEFT, padx=5)

        self.btn_refresh = tk.Button(top, text="Refresh Now", command=self.refresh_once)
        self.btn_refresh.pack(side=tk.RIGHT, padx=5)

        self.txt = scrolledtext.ScrolledText(self, height=30)
        self.txt.pack(fill=tk.BOTH, expand=True)

        self.reward_history = deque(maxlen=100)

        self.update_loop()

    def refresh_once(self):
        self._render()

    def update_loop(self):
        self._render()
        self.after(1000, self.update_loop)

    def _render(self):
        stats = self.executor.stats()
        router_data = stats.get("router_data", {})

        self.txt.delete("1.0", tk.END)

        self.txt.insert(tk.END, "=== Router Summary ===\n")
        self.txt.insert(tk.END, f"Router Model: {stats.get('router_model')}\n")
        self.txt.insert(tk.END, f"Avg Latency: {stats.get('avg_latency_ms'):.2f} ms\n")
        self.txt.insert(tk.END, f"KV Skip Enabled: {stats.get('kv_skip_enabled')}\n")
        self.txt.insert(tk.END, f"Checkpoint: {stats.get('router_ckpt_path')}\n")
        self.txt.insert(tk.END, f"Total Calls: {stats.get('calls')}\n\n")

        self.txt.insert(tk.END, "=== Telemetry ===\n")
        tel = GLOBAL_CACHE.get("last_telemetry", {})
        for k, v in tel.items():
            self.txt.insert(tk.END, f"{k}: {v}\n")
        self.txt.insert(tk.END, "\n")

        self.txt.insert(tk.END, "=== Per-Layer Routing (Heatmap-ish) ===\n")
        for layer, info in router_data.items():
            score = info["last_score"]
            calls = info["calls"]
            depth = info["depth"]
            device = info["device"]

            bar_len = 0
            if score is not None:
                bar_len = int(max(1, min(50, score * 50)))
            bar = "#" * bar_len

            self.txt.insert(
                tk.END,
                f"{layer:<40} depth={depth:<3} calls={calls:<4} device={device:<10} "
                f"score={score} [{bar}]\n"
            )

        self.txt.insert(tk.END, "\n=== Router Reward Trend (approx) ===\n")
        rewards = GLOBAL_CACHE.get("router_rewards", [])
        if rewards:
            self.reward_history.extend(rewards)
            GLOBAL_CACHE["router_rewards"] = []
        if self.reward_history:
            line = ""
            for r in self.reward_history:
                idx = int(max(0, min(9, r * 10)))
                line += "▁▂▃▄▅▆▇█"[idx] if idx < len("▁▂▃▄▅▆▇█") else "█"
            self.txt.insert(tk.END, line + "\n")
        else:
            self.txt.insert(tk.END, "(no rewards yet)\n")


# ============================================================
# Tkinter GUI
# ============================================================
class BorgCompilerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Borg Compiler v8.4 - RL Router + Autopilot + Dashboard")

        self.optimizer = Optimizer()
        self.generator = CodeGenerator(self.optimizer)

        top_frame = tk.Frame(root)
        top_frame.pack(fill=tk.X, padx=5, pady=5)

        self.btn_load = tk.Button(top_frame, text="Load File", command=self.load_file)
        self.btn_load.pack(side=tk.LEFT, padx=2)

        self.btn_optimize = tk.Button(top_frame, text="Optimize", command=self.optimize_code)
        self.btn_optimize.pack(side=tk.LEFT, padx=2)

        self.btn_save = tk.Button(top_frame, text="Save Optimized", command=self.save_optimized)
        self.btn_save.pack(side=tk.LEFT, padx=2)

        self.btn_diff = tk.Button(top_frame, text="Show Diff", command=self.show_diff)
        self.btn_diff.pack(side=tk.LEFT, padx=2)

        self.btn_dashboard = tk.Button(top_frame, text="Router Dashboard",
                                       command=lambda: RouterDashboard(EXECUTOR))
        self.btn_dashboard.pack(side=tk.LEFT, padx=2)

        lbl_original = tk.Label(root, text="Original Code")
        lbl_original.pack(anchor=tk.W, padx=5)
        self.txt_original = scrolledtext.ScrolledText(root, height=15)
        self.txt_original.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        lbl_optimized = tk.Label(root, text="Optimized + Predictive Code")
        lbl_optimized.pack(anchor=tk.W, padx=5)
        self.txt_optimized = scrolledtext.ScrolledText(root, height=15)
        self.txt_optimized.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.current_file_path = None

    def load_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("Python files", "*.py"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.txt_original.delete("1.0", tk.END)
            self.txt_original.insert(tk.END, content)
            self.current_file_path = path
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file:\n{e}")

    def optimize_code(self):
        original = self.txt_original.get("1.0", tk.END)
        optimized = self.generator.generate_optimized_code(original)
        self.txt_optimized.delete("1.0", tk.END)
        self.txt_optimized.insert(tk.END, optimized)

    def save_optimized(self):
        optimized = self.txt_optimized.get("1.0", tk.END)
        if not optimized.strip():
            messagebox.showwarning("Warning", "No optimized code to save.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".py",
            filetypes=[("Python files", "*.py"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(optimized)
            messagebox.showinfo("Saved", f"Optimized code saved to:\n{path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save file:\n{e}")

    def show_diff(self):
        original = self.txt_original.get("1.0", tk.END)
        optimized = self.txt_optimized.get("1.0", tk.END)
        diff_text = generate_diff(original, optimized)
        if not diff_text.strip():
            messagebox.showinfo("Diff", "No differences detected.")
            return
        diff_window = tk.Toplevel(self.root)
        diff_window.title("Diff - Original vs Optimized")
        txt_diff = scrolledtext.ScrolledText(diff_window)
        txt_diff.pack(fill=tk.BOTH, expand=True)
        txt_diff.insert(tk.END, diff_text)


# ============================================================
# Project-wide optimization
# ============================================================
def optimize_project(root_dir: str, pattern: str = "*.py", in_place: bool = False):
    optimizer = Optimizer()
    generator = CodeGenerator(optimizer)

    for dirpath, dirnames, filenames in os.walk(root_dir):
        for filename in filenames:
            if fnmatch.fnmatch(filename, pattern):
                full_path = os.path.join(dirpath, filename)
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        original_code = f.read()
                    optimized_code = generator.generate_optimized_code(original_code)
                    if in_place:
                        with open(full_path, "w", encoding="utf-8") as f:
                            f.write(optimized_code)
                        print(f"[PROJECT] Optimized in-place: {full_path}")
                    else:
                        out_path = full_path + ".borg.py"
                        with open(out_path, "w", encoding="utf-8") as f:
                            f.write(optimized_code)
                        print(f"[PROJECT] Optimized -> {out_path}")
                except Exception:
                    traceback.print_exc()
                    print(f"[PROJECT] Failed to optimize: {full_path}")


# ============================================================
# Telemetry / RL router training
# ============================================================
def get_system_telemetry() -> Dict[str, float]:
    GLOBAL_TELEMETRY["cpu_load"] = random.uniform(0.1, 0.9)
    GLOBAL_TELEMETRY["gpu_load"] = random.uniform(0.0, 1.0) if HAS_CUDA else 0.0
    GLOBAL_TELEMETRY["mem_used"] = random.uniform(0.1, 0.9)
    GLOBAL_TELEMETRY["net_latency_ms"] = random.uniform(5.0, 80.0)
    return dict(GLOBAL_TELEMETRY)


def train_policy_net_step(sys_tel: Dict[str, float], latency_ms: float):
    if torch is None or nn is None:
        return
    router = getattr(EXECUTOR, "router_net", None)
    opt = getattr(EXECUTOR, "router_opt", None)
    feats = GLOBAL_CACHE.get("last_router_feats", None)
    if router is None or opt is None or feats is None:
        return

    router.train()
    opt.zero_grad()

    norm_lat = min(1.0, latency_ms / 500.0)
    reward = 1.0 - norm_lat
    target = torch.tensor([[reward]], dtype=torch.float32, device=feats.device)

    pred = router(feats)
    loss = (pred - target).pow(2).mean()
    loss.backward()
    opt.step()

    router.eval()

    GLOBAL_CACHE.setdefault("router_rewards", []).append(float(reward))


def telemetry_broadcast_loop():
    while True:
        tel = get_system_telemetry()
        GLOBAL_CACHE["last_telemetry"] = tel
        time.sleep(1.0)


def telemetry_listener_loop():
    while True:
        time.sleep(1.0)


def distributed_cache_broadcast_loop(cache: Dict[str, Any]):
    while True:
        time.sleep(1.0)


def distributed_cache_listener_loop():
    while True:
        time.sleep(1.0)


# ============================================================
# Router ML model
# ============================================================
class RouterNet(nn.Module if nn is not None else object):
    def __init__(self, in_dim: int = 8, hidden_dim: int = 32):
        if nn is not None:
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(in_dim, hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, 1),
            )
        else:
            pass

    def forward(self, x):
        if nn is None or torch is None:
            return x
        return self.net(x)


# ============================================================
# Forklift executor (Linear + Attention + KV sparsity)
# ============================================================
class ForkliftExecutor:
    def __init__(self):
        self._stats = {
            "calls": 0,
            "avg_latency_ms": 0.0,
            "router_data": {},
        }
        self.fp8_scale = 16.0
        self.sparsity_threshold = 1e-3
        self.devices = self._init_devices()
        self.router_ckpt_path = GLOBAL_CONFIG["router_ckpt"]

        self.kv_sparsity_threshold = 1e-3
        self.kv_skip_enabled = True

        if torch is not None and nn is not None:
            self.router_net = RouterNet(in_dim=8, hidden_dim=32).to(DEFAULT_DEVICE)
            self.router_opt = torch.optim.Adam(self.router_net.parameters(), lr=1e-3)
            self._try_load_router_ckpt()
        else:
            self.router_net = None
            self.router_opt = None

    def _init_devices(self):
        if not HAS_CUDA or NUM_GPUS <= 0 or torch is None:
            return [torch.device("cpu")] if torch is not None else ["cpu"]
        return [torch.device(f"cuda:{i}") for i in range(NUM_GPUS)]

    def reset_stats(self, clear_router_data: bool = False):
        self._stats["calls"] = 0
        self._stats["avg_latency_ms"] = 0.0
        if clear_router_data:
            self._stats["router_data"] = {}

    def _emulate_fp8(self, tensor: torch.Tensor) -> torch.Tensor:
        if tensor is None or torch is None:
            return tensor
        scaled = tensor / self.fp8_scale
        q = torch.clamp(torch.round(scaled * 128.0), -128, 127).to(torch.int8)
        dq = q.float() / 128.0 * self.fp8_scale
        return dq

    def _apply_sparsity(self, tensor: torch.Tensor) -> torch.Tensor:
        if tensor is None or torch is None:
            return tensor
        mask = tensor.abs() > self.sparsity_threshold
        return tensor * mask

    def _build_router_features(self, layer_name: str, layer_depth: int, x: torch.Tensor) -> torch.Tensor:
        tel = get_system_telemetry()
        bsz = float(x.shape[0]) if x.ndim >= 1 else 1.0
        hdim = float(x.shape[-1]) if x.ndim >= 1 else 1.0
        mean_abs = float(x.abs().mean().item())
        std_abs = float(x.abs().std().item())

        feat = torch.tensor(
            [
                float(layer_depth),
                bsz,
                hdim,
                mean_abs,
                std_abs,
                float(tel["cpu_load"]),
                float(tel["gpu_load"]),
                float(tel["mem_used"]),
            ],
            dtype=torch.float32,
            device=DEFAULT_DEVICE,
        )
        return feat.unsqueeze(0)

    def _choose_device_for_layer(self, layer_name: str, layer_depth: int, feats: torch.Tensor = None) -> torch.device:
        if len(self.devices) == 1 or self.router_net is None or torch is None:
            return self.devices[0]
        with torch.no_grad():
            if feats is None:
                dummy_x = torch.zeros((1, 16), device=DEFAULT_DEVICE)
                feats = self._build_router_features(layer_name, layer_depth, dummy_x)
            score = self.router_net(feats).item()
        idx = int(max(0, min(len(self.devices) - 1, int(score * len(self.devices))))) if len(self.devices) > 1 else 0
        return self.devices[idx]

    def _kv_cache_sparsify(self, kv_tensor: torch.Tensor) -> torch.Tensor:
        if kv_tensor is None or torch is None or not self.kv_skip_enabled:
            return kv_tensor
        mask = kv_tensor.abs() > self.kv_sparsity_threshold
        return kv_tensor * mask

    def linear(self, layer_name, weight, bias, x, layer_depth: int = 0):
        if torch is None:
            return x

        feats = None
        router_score = None
        if self.router_net is not None:
            with torch.no_grad():
                feats = self._build_router_features(layer_name, layer_depth, x.to(DEFAULT_DEVICE))
                GLOBAL_CACHE["last_router_feats"] = feats
                router_score = self.router_net(feats).item()

        device = self._choose_device_for_layer(layer_name, layer_depth, feats)

        t0 = time.time()

        w = weight.to(device)
        b = bias.to(device) if bias is not None else None
        inp = x.to(device)

        if router_score is not None:
            self.sparsity_threshold = 1e-3 + max(0.0, 0.01 * (1.0 - router_score))

        w = self._emulate_fp8(w)
        w = self._apply_sparsity(w)

        out = inp @ w.T
        if b is not None:
            out = out + b

        dt = (time.time() - t0) * 1000.0
        self._stats["calls"] += 1
        n = self._stats["calls"]
        self._stats["avg_latency_ms"] = (
            (self._stats["avg_latency_ms"] * (n - 1) + dt) / n
        )
        self._stats["router_data"].setdefault(
            layer_name,
            {"depth": layer_depth, "calls": 0, "device": str(device), "last_score": router_score},
        )
        self._stats["router_data"][layer_name]["calls"] += 1
        self._stats["router_data"][layer_name]["last_score"] = router_score

        return out

    def attention(self, layer_name, q, k, v, layer_depth: int = 0):
        if torch is None:
            return q @ k.transpose(-2, -1) @ v

        feats = None
        router_score = None
        if self.router_net is not None:
            with torch.no_grad():
                feats = self._build_router_features(layer_name, layer_depth, q.to(DEFAULT_DEVICE))
                GLOBAL_CACHE["last_router_feats"] = feats
                router_score = self.router_net(feats).item()

        device = self._choose_device_for_layer(layer_name, layer_depth, feats)

        t0 = time.time()

        q = q.to(device)
        k = k.to(device)
        v = v.to(device)

        if router_score is not None:
            self.kv_sparsity_threshold = 1e-3 + max(0.0, 0.01 * (1.0 - router_score))

        k = self._kv_cache_sparsify(k)
        v = self._kv_cache_sparsify(v)

        attn_scores = q @ k.transpose(-2, -1)
        attn_probs = torch.softmax(attn_scores, dim=-1)
        out = attn_probs @ v

        dt = (time.time() - t0) * 1000.0
        self._stats["calls"] += 1
        n = self._stats["calls"]
        self._stats["avg_latency_ms"] = (
            (self._stats["avg_latency_ms"] * (n - 1) + dt) / n
        )
        self._stats["router_data"].setdefault(
            layer_name,
            {"depth": layer_depth, "calls": 0, "device": str(device), "last_score": router_score},
        )
        self._stats["router_data"][layer_name]["calls"] += 1
        self._stats["router_data"][layer_name]["last_score"] = router_score

        return out

    def stats(self) -> Dict[str, Any]:
        out = dict(self._stats)
        out["router_model"] = "RouterNet-MLP-v8.4" if self.router_net is not None else "none"
        out["router_ckpt_path"] = self.router_ckpt_path
        out["kv_skip_enabled"] = self.kv_skip_enabled
        return out

    def save_router_ckpt(self, path: str = None):
        if torch is None or self.router_net is None:
            return
        path = path or self.router_ckpt_path
        try:
            torch.save(self.router_net.state_dict(), path)
            print(f"[Router] Saved checkpoint to {path}")
        except Exception as e:
            print(f"[Router] Failed to save checkpoint: {e}")

    def load_router_ckpt(self, path: str = None):
        if torch is None or self.router_net is None:
            return
        path = path or self.router_ckpt_path
        if not os.path.isfile(path):
            print(f"[Router] No checkpoint at {path}")
            return
        try:
            state = torch.load(path, map_location=DEFAULT_DEVICE)
            self.router_net.load_state_dict(state)
            self.router_net.to(DEFAULT_DEVICE)
            self.router_net.eval()
            print(f"[Router] Loaded checkpoint from {path}")
        except Exception as e:
            print(f"[Router] Failed to load checkpoint: {e}")

    def _try_load_router_ckpt(self):
        if os.path.isfile(self.router_ckpt_path):
            self.load_router_ckpt(self.router_ckpt_path)


EXECUTOR = ForkliftExecutor()


def save_router_checkpoint(path: str = None):
    EXECUTOR.save_router_ckpt(path)


def load_router_checkpoint(path: str = None):
    EXECUTOR.load_router_ckpt(path)


# ============================================================
# ForkliftLinear / Attention wrappers
# ============================================================
if nn is not None:

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


    class ForkliftAttention(nn.Module):
        def __init__(self, base: nn.Module, name: str, executor: ForkliftExecutor, depth: int = 0):
            super().__init__()
            self.base = base
            self.name = name
            self.executor = executor
            self.depth = depth

        def forward(self, *args, **kwargs):
            if hasattr(self.base, "forward"):
                out = self.base(*args, **kwargs)
                return out
            return args[0]

    def _patch_module_with_forklift(module: nn.Module, prefix: str = "", depth: int = 0):
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
    def patch_model_with_forklift(model):
        pass


# ============================================================
# TinyFallback model
# ============================================================
class TinyFallback(nn.Module if nn is not None else object):
    def __init__(self, vocab_size: int = 256, hidden_dim: int = 64):
        if nn is not None:
            super().__init__()
            self.emb = nn.Embedding(vocab_size, hidden_dim)
            self.lin = nn.Linear(hidden_dim, vocab_size)
        else:
            pass

    def forward(self, input_ids):
        if nn is None or torch is None:
            return input_ids
        x = self.emb(input_ids)
        x = x.mean(dim=1)
        logits = self.lin(x)
        return logits

    def generate(self, input_ids, max_new_tokens: int = 32, **kwargs):
        if torch is None:
            return input_ids
        return input_ids


# ============================================================
# Model loading
# ============================================================
def _build_dummy_tokenizer():
    class DummyTok:
        def __init__(self):
            self.eos_token_id = 0

        def __call__(self, text, return_tensors=None):
            ids = [ord(c) % 256 for c in text]
            if torch is not None:
                t = torch.tensor([ids], dtype=torch.long)
            else:
                t = [[i for i in ids]]
            return {"input_ids": t}

        def decode(self, ids, skip_special_tokens=True):
            if torch is not None and isinstance(ids, torch.Tensor):
                ids = ids.tolist()
            return "".join(chr(int(i) % 256) for i in ids)
    return DummyTok()


def load_model(model_name: str = PRIMARY_MODEL_NAME):
    global CURRENT_MODEL, CURRENT_TOKENIZER, CURRENT_MODEL_NAME, IS_FALLBACK_MODEL

    if CURRENT_MODEL is not None and CURRENT_TOKENIZER is not None:
        return

    if AutoTokenizer is None or AutoModelForCausalLM is None or torch is None:
        print("[Node] Transformers / torch not available, using TinyFallback.")
        tok = _build_dummy_tokenizer()
        mdl = TinyFallback().to(DEFAULT_DEVICE if torch else "cpu")
        CURRENT_MODEL = mdl
        CURRENT_TOKENIZER = tok
        CURRENT_MODEL_NAME = "TinyFallback"
        IS_FALLBACK_MODEL = True
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
            tok = _build_dummy_tokenizer()

        mdl = TinyFallback().to(DEFAULT_DEVICE if torch else "cpu")
        mdl.eval()

        CURRENT_MODEL = mdl
        CURRENT_TOKENIZER = tok
        CURRENT_MODEL_NAME = "TinyFallback"
        IS_FALLBACK_MODEL = True
        print("[Node] Using TinyFallback model.")


# ============================================================
# Speculative decoding stub
# ============================================================
def speculative_generate(mdl, inputs, max_new_tokens: int, tok):
    if isinstance(mdl, TinyFallback):
        return mdl.generate(inputs["input_ids"], max_new_tokens=max_new_tokens)
    return mdl.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        do_sample=True,
        top_p=0.9,
        temperature=0.8,
        pad_token_id=getattr(tok, "eos_token_id", None),
    )


# ============================================================
# Text generation API
# ============================================================
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

        out_ids = speculative_generate(mdl, inputs, max_new_tokens, tok)

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
        return prompt, {"model_name": "no_torch", "is_fallback": True, "latency_ms": 0.0}


# ============================================================
# RPC server
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
    while True:
        conn, addr = s.accept()
        t = threading.Thread(target=handle_rpc_client, args=(conn, addr), daemon=True)
        t.start()


# ============================================================
# Simple local CLI
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
# Router-only training mode
# ============================================================
def router_training_loop(num_iters: int = 100, max_new_tokens: int = 32):
    print(f"[RouterTrain] Starting router-only training for {num_iters} iterations")
    for i in range(num_iters):
        prompt = f"SYNTHETIC_PROMPT_{i}"
        text, stats = generate_text(prompt, max_new_tokens=max_new_tokens)
        if i % 10 == 0:
            print(f"[RouterTrain] iter={i}, latency={stats.get('latency_ms', 0):.2f} ms")
    print("[RouterTrain] Done.")


# ============================================================
# CLI / diagnostics
# ============================================================
def self_diagnostic_report(args):
    print("\n[Diagnostics] Borg v8.4 self-check")
    print(f"  HAS_CUDA: {HAS_CUDA}")
    print(f"  NUM_GPUS: {NUM_GPUS}")
    print(f"  DEFAULT_DEVICE: {DEFAULT_DEVICE}")
    print(f"  PRIMARY_MODEL_NAME: {PRIMARY_MODEL_NAME}")
    print(f"  RPC host: {args.host}")
    print(f"  RPC port: {args.rpc_port}")
    print(f"  headless_node: {getattr(args, 'headless_node', False)}")
    print(f"  gui: {getattr(args, 'gui', False)}")
    print(f"  project_root: {getattr(args, 'project_root', None)}")
    print(f"  optimize_file: {getattr(args, 'optimize_file', None)}")
    print(f"  router_ckpt: {GLOBAL_CONFIG['router_ckpt']}")
    print(f"  router_save: {getattr(args, 'router_save', None)}")
    print(f"  router_load: {getattr(args, 'router_load', None)}")
    print(f"  router_train_only: {getattr(args, 'router_train_only', False)}")
    print(f"  CONFIG_PATH: {CONFIG_PATH}")
    print(f"  GLOBAL_CONFIG: {GLOBAL_CONFIG}")


def safe_argparse():
    parser = argparse.ArgumentParser(
        description="Borg Compiler v8.4 + Forklift Node - Universal Autopilot"
    )
    parser.add_argument("--server_host", type=str, default="127.0.0.1")
    parser.add_argument("--server_port", type=int, default=65432)
    parser.add_argument("--optimize_file", type=str, default=None)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--project_root", type=str, default=None)
    parser.add_argument("--project_pattern", type=str, default="*.py")
    parser.add_argument("--in_place", action="store_true")
    parser.add_argument("--rpc-port", type=int, default=GLOBAL_CONFIG["rpc_port"])
    parser.add_argument("--host", type=str, default=GLOBAL_CONFIG["rpc_host"])
    parser.add_argument("--headless-node", action="store_true")
    parser.add_argument("--diagnostic", action="store_true")

    parser.add_argument("--router-save", type=str, default=None)
    parser.add_argument("--router-load", type=str, default=None)
    parser.add_argument("--router-train-only", action="store_true")
    parser.add_argument("--router-train-iters", type=int, default=100)
    parser.add_argument("--router-train-max-tokens", type=int, default=32)

    args, unknown = parser.parse_known_args()

    defaults = {
        "headless_node": GLOBAL_CONFIG["headless_default"],
        "gui": GLOBAL_CONFIG["gui_default"],
        "project_root": GLOBAL_CONFIG["project_root_default"] or None,
    }
    for k, v in defaults.items():
        if not hasattr(args, k):
            setattr(args, k, v)

    return args, unknown


def run_cli(args):
    if args.project_root:
        optimize_project(args.project_root, pattern=args.project_pattern, in_place=args.in_place)
        return

    optimizer = Optimizer()
    generator = CodeGenerator(optimizer)

    if args.optimize_file and os.path.isfile(args.optimize_file):
        with open(args.optimize_file, "r", encoding="utf-8") as f:
            original_code = f.read()
    else:
        original_code = SAMPLE_ORIGINAL_CODE.format(
            host=args.server_host, port=args.server_port
        )

    optimized_code = generator.generate_optimized_code(original_code)
    print(optimized_code)


# ============================================================
# Main
# ============================================================
def main():
    load_dynamic_config()
    threading.Thread(target=hot_reload_config_loop, daemon=True).start()

    args, unknown = safe_argparse()

    threading.Thread(target=telemetry_broadcast_loop, daemon=True).start()
    threading.Thread(target=telemetry_listener_loop, daemon=True).start()
    threading.Thread(target=distributed_cache_broadcast_loop, args=(GLOBAL_CACHE,), daemon=True).start()
    threading.Thread(target=distributed_cache_listener_loop, daemon=True).start()

    threading.Thread(target=rpc_server_loop, args=(args.host, args.rpc_port), daemon=True).start()

    if args.router_load:
        load_router_checkpoint(args.router_load)

    if args.diagnostic:
        self_diagnostic_report(args)
        return

    if args.router_train_only:
        router_training_loop(num_iters=args.router_train_iters,
                             max_new_tokens=args.router_train_max_tokens)
        if args.router_save:
            save_router_checkpoint(args.router_save)
        return

    if args.headless_node:
        print("[Node] Running in headless RPC mode.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        if args.router_save:
            save_router_checkpoint(args.router_save)
        return

    if args.gui or (len(unknown) == 0 and args.optimize_file is None and args.project_root is None):
        root = tk.Tk()
        app = BorgCompilerGUI(root)
        root.mainloop()
    else:
        run_cli(args)

    if args.router_save:
        save_router_checkpoint(args.router_save)


if __name__ == "__main__":
    try:
        main()
    finally:
        _global_batch_sender.stop()
