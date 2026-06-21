"""
HybridBrain v6.1 – Remote Organ Server (Ø‑aware)
Consumes and produces universal 0/1/Ø envelopes.
"""

import json
import socket
import threading
from typing import Dict, Any, Tuple

# Import your universal encoding API + semantic vector
# from your main HybridBrain file:
# from hybridbrain_v6 import UniversalEncodingAPI, SemanticVector

class RemoteOrganServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 9000):
        self.host = host
        self.port = port
        self.organs: Dict[str, Any] = {}
        self.running = False

    def register_organ(self, name: str, organ: Any):
        """Register a local organ implementation."""
        self.organs[name] = organ

    def _handle_client(self, conn: socket.socket):
        try:
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break

            line = data.decode("utf-8").strip()
            if not line:
                return

            req = json.loads(line)

            organ_name = req.get("organ")
            method_name = req.get("method")
            envelope = req.get("envelope", {})

            # Decode Ø-envelope
            payload, semantic_vec, meta = UniversalEncodingAPI.decode_payload(envelope)

            organ = self.organs.get(organ_name)
            if organ is None:
                resp_payload = {"error": f"Unknown organ '{organ_name}'"}
                resp_env = UniversalEncodingAPI.encode_payload(resp_payload, semantic_vec, meta)
            else:
                method = getattr(organ, method_name, None)
                if not callable(method):
                    resp_payload = {"error": f"Unknown method '{method_name}'"}
                    resp_env = UniversalEncodingAPI.encode_payload(resp_payload, semantic_vec, meta)
                else:
                    # Call the organ method with decoded payload
                    try:
                        result = method(payload)
                    except Exception as e:
                        result = {"error": str(e)}

                    # Re-encode response with same Ø-semantics
                    resp_env = UniversalEncodingAPI.encode_payload(result, semantic_vec, meta)

            out = json.dumps({"envelope": resp_env}) + "\n"
            conn.sendall(out.encode("utf-8"))

        except Exception:
            pass
        finally:
            conn.close()

    def start(self):
        """Start the Ø‑aware remote organ server."""
        self.running = True
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((self.host, self.port))
        s.listen(5)
        print(f"[RemoteOrganServer] Listening on {self.host}:{self.port}")

        while self.running:
            conn, addr = s.accept()
            t = threading.Thread(target=self._handle_client, args=(conn,), daemon=True)
            t.start()

    def stop(self):
        self.running = False
