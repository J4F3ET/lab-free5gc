"""Canal: voltea bits (r = c + e). Manual (inyectar, elegir_posiciones) y aleatorio (bsc, rafaga).

Los aleatorios son deterministas con semilla (random.Random) y solo usan la biblioteca estándar.
  bsc:P                       canal binario simétrico: cada bit se voltea con probabilidad P.
  rafaga:P_MALO,PBM,PMB       Gilbert-Elliott de dos estados. Empieza en «bueno» (error 0); en «malo»
                              el error por bit es P_MALO; PBM = P(bueno→malo) por bit, PMB = P(malo→bueno).
"""
from __future__ import annotations

import math
import random
from typing import Iterable


def inyectar(datos: bytes, posiciones: Iterable[int]) -> bytes:
    """XOR de los bits en `posiciones` (índice 0 = bit más significativo del primer byte).

    No muta la entrada. Lanza IndexError si una posición cae fuera de rango.
    Una posición repetida se voltea dos veces (queda sin cambio), como e + e = 0 módulo 2.
    """
    salida = bytearray(datos)
    total = 8 * len(salida)
    for pos in posiciones:
        if not 0 <= pos < total:
            raise IndexError(f"posición de bit {pos} fuera de rango (0..{total - 1})")
        salida[pos >> 3] ^= 0x80 >> (pos & 7)
    return bytes(salida)


def elegir_posiciones(nbits_total: int, cantidad: int, semilla: int | None = None,
                      desde_bit: int = 0) -> list[int]:
    """Elige `cantidad` posiciones distintas en [desde_bit, nbits_total), ordenadas.

    `desde_bit=8` permite no tocar el primer byte (id del código, que viaja sin proteger).
    """
    if cantidad < 0 or desde_bit < 0:
        raise ValueError("cantidad y desde_bit deben ser >= 0")
    disponibles = nbits_total - desde_bit
    if cantidad > disponibles:
        raise ValueError(f"no hay {cantidad} posiciones distintas en [{desde_bit}, {nbits_total})")
    return sorted(random.Random(semilla).sample(range(desde_bit, nbits_total), cantidad))


def _validar_prob(nombre: str, valor) -> float:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not 0 <= valor <= 1:
        raise ValueError(f"{nombre} debe ser un número en [0, 1], no {valor!r}")
    return float(valor)


def bsc(nbits_total: int, p: float, semilla: int | None = None, desde_bit: int = 8) -> list[int]:
    """Posiciones volteadas por un canal binario simétrico en [desde_bit, nbits_total), ordenadas.

    Cada bit se voltea con probabilidad `p`, independiente. Usa saltos geométricos: el coste es
    proporcional al número de errores, no al de bits (800 bits con p=1e-3 es instantáneo).
    """
    p = _validar_prob("p", p)
    if desde_bit < 0:
        raise ValueError("desde_bit debe ser >= 0")
    if p == 0 or nbits_total <= desde_bit:
        return []
    if p == 1:
        return list(range(desde_bit, nbits_total))
    rng = random.Random(semilla)
    log_q = math.log1p(-p)              # log(1 - p), exacto también para p muy pequeña
    salida: list[int] = []
    i = desde_bit
    while True:
        # Aciertos (sin error) hasta el siguiente volteo: geométrica. u en (0, 1] para que log sea finito.
        salto = math.log(1.0 - rng.random()) / log_q
        if i + salto >= nbits_total:
            break
        i += int(salto)
        salida.append(i)
        i += 1
    return salida


def rafaga(nbits_total: int, p_bueno: float, p_malo: float, p_bueno_a_malo: float,
           p_malo_a_bueno: float, semilla: int | None = None, desde_bit: int = 8) -> list[int]:
    """Posiciones volteadas por un modelo Gilbert-Elliott en [desde_bit, nbits_total), ordenadas.

    Dos estados. Por bit: primero se decide el error con la probabilidad del estado actual
    (p_bueno o p_malo) y después se transiciona (bueno→malo con p_bueno_a_malo, malo→bueno con
    p_malo_a_bueno). Empieza en «bueno».
    """
    p_bueno = _validar_prob("p_bueno", p_bueno)
    p_malo = _validar_prob("p_malo", p_malo)
    a_malo = _validar_prob("p_bueno_a_malo", p_bueno_a_malo)
    a_bueno = _validar_prob("p_malo_a_bueno", p_malo_a_bueno)
    if desde_bit < 0:
        raise ValueError("desde_bit debe ser >= 0")
    rng = random.Random(semilla)
    salida: list[int] = []
    malo = False
    for i in range(desde_bit, nbits_total):
        if rng.random() < (p_malo if malo else p_bueno):
            salida.append(i)
        if rng.random() < (a_bueno if malo else a_malo):
            malo = not malo
    return salida


class Canal:
    """Canal aleatorio parseado de CANAL. `nombre` es el texto original; `posiciones` da los volteos."""

    def __init__(self, nombre: str, modelo: str, params: tuple[float, ...]):
        self.nombre = nombre
        self.modelo = modelo
        self.params = params

    def posiciones(self, nbits_total: int, semilla: int | None = None, desde_bit: int = 8) -> list[int]:
        if self.modelo == "bsc":
            return bsc(nbits_total, self.params[0], semilla, desde_bit)
        p_malo, pbm, pmb = self.params
        return rafaga(nbits_total, 0.0, p_malo, pbm, pmb, semilla, desde_bit)

    def __repr__(self) -> str:
        return f"Canal({self.nombre!r})"


_FORMATO_CANAL = ("usa bsc:P (p. ej. bsc:1e-3) o rafaga:P_MALO,P_BUENO_A_MALO,P_MALO_A_BUENO "
                  "(p. ej. rafaga:0.3,0.01,0.2), con probabilidades en [0, 1]; o vacío para apagarlo")


def parse_canal(texto: str | None) -> Canal | None:
    """Canal desde su texto. Vacío o None -> None (sin canal aleatorio). ValueError si no es válido."""
    if texto is None:
        return None
    if not isinstance(texto, str):
        raise ValueError(f"CANAL debe ser texto; {_FORMATO_CANAL}")
    limpio = texto.strip()
    if not limpio:
        return None
    modelo, sep, resto = limpio.partition(":")
    modelo = modelo.strip().lower()
    if not sep or modelo not in ("bsc", "rafaga"):
        raise ValueError(f"CANAL={texto!r} no es un canal válido; {_FORMATO_CANAL}")
    esperados = 1 if modelo == "bsc" else 3
    partes = resto.split(",")
    if len(partes) != esperados:
        raise ValueError(f"CANAL={texto!r}: el modelo {modelo} lleva {esperados} número(s); {_FORMATO_CANAL}")
    valores = []
    for parte in partes:
        try:
            v = float(parte)
        except ValueError:
            raise ValueError(f"CANAL={texto!r}: {parte.strip()!r} no es un número; {_FORMATO_CANAL}") from None
        if not 0 <= v <= 1:   # también rechaza nan
            raise ValueError(f"CANAL={texto!r}: probabilidad {parte.strip()} fuera de [0, 1]; {_FORMATO_CANAL}")
        valores.append(v)
    return Canal(limpio, modelo, tuple(valores))
