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


# ---------------------------------------------------------------------------
# Batch sending infrastructure (real batching, generic)
# ---------------------------------------------------------------------------
class BatchSender:
    def __init__(self, flush_interval=0.5, max_batch_size=64 * 1024):
        self.flush_interval = flush_interval
        self.max_batch_size = max_batch_size
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def send(self, data: str):
        self._queue.put(data)

    def _worker(self):
        buffer = []
        last_flush = None
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
        # Real batching: here we just print as a placeholder.
        # You can replace this with actual socket sending logic.
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
    _global_batch_sender.send(str(data))


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
# Optimizer: decides when to apply transformations
# ---------------------------------------------------------------------------
class Optimizer:
    def reduce_network_calls(self, tree: ast.AST) -> bool:
        # Apply batching if we see any .send or .sendall calls
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("send", "sendall"):
                    return True
        return False

    def minimize_memory_usage(self, tree: ast.AST) -> bool:
        # Apply memory optimization if we see large list literals
        for node in ast.walk(tree):
            if isinstance(node, ast.List) and len(node.elts) > 100:
                return True
        return False

    def parallelize_operations(self, tree: ast.AST) -> bool:
        # Apply parallelization if we see simple for-loops
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                return True
        return False


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
        }
        self.imported_libraries = set()

    def predict_optimization(self, original_code: str) -> dict:
        tree = ast.parse(original_code)
        return {
            "reduce_network_calls": self.optimizer.reduce_network_calls(tree),
            "minimize_memory_usage": self.optimizer.minimize_memory_usage(tree),
            "parallelize_operations": self.optimizer.parallelize_operations(tree),
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

        try:
            if self.optimization_rules["reduce_network_calls"] and decisions["reduce_network_calls"]:
                tree = self.reduce_network_calls(tree)
            if self.optimization_rules["minimize_memory_usage"] and decisions["minimize_memory_usage"]:
                tree = self.minimize_memory_usage(tree)
            if self.optimization_rules["parallelize_operations"] and decisions["parallelize_operations"]:
                tree = self.parallelize_operations(tree)
        except Exception:
            traceback.print_exc()
            return original_code

        # Ensure required libraries
        self.imported_libraries.update({"socket", "concurrent.futures", "ast"})

        try:
            optimized_code = ast.unparse(tree)
        except Exception:
            traceback.print_exc()
            return original_code

        import_lines = [f"import {lib}" for lib in sorted(self.imported_libraries)]
        header = "\n".join(import_lines)
        return f"{header}\n\n{optimized_code}"

    # ----------------------------------------------------------------------
    # AST transforms
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
# Diff helper (for dry-run / GUI preview)
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
        self.root.title("Borg Compiler - AST Optimizer")

        self.optimizer = Optimizer()
        self.generator = CodeGenerator(self.optimizer)

        # Top frame: buttons
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

        # Middle frame: original code
        lbl_original = tk.Label(root, text="Original Code")
        lbl_original.pack(anchor=tk.W, padx=5)
        self.txt_original = scrolledtext.ScrolledText(root, height=15)
        self.txt_original.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Bottom frame: optimized code
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
# CLI / main entrypoint
# ---------------------------------------------------------------------------
def run_cli(args):
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
        description="Borg Compiler - AST-based Python optimizer"
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

    args, unknown = parser.parse_known_args()

    # If launched by double-click (no args), default to GUI
    if args.gui or (len(unknown) == 0 and args.optimize_file is None):
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
