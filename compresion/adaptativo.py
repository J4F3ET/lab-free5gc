"""Adaptación del código FEC al BER del canal: estimador, política con histéresis e informe.

Módulo PURO: sin sockets ni hilos. Lo usa el receptor para medir el canal con lo que ve el
decodificador (síndromes y no corregibles) y el emisor para elegir el código a partir de un
informe de vuelta. El informe viaja en 12 bytes:

  [magia 2 B b"\\xF0R"][ber_x1e9 4 B][muestras 3 B][secuencia 2 B][CRC8 1 B]

Estimación del BER (por lote de palabras decodificadas):
  bits_mal ≈ síndromes_no_cero + no_corregibles   (cada no corregible implica ≥ 2 bits)
  BER_lote = bits_mal / (palabras · n)
  BER      = (1 - α)·BER + α·BER_lote              (media móvil exponencial, α = 0.2 por defecto)

Política con histéresis sobre la tabla ordenada (umbral_superior_ber, nombre):
  - Subir protección: en cuanto el BER supera el umbral del escalón actual.
  - Bajar protección: el BER debe estar por debajo de 0.5 × el umbral del escalón inmediatamente
    inferior durante `espera` evaluaciones consecutivas.
"""
from __future__ import annotations

import math
import struct

import fec

# De menos a más protección (y sobrecoste) según la especificación del proyecto.
ESCALERA = ("hamming_15_11", "secded_16_11", "secded_8_4", "hamming_7_4", "repeticion_5_1")

# Tabla por defecto: (umbral superior de BER, código). Índice creciente = más protección.
# hamming_7_4 queda fuera: solo se usa si el usuario lo pide explícitamente en la tabla.
TABLA_DEFECTO = (
    (1e-3, "hamming_15_11"),
    (2e-3, "secded_16_11"),
    (2e-2, "secded_8_4"),
    (math.inf, "repeticion_5_1"),
)

MAGIA = b"\xF0R"
LARGO_INFORME = 12
# magia (2 B) + ber_x1e9 (uint32) + muestras (24 bits) + secuencia (uint16) = 11 B, luego CRC8.
_FORMATO_CUERPO = "!2sI3sH"
_MAX_BER_X1E9 = 0xFFFFFFFF
_MAX_MUESTRAS = 0xFFFFFF
_MAX_SECUENCIA = 0xFFFF


def _numero_no_negativo(nombre: str, valor) -> float:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not valor >= 0:
        raise ValueError(f"{nombre} debe ser un número >= 0, no {valor!r}")
    return valor


def _entero_en_rango(nombre: str, valor, maximo: int) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or not 0 <= valor <= maximo:
        raise ValueError(f"{nombre} debe ser un entero en 0..{maximo}, no {valor!r}")
    return valor


class Estimador:
    """Media móvil exponencial del BER del canal, estimada desde el receptor."""

    def __init__(self, alfa: float = 0.2):
        if isinstance(alfa, bool) or not isinstance(alfa, (int, float)) or not 0 < alfa <= 1:
            raise ValueError(f"alfa debe estar en (0, 1], no {alfa!r}")
        self._alfa = float(alfa)
        self._ber = 0.0
        self._muestras = 0

    def observar(self, palabras: int, sindromes_no_cero: int, no_corregibles: int, n: int) -> None:
        """Acumula un lote decodificado. Con palabras=0 no cambia el estimador."""
        for nombre, valor in (("palabras", palabras), ("sindromes_no_cero", sindromes_no_cero),
                              ("no_corregibles", no_corregibles)):
            _entero_en_rango(nombre, valor, 1 << 62)
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError(f"n debe ser un entero >= 1, no {n!r}")
        if palabras == 0:
            return
        bits_mal = sindromes_no_cero + no_corregibles
        lote = min(1.0, bits_mal / (palabras * n))
        self._ber = (1.0 - self._alfa) * self._ber + self._alfa * lote
        self._muestras += palabras

    @property
    def ber(self) -> float:
        return self._ber

    @property
    def muestras(self) -> int:
        """Palabras totales vistas (suma de `palabras` de los lotes no vacíos)."""
        return self._muestras


def escalon(ber: float, tabla=TABLA_DEFECTO) -> str:
    """Código de la tabla para un BER dado, SIN histéresis (primer umbral con ber ≤ umbral)."""
    b = _numero_no_negativo("ber", ber)
    for umbral, nombre in tabla:
        if b <= umbral:
            return nombre
    return tabla[-1][1]


class Politica:
    """Elige el código según el BER, con histéresis asimétrica."""

    def __init__(self, tabla=TABLA_DEFECTO, inicial: str = "secded_8_4", espera: int = 3):
        if isinstance(espera, bool) or not isinstance(espera, int) or espera < 1:
            raise ValueError(f"espera debe ser un entero >= 1, no {espera!r}")
        if not tabla:
            raise ValueError("la tabla no puede estar vacía")
        filas = []
        for umbral, nombre in tabla:
            canonico = fec.buscar(nombre).nombre  # lanza ValueError si el código no existe
            filas.append((float(umbral), canonico))
        if len({nombre for _, nombre in filas}) != len(filas):
            raise ValueError("cada código aparece una sola vez en la tabla")
        if any(filas[i][0] >= filas[i + 1][0] for i in range(len(filas) - 1)):
            raise ValueError("los umbrales de la tabla deben ser estrictamente crecientes")
        self._tabla = tuple(filas)
        nombres = [nombre for _, nombre in self._tabla]
        if inicial not in nombres:
            raise ValueError(f"el código inicial {inicial!r} no está en la tabla")
        self._espera = espera
        self._idx = nombres.index(inicial)
        self._bajadas = 0

    def _objetivo(self, ber: float) -> int:
        for i, (umbral, _) in enumerate(self._tabla):
            if ber <= umbral:
                return i
        return len(self._tabla) - 1

    def decidir(self, ber: float) -> str:
        """Evalúa un BER y devuelve el código que queda activo tras la histéresis."""
        b = _numero_no_negativo("ber", ber)
        objetivo = self._objetivo(b)
        if objetivo > self._idx:
            # Más protección: inmediato.
            self._idx = objetivo
            self._bajadas = 0
        elif objetivo < self._idx:
            # Menos protección: exige ber < 0.5 × umbral del escalón inferior, `espera` veces seguidas.
            if b < 0.5 * self._tabla[self._idx - 1][0]:
                self._bajadas += 1
                if self._bajadas >= self._espera:
                    self._idx = objetivo
                    self._bajadas = 0
            else:
                self._bajadas = 0
        else:
            self._bajadas = 0
        return self.actual

    @property
    def actual(self) -> str:
        return self._tabla[self._idx][1]


def crc8(datos: bytes) -> int:
    """CRC-8 (polinomio 0x07, valor inicial 0)."""
    crc = 0
    for b in datos:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def codificar_informe(ber_x1e9: int, muestras: int, secuencia: int) -> bytes:
    """Empaqueta el informe receptor→emisor en 12 bytes (ver el docstring del módulo)."""
    ber = _entero_en_rango("ber_x1e9", ber_x1e9, _MAX_BER_X1E9)
    mue = _entero_en_rango("muestras", muestras, _MAX_MUESTRAS)
    sec = _entero_en_rango("secuencia", secuencia, _MAX_SECUENCIA)
    cuerpo = struct.pack(_FORMATO_CUERPO, MAGIA, ber, mue.to_bytes(3, "big"), sec)
    return cuerpo + bytes([crc8(cuerpo)])


def decodificar_informe(datos: bytes) -> tuple[int, int, int]:
    """Desempaqueta un informe; cualquier fallo (largo, magia, CRC) lanza ValueError."""
    if not isinstance(datos, (bytes, bytearray)):
        raise ValueError("el informe debe ser bytes")
    if len(datos) != LARGO_INFORME:
        raise ValueError(f"informe de {len(datos)} bytes; se esperaban {LARGO_INFORME}")
    cuerpo, crc = bytes(datos[:-1]), datos[-1]
    if crc8(cuerpo) != crc:
        raise ValueError("CRC8 del informe no coincide")
    magia, ber, mue, sec = struct.unpack(_FORMATO_CUERPO, cuerpo)
    if magia != MAGIA:
        raise ValueError("magia del informe incorrecta")
    return ber, int.from_bytes(mue, "big"), sec


def ber_teorico_palabra_mal(n: int, p: float) -> float:
    """P(una palabra de n bits tiene ≥ 2 errores) con BSC de probabilidad p: 1-(1-p)^n-n·p·(1-p)^(n-1)."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"n debe ser un entero >= 1, no {n!r}")
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1:
        raise ValueError(f"p debe estar en [0, 1], no {p!r}")
    q = 1.0 - p
    return 1.0 - q ** n - n * p * q ** (n - 1)
