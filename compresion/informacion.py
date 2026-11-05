#!/usr/bin/env python3
"""
informacion.py — Lo que pide el punto (c) del Lab 3 llevado a la red: por cada algoritmo de
compresión, el paquete ORIGINAL, el COMPRIMIDO y el RECUPERADO, y la información mutua entre
el original y el recuperado. Además, la tasa, la velocidad y la confiabilidad de cada uno.

    python evaluar.py informacion                        # -> resultados/informacion.md y .json
    python evaluar.py informacion --publicar URL         # y además al pushgateway (Grafana)

Qué se mide, por algoritmo y por clase de tráfico (y para el flujo mezclado completo):

  · Original    bytes y H(O): entropía de orden 0 de sus bytes, en bits por byte.
  · Comprimido  bytes, razón de compresión y H(C). Un buen compresor deja bytes casi
                aleatorios (H(C) cerca de 8) en menos espacio.
  · Recuperado  H(R) e I(O;R), la información mutua entre el original y lo que reconstruye el
                receptor, en bits por byte del original. Se mide en dos canales:
                  - perfecto: no se pierde nada. Todo algoritmo sin pérdida da I(O;R) = H(O).
                  - con pérdida de 1 % de los paquetes, al azar. El paquete perdido no llega,
                    y los que dependen de él (DEFLATE con estado por flujo) pueden llegar mal.
                    Aquí los algoritmos se separan.
                "Información entregada" = I(O;R) / H(O): qué fracción de la información del
                original llega al otro lado.
  · Velocidad   µs por paquete de comprimir y de descomprimir.
  · Confiabilidad  % de los paquetes que llegan pero salen mal, con 1 % de pérdida.

Cómo se estima I(O;R): byte a byte, alineando cada byte del original con el byte de la misma
posición del recuperado. Si el paquete no llegó, no se pudo descomprimir o salió más corto,
esa posición cuenta como el símbolo "borrado" (un 257.º símbolo). Con la tabla conjunta de
conteos se calcula I = H(O) + H(R) − H(O,R) (estimador directo, "plug-in"). Si el recuperado es
idéntico, la tabla es diagonal y da exactamente I = H(O).

Por qué no se reporta I(O;C), entre el original y el comprimido: el comprimido es una función
invertible del original (si no, el receptor no podría reconstruirlo), así que por paquete
I(O;C) = H(O) para cualquier algoritmo sin pérdida. Byte a byte no tiene sentido: el byte i
del comprimido no corresponde al byte i del original. Lo que sí distingue a los algoritmos es
en cuántos bytes meten esa información: eso es la razón de compresión y H(C).
"""
from __future__ import annotations

import math, random, time
from collections import Counter

import compresores as C
from balance import politicas

BORRADO = 256
P_PERDIDA, SEMILLA = 0.01, 7
CANALES = ("perfecto", "perdida_1pct")
# Nombres de las políticas como etiqueta de Prometheus ("algoritmo"). Los códecs usan su nombre
# del registro; un códec nuevo aparece solo con el suyo.
NOMBRES = {"sin compresión": "sin_compresion", "ROHC + ninguno": "solo_rohc", "ROHC + bandido UCB": "bandido_ucb"}


def _h(conteo: Counter) -> float:
    n = sum(conteo.values())
    return -sum(c / n * math.log2(c / n) for c in conteo.values()) if n else 0.0


class Acumulador:
    """Conteos de una clase (o de todas) para un algoritmo y un canal."""

    def __init__(self):
        self.o, self.r, self.oc, self.c = Counter(), Counter(), Counter(), Counter()
        self.bytes_o = self.bytes_c = self.paquetes = self.llegaron = self.mal = 0
        self.t_comp = self.t_desc = 0.0

    def sumar(self, o: bytes, marco: bytes, r, t_comp: float, t_desc: float, llego: bool) -> None:
        self.paquetes += 1
        self.bytes_o += len(o); self.bytes_c += len(marco)
        self.t_comp += t_comp; self.t_desc += t_desc
        self.c.update(marco)
        self.o.update(o)
        if r is None:
            rr = [BORRADO] * len(o)
        else:
            rr = list(r[:len(o)]) + [BORRADO] * max(0, len(o) - len(r))
        self.r.update(rr)
        self.oc.update(zip(o, rr))
        if llego:
            self.llegaron += 1
            self.mal += r != o

    def resultado(self) -> dict:
        ho, hr, hor = _h(self.o), _h(self.r), _h(self.oc)
        i_or = max(0.0, ho + hr - hor)
        return {
            "paquetes": self.paquetes,
            "original": {"bytes": self.bytes_o, "entropia": round(ho, 4)},
            "comprimido": {"bytes": self.bytes_c, "entropia": round(_h(self.c), 4),
                           "razon": round(self.bytes_o / self.bytes_c, 4),
                           "ahorro_pct": round(100 * (1 - self.bytes_c / self.bytes_o), 2)},
            "recuperado": {"entropia": round(hr, 4), "info_mutua": round(i_or, 4),
                           "info_entregada_pct": round(100 * i_or / ho, 3) if ho else 100.0},
            "comprimir_us": round(self.t_comp / self.paquetes * 1e6, 1),
            "descomprimir_us": round(self.t_desc / self.paquetes * 1e6, 1),
            "mal_pct": round(100 * self.mal / self.llegaron, 2) if self.llegaron else 0.0,
        }


def medir(m, mezcla, fab, canal: str) -> dict:
    """Pasa el flujo mezclado por un algoritmo y un canal. -> {clase o "todas": resultado}."""
    caen = random.Random(SEMILLA)
    acc: dict[str, Acumulador] = {"todas": Acumulador()}
    enviar = fab() if fab is not None else (lambda p: p)
    rx = C.Descompresor(m) if fab is not None else None
    reloj = time.perf_counter
    for clase, _, p in mezcla:
        t0 = reloj(); marco = enviar(p); t1 = reloj()
        perdido = canal == "perdida_1pct" and caen.random() < P_PERDIDA
        r, t2 = None, t1
        if not perdido:
            try:
                r = rx.desempaquetar(marco) if rx is not None else marco
            except Exception:
                r = b""  # llegó pero no se pudo descomprimir: todo se cuenta como borrado
            t2 = reloj()
        for a in (acc["todas"], acc.setdefault(clase, Acumulador())):
            a.sumar(p, marco, r, t1 - t0, t2 - t1, not perdido)
    return {k: a.resultado() for k, a in acc.items()}


def evaluar(m, mezcla) -> dict:
    res = {}
    for nombre, fab in politicas(m):
        alg = NOMBRES.get(nombre, nombre.replace("ROHC + ", ""))
        print(f"  {alg}...", flush=True)
        res[alg] = {canal: medir(m, mezcla, fab, canal) for canal in CANALES}
    return {"paquetes": len(mezcla), "p_perdida": P_PERDIDA, "algoritmos": res}


# ═══════════════════════════ Prometheus / pushgateway ═══════════════════════════
def texto_prometheus(r: dict) -> str:
    """Formato de exposición de Prometheus. Todas las métricas llevan algoritmo y clase."""
    lineas = {}

    def g(nombre, ayuda, etiquetas, valor):
        if nombre not in lineas:
            lineas[nombre] = [f"# HELP {nombre} {ayuda}", f"# TYPE {nombre} gauge"]
        et = ",".join(f'{k}="{v}"' for k, v in etiquetas.items())
        lineas[nombre].append(f"{nombre}{{{et}}} {valor}")

    for alg, canales in r["algoritmos"].items():
        for clase, x in canales["perfecto"].items():
            e = {"algoritmo": alg, "clase": clase}
            g("lab5g_cmp_eval_bytes", "Bytes por etapa (original / comprimido)", dict(e, etapa="original"),
              x["original"]["bytes"])
            g("lab5g_cmp_eval_bytes", "Bytes por etapa (original / comprimido)", dict(e, etapa="comprimido"),
              x["comprimido"]["bytes"])
            g("lab5g_cmp_eval_entropia_bits_por_byte", "Entropia de orden 0 por etapa",
              dict(e, etapa="original"), x["original"]["entropia"])
            g("lab5g_cmp_eval_entropia_bits_por_byte", "Entropia de orden 0 por etapa",
              dict(e, etapa="comprimido"), x["comprimido"]["entropia"])
            g("lab5g_cmp_eval_razon", "Razon de compresion (bytes originales / comprimidos)", e,
              x["comprimido"]["razon"])
            g("lab5g_cmp_eval_ahorro_pct", "Porcentaje de bytes ahorrados", e, x["comprimido"]["ahorro_pct"])
            g("lab5g_cmp_eval_comprimir_us", "Microsegundos por paquete para comprimir", e, x["comprimir_us"])
            g("lab5g_cmp_eval_descomprimir_us", "Microsegundos por paquete para descomprimir", e,
              x["descomprimir_us"])
        for canal, por_clase in canales.items():
            for clase, x in por_clase.items():
                e = {"algoritmo": alg, "clase": clase, "canal": canal}
                g("lab5g_cmp_eval_entropia_bits_por_byte", "Entropia de orden 0 por etapa",
                  {"algoritmo": alg, "clase": clase, "etapa": f"recuperado_{canal}"}, x["recuperado"]["entropia"])
                g("lab5g_cmp_eval_info_mutua_bits_por_byte", "I(original; recuperado) en bits por byte del original",
                  e, x["recuperado"]["info_mutua"])
                g("lab5g_cmp_eval_info_entregada_pct", "I(O;R) / H(O): fraccion de la informacion que llega", e,
                  x["recuperado"]["info_entregada_pct"])
                g("lab5g_cmp_eval_mal_reconstruidos_pct", "Paquetes que llegan pero salen mal", e, x["mal_pct"])
    g("lab5g_cmp_eval_ultima_corrida_segundos", "Hora (epoch) de la ultima evaluacion", {"job_eval": "informacion"},
      int(time.time()))
    return "\n".join(l for v in lineas.values() for l in v) + "\n"


def publicar(url: str, texto: str) -> None:
    import urllib.request
    req = urllib.request.Request(f"{url.rstrip('/')}/metrics/job/cmp_eval", data=texto.encode(), method="PUT")
    urllib.request.urlopen(req, timeout=10).read()


# ═══════════════════════════ reporte ═══════════════════════════
def markdown(r: dict) -> str:
    md = ("# Original, comprimido y recuperado por algoritmo (generado por `evaluar.py informacion`)\n\n"
          f"{r['paquetes']} paquetes del flujo mezclado de PRUEBA. H = entropía de orden 0 en bits por byte. "
          "I(O;R) = información mutua entre el original y el recuperado, en bits por byte del original "
          f"(ver `informacion.py`). Canal con pérdida: {r['p_perdida']:.0%} de los paquetes al azar.\n\n"
          "## 1. Flujo mezclado completo\n\n"
          "| algoritmo | H(O) | bytes O | bytes C | razón | ahorro % | H(C) | I(O;R) perfecto | "
          "I(O;R) con pérdida | info. entregada con pérdida % | mal con pérdida % | comprimir µs | descomprimir µs |\n"
          "|" + "---|" * 13 + "\n")
    for alg, ca in r["algoritmos"].items():
        p, q = ca["perfecto"]["todas"], ca["perdida_1pct"]["todas"]
        md += (f"| {alg} | {p['original']['entropia']} | {p['original']['bytes']} | {p['comprimido']['bytes']} | "
               f"{p['comprimido']['razon']} | {p['comprimido']['ahorro_pct']} | {p['comprimido']['entropia']} | "
               f"{p['recuperado']['info_mutua']} | {q['recuperado']['info_mutua']} | "
               f"{q['recuperado']['info_entregada_pct']} | {q['mal_pct']} | {p['comprimir_us']} | "
               f"{p['descomprimir_us']} |\n")
    clases = [c for c in next(iter(r["algoritmos"].values()))["perfecto"] if c != "todas"]
    md += ("\n## 2. Información entregada con 1 % de pérdida, por clase (% de H(O) que llega)\n\n"
           "Con canal perfecto todos dan 100 %. Lo que baja de 99 % (lo que se pierde en el canal) es daño "
           "que agrega el algoritmo.\n\n| algoritmo | " + " | ".join(clases) + " |\n|" + "---|" * (len(clases) + 1) + "\n")
    for alg, ca in r["algoritmos"].items():
        md += f"| {alg} | " + " | ".join(str(ca["perdida_1pct"][c]["recuperado"]["info_entregada_pct"])
                                         for c in clases) + " |\n"
    md += ("\n## 3. Ahorro por clase (%) y H(C)\n\n| algoritmo | " + " | ".join(clases) + " |\n|"
           + "---|" * (len(clases) + 1) + "\n")
    for alg, ca in r["algoritmos"].items():
        md += f"| {alg} | " + " | ".join(
            f"{ca['perfecto'][c]['comprimido']['ahorro_pct']} (H {ca['perfecto'][c]['comprimido']['entropia']})"
            for c in clases) + " |\n"
    return md
