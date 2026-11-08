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

import json, math, random, time
from collections import Counter

import compresores as C
import trafico as T
from balance import politicas

BORRADO = 256
P_PERDIDA, SEMILLA = 0.01, 7
CANALES = ("perfecto", "perdida_1pct")
# Nombres de las políticas como etiqueta de Prometheus ("algoritmo"). Los códecs usan su nombre
# del registro; un códec nuevo aparece solo con el suyo.
NOMBRES = {"sin compresión": "sin_compresion", "ROHC + ninguno": "solo_rohc", "ROHC + bandido UCB": "bandido_ucb"}
# Título y descripción de las políticas que no son un códec (los códecs traen los suyos).
TEXTOS = {"sin_compresion": ("Sin compresión (referencia)",
                             "El paquete viaja tal cual. Es el punto cero contra el que se mide todo."),
          "solo_rohc": ("Solo ROHC (cabeceras)",
                        "Comprime solo las cabeceras IP/UDP/RTP (como ROHC en PDCP); la carga viaja tal cual."),
          "bandido_ucb": ("Bandido UCB (IA): elige por paquete",
                          "La IA del lab: un bandido UCB con descuento que elige, paquete a paquete, entre "
                          "ninguno y los mejores códecs, según el tamaño y la entropía del paquete.")}


def textos_algoritmo(alg: str) -> tuple[str, str]:
    """(título, descripción) de un algoritmo del reporte. Los códecs: "ROHC + <título>"."""
    if alg in TEXTOS:
        return TEXTOS[alg]
    cls = next((k for k in C.REGISTRO.values() if k.nombre == alg), None)
    if cls is None:
        return alg, ""
    return f"ROHC + {cls.titulo or alg}", cls.descripcion


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
    print("  tasa-distorsión IoT...", flush=True)
    return {"paquetes": len(mezcla), "p_perdida": P_PERDIDA, "algoritmos": res,
            "tasa_distorsion_iot": tasa_distorsion_iot(m, mezcla)}


# ═══════════════════════════ con pérdida: tasa-distorsión de la telemetría ═══════════════════════════
# Escalas de los pasos de PredictivoIoT (× PASOS_IOT). 0 = pasos de 1e-6: en la práctica sin pérdida.
ESCALAS_IOT = (0.0, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0)
CAMPOS_IOT = ("temp", "hum", "bat")


def _pasos(escala: float) -> dict:
    return {k: (1e-6 if escala == 0 else v * escala) for k, v in C.PASOS_IOT.items()}


def tasa_distorsion_iot(m, mezcla) -> dict:
    """Solo los paquetes iot_json del flujo, con PredictivoIoT a varias escalas de paso y, como
    referencia, con los mejores códecs sin pérdida. Por cada punto: bytes, ahorro, error máximo
    y medio por campo, % de paquetes dentro de la tolerancia (error ≤ paso/2 en todos los campos)
    e I(X;Y)/H(X) de cada medida.

    Aquí I(X;Y) se mide sobre los VALORES de cada campo (X = lectura original, Y = la que recibe el
    receptor; un paquete perdido es Y = borrado), no byte a byte: con pérdida el JSON cambia de
    largo (21.53 → 21.5) y alinear bytes por posición ya no compara lo mismo. Con ~600 lecturas
    el estimador directo tiene sesgo (H no puede pasar de log2 600 ≈ 9,2 bits), así que sirve para
    comparar puntos entre sí."""
    iot = [p for clase, _, p in mezcla if clase == "iot_json"]
    puntos = [("predictivo", e) for e in ESCALAS_IOT] + [("sin_perdida", c) for c in (C.ZSTD_DICT, C.DEFLATE_FLUJO)
                                                           if c in C.CODECS]
    filas = []
    for metodo, param in puntos:
        con_perdida = metodo == "predictivo"
        pasos = _pasos(param) if con_perdida else None
        etiqueta = f"predictivo ×{param:g}" if con_perdida else C.CODECS[param]
        for canal in CANALES:
            tx = C.Compresor(m, reinicio_flujo=16, perdida_iot=pasos)
            rx = C.Descompresor(m)
            caen = random.Random(SEMILLA)
            bo = bc = llegaron = dentro = dañados = 0
            err = {k: [] for k in CAMPOS_IOT}
            xy = {k: (Counter(), Counter(), Counter()) for k in CAMPOS_IOT}
            for p in iot:
                marco, _ = tx.empaquetar(p, C.PREDICTIVO_IOT if con_perdida else param)
                bo += len(p); bc += len(marco)
                orig = json.loads(p[28:])
                rec = None
                if not (canal == "perdida_1pct" and caen.random() < P_PERDIDA):
                    llegaron += 1
                    try:
                        r = rx.desempaquetar(marco)
                        e = C.PredictivoIoT.error_campos(p[28:], r[28:])
                        rec = json.loads(r[28:]) if e is not None else None
                    except Exception:
                        e = None
                    if e is None:
                        dañados += 1
                    else:
                        tol = pasos or {}
                        if all(e[k] <= tol.get(k, 0) / 2 + 1e-9 for k in CAMPOS_IOT):
                            dentro += 1
                        for k in CAMPOS_IOT:
                            err[k].append(e[k])
                for k in CAMPOS_IOT:
                    y = rec[k] if rec else "borrado"
                    xy[k][0].update([orig[k]]); xy[k][1].update([y]); xy[k][2].update([(orig[k], y)])
            info = {}
            for k in CAMPOS_IOT:
                hx, hy, hxy = (_h(c) for c in xy[k])
                info[k] = round(100 * max(0.0, hx + hy - hxy) / hx, 2) if hx else 100.0
            filas.append({
                "metodo": etiqueta, "escala": param if con_perdida else None, "canal": canal,
                "pasos": pasos, "paquetes": len(iot), "bytes_original": bo, "bytes_comprimido": bc,
                "bytes_por_paquete": round(bc / len(iot), 2), "ahorro_pct": round(100 * (1 - bc / bo), 2),
                "error_max": {k: round(max(v), 6) if v else None for k, v in err.items()},
                "error_medio": {k: round(sum(v) / len(v), 6) if v else None for k, v in err.items()},
                "dentro_tolerancia_pct": round(100 * dentro / llegaron, 2) if llegaron else 0.0,
                "dañados_pct": round(100 * dañados / llegaron, 2) if llegaron else 0.0,
                "info_entregada_pct": info,
                "info_entregada_media_pct": round(sum(info.values()) / len(info), 2)})
    return {"campos": list(CAMPOS_IOT), "pasos_base": C.PASOS_IOT, "filas": filas}


# ═══════════════════════════ Prometheus / pushgateway ═══════════════════════════
def texto_prometheus(r: dict) -> str:
    """Formato de exposición de Prometheus. Todas las métricas llevan algoritmo y clase."""
    lineas = {}

    def g(nombre, ayuda, etiquetas, valor):
        if nombre not in lineas:
            lineas[nombre] = [f"# HELP {nombre} {ayuda}", f"# TYPE {nombre} gauge"]
        esc = lambda v: str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        et = ",".join(f'{k}="{esc(v)}"' for k, v in etiquetas.items())
        lineas[nombre].append(f"{nombre}{{{et}}} {valor}")

    # Para los menús de Grafana: el texto que se ve (numerado, para que salga en este orden) y la
    # descripción de cada algoritmo y de cada clase. Valor siempre 1; lo útil son las etiquetas.
    for i, alg in enumerate(r["algoritmos"], 1):
        titulo, desc = textos_algoritmo(alg)
        g("lab5g_cmp_eval_algoritmo_info", "Titulo y descripcion de cada algoritmo (para los menus)",
          {"algoritmo": alg, "texto": f"{i:02d} · {titulo}", "descripcion": desc}, 1)
    clases = next(iter(r["algoritmos"].values()))["perfecto"]
    for clase in clases:
        texto = "Todas las clases (flujo mezclado)" if clase == "todas" else \
            f"{clase} — {T.DESCRIPCION_CLASE.get(clase, '')}"
        g("lab5g_cmp_eval_clase_info", "Descripcion de cada clase de trafico (para los menus)",
          {"clase": clase, "texto": texto}, 1)
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
    for f in r.get("tasa_distorsion_iot", {}).get("filas", []):
        e = {"metodo": f["metodo"], "canal": f["canal"]}
        g("lab5g_cmp_eval_td_ahorro_pct", "IoT: ahorro de bytes por metodo (tasa-distorsion)", e, f["ahorro_pct"])
        g("lab5g_cmp_eval_td_bytes_por_paquete", "IoT: bytes por paquete transmitidos", e, f["bytes_por_paquete"])
        g("lab5g_cmp_eval_td_dentro_tolerancia_pct", "IoT: paquetes recibidos con error <= paso/2 en todos los campos",
          e, f["dentro_tolerancia_pct"])
        g("lab5g_cmp_eval_td_info_entregada_pct", "IoT: I(X;Y)/H(X) medio de las medidas (valores, no bytes)", e,
          f["info_entregada_media_pct"])
        for campo, v in f["error_max"].items():
            if v is not None:
                g("lab5g_cmp_eval_td_error_max", "IoT: error absoluto maximo por campo", dict(e, campo=campo), v)
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
    td = r.get("tasa_distorsion_iot")
    if td:
        base = ", ".join(f"{k} {v:g}" for k, v in td["pasos_base"].items())
        md += ("\n## 4. Con pérdida: tasa-distorsión de la telemetría IoT (`predictivo_iot`)\n\n"
               f"Solo los paquetes iot_json. «×e» = pasos de cuantización × e (×1 = {base}); ×0 = pasos de "
               "1e-6, en la práctica sin pérdida. «Dentro de tolerancia» = error ≤ paso/2 en temp, hum y bat. "
               "«I/H» = información mutua entre la lectura original y la recibida, sobre los valores.\n\n"
               "| método | canal | bytes/paquete | ahorro % | error máx temp / hum / bat | dentro de tolerancia % | "
               "dañados % | I/H medio % |\n|---|---|---:|---:|---|---:|---:|---:|\n")
        for f in td["filas"]:
            em = " / ".join("—" if f["error_max"][k] is None else f"{f['error_max'][k]:g}" for k in td["campos"])
            md += (f"| {f['metodo']} | {f['canal']} | {f['bytes_por_paquete']} | {f['ahorro_pct']} | {em} | "
                   f"{f['dentro_tolerancia_pct']} | {f['dañados_pct']} | {f['info_entregada_media_pct']} |\n")
    return md
