"""Códigos de bloque lineales (n,k) módulo 2, con la notación de clase.

  mensaje m (k bits) ; paridad b = m·P  (P es k×(n-k)) ; G = [P : I_k] (k×n)
  palabra código c = m·G = [b : m]  (sistemático, paridad primero)
  H = [I_{n-k} : Pᵀ] ((n-k)×n) ; H·Gᵀ = 0 (mod 2)
  recibido r = c + e ; síndrome s = r·Hᵀ = e·Hᵀ
  s = 0 -> sin error ; s ≠ 0 -> error ; si es de un solo bit en la posición i,
  s es la columna i de H y se corrige volteando el bit i.

Hamming (7,4) tiene d_min = 3: detecta 2 errores y corrige 1. Con 2 errores el decodificador
de 1 bit SÍ ve s ≠ 0 pero corrige MAL (voltea un tercer bit y entrega otra palabra código);
queda contado como "corregido", no se oculta: ver test_fec.py.

Marco en bytes:  [1 B id de código][palabras código empaquetadas MSB-first, relleno de ceros]
Lo codificado es  [2 B secuencia !H][2 B largo de datos !H][datos].
El id de código viaja SIN proteger: un error ahí hace fallar la decodificación (ValueError
o basura); proteger ese byte queda para una entrega posterior.
"""
from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

MAX_POSICIONES = 64  # tope de posiciones_corregidas guardadas en Resultado


@dataclass
class PalabraResultado:
    r: list[int]            # palabra recibida
    c_corregida: list[int]  # palabra tras la corrección (igual a r si no se corrigió)
    m: list[int]            # mensaje decodificado (últimos k bits de c_corregida)
    sindrome: tuple[int, ...]
    posicion: int | None    # índice 0-based del bit volteado; None si no corrigió
    estado: str             # "limpio" | "corregido" | "no_corregible"


@dataclass
class Resultado:
    secuencia: int
    largo: int
    palabras: int
    sindromes_no_cero: int
    corregidas: int
    no_corregibles: int
    sindromes: dict[str, int] = field(default_factory=dict)  # "011" -> conteo (solo s ≠ 0)
    posiciones_corregidas: list[tuple[int, int]] = field(default_factory=list)  # (palabra, pos)
    bits_codificados: int = 0  # palabras * n (sin id ni relleno)


class CodigoCanal(Protocol):
    """Interfaz mínima de un código de canal sobre bytes.

    Siguiente entrega: SECDED, CRC y Reed-Solomon implementan esto sin tocar el resto.
    """
    nombre: str
    n: int
    k: int

    def codificar(self, datos: bytes, secuencia: int = 0) -> bytes: ...
    def decodificar(self, marco: bytes) -> tuple[bytes, Resultado]: ...


INTERFAZ = CodigoCanal


def _bits_a_texto(bits) -> str:
    return "".join("1" if b else "0" for b in bits)


def _a_bytes(texto: str) -> bytes:
    """Cadena '0'/'1' -> bytes, relleno de ceros al final."""
    if not texto:
        return b""
    texto += "0" * (-len(texto) % 8)
    return int(texto, 2).to_bytes(len(texto) // 8, "big")


def _de_bytes(datos: bytes) -> str:
    return format(int.from_bytes(datos, "big"), f"0{8 * len(datos)}b") if datos else ""


class CodigoLineal:
    """Código de bloque lineal sistemático (n,k) definido por P (k×(n-k))."""

    def __init__(self, nombre: str, P: list[list[int]], id_codigo: int | None = None):
        if not P or any(len(f) != len(P[0]) for f in P):
            raise ValueError("P debe ser una matriz k×(n-k) no vacía")
        if any(x not in (0, 1) for f in P for x in f):
            raise ValueError("P solo admite 0/1")
        self.nombre = nombre
        self.id = id_codigo
        self.P = [list(f) for f in P]
        self.k = len(P)
        self.nk = len(P[0])
        self.n = self.k + self.nk
        # G = [P : I_k]
        self.G = [self.P[i] + [1 if j == i else 0 for j in range(self.k)] for i in range(self.k)]
        # H = [I_{n-k} : Pᵀ]
        self.H = [[1 if j == i else 0 for j in range(self.nk)] + [self.P[t][i] for t in range(self.k)]
                  for i in range(self.nk)]
        for h in self.H:
            for g in self.G:
                if sum(a * b for a, b in zip(h, g)) % 2:
                    raise ValueError("H·Gᵀ ≠ 0 (mod 2)")
        self._columnas = [tuple(self.H[f][c] for f in range(self.nk)) for c in range(self.n)]
        self._pos_de_sindrome = {col: i for i, col in enumerate(self._columnas)}
        self._tab = None

    # --- métodos didácticos sobre listas de bits ---
    def codificar_palabra(self, m: list[int]) -> list[int]:
        """c = m·G = [b : m]."""
        if len(m) != self.k:
            raise ValueError(f"m debe tener {self.k} bits")
        b = [sum(m[i] * self.P[i][j] for i in range(self.k)) % 2 for j in range(self.nk)]
        return b + list(m)

    def sindrome(self, r: list[int]) -> tuple[int, ...]:
        """s = r·Hᵀ."""
        if len(r) != self.n:
            raise ValueError(f"r debe tener {self.n} bits")
        return tuple(sum(a * b for a, b in zip(fila, r)) % 2 for fila in self.H)

    def posicion_error(self, s: tuple[int, ...]) -> int | None:
        """Índice 0-based de la columna de H igual a s; None si s=0 o no coincide con ninguna."""
        if not any(s):
            return None
        return self._pos_de_sindrome.get(tuple(s))

    def corregir_palabra(self, r: list[int]) -> PalabraResultado:
        s = self.sindrome(r)
        if not any(s):
            return PalabraResultado(list(r), list(r), list(r[self.nk:]), s, None, "limpio")
        pos = self.posicion_error(s)
        if pos is None:
            return PalabraResultado(list(r), list(r), list(r[self.nk:]), s, None, "no_corregible")
        c = list(r)
        c[pos] ^= 1
        return PalabraResultado(list(r), c, c[self.nk:], s, pos, "corregido")

    # --- tablas para la API rápida ---
    def _tablas(self):
        if self._tab is None:
            k, n = self.k, self.n
            enc = {}
            for v in range(1 << k):
                m = [(v >> (k - 1 - i)) & 1 for i in range(k)]
                enc[_bits_a_texto(m)] = _bits_a_texto(self.codificar_palabra(m))
            dec = {}  # palabra(str) -> (mensaje str, estado, síndrome str, posición)
            for v in range(1 << n):
                r = [(v >> (n - 1 - i)) & 1 for i in range(n)]
                p = self.corregir_palabra(r)
                dec[_bits_a_texto(r)] = (_bits_a_texto(p.m), p.estado, _bits_a_texto(p.sindrome), p.posicion)
            self._tab = (enc, {w: x[0] for w, x in dec.items()}, dec)
        return self._tab

    # --- API sobre bytes ---
    def codificar(self, datos: bytes, secuencia: int = 0) -> bytes:
        if self.id is None:
            raise ValueError(f"el código {self.nombre!r} no tiene id de registro")
        if not 0 <= secuencia <= 0xFFFF:
            raise ValueError("secuencia fuera de 0..65535")
        if len(datos) > 0xFFFF:
            raise ValueError("datos de más de 65535 bytes")
        enc = self._tablas()[0]
        bits = _de_bytes(struct.pack("!HH", secuencia, len(datos)) + bytes(datos))
        bits += "0" * (-len(bits) % self.k)
        k = self.k
        palabras = "".join(map(enc.__getitem__, [bits[i:i + k] for i in range(0, len(bits), k)]))
        return bytes([self.id]) + _a_bytes(palabras)

    def decodificar(self, marco: bytes) -> tuple[bytes, Resultado]:
        if not marco:
            raise ValueError("marco vacío")
        if marco[0] != self.id:
            raise ValueError(f"id {marco[0]} no corresponde a {self.nombre!r} (id {self.id})")
        n, k = self.n, self.k
        _, dec_m, dec = self._tablas()
        bits = _de_bytes(marco[1:])
        total = len(bits) // n
        if total * k < 32:
            raise ValueError("marco demasiado corto para la cabecera")
        todas = [bits[i:i + n] for i in range(0, total * n, n)]
        mensaje = "".join(map(dec_m.__getitem__, todas))
        secuencia, largo = int(mensaje[:16], 2), int(mensaje[16:32], 2)
        nbloques = -(-(32 + 8 * largo) // k)
        if nbloques > total or len(marco) != 1 + -(-(nbloques * n) // 8):
            raise ValueError("largo inconsistente con el tamaño del marco")
        palabras = todas[:nbloques]
        res = Resultado(secuencia, largo, nbloques, 0, 0, 0, bits_codificados=nbloques * n)
        corregibles = set()
        for w, cnt in Counter(palabras).items():
            _, estado, s, _pos = dec[w]
            if estado == "limpio":
                continue
            res.sindromes_no_cero += cnt
            res.sindromes[s] = res.sindromes.get(s, 0) + cnt
            if estado == "corregido":
                res.corregidas += cnt
                corregibles.add(w)
            else:
                res.no_corregibles += cnt
        if corregibles:
            for i, w in enumerate(palabras):
                if w in corregibles:
                    res.posiciones_corregidas.append((i, dec[w][3]))
                    if len(res.posiciones_corregidas) >= MAX_POSICIONES:
                        break
        datos = _a_bytes(mensaje[32:32 + 8 * largo])
        return datos, res


REPETICION_5_1 = CodigoLineal("repeticion_5_1", [[1, 1, 1, 1]], id_codigo=1)
HAMMING_7_4 = CodigoLineal("hamming_7_4", [[1, 1, 0], [0, 1, 1], [1, 1, 1], [1, 0, 1]], id_codigo=2)

CODIGOS: dict[int, CodigoLineal] = {c.id: c for c in (REPETICION_5_1, HAMMING_7_4)}


def buscar(codigo) -> CodigoLineal:
    """Acepta un CodigoLineal, su id (int) o su nombre (str)."""
    if isinstance(codigo, CodigoLineal):
        return codigo
    if isinstance(codigo, int) and codigo in CODIGOS:
        return CODIGOS[codigo]
    for c in CODIGOS.values():
        if c.nombre == codigo:
            return c
    raise ValueError(f"código desconocido: {codigo!r}")


def codificar(datos: bytes, codigo, secuencia: int = 0) -> bytes:
    return buscar(codigo).codificar(datos, secuencia)


def decodificar(marco: bytes) -> tuple[bytes, Resultado]:
    if not marco:
        raise ValueError("marco vacío")
    if marco[0] not in CODIGOS:
        raise ValueError(f"id de código desconocido: {marco[0]}")
    return CODIGOS[marco[0]].decodificar(marco)
