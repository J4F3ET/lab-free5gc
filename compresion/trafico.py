#!/usr/bin/env python3
"""
trafico.py — Tráfico de prueba con forma de paquetes 5G reales (solo biblioteca estándar).

Cada flujo es una lista de paquetes IPv4 completos (cabecera IP + UDP/TCP [+ RTP] + carga),
con checksums válidos, tal como los vería pasar el UE por uesimtun0. Las clases imitan lo
que lleva cada slice:

  iot_json     mMTC   telemetría JSON de un sensor, ~110 B, un paquete por segundo
  urllc_ctrl   URLLC  mensaje binario de control (posición, estado), ~24 B de carga
  voip_rtp     eMBB   voz: RTP + 33 B de AMR-WB (ya comprimido por el códec de voz)
  video_rtp    eMBB   video: RTP + 1200 B de H.264 (ya comprimido)
  web_tls      eMBB   HTTPS: TCP + registros TLS (cifrado = bytes aleatorios)
  http_texto   eMBB   HTTP/API sin cifrar: TCP + texto real (la documentación del repo)

La carga "comprimida" o "cifrada" se modela con bytes aleatorios porque estadísticamente
eso es lo que es: un códec de voz/video o un cifrado dejan la entropía en ~8 bits/byte.

Entrenamiento y prueba usan semillas distintas y, para el texto, archivos distintos:
así lo que se entrena (tabla de Huffman, diccionario) nunca vio los paquetes de prueba.
"""
from __future__ import annotations

import json, random, struct
from pathlib import Path

TEXTOS = Path(__file__).resolve().parent / "textos"
UE_IP = bytes([10, 60, 0, 1])
DN_IP = bytes([10, 100, 204, 10])

CLASES = ["iot_json", "urllc_ctrl", "voip_rtp", "video_rtp", "web_tls", "http_texto"]
SLICE = {"iot_json": "mMTC", "urllc_ctrl": "URLLC", "voip_rtp": "eMBB", "video_rtp": "eMBB",
         "web_tls": "eMBB", "http_texto": "eMBB"}

# Textos reales para http_texto. Se separan por archivo: unos para entrenar, otros para probar.
# Son copias CONGELADAS de docs/01-07 (versión del commit de86f04, con CRLF), no los docs vivos:
# así editar la documentación no cambia el tráfico de prueba ni los resultados, y los textos
# viajan con compresion/ dentro de los contenedores (que no montan docs/).
TEXTOS_ENTRENO = ["01-conceptos-5g.md", "03-servicios.md", "05-diagnostico.md", "07-cicd.md"]
TEXTOS_PRUEBA = ["02-instalacion.md", "04-operacion.md", "06-plmn-e-identidades.md"]


# ═══════════════════════════ cabeceras ═══════════════════════════
def checksum16(datos: bytes) -> int:
    if len(datos) % 2:
        datos += b"\0"
    s = sum(struct.unpack(f"!{len(datos) // 2}H", datos))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def cabecera_ip(largo_total: int, ident: int, proto: int, src=UE_IP, dst=DN_IP, ttl=64, tos=0) -> bytes:
    h = struct.pack("!BBHHHBBH4s4s", 0x45, tos, largo_total, ident & 0xFFFF, 0x4000, ttl, proto, 0, src, dst)
    return h[:10] + struct.pack("!H", checksum16(h)) + h[12:]


def paquete_udp(carga: bytes, sport: int, dport: int, ident: int, src=UE_IP, dst=DN_IP) -> bytes:
    largo_udp = 8 + len(carga)
    pseudo = src + dst + struct.pack("!BBH", 0, 17, largo_udp)
    u = struct.pack("!HHHH", sport, dport, largo_udp, 0) + carga
    ck = checksum16(pseudo + u) or 0xFFFF
    u = u[:6] + struct.pack("!H", ck) + u[8:]
    return cabecera_ip(20 + largo_udp, ident, 17, src, dst) + u


def paquete_tcp(carga: bytes, sport: int, dport: int, seq: int, ack: int, ident: int,
                src=UE_IP, dst=DN_IP) -> bytes:
    t = struct.pack("!HHIIBBHHH", sport, dport, seq & 0xFFFFFFFF, ack & 0xFFFFFFFF, 0x50, 0x18,
                    502, 0, 0) + carga
    pseudo = src + dst + struct.pack("!BBH", 0, 6, len(t))
    t = t[:16] + struct.pack("!H", checksum16(pseudo + t)) + t[18:]
    return cabecera_ip(20 + len(t), ident, 6, src, dst) + t


def cabecera_rtp(seq: int, ts: int, ssrc: int, pt: int, marca: bool = False) -> bytes:
    return struct.pack("!BBHII", 0x80, (0x80 if marca else 0) | pt, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc)


# ═══════════════════════════ cargas por clase ═══════════════════════════
def _cargas_iot(rng: random.Random, n: int) -> list[bytes]:
    dev = f"sensor-{rng.randrange(10000):04d}"
    ts, temp, hum, bat = 1_730_000_000 + rng.randrange(10**6), rng.uniform(15, 30), rng.uniform(30, 80), 3.9
    out = []
    for _ in range(n):
        ts += 1
        temp += rng.gauss(0, 0.05); hum += rng.gauss(0, 0.2); bat -= rng.random() * 1e-4
        msg = {"dev": dev, "ts": ts, "temp": round(temp, 2), "hum": round(hum, 1),
               "bat": round(bat, 3), "rssi": -rng.randrange(60, 95),
               "st": "ok" if rng.random() > 0.02 else "alarma"}
        out.append(json.dumps(msg, separators=(",", ":")).encode())
    return out


def _cargas_urllc(rng: random.Random, n: int) -> list[bytes]:
    ident, x, y, z = rng.randrange(1 << 16), rng.randrange(-3000, 3000), rng.randrange(-3000, 3000), 120
    out = []
    for seq in range(n):
        x += rng.randrange(-4, 5); y += rng.randrange(-4, 5)
        estado = 1 if rng.random() > 0.01 else 3
        cuerpo = struct.pack("!HIBhhhHH", ident, seq, estado, x, y, z, rng.randrange(0, 50), 0)
        out.append(cuerpo + struct.pack("!H", checksum16(cuerpo)) + b"\0\0\0")
    return out


def _cargas_texto(rng: random.Random, n: int, archivos: list[str]) -> list[bytes]:
    texto = b"".join((TEXTOS / a).read_bytes() for a in archivos)
    cab = (b"HTTP/1.1 200 OK\r\nContent-Type: text/markdown; charset=utf-8\r\n"
           b"Content-Length: %d\r\nConnection: keep-alive\r\n\r\n" % len(texto))
    flujo = cab + texto
    ini = rng.randrange(0, max(1, len(flujo) - 1400 * n)) if len(flujo) > 1400 * n else 0
    trozos = [flujo[i:i + 1400] for i in range(ini, len(flujo), 1400)]
    return trozos[:n] if len(trozos) >= n else (trozos * (n // len(trozos) + 1))[:n]


# ═══════════════════════════ flujos ═══════════════════════════
def flujo(clase: str, rng: random.Random, n: int, textos: list[str]) -> list[bytes]:
    """n paquetes IPv4 de un flujo de la clase dada."""
    ident = rng.randrange(1 << 16)
    sport = rng.randrange(32768, 61000)
    paquetes = []
    if clase == "iot_json":
        for c in _cargas_iot(rng, n):
            paquetes.append(paquete_udp(c, sport, 5683, ident)); ident += 1
    elif clase == "urllc_ctrl":
        for c in _cargas_urllc(rng, n):
            paquetes.append(paquete_udp(c, sport, 9000, ident)); ident += 1
    elif clase == "voip_rtp":
        seq, ts, ssrc = rng.randrange(1 << 16), rng.randrange(1 << 32), rng.randrange(1 << 32)
        for _ in range(n):
            carga = cabecera_rtp(seq, ts, ssrc, 96) + rng.randbytes(33)
            paquetes.append(paquete_udp(carga, sport, 4000, ident))
            ident += 1; seq += 1; ts += 320
    elif clase == "video_rtp":
        seq, ts, ssrc = rng.randrange(1 << 16), rng.randrange(1 << 32), rng.randrange(1 << 32)
        i = 0
        while len(paquetes) < n:
            por_cuadro = rng.randrange(2, 7)
            for j in range(por_cuadro):
                carga = cabecera_rtp(seq, ts, ssrc, 97, marca=(j == por_cuadro - 1)) + rng.randbytes(1200)
                paquetes.append(paquete_udp(carga, sport, 5004, ident))
                ident += 1; seq += 1
            ts += 3000; i += 1
        paquetes = paquetes[:n]
    elif clase == "web_tls":
        seq, ack = rng.randrange(1 << 32), rng.randrange(1 << 32)
        for _ in range(n):
            cuerpo = rng.randbytes(1395)
            carga = b"\x17\x03\x03" + struct.pack("!H", len(cuerpo)) + cuerpo
            paquetes.append(paquete_tcp(carga, sport, 443, seq, ack, ident))
            ident += 1; seq += len(carga)
    elif clase == "http_texto":
        seq, ack = rng.randrange(1 << 32), rng.randrange(1 << 32)
        for c in _cargas_texto(rng, n, textos):
            paquetes.append(paquete_tcp(c, sport, 8080, seq, ack, ident))
            ident += 1; seq += len(c)
    else:
        raise ValueError(clase)
    return paquetes


def conjunto(semilla: int, paquetes_por_flujo: int = 200, flujos_por_clase: int = 3,
             prueba: bool = False) -> dict[str, list[list[bytes]]]:
    """{clase: [flujo, flujo, ...]} — cada flujo es una lista de paquetes."""
    rng = random.Random(semilla)
    textos = TEXTOS_PRUEBA if prueba else TEXTOS_ENTRENO
    return {c: [flujo(c, rng, paquetes_por_flujo, textos) for _ in range(flujos_por_clase)] for c in CLASES}


def intercalar(conj: dict[str, list[list[bytes]]], semilla: int) -> list[tuple[str, int, bytes]]:
    """Mezcla todos los flujos en un solo orden de llegada (como una celda con varios UE),
    manteniendo el orden interno de cada flujo. Devuelve (clase, id_flujo, paquete)."""
    rng = random.Random(semilla)
    colas = [(c, i, list(f)) for c, fs in conj.items() for i, f in enumerate(fs)]
    salida, pos = [], [0] * len(colas)
    vivos = list(range(len(colas)))
    while vivos:
        j = rng.choice(vivos)
        c, i, f = colas[j]
        salida.append((c, j, f[pos[j]]))
        pos[j] += 1
        if pos[j] == len(f):
            vivos.remove(j)
    return salida
