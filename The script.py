#!/usr/bin/env python3
import os
import subprocess
import datetime
import ssl
import threading
import socket
import ssl
import sys
import time

###############################################
#  LPU AUTO‑CERT ENGINE (FULLY MERGED)
###############################################

CA_KEY = "ca.key"
CA_CRT = "ca.crt"
SERVER_KEY = "server.key"
SERVER_CSR = "server.csr"
SERVER_CRT = "server.crt"
CLIENT_KEY = "client.key"
CLIENT_CSR = "client.csr"
CLIENT_CRT = "client.crt"
SAN_CONFIG = "san.cnf"

def run_silent(cmd):
    return subprocess.run(cmd, shell=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)

def openssl_ok():
    try:
        subprocess.run("openssl version", shell=True, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except:
        return False

def write_san_config():
    cfg = """
[ req ]
default_bits       = 4096
distinguished_name = req_distinguished_name
req_extensions     = req_ext
prompt             = no

[ req_distinguished_name ]
CN = localhost

[ req_ext ]
subjectAltName = @alt_names

[ alt_names ]
DNS.1 = localhost
IP.1  = 127.0.0.1
"""
    with open(SAN_CONFIG, "w") as f:
        f.write(cfg)

def cert_expired(path):
    try:
        cert = ssl._ssl._test_decode_cert(path)
        exp = datetime.datetime.strptime(cert['notAfter'], "%b %d %H:%M:%S %Y %Z")
        return exp < datetime.datetime.utcnow()
    except:
        return True

def cert_mismatch():
    if not os.path.exists(SERVER_CRT) or not os.path.exists(CA_CRT):
        return True
    try:
        cmd = f"openssl verify -CAfile {CA_CRT} {SERVER_CRT}"
        r = run_silent(cmd)
        return b"OK" not in r.stdout
    except:
        return True

def generate_all():
    write_san_config()

    run_silent(f"openssl genrsa -out {CA_KEY} 4096")
    run_silent(f"openssl req -x509 -new -nodes -key {CA_KEY} -sha256 -days 3650 "
               f"-subj \"/CN=LPU-CA\" -out {CA_CRT}")

    run_silent(f"openssl genrsa -out {SERVER_KEY} 4096")
    run_silent(f"openssl req -new -key {SERVER_KEY} -out {SERVER_CSR} -config {SAN_CONFIG}")
    run_silent(f"openssl x509 -req -in {SERVER_CSR} -CA {CA_CRT} -CAkey {CA_KEY} "
               f"-out {SERVER_CRT} -days 3650 -sha256 -extfile {SAN_CONFIG} -extensions req_ext")

    run_silent(f"openssl genrsa -out {CLIENT_KEY} 4096")
    run_silent(f"openssl req -new -key {CLIENT_KEY} -subj \"/CN=LPU-Client\" -out {CLIENT_CSR}")
    run_silent(f"openssl x509 -req -in {CLIENT_CSR} -CA {CA_CRT} -CAkey {CA_KEY} "
               f"-out {CLIENT_CRT} -days 3650 -sha256")

def ensure_certs():
    print("[LPU] Checking TLS certificate state...")

    if not openssl_ok():
        print("[LPU] OpenSSL missing → TLS disabled (fallback mode).")
        return False

    missing = any(not os.path.exists(f) for f in
                  [CA_KEY, CA_CRT, SERVER_KEY, SERVER_CRT, CLIENT_KEY, CLIENT_CRT])

    if missing:
        print("[LPU] Missing certs → generating fresh chain.")
        generate_all()
        return True

    if cert_expired(SERVER_CRT) or cert_expired(CLIENT_CRT) or cert_expired(CA_CRT):
        print("[LPU] Expired certs → regenerating full chain.")
        generate_all()
        return True

    if cert_mismatch():
        print("[LPU] Cert chain mismatch → repairing.")
        generate_all()
        return True

    print("[LPU] TLS certs valid.")
    return True


###############################################
#  LPU TLS SERVER (AUTO‑TLS / FALLBACK)
###############################################

def start_server_tls():
    print("[LPU] Starting TLS server...")

    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(certfile=SERVER_CRT, keyfile=SERVER_KEY)
    context.load_verify_locations(CA_CRT)
    context.verify_mode = ssl.CERT_REQUIRED

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 9443))
    sock.listen(5)

    print("[LPU] TLS server running on port 9443")

    while True:
        conn, addr = sock.accept()
        try:
            tls = context.wrap_socket(conn, server_side=True)
            data = tls.recv(4096).decode()
            tls.send(b"OK")
            tls.close()
        except:
            pass

def start_server_no_tls():
    print("[LPU] Starting NON‑TLS fallback server...")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 9080))
    sock.listen(5)

    print("[LPU] Fallback server running on port 9080")

    while True:
        conn, addr = sock.accept()
        data = conn.recv(4096).decode()
        conn.send(b"OK")
        conn.close()


###############################################
#  LPU GUI LAUNCHER (AUTO‑CONNECT)
###############################################

def start_gui(tls_enabled):
    print("[LPU] Launching GUI...")

    if tls_enabled:
        print("[LPU] GUI connecting via TLS → 127.0.0.1:9443")
    else:
        print("[LPU] GUI connecting via fallback → 127.0.0.1:9080")

    # Placeholder for your actual GUI code
    time.sleep(1)
    print("[LPU] GUI online.")


###############################################
#  MAIN LAUNCH SEQUENCE
###############################################

def main():
    print("=== LPU UNIFIED LAUNCHER ===")

    tls_enabled = ensure_certs()

    if tls_enabled:
        threading.Thread(target=start_server_tls, daemon=True).start()
    else:
        threading.Thread(target=start_server_no_tls, daemon=True).start()

    start_gui(tls_enabled)

    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
