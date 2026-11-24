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

Recompensa de un paquete, dos variantes (la regla de decisión es la misma en las dos):

  · Por ahorro (por defecto):   r = ahorro − COSTO_CPU
      ahorro = bytes de carga ahorrados / bytes de carga. COSTO_CPU = 0,01 para cualquier
      códec que no sea "ninguno": si comprimir no ahorra nada (tráfico cifrado), "ninguno"
      gana el empate y el bandido deja de gastar CPU. No sabe nada del enlace.

  · Por latencia (velocidad_bps = v):  r = ahorro − t_cpu · v / (8 · bytes de carga)
      Es la fracción del tiempo de transmisión que se ahorra, descontado lo que se tardó en
      comprimir: (tiempo ganado en el enlace − tiempo de CPU) / tiempo que tardaría la carga
      sin comprimir. Con un enlace lento el término de CPU es despreciable y gana el que más
      ahorra; con uno rápido la CPU pesa más que los bytes y gana "ninguno". Es la misma
      cuenta que balance.py hace fuera de línea, pero aprendida en vivo paquete a paquete.
      t_cpu se MIDE (reloj del emisor) y solo incluye comprimir: el emisor no ve cuánto tarda
      el receptor en descomprimir (en DEFLATE y zstd es 4 a 6 veces menos que comprimir).

Qué deja ver el bandido (para Grafana, tablero "bandido en vivo"; lo publica shim.py):

  · Por brazo: veces elegido, veces que cayó al respaldo (lo eligió y no achicó), bytes de carga
    que entraron y que salieron, segundos de CPU y suma de recompensas. Con eso se grafica la
    recompensa media, el ahorro efectivo y los µs por paquete de cada brazo.
  · Por contexto y brazo: N (cuántas veces lo probó, con descuento), la media S/N y el índice UCB
    con el que decide. Es el estado interno: explica cada decisión.
  · Sombra: 1 de cada SOMBRA_CADA paquetes se comprime además con todos los códecs rápidos, sin
    mandarlo ni tocar estado, solo para medir. Da el ahorro que habría tenido cada algoritmo sobre
    el tráfico real y el "mejor por paquete", y de ahí el arrepentimiento del bandido.
"""
from __future__ import annotations

import math, time

from compresores import (CODECS, DEFLATE_DICT, DEFLATE_FLUJO, NINGUNO, PREDICTIVO_AUDIO, REGISTRO, ZSTD_DICT,
                         Compresor, clave_flujo, entropia_relativa, nombre_codec)

COSTO_CPU = 0.01

# Configuración para la red, elegida con `evaluar.py ajustar` sobre el conjunto de
# ENTRENAMIENTO (no el de prueba). Huffman estático, DEFLATE sin diccionario y TIC por
# paquete nunca fueron el mejor códec en ninguna clase: como brazos solo agregan
# exploración (CPU) sin ahorrar un byte más, así que en la red no se ofrecen. zstd con
# diccionario, si está disponible, reemplaza a DEFLATE con diccionario: el bandido ahorra
# ~0,4 puntos menos pero reconstruye mal la mitad de paquetes con pérdida y explora menos
# (ver resultados/ajuste.md). Sin zstd queda la configuración anterior.
# predictivo_audio (2026-11-13): el tráfico de prueba no trae audio PCM, así que ahí no gana nada y el
# bandido lo deja; en audio sin comprimir (un WAV por la página de envío) ahorra ~40 % donde los
# otros brazos sacan ~0-12 %.
if ZSTD_DICT in CODECS:
    BRAZOS_RED, GAMMA_RED, C_RED = (NINGUNO, ZSTD_DICT, DEFLATE_FLUJO, PREDICTIVO_AUDIO), 0.999, 0.02
else:
    BRAZOS_RED, GAMMA_RED, C_RED = (NINGUNO, DEFLATE_DICT, DEFLATE_FLUJO, PREDICTIVO_AUDIO), 0.999, 0.05

# Ráfaga: cuando el bandido elige un brazo CON MEMORIA POR FLUJO (deflate_flujo), lo sostiene
# RAFAGA paquetes seguidos de ese flujo antes de volver a decidir. Sin esto, probarlo un paquete
# suelto lo subestima: rinde más cuanto más se usa, porque recuerda los anteriores (docs/08 §4.12).
# La recompensa se sigue anotando en cada paquete, así que la media ve lo que de verdad rinde.
RAFAGA_RED = 8
CON_MEMORIA = frozenset({DEFLATE_FLUJO})


# Nombres de las cubetas del contexto (etiquetas de las métricas). Los cortes están en contexto().
# tamaño de la carga: chico < 64 B, mediano 64 a 511 B, grande >= 512 B
# entropía relativa (H de los primeros 256 B / el máximo posible): baja < 0,75, media 0,75 a 0,9, alta >= 0,9
TAMANOS = ("chico", "mediano", "grande")
ENTROPIAS = ("baja", "media", "alta")
SOMBRA_CADA = 100  # en la red: 1 de cada 100 paquetes se mide con todos los códecs rápidos


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

    def indices(self, ctx) -> list[tuple[float, float, float]]:
        """Por brazo: (N, media, índice UCB) en ese contexto. Es la cuenta con la que decide:

            índice_j = S_j / N_j  +  c · sqrt( ln(Σ N) / N_j )
                       └ media ┘     └── bono de exploración ──┘

        media = recompensa promedio reciente del brazo (S y N se multiplican por γ en cada paquete:
        lo viejo pesa cada vez menos). El bono es grande si el brazo se ha probado poco frente al
        total, y baja al probarlo: así un brazo abandonado vuelve a probarse cada tanto. Un brazo
        sin probar (N = 0) tiene índice infinito: se prueba primero."""
        N, S = self._ctx(ctx)
        tot = max(math.log(sum(N)), 0.0) if sum(N) > 0 else 0.0
        return [(nj, sj / nj, sj / nj + self.c * math.sqrt(tot / nj)) if nj > 0 else (0.0, 0.0, math.inf)
                for nj, sj in zip(N, S)]

    def elegir(self, ctx) -> int:
        mejor, jmejor = -1e9, 0
        for j, (nj, _, v) in enumerate(self.indices(ctx)):
            if nj == 0.0:                  # un brazo nunca probado se prueba primero
                return self.brazos[j]
            if v > mejor + 1e-12:          # empate -> el de menor id (ninguno = 0)
                mejor, jmejor = v, j
        return self.brazos[jmejor]

    def estado_exportable(self) -> list[tuple[tuple, int, float, float, float]]:
        """(contexto, brazo, N, media, índice) de cada contexto visto y cada brazo ya probado."""
        return [(ctx, self.brazos[j], n, media, ind) for ctx in sorted(self.estado)
                for j, (n, media, ind) in enumerate(self.indices(ctx)) if n > 0]

    def actualizar(self, ctx, codec: int, r: float) -> None:
        N, S = self._ctx(ctx)
        g = self.gamma
        for j in range(len(N)):
            N[j] *= g; S[j] *= g
        j = self.brazos.index(codec)
        N[j] += 1.0
        S[j] += r


def recompensa(largo_carga: int, largo_salida: int, codec: int,
               t_cpu: float = 0.0, velocidad_bps: float | None = None) -> float:
    """Ver el encabezado. Sin velocidad_bps: ahorro − COSTO_CPU. Con velocidad_bps: fracción
    del tiempo de transmisión ahorrada, neta de la CPU, acotada a [−1, 1]."""
    if largo_carga == 0:
        return 0.0
    ahorro = (largo_carga - largo_salida) / largo_carga
    if velocidad_bps is None:
        return ahorro - (0.0 if codec == NINGUNO else COSTO_CPU)
    if codec == NINGUNO:
        return 0.0
    return max(-1.0, ahorro - t_cpu * velocidad_bps / (8 * largo_carga))


# Bytes fijos por paquete en el aire, fuera de la carga: IP+UDP del túnel (28 B) + 1 B de id de FEC.
# Ver recompensa_aire: el encabezado de 4 B (secuencia y largo) va dentro del paréntesis, multiplicado por f.
OVERHEAD_AIRE = 33


def aire(largo_carga: int, factor_fec: float, overhead_fijo: int = OVERHEAD_AIRE) -> float:
    """Bytes que ocupa un paquete en el aire: overhead_fijo + factor_fec · (4 + largo_carga).

    factor_fec = n/k del código FEC (1,0 sin FEC; 1,364 Hamming 15/11; 1,75 Hamming 7/4; 2,0 SECDED 8/4):
    la paridad crece con la carga, así que se multiplica. El 4 de dentro del paréntesis son la secuencia
    y el largo, que también van codificados.

    Desglose elegido: 33 = 28 (IP+UDP) + 1 (id FEC) + 4. Los 4 B de secuencia/largo están contados DOS
    veces (una en el 33 y otra dentro de f·(4+L)): es un error por exceso de 4 B por paquete. Lo
    correcto estricto sería overhead_fijo = 29. Se deja 33 porque es lo que se pidió, y el efecto en
    el ahorro relativo es mínimo (y el mismo para todos los códecs)."""
    return overhead_fijo + factor_fec * (4 + largo_carga)


def recompensa_aire(largo_carga: int, largo_salida: int, codec: int, factor_fec: float,
                    overhead_fijo: int = OVERHEAD_AIRE, costo_cpu: float = COSTO_CPU) -> float:
    """Recompensa en BYTES DEL AIRE (no solo de carga). Opcional: no reemplaza a recompensa().

        ahorro_aire = 1 − aire(largo_salida) / aire(largo_carga)     (acotado a [−1, 1])
        r = ahorro_aire − (0 si codec == NINGUNO, si no costo_cpu / factor_fec)

    Por qué: el ahorro de carga ignora la paridad FEC (que escala con f) y el overhead fijo por
    paquete. Con f = 2 un byte de carga ahorrado vale dos bytes en el aire, así que el costo de CPU
    relativo baja en la misma proporción: por eso el costo se divide entre f.

    Diferencia con recompensa() sin velocidad (con f = 1 y overhead_fijo = 0 NO coincide):
      · el denominador y el numerador llevan el 4 B de secuencia/largo, así que el ahorro relativo
        es algo menor en paquetes chicos (1 − (4+Ls)/(4+Lc) en vez de (Lc − Ls)/Lc);
      · el costo de CPU se mantiene en costo_cpu / 1 = 0,01 cuando f = 1, igual que antes.
    Para paquetes grandes las dos recompensas se parecen; en chicos la de aire es más conservadora.
    Con 'ninguno' devuelve 0 siempre (no hay nada que ahorrar ni CPU que cobrar)."""
    if largo_carga == 0 or codec == NINGUNO:
        return 0.0
    a_c = aire(largo_carga, factor_fec, overhead_fijo)
    a_s = aire(largo_salida, factor_fec, overhead_fijo)
    ahorro = max(-1.0, min(1.0, 1.0 - a_s / a_c))
    return ahorro - costo_cpu / factor_fec


class EmisorAdaptativo:
    """El emisor completo que usa la red: ROHC-lite + códec de carga elegido por el bandido.

    El contexto (tamaño, entropía) se calcula en el primer paquete de cada flujo y se
    recalcula cada REFRESCO_CTX paquetes: medir la entropía de cada paquete en Python
    costaba más CPU que la propia compresión, y dentro de un flujo casi no cambia.

    velocidad_bps: activa la recompensa por latencia (ver el encabezado). factor_cpu escala el
    tiempo de CPU medido, para modelar una implementación más rápida (0,1 = 10× más rápida,
    como la vista cpu_10x de balance.py)."""

    REFRESCO_CTX = 32

    def __init__(self, modelo, reinicio_flujo: int = 16, gamma: float = GAMMA_RED, c: float = C_RED,
                 brazos=BRAZOS_RED, velocidad_bps: float | None = None, factor_cpu: float = 1.0,
                 perdida_iot: dict | None = None, sombra_cada: int = 0, sombra_codecs=None,
                 contexto_fn=contexto, rafaga: int = RAFAGA_RED, factor_fec: float | None = None,
                 costo_cpu: float = COSTO_CPU, overhead_fijo: int = OVERHEAD_AIRE):
        # factor_fec = f (None = recompensa antigua, de carga). Si se da, la recompensa es en bytes del
        # aire (recompensa_aire) con costo_cpu y overhead_fijo; velocidad_bps no puede combinarse con ella.
        if factor_fec is not None and velocidad_bps is not None:
            raise ValueError("factor_fec (recompensa en aire) y velocidad_bps (latencia) no se combinan")
        self.factor_fec, self.costo_cpu, self.overhead_fijo = factor_fec, costo_cpu, overhead_fijo
        # sombra_cada = K > 0: 1 de cada K paquetes se mide además con sombra_codecs (por defecto,
        # los códecs sin pérdida que no están marcados `lento`). 0 = sin sombra (las evaluaciones
        # fuera de línea no la necesitan: ya corren cada algoritmo por separado).
        # contexto_fn: cómo se saca el contexto de una carga (para medir qué aporta: evaluar.py bandido).
        self.contexto_fn = contexto_fn
        # rafaga = R > 1: un brazo de CON_MEMORIA elegido para un flujo se sostiene R paquetes de ese
        # flujo (se corta antes si un paquete cae al respaldo). 0 o 1 = decidir en cada paquete.
        self.rafaga = rafaga
        self.en_rafaga: dict[tuple, list] = {}  # clave de flujo -> [brazo, paquetes que le quedan]
        self.por_rafaga = 0  # paquetes que se mandaron por ráfaga sin consultar al bandido
        # perdida_iot: la telemetría JSON va con PredictivoIoT (con pérdida acotada) en vez de
        # pasar por el bandido; el resto del tráfico sigue igual. None = todo sin pérdida.
        self.tx = Compresor(modelo, reinicio_flujo=reinicio_flujo, perdida_iot=perdida_iot)
        self.bandido = BandidoCodec(brazos=brazos, gamma=gamma, c=c)
        self.velocidad_bps, self.factor_cpu = velocidad_bps, factor_cpu
        self.elegidos: dict[int, int] = {}  # cuántas veces se ELIGIÓ cada códec (antes del respaldo)
        self.ctx_flujo: dict[tuple, list] = {}  # clave -> [contexto, paquetes desde que se midió]
        # Por brazo ELEGIDO: [veces elegido, respaldos, bytes de carga que entraron, bytes que
        # salieron, segundos de CPU, suma de recompensas]. Acumulados: Grafana saca tasas con rate().
        self.por_brazo: dict[int, list] = {b: [0, 0, 0, 0, 0.0, 0.0] for b in self.bandido.brazos}
        self.por_contexto: dict[tuple, int] = {}  # paquetes que pasaron por cada contexto
        self.sombra_cada, self.n = sombra_cada, 0
        self.sombra_codecs = list(sombra_codecs) if sombra_codecs is not None else [
            i for i in CODECS if not REGISTRO[i].lento]
        # Bytes de carga de los paquetes muestreados: lo que entró ("original"), lo que mandó el
        # bandido, el mejor códec de cada paquete, y lo que habría salido con cada códec.
        self.sombra = {"paquetes": 0, "original": 0, "bandido": 0, "mejor": 0,
                       "bytes": {i: 0 for i in self.sombra_codecs}, "cpu": {i: 0.0 for i in self.sombra_codecs}}

    def enviar(self, pkt: bytes) -> tuple[bytes, int]:
        _, forzado = self.tx.preparar(pkt)
        if forzado is not None:  # telemetría con pérdida activa: no la decide el bandido
            self.ultimo_elegido = forzado
            return self.tx.empaquetar(pkt, forzado)
        hc, pref, carga = self.tx.separar(pkt)
        clave = clave_flujo(pkt)
        ent = self.ctx_flujo.get(clave)
        if ent is None or ent[1] >= self.REFRESCO_CTX:
            if len(self.ctx_flujo) > 4096:
                self.ctx_flujo.clear()
            ent = self.ctx_flujo[clave] = [self.contexto_fn(carga), 0]
        ent[1] += 1
        ctx = ent[0]
        raf = self.en_rafaga.get(clave)
        if raf and raf[1] > 0:
            elegido = raf[0]; raf[1] -= 1; self.por_rafaga += 1
        else:
            elegido = self.bandido.elegir(ctx)
            if self.rafaga > 1 and elegido in CON_MEMORIA:
                if len(self.en_rafaga) > 4096:
                    self.en_rafaga.clear()
                self.en_rafaga[clave] = [elegido, self.rafaga - 1]
            elif raf:
                del self.en_rafaga[clave]
        cod = self.ultimo_elegido = elegido
        self.elegidos[elegido] = self.elegidos.get(elegido, 0) + 1
        t0 = time.perf_counter()
        d, conf = self.tx.probar(cod, carga, clave)
        t_cpu = (time.perf_counter() - t0) * self.factor_cpu
        if cod != NINGUNO and len(d) >= len(carga):
            cod, d, conf = NINGUNO, carga, None
            self.en_rafaga.pop(clave, None)  # no achicó: no tiene sentido insistir con este flujo
        self.n += 1
        if self.sombra_cada and self.n % self.sombra_cada == 0:
            self._medir_sombra(carga, clave, len(d))   # antes de confirmar: con el estado de ANTES
        if conf:
            conf()
        # Se premia (y se cobra el costo de CPU a) el brazo ELEGIDO aunque haya caído al
        # respaldo "ninguno": intentarlo gastó CPU igual, y así aprende que ahí no sirve.
        if self.factor_fec is not None:
            r = recompensa_aire(len(carga), len(d), elegido, self.factor_fec, self.overhead_fijo, self.costo_cpu)
        else:
            r = recompensa(len(carga), len(d), elegido, t_cpu, self.velocidad_bps)
        self.bandido.actualizar(ctx, elegido, r)
        e = self.por_brazo[elegido]
        e[0] += 1; e[1] += cod != elegido; e[2] += len(carga); e[3] += len(d); e[4] += t_cpu; e[5] += r
        self.por_contexto[ctx] = self.por_contexto.get(ctx, 0) + 1
        return bytes([(0x80 if hc else 0) | cod]) + pref + d, cod

    def _medir_sombra(self, carga: bytes, clave: tuple, enviado: int) -> None:
        """Comprime la carga con cada códec de la sombra SIN confirmar estado ni mandar nada.

        Qué mide: para ESTE paquete, con el estado que tiene el emisor en este momento, cuántos
        bytes habría mandado cada códec (con el mismo respaldo: si no achica, va tal cual). Es
        el contrafactual de la DECISIÓN, que es lo que le importa al bandido:

            arrepentimiento = (bytes del bandido − bytes del mejor códec de cada paquete)
                              / bytes originales

        No es exactamente "ese códec fijo todo el día": deflate_flujo solo recuerda los paquetes
        que el bandido sí mandó por él, así que aquí sale algo peor que si fuera fijo."""
        s = self.sombra
        mejor = len(carga)
        for cod in self.sombra_codecs:
            t0 = time.perf_counter()
            try:
                largo = min(len(self.tx.probar(cod, carga, clave)[0]), len(carga))
            except Exception:
                largo = len(carga)
            s["cpu"][cod] += time.perf_counter() - t0
            s["bytes"][cod] += largo
            mejor = min(mejor, largo)
        s["paquetes"] += 1; s["original"] += len(carga); s["bandido"] += enviado
        s["mejor"] += min(mejor, enviado)


def texto_bandido(em: "EmisorAdaptativo", l: str) -> str:
    """Las variables del bandido en formato Prometheus (ver el encabezado de bandido.py). Todo lo
    que termina en un contador es acumulado desde que arrancó el proceso; el tablero
    "bandido en vivo" (observability/grafana/dashboards_compresion.py) saca de ahí:

        recompensa media del brazo   = Δ recompensa_suma / Δ elegidos
        ahorro efectivo del brazo    = 1 − Δ carga_bytes{salida} / Δ carga_bytes{entrada}
        µs de CPU por paquete        = 1e6 · Δ cpu_segundos / Δ elegidos
        tasa de respaldo             = Δ respaldos / Δ elegidos
        ahorro que habría tenido X   = 1 − Δ sombra_bytes{X} / Δ sombra_bytes{original}
        arrepentimiento del bandido  = (Δ sombra_bytes{bandido_ucb} − Δ sombra_bytes{mejor}) / Δ sombra_bytes{original}

    lab5g_cmp_bandido_* son el estado interno en este instante (no acumulados)."""
    familias: dict[str, list[str]] = {}   # las muestras de una misma métrica van juntas

    def g(nombre, etiquetas, valor):
        et = ",".join([l] + [f'{k}="{v}"' for k, v in etiquetas.items()])
        familias.setdefault(nombre, []).append(f"lab5g_cmp_{nombre}{{{et}}} {valor}\n")
        return ""

    s = ""
    for cod, (elegido, respaldos, entro, salio, cpu, rec) in em.por_brazo.items():
        n = {"codec": nombre_codec(cod)}
        s += g("elegidos", n, elegido) + g("respaldos", n, respaldos)
        s += g("carga_bytes", dict(n, tipo="entrada"), entro) + g("carga_bytes", dict(n, tipo="salida"), salio)
        s += g("cpu_segundos", n, f"{cpu:.6f}") + g("recompensa_suma", n, f"{rec:.6f}")
    for (t, e), paquetes in em.por_contexto.items():
        s += g("contexto_paquetes", {"tam": TAMANOS[t], "ent": ENTROPIAS[e]}, paquetes)
    for (t, e), cod, n, media, indice in em.bandido.estado_exportable():
        et = {"tam": TAMANOS[t], "ent": ENTROPIAS[e], "codec": nombre_codec(cod)}
        s += g("bandido_n", et, f"{n:.3f}") + g("bandido_media", et, f"{media:.5f}")
        s += g("bandido_indice", et, f"{indice:.5f}")
    sm = em.sombra
    if em.sombra_cada:
        s += g("sombra_paquetes", {}, sm["paquetes"])
        for nombre in ("original", "mejor"):
            s += g("sombra_bytes", {"codec": nombre}, sm[nombre])
        s += g("sombra_bytes", {"codec": "bandido_ucb"}, sm["bandido"])   # mismo nombre que en la evaluación
        for cod in em.sombra_codecs:
            s += g("sombra_bytes", {"codec": nombre_codec(cod)}, sm["bytes"][cod])
            s += g("sombra_cpu_segundos", {"codec": nombre_codec(cod)}, f"{sm['cpu'][cod]:.6f}")
    return "".join(x for v in familias.values() for x in v)
