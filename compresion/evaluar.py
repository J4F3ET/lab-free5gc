#!/usr/bin/env python3
"""
evaluar.py — Compara las opciones de compresión sobre tráfico 5G de prueba, sin red.

  python evaluar.py entrenar            # entrena modelo.json con el conjunto de ENTRENAMIENTO
  python evaluar.py ajustar             # elige γ, c y brazos del bandido, también con ENTRENAMIENTO
  python evaluar.py comparar            # mide todo sobre el conjunto de PRUEBA -> resultados/
  python evaluar.py balance             # latencia neta de cada algoritmo según el enlace (balance.py)

Entrenamiento (semilla 1, textos TEXTOS_ENTRENO) y prueba (semilla 2, TEXTOS_PRUEBA) no
comparten paquetes: lo que se mide es sobre tráfico que el modelo nunca vio. Es justo lo
que el Lab 3 declaraba como límite de su simulación ("no hay un flujo de prueba separado").

Qué se mide (todo verificado: cada paquete se descomprime y se compara byte a byte):
  1. Por clase de tráfico: ahorro de cada códec, con y sin compresión de cabeceras.
  2. Flujo mezclado (todas las clases intercaladas, como una celda): códecs fijos y el
     bandido. Bytes y tiempo de CPU del emisor.
  3. Latencia por paquete de cada códec (comprimir + descomprimir, Python puro).
  4. Robustez: 1 % de paquetes perdidos entre emisor y receptor.
"""
from __future__ import annotations

import argparse, json, random, statistics, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trafico as T
import compresores as C
from bandido import EmisorAdaptativo

DIR = Path(__file__).resolve().parent
MODELO = DIR / "modelo.json"
SALIDA = DIR / "resultados"
P_FLUJO, F_CLASE = 200, 3


def entrenar() -> C.Modelo:
    conj = T.conjunto(1, P_FLUJO, F_CLASE, prueba=False)
    hc = C.Compresor(None)
    cargas = [hc.separar(p)[2] for fs in conj.values() for f in fs for p in f]
    m = C.Modelo.entrenar(cargas)
    m.guardar(MODELO)
    print(f"modelo.json: {len(m.diccionario)} B de diccionario, Huffman de {len(m.huff.lens)} símbolos "
          f"(códigos de {m.huff.lmin} a {m.huff.lmax} bits), entrenado con {len(cargas)} paquetes")
    return m


def _pct(a, b):
    return round(100 * (1 - b / a), 2) if a else 0.0


def por_clase(m, conj):
    filas = []
    for clase, fs in conj.items():
        crudo = sum(len(p) for f in fs for p in f)
        fila = {"clase": clase, "slice": T.SLICE[clase], "paquetes": sum(map(len, fs)),
                "bytes_crudos": crudo, "largo_medio": round(crudo / sum(map(len, fs)), 1)}
        for hc in (False, True):
            for cod, nom in C.CODECS.items():
                tx, rx, out = C.Compresor(m, hc), C.Descompresor(m), 0
                for f in fs:
                    for p in f:
                        marco, _ = tx.empaquetar(p, cod)
                        assert rx.desempaquetar(marco) == p, (clase, nom, hc)
                        out += len(marco)
                fila[f"{'rohc+' if hc else ''}{nom}"] = _pct(crudo, out)
        filas.append(fila)
    return filas


def flujo_mezclado(m, mezcla):
    crudo = sum(len(p) for _, _, p in mezcla)
    res = []

    def correr(nombre, elegir, reinicio=0):
        tx, rx = C.Compresor(m, reinicio_flujo=reinicio), C.Descompresor(m)
        out, cpu, usos, intentados = 0, 0.0, {}, 0
        usos_clase: dict[str, dict[str, int]] = {}
        elegidos_clase: dict[str, dict[str, int]] = {}
        for clase, _, p in mezcla:
            t0 = time.perf_counter()
            if isinstance(elegir, EmisorAdaptativo):
                marco, cod = elegir.enviar(p)
                ne = C.CODECS[elegir.ultimo_elegido]
                ec = elegidos_clase.setdefault(clase, {})
                ec[ne] = ec.get(ne, 0) + 1
            elif elegir == "crudo":
                marco, cod = p, -1
            else:
                marco, cod = tx.empaquetar(p, elegir)
            cpu += time.perf_counter() - t0
            # Bytes que pasaron por un compresor (métrica de CPU que no depende de la máquina).
            if isinstance(elegir, EmisorAdaptativo):
                intentados += len(p) * (elegir.ultimo_elegido != C.NINGUNO)
            elif elegir not in ("crudo", C.NINGUNO):
                intentados += len(p)
            if cod >= 0:
                assert rx.desempaquetar(marco) == p, nombre
            out += len(marco)
            nom = C.CODECS.get(cod, "crudo")
            usos[nom] = usos.get(nom, 0) + 1
            uc = usos_clase.setdefault(clase, {})
            uc[nom] = uc.get(nom, 0) + 1
        res.append({"politica": nombre, "bytes": out, "razon": round(crudo / out, 4),
                    "ahorro_pct": _pct(crudo, out), "cpu_emisor_ms": round(cpu * 1000, 1),
                    "bytes_comprimidos_por_byte": round(intentados / crudo, 3),
                    "cpu_us_por_paquete": round(cpu * 1e6 / len(mezcla), 1), "usos": usos,
                    "usos_por_clase": usos_clase, "elegidos_por_clase": elegidos_clase})

    correr("sin compresión (crudo)", "crudo")
    for cod, nom in C.CODECS.items():
        correr(f"ROHC + {nom}", cod)
    correr("ROHC + deflate_flujo, reinicio cada 16", C.DEFLATE_FLUJO, reinicio=16)
    correr("ROHC + bandido UCB, 6 brazos, γ=0,99 (como el Lab 3)",
           EmisorAdaptativo(m, reinicio_flujo=16, gamma=0.99, brazos=tuple(C.CODECS)))
    correr("ROHC + bandido UCB, configuración de red, sin reinicio", EmisorAdaptativo(m, reinicio_flujo=0))
    correr("ROHC + bandido UCB, configuración de red", EmisorAdaptativo(m, reinicio_flujo=16))
    return {"paquetes": len(mezcla), "bytes_crudos": crudo, "politicas": res}


def latencias(m, conj):
    """µs por paquete (mediana) de comprimir y descomprimir, por códec y clase. En las clases
    cifradas/comprimidas casi todo cae al respaldo, así que se mide el costo de intentarlo."""
    out = []
    for cod, nom in C.CODECS.items():
        fila = {"codec": nom}
        for clase, fs in conj.items():
            tx, rx = C.Compresor(m), C.Descompresor(m)
            te, td = [], []
            for p in fs[0][:80]:
                t0 = time.perf_counter(); marco, _ = tx.empaquetar(p, cod); t1 = time.perf_counter()
                rx.desempaquetar(marco); t2 = time.perf_counter()
                te.append(t1 - t0); td.append(t2 - t1)
            fila[clase] = {"comprimir_us": round(statistics.median(te) * 1e6, 1),
                           "descomprimir_us": round(statistics.median(td) * 1e6, 1)}
        out.append(fila)
    return out


def perdidas(m, mezcla, p_perdida=0.01, semilla=7):
    """El receptor no ve el 1 % de los marcos. ¿Cuántos de los que SÍ llegan salen mal?"""
    rng = random.Random(semilla)
    caen = [rng.random() < p_perdida for _ in mezcla]
    res = []
    casos = [("ROHC + deflate_dict (sin estado)", C.DEFLATE_DICT, 0),
             ("ROHC + deflate_flujo (con estado, sin reinicio)", C.DEFLATE_FLUJO, 0),
             ("ROHC + deflate_flujo (reinicio cada 16 paq.)", C.DEFLATE_FLUJO, 16),
             ("ROHC + ninguno", C.NINGUNO, 0)]
    casos += [("ROHC + bandido, config. de red sin reinicio", "bandido", 0),
              ("ROHC + bandido, config. de red (reinicio cada 16 paq.)", "bandido", 16)]
    for nombre, cod, reinicio in casos:
        tx, rx = C.Compresor(m, reinicio_flujo=reinicio), C.Descompresor(m)
        if cod == "bandido":
            em = EmisorAdaptativo(m, reinicio_flujo=reinicio)
        ok = mal = 0
        out = crudo = 0
        for (_, _, p), cae in zip(mezcla, caen):
            marco, _ = em.enviar(p) if cod == "bandido" else tx.empaquetar(p, cod)
            out += len(marco); crudo += len(p)
            if cae:
                continue
            try:
                bien = rx.desempaquetar(marco) == p
            except Exception:
                bien = False
            ok += bien; mal += not bien
        res.append({"caso": nombre, "recibidos": ok + mal, "mal_reconstruidos": mal,
                    "pct_mal": round(100 * mal / (ok + mal), 2), "ahorro_pct": _pct(crudo, out)})
    return {"p_perdida": p_perdida, "perdidos": sum(caen), "casos": res}


def ajustar():
    """Rejilla de parámetros del bandido sobre el conjunto de ENTRENAMIENTO."""
    m = C.Modelo.cargar(MODELO) if MODELO.exists() else entrenar()
    mezcla = T.intercalar(T.conjunto(1, P_FLUJO, F_CLASE, prueba=False), 3)
    crudo = sum(len(p) for *_, p in mezcla)
    filas = []
    for nb, br in (("6 (todos)", tuple(C.CODECS)), ("3 (ninguno, dicc., flujo)", (0, 3, 4))):
        for g in (0.99, 0.995, 0.999):
            for c in (0.02, 0.05):
                em = EmisorAdaptativo(m, reinicio_flujo=16, gamma=g, c=c, brazos=br)
                t0 = time.perf_counter()
                out = sum(len(em.enviar(p)[0]) for *_, p in mezcla)
                filas.append({"brazos": nb, "gamma": g, "c": c, "ahorro_pct": _pct(crudo, out),
                              "cpu_us_por_paquete": round((time.perf_counter() - t0) * 1e6 / len(mezcla), 1),
                              "elegidos_ninguno": em.elegidos.get(0, 0)})
                print(filas[-1])
    md = ("# Ajuste del bandido (conjunto de ENTRENAMIENTO, semilla 1)\n\nCriterio: mayor ahorro; entre "
          "los que quedan a menos de 0,1 puntos del mejor, el de menos CPU.\n\n")
    md += tabla_md(filas, ["brazos", "gamma", "c", "ahorro_pct", "cpu_us_por_paquete", "elegidos_ninguno"],
                   ["brazos", "γ", "c", "ahorro %", "CPU µs/paq", "veces que eligió ninguno (ideal 2400)"])
    mejor = max(f["ahorro_pct"] for f in filas)
    elegido = min((f for f in filas if f["ahorro_pct"] >= mejor - 0.1), key=lambda f: f["cpu_us_por_paquete"])
    md += f"\nElegido: brazos {elegido['brazos']}, γ = {elegido['gamma']}, c = {elegido['c']}.\n"
    SALIDA.mkdir(exist_ok=True)
    (SALIDA / "ajuste.md").write_text(md, encoding="utf-8")
    print(md)


def tabla_md(filas, cols, cab=None):
    cab = cab or cols
    s = "| " + " | ".join(cab) + " |\n|" + "---|" * len(cols) + "\n"
    for f in filas:
        s += "| " + " | ".join(str(f[c]) for c in cols) + " |\n"
    return s


def comparar():
    m = C.Modelo.cargar(MODELO) if MODELO.exists() else entrenar()
    conj = T.conjunto(2, P_FLUJO, F_CLASE, prueba=True)
    mezcla = T.intercalar(conj, 3)
    print("1/4 por clase..."); pc = por_clase(m, conj)
    print("2/4 flujo mezclado..."); fm = flujo_mezclado(m, mezcla)
    print("3/4 latencias..."); la = latencias(m, conj)
    print("4/4 pérdidas..."); pe = perdidas(m, mezcla)
    SALIDA.mkdir(exist_ok=True)
    (SALIDA / "resultados.json").write_text(json.dumps(
        {"por_clase": pc, "flujo_mezclado": fm, "latencias": la, "perdidas": pe}, indent=1, ensure_ascii=False),
        encoding="utf-8")

    md = "# Resultados (generado por `evaluar.py comparar`)\n\n"
    md += "## 1. Ahorro por clase de tráfico (% de bytes)\n\n"
    cols = ["clase", "slice", "largo_medio", "rohc+ninguno"] + [f"rohc+{n}" for n in C.CODECS.values() if n != "ninguno"] \
        + ["tic_por_paquete", "deflate_flujo"]
    md += tabla_md(pc, cols, ["clase", "slice", "B/paq", "solo ROHC", "ROHC+Huffman estático", "ROHC+DEFLATE",
                              "ROHC+DEFLATE dicc.", "ROHC+DEFLATE flujo", "ROHC+TIC por paquete",
                              "TIC por paquete (sin ROHC)", "DEFLATE flujo (sin ROHC)"])
    md += f"\n## 2. Flujo mezclado ({fm['paquetes']} paquetes, {fm['bytes_crudos']:,} B)\n\n"
    md += tabla_md(fm["politicas"], ["politica", "bytes", "razon", "ahorro_pct", "bytes_comprimidos_por_byte",
                                     "cpu_us_por_paquete"],
                   ["política", "bytes enviados", "razón", "ahorro %", "bytes que pasan por un compresor / byte",
                    "CPU emisor µs/paq (ruidoso)"])
    b = next(r for r in fm["politicas"] if r["politica"].endswith("configuración de red"))
    md += ("\nQué ELIGIÓ el bandido (configuración de red) en cada clase, antes del respaldo. En las "
           "clases cifradas o ya comprimidas, todo lo que no es \"ninguno\" es exploración:\n\n| clase | "
           + " | ".join(C.CODECS.values()) + " |\n|" + "---|" * (len(C.CODECS) + 1) + "\n")
    for clase, u in b["elegidos_por_clase"].items():
        md += f"| {clase} | " + " | ".join(str(u.get(n, 0)) for n in C.CODECS.values()) + " |\n"
    md += "\n## 3. Latencia por paquete en µs (mediana, Python puro; comprimir / descomprimir)\n\n| códec | " + " | ".join(
        f"{k} comp/desc" for k in la[0] if k != "codec") + " |\n|" + "---|" * len(la[0]) + "\n"
    for f in la:
        md += f"| {f['codec']} | " + " | ".join(f"{v['comprimir_us']} / {v['descomprimir_us']}"
                                                for k, v in f.items() if k != "codec") + " |\n"
    md += f"\n## 4. Pérdida de {pe['p_perdida']*100:.0f} % de los paquetes ({pe['perdidos']} perdidos)\n\n"
    md += tabla_md(pe["casos"], ["caso", "recibidos", "mal_reconstruidos", "pct_mal", "ahorro_pct"],
                   ["caso", "recibidos", "mal reconstruidos", "% mal", "ahorro %"])
    (SALIDA / "resultados.md").write_text(md, encoding="utf-8")
    print(md)


def balance():
    """Latencia neta de cada algoritmo según velocidad y carga del enlace (ver balance.py)."""
    import balance as B
    m = C.Modelo.cargar(MODELO) if MODELO.exists() else entrenar()
    mezcla = T.intercalar(T.conjunto(2, P_FLUJO, F_CLASE, prueba=True), 3)
    r = B.evaluar(m, mezcla)
    SALIDA.mkdir(exist_ok=True)
    (SALIDA / "balance.json").write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
    md = B.markdown(r)
    (SALIDA / "balance.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("accion", choices=["entrenar", "ajustar", "comparar", "balance"])
    a = ap.parse_args()
    {"entrenar": entrenar, "ajustar": ajustar, "comparar": comparar, "balance": balance}[a.accion]()
