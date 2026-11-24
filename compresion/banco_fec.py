"""Banco offline de códigos de canal: cuánto reduce cada código el BER.

  python compresion/banco_fec.py [--paquetes N] [--largo B] [--semilla S] [--sin-archivos]

Compara sin protección, repetición 5/1, Hamming (7,4), Hamming (15,11), SECDED (8,4) y SECDED (16,11).
Para cada código y cada probabilidad del canal BSC genera N cargas aleatorias de B bytes, las
codifica, voltea bits del marco (nunca el byte del id), decodifica y compara con el original.
Después repite con un canal de ráfaga (Gilbert-Elliott) para ver el efecto de los errores
agrupados. Al final mide en bytes el marco de cada código para cargas típicas (tamano_en_cable).

Es determinista: la misma semilla da las mismas cargas y los mismos errores para todos los códigos.
Solo biblioteca estándar (más los módulos del propio proyecto: canal, fec, registro_fec).
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import canal
import fec
import registro_fec

DIR = Path(__file__).resolve().parent
SALIDA = DIR / "resultados" / "fec.md"

P_BARRIDO = [1e-4, 3e-4, 1e-3, 3e-3, 1e-2, 3e-2, 1e-1]
P_RAFAGA = (0.3, 0.01, 0.2)  # p_malo, p_bueno_a_malo, p_malo_a_bueno
PAQUETES, LARGO, SEMILLA = 300, 200, 1
CODIGOS = ("sin_codigo", "repeticion_5_1", "hamming_7_4", "hamming_15_11", "secded_8_4", "secded_16_11")
CODIGOS_FEC = CODIGOS[1:]
ETIQUETAS = {"sin_codigo": "sin código", "repeticion_5_1": "repetición 5/1 (5×)",
             "hamming_7_4": "Hamming 7/4 (1,75×)", "hamming_15_11": "Hamming 15/11 (1,36×)",
             "secded_8_4": "SECDED 8/4 (2×)", "secded_16_11": "SECDED 16/11 (1,45×)"}
CATEGORIAS = ("limpios", "corregidos_bien", "corregidos_mal", "no_corregibles", "invalidos")
DESDE_DATOS = 8  # bits del id de código: nunca se voltean
CARGAS_CABLE = (40, 200, 780, 1400)  # bytes comprimidos que entran a la FEC
IP_UDP_TUNEL = 28  # IP + UDP del túnel: tamano_en_cable no los cuenta, se suman aparte


def palabras_de(largo: int, k: int) -> int:
    """Nº de palabras código de una carga de `largo` bytes (cabecera de 4 B incluida)."""
    return -(-(32 + 8 * largo) // k)


def p_fallo(n: int, p: float) -> float:
    """P(una palabra de n bits recibe 2 o más errores) con errores independientes de prob. p."""
    return 1 - (1 - p) ** n - n * p * (1 - p) ** (n - 1)


def _bits_distintos(a: bytes, b: bytes) -> int:
    if len(a) != len(b):
        return 8 * len(b)
    return bin(int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).count("1")


def clasificar_paquete(datos: bytes, salida: bytes | None, res: fec.Resultado | None = None,
                       invalido: bool = False) -> str:
    """Una categoría por paquete, en este orden de prioridad."""
    if invalido:
        return "invalidos"
    if res is not None and res.no_corregibles:
        return "no_corregibles"
    if salida != datos:
        return "corregidos_mal"   # datos distintos sin aviso (sin código: datos dañados)
    if res is not None and res.sindromes_no_cero:
        return "corregidos_bien"
    return "limpios"


def _medir(codigo: str, cargas: list[bytes], posiciones) -> dict:
    """`posiciones(nbits, desde_bit, i)` devuelve los bits volteados del paquete i."""
    cod = None if codigo == "sin_codigo" else fec.buscar(codigo)
    n, k = (1, 1) if cod is None else (cod.n, cod.k)
    desde = 0 if cod is None else DESDE_DATOS
    cuenta = {c: 0 for c in CATEGORIAS}
    flips = bits_entrada = errores = bits_datos = palabras = palabras_mal = 0
    for i, datos in enumerate(cargas):
        marco = datos if cod is None else cod.codificar(datos, i & 0xFFFF)
        nbits = 8 * len(marco)
        pos = posiciones(nbits, desde, i)
        flips += len(pos)
        bits_entrada += nbits - desde
        bits_datos += 8 * len(datos)
        recibido = canal.inyectar(marco, pos)
        invalido, res, salida = False, None, recibido
        if cod is not None:
            nw = palabras_de(len(datos), cod.k)
            por_palabra = Counter((q - desde) // cod.n for q in pos)
            palabras += nw
            palabras_mal += sum(1 for w, c in por_palabra.items() if c >= 2 and w < nw)
            try:
                salida, res = cod.decodificar(recibido)
            except ValueError:
                invalido, salida = True, None
        errores += 8 * len(datos) if invalido else _bits_distintos(salida, datos)
        cuenta[clasificar_paquete(datos, salida, res, invalido)] += 1
    return {"codigo": codigo, "n": n, "k": k, "paquetes": len(cargas), "flips": flips,
            "ber_entrada": flips / bits_entrada if bits_entrada else 0.0,
            "ber_residual": errores / bits_datos if bits_datos else 0.0,
            **cuenta, "palabras": palabras, "palabras_mal": palabras_mal}


def _fila(medida: dict, modelo: str, p_txt: str, p, teorica) -> dict:
    f = dict(medida, modelo=modelo, p=p, p_txt=p_txt, p_teorica=teorica)
    f["p_observada"] = f["palabras_mal"] / f["palabras"] if f["palabras"] else None
    return f


def tabla_cable(cargas=CARGAS_CABLE, codigos=CODIGOS_FEC) -> list[dict]:
    """Bytes del marco de cada código para cada carga (medido codificando de verdad y comparado con
    registro_fec.tamano_en_cable). El cable suma los 28 B de IP+UDP del túnel."""
    rng = random.Random(7)
    filas = []
    for largo in cargas:
        datos = rng.randbytes(largo)
        for cod in codigos:
            c = fec.buscar(cod)
            marco = registro_fec.tamano_en_cable(cod, largo)
            real = len(c.codificar(datos, 0))
            filas.append({"codigo": cod, "n": c.n, "k": c.k, "carga": largo, "marco": marco,
                          "marco_real": real, "cable": marco + IP_UDP_TUNEL,
                          "f_marco": marco / largo, "f_cable": (marco + IP_UDP_TUNEL) / largo,
                          "mtu": registro_fec.mtu_con_fec(cod)})
    return filas


def correr(paquetes: int = PAQUETES, largo: int = LARGO, semilla: int = SEMILLA,
           ps=P_BARRIDO, rafaga=P_RAFAGA) -> dict:
    """Barrido BSC (filas en `barrido`), canal de ráfaga (`rafaga`) y bytes en el aire (`cable`)."""
    rng = random.Random(semilla)
    cargas = [rng.randbytes(largo) for _ in range(paquetes)]
    barrido = []
    for ip, p in enumerate(ps):
        for cod in CODIGOS:
            def pos(nbits, desde, i, p=p, ip=ip):
                return canal.bsc(nbits, p, semilla * 1_000_003 + ip * 10_007 + i, desde)
            m = _medir(cod, cargas, pos)
            teorica = None if cod == "sin_codigo" else p_fallo(m["n"], p)
            barrido.append(_fila(m, "bsc", f"{p:g}", p, teorica))
    pm, pbm, pmb = rafaga
    txt = f"ráfaga ({pm:g}, {pbm:g}, {pmb:g})"
    rafagas = []
    for cod in CODIGOS:
        def pos(nbits, desde, i):
            return canal.rafaga(nbits, 0.0, pm, pbm, pmb, semilla=semilla * 1_000_003 + 999 * 10_007 + i,
                                desde_bit=desde)
        rafagas.append(_fila(_medir(cod, cargas, pos), "rafaga", txt, None, None))
    return {"parametros": {"paquetes": paquetes, "largo": largo, "semilla": semilla,
                           "ps": list(ps), "rafaga": list(rafaga)},
            "barrido": barrido, "rafaga": rafagas, "cable": tabla_cable()}


def _fmt(x) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return "0" if x == 0 else f"{x:.2e}"
    return str(x)


def _tabla(cab: list[str], filas: list[list[str]]) -> str:
    s = "| " + " | ".join(cab) + " |\n|" + "---|" * len(cab) + "\n"
    for f in filas:
        s += "| " + " | ".join(f) + " |\n"
    return s


def _fila_detalle(f: dict) -> list[str]:
    return [f["codigo"], f"{f['n']}/{f['k']}", f["p_txt"], _fmt(f["ber_entrada"]), _fmt(f["ber_residual"]),
            str(f["limpios"]), str(f["corregidos_bien"]), str(f["corregidos_mal"]), str(f["no_corregibles"]),
            str(f["invalidos"]), f"{_fmt(f['p_teorica'])} / {_fmt(f['p_observada'])}"]


def _pivote(filas: list[dict]) -> str:
    grupos: dict[str, dict[str, dict]] = {}
    for f in filas:
        grupos.setdefault(f["p_txt"], {})[f["codigo"]] = f
    cab = ["canal", "BER entrada", *(ETIQUETAS[c] for c in CODIGOS)]
    rows = []
    for txt, g in grupos.items():
        ref = g.get("sin_codigo")
        rows.append([txt, _fmt(ref["ber_entrada"]) if ref else "—"]
                    + [_fmt(g[c]["ber_residual"]) if c in g else "—" for c in CODIGOS])
    return _tabla(cab, rows)


def _tabla_cable(filas: list[dict]) -> str:
    cab = ["carga (B)", "código", "n/k", "marco FEC (B)", "cable con IP+UDP (B)", "factor marco/carga",
           "factor cable/carga", "MTU de cmp0 (B)"]
    rows = [[str(f["carga"]), f["codigo"], f"{f['n']}/{f['k']}", str(f["marco"]), str(f["cable"]),
             f"{f['f_marco']:.3f}", f"{f['f_cable']:.3f}", str(f["mtu"])] for f in filas]
    return _tabla(cab, rows)


def _fila_de(filas: list[dict], codigo: str, p: float | None) -> dict | None:
    """Fila del barrido de ese código con p más cercana (en escala logarítmica)."""
    cand = [f for f in filas if f["codigo"] == codigo and f["p"] is not None]
    if not cand or not p or p <= 0:
        return None
    return min(cand, key=lambda f: abs(math.log10(f["p"]) - math.log10(p)))


def _cruce(filas: list[dict], codigo: str) -> str:
    """Primer punto del barrido (p creciente) donde el BER residual ya no baja del de entrada."""
    for f in sorted((f for f in filas if f["codigo"] == codigo), key=lambda f: f["p"]):
        if f["ber_residual"] >= f["ber_entrada"]:
            return f"p={f['p_txt']}"
    return "no cruza en el barrido"


def _nota(datos: dict) -> str:
    b = datos["barrido"]
    rs = {f["codigo"]: f for f in datos["rafaga"]}
    t = {c: _fila_de(b, c, 1e-3) for c in CODIGOS}
    al = {c: _fila_de(b, c, 3e-2) for c in CODIGOS}
    cr = {c: _fila_de(b, c, 3e-3) for c in CODIGOS}
    if None in (*t.values(), *al.values(), *cr.values()) or len(rs) < len(CODIGOS):
        return ""
    residuales = ", ".join(f"{ETIQUETAS[c]} {_fmt(t[c]['ber_residual'])}" for c in CODIGOS_FEC)
    cruces = ", ".join(f"{ETIQUETAS[c]} {_cruce(b, c)}" for c in CODIGOS_FEC)
    ref = {c: _fila_de(b, c, rs[c]["ber_entrada"]) for c in CODIGOS}
    lineas = [
        f"- Sin código, el BER residual es el de entrada ({_fmt(t['sin_codigo']['ber_entrada'])} a p=1e-3). "
        f"BER residual a p=1e-3: {residuales}.",
        f"- Dónde cruza cada código (primer punto del barrido con BER residual ≥ BER entrada): {cruces}.",
        f"- Hamming 7/4 y 15/11 con 2 errores en una palabra: el síndrome no es cero y es una columna de H, "
        f"así que se cuenta como «corregido» y se voltea un tercer bit. A p=3e-2 hay "
        f"{al['hamming_7_4']['corregidos_mal']} paquetes corregidos mal de {al['hamming_7_4']['paquetes']} "
        f"con 7/4 y {al['hamming_15_11']['corregidos_mal']} con 15/11, sin ningún aviso.",
        f"- SECDED 8/4 y 16/11 dejan esos casos como «no corregibles» (síndrome de peso par): a p=3e-2 marcan "
        f"{al['secded_8_4']['no_corregibles']} y {al['secded_16_11']['no_corregibles']} paquetes como detectados, "
        f"y solo {al['secded_8_4']['corregidos_mal']} y {al['secded_16_11']['corregidos_mal']} salen mal sin aviso.",
        f"- 15/11 y 16/11 cuestan menos banda (1,36× y 1,45× frente a 1,75× y 2×), pero no son mejores en todo: "
        f"a p=1e-3 dejan {_fmt(t['hamming_15_11']['ber_residual'])} y {_fmt(t['secded_16_11']['ber_residual'])} "
        f"de BER residual (7/4 deja {_fmt(t['hamming_7_4']['ber_residual'])} y 8/4 {_fmt(t['secded_8_4']['ber_residual'])}), "
        f"y con ruido alto sufren más palabras con 2 o más errores: a p=3e-2 la fracción observada de palabras "
        f"fallidas es {_fmt(al['hamming_15_11']['p_observada'])} (15/11) frente a {_fmt(al['hamming_7_4']['p_observada'])} (7/4), "
        f"y {_fmt(al['secded_16_11']['p_observada'])} (16/11) frente a {_fmt(al['secded_8_4']['p_observada'])} (8/4).",
        f"- Ráfaga (BER de entrada medio {_fmt(rs['hamming_7_4']['ber_entrada'])}): BER residual "
        + ", ".join(f"{ETIQUETAS[c]} {_fmt(rs[c]['ber_residual'])}" for c in CODIGOS_FEC)
        + ". Hamming 7/4 falla en "
        f"{_fmt(rs['hamming_7_4']['p_observada'])} de sus palabras frente a {_fmt(ref['hamming_7_4']['p_observada'])} "
        f"con errores independientes al BER más cercano (p={ref['hamming_7_4']['p']:g}).",
    ]
    return "\n".join(lineas) + "\n"


def a_markdown(datos: dict) -> str:
    par = datos["parametros"]
    md = ("# Banco FEC: cuánto reduce el BER cada código (generado por `compresion/banco_fec.py`)\n\n"
          f"{par['paquetes']} cargas aleatorias de {par['largo']} B por código y punto, semilla {par['semilla']}. "
          "Canal BSC independiente por bit (barrido) y Gilbert-Elliott (ráfaga). El byte del id de código "
          "nunca se voltea.\n\n"
          "- BER entrada = bits volteados / bits del marco sin el id.\n"
          "- BER residual = bits de datos distintos tras decodificar / bits de datos. Un paquete inválido "
          "cuenta todos sus bits de datos como erróneos.\n"
          "- Categorías (cada paquete cae en una sola, en este orden): inválido (el decodificador lanza "
          "ValueError); no corregible (algún síndrome sin columna: el decodificador lo avisa); corregido mal "
          "(datos distintos sin aviso); corregido bien (síndrome distinto de cero y datos idénticos); limpio "
          "(sin síndrome). En «sin código», «corregido mal» son los paquetes con datos dañados.\n"
          "- p_palabra_fallida = P(una palabra código recibe 2 o más errores). Teórica = "
          "1-(1-p)^n-np(1-p)^(n-1) con errores independientes; observada = palabras con ≥2 errores / palabras "
          "del barrido. Con ráfaga no hay teórica (los errores no son independientes).\n\n")
    md += "## 1. Curva BER residual (canal BSC)\n\n" + _pivote(datos["barrido"]) + "\n"
    md += "## 2. Detalle por código y punto\n\n"
    cab = ["código", "n/k", "p", "BER entrada", "BER residual", "limpios", "corregidos bien", "corregidos mal",
           "no corregibles", "inválidos", "p_palabra_fallida teórica / observada"]
    md += _tabla(cab, [_fila_detalle(f) for f in datos["barrido"]]) + "\n"
    md += "## 3. Canal de ráfaga (errores agrupados)\n\n"
    md += _tabla(cab, [_fila_detalle(f) for f in datos["rafaga"]]) + "\n"
    md += "## 4. Bytes en el aire por código\n\n"
    md += ("Medido codificando cargas de cada tamaño y comparando con `registro_fec.tamano_en_cable` "
           "(incluye el id de FEC y los 4 B de secuencia y largo). «Cable» suma además los 28 B de IP+UDP del "
           "túnel, que la función no cuenta. El MTU de cmp0 es `registro_fec.mtu_con_fec`; una carga mayor "
           "que ese MTU no la produce cmp0 con ese código y se muestra solo para medir el factor.\n\n")
    md += _tabla_cable(datos["cable"]) + "\n"
    md += ("El factor tiende a n/k para cargas grandes y se aleja en las chicas por el overhead fijo "
           "(1 B de id, 4 B de cabecera, 28 B de túnel y el relleno hasta el byte).\n\n")
    md += "## Nota de lectura\n\n" + _nota(datos)
    return md


def escribir(md: str, ruta: Path = SALIDA) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(md, encoding="utf-8")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Banco offline: BER residual por código FEC")
    ap.add_argument("--paquetes", type=int, default=PAQUETES)
    ap.add_argument("--largo", type=int, default=LARGO, help="bytes de carga por paquete")
    ap.add_argument("--semilla", type=int, default=SEMILLA)
    ap.add_argument("--sin-archivos", action="store_true", help="no escribir resultados/fec.md")
    a = ap.parse_args(argv)
    md = a_markdown(correr(paquetes=a.paquetes, largo=a.largo, semilla=a.semilla))
    if not a.sin_archivos:
        escribir(md)
        print(f"escrito: {SALIDA}", file=sys.stderr)
    print(md)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    main()
