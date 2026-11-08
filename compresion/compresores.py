#!/usr/bin/env python3
"""
compresores.py — Compresión por paquete para el plano de usuario 5G (solo biblioteca estándar).

Es la versión "para la red" del códec del Lab 3 (UD.Lab3.TeoriaInformacion): sin barridos,
sin gráficas, sin NumPy, solo lo que tiene que correr en cada paquete. Corre igual en el
python:3.14-alpine de los contenedores del lab que en un PC (en un Python sin zstd, el códec
zstd_dict no se registra y todo lo demás funciona igual).

Un paquete comprimido viaja así:

    [byte 0][cabecera ROHC-lite, si aplica][id de flujo, si códec = deflate_flujo][carga]
     bit 7 del byte 0 = 1 si la cabecera IP/UDP(/RTP) va comprimida
     bits 0-3          = códec de la carga (tabla CODECS)

Dos piezas independientes:

  1. Compresión de CABECERAS (CompresorCabeceras / DescompresorCabeceras): una versión
     reducida de ROHC (RFC 5795, perfiles RTP y UDP, modo U). Es lo que 3GPP pone en PDCP.
     Las cabeceras IP+UDP+RTP (40 B) cambian de forma predecible paquete a paquete, así que
     basta mandar un contexto una vez y luego solo lo que no se puede deducir.

  2. Compresión de CARGA (los códecs): eligen cómo comprimir los bytes que van detrás.
     Ninguno puede expandir más de lo que cuesta su marco: si la salida no es más chica que
     la entrada, se manda la carga tal cual (códec 0). Cada códec es una estrategia (clase
     Codec) anotada en el registro con @registrar: agregar un algoritmo es escribir una clase,
     sin tocar Compresor, Descompresor, el bandido ni evaluar.py.

Los códecs que necesitan modelo (Huffman estático, diccionario DEFLATE) lo cargan de un
Modelo entrenado offline con tráfico de entrenamiento — el mismo archivo en los dos
extremos, igual que la tabla de Huffman estática de HPACK en HTTP/2.
"""
from __future__ import annotations

import base64, heapq, json, struct, zlib
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Optional

# ═══════════════════════════ ids de los códecs de carga ═══════════════════════════
# El id viaja en los bits 0-3 del byte 0: caben 16 códecs (0-15). Son parte del formato del
# marco, así que un id ya usado no se reasigna. Cada id lo implementa una estrategia (más abajo).
NINGUNO, HUFF_ESTATICO, DEFLATE, DEFLATE_DICT, DEFLATE_FLUJO, TIC_PAQUETE, ZSTD_DICT = range(7)
PREDICTIVO_IOT = 7  # con pérdida: ver PredictivoIoT
MAX_CODECS = 16
CODECS: dict[int, str] = {}  # id -> nombre de los códecs SIN pérdida; lo llena @registrar
# Los códecs con pérdida (con_perdida = True) van aparte: el receptor los sabe descomprimir, pero
# no entran al bandido ni a las comparaciones que exigen recuperar el paquete exacto.
CODECS_CON_PERDIDA: dict[int, str] = {}
FIN = 256  # símbolo de fin de mensaje del Huffman estático
COLA_SYNC = b"\x00\x00\xff\xff"  # lo que deja Z_SYNC_FLUSH al final; no hace falta mandarlo

# zstd: en Python 3.14+ viene en la biblioteca estándar (compression.zstd, PEP 784), que es lo
# que usan los contenedores. En un PC con Python más viejo sirve el paquete `zstandard`. Sin
# ninguno de los dos, el códec zstd_dict simplemente no se registra.
try:
    from compression import zstd as _zstd
    ZSTD = "stdlib"
except ImportError:
    try:
        import zstandard as _zstd
        ZSTD = "zstandard"
    except ImportError:
        _zstd, ZSTD = None, None
MAGIA_ZSTD = b"\x28\xb5\x2f\xfd"  # 4 bytes fijos al inicio de todo marco zstd: no se mandan
# Nivel medido sobre el conjunto de ENTRENAMIENTO (diccionario de 4 KB): nivel 3 ahorra 21,3 %
# a 22 µs/paq, nivel 9 ahorra 22,2 % a 43 µs, nivel 19 ahorra 22,4 % a 124 µs. El 9 es el codo.
NIVEL_ZSTD = 9


# ═══════════════════════════ Huffman canónico (sin NumPy) ═══════════════════════════
def longitudes_huffman(frec: list[int]) -> list[int]:
    """Longitud del código de cada símbolo (mismo algoritmo que HuffmanCanonico del Lab 3)."""
    U = len(frec)
    if U == 1:
        return [1]
    heap = [(f, i) for i, f in enumerate(frec)]
    heapq.heapify(heap)
    padre, sig = [0] * (2 * U - 1), U
    while len(heap) > 1:
        f1, a = heapq.heappop(heap)
        f2, b = heapq.heappop(heap)
        padre[a] = padre[b] = sig
        heapq.heappush(heap, (f1 + f2, sig))
        sig += 1
    prof = [0] * (2 * U - 1)
    for nodo in range(2 * U - 3, -1, -1):
        prof[nodo] = prof[padre[nodo]] + 1
    return prof[:U]


def codigos_canonicos(lens: list[int]) -> list[int]:
    orden = sorted(range(len(lens)), key=lambda i: (lens[i], i))
    codigos, code, prev = [0] * len(lens), 0, lens[orden[0]]
    for i in orden:
        code <<= lens[i] - prev
        codigos[i] = code
        code += 1
        prev = lens[i]
    return codigos


class Huffman:
    """Código canónico sobre un alfabeto de símbolos 0..U-1, con decodificación por tabla
    (longitud, código) -> símbolo."""

    def __init__(self, lens: list[int]):
        self.lens = lens
        self.codigos = codigos_canonicos(lens)
        self.tabla = {(l, c): s for s, (l, c) in enumerate(zip(lens, self.codigos))}
        self.lmin, self.lmax = min(lens), max(lens)

    def codificar(self, simbolos) -> tuple[int, int]:
        """Devuelve (entero con los bits, cantidad de bits)."""
        acc, nb, L, C = 0, 0, self.lens, self.codigos
        for s in simbolos:
            acc = (acc << L[s]) | C[s]
            nb += L[s]
        return acc, nb

    def decodificar(self, datos: bytes, nbits: int, n: int | None = None, fin: int | None = None) -> list[int]:
        """Lee hasta n símbolos o hasta el símbolo `fin`."""
        acc = int.from_bytes(datos, "big")
        total = len(datos) * 8
        out, code, l, pos, tabla = [], 0, 0, 0, self.tabla
        while pos < nbits:
            code = (code << 1) | ((acc >> (total - 1 - pos)) & 1)
            pos += 1; l += 1
            s = tabla.get((l, code))
            if s is not None:
                if s == fin:
                    break
                out.append(s)
                if n is not None and len(out) == n:
                    break
                code, l = 0, 0
        return out


def entropia(datos: bytes) -> float:
    """H en bits/byte (k = 8) de un trozo de bytes."""
    import math
    n, frec = len(datos), {}
    for b in datos:
        frec[b] = frec.get(b, 0) + 1
    return -sum(f / n * math.log2(f / n) for f in frec.values())


def entropia_relativa(datos: bytes) -> float:
    """H dividida por el máximo alcanzable con ese largo. En una carga corta la entropía
    medida nunca llega a 8 (con 33 bytes el máximo es log2(33) ≈ 5,04), así que comparar
    H contra 8 haría parecer "compresible" a cualquier paquete chico de bytes aleatorios."""
    import math
    if len(datos) < 2:
        return 0.0
    return entropia(datos) / min(8.0, math.log2(len(datos)))


# Por encima de esto una carga se considera ya comprimida/cifrada.
UMBRAL_ENTRENO = 0.85


def bits_a_bytes(acc: int, nb: int) -> bytes:
    relleno = (-nb) % 8
    return (acc << relleno).to_bytes((nb + relleno) // 8, "big")


# ═══════════════════════════ modelo entrenado ═══════════════════════════
class Modelo:
    """Lo que comparten compresor y descompresor: la tabla de Huffman estática (257
    longitudes: 256 bytes + FIN), el diccionario de DEFLATE y el diccionario de zstd. Se
    entrena offline."""

    TAM_DICT = 4096

    def __init__(self, lens: list[int], diccionario: bytes, zstd_dict: bytes = b""):
        self.huff = Huffman(lens)
        self.diccionario = diccionario
        # Diccionario ENTRENADO por zstd (no solo texto: trae también tablas de entropía ya
        # ajustadas al tráfico). Vacío si el modelo se entrenó sin zstd disponible.
        self.zstd_dict = zstd_dict

    @classmethod
    def entrenar(cls, cargas: list[bytes]) -> "Modelo":
        # Las cargas ya comprimidas o cifradas (H ≈ 8 bits/byte) no aportan nada que aprender
        # y aplanarían la tabla: se descartan por su entropía, sin necesidad de etiquetas.
        cargas = [c for c in cargas if c and entropia_relativa(c) < UMBRAL_ENTRENO]
        # Huffman: frecuencias de byte en las cargas de entrenamiento, +1 para que todo byte
        # tenga código (si no, un byte nunca visto no se podría codificar).
        frec = [1] * 257
        for c in cargas:
            for b in c:
                frec[b] += 1
        frec[FIN] = max(1, len(cargas))
        lens = longitudes_huffman(frec)
        # Diccionario DEFLATE: subcadenas que más se repiten entre paquetes. DEFLATE busca
        # coincidencias hacia atrás, así que lo más útil va al FINAL (distancias cortas).
        conteo: dict[bytes, int] = {}
        for c in cargas:
            vistos = set()
            for n in (8, 16, 32):
                for i in range(0, max(0, len(c) - n + 1), 4):
                    s = c[i:i + n]
                    if s not in vistos:
                        vistos.add(s); conteo[s] = conteo.get(s, 0) + 1
        mejores = sorted((s for s, k in conteo.items() if k >= 3),
                         key=lambda s: conteo[s] * len(s), reverse=True)
        elegidos, tam = [], 0
        for s in mejores:
            if any(s in e for e in elegidos):
                continue
            elegidos.append(s); tam += len(s)
            if tam >= cls.TAM_DICT:
                break
        # zstd entrena su propio diccionario (algoritmo COVER) con las mismas cargas. Mismo
        # tamaño que el de DEFLATE, para que la comparación sea justa.
        zd = zstd_entrenar(cargas, cls.TAM_DICT) if ZSTD else b""
        return cls(lens, b"".join(reversed(elegidos))[-cls.TAM_DICT:], zd)

    def guardar(self, ruta: str | Path) -> None:
        Path(ruta).write_text(json.dumps({
            "huffman_longitudes": self.huff.lens,
            "diccionario_b64": base64.b64encode(self.diccionario).decode(),
            "zstd_diccionario_b64": base64.b64encode(self.zstd_dict).decode(),
        }))

    @classmethod
    def cargar(cls, ruta: str | Path) -> "Modelo":
        d = json.loads(Path(ruta).read_text())
        return cls(d["huffman_longitudes"], base64.b64decode(d["diccionario_b64"]),
                   base64.b64decode(d.get("zstd_diccionario_b64", "")))


# ═══════════════════════════ códecs de carga ═══════════════════════════
def _deflate(datos: bytes, zdict: bytes | None = None) -> bytes:
    c = zlib.compressobj(9, zlib.DEFLATED, -15, 9, zlib.Z_DEFAULT_STRATEGY, *( [zdict] if zdict else []))
    return c.compress(datos) + c.flush()


def _inflate(datos: bytes, zdict: bytes | None = None) -> bytes:
    d = zlib.decompressobj(-15, *([zdict] if zdict else []))
    return d.decompress(datos) + d.flush()


def zstd_entrenar(muestras: list[bytes], tam: int) -> bytes:
    """Diccionario zstd entrenado con `muestras` (bytes listos para guardar en el Modelo)."""
    if ZSTD == "stdlib":
        return _zstd.train_dict(muestras, tam).dict_content
    return _zstd.train_dictionary(tam, muestras).as_bytes()


class _Zstd:
    """zstd con diccionario y el marco más corto posible: sin checksum (el túnel ya lo tiene),
    sin tamaño, sin id de diccionario (los dos extremos tienen el mismo Modelo) y sin los 4
    bytes mágicos, que se vuelven a poner al descomprimir (como COLA_SYNC en DEFLATE)."""

    def __init__(self, dicc: bytes, nivel: int):
        if ZSTD == "stdlib":
            P = _zstd.CompressionParameter
            self._zd = _zstd.ZstdDict(dicc)
            self._c = _zstd.ZstdCompressor(options={P.compression_level: nivel, P.checksum_flag: 0,
                                                    P.content_size_flag: 0, P.dict_id_flag: 0},
                                           zstd_dict=self._zd)
        else:
            zd = _zstd.ZstdCompressionDict(dicc)
            self._c = _zstd.ZstdCompressor(level=nivel, dict_data=zd, write_checksum=False,
                                           write_content_size=False, write_dict_id=False)
            self._d = _zstd.ZstdDecompressor(dict_data=zd)

    def comprimir(self, datos: bytes) -> bytes:
        if ZSTD == "stdlib":
            out = self._c.compress(datos, _zstd.ZstdCompressor.FLUSH_FRAME)
        else:
            out = self._c.compress(datos)
        assert out.startswith(MAGIA_ZSTD)
        return out[4:]

    def descomprimir(self, datos: bytes) -> bytes:
        if ZSTD == "stdlib":
            return _zstd.decompress(MAGIA_ZSTD + datos, zstd_dict=self._zd)
        return self._d.decompress(MAGIA_ZSTD + datos, max_output_size=1 << 16)


def tic_codificar(datos: bytes) -> bytes:
    """El enfoque del Lab 3 llevado a un solo paquete: Huffman ADAPTATIVO (la tabla se
    calcula con ese mismo paquete y viaja con él) con barrido de k ∈ {8, 16} bits por
    símbolo, quedándose con el más chico. La cabecera ya es binaria y mínima, no el JSON
    del .tic: [k/8][largo 2B][U 2B][U × (símbolo, longitud)][bits]."""
    mejor = None
    for kb in (1, 2):
        rel = (-len(datos)) % kb
        d = datos + b"\0" * rel
        simb = [d[i:i + kb] for i in range(0, len(d), kb)]
        alfabeto = sorted(set(simb))
        idx = {s: i for i, s in enumerate(alfabeto)}
        frec = [0] * len(alfabeto)
        for s in simb:
            frec[idx[s]] += 1
        lens = longitudes_huffman(frec)
        acc, nb = Huffman(lens).codificar(idx[s] for s in simb)
        cab = struct.pack("!BHH", kb, len(datos), len(alfabeto))
        cab += b"".join(s + bytes([l]) for s, l in zip(alfabeto, lens))
        out = cab + bits_a_bytes(acc, nb)
        if mejor is None or len(out) < len(mejor):
            mejor = out
    return mejor


def tic_decodificar(datos: bytes) -> bytes:
    kb, largo, U = struct.unpack("!BHH", datos[:5])
    pos, alfabeto, lens = 5, [], []
    for _ in range(U):
        alfabeto.append(datos[pos:pos + kb]); lens.append(datos[pos + kb]); pos += kb + 1
    n = -(-largo // kb)
    simb = Huffman(lens).decodificar(datos[pos:], (len(datos) - pos) * 8, n=n)
    return b"".join(alfabeto[s] for s in simb)[:largo]


# ═══════════════════════════ estrategias de códec (patrón Strategy) ═══════════════════════════
Confirmar = Optional[Callable[[], None]]


class Codec:
    """Una estrategia de compresión de CARGA. Para agregar un algoritmo:

        @registrar
        class MiCodec(Codec):
            id, nombre = 6, "mi_codec"          # id libre de 0-15, nunca reutilizar uno
            def comprimir(self, carga, clave): return salida, None
            def descomprimir(self, datos): return carga

    y queda disponible en Compresor/Descompresor, en el bandido (si se agrega a sus brazos)
    y en todas las mediciones de evaluar.py, que recorren CODECS.

    Cada extremo crea SU instancia de cada códec, así que una estrategia puede guardar estado
    (p. ej. por flujo). Reglas para que emisor y receptor no se desincronicen:
      · comprimir() NO cambia el estado: si lo necesita, devuelve una función `confirmar`, que
        el Compresor llama solo si el marco sale con este códec (si la salida no achica, se
        manda con NINGUNO y el receptor nunca ve este paquete por este códec).
      · descomprimir() recibe exactamente los bytes que devolvió comprimir().
      · Lo que deba compartirse entre extremos (tablas, diccionarios) sale del Modelo."""

    id: int = -1
    nombre: str = ""
    titulo: str = ""       # nombre corto y legible (menú de Grafana)
    descripcion: str = ""  # qué hace, en una o dos frases (panel de Grafana)
    con_perdida = False    # True: el receptor recibe una versión aproximada (ver PredictivoIoT)

    def __init__(self, modelo: "Modelo", **opciones):
        self.modelo = modelo

    def comprimir(self, carga: bytes, clave: tuple) -> tuple[bytes, Confirmar]:
        raise NotImplementedError

    def descomprimir(self, datos: bytes) -> bytes:
        raise NotImplementedError


REGISTRO: dict[int, type[Codec]] = {}


def registrar(cls: type[Codec]) -> type[Codec]:
    if not 0 <= cls.id < MAX_CODECS:
        raise ValueError(f"{cls.__name__}: id {cls.id} fuera de 0-{MAX_CODECS - 1}")
    if cls.id in REGISTRO and REGISTRO[cls.id] is not cls:
        raise ValueError(f"{cls.__name__}: el id {cls.id} ya es de {REGISTRO[cls.id].__name__}")
    REGISTRO[cls.id] = cls
    CODECS.clear(); CODECS_CON_PERDIDA.clear()
    for i in sorted(REGISTRO):
        (CODECS_CON_PERDIDA if REGISTRO[i].con_perdida else CODECS)[i] = REGISTRO[i].nombre
    return cls


def nombre_codec(i: int) -> str:
    """Nombre de cualquier códec registrado, con o sin pérdida."""
    return CODECS.get(i) or CODECS_CON_PERDIDA.get(i) or f"codec_{i}"


def instanciar(modelo: "Modelo", **opciones) -> dict[int, Codec]:
    """Una instancia de cada códec registrado, para un extremo."""
    return {i: REGISTRO[i](modelo, **opciones) for i in sorted(REGISTRO)}


@registrar
class Ninguno(Codec):
    id, nombre = NINGUNO, "ninguno"
    titulo = "Ninguno"
    descripcion = "La carga viaja tal cual. Es lo correcto para tráfico cifrado o ya comprimido."

    def comprimir(self, carga, clave):
        return carga, None

    def descomprimir(self, datos):
        return datos


@registrar
class HuffmanEstatico(Codec):
    """Tabla fija entrenada offline (como la de HPACK), terminada con el símbolo FIN."""
    id, nombre = HUFF_ESTATICO, "huffman_estatico"
    titulo = "Huffman estático"
    descripcion = ("Huffman con una tabla fija entrenada antes con tráfico (como HPACK en HTTP/2). "
                   "No manda la tabla en cada paquete.")

    def comprimir(self, carga, clave):
        acc, nb = self.modelo.huff.codificar(list(carga) + [FIN])
        return bits_a_bytes(acc, nb), None

    def descomprimir(self, datos):
        return bytes(self.modelo.huff.decodificar(datos, len(datos) * 8, fin=FIN))


@registrar
class Deflate(Codec):
    id, nombre = DEFLATE, "deflate"
    titulo = "DEFLATE"
    descripcion = "El de zip/gzip (LZ77 + Huffman). Cada paquete se comprime solo, sin ayuda."

    def comprimir(self, carga, clave):
        return _deflate(carga), None

    def descomprimir(self, datos):
        return _inflate(datos)


@registrar
class DeflateDiccionario(Codec):
    """DEFLATE con el diccionario de 4 KB del Modelo: sin estado, cada paquete se decodifica solo."""
    id, nombre = DEFLATE_DICT, "deflate_dict"
    titulo = "DEFLATE con diccionario"
    descripcion = ("DEFLATE que arranca de un diccionario de 4 KB entrenado con tráfico. Sin estado: "
                   "una pérdida no afecta a otros paquetes.")

    def comprimir(self, carga, clave):
        return _deflate(carga, self.modelo.diccionario), None

    def descomprimir(self, datos):
        return _inflate(datos, self.modelo.diccionario)


@registrar
class DeflateFlujo(Codec):
    """DEFLATE con estado por flujo (hasta 128 a la vez), arrancando del diccionario del Modelo.
    Va antecedido de 1 byte: id de flujo (bits 0-6) y bit 7 = "flujo nuevo o reiniciado", para
    que el receptor arranque un descompresor limpio.

    reinicio_flujo = N > 0: el flujo arranca de cero cada N paquetes. Cuesta algo de razón,
    pero una pérdida solo arruina hasta el siguiente reinicio (sin canal de vuelta no hay otra
    forma de resincronizar, igual que ROHC en modo U)."""
    id, nombre = DEFLATE_FLUJO, "deflate_flujo"
    titulo = "DEFLATE por flujo (tipo UDC de 3GPP)"
    descripcion = ("DEFLATE que recuerda los paquetes anteriores de la misma conexión, como el UDC de "
                   "3GPP. Es el que más ahorra, pero una pérdida daña a los siguientes del flujo.")

    def __init__(self, modelo, reinicio_flujo: int = 0, **opciones):
        super().__init__(modelo)
        self.reinicio_flujo = reinicio_flujo
        # emisor: clave de flujo -> (fid, compressobj, paquetes desde el último reinicio)
        self.tx: OrderedDict[tuple, tuple[int, object, int]] = OrderedDict()
        self.sig_fid = 0
        self.rx: dict[int, object] = {}  # receptor: fid -> decompressobj

    def comprimir(self, carga, clave):
        ent = self.tx.get(clave)
        nuevo = ent is None or (self.reinicio_flujo and ent[2] >= self.reinicio_flujo)
        if nuevo:
            fid = self.sig_fid % 128 if ent is None else ent[0]
            c = zlib.compressobj(9, zlib.DEFLATED, -15, 9, zlib.Z_DEFAULT_STRATEGY, self.modelo.diccionario)
            cuenta = 0
        else:
            fid, c, cuenta = ent
        # Se trabaja sobre una copia: si al final no se usa este códec, el estado no avanza.
        c2 = c.copy()
        out = c2.compress(carga) + c2.flush(zlib.Z_SYNC_FLUSH)
        assert out.endswith(COLA_SYNC)

        def confirmar():
            if ent is None:
                self.sig_fid += 1
                for k, (f, _, _) in list(self.tx.items()):
                    if f == fid:  # el id se recicla: el flujo viejo pierde su estado
                        del self.tx[k]
            self.tx[clave] = (fid, c2, cuenta + 1)
        return bytes([fid | (0x80 if nuevo else 0)]) + out[:-4], confirmar

    def descomprimir(self, datos):
        fid, nuevo = datos[0] & 0x7F, datos[0] & 0x80
        if nuevo or fid not in self.rx:
            self.rx[fid] = zlib.decompressobj(-15, self.modelo.diccionario)
        return self.rx[fid].decompress(datos[1:] + COLA_SYNC)


@registrar
class TicPorPaquete(Codec):
    """El códec del Lab 3 llevado a un paquete (Huffman adaptativo, k ∈ {8, 16}). Se conserva
    para comparar: en paquetes casi siempre expande (ver docs/08)."""
    id, nombre = TIC_PAQUETE, "tic_por_paquete"
    titulo = "TIC del Lab 3 por paquete"
    descripcion = ("El códec del Lab 3 aplicado a cada paquete: Huffman adaptativo con k = 8 o 16 bits y "
                   "la tabla dentro del paquete. Se deja para comparar: en paquetes casi no ahorra.")

    def comprimir(self, carga, clave):
        return tic_codificar(carga), None

    def descomprimir(self, datos):
        return tic_decodificar(datos)


class ZstdDiccionario(Codec):
    """zstd (Zstandard, RFC 8878) con un diccionario entrenado por el propio zstd. Es lo que se
    usa hoy para mensajes cortos (RocksDB, HTTP Compression Dictionary Transport). Sin estado,
    igual que deflate_dict: cada paquete se decodifica solo, así que una pérdida no se arrastra."""
    id, nombre = ZSTD_DICT, "zstd_dict"
    titulo = "zstd con diccionario"
    descripcion = ("Zstandard con un diccionario entrenado con tráfico (lo actual para mensajes cortos). "
                   "Sin estado: una pérdida no afecta a otros paquetes.")

    def __init__(self, modelo, **opciones):
        super().__init__(modelo)
        dicc = getattr(modelo, "zstd_dict", b"")
        self.z = _Zstd(dicc, NIVEL_ZSTD) if dicc else None

    def comprimir(self, carga, clave):
        if self.z is None:  # modelo sin diccionario zstd: no achica, el Compresor manda NINGUNO
            return carga, None
        return self.z.comprimir(carga), None

    def descomprimir(self, datos):
        return self.z.descomprimir(datos)


if ZSTD:
    registrar(ZstdDiccionario)


# ═══════════════════════════ con pérdida: predictivo para telemetría ═══════════════════════════
# Paso de cuantización por campo: el error de reconstrucción queda en ≤ paso/2. Los campos que no
# aparecen aquí (enteros como ts o rssi, y el texto) viajan exactos.
PASOS_IOT = {"temp": 0.05, "hum": 0.5, "bat": 0.005}


def pasos_desde_texto(texto: str | None) -> dict[str, float]:
    """"temp=0.1,hum=1" -> {"temp": 0.1, "hum": 1.0}. Vacío o None -> PASOS_IOT. Un número solo
    ("2") escala todos los pasos por defecto."""
    if not texto:
        return dict(PASOS_IOT)
    texto = texto.strip()
    try:
        return {k: v * float(texto) for k, v in PASOS_IOT.items()}
    except ValueError:
        pass
    pasos = {}
    for par in texto.split(","):
        k, v = par.split("=")
        pasos[k.strip()] = float(v)
    return pasos


def _varint(n: int) -> bytes:
    z = (n << 1) ^ (n >> 63)  # zigzag: 0,-1,1,-2,... -> 0,1,2,3,... (los negativos chicos ocupan poco)
    out = bytearray()
    while True:
        b = z & 0x7F
        z >>= 7
        if z:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _leer_varint(d: bytes, p: int) -> tuple[int, int]:
    z = s = 0
    while True:
        b = d[p]; p += 1
        z |= (b & 0x7F) << s; s += 7
        if not b & 0x80:
            return (z >> 1) ^ -(z & 1), p


def _decimales(v: float) -> int:
    r = repr(v)
    return len(r.split(".")[1]) if "." in r and "e" not in r else 0


class _EsquemaIoT:
    """Lo que emisor y receptor recuerdan de un flujo: el orden de las claves, el tipo y paso de
    cada campo numérico, y los últimos valores RECONSTRUIDOS (no los originales: así los dos
    extremos predicen lo mismo y el error no se acumula — DPCM de lazo cerrado)."""
    __slots__ = ("claves", "pasos", "decs", "ultimo", "previo", "cuenta")

    def __init__(self, obj: dict, pasos: dict[str, float]):
        self.claves = list(obj)
        self.pasos, self.decs = {}, {}
        for k, v in obj.items():
            if isinstance(v, float):
                # Sin paso configurado: 1e-6, en la práctica exacto para lecturas de 2 o 3 decimales.
                p = pasos.get(k, 1e-6)
                # Se redondea a 6 decimales solo para limpiar el ruido de coma flotante (21.53 + 0.05
                # no da exacto 21.58 en binario). Redondear a menos decimales (los del paso) sumaría
                # error: el valor del marco clave puede tener más decimales que el paso.
                self.decs[k] = 6
                # El paso viaja como float32: el emisor usa ese mismo valor redondeado para que los
                # dos extremos hagan exactamente la misma cuenta.
                self.pasos[k] = struct.unpack("!f", struct.pack("!f", p))[0]
            elif isinstance(v, int) and not isinstance(v, bool):
                self.pasos[k] = 1
        self.ultimo = dict(obj)
        self.previo = dict(obj)
        self.cuenta = 0

    def compatible(self, obj: dict) -> bool:
        return list(obj) == self.claves and all(
            (isinstance(obj[k], float) if k in self.decs else
             isinstance(obj[k], int) and not isinstance(obj[k], bool) if k in self.pasos else
             isinstance(obj[k], str)) for k in self.claves)

    def prediccion(self, k):
        if k in self.decs:
            return self.ultimo[k]                               # flotante: el último valor
        return self.ultimo[k] + (self.ultimo[k] - self.previo[k])  # entero: sigue la tendencia (ts + 1)

    def avanzar(self, rec: dict):
        self.previo, self.ultimo = self.ultimo, rec
        self.cuenta += 1


def _json(obj: dict) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode()


class PredictivoIoT(Codec):
    """Compresión CON PÉRDIDA acotada para telemetría JSON (iot_json, mMTC).

    Cada campo numérico se predice con el último valor reconstruido del mismo flujo y solo se
    manda el error de predicción cuantizado con un paso por campo (PASOS_IOT): el receptor
    recupera cada valor con un error ≤ paso/2. Los enteros y el texto viajan exactos.

      clave (marco completo):  [fid | 0x80] [pasos: n × float32] [JSON original, DEFLATE con diccionario]
      delta:                   [fid] [mapa de textos cambiados] [error cuantizado de cada número, varint]

    Un sensor que reporta temperatura, humedad y batería cabe en ~6 bytes en vez de ~110. Cada
    `reinicio` paquetes (16, como deflate_flujo) va un marco completo, exacto: si se perdió un
    delta, el receptor se resincroniza ahí. Si la carga no es un objeto JSON plano que se
    reproduzca igual con json.dumps, se devuelve tal cual y el Compresor manda NINGUNO."""
    id, nombre = PREDICTIVO_IOT, "predictivo_iot"
    titulo = "Predictivo con pérdida para IoT"
    descripcion = ("Para telemetría JSON: predice cada medida con la anterior y manda solo el error "
                   "cuantizado. Pierde precisión de forma acotada (p. ej. ±0,025 °C) a cambio de ~90 % de ahorro.")
    con_perdida = True
    REINICIO = 16

    def __init__(self, modelo, pasos_iot: dict | None = None, **opciones):
        super().__init__(modelo)
        self.pasos = dict(PASOS_IOT if pasos_iot is None else pasos_iot)
        self.tx: OrderedDict[tuple, tuple[int, _EsquemaIoT]] = OrderedDict()
        self.sig_fid = 0
        self.rx: dict[int, _EsquemaIoT] = {}

    # ---- emisor ----
    @staticmethod
    def _objeto(carga: bytes):
        try:
            obj = json.loads(carga)
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(obj, dict) or any(isinstance(v, (dict, list)) for v in obj.values()):
            return None
        return obj if _json(obj) == carga else None   # solo si se reproduce byte a byte

    def _codificar(self, carga: bytes, clave: tuple):
        """-> (marco, carga reconstruida, función confirmar) o None si no aplica. No cambia estado."""
        obj = self._objeto(carga)
        if obj is None:
            return None
        ent = self.tx.get(clave)
        esq = ent[1] if ent else None
        if esq is None or esq.cuenta >= self.REINICIO or not esq.compatible(obj):
            fid = self.sig_fid % 128 if ent is None else ent[0]
            nuevo = _EsquemaIoT(obj, self.pasos)
            numericos = [k for k in nuevo.claves if k in nuevo.pasos]
            # Por campo numérico: paso (float32) + decimales (1 byte; 255 = entero).
            marco = (bytes([fid | 0x80, len(numericos)])
                     + b"".join(struct.pack("!fB", nuevo.pasos[k], nuevo.decs.get(k, 255)) for k in numericos)
                     + _deflate(carga, self.modelo.diccionario))

            def confirmar():
                if ent is None:
                    self.sig_fid += 1
                    for k2, (f, _) in list(self.tx.items()):
                        if f == fid:
                            del self.tx[k2]
                self.tx[clave] = (fid, nuevo)
            return marco, carga, confirmar        # el marco clave es exacto
        fid = ent[0]
        rec, mapa, cuerpo = {}, 0, bytearray()
        textos = [k for k in esq.claves if k not in esq.pasos]
        for i, k in enumerate(textos):
            if obj[k] != esq.ultimo[k]:
                mapa |= 1 << i
                b = obj[k].encode()
                cuerpo += _varint(len(b)) + b
            rec[k] = obj[k]
        for k in esq.claves:
            if k not in esq.pasos:
                continue
            pred, paso = esq.prediccion(k), esq.pasos[k]
            q = round((obj[k] - pred) / paso)
            cuerpo += _varint(q)
            rec[k] = round(pred + q * paso, esq.decs[k]) if k in esq.decs else pred + q
        rec = {k: rec[k] for k in esq.claves}
        marco = bytes([fid]) + _varint(mapa) + bytes(cuerpo)
        return marco, _json(rec), lambda: esq.avanzar(rec)

    def reconstruir(self, carga: bytes, clave: tuple) -> bytes:
        """Lo que va a recibir el receptor si esta carga se manda con este códec (sin cambiar estado)."""
        r = self._codificar(carga, clave)
        return carga if r is None else r[1]

    def comprimir(self, carga, clave):
        r = self._codificar(carga, clave)
        if r is None:
            return carga, None
        return r[0], r[2]

    # ---- receptor ----
    def descomprimir(self, datos):
        fid, es_clave = datos[0] & 0x7F, datos[0] & 0x80
        if es_clave:
            n = datos[1]
            campos = [struct.unpack("!fB", datos[2 + 5 * i:7 + 5 * i]) for i in range(n)]
            carga = _inflate(datos[2 + 5 * n:], self.modelo.diccionario)
            obj = json.loads(carga)
            esq = _EsquemaIoT(obj, {})
            for k, (p, dec) in zip([k for k in esq.claves if k in esq.pasos], campos):
                if k in esq.decs:
                    esq.pasos[k], esq.decs[k] = p, dec
            self.rx[fid] = esq
            return carga
        esq = self.rx[fid]   # KeyError si se perdió el marco clave: el paquete no se puede armar
        mapa, p = _leer_varint(datos, 1)
        rec = {}
        textos = [k for k in esq.claves if k not in esq.pasos]
        for i, k in enumerate(textos):
            if mapa >> i & 1:
                largo, p = _leer_varint(datos, p)
                rec[k] = datos[p:p + largo].decode(); p += largo
            else:
                rec[k] = esq.ultimo[k]
        for k in esq.claves:
            if k not in esq.pasos:
                continue
            q, p = _leer_varint(datos, p)
            pred, paso = esq.prediccion(k), esq.pasos[k]
            rec[k] = round(pred + q * paso, esq.decs[k]) if k in esq.decs else pred + q
        rec = {k: rec[k] for k in esq.claves}
        esq.avanzar(rec)
        return _json(rec)

    # ---- para medir ----
    @staticmethod
    def error_campos(original: bytes, recuperado: bytes) -> dict[str, float] | None:
        """Error absoluto por campo numérico entre dos cargas JSON, o None si no se pueden comparar."""
        try:
            a, b = json.loads(original), json.loads(recuperado)
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(a, dict) or not isinstance(b, dict) or list(a) != list(b):
            return None
        err = {}
        for k in a:
            if isinstance(a[k], (int, float)) and not isinstance(a[k], bool):
                if not isinstance(b[k], (int, float)):
                    return None
                err[k] = abs(a[k] - b[k])
            elif a[k] != b[k]:
                return None
        return err


registrar(PredictivoIoT)


# ═══════════════════════════ ROHC-lite (cabeceras) ═══════════════════════════
IR_UDP, IR_RTP = 0xFD, 0xFC
F_IPID, F_TS, F_SEQ, F_MARCA = 0x01, 0x02, 0x04, 0x08
REFRESCO = 64   # modo U: se reenvía el contexto completo cada 64 paquetes (sin canal de vuelta)
MAX_CTX = 256


def _analizar(pkt: bytes):
    """Si el paquete es IPv4 sin opciones ni fragmentar + UDP (+RTP v2 simple), devuelve sus
    campos; si no, None (la cabecera viaja sin comprimir)."""
    if len(pkt) < 28 or pkt[0] != 0x45 or pkt[9] != 17:
        return None
    total, ipid, ff = struct.unpack("!HHH", pkt[2:8])
    if total != len(pkt) or (ff & 0x3FFF):
        return None
    sport, dport, ulen, uck = struct.unpack("!HHHH", pkt[20:28])
    if ulen != len(pkt) - 20:
        return None
    f = dict(tos=pkt[1], ipid=ipid, ff=ff, ttl=pkt[8], src=pkt[12:16], dst=pkt[16:20],
             sport=sport, dport=dport, uck=uck, rtp=False)
    if len(pkt) >= 40 and pkt[28] == 0x80:
        seq, ts, ssrc = struct.unpack("!HII", pkt[30:40])
        f.update(rtp=True, pt=pkt[29] & 0x7F, marca=pkt[29] >> 7, seq=seq, ts=ts, ssrc=ssrc)
    return f


def _estaticos(f) -> tuple:
    return (f["tos"], f["ff"], f["ttl"], f["src"], f["dst"], f["sport"], f["dport"], f["rtp"],
            f.get("pt"), f.get("ssrc"))


class _Contexto:
    __slots__ = ("estat", "ipid", "seq", "ts", "paso", "desde_ir")

    def __init__(self, f):
        self.estat = _estaticos(f)
        self.ipid, self.seq, self.ts, self.paso, self.desde_ir = f["ipid"], f.get("seq"), f.get("ts"), 0, 0

    def avanzar(self, f) -> None:
        """Igual en compresor y descompresor: los dos ven los mismos valores completos."""
        if f["rtp"]:
            d = (f["seq"] - self.seq) & 0xFFFF
            dts = (f["ts"] - self.ts) & 0xFFFFFFFF
            if d and dts % d == 0:
                self.paso = dts // d
            self.seq, self.ts = f["seq"], f["ts"]
        self.ipid = f["ipid"]
        self.desde_ir += 1


class CompresorCabeceras:
    def __init__(self):
        self.ctx: OrderedDict[tuple, tuple[int, _Contexto]] = OrderedDict()
        self.sig_cid = 0

    def comprimir(self, pkt: bytes):
        """-> (cabecera comprimida, resto del paquete) o None si no aplica."""
        f = _analizar(pkt)
        if f is None:
            return None
        lh = 40 if f["rtp"] else 28
        clave = (f["src"], f["dst"], f["sport"], f["dport"], f.get("ssrc"))
        ent = self.ctx.get(clave)
        if ent is not None:
            self.ctx.move_to_end(clave)
        if ent is None or ent[1].estat != _estaticos(f) or ent[1].desde_ir >= REFRESCO:
            if ent is None:
                cid = self.sig_cid % MAX_CTX; self.sig_cid += 1
                if len(self.ctx) >= MAX_CTX:
                    self.ctx.popitem(last=False)
            else:
                cid = ent[0]
            self.ctx[clave] = (cid, _Contexto(f))
            return bytes([IR_RTP if f["rtp"] else IR_UDP, cid]) + pkt[:lh], pkt[lh:]
        cid, c = ent
        flags, extra = 0, b""
        if f["rtp"]:
            d = (f["seq"] - c.seq) & 0xFFFF
            if not 1 <= d <= 255:
                flags |= F_SEQ; extra += struct.pack("!H", f["seq"])
            if f["ipid"] != (c.ipid + d) & 0xFFFF:
                flags |= F_IPID; extra += struct.pack("!H", f["ipid"])
            if f["ts"] != (c.ts + c.paso * d) & 0xFFFFFFFF:
                flags |= F_TS; extra += struct.pack("!I", f["ts"])
            if f["marca"]:
                flags |= F_MARCA
            lsb = f["seq"] & 0xFF
        else:
            d = (f["ipid"] - c.ipid) & 0xFFFF
            if not 1 <= d <= 255:
                flags |= F_IPID; extra += struct.pack("!H", f["ipid"])
            lsb = f["ipid"] & 0xFF
        c.avanzar(f)
        return bytes([0x80 | flags, cid, lsb]) + struct.pack("!H", f["uck"]) + extra, pkt[lh:]


class DescompresorCabeceras:
    def __init__(self):
        self.ctx: dict[int, _Contexto] = {}
        self.cab: dict[int, bytes] = {}  # cabecera completa de referencia de cada contexto

    def descomprimir(self, buf: bytes, pos: int):
        """-> (nueva posición, función que arma el paquete dada la carga)."""
        tipo, cid = buf[pos], buf[pos + 1]
        if tipo in (IR_UDP, IR_RTP):
            lh = 40 if tipo == IR_RTP else 28
            cab = buf[pos + 2:pos + 2 + lh]
            self.cab[cid] = cab
            c = _Contexto(self._campos(cab))
            self.ctx[cid] = c
            return pos + 2 + lh, lambda resto: cab + resto
        c, ref = self.ctx[cid], self._campos(self.cab[cid])
        flags, lsb = tipo & 0x0F, buf[pos + 2]
        uck = struct.unpack("!H", buf[pos + 3:pos + 5])[0]
        p = pos + 5
        f = dict(ref, uck=uck)
        if ref["rtp"]:
            if flags & F_SEQ:
                f["seq"] = struct.unpack("!H", buf[p:p + 2])[0]; p += 2
            else:
                f["seq"] = (c.seq + ((lsb - c.seq) & 0xFF)) & 0xFFFF
            d = (f["seq"] - c.seq) & 0xFFFF
            if flags & F_IPID:
                f["ipid"] = struct.unpack("!H", buf[p:p + 2])[0]; p += 2
            else:
                f["ipid"] = (c.ipid + d) & 0xFFFF
            if flags & F_TS:
                f["ts"] = struct.unpack("!I", buf[p:p + 4])[0]; p += 4
            else:
                f["ts"] = (c.ts + c.paso * d) & 0xFFFFFFFF
            f["marca"] = 1 if flags & F_MARCA else 0
        else:
            if flags & F_IPID:
                f["ipid"] = struct.unpack("!H", buf[p:p + 2])[0]; p += 2
            else:
                f["ipid"] = (c.ipid + ((lsb - c.ipid) & 0xFF)) & 0xFFFF
        c.avanzar(f)
        return p, lambda resto: self._armar(f, resto)

    @staticmethod
    def _campos(cab: bytes) -> dict:
        ipid, ff = struct.unpack("!HH", cab[4:8])
        sport, dport, _, uck = struct.unpack("!HHHH", cab[20:28])
        f = dict(tos=cab[1], ipid=ipid, ff=ff, ttl=cab[8], src=cab[12:16], dst=cab[16:20],
                 sport=sport, dport=dport, uck=uck, rtp=len(cab) == 40)
        if f["rtp"]:
            seq, ts, ssrc = struct.unpack("!HII", cab[30:40])
            f.update(pt=cab[29] & 0x7F, marca=cab[29] >> 7, seq=seq, ts=ts, ssrc=ssrc)
        return f

    @staticmethod
    def _armar(f: dict, resto: bytes) -> bytes:
        rtp = b""
        if f["rtp"]:
            rtp = struct.pack("!BBHII", 0x80, (f["marca"] << 7) | f["pt"], f["seq"], f["ts"], f["ssrc"])
        ulen = 8 + len(rtp) + len(resto)
        ip = struct.pack("!BBHHHBBH4s4s", 0x45, f["tos"], 20 + ulen, f["ipid"], f["ff"], f["ttl"], 17, 0,
                         f["src"], f["dst"])
        ip = ip[:10] + struct.pack("!H", _ck(ip)) + ip[12:]
        return ip + struct.pack("!HHHH", f["sport"], f["dport"], ulen, f["uck"]) + rtp + resto


def _con_carga(pkt: bytes, carga: bytes) -> bytes:
    """El mismo paquete IPv4/UDP (cabecera de 20 + 8 B) con otra carga: rehace los largos y las
    dos sumas de verificación, igual que las calcularía el sistema operativo."""
    ulen = 8 + len(carga)
    ip = bytearray(pkt[:20])
    ip[2:4] = struct.pack("!H", 20 + ulen)
    ip[10:12] = b"\0\0"
    ip[10:12] = struct.pack("!H", _ck(bytes(ip)))
    udp = bytearray(pkt[20:28])
    udp[4:6] = struct.pack("!H", ulen)
    if udp[6:8] != b"\0\0":  # 0 = el emisor no usa suma UDP; se respeta
        udp[6:8] = b"\0\0"
        datos = bytes(ip[12:20]) + struct.pack("!BBH", 0, 17, ulen) + bytes(udp) + carga
        udp[6:8] = struct.pack("!H", _ck(datos + b"\0" * (len(datos) % 2)) or 0xFFFF)
    return bytes(ip) + bytes(udp) + carga


def _ck(d: bytes) -> int:
    s = sum(struct.unpack(f"!{len(d) // 2}H", d))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


# ═══════════════════════════ extremos completos ═══════════════════════════
def clave_flujo(pkt: bytes) -> tuple:
    """5-tupla del paquete (para el estado de deflate_flujo y para el bandido)."""
    if len(pkt) >= 24 and pkt[0] >> 4 == 4:
        return (pkt[12:20], pkt[9], pkt[20:24])
    return (b"", 0, b"")


class Compresor:
    """Un extremo emisor: cabeceras + códec de carga elegido por paquete."""

    def __init__(self, modelo: Modelo, cabeceras: bool = True, reinicio_flujo: int = 0,
                 perdida_iot: dict | None = None):
        """reinicio_flujo: ver DeflateFlujo. perdida_iot: pasos de cuantización (ver PASOS_IOT)
        para mandar la telemetría JSON con PredictivoIoT; None = todo sin pérdida. Las opciones
        llegan a todas las estrategias y cada una toma las que le sirven."""
        self.modelo = modelo
        self.hc = CompresorCabeceras() if cabeceras else None
        self.perdida_iot = perdida_iot
        self.codecs = instanciar(modelo, reinicio_flujo=reinicio_flujo, pasos_iot=perdida_iot)

    def preparar(self, pkt: bytes) -> tuple[bytes, int | None]:
        """Pérdida acotada para IoT: si está activa y el paquete es telemetría JSON por UDP,
        devuelve el paquete con la carga que va a reconstruir el receptor (y las sumas de
        verificación IP y UDP rehechas, si no el receptor lo descartaría) y el códec que hay que
        usar. Si no aplica: (pkt, None). No cambia estado."""
        if self.perdida_iot is None:
            return pkt, None
        f = _analizar(pkt)
        if f is None or f["rtp"] or not pkt[28:29] == b"{":
            return pkt, None
        estrategia = self.codecs[PREDICTIVO_IOT]
        r = estrategia._codificar(pkt[28:], clave_flujo(pkt))
        if r is None:
            return pkt, None
        return (pkt if r[1] == pkt[28:] else _con_carga(pkt, r[1])), PREDICTIVO_IOT

    def separar(self, pkt: bytes):
        """Comprime la cabecera (si aplica). -> (prefijo del marco, carga a comprimir)."""
        if self.hc is not None:
            r = self.hc.comprimir(pkt)
            if r is not None:
                return True, r[0], r[1]
        return False, b"", pkt

    def probar(self, codec: int, carga: bytes, clave: tuple):
        """Comprime la carga con un códec SIN confirmar estado. -> (bytes, confirmar)."""
        estrategia = self.codecs.get(codec)
        if estrategia is None:
            raise ValueError(f"códec desconocido {codec}")
        return estrategia.comprimir(carga, clave)

    def empaquetar(self, pkt: bytes, codec: int) -> tuple[bytes, int]:
        """Paquete completo -> (marco a transmitir, códec realmente usado)."""
        pkt, forzado = self.preparar(pkt)
        if forzado is not None:
            codec = forzado
        hc, pref, carga = self.separar(pkt)
        datos, confirmar = self.probar(codec, carga, clave_flujo(pkt))
        if codec != NINGUNO and len(datos) >= len(carga):
            codec, datos, confirmar = NINGUNO, carga, None
        if confirmar:
            confirmar()
        return bytes([(0x80 if hc else 0) | codec]) + pref + datos, codec


class Descompresor:
    def __init__(self, modelo: Modelo):
        self.modelo = modelo
        self.hc = DescompresorCabeceras()
        self.codecs = instanciar(modelo)

    def desempaquetar(self, marco: bytes) -> bytes:
        b0, pos = marco[0], 1
        armar = None
        if b0 & 0x80:
            pos, armar = self.hc.descomprimir(marco, pos)
        codec = b0 & 0x0F
        estrategia = self.codecs.get(codec)
        if estrategia is None:
            raise ValueError(f"códec desconocido {codec}")
        carga = estrategia.descomprimir(marco[pos:])
        return armar(carga) if armar else carga
