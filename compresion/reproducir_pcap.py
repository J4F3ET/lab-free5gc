#!/usr/bin/env python3
"""
reproducir_pcap.py — Tráfico REAL de terceros por la red 5G: una captura .pcap / .pcapng.

Sirve cualquier captura pública (Wireshark SampleCaptures, Netresec, CIC…) o una propia. Se toma
la CARGA de cada paquete TCP/UDP (sin las cabeceras originales, que el kernel del UE vuelve a
poner) y se manda, en el mismo orden y con el mismo tamaño, como UDP por el túnel cmp0: así el
compresor y el bandido ven el contenido real (DNS, HTTP, TLS, QUIC…), paquete a paquete.

  # qué trae la captura: paquetes, servicios, bytes y entropía de la carga
  python reproducir_pcap.py resumen captura.pcap

  # mandarla por la red, desde el netns del UE (dentro de cmp-ue), por el compresor (cmp0)
  docker exec cmp-ue python3 /cmp/reproducir_pcap.py enviar /cmp/datos_externos/captura.pcap --pps 50

  # en el PC: los 9 algoritmos con esa carga, por servicio
  python reproducir_pcap.py comparar captura.pcap

Formatos: pcap clásico (µs y ns, ambos órdenes de bytes), pcapng, y los dos comprimidos con gzip.
Enlaces: Ethernet (con VLAN), IP crudo, Linux «cooked» (SLL y SLL2) y loopback. IPv4 e IPv6.
Límites declarados: los paquetes TCP sin carga (ACK puros) se omiten; las cargas de más de
--max-carga bytes se parten (el túnel tiene MTU 1400); los fragmentos IP se omiten. Solo biblioteca
estándar (corre en python:3.14-alpine). No reinicia ni toca el compresor.
"""
from __future__ import annotations

import argparse, gzip, math, socket, struct, sys, time
from collections import Counter, OrderedDict
from pathlib import Path

PUERTO_SUMIDERO = 5004      # sumidero UDP de shim.py en cmp-dn (descarta lo que llega)
MAX_CARGA = 1300            # MTU de cmp0 (1400) menos IP/UDP y margen del marco
SERVICIOS = {53: "dns", 5353: "mdns", 80: "http", 8080: "http", 443: "tls", 8443: "tls", 853: "tls",
             123: "ntp", 67: "dhcp", 68: "dhcp", 1900: "ssdp", 5683: "coap", 1883: "mqtt", 8883: "mqtt",
             22: "ssh", 25: "smtp", 587: "smtp", 110: "pop3", 143: "imap", 21: "ftp", 3478: "stun",
             5060: "sip", 161: "snmp", 137: "netbios", 138: "netbios", 139: "netbios", 445: "smb"}


# ───────────── lectura de la captura ─────────────
def _abrir(ruta):
    with open(ruta, "rb") as f:
        gz = f.read(2) == b"\x1f\x8b"
    return gzip.open(ruta, "rb") if gz else open(ruta, "rb")


def tramas(ruta):
    """(tiempo s, tipo de enlace, bytes de la trama) de cada paquete de un pcap o pcapng."""
    with _abrir(ruta) as f:
        cab = f.read(4)
        if cab == b"\x0a\x0d\x0d\x0a":
            yield from _pcapng(f, cab)
            return
        orden = {b"\xd4\xc3\xb2\xa1": ("<", 1e-6), b"\xa1\xb2\xc3\xd4": (">", 1e-6),
                 b"\x4d\x3c\xb2\xa1": ("<", 1e-9), b"\xa1\xb2\x3c\x4d": (">", 1e-9)}.get(cab)
        if orden is None:
            raise ValueError("no es un pcap ni un pcapng")
        e, escala = orden
        _, _, _, _, _, enlace = struct.unpack(e + "HHiIII", f.read(20))
        while (h := f.read(16)) and len(h) == 16:
            seg, frac, guardado, _ = struct.unpack(e + "IIII", h)
            yield seg + frac * escala, enlace & 0xFFFF, f.read(guardado)


def _pcapng(f, cab):
    e, enlaces = "<", []
    while True:
        if cab is None:
            cab = f.read(4)
        if len(cab) < 4:
            return
        largo_b = f.read(4)
        if cab == b"\x0a\x0d\x0d\x0a":            # Section Header: fija el orden de bytes
            magia = f.read(4)
            e = "<" if magia == b"\x4d\x3c\x2b\x1a" else ">"
            largo = struct.unpack(e + "I", largo_b)[0]
            f.read(largo - 12); enlaces = []
        else:
            tipo, largo = struct.unpack(e + "I", cab)[0], struct.unpack(e + "I", largo_b)[0]
            cuerpo = f.read(largo - 8)
            if tipo == 1:                          # Interface Description
                enlaces.append(struct.unpack(e + "H", cuerpo[:2])[0])
            elif tipo == 6:                        # Enhanced Packet
                iface, alto, bajo, guardado = struct.unpack(e + "IIII", cuerpo[:16])
                yield ((alto << 32) | bajo) * 1e-6, enlaces[iface] if iface < len(enlaces) else 1, cuerpo[20:20 + guardado]
            elif tipo == 3:                        # Simple Packet
                guardado = struct.unpack(e + "I", cuerpo[:4])[0]
                yield 0.0, enlaces[0] if enlaces else 1, cuerpo[4:4 + guardado]
        cab = None


def ip_de_trama(enlace: int, t: bytes) -> bytes | None:
    """La trama sin la cabecera de enlace -> el paquete IP, o None si no es IP."""
    if enlace == 1:                                # Ethernet
        tipo, pos = struct.unpack("!H", t[12:14])[0] if len(t) >= 14 else 0, 14
        while tipo in (0x8100, 0x88A8) and len(t) >= pos + 4:     # VLAN
            tipo, pos = struct.unpack("!H", t[pos + 2:pos + 4])[0], pos + 4
        return t[pos:] if tipo in (0x0800, 0x86DD) else None
    if enlace in (12, 14, 101, 228, 229):          # IP crudo
        return t
    if enlace == 113:                              # Linux cooked (SLL)
        return t[16:] if t[14:16] in (b"\x08\x00", b"\x86\xdd") else None
    if enlace == 276:                              # Linux cooked v2 (SLL2)
        return t[20:] if t[0:2] in (b"\x08\x00", b"\x86\xdd") else None
    if enlace in (0, 108):                         # loopback BSD
        return t[4:]
    return None


def transporte(ip: bytes):
    """Paquete IP -> (proto, origen, destino, puerto origen, puerto destino, carga) o None."""
    if not ip:
        return None
    v = ip[0] >> 4
    if v == 4 and len(ip) >= 20:
        ihl, total = (ip[0] & 15) * 4, struct.unpack("!H", ip[2:4])[0]
        if struct.unpack("!H", ip[6:8])[0] & 0x3FFF:        # fragmento: se omite
            return None
        proto, org, dst, resto = ip[9], ip[12:16], ip[16:20], ip[ihl:total or len(ip)]
    elif v == 6 and len(ip) >= 40:
        proto, org, dst, resto = ip[6], ip[8:24], ip[24:40], ip[40:40 + struct.unpack("!H", ip[4:6])[0]]
        while proto in (0, 43, 60) and len(resto) >= 8:     # cabeceras de extensión simples
            proto, resto = resto[0], resto[(resto[1] + 1) * 8:]
    else:
        return None
    if proto == 17 and len(resto) >= 8:
        sp, dp = struct.unpack("!HH", resto[:4])
        return 17, org, dst, sp, dp, resto[8:]
    if proto == 6 and len(resto) >= 20:
        sp, dp = struct.unpack("!HH", resto[:4])
        return 6, org, dst, sp, dp, resto[(resto[12] >> 4) * 4:]
    return None


def servicio(proto: int, sp: int, dp: int, carga: bytes) -> str:
    nombre = SERVICIOS.get(min(sp, dp)) or SERVICIOS.get(dp) or SERVICIOS.get(sp)
    if proto == 17 and 443 in (sp, dp):
        return "quic"
    if nombre:
        return nombre
    if carga[:4] in (b"GET ", b"POST", b"HTTP", b"HEAD", b"PUT "):
        return "http"
    if carga[:1] in (b"\x16", b"\x17") and carga[1:3] in (b"\x03\x01", b"\x03\x03"):
        return "tls"
    return "otro_tcp" if proto == 6 else "otro_udp"


def cargas(ruta, max_carga: int = MAX_CARGA, maximo: int = 0):
    """Lista de (servicio, flujo, carga) en el orden de la captura; las cargas largas, partidas."""
    flujos: dict[tuple, int] = {}
    salida, omitidos = [], Counter()
    for _, enlace, t in tramas(ruta):
        ip = ip_de_trama(enlace, t)
        tr = transporte(ip) if ip is not None else None
        if tr is None:
            omitidos["no es TCP/UDP sobre IP (o fragmento)"] += 1
            continue
        proto, org, dst, sp, dp, c = tr
        if not c:
            omitidos["sin carga (ACK, SYN…)"] += 1
            continue
        fid = flujos.setdefault((proto, org, dst, sp, dp), len(flujos))
        s = servicio(proto, sp, dp, c)
        for i in range(0, len(c), max_carga):
            salida.append((s, fid, c[i:i + max_carga]))
        if maximo and len(salida) >= maximo:
            break
    return salida, omitidos


def entropia(b: bytes) -> float:
    n = len(b)
    return -sum(k / n * math.log2(k / n) for k in Counter(b).values()) if n else 0.0


# ───────────── subcomandos ─────────────
def resumen(a) -> None:
    sal, omit = cargas(a.archivo, a.max_carga, a.max)
    por = OrderedDict()
    for s, _, c in sal:
        por.setdefault(s, []).append(c)
    total = sum(len(c) for _, _, c in sal)
    print(f"{len(sal):,} paquetes con carga, {total:,} bytes, {len({f for _, f, _ in sal})} flujos; omitidos: {dict(omit)}")
    print(f"{'servicio':10} {'paquetes':>9} {'bytes':>11} {'medio':>6}  H (bits/byte)")
    for s, cs in sorted(por.items(), key=lambda kv: -sum(map(len, kv[1]))):
        b = b"".join(cs)
        print(f"{s:10} {len(cs):>9,} {len(b):>11,} {len(b) // len(cs):>6}  {entropia(b):.3f}")


def enviar(a) -> None:
    sal, omit = cargas(a.archivo, a.max_carga, a.max)
    print(f"{len(sal):,} paquetes con carga; omitidos: {dict(omit)}", flush=True)
    origen = ""
    if not a.destino.startswith("10.99."):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from shim import ip_de
        origen = ip_de("uesimtun0", 5)
    socks: dict[int, socket.socket] = {}       # un socket (puerto de origen) por flujo, hasta --max-flujos
    pausa, n, byts, t0 = 1 / a.pps, 0, 0, time.time()
    for _, fid, c in sal:
        k = fid % a.max_flujos
        s = socks.get(k)
        if s is None:
            s = socks[k] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind((origen, 0))
        s.sendto(c, (a.destino, a.puerto))
        n += 1; byts += len(c)
        if n % 500 == 0:
            print(f"  {n}/{len(sal)} paquetes, {byts:,} bytes de carga, {time.time() - t0:.0f} s", flush=True)
        time.sleep(pausa)
    print(f"listo: {n} paquetes, {byts:,} bytes de carga en {time.time() - t0:.0f} s → {a.destino}:{a.puerto}")


def comparar(a) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import compresores as C, informacion as I, trafico as T
    from evaluar import MODELO, entrenar
    import json
    sal, omit = cargas(a.archivo, a.max_carga, a.max)
    print(f"{len(sal):,} paquetes con carga; omitidos: {dict(omit)}", flush=True)
    mezcla = [(s, fid, T.paquete_udp(c, 20000 + fid % 40000, PUERTO_SUMIDERO, i & 0xFFFF)) for i, (s, fid, c) in enumerate(sal)]
    m = C.Modelo.cargar(MODELO) if MODELO.exists() else entrenar()
    res = {}
    for nombre, fab in I.politicas(m):
        alg = I.NOMBRES.get(nombre, nombre.replace("ROHC + ", ""))
        print(f"  {alg}...", flush=True)
        res[alg] = {canal: I.medir(m, mezcla, fab, canal) for canal in I.CANALES}
    r = {"captura": Path(a.archivo).name, "paquetes": len(mezcla), "p_perdida": I.P_PERDIDA, "algoritmos": res}
    md = I.markdown(r).replace(
        "# Original, comprimido y recuperado por algoritmo (generado por `evaluar.py informacion`)",
        f"# Captura real `{Path(a.archivo).name}`: los 9 algoritmos (generado por `reproducir_pcap.py comparar`)"
    ).replace("del flujo mezclado de PRUEBA", "con carga de la captura (la carga TCP/UDP de cada paquete, en orden)"
              ).replace("por clase", "por servicio")
    salida = Path(a.salida)
    salida.with_suffix(".md").write_text(md, encoding="utf-8")
    salida.with_suffix(".json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(md)
    print(f"→ {salida.with_suffix('.md')} y .json")


def main() -> None:
    sys.stdout.reconfigure(errors="replace")   # consola de Windows (cp1252) sin «→»
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def comunes(p, maximo):
        p.add_argument("archivo", help="captura .pcap / .pcapng (también .gz)")
        p.add_argument("--max", type=int, default=maximo, help="máximo de paquetes (0 = todos)")
        p.add_argument("--max-carga", type=int, default=MAX_CARGA, help="las cargas más largas se parten")

    p = sub.add_parser("resumen", help="qué trae la captura"); comunes(p, 0); p.set_defaults(f=resumen)
    p = sub.add_parser("enviar", help="mandarla por la red (correr en el netns del UE)"); comunes(p, 0)
    p.add_argument("--destino", default="10.99.0.2", help="10.99.0.2 = por el compresor; 10.100.204.30 = directo")
    p.add_argument("--puerto", type=int, default=PUERTO_SUMIDERO)
    p.add_argument("--pps", type=float, default=50)
    p.add_argument("--max-flujos", type=int, default=64, help="sockets (puertos de origen) como máximo")
    p.set_defaults(f=enviar)
    p = sub.add_parser("comparar", help="los 9 algoritmos con esta captura, en este PC"); comunes(p, 5000)
    p.add_argument("--salida", default=str(Path(__file__).resolve().parent / "resultados" / "captura"))
    p.set_defaults(f=comparar)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
