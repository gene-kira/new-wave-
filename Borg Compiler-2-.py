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
        # Placeholder: real implementation could send over network.
        joined = "\n".join(buffer)
        print("[BATCH FLUSH]")
        print(joined)

    def stop(self):
        self._stop_event.set()
        self._thread.join(timeout=1.0)


_global_batch_sender = BatchSender()


def batch_send(data: str):
    """
    Public batching API used by transformed code.
    """
    _global_batch_sender.send(data)


# ---------------------------------------------------------------------------
# Parallel execution helper
# ---------------------------------------------------------------------------
def parallel_for(iterable, func):
    """
    Execute func(item) for each item in iterable using ThreadPoolExecutor.
    """
    with concurrent.futures.ThreadPoolExecutor() as executor:
        list(executor.map(func, iterable))


# ---------------------------------------------------------------------------
# LibraryResolver: aggressive autoloader (Option B)
# ---------------------------------------------------------------------------
class LibraryResolver:
    """
    Aggressive autoloader:
    - Scans AST for used names
    - Excludes builtins and locally defined names
    - Treats remaining names as modules to import
    """

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
            # Aggressive mode: treat any remaining name as a module to import
            missing.add(name)

        return missing


# ---------------------------------------------------------------------------
# Plugin system
# ---------------------------------------------------------------------------
class PluginManager:
    def __init__(self):
        self.plugins = []  # list of callables: plugin(tree: ast.AST) -> ast.AST

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
        # If there are returns in the middle of blocks, we can try to clean after them
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
        # Always allowed; it's analysis only
        return True

    def ai_assisted_optimization(self, tree: ast.AST) -> bool:
        # Stub: always allowed
        return True

    def bytecode_optimization(self, tree: ast.AST) -> bool:
        # Stub: always allowed
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
        }
        self.imported_libraries = set()
        self.resolver = LibraryResolver()
        self.plugin_manager = PluginManager()
        self.type_info = {}

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
        }

    def generate_optimized_code(self, original_code: str) -> str:
        try:
            tree = ast.parse(original_code)
        except SyntaxError:
            return original_code

        decisions = self.predict_optimization(original_code)

        # Collect existing imports
        self.imported_libraries.clear()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for name in node.names:
                    base = name.name.split(".")[0]
                    self.imported_libraries.add(base)

        # Aggressive autoloader: resolve missing imports
        missing_imports = self.resolver.resolve_missing_imports(
            tree, self.imported_libraries
        )
        self.imported_libraries.update(missing_imports)

        try:
            if self.optimization_rules["type_inference"] and decisions["type_inference"]:
                self._type_inference(tree)

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
            if self.optimization_rules["ai_assisted_optimization"] and decisions["ai_assisted_optimization"]:
                tree = self.ai_assisted_optimization(tree)
            if self.optimization_rules["bytecode_optimization"] and decisions["bytecode_optimization"]:
                tree = self.bytecode_optimization(tree)

            # Plugin system
            tree = self.plugin_manager.apply(tree)

        except Exception:
            traceback.print_exc()
            return original_code

        # Ensure core libraries
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
    # AST transforms and analysis
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
        """
        Conservative parallelization:
        Transform loops of the form:
            for x in iterable:
                func(x)
        into:
            parallel_for(iterable, func)
        """
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
        """
        Remove statements that are unreachable after return/raise/break/continue
        within the same block.
        """
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
        """
        Fold simple constant expressions like 2+3, 4*5, etc.
        """
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
        """
        Unroll small range loops:
            for i in range(N): body
        where N <= 4
        """
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
        """
        Very basic type inference: track simple assignments of constants.
        """
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
        """
        Stub for AI-assisted optimization.
        Currently just annotates the module with a comment.
        """
        comment = ast.Expr(value=ast.Constant(value="# AI-assisted optimization placeholder"))
        if isinstance(tree, ast.Module):
            tree.body.insert(0, comment)
        return tree

    def bytecode_optimization(self, tree: ast.AST) -> ast.AST:
        """
        Stub for bytecode-level optimization:
        - Compile to ensure validity
        - Could be extended to manipulate code objects
        """
        try:
            code_obj = compile(tree, "<bytecode_opt>", "exec")
            if not isinstance(code_obj, types.CodeType):
                return tree
        except Exception:
            traceback.print_exc()
        return tree


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
        self.root.title("Borg Compiler v3.0 - AST Optimizer (Aggressive Autoloader)")

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
        description="Borg Compiler v3.0 - AST-based Python optimizer with aggressive autoloader and advanced passes"
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

    # If launched by double-click (no args), default to GUI
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
