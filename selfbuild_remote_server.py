def start_selfbuild_remote_server(core: HybridBrainCore, host: str = "0.0.0.0", port: int = 9000):
    server = RemoteOrganServer(host=host, port=port)
    server.register_organ("SelfBuildOrgan", core.get_organ("SelfBuildOrgan"))
    t = threading.Thread(target=server.start, daemon=True)
    t.start()
    print(f"[SelfBuildRemote] SelfBuildOrgan exposed on {host}:{port}")
    return server
