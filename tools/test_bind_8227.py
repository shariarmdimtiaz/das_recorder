from __future__ import annotations

import socket
import time

LOCAL_IP = "0.0.0.0"
PORT = 8227

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind((LOCAL_IP, PORT))

print(f"Listening on UDP {LOCAL_IP}:{PORT}")
print("Open another CMD and run: netstat -ano -p UDP | findstr :8227")
print("Press Ctrl+C to stop.")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Stopped.")
finally:
    sock.close()
