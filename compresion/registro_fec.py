"""Capa FEC del shim: emisión, recepción con canal manual, registro de paquetes y comandos.

Todo aquí es puro (sin TUN ni sockets) para poder probarlo; shim.py solo conecta la red.

  emisión:    marco comprimido --fec.codificar(secuencia)--> marco en el cable
  recepción:  marco del cable --[canal: voltear bits armados]--> fec.decodificar
              --> marco comprimido --> descomprimir y escribir a la TUN (callback del shim)

El canal se modela EN EL RECEPTOR: voltear bits del marco recibido antes de decodificar equivale a
un canal ruidoso entre codificar y decodificar, y deja todo el registro en un solo proceso.

LIMITACIÓN: el receptor no tiene el paquete original, así que `integro` NO compara contra él.
Solo dice que lo descomprimido se escribió a la TUN sin excepción. Una corrección errónea (p. ej.
2 errores en Hamming) puede dar integro=True con datos distintos; el IP/TCP/UDP de la propia
aplicación (checksums) es quien lo detectaría.

MTU con FEC. Un paquete de L bytes comprimidos ocupa en el cable
    1 + ceil(ceil((32 + 8L) / k) * n / 8)   bytes   (≈ L·n/k + 5 B de cabecera y relleno)
y debe caber en  mtu_enlace − 28 (IP+UDP del túnel). Despejando L:
    bloques = floor(8·(mtu_enlace − 28 − 1) / n) ;  L_max = floor((bloques·k − 32) / 8)
y el MTU de cmp0 es  L_max − margen  (margen 36 B, el mismo que hoy: 1464−28=1436 → 1400).
Hamming (7,4): 779 ; repetición (5,1): 247. Sin FEC no se usa: sigue valiendo CMP_MTU.
"""
from __future__ import annotations

import threading
import time
from collections import deque

import canal
import fec
from compresores import nombre_codec

MTU_ENLACE = 1464        # MTU de uesimtun0
TUNEL = 28               # IP + UDP del túnel
MARGEN = 36              # 1464 - 28 - 1400 = lo que deja hoy CMP_MTU por defecto
MAX_POSICIONES_ARMADA = 256
ESTADOS = ("limpio", "corregido", "no_corregible", "invalido")


def mtu_con_fec(codigo, mtu_enlace: int = MTU_ENLACE, tunel: int = TUNEL, margen: int = MARGEN) -> int:
    """MTU de cmp0 con FEC activo (ver la fórmula en el docstring del módulo)."""
    c = fec.buscar(codigo)
    bloques = 8 * (mtu_enlace - tunel - 1) // c.n
    return max(68, (bloques * c.k - 32) // 8 - margen)


def tamano_en_cable(codigo, largo: int) -> int:
    """Bytes del marco FEC para `largo` bytes comprimidos."""
    c = fec.buscar(codigo)
    return 1 + -(-(-(-(32 + 8 * largo) // c.k) * c.n) // 8)


class RegistroFec:
    """Últimos `capacidad` paquetes (anillo), con id incremental global. Seguro entre hilos."""

    def __init__(self, capacidad: int = 200):
        self._cola: deque[dict] = deque(maxlen=capacidad)
        self._sig = 1
        self._cerrojo = threading.Lock()

    def anotar(self, **campos) -> dict:
        """Añade una entrada; le pone `id` (incremental, empieza en 1) y devuelve la entrada."""
        with self._cerrojo:
            entrada = {"id": self._sig, **campos}
            self._sig += 1
            self._cola.append(entrada)
            return entrada

    def listar(self, desde_id: int = 0) -> list[dict]:
        """Entradas con id > desde_id, de más antigua a más reciente."""
        with self._cerrojo:
            return [dict(e) for e in self._cola if e["id"] > desde_id]


def validar_armada(orden: dict) -> dict:
    """Valida el comando de inyección y devuelve la armada normalizada. ValueError si es mala.

    {"bits": N, "semilla": opcional}  o  {"posiciones": [bits dentro del marco recibido]}.
    Las posiciones cuentan desde el bit 0 del primer byte del marco; el byte 0 (id del código)
    no se toca, así que deben ser >= 8.
    """
    if not isinstance(orden, dict):
        raise ValueError("la orden debe ser un objeto JSON")
    if "posiciones" in orden:
        pos = orden["posiciones"]
        if not isinstance(pos, list) or not pos or len(pos) > MAX_POSICIONES_ARMADA:
            raise ValueError(f"posiciones: lista de 1 a {MAX_POSICIONES_ARMADA} enteros")
        if any(isinstance(p, bool) or not isinstance(p, int) or p < 8 for p in pos):
            raise ValueError("posiciones: enteros >= 8 (el byte 0 es el id del código y no se toca)")
        return {"posiciones": list(pos)}
    n = orden.get("bits")
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_POSICIONES_ARMADA:
        raise ValueError(f"bits: entero entre 1 y {MAX_POSICIONES_ARMADA}")
    semilla = orden.get("semilla")
    if semilla is not None and (isinstance(semilla, bool) or not isinstance(semilla, int)):
        raise ValueError("semilla: entero o ausente")
    return {"bits": n, "semilla": semilla}


def aplicar_canal(marco: bytes, armada: dict | None) -> tuple[bytes, list[int]]:
    """Voltea los bits de la armada en el marco recibido. Devuelve (marco, posiciones aplicadas).

    Solo zona de palabras código (desde el bit 8). Con {"bits": N} se eligen N posiciones al azar
    (N se recorta a los bits disponibles); con "posiciones", las que caen fuera del marco se ignoran.
    """
    if not armada:
        return marco, []
    total = 8 * len(marco)
    if "posiciones" in armada:
        pos = [p for p in armada["posiciones"] if 8 <= p < total]
    else:
        pos = canal.elegir_posiciones(total, min(armada["bits"], max(0, total - 8)),
                                      armada.get("semilla"), desde_bit=8)
    return canal.inyectar(marco, pos), pos


def info_codec(marco_comprimido: bytes) -> tuple[int, str, bool]:
    """(id del códec, nombre, tiene cabecera ROHC-lite) a partir del byte 0 del marco comprimido."""
    b0 = marco_comprimido[0]
    return b0 & 0x0F, nombre_codec(b0 & 0x0F), bool(b0 & 0x80)


def estado_de(res: fec.Resultado) -> str:
    if res.no_corregibles:
        return "no_corregible"
    return "corregido" if res.corregidas else "limpio"


class CapaFec:
    """Estado FEC de un lado del shim: código, secuencia, armada, registro y contadores."""

    def __init__(self, codigo, lado: str, capacidad: int = 200, reloj=time.time):
        self.codigo = fec.buscar(codigo)
        self.lado = lado
        self.registro = RegistroFec(capacidad)
        self.secuencia = 0
        self.armada: dict | None = None
        self._cerrojo = threading.Lock()
        self._reloj = reloj
        self.c_paquetes: dict[tuple[str, str], int] = {}   # (estado, codec) -> n
        self.c_sindrome: dict[str, int] = {}
        self.c_bits = 0
        self.c_palabras = 0
        self.c_bytes = {"comprimido": 0, "fec": 0}

    # --- emisión ---
    def emitir(self, marco_comprimido: bytes) -> bytes:
        """Codifica con FEC (secuencia propia mod 65536) y cuenta los bytes de ambos tamaños."""
        marco = fec.codificar(marco_comprimido, self.codigo, self.secuencia)
        self.secuencia = (self.secuencia + 1) & 0xFFFF
        self.c_bytes["comprimido"] += len(marco_comprimido)
        self.c_bytes["fec"] += len(marco)
        return marco

    # --- inyección manual ---
    def armar(self, orden: dict) -> dict:
        armada = validar_armada(orden)
        with self._cerrojo:
            self.armada = armada
        return dict(armada)

    def _consumir(self) -> dict | None:
        with self._cerrojo:
            a, self.armada = self.armada, None
            return a

    # --- recepción ---
    def recibir(self, marco: bytes, procesar) -> tuple[dict, Exception | None]:
        """Canal → decodificar → procesar → entrada de registro.

        `procesar(marco_comprimido) -> int` descomprime, escribe a la TUN y devuelve el tamaño del
        paquete IP; si lanza, la entrada queda con integro=False y bytes_ip=None. Si decodificar
        lanza ValueError, NO se llama a procesar (no se escribe a la TUN) y el estado es "invalido".
        Devuelve (entrada, excepción o None); el shim cuenta el error si hay excepción.
        """
        recibido, bits = aplicar_canal(marco, self._consumir())
        base = dict(secuencia=None, lado=self.lado, t=self._reloj(), codigo=self.codigo.nombre,
                    codec_id=None, codec=None, bytes_comprimido=None, bytes_fec=len(marco),
                    bytes_ip=None, bits_inyectados=bits, palabras=0, sindromes_no_cero=0,
                    corregidas=0, no_corregibles=0, sindromes={}, posiciones_corregidas=[],
                    estado="invalido", integro=False)
        try:
            comprimido, res = fec.decodificar(recibido)
        except ValueError as e:
            return self._cerrar(base), e
        cid, cnombre, _hc = info_codec(comprimido) if comprimido else (None, None, False)
        base.update(secuencia=res.secuencia, codec_id=cid, codec=cnombre,
                    bytes_comprimido=len(comprimido), palabras=res.palabras,
                    sindromes_no_cero=res.sindromes_no_cero, corregidas=res.corregidas,
                    no_corregibles=res.no_corregibles, sindromes=dict(res.sindromes),
                    posiciones_corregidas=[list(p) for p in res.posiciones_corregidas],
                    estado=estado_de(res))
        error = None
        try:
            if not comprimido:
                raise ValueError("marco comprimido vacío")
            base["bytes_ip"] = procesar(comprimido)
            base["integro"] = True
        except Exception as e:  # contexto perdido, flujo desincronizado, datos corruptos...
            error = e
        return self._cerrar(base), error

    def _cerrar(self, base: dict) -> dict:
        entrada = self.registro.anotar(**base)
        k = (entrada["estado"], entrada["codec"] or "n/a")
        self.c_paquetes[k] = self.c_paquetes.get(k, 0) + 1
        for s, v in entrada["sindromes"].items():
            self.c_sindrome[s] = self.c_sindrome.get(s, 0) + v
        self.c_bits += len(entrada["bits_inyectados"])
        self.c_palabras += entrada["palabras"]
        return entrada

    # --- métricas y comandos ---
    def texto(self, l: str) -> str:
        """Métricas Prometheus (l = 'lado="ue"')."""
        c = f'{l},codigo="{self.codigo.nombre}"'
        s = "".join(f'lab5g_cmp_fec_paquetes_total{{{c},estado="{e}",codec="{n}"}} {v}\n'
                    for (e, n), v in self.c_paquetes.items())
        s += "".join(f'lab5g_cmp_fec_sindrome_total{{{c},sindrome="{k}"}} {v}\n'
                     for k, v in self.c_sindrome.items())
        s += f"lab5g_cmp_fec_bits_inyectados_total{{{c}}} {self.c_bits}\n"
        s += f"lab5g_cmp_fec_palabras_total{{{c}}} {self.c_palabras}\n"
        s += "".join(f'lab5g_cmp_fec_bytes_total{{{c},tipo="{t}"}} {v}\n' for t, v in self.c_bytes.items())
        return s

    def describir_codigo(self) -> dict:
        c = self.codigo
        return {"codigo": c.nombre, "n": c.n, "k": c.k, "P": c.P, "H": c.H}

    def responder(self, linea: str) -> dict:
        """Una línea de comando -> respuesta (dict). Nunca lanza."""
        import json
        try:
            verbo, _, resto = linea.strip().partition(" ")
            verbo = verbo.upper()
            if verbo == "PAQUETES":
                desde = int(resto) if resto.strip() else 0
                with self._cerrojo:
                    a = dict(self.armada) if self.armada else None
                return {"codigo": self.codigo.nombre, "n": self.codigo.n, "k": self.codigo.k,
                        "lado": self.lado, "armada": a, "paquetes": self.registro.listar(desde)}
            if verbo == "INYECTAR":
                return {"ok": True, "armada": self.armar(json.loads(resto))}
            if verbo == "CODIGO":
                return self.describir_codigo()
            return {"ok": False, "error": f"comando desconocido: {verbo[:20]!r}"}
        except (ValueError, TypeError) as e:  # incluye JSONDecodeError
            return {"ok": False, "error": str(e)[:200]}
