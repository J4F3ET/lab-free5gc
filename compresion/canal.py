"""Canal manual mínimo: voltea bits elegidos (r = c + e). Sin BSC ni ráfagas (fuera de alcance)."""
from __future__ import annotations

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
