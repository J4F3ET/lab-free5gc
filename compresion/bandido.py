#!/usr/bin/env python3
"""
bandido.py — La "IA" del compresor: un bandido UCB con descuento que elige el códec de
carga paquete a paquete (solo biblioteca estándar).

Es la misma regla de decisión que BandidoUCBDescontado del Lab 3 (tic_bandido.py,
Garivier y Moulines), con dos cambios que pide la red:

  1. QUÉ elige. En el Lab 3 el brazo era el tamaño de símbolo k. En un paquete de 30 a
     1400 bytes eso deja de tener sentido (con k = 2048 un paquete entero es 1 a 5
     símbolos), así que aquí los brazos son los códecs de compresores.CODECS — incluido
     "ninguno", que es la respuesta correcta para tráfico cifrado.
  2. CONTEXTO. En vez de la categoría de la fuente medida sobre 256 KB, dos rasgos que se
     calculan sobre el propio paquete sin etiquetas: tamaño de la carga y su entropía
     relativa (compresores.entropia_relativa).

Solo corre en el emisor. El receptor no necesita saber nada del bandido: lee el id del
códec en el primer byte del marco. No hay que sincronizar nada entre los dos extremos.

Recompensa de un paquete:  r = (bytes de carga ahorrados / bytes de carga) − COSTO_CPU
  · COSTO_CPU = 0,01 para cualquier códec que no sea "ninguno": si comprimir no ahorra
    nada (tráfico cifrado), "ninguno" gana el empate y el bandido deja de gastar CPU.
"""
from __future__ import annotations

import math

from compresores import CODECS, DEFLATE_DICT, DEFLATE_FLUJO, NINGUNO, Compresor, clave_flujo, entropia_relativa

COSTO_CPU = 0.01

# Configuración para la red, elegida con `evaluar.py ajustar` sobre el conjunto de
# ENTRENAMIENTO (no el de prueba). Huffman estático, DEFLATE sin diccionario y TIC por
# paquete nunca fueron el mejor códec en ninguna clase: como brazos solo agregan
# exploración (CPU) sin ahorrar un byte más, así que en la red no se ofrecen.
BRAZOS_RED = (NINGUNO, DEFLATE_DICT, DEFLATE_FLUJO)
GAMMA_RED, C_RED = 0.999, 0.05


def contexto(carga: bytes) -> tuple[int, int]:
    """(cubeta de tamaño, cubeta de entropía relativa) — 3 × 3 = 9 contextos."""
    n = len(carga)
    t = 0 if n < 64 else (1 if n < 512 else 2)
    h = entropia_relativa(carga[:256])
    e = 0 if h < 0.75 else (1 if h < 0.9 else 2)
    return t, e


class BandidoCodec:
    def __init__(self, brazos=tuple(CODECS), gamma: float = 0.99, c: float = 0.05):
        self.brazos = list(brazos)
        self.gamma, self.c = gamma, c
        self.estado: dict[tuple, tuple[list[float], list[float]]] = {}

    def _ctx(self, ctx):
        if ctx not in self.estado:
            n = len(self.brazos)
            self.estado[ctx] = ([0.0] * n, [0.0] * n)
        return self.estado[ctx]

    def elegir(self, ctx) -> int:
        N, S = self._ctx(ctx)
        for j, nj in enumerate(N):        # un brazo nunca probado se prueba primero
            if nj == 0.0:
                return self.brazos[j]
        tot = math.log(sum(N))
        mejor, jmejor = -1e9, 0
        for j, (nj, sj) in enumerate(zip(N, S)):
            v = sj / nj + self.c * math.sqrt(max(tot, 0.0) / nj)
            if v > mejor + 1e-12:          # empate -> el de menor id (ninguno = 0)
                mejor, jmejor = v, j
        return self.brazos[jmejor]

    def actualizar(self, ctx, codec: int, r: float) -> None:
        N, S = self._ctx(ctx)
        g = self.gamma
        for j in range(len(N)):
            N[j] *= g; S[j] *= g
        j = self.brazos.index(codec)
        N[j] += 1.0
        S[j] += r


def recompensa(largo_carga: int, largo_salida: int, codec: int) -> float:
    if largo_carga == 0:
        return 0.0
    ahorro = (largo_carga - largo_salida) / largo_carga
    return ahorro - (0.0 if codec == NINGUNO else COSTO_CPU)


class EmisorAdaptativo:
    """El emisor completo que usa la red: ROHC-lite + códec de carga elegido por el bandido.

    El contexto (tamaño, entropía) se calcula en el primer paquete de cada flujo y se
    recalcula cada REFRESCO_CTX paquetes: medir la entropía de cada paquete en Python
    costaba más CPU que la propia compresión, y dentro de un flujo casi no cambia."""

    REFRESCO_CTX = 32

    def __init__(self, modelo, reinicio_flujo: int = 16, gamma: float = GAMMA_RED, c: float = C_RED,
                 brazos=BRAZOS_RED):
        self.tx = Compresor(modelo, reinicio_flujo=reinicio_flujo)
        self.bandido = BandidoCodec(brazos=brazos, gamma=gamma, c=c)
        self.elegidos: dict[int, int] = {}  # cuántas veces se ELIGIÓ cada códec (antes del respaldo)
        self.ctx_flujo: dict[tuple, list] = {}  # clave -> [contexto, paquetes desde que se midió]

    def enviar(self, pkt: bytes) -> tuple[bytes, int]:
        hc, pref, carga = self.tx.separar(pkt)
        clave = clave_flujo(pkt)
        ent = self.ctx_flujo.get(clave)
        if ent is None or ent[1] >= self.REFRESCO_CTX:
            if len(self.ctx_flujo) > 4096:
                self.ctx_flujo.clear()
            ent = self.ctx_flujo[clave] = [contexto(carga), 0]
        ent[1] += 1
        ctx = ent[0]
        elegido = cod = self.ultimo_elegido = self.bandido.elegir(ctx)
        self.elegidos[elegido] = self.elegidos.get(elegido, 0) + 1
        d, conf = self.tx.probar(cod, carga, clave)
        if cod != NINGUNO and len(d) >= len(carga):
            cod, d, conf = NINGUNO, carga, None
        if conf:
            conf()
        # Se premia (y se cobra el costo de CPU a) el brazo ELEGIDO aunque haya caído al
        # respaldo "ninguno": intentarlo gastó CPU igual, y así aprende que ahí no sirve.
        self.bandido.actualizar(ctx, elegido, recompensa(len(carga), len(d), elegido))
        return bytes([(0x80 if hc else 0) | cod]) + pref + d, cod
