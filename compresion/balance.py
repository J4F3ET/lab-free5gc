#!/usr/bin/env python3
"""
balance.py — ¿Qué algoritmo de compresión deja la red con MENOS latencia, según el enlace?

Comprimir cuesta CPU (tiempo de procesamiento) y ahorra bytes (tiempo en el enlace y en la
cola). Cuál gana depende de qué tan lento y qué tan cargado esté el enlace. Este módulo mide
cada política sobre tráfico de prueba real y calcula la latencia media que tendría en cada
escenario (velocidad del enlace × carga), para decir cuál conviene en cada caso.

    python evaluar.py balance            # -> resultados/balance.md y balance.json

Políticas evaluadas (todas automáticas: un códec nuevo registrado con @registrar entra solo):
  · sin compresión (crudo) — la referencia: 0 CPU, todos los bytes
  · ROHC + cada códec de compresores.CODECS, fijo
  · ROHC + bandido UCB (configuración de red), que elige el códec por ahorro de bytes
  · ROHC + bandido UCB por latencia: el mismo bandido, pero premiado con el tiempo ganado
    (bandido.py, velocidad_bps). Como lo que aprende depende del enlace, se corre una vez POR
    VELOCIDAD, sabiendo esa velocidad, y en cada escenario se usa la corrida de su velocidad.

Modelo de latencia (por paquete, en promedio):

  compresor ──► enlace ──► descompresor        tres colas M/G/1 en serie (independencia de
                                               Kleinrock: cada etapa se trata por separado)

  En cada etapa:  T = E[S] + λ·E[S²] / (2·(1 − ρ)),   ρ = λ·E[S]   (Pollaczek–Khinchine)
    · compresor / descompresor: S = tiempo MEDIDO de comprimir / descomprimir ese paquete
    · enlace:                   S = 8 · bytes del marco / velocidad del enlace
    · λ (paquetes/s) sale de la carga: "carga 80 %" = el tráfico SIN comprimir ocuparía el 80 %
      del enlace. Es la misma tasa de paquetes para todas las políticas: comprimir no cambia
      cuántos paquetes manda la aplicación, solo cuántos bytes ocupa cada uno.
    · ρ ≥ 1 en cualquier etapa = esa etapa no da abasto: la latencia crece sin límite (∞).

  Lo que el modelo NO incluye (y por qué):
    · propagación: igual para todas las políticas, no cambia quién gana.
    · el planificador de radio de 5G (slots, PRB, HARQ): favorece aún más a los paquetes chicos
      (un paquete que cabe en un slot no espera al siguiente), así que el modelo es conservador
      a favor de "no comprimir".
    · pérdidas: se reportan aparte (% de paquetes mal reconstruidos con 1 % de pérdida), porque
      cambian la CALIDAD, no la latencia media.

  CPU: los tiempos son de Python en esta máquina. --factor-cpu 0.1 modela una implementación
  10× más rápida (C, o el hardware de la estación base, que es donde va ROHC en 5G). El reporte
  trae las dos vistas.
"""
from __future__ import annotations

import gc, math, random, time
from typing import Callable

import compresores as C
from bandido import EmisorAdaptativo

VELOCIDADES = [1e5, 1e6, 1e7, 1e8, 1e9]   # bps: NB-IoT ~ 100 kbps … fibra 1 Gbps
CARGAS = [0.5, 0.8, 0.95]                 # ocupación del enlace con el tráfico SIN comprimir
CARGA_EQUILIBRIO = 0.5                    # carga para la velocidad de equilibrio por clase
REINICIO = 16                             # deflate_flujo con reinicio, como en la red
P_PERDIDA, MAX_MAL_PCT = 0.01, 1.0        # robustez: tope de % mal reconstruidos para "robusto"


def _nombre(v: float) -> str:
    return f"{v / 1e9:.3g} Gbps" if v >= 1e9 else f"{v / 1e6:.3g} Mbps" if v >= 1e6 else f"{v / 1e3:.3g} kbps"


# ═══════════════════════════ políticas ═══════════════════════════
def politicas(m) -> list[tuple[str, Callable[[], Callable[[bytes], bytes]]]]:
    """(nombre, fábrica). Cada fábrica arma un emisor NUEVO: una función paquete -> marco."""
    def fija(cod):
        def fab():
            tx = C.Compresor(m, reinicio_flujo=REINICIO)
            return lambda p: tx.empaquetar(p, cod)[0]
        return fab

    def bandido():
        em = EmisorAdaptativo(m, reinicio_flujo=REINICIO)
        return lambda p: em.enviar(p)[0]

    pol = [("sin compresión", None)]
    pol += [(f"ROHC + {nom}", fija(cod)) for cod, nom in C.CODECS.items()]
    pol.append(("ROHC + bandido UCB", bandido))
    return pol


LATENCIA = "ROHC + bandido UCB (latencia)"


def bandido_latencia(m, velocidad: float, factor_cpu: float):
    """Fábrica del bandido premiado por latencia, que sabe la velocidad del enlace."""
    def fab():
        em = EmisorAdaptativo(m, reinicio_flujo=REINICIO, velocidad_bps=velocidad, factor_cpu=factor_cpu)
        fab.emisor = em
        return lambda p: em.enviar(p)[0]
    return fab


def medir(m, mezcla, fab) -> list[tuple[str, int, int, float, float]]:
    """Por paquete: (clase, bytes crudos, bytes del marco, s comprimir, s descomprimir).
    Verifica cada paquete byte a byte."""
    if fab is None:
        return [(clase, len(p), len(p), 0.0, 0.0) for clase, _, p in mezcla]
    enviar, rx = fab(), C.Descompresor(m)
    out, reloj = [], time.perf_counter
    gc.disable()
    try:
        for clase, _, p in mezcla:
            t0 = reloj(); marco = enviar(p); t1 = reloj()
            r = rx.desempaquetar(marco); t2 = reloj()
            assert r == p, clase
            out.append((clase, len(p), len(marco), t1 - t0, t2 - t1))
    finally:
        gc.enable()
    return out


def pct_mal(m, mezcla, fab, semilla=7) -> float:
    """% de los paquetes que SÍ llegan que salen mal, si se pierde el 1 % (como evaluar.perdidas)."""
    if fab is None:
        return 0.0
    rng = random.Random(semilla)
    enviar, rx, ok, mal = fab(), C.Descompresor(m), 0, 0
    for _, _, p in mezcla:
        marco = enviar(p)
        if rng.random() < P_PERDIDA:
            continue
        try:
            bien = rx.desempaquetar(marco) == p
        except Exception:
            bien = False
        ok += bien; mal += not bien
    return 100 * mal / (ok + mal)


# ═══════════════════════════ modelo de colas ═══════════════════════════
def _mg1(lam: float, s1: float, s2: float) -> float:
    """Tiempo medio en el sistema de una cola M/G/1 (s). s1 = E[S], s2 = E[S²]."""
    if s1 == 0.0:
        return 0.0
    rho = lam * s1
    if rho >= 1.0:
        return math.inf
    return s1 + lam * s2 / (2 * (1 - rho))


def momentos(filas, factor_cpu: float) -> dict:
    n = len(filas)
    tc = [f[3] * factor_cpu for f in filas]
    td = [f[4] * factor_cpu for f in filas]
    L = [8 * f[2] for f in filas]  # bits del marco
    return {"n": n, "crudo_bits": sum(8 * f[1] for f in filas) / n,
            "L1": sum(L) / n, "L2": sum(x * x for x in L) / n,
            "c1": sum(tc) / n, "c2": sum(x * x for x in tc) / n,
            "d1": sum(td) / n, "d2": sum(x * x for x in td) / n}


def latencia(mo: dict, velocidad: float, carga: float, crudo_bits: float) -> float:
    """Latencia media (s) de una política. crudo_bits = tamaño medio SIN comprimir (fija λ)."""
    lam = carga * velocidad / crudo_bits
    return (_mg1(lam, mo["c1"], mo["c2"])
            + _mg1(lam, mo["L1"] / velocidad, mo["L2"] / velocidad ** 2)
            + _mg1(lam, mo["d1"], mo["d2"]))


def equilibrio(mo: dict, ref: dict, carga: float) -> float | None:
    """Velocidad de enlace por debajo de la cual la política tiene MENOS latencia que no
    comprimir. inf = gana siempre (en el rango probado), None = no gana nunca."""
    gana = lambda v: latencia(mo, v, carga, ref["crudo_bits"]) < latencia(ref, v, carga, ref["crudo_bits"])
    lo, hi = math.log10(1e3), math.log10(1e11)   # 1 kbps … 100 Gbps
    if not gana(10 ** lo):
        return None
    if gana(10 ** hi):
        return math.inf
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if gana(10 ** mid) else (lo, mid)
    return 10 ** lo


# ═══════════════════════════ reporte ═══════════════════════════
def evaluar(m, mezcla) -> dict:
    pol = politicas(m)
    medidas, mal = {}, {}
    for nombre, fab in pol:
        print(f"  midiendo {nombre}...", flush=True)
        medidas[nombre] = medir(m, mezcla, fab)
        mal[nombre] = round(pct_mal(m, mezcla, fab), 2)
    clases = list(dict.fromkeys(c for c, _, _ in mezcla))
    vistas = {}
    for etiqueta, factor in (("python", 1.0), ("cpu_10x", 0.1)):
        mo = {n: momentos(f, factor) for n, f in medidas.items()}
        ref = mo["sin compresión"]
        # El bandido por latencia, una corrida por velocidad (aprende con el costo de CPU de esta vista).
        lat_v = {}
        for v in VELOCIDADES:
            print(f"  midiendo {LATENCIA} a {_nombre(v)} ({etiqueta})...", flush=True)
            fab = bandido_latencia(m, v, factor)
            fil = medir(m, mezcla, fab)
            elegidos = fab.emisor.elegidos
            lat_v[v] = {"momentos": momentos(fil, factor), "mal": round(pct_mal(m, mezcla, fab), 2),
                        "ahorro_pct": round(100 * (1 - sum(f[2] for f in fil) / sum(f[1] for f in fil)), 2),
                        "comprimir_us": round(sum(f[3] for f in fil) / len(fil) * 1e6, 1),
                        "elegidos_pct": {C.CODECS[k]: round(100 * n / sum(elegidos.values()), 1)
                                         for k, n in sorted(elegidos.items())}}
        rejilla = []
        for v in VELOCIDADES:
            for carga in CARGAS:
                lat = {n: latencia(mo[n], v, carga, ref["crudo_bits"]) for n in mo}
                lat[LATENCIA] = latencia(lat_v[v]["momentos"], v, carga, ref["crudo_bits"])
                mal_v = dict(mal, **{LATENCIA: lat_v[v]["mal"]})
                robustos = {n: t for n, t in lat.items() if mal_v[n] <= MAX_MAL_PCT}
                rejilla.append({"velocidad_bps": v, "carga": carga,
                                "latencia_ms": {n: (round(t * 1e3, 4) if math.isfinite(t) else None)
                                                for n, t in lat.items()},
                                "ganador": min(lat, key=lat.get),
                                "ganador_robusto": min(robustos, key=robustos.get)})
        por_clase = {}
        for clase in clases:
            mc = {n: momentos([f for f in fil if f[0] == clase], factor) for n, fil in medidas.items()}
            rc = mc["sin compresión"]
            por_clase[clase] = {n: equilibrio(mc[n], rc, CARGA_EQUILIBRIO) for n in mc if n != "sin compresión"}
            # JSON sin Infinity: "siempre" = gana en todo el rango, None = nunca
            por_clase[clase] = {n: ("siempre" if x is not None and math.isinf(x) else x)
                                for n, x in por_clase[clase].items()}
        bandido_lat = [{"velocidad_bps": v, **{k: x for k, x in d.items() if k != "momentos"}}
                       for v, d in lat_v.items()]
        vistas[etiqueta] = {"factor_cpu": factor, "rejilla": rejilla, "equilibrio_por_clase": por_clase,
                            "bandido_latencia": bandido_lat}
    resumen = []
    for nombre, fil in medidas.items():
        crudo, marco = sum(f[1] for f in fil), sum(f[2] for f in fil)
        resumen.append({"politica": nombre, "ahorro_pct": round(100 * (1 - marco / crudo), 2),
                        "comprimir_us": round(sum(f[3] for f in fil) / len(fil) * 1e6, 1),
                        "descomprimir_us": round(sum(f[4] for f in fil) / len(fil) * 1e6, 1),
                        "pct_mal_1pct_perdida": mal[nombre]})
    return {"paquetes": len(mezcla), "resumen": resumen, "vistas": vistas,
            "parametros": {"velocidades_bps": VELOCIDADES, "cargas": CARGAS, "carga_equilibrio": CARGA_EQUILIBRIO,
                           "reinicio_flujo": REINICIO, "p_perdida": P_PERDIDA, "max_mal_pct": MAX_MAL_PCT}}


def _ms(x) -> str:
    if x is None:
        return "∞"
    return f"{x:.3g}" if x < 100 else f"{x:.0f}"


def _vel(x) -> str:
    if x is None:
        return "nunca"
    if x == "siempre":
        return x
    return _nombre(x)


def markdown(r: dict) -> str:
    nombres = [f["politica"] for f in r["resumen"]]
    corto = lambda n: n.replace("ROHC + ", "")
    md = ("# Balance latencia ↔ banda por algoritmo (generado por `evaluar.py balance`)\n\n"
          f"{r['paquetes']} paquetes del flujo mezclado de PRUEBA, cada uno verificado byte a byte. "
          "Modelo: compresor → enlace → descompresor como colas M/G/1 en serie (Pollaczek–Khinchine), "
          "con los tamaños y tiempos medidos de cada paquete. Ver el encabezado de `balance.py`.\n\n"
          "## 1. Lo que cuesta y lo que ahorra cada política\n\n"
          "| política | ahorro de bytes % | comprimir µs/paq | descomprimir µs/paq | % mal con 1 % de pérdida |\n"
          "|---|---|---|---|---|\n")
    for f in r["resumen"]:
        md += (f"| {f['politica']} | {f['ahorro_pct']} | {f['comprimir_us']} | {f['descomprimir_us']} | "
               f"{f['pct_mal_1pct_perdida']} |\n")
    titulos = {"python": "CPU medida (Python, esta máquina)",
               "cpu_10x": "CPU 10× más rápida (supuesto: implementación en C o en la estación base)"}
    rejilla_nombres = nombres + [LATENCIA]
    for i, (et, v) in enumerate(r["vistas"].items()):
        md += (f"\n## {2 + 3 * i}. Latencia media en ms por escenario — {titulos[et]}\n\n"
               "Carga = cuánto ocuparía el enlace el tráfico SIN comprimir. **Negrita** = menor latencia. "
               f"\"Robusto\" = el mejor entre los que reconstruyen mal ≤ {MAX_MAL_PCT:g} % con 1 % de pérdida. "
               "∞ = alguna etapa no da abasto. La última columna es el bandido premiado por latencia, que "
               "sabe la velocidad del enlace.\n\n"
               "| enlace | carga | " + " | ".join(corto(n) for n in rejilla_nombres) + " | gana | gana (robusto) |\n"
               "|" + "---|" * (len(rejilla_nombres) + 4) + "\n")
        for c in v["rejilla"]:
            celdas = []
            for n in rejilla_nombres:
                t = _ms(c["latencia_ms"][n])
                celdas.append(f"**{t}**" if n == c["ganador"] else t)
            md += (f"| {_nombre(c['velocidad_bps'])} | {c['carga']:.0%} | " + " | ".join(celdas)
                   + f" | {corto(c['ganador'])} | {corto(c['ganador_robusto'])} |\n")
        cods = list(dict.fromkeys(k for b in v["bandido_latencia"] for k in b["elegidos_pct"]))
        md += (f"\n## {3 + 3 * i}. Qué aprende el bandido por latencia a cada velocidad — {titulos[et]}\n\n"
               "Mismo bandido, mismos brazos; solo cambia la velocidad del enlace que conoce. % de paquetes en "
               "que eligió cada códec (antes del respaldo a \"ninguno\").\n\n"
               "| enlace | ahorro % | comprimir µs/paq | % mal con 1 % de pérdida | "
               + " | ".join(f"elige {k} %" for k in cods) + " |\n|" + "---|" * (len(cods) + 4) + "\n")
        for b in v["bandido_latencia"]:
            md += (f"| {_nombre(b['velocidad_bps'])} | {b['ahorro_pct']} | {b['comprimir_us']} | {b['mal']} | "
                   + " | ".join(str(b["elegidos_pct"].get(k, 0)) for k in cods) + " |\n")
        md += (f"\n## {4 + 3 * i}. Velocidad de equilibrio por clase — {titulos[et]}\n\n"
               f"Con carga {r['parametros']['carga_equilibrio']:.0%}: por debajo de esta velocidad de enlace la "
               "política tiene MENOS latencia que no comprimir. \"nunca\" = no ahorra lo suficiente para pagar su "
               "CPU a ninguna velocidad; \"siempre\" = gana en todo el rango probado (1 kbps a 100 Gbps).\n\n"
               "| clase | " + " | ".join(corto(n) for n in nombres[1:]) + " |\n|" + "---|" * len(nombres) + "\n")
        for clase, eq in v["equilibrio_por_clase"].items():
            md += f"| {clase} | " + " | ".join(_vel(eq[n]) for n in nombres[1:]) + " |\n"
    return md
