#!/usr/bin/env python3
"""
reproducir_iot.py — Telemetría IoT REAL por la red 5G: los sensores del Intel Berkeley Research Lab.

Fuente: https://db.csail.mit.edu/labdata/labdata.html (data.txt.gz, 54 sensores Mica2Dot, una
lectura cada 31 s, 28 feb – 5 abr 2004). Cada línea:  fecha hora epoch sensor temp hum luz voltaje
(°C, % de humedad relativa, lux, voltios). El .gz se lee tal cual, sin descomprimir a disco.

Cada lectura se vuelve un paquete JSON como el de la clase iot_json del lab:
    {"dev":"intel-31","ts":1078012345,"seq":17,"temp":19.1652,"hum":38.8039,"luz":45.08,"bat":2.68742}
y cada sensor usa su propio puerto de origen, como haría un sensor real (el bandido y los
compresores por flujo lo ven como un flujo aparte).

  # recorte limpio para copiar al servidor (pocos MB en vez de 34)
  python reproducir_iot.py recortar data.txt.gz --sensores 3 --lecturas 2000 --salida intel.txt.gz

  # mandarlo por la red, desde el netns del UE (dentro de cmp-ue), por el compresor (cmp0)
  docker exec cmp-ue python3 /cmp/reproducir_iot.py enviar /cmp/datos_externos/intel.txt.gz --pps 20

  # en el PC: los 9 algoritmos y la tasa-distorsión de predictivo_iot con esas lecturas reales
  python reproducir_iot.py comparar data.txt.gz --sensores 3 --lecturas 2000

Solo biblioteca estándar (corre en python:3.14-alpine). No reinicia ni toca el compresor: los
paquetes entran a cmp0 como cualquier otra aplicación y van al sumidero UDP 5683 de cmp-dn.
"""
from __future__ import annotations

import argparse, calendar, gzip, json, socket, sys, time
from collections import Counter
from pathlib import Path

PUERTO_IOT = 5683        # sumidero de shim.py en cmp-dn (el de la clase iot_json)
PUERTO_BASE = 41000      # puerto de origen = PUERTO_BASE + id del sensor
# Rangos físicos del sensor (hoja de datos de la weatherboard y la página del dataset). Fuera de
# ellos la lectura es basura: pasa sobre todo cuando la batería se agota (temperaturas de 100+ °C).
RANGOS = {"temp": (-10.0, 60.0), "hum": (0.0, 100.0), "luz": (0.0, 100_000.0), "bat": (1.8, 3.4)}


# ───────────── lectura y limpieza ─────────────
def leer(ruta: str | Path, sensores: set[int] | None = None):
    """Lecturas válidas como dicts, en el orden del archivo. -> (lecturas, conteo de descartes)."""
    abrir = gzip.open if str(ruta).endswith(".gz") else open
    malas = Counter()
    lecturas = []
    with abrir(ruta, "rt", encoding="ascii", errors="replace") as f:
        for linea in f:
            r = parsear(linea)
            if isinstance(r, str):
                malas[r] += 1
            elif sensores is None or r["sensor"] in sensores:
                lecturas.append(r)
    return lecturas, malas


def parsear(linea: str):
    """dict con la lectura, o un texto con el motivo si se descarta."""
    p = linea.split()
    if len(p) != 8:
        return "incompleta"
    try:
        fecha, hora = p[0], p[1]
        ts = calendar.timegm(time.strptime(f"{fecha} {hora[:8]}", "%Y-%m-%d %H:%M:%S"))
        epoch, sensor = int(p[2]), int(p[3])
        # Se guardan los textos originales: el JSON lleva el valor tal como lo reportó el sensor.
        v = {"temp": p[4], "hum": p[5], "luz": p[6], "bat": p[7]}
        for k, (lo, hi) in RANGOS.items():
            if not lo <= float(v[k]) <= hi:
                return f"fuera de rango ({k})"
    except ValueError:
        return "ilegible"
    return {"sensor": sensor, "ts": ts, "seq": epoch, **v}


def elegir(lecturas: list[dict], sensores: int | str, lecturas_por_sensor: int) -> list[dict]:
    """Los sensores pedidos («3» = los 3 con más lecturas válidas, o «1,7,31»), cada uno en orden
    de tiempo y recortado a sus primeras N lecturas. Resultado intercalado por tiempo, como
    llegarían a la red varios sensores a la vez."""
    por = {}
    for r in lecturas:
        por.setdefault(r["sensor"], []).append(r)
    if isinstance(sensores, str) and "," in sensores:
        ids = [int(x) for x in sensores.split(",")]
    else:
        ids = [s for s, _ in sorted(por.items(), key=lambda kv: -len(kv[1]))[:int(sensores)]]
    salida = []
    for s in ids:
        serie = sorted(por.get(s, []), key=lambda r: (r["ts"], r["seq"]))
        vistos, limpia = set(), []
        for r in serie:                       # el log repite algunas lecturas (mismo epoch)
            if r["seq"] not in vistos:
                vistos.add(r["seq"]); limpia.append(r)
        salida += limpia[:lecturas_por_sensor] if lecturas_por_sensor else limpia
    return sorted(salida, key=lambda r: (r["ts"], r["sensor"]))


def carga(r: dict) -> bytes:
    """La lectura como paquete JSON compacto, con los números tal cual vienen en el archivo."""
    texto = (f'{{"dev":"intel-{r["sensor"]:02d}","ts":{r["ts"]},"seq":{r["seq"]},'
             f'"temp":{float(r["temp"])!r},"hum":{float(r["hum"])!r},"luz":{float(r["luz"])!r},'
             f'"bat":{float(r["bat"])!r}}}')
    return texto.encode()


def a_linea(r: dict) -> str:
    t = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(r["ts"]))
    return f"{t} {r['seq']} {r['sensor']} {r['temp']} {r['hum']} {r['luz']} {r['bat']}\n"


# ───────────── subcomandos ─────────────
def recortar(a) -> None:
    lecturas, malas = leer(a.archivo)
    sel = elegir(lecturas, a.sensores, a.lecturas)
    with gzip.open(a.salida, "wt", encoding="ascii") as f:
        f.writelines(a_linea(r) for r in sel)
    resumen(lecturas, malas, sel)
    print(f"→ {a.salida}: {len(sel)} lecturas, {Path(a.salida).stat().st_size:,} bytes")


def resumen(lecturas, malas, sel) -> None:
    print(f"válidas: {len(lecturas):,}   descartadas: {sum(malas.values()):,} {dict(malas)}")
    cuenta = Counter(r["sensor"] for r in sel)
    print("elegidas:", ", ".join(f"sensor {s}: {n}" for s, n in sorted(cuenta.items())))


def enviar(a) -> None:
    lecturas, malas = leer(a.archivo)
    sel = elegir(lecturas, a.sensores, a.lecturas)
    resumen(lecturas, malas, sel)
    # Al túnel cmp0 (10.99.x) el kernel ya lo manda por cmp0. Directo a la DN hay que salir con la
    # IP de la sesión PDU (UERANSIM solo enruta por uesimtun0 lo que sale con ella), como generar.py.
    origen = ""
    if not a.destino.startswith("10.99."):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from shim import ip_de
        origen = ip_de("uesimtun0", 5)
    socks: dict[int, socket.socket] = {}
    pausa, n, byts, t0 = 1 / a.pps, 0, 0, time.time()
    for r in sel:
        s = socks.get(r["sensor"])
        if s is None:
            s = socks[r["sensor"]] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.bind((origen, PUERTO_BASE + r["sensor"]))
            except OSError:
                s.bind((origen, 0))
        c = carga(r)
        s.sendto(c, (a.destino, a.puerto))
        n += 1; byts += len(c)
        if n % 500 == 0:
            print(f"  {n}/{len(sel)} lecturas, {byts:,} bytes de carga, {time.time() - t0:.0f} s", flush=True)
        time.sleep(pausa)
    print(f"listo: {n} paquetes de {len(socks)} sensores, {byts:,} bytes de carga en {time.time() - t0:.0f} s "
          f"→ {a.destino}:{a.puerto}")


def comparar(a) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import compresores as C, informacion as I, trafico as T
    from evaluar import MODELO, entrenar
    lecturas, malas = leer(a.archivo)
    sel = elegir(lecturas, a.sensores, a.lecturas)
    resumen(lecturas, malas, sel)
    mezcla = [("iot_json", r["sensor"], T.paquete_udp(carga(r), PUERTO_BASE + r["sensor"], PUERTO_IOT, i & 0xFFFF))
              for i, r in enumerate(sel)]
    m = C.Modelo.cargar(MODELO) if MODELO.exists() else entrenar()
    r = I.evaluar(m, mezcla)
    md = I.markdown(r).replace(
        "# Original, comprimido y recuperado por algoritmo (generado por `evaluar.py informacion`)",
        "# Telemetría IoT REAL (Intel Berkeley Research Lab): los 9 algoritmos (generado por `reproducir_iot.py comparar`)"
    ).replace("del flujo mezclado de PRUEBA", f"reales de {len(set(x['sensor'] for x in sel))} sensores (una lectura = un paquete)")
    salida = Path(a.salida)
    salida.with_suffix(".md").write_text(md, encoding="utf-8")
    salida.with_suffix(".json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(md)
    print(f"→ {salida.with_suffix('.md')} y .json")


def main() -> None:
    sys.stdout.reconfigure(errors="replace")   # consola de Windows (cp1252) sin «→»
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def comunes(p, lecturas=2000):
        p.add_argument("archivo", help="data.txt.gz (o un recorte, .gz o texto)")
        p.add_argument("--sensores", default="3", help="cuántos (los que tienen más lecturas) o una lista: 1,7,31")
        p.add_argument("--lecturas", type=int, default=lecturas, help="por sensor; 0 = todas")

    p = sub.add_parser("recortar", help="escribe un recorte limpio (.gz) para copiar al servidor")
    comunes(p); p.add_argument("--salida", default="intel.txt.gz"); p.set_defaults(f=recortar)
    p = sub.add_parser("enviar", help="manda las lecturas por la red (correr en el netns del UE)")
    comunes(p, 0)
    p.add_argument("--destino", default="10.99.0.2", help="10.99.0.2 = por el compresor; 10.100.204.30 = directo")
    p.add_argument("--puerto", type=int, default=PUERTO_IOT)
    p.add_argument("--pps", type=float, default=20, help="paquetes por segundo (el sensor real manda 1 cada 31 s)")
    p.set_defaults(f=enviar)
    p = sub.add_parser("comparar", help="los 9 algoritmos + tasa-distorsión con estas lecturas, en este PC")
    comunes(p); p.add_argument("--salida", default=str(Path(__file__).resolve().parent / "resultados" / "intel_iot"))
    p.set_defaults(f=comparar)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
