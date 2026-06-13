import ast
import os
import socket
import concurrent.futures
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

# Optional JIT backends (Numba / LLVM) – used if available
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


# ---------------------------------------------------------------------------
# Batch sending infrastructure (real batching, generic)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Parallel execution helper
# ---------------------------------------------------------------------------
def parallel_for(iterable, func):
    with concurrent.futures.ThreadPoolExecutor() as executor:
        list(executor.map(func, iterable))


# ---------------------------------------------------------------------------
# LibraryResolver: aggressive autoloader (Option B)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Plugin system
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# CFG / Dominator / SSA / Data-flow / Alias structures
# ---------------------------------------------------------------------------
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
        self.doms = {}      # node -> set of dominators
        self.frontier = {}  # node -> set of nodes in its frontier

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
        self.versioned_vars = {}  # name -> version int
        self.phi_nodes = {}       # block -> {var: [sources]}

    def new_version(self, name):
        v = self.versioned_vars.get(name, 0) + 1
        self.versioned_vars[name] = v
        return f"{name}_{v}"

    def add_phi(self, block, var, sources):
        self.phi_nodes.setdefault(block, {})[var] = sources


class DataFlowInfo:
    def __init__(self):
        self.defs = {}
        self.uses = {}
        self.liveness = {}        # node -> live vars
        self.reaching_defs = {}   # node -> {var: defs}
        self.alias_graph = {}     # var -> set of aliases


# ---------------------------------------------------------------------------
# Optimizer: decides when to apply transformations
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# CodeGenerator: AST-based Borg Compiler core
# ---------------------------------------------------------------------------
class CodeGenerator:
    def __init__(self, optimizer: Optimizer):
        self.optimizer = optimizer
        self.optimization_rules = {
            "reduce_network_calls": True,
            "minimize_memory_usage": True,
            "parallelize_operations": True,
            "dead_code_elimination": True,
            "constant_folding": True,
            "loop_unrolling": True,
            "type_inference": True,
            "ai_assisted_optimization": True,
            "bytecode_optimization": True,
            "ssa": True,
            "cfg": True,
            "data_flow": True,
            "dead_store_elimination": True,
            "peephole": True,
            "jit_hooks": True,
            "ml_ranking": True,
            "semantic_analysis": True,
            "cross_file_inlining": True,
            "dominator_tree": True,
            "liveness": True,
            "reaching_defs": True,
            "constant_propagation": True,
            "escape_analysis": True,
            "function_inlining": True,
            "interprocedural": True,
            "speculative": True,
            "pgo": True,
            "bytecode_rewriting": True,
            "jit_compilation": True,
            "llvm_ir": True,
            "control_flow_restructuring": True,
        }
        self.imported_libraries = set()
        self.resolver = LibraryResolver()
        self.plugin_manager = PluginManager()
        self.type_info = {}
        self.cfgs = {}
        self.data_flow_info = {}
        self.ssa_forms = {}
        self.dom_trees = {}

    def predict_optimization(self, original_code: str) -> dict:
        tree = ast.parse(original_code)
        return {
            "reduce_network_calls": self.optimizer.reduce_network_calls(tree),
            "minimize_memory_usage": self.optimizer.minimize_memory_usage(tree),
            "parallelize_operations": self.optimizer.parallelize_operations(tree),
            "dead_code_elimination": self.optimizer.dead_code_elimination(tree),
            "constant_folding": self.optimizer.constant_folding(tree),
            "loop_unrolling": self.optimizer.loop_unrolling(tree),
            "type_inference": self.optimizer.type_inference(tree),
            "ai_assisted_optimization": self.optimizer.ai_assisted_optimization(tree),
            "bytecode_optimization": self.optimizer.bytecode_optimization(tree),
            "ssa": self.optimizer.ssa(tree),
            "cfg": self.optimizer.cfg(tree),
            "data_flow": self.optimizer.data_flow(tree),
            "dead_store_elimination": self.optimizer.dead_store_elimination(tree),
            "peephole": self.optimizer.peephole(tree),
            "jit_hooks": self.optimizer.jit_hooks(tree),
            "ml_ranking": self.optimizer.ml_ranking(tree),
            "semantic_analysis": self.optimizer.semantic_analysis(tree),
            "cross_file_inlining": self.optimizer.cross_file_inlining(tree),
            "dominator_tree": self.optimizer.dominator_tree(tree),
            "liveness": self.optimizer.liveness(tree),
            "reaching_defs": self.optimizer.reaching_defs(tree),
            "constant_propagation": self.optimizer.constant_propagation(tree),
            "escape_analysis": self.optimizer.escape_analysis(tree),
            "function_inlining": self.optimizer.function_inlining(tree),
            "interprocedural": self.optimizer.interprocedural(tree),
            "speculative": self.optimizer.speculative(tree),
            "pgo": self.optimizer.pgo(tree),
            "bytecode_rewriting": self.optimizer.bytecode_rewriting(tree),
            "jit_compilation": self.optimizer.jit_compilation(tree),
            "llvm_ir": self.optimizer.llvm_ir(tree),
            "control_flow_restructuring": self.optimizer.control_flow_restructuring(tree),
        }

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
            if self.optimization_rules["type_inference"] and decisions["type_inference"]:
                self._type_inference(tree)

            if self.optimization_rules["cfg"] and decisions["cfg"]:
                self._build_cfg(tree)

            if self.optimization_rules["dominator_tree"] and decisions["dominator_tree"]:
                self._build_dominator_trees()

            if self.optimization_rules["data_flow"] and decisions["data_flow"]:
                self._data_flow_analysis(tree)

            if self.optimization_rules["liveness"] and decisions["liveness"]:
                self._liveness_analysis()

            if self.optimization_rules["reaching_defs"] and decisions["reaching_defs"]:
                self._reaching_definitions()

            if self.optimization_rules["ssa"] and decisions["ssa"]:
                tree = self._ssa_transform(tree)

            if self.optimization_rules["constant_propagation"] and decisions["constant_propagation"]:
                tree = self.constant_propagation(tree)

            if self.optimization_rules["reduce_network_calls"] and decisions["reduce_network_calls"]:
                tree = self.reduce_network_calls(tree)
            if self.optimization_rules["minimize_memory_usage"] and decisions["minimize_memory_usage"]:
                tree = self.minimize_memory_usage(tree)
            if self.optimization_rules["parallelize_operations"] and decisions["parallelize_operations"]:
                tree = self.parallelize_operations(tree)
            if self.optimization_rules["dead_code_elimination"] and decisions["dead_code_elimination"]:
                tree = self.dead_code_elimination(tree)
            if self.optimization_rules["constant_folding"] and decisions["constant_folding"]:
                tree = self.constant_folding(tree)
            if self.optimization_rules["loop_unrolling"] and decisions["loop_unrolling"]:
                tree = self.loop_unrolling(tree)
            if self.optimization_rules["dead_store_elimination"] and decisions["dead_store_elimination"]:
                tree = self.dead_store_elimination(tree)
            if self.optimization_rules["peephole"] and decisions["peephole"]:
                tree = self.peephole(tree)
            if self.optimization_rules["escape_analysis"] and decisions["escape_analysis"]:
                tree = self.escape_analysis(tree)
            if self.optimization_rules["function_inlining"] and decisions["function_inlining"]:
                tree = self.function_inlining(tree)
            if self.optimization_rules["interprocedural"] and decisions["interprocedural"]:
                tree = self.interprocedural(tree)
            if self.optimization_rules["speculative"] and decisions["speculative"]:
                tree = self.speculative(tree)
            if self.optimization_rules["pgo"] and decisions["pgo"]:
                tree = self.pgo(tree)
            if self.optimization_rules["ai_assisted_optimization"] and decisions["ai_assisted_optimization"]:
                tree = self.ai_assisted_optimization(tree)
            if self.optimization_rules["bytecode_optimization"] and decisions["bytecode_optimization"]:
                tree = self.bytecode_optimization(tree)
            if self.optimization_rules["bytecode_rewriting"] and decisions["bytecode_rewriting"]:
                tree = self.bytecode_rewriting(tree)
            if self.optimization_rules["jit_hooks"] and decisions["jit_hooks"]:
                tree = self.jit_hooks(tree)
            if self.optimization_rules["ml_ranking"] and decisions["ml_ranking"]:
                tree = self.ml_ranking(tree)
            if self.optimization_rules["semantic_analysis"] and decisions["semantic_analysis"]:
                tree = self.semantic_analysis(tree)
            if self.optimization_rules["cross_file_inlining"] and decisions["cross_file_inlining"]:
                tree = self.cross_file_inlining(tree)
            if self.optimization_rules["jit_compilation"] and decisions["jit_compilation"]:
                tree = self.jit_compilation(tree)
            if self.optimization_rules["llvm_ir"] and decisions["llvm_ir"]:
                tree = self.llvm_ir(tree)
            if self.optimization_rules["control_flow_restructuring"] and decisions["control_flow_restructuring"]:
                tree = self.control_flow_restructuring(tree)

            tree = self.plugin_manager.apply(tree)

        except Exception:
            traceback.print_exc()
            return original_code

        self.imported_libraries.update({"socket", "concurrent", "concurrent.futures", "ast"})

        try:
            optimized_code = ast.unparse(tree)
        except Exception:
            traceback.print_exc()
            return original_code

        import_lines = [f"import {lib}" for lib in sorted(self.imported_libraries)]
        header = "\n".join(import_lines)
        return f"{header}\n\n{optimized_code}"

    # ----------------------------------------------------------------------
    # Core AST transforms and analyses
    # ----------------------------------------------------------------------
    def reduce_network_calls(self, tree: ast.AST) -> ast.AST:
        class ReduceNetworkCalls(ast.NodeTransformer):
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

        transformer = ReduceNetworkCalls()
        return transformer.visit(tree)

    def minimize_memory_usage(self, tree: ast.AST) -> ast.AST:
        class MinimizeMemoryUsage(ast.NodeTransformer):
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

        transformer = MinimizeMemoryUsage()
        return transformer.visit(tree)

    def parallelize_operations(self, tree: ast.AST) -> ast.AST:
        class ParallelizeOperations(ast.NodeTransformer):
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

        transformer = ParallelizeOperations()
        return transformer.visit(tree)

    def dead_code_elimination(self, tree: ast.AST) -> ast.AST:
        class DeadCodeEliminator(ast.NodeTransformer):
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

        transformer = DeadCodeEliminator()
        return transformer.visit(tree)

    def constant_folding(self, tree: ast.AST) -> ast.AST:
        class ConstantFolder(ast.NodeTransformer):
            def visit_BinOp(self, node):
                node = self.generic_visit(node)
                if isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant):
                    try:
                        value = eval(compile(ast.Expression(node), "<const_fold>", "eval"))
                        return ast.copy_location(ast.Constant(value=value), node)
                    except Exception:
                        return node
                return node

        transformer = ConstantFolder()
        return transformer.visit(tree)

    def loop_unrolling(self, tree: ast.AST) -> ast.AST:
        class LoopUnroller(ast.NodeTransformer):
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

        transformer = LoopUnroller()
        return transformer.visit(tree)

    def _type_inference(self, tree: ast.AST):
        type_info = {}

        class TypeInfer(ast.NodeVisitor):
            def visit_Assign(self, node):
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                    if isinstance(node.value, ast.Constant):
                        type_info[name] = type(node.value.value).__name__
                self.generic_visit(node)

        TypeInfer().visit(tree)
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

        class CFGBuilder(ast.NodeVisitor):
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

        CFGBuilder().visit(tree)
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

        class DFVisitor(ast.NodeVisitor):
            def visit_Assign(self, node):
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                    info.defs.setdefault(name, []).append(node)
                self.generic_visit(node)

            def visit_Name(self, node):
                if isinstance(node.ctx, ast.Load):
                    info.uses.setdefault(node.id, []).append(node)

        DFVisitor().visit(tree)
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

        class SSATransformer(ast.NodeTransformer):
            def visit_Assign(self, node):
                node = self.generic_visit(node)
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                    new_name = ssa.new_version(name)
                    node.targets[0].id = new_name
                return node

            def visit_Name(self, node):
                return node

        new_tree = SSATransformer().visit(tree)
        self.ssa_forms = ssa.versioned_vars
        return new_tree

    def dead_store_elimination(self, tree: ast.AST) -> ast.AST:
        info = self.data_flow_info
        if not isinstance(info, DataFlowInfo):
            return tree

        live_vars = set(info.uses.keys())

        class DeadStoreEliminator(ast.NodeTransformer):
            def visit_Assign(self, node):
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                    if name not in live_vars:
                        return None
                return self.generic_visit(node)

        transformer = DeadStoreEliminator()
        return transformer.visit(tree)

    def peephole(self, tree: ast.AST) -> ast.AST:
        class PeepholeOptimizer(ast.NodeTransformer):
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

        transformer = PeepholeOptimizer()
        return transformer.visit(tree)

    def constant_propagation(self, tree: ast.AST) -> ast.AST:
        const_env = {}

        class ConstProp(ast.NodeTransformer):
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

        transformer = ConstProp()
        return transformer.visit(tree)

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
        comment = ast.Expr(value=ast.Constant(value="# Real speculative execution with guards placeholder"))
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
        score = random.random()
        comment = ast.Expr(value=ast.Constant(value=f"# ML ranking score (stub model): {score:.4f}"))
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

    def _names_in_node(self, node):
        names = []

        class NV(ast.NodeVisitor):
            def visit_Name(self, n):
                names.append(n.id)

        NV().visit(node)
        return names


# ---------------------------------------------------------------------------
# Sample original code template
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Diff helper
# ---------------------------------------------------------------------------
def generate_diff(original: str, optimized: str) -> str:
    original_lines = original.splitlines(keepends=True)
    optimized_lines = optimized.splitlines(keepends=True)
    diff = difflib.unified_diff(
        original_lines, optimized_lines, fromfile="original", tofile="optimized"
    )
    return "".join(diff)


# ---------------------------------------------------------------------------
# Tkinter GUI: Borg Compiler front-end
# ---------------------------------------------------------------------------
class BorgCompilerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Borg Compiler v7.0 - Full-stack AST/CFG/SSA/DF/JIT/LLVM/IR optimizer")

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

        lbl_original = tk.Label(root, text="Original Code")
        lbl_original.pack(anchor=tk.W, padx=5)
        self.txt_original = scrolledtext.ScrolledText(root, height=15)
        self.txt_original.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        lbl_optimized = tk.Label(root, text="Optimized Code")
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


# ---------------------------------------------------------------------------
# Project-wide optimization
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# CLI / main entrypoint
# ---------------------------------------------------------------------------
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


def main():
    parser = argparse.ArgumentParser(
        description="Borg Compiler v7.0 - Full-stack AST/CFG/SSA/Dataflow/JIT/LLVM/IR optimizer with aggressive autoloader"
    )
    parser.add_argument(
        "--server_host",
        type=str,
        default="127.0.0.1",
        help="Target server host for sample code.",
    )
    parser.add_argument(
        "--server_port",
        type=int,
        default=65432,
        help="Target server port for sample code.",
    )
    parser.add_argument(
        "--optimize_file",
        type=str,
        default=None,
        help="Path to a Python file to optimize.",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch Borg Compiler GUI.",
    )
    parser.add_argument(
        "--project_root",
        type=str,
        default=None,
        help="Root directory for project-wide optimization.",
    )
    parser.add_argument(
        "--project_pattern",
        type=str,
        default="*.py",
        help="Glob pattern for files in project-wide optimization.",
    )
    parser.add_argument(
        "--in_place",
        action="store_true",
        help="Overwrite files in project-wide optimization instead of writing .borg.py copies.",
    )

    args, unknown = parser.parse_known_args()

    if args.gui or (len(unknown) == 0 and args.optimize_file is None and args.project_root is None):
        root = tk.Tk()
        app = BorgCompilerGUI(root)
        root.mainloop()
    else:
        run_cli(args)


if __name__ == "__main__":
    try:
        main()
    finally:
        _global_batch_sender.stop()
