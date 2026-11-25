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
Hamming (7,4): 780 ; repetición (5,1): 247. Sin FEC no se usa: sigue valiendo CMP_MTU.

Modo auto (FEC=auto). El emisor elige el código de ESCALERA_AUTO según el BER que mide el receptor
(adaptativo.py). El receptor manda un informe de 13 bytes (MARCA_INFORME + 12 B de adaptativo) como
mucho cada INTERVALO_INFORME s y solo si hay palabras nuevas; el emisor aplica la política al
informe. Un marco que empieza por 0xFF es siempre un informe: ningún id de código vale 0xFF y el
KEEPALIVE de shim.py es de 1 byte (el shim lo filtra antes). El MTU de cmp0 en auto es el mínimo de
la escalera (mtu_auto), así cabe cualquier código que se elija.

Reenvío por NACK (reenvio=True, env REENVIO=1; apagado por defecto). Si el receptor decodifica una
palabra con doble error detectado (no_corregible), NO entrega el paquete a la TUN: encola su secuencia
y manda un NACK (0xFE + n + n×uint16 + CRC8). El emisor guarda en un anillo (128 marcos) cada marco
LIMPIO, antes del canal, y reenvía el que le pidan (máx. 2 veces por secuencia, 8 por segundo); el
reenvío vuelve a pasar por el canal del receptor, con ruido nuevo. La secuencia del NACK puede ser
errónea si el error cayó en la cabecera; entonces el NACK no encuentra copia (sin_copia) o pide otra.

Elegir el algoritmo en caliente (dinamico=True, es decir FEC en OPCIONES_ELEGIR). La página de síntoma
puede cambiar el código sin reiniciar: ELEGIR <nombre> en el shim de cmp-dn cambia su emisión (DN→UE) y
el shim manda al UE una trama de control de 3 B (0xFD + índice + CRC8) que el UE aplica a su emisión
(UE→DN). La trama se repite cada ELEGIR_CADA_S s mientras la elección siga vigente, así sobrevive a que
reinicie el UE. Con dinamico=True la maquinaria de auto (estimador, política, informes) existe siempre:
el receptor informa de su BER aunque el código sea fijo, y el emisor solo cambia con los informes si está
en auto. Con dinamico=False (FEC fijo como repeticion_5_1, o vacío) todo se comporta como antes.
"""
from __future__ import annotations

import struct
import threading
import time
from collections import deque

import adaptativo
import canal
import fec
from collections import Counter
from compresores import nombre_codec

MTU_ENLACE = 1464        # MTU de uesimtun0
TUNEL = 28               # IP + UDP del túnel
MARGEN = 36              # 1464 - 28 - 1400 = lo que deja hoy CMP_MTU por defecto
MAX_POSICIONES_ARMADA = 256
CLAVES_CONTADORES = ("paquetes", "con_error_inyectado", "sindromes_no_cero", "corregidas",
                     "no_corregibles", "invalidos", "bits_inyectados", "palabras", "corregidos_mal",
                     "reenviados")
# reenviados (receptor, solo REENVIO=1): entradas que llegaron por reenvío tras un NACK propio.
# corregido_mal (añadido tras la verificación del 22-11): el síndrome indicó una corrección (estado
# «corregido») pero el paquete no se pudo reconstruir (integro=False), p. ej. 2 errores en Hamming (7,4).
ESTADOS = ("limpio", "corregido", "corregido_mal", "no_corregible", "invalido")

# --- modo auto ---
# Escalera de códigos que el emisor puede elegir. Sin repetición (5,1) ni Hamming (7,4): el MTU de
# cmp0 en auto es el mínimo de la escalera (mtu_auto), y con (5,1) bajaría a 247 B.
ESCALERA_AUTO = ("hamming_15_11", "secded_16_11", "secded_8_4")
# (umbral superior de BER, código): de menos a más protección. Umbrales inclusivos; ver adaptativo.Politica.
TABLA_AUTO = ((1e-3, "hamming_15_11"), (2e-3, "secded_16_11"), (float("inf"), "secded_8_4"))
CODIGO_INICIAL_AUTO = "secded_8_4"   # el más protector de la escalera: arranque conservador
MARCA_INFORME = 0xFF                 # primer byte de un informe; no es id de código (1..5) ni el KEEPALIVE
INTERVALO_INFORME = 2.0              # s mínimos entre dos informes del receptor


def mtu_con_fec(codigo, mtu_enlace: int = MTU_ENLACE, tunel: int = TUNEL, margen: int = MARGEN) -> int:
    """MTU de cmp0 con FEC activo (ver la fórmula en el docstring del módulo)."""
    c = fec.buscar(codigo)
    bloques = 8 * (mtu_enlace - tunel - 1) // c.n
    return max(68, (bloques * c.k - 32) // 8 - margen)


def mtu_auto(mtu_enlace: int = MTU_ENLACE, tunel: int = TUNEL, margen: int = MARGEN) -> int:
    """MTU de cmp0 en modo auto: el menor de la escalera, para que quepa cualquier código de ella."""
    return min(mtu_con_fec(c, mtu_enlace, tunel, margen) for c in ESCALERA_AUTO)


def tamano_en_cable(codigo, largo: int) -> int:
    """Bytes del marco FEC para `largo` bytes comprimidos."""
    c = fec.buscar(codigo)
    return 1 + -(-(-(-(32 + 8 * largo) // c.k) * c.n) // 8)


def es_auto(valor) -> bool:
    """¿El valor de FEC pide el modo auto? (sin distinguir mayúsculas ni espacios)"""
    return isinstance(valor, str) and valor.strip().lower() == "auto"


def es_informe(marco: bytes) -> bool:
    """Un marco que empieza por MARCA_INFORME es un informe. El KEEPALIVE (1 byte, 0xFF) también lo
    cumple: el shim lo descarta antes de preguntar."""
    return len(marco) > 0 and marco[0] == MARCA_INFORME


# --- reenvío por NACK (solo con reenvio=True) ---
MARCA_NACK = 0xFE                    # no es id de código (1..5), ni MARCA_INFORME (0xFF) ni KEEPALIVE
NACK_MAX_SECUENCIAS = 8              # secuencias por mensaje NACK
NACK_COLA_MAX = 16                   # pendientes en el receptor (sin repetir)
ANILLO_REENVIO = 128                 # marcos limpios que guarda el emisor (evicción FIFO)
REENVIOS_POR_SECUENCIA = 2           # tope de reenvíos de una misma secuencia
REENVIOS_POR_SEGUNDO = 8             # tope global de reenvíos por segundo (reloj de CapaFec)
NACK_TTL_S = 30.0                    # receptor: cuánto espera un reenvío tras pedirlo (reloj de CapaFec)
NACK_RECORDADOS_MAX = 64             # receptor: secuencias pedidas recordadas (se olvidan las más viejas)
CLAVES_REENVIO = ("nack_enviados", "nack_recibidos", "reenviados", "sin_copia", "descartados_limite",
                  "nack_malos")


def es_nack(marco: bytes) -> bool:
    """¿Es un NACK? Empieza por MARCA_NACK, que ningún marco FEC puede empezar (ids 1..5)."""
    return len(marco) > 0 and marco[0] == MARCA_NACK


def codificar_nack(secuencias) -> bytes:
    """NACK completo para el cable: [MARCA_NACK][n 1 B][n × uint16 big-endian][CRC8 de lo anterior]."""
    seqs = list(secuencias)
    if not 1 <= len(seqs) <= NACK_MAX_SECUENCIAS:
        raise ValueError(f"un NACK lleva de 1 a {NACK_MAX_SECUENCIAS} secuencias, no {len(seqs)}")
    for s in seqs:
        if isinstance(s, bool) or not isinstance(s, int) or not 0 <= s <= 0xFFFF:
            raise ValueError(f"secuencia fuera de 0..65535: {s!r}")
    cuerpo = bytes([MARCA_NACK, len(seqs)]) + struct.pack(f"!{len(seqs)}H", *seqs)
    return cuerpo + bytes([adaptativo.crc8(cuerpo)])


def decodificar_nack(datos) -> list[int]:
    """Secuencias de un NACK completo (con su marca). ValueError si la marca, el largo o el CRC no cuadran."""
    if not isinstance(datos, (bytes, bytearray)) or len(datos) < 2:
        raise ValueError("NACK demasiado corto")
    if datos[0] != MARCA_NACK:
        raise ValueError("no es un NACK")
    n = datos[1]
    if not 1 <= n <= NACK_MAX_SECUENCIAS or len(datos) != 3 + 2 * n:
        raise ValueError(f"NACK de {len(datos)} B con n={n}: el largo no cuadra")
    if adaptativo.crc8(bytes(datos[:-1])) != datos[-1]:
        raise ValueError("CRC8 del NACK no coincide")
    return list(struct.unpack(f"!{n}H", bytes(datos[2:-1])))


# --- elegir el algoritmo en caliente (solo con dinamico=True) ---
OPCIONES_ELEGIR = ("auto", "hamming_15_11", "secded_16_11", "secded_8_4", "hamming_7_4")
CODIGOS_ELEGIBLES = tuple(o for o in OPCIONES_ELEGIR if o != "auto")   # los códigos que puede tener el marco
MARCA_ELEGIR = 0xFD                  # trama de control DN→UE; no es id de código (1..5), ni NACK (0xFE), ni INFORME (0xFF)
ELEGIR_CADA_S = 10.0                 # el DN repite la elección vigente cada tantos s (sobrevive a reinicios del UE)


def es_dinamico(valor) -> bool:
    """¿El valor de FEC permite elegir el algoritmo en caliente? (sin distinguir mayúsculas ni espacios)"""
    return isinstance(valor, str) and valor.strip().lower() in OPCIONES_ELEGIR


def es_eleccion(marco: bytes) -> bool:
    """¿Es una trama de control de elección? Empieza por MARCA_ELEGIR, que ningún marco FEC puede empezar."""
    return len(marco) > 0 and marco[0] == MARCA_ELEGIR


def codificar_eleccion(nombre: str) -> bytes:
    """Trama de control para el cable: [MARCA_ELEGIR][índice en OPCIONES_ELEGIR][CRC8 de los 2 anteriores]."""
    if nombre not in OPCIONES_ELEGIR:
        raise ValueError(f"algoritmo {nombre!r} no válido; usa uno de: {', '.join(OPCIONES_ELEGIR)}")
    cuerpo = bytes([MARCA_ELEGIR, OPCIONES_ELEGIR.index(nombre)])
    return cuerpo + bytes([adaptativo.crc8(cuerpo)])


def decodificar_eleccion(marco) -> str:
    """Nombre del algoritmo de una trama de control. ValueError si el largo, la marca, el CRC o el índice no cuadran."""
    if not isinstance(marco, (bytes, bytearray)):
        raise ValueError("la trama de elección debe ser bytes")
    if len(marco) != 3:
        raise ValueError(f"trama de elección de {len(marco)} B; debe ser de 3")
    if marco[0] != MARCA_ELEGIR:
        raise ValueError("no es una trama de elección")
    if adaptativo.crc8(bytes(marco[:2])) != marco[2]:
        raise ValueError("CRC8 de la elección no coincide")
    if marco[1] >= len(OPCIONES_ELEGIR):
        raise ValueError(f"índice de elección {marco[1]} fuera de rango (0..{len(OPCIONES_ELEGIR) - 1})")
    return OPCIONES_ELEGIR[marco[1]]


def _nueva_politica() -> adaptativo.Politica:
    return adaptativo.Politica(TABLA_AUTO, inicial=CODIGO_INICIAL_AUTO)


class RegistroFec:
    """Últimos `capacidad` paquetes (anillo), con id incremental global. Seguro entre hilos."""

    def __init__(self, capacidad: int = 200):
        self._cola: deque[dict] = deque(maxlen=capacidad)
        self._sig = 1
        self._cerrojo = threading.Lock()
        # Acumulados desde el arranque; NO se derivan del anillo (que olvida lo viejo).
        self._tot = {k: 0 for k in CLAVES_CONTADORES}

    def anotar(self, **campos) -> dict:
        """Añade una entrada; le pone `id` (incremental, empieza en 1) y devuelve la entrada."""
        with self._cerrojo:
            entrada = {"id": self._sig, **campos}
            self._sig += 1
            self._cola.append(entrada)
            t = self._tot
            t["paquetes"] += 1
            t["con_error_inyectado"] += 1 if campos.get("bits_inyectados") else 0
            t["sindromes_no_cero"] += int(campos.get("sindromes_no_cero") or 0)
            t["corregidas"] += int(campos.get("corregidas") or 0)
            t["no_corregibles"] += int(campos.get("no_corregibles") or 0)
            t["invalidos"] += 1 if campos.get("estado") == "invalido" else 0
            t["corregidos_mal"] += 1 if campos.get("estado") == "corregido_mal" else 0
            t["reenviados"] += 1 if campos.get("reenviado") else 0
            t["bits_inyectados"] += len(campos.get("bits_inyectados") or ())
            t["palabras"] += int(campos.get("palabras") or 0)
            return entrada

    def contadores(self) -> dict:
        """Contadores acumulados desde el arranque + `ultimo_id` (id de la última entrada, 0 si ninguna)."""
        with self._cerrojo:
            return {**self._tot, "ultimo_id": self._sig - 1}

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


CODEC_NA = "n/a"                # marco inválido: no hay códec que leer
CODEC_DESCONOCIDO = "desconocido"   # id sin nombre registrado (p. ej. un bit volteado en el byte 0)


def etiqueta_codec(codec_id: int | None) -> str:
    """Valor de la etiqueta `codec` de las métricas: siempre de un conjunto cerrado y precargado
    (nombres registrados, «n/a» o «desconocido»), nunca «codec_N»."""
    if codec_id is None:
        return CODEC_NA
    n = nombre_codec(codec_id)
    return CODEC_DESCONOCIDO if n == f"codec_{codec_id}" else n


def info_codec(marco_comprimido: bytes) -> tuple[int, str, bool]:
    """(id del códec, nombre, tiene cabecera ROHC-lite) a partir del byte 0 del marco comprimido."""
    b0 = marco_comprimido[0]
    return b0 & 0x0F, nombre_codec(b0 & 0x0F), bool(b0 & 0x80)


def validar_codigo(valor) -> fec.CodigoLineal:
    """Código FEC por nombre/id o ValueError con mensaje claro (para fallar al arrancar el shim).
    «auto» es válido: devuelve el código inicial de la escalera (el que usa CapaFec al arrancar)."""
    if es_auto(valor):
        return fec.buscar(CODIGO_INICIAL_AUTO)
    try:
        return fec.buscar(valor)
    except ValueError:
        validos = ", ".join([c.nombre for c in fec.CODIGOS.values()] + ["auto"])
        raise ValueError(f"FEC={valor!r} no es un código válido; usa uno de: {validos} (o vacío para apagarlo)") from None


def emitir_marco(capa, marco_comprimido: bytes) -> bytes:
    """Decisión de emisión del shim: sin capa FEC (None) el marco pasa SIN tocar (mismo objeto)."""
    return capa.emitir(marco_comprimido) if capa else marco_comprimido


def recibir_marco(capa, rx, marco: bytes, escribir=None):
    """Recepción con FEC: (paquete IP | None, entrada, excepción | None). Sin red ni TUN.

    `rx.desempaquetar(comprimido)` descomprime; `escribir(pkt)` (la TUN en el shim) se llama dentro de
    `procesar`, así `integro` sigue significando «se escribió sin excepción». Con marco inválido o
    descompresión fallida el paquete es None.
    """
    salida: list[bytes] = []

    def procesar(comprimido: bytes) -> int:
        pkt = rx.desempaquetar(comprimido)
        if escribir is not None:
            escribir(pkt)
        salida.append(pkt)
        return len(pkt)

    entrada, error = capa.recibir(marco, procesar)
    return (salida[0] if salida and error is None else None), entrada, error


def estado_de(res: fec.Resultado) -> str:
    if res.no_corregibles:
        return "no_corregible"
    return "corregido" if res.corregidas else "limpio"


class CapaFec:
    """Estado FEC de un lado del shim: código, secuencia, armada, registro y contadores.

    `codigo="auto"` activa el modo auto (ver el docstring del módulo): el código vigente cambia con los
    informes del receptor y los contadores se etiquetan con el código real de cada marco."""

    def __init__(self, codigo, lado: str, capacidad: int = 200, reloj=time.time,
                 canal=None, semilla_canal: int = 1, reenvio: bool = False, dinamico: bool = False):
        """`canal`: objeto de canal.parse_canal (o None = sin canal aleatorio). Cada marco recibido usa
        la semilla `semilla_canal + n`, con n = marcos recibidos hasta entonces: determinista.
        `reenvio`: activa el reenvío por NACK (ver el docstring del módulo); apagado = comportamiento de siempre.
        `dinamico`: permite ELEGIR el algoritmo en caliente y crea siempre la maquinaria de auto (ver el módulo)."""
        self.dinamico = bool(dinamico)
        self.auto = es_auto(codigo)              # estado mutable: ELEGIR «auto» lo activa, otro código lo quita
        self.codigo = fec.buscar(CODIGO_INICIAL_AUTO if self.auto else codigo)
        con_maquinaria = self.auto or self.dinamico
        self.politica = _nueva_politica() if con_maquinaria else None
        self.estimador = adaptativo.Estimador() if con_maquinaria else None
        self.eleccion: str | None = None          # último algoritmo elegido en caliente (ELEGIR / trama de control)
        self.elegir_stats = {"elegida_local": 0, "recibida": 0, "mala": 0}
        self._elegir_remoto: str | None = None    # DN: elección que se repite hacia el UE
        self._t_elegir: float | None = None       # DN: instante del último envío de esa elección
        self.informes = {"enviados": 0, "recibidos": 0, "malos": 0}   # solo en auto (o dinamico al enviar)
        self.cambios_codigo = 0
        self._t_informe: float | None = None
        self._muestras_informadas = 0
        self._secuencia_informe = 0
        self.lado = lado
        self.registro = RegistroFec(capacidad)
        self.secuencia = 0
        self.armada: dict | None = None
        self.canal = canal
        self.semilla_canal = semilla_canal
        self._marcos_rx = 0
        self._cerrojo = threading.Lock()
        self._reloj = reloj
        # Contadores por código: (codigo, estado, codec) -> n ; (codigo, síndrome) -> n ; codigo -> filas.
        self._paq: dict[tuple[str, str, str], int] = {}     # (codigo, estado, codec) -> n
        self._sind: dict[tuple[str, str], int] = {}         # (codigo, síndrome) -> n
        self.c_codigo: dict[str, dict[str, int]] = {}
        # Reenvío (solo reenvio=True). Emisor: anillo secuencia -> (marco limpio, código, largo comprimido).
        self.reenvio = bool(reenvio)
        self._anillo: dict[int, tuple[bytes, str, int]] = {}
        self._reenvios_seq: dict[int, int] = {}                 # reenvíos hechos por secuencia (en el anillo)
        self._reenvio_cola: list[tuple[bytes, str, int]] = []   # marcos limpios por reenviar
        self._t_reenvios: deque[float] = deque()                # instantes de los últimos reenvíos (1 s)
        self._nack_cola: list[int] = []                         # receptor: secuencias por pedir
        self._nack_secuencias: dict[int, float] = {}            # receptor: secuencia pedida -> instante del NACK
        self.reenvio_stats = {k: 0 for k in CLAVES_REENVIO}
        self._precargar()

    def _fila(self, codigo: str) -> dict[str, int]:
        return self.c_codigo.setdefault(codigo, {"bits": 0, "palabras": 0, "comprimido": 0, "fec": 0})

    @property
    def c_paquetes(self) -> dict[tuple[str, str], int]:
        """Agregado por (estado, codec), todos los códigos juntos."""
        total: dict[tuple[str, str], int] = {}
        for (_cod, e, n), v in self._paq.items():
            total[(e, n)] = total.get((e, n), 0) + v
        return total

    @property
    def c_sindrome(self) -> dict[str, int]:
        """Agregado por síndrome, todos los códigos juntos."""
        total: dict[str, int] = {}
        for (_cod, k), v in self._sind.items():
            total[k] = total.get(k, 0) + v
        return total

    @property
    def c_bits(self) -> int:
        return sum(f["bits"] for f in self.c_codigo.values())

    @property
    def c_palabras(self) -> int:
        return sum(f["palabras"] for f in self.c_codigo.values())

    @property
    def c_bytes(self) -> dict[str, int]:
        return {t: sum(f[t] for f in self.c_codigo.values()) for t in ("comprimido", "fec")}

    def _precargar(self) -> None:
        """Crea a 0 todas las series posibles (estado × códec y síndromes no nulos) de cada código
        posible (toda la escalera en auto; el código fijo si no). Un contador que nace ya valiendo 1
        hace que increase()/rate() de Prometheus no cuenten el primer evento: con las series en 0
        desde el arranque, la primera inyección se ve en Grafana."""
        from compresores import CODECS, CODECS_CON_PERDIDA
        codecs = sorted(set(CODECS.values()) | set(CODECS_CON_PERDIDA.values())) + [CODEC_NA, CODEC_DESCONOCIDO]
        if self.dinamico:
            codigos = CODIGOS_ELEGIBLES
        else:
            codigos = ESCALERA_AUTO if self.auto else (self.codigo.nombre,)
        for nombre in codigos:
            c = fec.buscar(nombre)
            self._fila(c.nombre)
            for e in ESTADOS:
                for cod in codecs:
                    self._paq[(c.nombre, e, cod)] = 0
            r = c.n - c.k
            for v in range(1, 2 ** r):
                self._sind[(c.nombre, format(v, f"0{r}b"))] = 0

    def mtu(self) -> int:
        """MTU de cmp0 para este lado: el mínimo de la escalera si puede cambiar de código (auto o dinámico),
        el del código fijo si no."""
        return mtu_auto() if (self.auto or self.dinamico) else mtu_con_fec(self.codigo)

    # --- emisión ---
    def emitir(self, marco_comprimido: bytes) -> bytes:
        """Codifica con FEC (secuencia propia mod 65536) y cuenta los bytes de ambos tamaños."""
        marco = fec.codificar(marco_comprimido, self.codigo, self.secuencia)
        if self.reenvio:   # el marco limpio, antes de cualquier canal o armada
            self._guardar_anillo(self.secuencia, marco, self.codigo.nombre, len(marco_comprimido))
        self.secuencia = (self.secuencia + 1) & 0xFFFF
        fila = self._fila(self.codigo.nombre)
        fila["comprimido"] += len(marco_comprimido)
        fila["fec"] += len(marco)
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

    def _aplicar_canales(self, marco: bytes) -> tuple[bytes, list[int]]:
        """Armada manual y, si hay, canal aleatorio sobre el mismo marco. Devuelve (marco, bits volteados).

        Los volteos se aplican en sucesión, así que cada bit sale invertido tantas veces como aparece
        en la unión de ambas listas: una posición en las dos se cancela (e + e = 0). Sin canal
        aleatorio es exactamente aplicar_canal de siempre.
        """
        with self._cerrojo:
            indice = self._marcos_rx
            self._marcos_rx += 1
        marco, manual = aplicar_canal(marco, self._consumir())
        if self.canal is None:
            return marco, manual
        ruido = self.canal.posiciones(8 * len(marco), self.semilla_canal + indice, desde_bit=8)
        marco = canal.inyectar(marco, ruido)
        cuenta = Counter(manual + ruido)
        return marco, sorted(p for p, n in cuenta.items() if n % 2)

    # --- recepción ---
    def _codigo_del_marco(self, marco: bytes) -> str:
        """Código que indica el byte 0 del marco en modo auto. Fuera de auto, o con id desconocido,
        el vigente (como siempre)."""
        if (self.auto or self.dinamico) and marco and marco[0] in fec.CODIGOS:
            return fec.CODIGOS[marco[0]].nombre
        return self.codigo.nombre

    def recibir(self, marco: bytes, procesar) -> tuple[dict, Exception | None]:
        """Canal → decodificar → procesar → entrada de registro.

        `procesar(marco_comprimido) -> int` descomprime, escribe a la TUN y devuelve el tamaño del
        paquete IP; si lanza, la entrada queda con integro=False y bytes_ip=None. Si decodificar
        lanza ValueError, NO se llama a procesar (no se escribe a la TUN) y el estado es "invalido".
        En auto, el estimador del BER se alimenta con cada marco decodificado (con su código real).
        Devuelve (entrada, excepción o None); el shim cuenta el error si hay excepción.
        """
        recibido, bits = self._aplicar_canales(marco)
        nombre = self._codigo_del_marco(recibido)
        base = dict(secuencia=None, lado=self.lado, t=self._reloj(), codigo=nombre,
                    codec_id=None, codec=None, bytes_comprimido=None, bytes_fec=len(marco),
                    bytes_ip=None, bits_inyectados=bits, palabras=0, sindromes_no_cero=0,
                    corregidas=0, no_corregibles=0, sindromes={}, posiciones_corregidas=[],
                    estado="invalido", integro=False, reenviado=False)
        try:
            comprimido, res = fec.decodificar(recibido)
        except ValueError as e:
            return self._cerrar(base), e
        if self.estimador is not None:   # el estimador observa siempre que exista (auto o dinámico)
            self.estimador.observar(res.palabras, res.sindromes_no_cero, res.no_corregibles, fec.buscar(nombre).n)
        cid, cnombre, _hc = info_codec(comprimido) if comprimido else (None, None, False)
        base.update(secuencia=res.secuencia, codec_id=cid, codec=cnombre,
                    bytes_comprimido=len(comprimido), palabras=res.palabras,
                    sindromes_no_cero=res.sindromes_no_cero, corregidas=res.corregidas,
                    no_corregibles=res.no_corregibles, sindromes=dict(res.sindromes),
                    posiciones_corregidas=[list(p) for p in res.posiciones_corregidas],
                    estado=estado_de(res))
        # Llega por reenvío si pedimos esta secuencia por NACK (el estado sigue siendo el real).
        base["reenviado"] = self.reenvio and self._es_reenvio(res.secuencia)
        if self.reenvio and res.no_corregibles:
            # Doble error detectado: no se entrega (la TUN no recibe basura) y se pide la secuencia.
            self._encolar_nack(res.secuencia)
            base["integro"] = False
            return self._cerrar(base), None
        error = None
        try:
            if not comprimido:
                raise ValueError("marco comprimido vacío")
            base["bytes_ip"] = procesar(comprimido)
            base["integro"] = True
        except Exception as e:  # contexto perdido, flujo desincronizado, datos corruptos...
            error = e
        if base["estado"] == "corregido" and not base["integro"]:
            base["estado"] = "corregido_mal"   # el síndrome «corrigió», pero el paquete no se reconstruyó
        return self._cerrar(base), error

    def _cerrar(self, base: dict) -> dict:
        entrada = self.registro.anotar(**base)
        cod = entrada["codigo"]
        k = (cod, entrada["estado"], etiqueta_codec(entrada["codec_id"]))
        self._paq[k] = self._paq.get(k, 0) + 1
        for s, v in entrada["sindromes"].items():
            ks = (cod, s)
            self._sind[ks] = self._sind.get(ks, 0) + v
        fila = self._fila(cod)
        fila["bits"] += len(entrada["bits_inyectados"])
        fila["palabras"] += entrada["palabras"]
        return entrada

    # --- informes del receptor (solo auto) ---
    def informe_pendiente(self, ahora: float) -> bytes | None:
        """Informe (MARCA_INFORME + 12 B) que el receptor debe mandar ahora, o None. Solo si hay estimador
        (auto o dinámico: el otro extremo puede estar en auto y los necesita): si pasaron ≥ INTERVALO_INFORME s
        desde el último y hay palabras medidas desde ese último.
        `ahora` lo pone el llamador (reloj del shim, o un reloj simulado en las pruebas)."""
        if self.estimador is None:
            return None
        with self._cerrojo:
            muestras = self.estimador.muestras
            if muestras <= self._muestras_informadas:
                return None
            if self._t_informe is not None and ahora - self._t_informe < INTERVALO_INFORME:
                return None
            self._t_informe = ahora
            self._muestras_informadas = muestras
            self._secuencia_informe = (self._secuencia_informe + 1) & 0xFFFF
            self.informes["enviados"] += 1
            ber_x1e9 = min(round(self.estimador.ber * 1e9), 0xFFFFFFFF)
            secuencia = self._secuencia_informe
        cuerpo = adaptativo.codificar_informe(ber_x1e9, min(muestras, 0xFFFFFF), secuencia)
        return bytes([MARCA_INFORME]) + cuerpo

    def recibir_informe(self, datos: bytes) -> bool:
        """Aplica un informe recibido (SIN la marca). True si era válido; False si era malo (cuenta en
        `malos` y no cambia el código) o si no estamos en auto. Nunca lanza."""
        if not self.auto:
            return False
        try:
            ber_x1e9, _muestras, _secuencia = adaptativo.decodificar_informe(datos)
        except ValueError:
            with self._cerrojo:
                self.informes["malos"] += 1
            return False
        with self._cerrojo:
            anterior = self.codigo.nombre
            self.codigo = fec.buscar(self.politica.decidir(ber_x1e9 / 1e9))
            if self.codigo.nombre != anterior:
                self.cambios_codigo += 1
            self.informes["recibidos"] += 1
        return True

    # --- elegir el algoritmo en caliente (solo con dinamico=True) ---
    def elegir(self, nombre) -> dict:
        """Fija el algoritmo: «auto» activa el modo auto (política nueva, código inicial de la escalera);
        otro nombre fija ese código y sale de auto. ValueError si el nombre no está en OPCIONES_ELEGIR o
        si la capa no es dinámica. Devuelve el estado resultante."""
        if not isinstance(nombre, str) or nombre not in OPCIONES_ELEGIR:
            raise ValueError(f"algoritmo {nombre!r} no válido; usa uno de: {', '.join(OPCIONES_ELEGIR)}")
        if not self.dinamico:
            raise ValueError("este despliegue no es dinámico: el algoritmo se fija con FEC al arrancar")
        with self._cerrojo:
            if nombre == "auto":
                self.auto = True
                self.politica = _nueva_politica()
                self.codigo = fec.buscar(CODIGO_INICIAL_AUTO)
            else:
                self.auto = False
                self.codigo = fec.buscar(nombre)
            self.eleccion = nombre
            return {"ok": True, "codigo_activo": self.codigo.nombre, "auto": self.auto, "eleccion": self.eleccion}

    def eleccion_pendiente(self, ahora: float) -> bytes | None:
        """Trama de control que el DN debe mandar al UE ahora, o None. Solo en el lado «dn» y solo si hay
        una elección fijada con ELEGIR: la primera vez de inmediato y luego cada ELEGIR_CADA_S s."""
        if self.lado != "dn":
            return None
        with self._cerrojo:
            nombre = self._elegir_remoto
            if nombre is None:
                return None
            if self._t_elegir is not None and ahora - self._t_elegir < ELEGIR_CADA_S:
                return None
            self._t_elegir = ahora
        return codificar_eleccion(nombre)

    def recibir_eleccion(self, marco: bytes) -> bool:
        """Aplica una trama de control (con su marca). True si era válida y se aplicó. Sin dinamico la ignora
        sin contarla. Una trama mala no cambia nada y cuenta como «mala». Nunca lanza."""
        if not self.dinamico:
            return False
        try:
            nombre = decodificar_eleccion(marco)
        except ValueError:
            with self._cerrojo:
                self.elegir_stats["mala"] += 1
            return False
        self.elegir(nombre)
        with self._cerrojo:
            self.elegir_stats["recibida"] += 1
        return True

    # --- reenvío por NACK (solo con reenvio=True) ---
    def _guardar_anillo(self, secuencia: int, marco: bytes, codigo: str, largo: int) -> None:
        with self._cerrojo:
            self._anillo.pop(secuencia, None)   # tras un wrap de 16 bits, la nueva va al final
            self._anillo[secuencia] = (marco, codigo, largo)
            self._reenvios_seq.pop(secuencia, None)
            while len(self._anillo) > ANILLO_REENVIO:
                vieja = next(iter(self._anillo))
                del self._anillo[vieja]
                self._reenvios_seq.pop(vieja, None)

    def _encolar_nack(self, secuencia: int) -> None:
        ahora = self._reloj()
        with self._cerrojo:
            if secuencia not in self._nack_cola:
                if len(self._nack_cola) >= NACK_COLA_MAX:
                    return
                self._nack_cola.append(secuencia)
            self._recordar_nack(secuencia, ahora)

    def _recordar_nack(self, secuencia: int, ahora: float) -> None:
        """Llamar con _cerrojo tomado. Recuerda una secuencia pedida; olvida las caducadas (TTL) y,
        si hay más de NACK_RECORDADOS_MAX, las más viejas."""
        ds = self._nack_secuencias
        ds.pop(secuencia, None)
        for s in [s for s, t in ds.items() if ahora - t > NACK_TTL_S]:
            del ds[s]
        ds[secuencia] = ahora
        while len(ds) > NACK_RECORDADOS_MAX:
            del ds[next(iter(ds))]

    def _es_reenvio(self, secuencia: int) -> bool:
        """True si `secuencia` la pedimos por NACK hace menos de NACK_TTL_S. La quita del recuerdo
        (caducada o no): un marco con esa secuencia solo cuenta como reenvío una vez."""
        ahora = self._reloj()
        with self._cerrojo:
            t = self._nack_secuencias.pop(secuencia, None)
        return t is not None and ahora - t <= NACK_TTL_S

    def nack_pendiente(self) -> bytes | None:
        """NACK con hasta NACK_MAX_SECUENCIAS secuencias encoladas (las quita de la cola), o None."""
        if not self.reenvio:
            return None
        with self._cerrojo:
            if not self._nack_cola:
                return None
            lote = self._nack_cola[:NACK_MAX_SECUENCIAS]
            del self._nack_cola[:NACK_MAX_SECUENCIAS]
            self.reenvio_stats["nack_enviados"] += 1
        return codificar_nack(lote)

    def recibir_nack(self, datos: bytes) -> bool:
        """Procesa un NACK completo (con su marca, tal como llega). True si era válido. Encola los
        reenvíos que procedan. Nunca lanza."""
        if not self.reenvio:
            return False
        try:
            secuencias = decodificar_nack(datos)
        except ValueError:
            with self._cerrojo:
                self.reenvio_stats["nack_malos"] += 1
            return False
        ahora = self._reloj()
        with self._cerrojo:
            self.reenvio_stats["nack_recibidos"] += 1
            for seq in secuencias:
                if seq not in self._anillo:
                    self.reenvio_stats["sin_copia"] += 1
                    continue
                if self._reenvios_seq.get(seq, 0) >= REENVIOS_POR_SECUENCIA:
                    continue
                while self._t_reenvios and ahora - self._t_reenvios[0] >= 1.0:
                    self._t_reenvios.popleft()
                if len(self._t_reenvios) >= REENVIOS_POR_SEGUNDO:
                    self.reenvio_stats["descartados_limite"] += 1
                    continue
                self._t_reenvios.append(ahora)
                self._reenvios_seq[seq] = self._reenvios_seq.get(seq, 0) + 1
                self._reenvio_cola.append(self._anillo[seq])
        return True

    def reenvios_pendientes(self) -> list[bytes]:
        """Marcos a reenviar ahora. Cada marco limpio se manda de nuevo tal cual (el receptor le aplica ruido nuevo
        en recibir(), como a cualquier envío). No incrementa `secuencia` ni vuelve a guardarse en el anillo."""
        if not self.reenvio:
            return []
        with self._cerrojo:
            cola, self._reenvio_cola = self._reenvio_cola, []
        salida = []
        for marco, codigo, largo in cola:
            enviado = marco   # el ruido del canal lo aplica el RECEPTOR en recibir(); aqui iria doble
            fila = self._fila(codigo)
            fila["comprimido"] += largo
            fila["fec"] += len(enviado)
            salida.append(enviado)
        with self._cerrojo:
            self.reenvio_stats["reenviados"] += len(salida)
        return salida

    # --- métricas y comandos ---
    def texto(self, l: str) -> str:
        """Métricas Prometheus (l = 'lado="ue"')."""
        s = "".join(f'lab5g_cmp_fec_paquetes_total{{{l},codigo="{cod}",estado="{e}",codec="{n}"}} {v}\n'
                    for (cod, e, n), v in list(self._paq.items()))
        s += "".join(f'lab5g_cmp_fec_sindrome_total{{{l},codigo="{cod}",sindrome="{k}"}} {v}\n'
                     for (cod, k), v in list(self._sind.items()))
        for cod, fila in list(self.c_codigo.items()):
            s += f'lab5g_cmp_fec_bits_inyectados_total{{{l},codigo="{cod}"}} {fila["bits"]}\n'
            s += f'lab5g_cmp_fec_palabras_total{{{l},codigo="{cod}"}} {fila["palabras"]}\n'
            s += "".join(f'lab5g_cmp_fec_bytes_total{{{l},codigo="{cod}",tipo="{t}"}} {fila[t]}\n'
                         for t in ("comprimido", "fec"))
        if self.estimador is not None:   # auto, o dinámico (que siempre tiene estimador)
            s += f"lab5g_cmp_fec_ber_estimado{{{l}}} {self.estimador.ber}\n"
            codigos = CODIGOS_ELEGIBLES if self.dinamico else ESCALERA_AUTO
            s += "".join(f'lab5g_cmp_fec_codigo_activo{{{l},codigo="{c}"}} {int(c == self.codigo.nombre)}\n'
                         for c in codigos)
            s += f"lab5g_cmp_fec_cambios_codigo_total{{{l}}} {self.cambios_codigo}\n"
            s += "".join(f'lab5g_cmp_fec_informes_total{{{l},tipo="{t}"}} {v}\n' for t, v in self.informes.items())
        if self.dinamico:
            with self._cerrojo:
                st_elegir = dict(self.elegir_stats)
            s += "".join(f'lab5g_cmp_fec_elegir_total{{{l},tipo="{t}"}} {v}\n' for t, v in st_elegir.items())
        if self.reenvio:
            with self._cerrojo:
                st = dict(self.reenvio_stats)
            s += "".join(f'lab5g_cmp_fec_reenvio_total{{{l},tipo="{t}"}} {v}\n' for t, v in st.items())
        return s

    def describir_codigo(self) -> dict:
        c = self.codigo
        return {"codigo": c.nombre, "n": c.n, "k": c.k, "P": c.P, "H": c.H,
                "canal": self.canal.nombre if self.canal is not None else None}

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
            if verbo == "CONTADORES":
                return {"ok": True, "codigo": self.codigo.nombre, "lado": self.lado, **self.registro.contadores()}
            if verbo == "INYECTAR":
                return {"ok": True, "armada": self.armar(json.loads(resto))}
            if verbo == "ELEGIR":
                try:
                    r = self.elegir(resto.strip())
                except ValueError as e:
                    return {"ok": False, "error": str(e)[:200], "dinamico": self.dinamico}
                with self._cerrojo:
                    self.elegir_stats["elegida_local"] += 1
                    if self.lado == "dn":   # el DN reenvía la elección al UE por el túnel (ver eleccion_pendiente)
                        self._elegir_remoto = r["eleccion"]
                        self._t_elegir = None
                return r
            if verbo == "CODIGO":
                extra: dict = {"reenvio": self.reenvio}
                if self.reenvio:
                    with self._cerrojo:
                        extra["reenvio_stats"] = dict(self.reenvio_stats)
                return {**self.describir_codigo(), "auto": self.auto,
                        "ber_estimado": self.estimador.ber if self.estimador is not None else None,
                        "codigo_activo": self.codigo.nombre,
                        "escalera": list(ESCALERA_AUTO) if self.auto else None,
                        "dinamico": self.dinamico,
                        "opciones": list(OPCIONES_ELEGIR) if self.dinamico else None,
                        "eleccion": self.eleccion, **extra}
            return {"ok": False, "error": f"comando desconocido: {verbo[:20]!r}"}
        except Exception as e:  # JSONDecodeError, RecursionError por JSON anidado... el hilo no debe morir
            if not isinstance(e, (ValueError, TypeError)):   # los de validación son rutina; el resto, no
                print(f"[cmp-{self.lado}] comando FEC fallido: {e!r}"[:200], flush=True)
            return {"ok": False, "error": str(e)[:200] or type(e).__name__}
