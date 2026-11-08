#!/usr/bin/env python3
"""
generar.py — Manda tráfico de prueba REAL (por sockets) desde el UE hacia la DN.

  python3 generar.py --destino 10.99.0.2       # por el compresor (túnel cmp0)
  python3 generar.py --destino 10.100.204.30   # directo, sin compresor

Usa las mismas cargas que trafico.py (semilla de PRUEBA), pero aquí las cabeceras IP/UDP/TCP
las pone el kernel. Los puertos coinciden con los sumideros de shim.py.
"""
from __future__ import annotations

import argparse, random, socket, struct, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trafico as T
from shim import ip_de


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--destino", required=True)
    ap.add_argument("--paquetes", type=int, default=200, help="por clase")
    ap.add_argument("--pausa-ms", type=float, default=2.0)
    a = ap.parse_args()
    rng = random.Random(2)
    n, pausa = a.paquetes, a.pausa_ms / 1000
    # Directo a la DN hay que salir con la IP de la sesión PDU: UERANSIM solo enruta por
    # uesimtun0 lo que sale con ella (ip rule from 10.60.x). Al túnel cmp0 (10.99.x) el
    # kernel ya lo manda por cmp0, y desde ahí shim.py lo saca con esa misma IP.
    origen = "" if a.destino.startswith("10.99.") else ip_de("uesimtun0", 5)
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.bind((origen, 0))

    def udp(cargas, puerto):
        for c in cargas:
            u.sendto(c, (a.destino, puerto)); time.sleep(pausa)

    def tcp(cargas, puerto):
        s = socket.create_connection((a.destino, puerto), timeout=10, source_address=(origen, 0))
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        for c in cargas:
            s.sendall(c); time.sleep(pausa)
        s.close()

    def rtp(largo, puerto, paso):
        seq, ts, ssrc = rng.randrange(1 << 16), rng.randrange(1 << 32), rng.randrange(1 << 32)
        out = []
        for i in range(n):
            out.append(T.cabecera_rtp(seq + i, ts + paso * i, ssrc, 96) + rng.randbytes(largo))
        return out

    plan = [
        ("iot_json", lambda: udp(T._cargas_iot(rng, n), 5683)),
        ("urllc_ctrl", lambda: udp(T._cargas_urllc(rng, n), 9000)),
        ("voip_rtp", lambda: udp(rtp(33, 4000, 320), 4000)),
        ("video_rtp", lambda: udp(rtp(1200, 5004, 3000), 5004)),
        ("web_tls", lambda: tcp([b"\x17\x03\x03" + struct.pack("!H", 1300) + rng.randbytes(1300)
                                 for _ in range(n)], 443)),
        ("http_texto", lambda: tcp(T._cargas_texto(rng, n, T.TEXTOS_PRUEBA), 8080)),
    ]
    for nombre, fn in plan:
        t0 = time.time(); fn()
        print(f"{nombre:11s} {n} envíos en {time.time() - t0:.1f} s", flush=True)


if __name__ == "__main__":
    main()
