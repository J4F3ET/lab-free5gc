#!/usr/bin/env python3
"""
generar.py — Manda tráfico de prueba REAL (por sockets) desde el UE hacia la DN.

  python3 generar.py --destino 10.99.0.2       # por el compresor (túnel cmp0)
  python3 generar.py --destino 10.100.204.30   # directo, sin compresor
  python3 generar.py --destino 10.99.0.2 --paquetes 25 --aleatorio   # de 1 a 25 por clase, al azar
  python3 generar.py --destino 10.99.0.2 --paquetes 25 --aleatorio --tasa 10-30 --continuo
        # sin parar; cada --cambio-s segundos (120) elige una tasa nueva al azar entre 10 y 30 paq/s

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
    ap.add_argument("--aleatorio", action="store_true",
                    help="cada clase manda un número al azar entre 1 y --paquetes, distinto en cada corrida")
    ap.add_argument("--tasa", default="",
                    help="paquetes por segundo, «MIN-MAX» o un número; reemplaza a --pausa-ms")
    ap.add_argument("--cambio-s", type=float, default=120.0,
                    help="con --tasa MIN-MAX: cada cuántos segundos se elige una tasa nueva")
    ap.add_argument("--continuo", action="store_true", help="repetir las clases sin parar (un solo proceso)")
    a = ap.parse_args()
    rng = random.Random(2)
    azar = random.Random()   # sin semilla: solo decide CUÁNTOS; el contenido sigue siendo el de PRUEBA
    n, pausa = a.paquetes, a.pausa_ms / 1000
    tasa_min, _, tasa_max = a.tasa.partition("-")
    tasa_min = float(tasa_min) if tasa_min else 0.0
    tasa_max = float(tasa_max) if tasa_max else tasa_min
    proximo_cambio = 0.0

    def ajustar_tasa():
        """Con --tasa: la pausa entre paquetes sale de una tasa que cambia cada --cambio-s segundos.
        Ritmo constante (no ráfagas): así el panel de paquetes por segundo muestra escalones."""
        nonlocal pausa, proximo_cambio
        ahora = time.time()
        if tasa_min and ahora >= proximo_cambio:
            tasa = azar.uniform(tasa_min, tasa_max)
            pausa = 1.0 / tasa
            proximo_cambio = ahora + a.cambio_s
            print(f"tasa {tasa:.1f} paq/s durante {a.cambio_s:.0f} s", flush=True)
    # Directo a la DN hay que salir con la IP de la sesión PDU: UERANSIM solo enruta por
    # uesimtun0 lo que sale con ella (ip rule from 10.60.x). Al túnel cmp0 (10.99.x) el
    # kernel ya lo manda por cmp0, y desde ahí shim.py lo saca con esa misma IP.
    origen = "" if a.destino.startswith("10.99.") else ip_de("uesimtun0", 5)
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.bind((origen, 0))

    def udp(cargas, puerto):
        for c in cargas:
            ajustar_tasa(); u.sendto(c, (a.destino, puerto)); time.sleep(pausa)

    def tcp(cargas, puerto):
        s = socket.create_connection((a.destino, puerto), timeout=10, source_address=(origen, 0))
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        for c in cargas:
            ajustar_tasa(); s.sendall(c); time.sleep(pausa)
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
    while True:
        for nombre, fn in plan:
            if a.aleatorio:
                n = azar.randint(1, a.paquetes)   # las funciones de arriba leen este n al correr
            t0 = time.time()
            try:
                fn()
            except OSError as e:                  # en modo continuo, un corte no detiene el generador
                if not a.continuo:
                    raise
                print(f"{nombre:11s} falló: {e}; sigo", flush=True); time.sleep(1)
                continue
            if not a.continuo:
                print(f"{nombre:11s} {n} envíos en {time.time() - t0:.1f} s", flush=True)
        if not a.continuo:
            break


if __name__ == "__main__":
    main()
