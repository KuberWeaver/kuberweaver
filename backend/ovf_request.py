"""Secret-free local-exec bridge; server accepts a single request per attempt."""
import socket
import sys

if __name__ == '__main__':
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(550)
        import hashlib
        from pathlib import Path
        import json
        folder = str(Path(sys.argv[1]).parent)
        key = hashlib.sha256(json.dumps(folder, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        client.connect('\0kw-ovf-' + key)
        client.sendall(b'import-v1')
        result = client.recv(16)
        sys.exit(0 if result == b'ok' else 1)
