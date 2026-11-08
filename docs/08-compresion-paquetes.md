# 08 — Compresión de paquetes en la red 5G (con IA)

El Lab 3 de Teoría de la Información ([`UD.Lab3.TeoriaInformacion`](https://github.com/J4F3ET/UD.Lab3.TeoriaInformacion))
construyó un códec Huffman adaptable a k bits por símbolo y un bandido UCB que elige k.
Aquí se lleva esa idea a los **paquetes** de esta red 5G: dónde debería ir, si es viable tal
cual, qué hubo que cambiar y por qué.

**Resumen:**

1. El códec del Lab 3 tal cual **no es viable por paquete**: un paquete IoT de 92 B se vuelve un `.tic` de 300 B.
2. Lo que sí sirve es **comprimir cabeceras (ROHC) más un compresor con diccionario entrenado**: DEFLATE con estado por flujo (lo que 3GPP hace en PDCP con UDC) o **zstd con diccionario**, que no guarda estado y por eso aguanta pérdidas.
3. La IA (el mismo bandido UCB) sigue siendo útil, pero ahora elige **qué códec usar en cada paquete, incluido "ninguno"**, no k. Puede premiarse por bytes ahorrados o **por latencia**, sabiendo la velocidad del enlace (§4.5.1).
4. Resultado sobre tráfico de prueba que el modelo no vio: **21,0 %** menos bytes, y con 1 % de pérdida entrega **99,1 %** de la información (§4.6). El bandido solo le pasa al compresor el 40 % de los bytes; un códec fijo se los pasa todos.

Todo está en [`compresion/`](../compresion). Solo usa la biblioteca estándar de Python, así
que corre en `python:3.14-alpine` sin instalar nada (3.14 trae zstd en `compression.zstd`).

---

## 1. Dónde puede ir un compresor en 5G

```
 UE                         gNB                             UPF                    DN
 App ─ TCP/UDP ─ IP ─ SDAP ─ PDCP ─ RLC ─ MAC ─ PHY ~~radio~~ PHY…PDCP ─ GTP-U ══ N3 ══ GTP-U ─ IP ══ N6 ══ servidor
  (A) extremo a extremo        (B) PDCP: ROHC / UDC            (C) túnel N3          (D) N6
```

| | (A) Aplicación | **(B) PDCP, UE↔gNB** | (C) N3, gNB↔UPF | (D) N6, UPF↔DN | (E) Lo que se implementó: túnel UE↔DN |
|---|---|---|---|---|---|
| Enlace que ahorra | todos | **la radio**, el recurso escaso y caro | el backhaul (fibra), que no es escaso | internet | radio + N3 + N6 |
| ¿Ve la carga en claro? | sí, antes de cifrar | solo si la app no cifró (TLS/QUIC ya llegan cifrados) | igual que B | igual que B | igual que B |
| Pérdidas por debajo | TCP: ninguna | RLC AM retransmite: casi ninguna, así que **la compresión con estado es segura** | GTP-U sobre UDP: sí | sí | sí (UDP) |
| Estándar | HTTP `Content-Encoding`, CoAP/CBOR | **sí**: ROHC (TS 38.323), EHC, UDC (TS 36.323, LTE Rel-15) | no | no | no |
| Costo extra por paquete | 0 | 0 (va en los bits de cabecera de PDCP) | 0 | 0 | **28 B** de IP+UDP del túnel |
| ¿Se puede en este lab? | sí, pero no es "de la red" | no sin modificar UERANSIM (su "radio" es UDP y no implementa PDCP) | no sin modificar el módulo `gtp5g` del kernel | sí, pero no ahorra nada de 5G | **sí**, con dos contenedores |

**El lugar correcto es (B), PDCP.** Ahí la radio es el recurso que se paga, PDCP ya cifra
*después* de comprimir (el orden correcto), y RLC en modo con acuse de recibo hace que casi no
haya pérdidas. Sin pérdidas, un compresor con memoria entre paquetes (DEFLATE por flujo) no se
desincroniza (ver §4.4).

**En el lab se implementó (E)**, el equivalente funcional más cercano: la misma compresión,
con el compresor en el UE y el descompresor en la DN en vez del gNB. Paga 28 B por paquete de
túnel que en PDCP no existirían, y las medidas en `uesimtun0` lo reflejan (§5).

## 2. ¿Es viable el códec del Lab 3 tal cual? No, por cinco razones medidas

| Problema | Dato |
|---|---|
| **La cabecera `.tic` pesa más que el paquete.** Es JSON autocontenido, pensado para archivos de MB. | Un paquete IoT de 92 B → `.tic` de **300 B** con k = 8 y 305 B con k = 16 (medido con `tic_codec.py codificar`). |
| **Huffman adaptativo por paquete no se paga ni con cabecera binaria mínima.** La tabla viaja con cada paquete. | `tic_por_paquete` (cabecera binaria, barrido k ∈ {8, 16}): IoT **−0,8 %**, URLLC −2 %, VoIP −1,4 % (expande); texto 20,5 % frente a 60,6 % de DEFLATE por flujo. |
| **k deja de tener sentido.** En el Lab 3 el mejor k iba de 16 a 512 bits, porque los archivos tienen MB. | Un paquete tiene de 30 a 1400 B: con k = 2048 son 1 a 5 símbolos y no hay estadística que estimar. |
| **El barrido por paquete cuesta 5 veces la CPU y es miope.** Elige lo mejor para *este* paquete y nunca deja acumular estado al compresor por flujo. | Medido al diseñar: 5 bytes comprimidos por cada byte enviado, y solo 18,1 % de ahorro frente a 24,2 % de DEFLATE por flujo. Por eso se descartó y ya no se evalúa. |
| **La mayor parte del tráfico ya va cifrado o comprimido.** | TLS, video y voz salen con entropía relativa de 0,98 y ningún códec les saca nada (§4.1). Según el informe de transparencia de Google, más del 90 % de las páginas que se cargan en Chrome usan HTTPS. |

Además, la compresión **con pérdida** (`--perdida n` del Lab 3) no se puede usar en paquetes:
un bit cambiado en una cabecera o en un registro TLS invalida el paquete entero.

## 3. Qué se conservó del Lab 3, qué se cambió y por qué

| Pieza del Lab 3 | En la red | Por qué |
|---|---|---|
| Huffman canónico (`HuffmanCanonico`) | Se conserva en su versión **estática**: una tabla entrenada offline que tienen los dos extremos, igual que HPACK en HTTP/2. Quedó como opción medida, no en la configuración final. | Sin tabla en cada paquete ya no expande (29,7 % en IoT), pero DEFLATE con diccionario lo supera en todas las clases. |
| Entropía H | Se conserva como **entropía relativa** H / log2(min(256, n)): es rasgo de contexto del bandido y filtro de entrenamiento. | En 33 B de bytes aleatorios H nunca pasa de 5,04 bits; comparar contra 8 los haría parecer compresibles. |
| Bandido UCB con descuento | Se conserva la regla. Los **brazos** pasan a ser códecs y el **contexto**, tamaño y entropía del paquete. | k ya no significa nada (ver §2), y "ninguno" tiene que poder ganar. |
| Barrido (de k en el Lab 3; de códecs, probando todos en cada paquete, en la red) | **Se quita.** El bandido es la única política que elige. | En el Lab 3 servía para *leer* el comportamiento en muchos k; aquí lo que se mide es el costo en la red, y el barrido cuesta 5 veces la CPU y ahorra menos (§2). |
| Ajustar y evaluar sobre el mismo flujo (límite 5 del Lab 3) | **Se corrige**: se entrena y ajusta con la semilla 1 y los docs 01, 03, 05 y 07; se evalúa con la semilla 2 y los docs 02, 04 y 06. Son copias congeladas en `compresion/textos/`, así que editar la documentación no cambia los resultados. | Resultado no optimista. |
| NumPy, gráficas, bitstrip, `.tic`, `--perdida` | **Se quitan.** | Nada de eso hace falta, o puede ir, en el camino de cada paquete. |
| — | **Nuevo: ROHC-lite** (cabeceras IP/UDP/RTP, RFC 5795 simplificado, modo U). | En voz y URLLC las cabeceras son más de la mitad del paquete. |
| — | **Nuevo: DEFLATE con diccionario entrenado** (como UDC de LTE) y **DEFLATE por flujo** (con memoria entre paquetes). | Son los que ganan en todo lo compresible. |
| — | **Nuevo: zstd con diccionario entrenado** (Zstandard, RFC 8878; diccionario de 4 KB que entrena el propio zstd). | Lo que se usa hoy para mensajes cortos. Ahorra más que DEFLATE con diccionario con la mitad de CPU, y sin estado. |

## 4. Comparación (tráfico de prueba, 3600 paquetes, todo verificado byte a byte)

Reproducible con `python compresion/evaluar.py entrenar && python compresion/evaluar.py ajustar && python compresion/evaluar.py comparar`.
Tablas completas en [`compresion/resultados/resultados.md`](../compresion/resultados/resultados.md) y
el ajuste en [`ajuste.md`](../compresion/resultados/ajuste.md).

### 4.1 Por clase de tráfico (% de bytes ahorrados)

| Clase (slice) | B/paq | Solo ROHC | +Huffman estático | +DEFLATE | +DEFLATE dicc. | +zstd dicc. | **+DEFLATE flujo** | +TIC por paquete |
|---|---|---|---|---|---|---|---|---|
| iot_json (mMTC) | 122 | 17,6 | 29,7 | 26,3 | 60,8 | 54,8 | **81,8** | 17,6 |
| urllc_ctrl (URLLC) | 50 | **43,0** | 43,0 | 43,0 | 43,0 | 43,0 | 43,0 | 43,0 |
| voip_rtp (eMBB) | 73 | **45,5** | 45,5 | 45,5 | 45,5 | 45,5 | 45,5 | 45,5 |
| video_rtp (eMBB) | 1240 | 2,5 | 2,5 | 2,5 | 2,5 | 2,5 | 2,5 | 2,5 |
| web_tls (eMBB) | 1440 | −0,07 | −0,07 | −0,07 | −0,07 | −0,07 | −0,07 | −0,07 |
| http_texto (eMBB) | 1434 | −0,07 | 31,9 | 40,3 | 43,9 | 51,3 | **60,6** | 20,5 |

Lectura:

- En **URLLC y voz** todo el ahorro viene de las **cabeceras**. ROHC baja IP+UDP+RTP de 40 B a 5 B. La carga de voz ya viene comprimida por el códec AMR.
- En **IoT y texto** gana la **memoria entre paquetes**: los mensajes de un sensor se parecen mucho entre sí, aunque cada uno por separado casi no tenga redundancia. Por eso Huffman, que mira un paquete a la vez, se queda en 30 %.
- **TLS y video no se pueden comprimir.** Lo único que se pierde es el byte de marco (−0,07 %), porque todo códec cae al respaldo "sin comprimir".
- **zstd con diccionario es el mejor de los que no guardan estado.** En texto saca 51,3 % frente a 43,9 % de DEFLATE con diccionario; en IoT queda algo por debajo (54,8 % frente a 60,8 %). DEFLATE por flujo sigue ganando en las dos, pero a costa de la robustez (§4.4).

### 4.2 Flujo mezclado (las 6 clases intercaladas, como una celda)

| Política | Ahorro | Bytes que pasan por un compresor, por byte enviado |
|---|---|---|
| Sin compresión | 0 % | 0 |
| Solo ROHC | 2,4 % | 0 |
| ROHC + DEFLATE dicc. (fijo) | 18,1 % | 1 |
| ROHC + zstd dicc. (fijo) | 20,4 % | 1 |
| ROHC + DEFLATE flujo (fijo, sin reinicio) | 24,2 % | 1 |
| ROHC + DEFLATE flujo (fijo, reinicio cada 16) | 22,5 % | 1 |
| ROHC + bandido, 7 brazos, γ = 0,99 (los parámetros del Lab 3) | 21,4 % | 0,67 |
| ROHC + bandido, configuración anterior (ninguno, DEFLATE dicc., DEFLATE flujo; c = 0,05) | 22,4 % | 0,51 |
| **ROHC + bandido, configuración de red** (ninguno, zstd dicc., DEFLATE flujo; γ = 0,999, c = 0,02, reinicio cada 16) | **21,0 %** | **0,41** |

El bandido logra casi el mismo ahorro que el mejor códec fijo, pero **solo comprime el 40 % de
los bytes**: aprendió a no gastar CPU en TLS, video, voz ni URLLC. Ahí elige "ninguno" en unos
550 de cada 600 paquetes; el resto es exploración. En lo compresible aprendió un códec por clase:
**DEFLATE por flujo para IoT** (598 de 600) y **zstd para texto** (598 de 600).

La configuración de red salió de una rejilla sobre el conjunto de **entrenamiento**
([`ajuste.md`](../compresion/resultados/ajuste.md)): γ ∈ {0,99; 0,995; 0,999}, c ∈ {0,02; 0,05} y
cuatro juegos de brazos. El criterio, en este orden:

1. ahorro a no más de 0,5 puntos del mejor;
2. con 1 % de pérdida, a lo sumo 2 % de paquetes mal reconstruidos;
3. de las que cumplen, la que menos intenta comprimir. Esa es la medida de CPU que no tiene ruido:
   en esta máquina la misma configuración dio 59 µs por paquete en una corrida y 113 µs en la
   siguiente.

Con zstd en los brazos, el bandido ahorra **1,4 puntos menos** que la configuración anterior,
pero reconstruye mal **la mitad de paquetes** con pérdida (1,8 % frente a 3,5 %) y explora
menos. El criterio de antes, "mayor ahorro y luego menos CPU", no miraba la robustez y elegía
DEFLATE con diccionario. Quitar Huffman estático, DEFLATE sin diccionario y TIC, que nunca
fueron los mejores en ninguna clase, baja la CPU unas 4 veces sin perder ahorro.

### 4.3 Latencia por paquete (Python puro, µs, comprimir / descomprimir)

| Códec | iot_json | urllc_ctrl | http_texto |
|---|---|---|---|
| ninguno (solo ROHC) | 10 / 10 | 9 / 10 | 4 / 1 |
| DEFLATE dicc. | 49 / 15 | 44 / 10 | 107 / 19 |
| DEFLATE flujo | 35 / 13 | 62 / 11 | 201 / 21 |
| zstd dicc. | 22 / 18 | 22 / 13 | 105 / 13 |
| Huffman estático | 44 / 347 | 20 / 12 | 946 / 6321 |
| TIC por paquete | 341 / 16 | 99 / 12 | 5179 / 7837 |

DEFLATE es C dentro de `zlib`; Huffman y TIC están en Python puro, y por eso la diferencia es
de 10 a 50 veces. El presupuesto de URLLC es de 1 ms de punta a punta: un Huffman de 6 ms por
paquete en Python no entra, y en C tampoco ganaría en bytes. En PDCP real todo esto corre en C
o en hardware.

### 4.4 Robustez: 1 % de paquetes perdidos

| Caso | Paquetes recibidos que salen mal |
|---|---|
| ROHC + DEFLATE dicc. (sin estado) | 0,14 % |
| ROHC + zstd dicc. (sin estado) | 0,14 % |
| ROHC + DEFLATE flujo, sin reinicio | **26,5 %** |
| ROHC + DEFLATE flujo, reinicio cada 16 paquetes | 3,1 % |
| ROHC + bandido, configuración anterior (con DEFLATE dicc.) | 3,5 % |
| ROHC + bandido, configuración de red (con zstd dicc.) | 1,8 % |

Este es el límite más importante. Cuando se pierde un paquete, el descompresor por flujo queda
desincronizado hasta el siguiente reinicio. Reiniciar cada 16 paquetes cuesta 1,7 puntos de
ahorro y baja el daño de 26,5 % a unos 3 %. Sobre un enlace con pérdidas, eso sigue siendo
demasiado. **Por eso este compresor va en PDCP**, encima de RLC AM, que retransmite. UDC de LTE
hace lo mismo: es solo de subida, lleva un checksum y el gNB pide un reinicio por un PDU de
control. ROHC solo (0,14 %) aguanta pérdidas porque manda los bits menos significativos de la
secuencia.

### 4.5 El balance final: ¿comprimir baja o sube la latencia?

Comprimir **suma** tiempo de CPU y **resta** tiempo en el enlace y en la cola. Cuál de los dos
pesa más depende del enlace. `python evaluar.py balance` lo calcula para cada algoritmo
registrado, y cualquier estrategia nueva entra sola. Usa los tamaños y tiempos medidos de cada
paquete y trata compresor → enlace → descompresor como tres colas M/G/1 en serie
(Pollaczek–Khinchine). El detalle completo está en
[`resultados/balance.md`](../compresion/resultados/balance.md) y el modelo, en el encabezado de
[`balance.py`](../compresion/balance.py).

Latencia media del flujo mezclado, en ms, con la CPU medida (Python):

| Enlace | Carga | Sin compresión | Solo ROHC | DEFLATE dicc. | zstd dicc. | DEFLATE flujo | Bandido (ahorro) | Bandido (latencia) |
|---|---|---|---|---|---|---|---|---|
| 100 kbps | 80 % | 267 | 244 | 137 | 128 | **122** | 126 | 126 |
| 1 Mbps | 80 % | 26,7 | 24,5 | 13,8 | 12,8 | **12,3** | 12,7 | 12,7 |
| 10 Mbps | 80 % | 2,67 | 2,46 | 1,49 | 1,34 | 1,39 | 1,35 | **1,33** |
| 100 Mbps | 80 % | 0,267 | 0,267 | ∞ | **0,237** | ∞ | 0,791 | 0,282 |
| 100 Mbps | 95 % | 1,05 | 0,75 | ∞ | **0,333** | ∞ | ∞ | 0,762 |
| 1 Gbps | 50 % | **0,011** | 0,132 | ∞ | ∞ | ∞ | ∞ | ∞ |

∞ significa que el compresor en Python no alcanza a procesar todos los paquetes que llegan.

Lo que dice:

- **Enlace lento o cargado (≤ 10 Mbps): comprimir baja la latencia a la mitad o menos.** Los
  bytes que se ahorran pesan más que la CPU que se gasta, y la cola se encoge mucho porque la
  espera crece como 1/(1 − ocupación).
- **Enlace rápido (≥ 100 Mbps con Python): casi todo compresor suma latencia, o no da
  abasto.** La excepción es zstd con diccionario, que por ser el más barato todavía gana a
  100 Mbps con carga alta (0,33 ms frente a 1,05 ms sin comprimir). A 1 Gbps no gana nadie: ni
  siquiera ROHC en Python da abasto.
- **Con la CPU 10× más rápida** (el caso de C o del hardware de la estación base), la frontera
  sube un orden de magnitud: el bandido gana hasta ~100 Mbps.
- **Tráfico cifrado (TLS): ningún algoritmo gana a ninguna velocidad.** Por eso el bandido
  aprende a no comprimirlo.
- **Velocidad de equilibrio por clase** (carga 50 %, Python): VoIP y URLLC ganan con ROHC por
  debajo de ~13–15 Mbps; IoT con DEFLATE flujo, por debajo de ~11 Mbps; texto, por debajo de
  ~50 Mbps.
- **El ganador de menor latencia no siempre es el más robusto.** DEFLATE flujo reconstruye mal
  ~3 % de los paquetes con 1 % de pérdida y el bandido 1,8 %. Si se exige ≤ 1 %, el ganador en
  enlaces lentos pasa a ser zstd con diccionario, que no guarda estado.

Lo que el modelo deja fuera: la propagación, que es igual para todos, y el planificador de radio
de 5G. Este último premia todavía más a los paquetes chicos, así que el modelo es conservador a
favor de no comprimir. El lab no puede mostrar la ganancia tal cual, porque sus enlaces son
puentes de Docker de varios Gbps. Para verla habría que limitar `uesimtun0` con `tc`.

#### 4.5.1 El bandido premiado por latencia

El bandido normal se premia con `ahorro − 0,01`: no sabe nada del enlace, así que a 100 Mbps
sigue comprimiendo y suma latencia. El **bandido por latencia** es el mismo modelo, con los
mismos brazos, el mismo contexto y la misma regla UCB. Solo cambia el premio, que ahora usa un
dato más, la velocidad del enlace *v*:

```
r = (tiempo ganado en el enlace − tiempo de CPU) / tiempo de la carga sin comprimir
  = ahorro − t_cpu · v / (8 · bytes de la carga)
```

Es la fracción del tiempo de transmisión que se ahorra, ya descontado lo que se tardó en comprimir.
`t_cpu` se mide con el reloj del emisor y solo incluye comprimir, porque el emisor no ve cuánto
tarda el receptor. "Ninguno" vale 0. Cuando el enlace es lento, el término de CPU es despreciable
y gana el que más ahorra; cuando es rápido, la CPU pesa más que los bytes y gana "ninguno". Es la
cuenta de §4.5, pero aprendida en vivo, paquete a paquete.

Lo que aprende a cada velocidad (`balance.py`, CPU de Python):

| Enlace | Elige "ninguno" | Elige zstd | Elige DEFLATE flujo | Ahorro |
|---|---|---|---|---|
| 100 kbps | 47 % | 30 % | 23 % | 21,0 % |
| 10 Mbps | 76 % | 8 % | 17 % | 21,2 % |
| 100 Mbps | **99,7 %** | 0,2 % | 0,2 % | 2,5 % (solo ROHC) |

- **A 10 Mbps es la política de menor latencia de todas** (1,33 ms con 80 % de carga), por encima de
  cualquier códec fijo y del bandido normal.
- **A 100 Mbps deja de comprimir**, y por eso no se satura como el bandido normal (0,28 ms frente a
  0,79 ms con 80 % de carga; con 95 % el bandido normal ya no da abasto).
- **Límite:** el premio mide el tiempo de *servicio*, no el de *cola*. Con el enlace casi lleno
  (95 % a 100 Mbps), comprimir con zstd igual convendría porque achica la cola, y el bandido no
  lo ve. Para eso habría que darle también la ocupación del enlace.

En la red, el bandido por latencia se activa con la variable `VELOCIDAD_ENLACE_BPS` en `cmp-ue` y
`cmp-dn` (por ejemplo, `1000000` si `uesimtun0` se limita a 1 Mbps con `tc`). Sin definirla, el
bandido se premia por ahorro, como antes.

### 4.6 Original, comprimido y recuperado: la información mutua

Es el punto (c) del Lab 3 aplicado a 5G: por cada algoritmo, el paquete **original**, el
**comprimido** y el **recuperado**, y la información mutua **I(O;R)** entre el original y lo que
reconstruye el receptor. Lo calcula `python evaluar.py informacion`
([`informacion.py`](../compresion/informacion.py)). El detalle completo está en
[`resultados/informacion.md`](../compresion/resultados/informacion.md).

Cómo se estima I(O;R): byte a byte, alineando cada byte del original con el de la misma posición
del recuperado. Si el paquete no llegó, no se pudo descomprimir o salió más corto, esa posición
cuenta como un símbolo "borrado". Con la tabla conjunta de conteos se calcula
I = H(O) + H(R) − H(O,R). **H** es entropía de orden 0 en bits por byte.

Flujo mezclado (3600 paquetes, H(O) = 7,57 bits por byte):

| Algoritmo | Ahorro | H(C) | I(O;R) canal perfecto | I(O;R) con 1 % de pérdida | Información entregada | Llegan mal |
|---|---|---|---|---|---|---|
| sin compresión | 0 % | 7,57 | 7,57 | 7,50 | 99,15 % | 0 % |
| solo ROHC | 2,4 % | 7,57 | 7,57 | 7,50 | 99,15 % | 0,14 % |
| DEFLATE dicc. | 18,1 % | 7,99 | 7,57 | 7,50 | 99,15 % | 0,14 % |
| zstd dicc. | 20,4 % | 7,99 | 7,57 | 7,50 | 99,15 % | 0,14 % |
| DEFLATE flujo | 22,5 % | 7,99 | 7,57 | 7,34 | **97,0 %** | 3,1 % |
| bandido UCB, configuración anterior (con DEFLATE dicc.) | 22,4 % | 7,99 | 7,57 | 7,30 | **96,5 %** | 3,5 % |
| bandido UCB, configuración de red (con zstd dicc.) | 21,0 % | 7,99 | 7,57 | 7,50 | **99,1 %** | 1,8 % |

Lo que dice:

- **Con canal perfecto, todos dan I(O;R) = H(O).** Ningún algoritmo pierde información: es la
  prueba de que todos son sin pérdida.
- **Con 1 % de pérdida, los algoritmos se separan.** El 0,85 % que falta en los que no guardan
  estado es lo que se lleva el propio canal. DEFLATE por flujo pierde 2 puntos más, porque un
  paquete perdido desincroniza a los siguientes del mismo flujo; en texto (`http_texto`) entrega
  solo 89,8 %.
- **El bandido con zstd recupera casi toda esa información.** Antes entregaba 96,5 % (87,5 % en
  texto). Ahora elige zstd, que no guarda estado, para el texto, y su texto llega igual que sin
  comprimir (98,5 %). Solo usa DEFLATE por flujo para IoT, donde es mucho mejor (81,8 % frente a
  54,8 %), y ahí paga 2 puntos (96,7 %). Cuesta 1,4 puntos de ahorro.
- **H(C) sube hacia 8 bits por byte.** El comprimido queda casi sin redundancia: la misma
  información cabe en menos bytes.
- **No se reporta I(O;C).** El comprimido es una función invertible del original (si no, el
  receptor no podría reconstruirlo), así que por paquete I(O;C) = H(O) en cualquier algoritmo
  sin pérdida. Lo que los distingue es en cuántos bytes meten esa información: la razón de
  compresión y H(C).

Es el mismo balance de §4.4, medido ahora en bits de información: guardar estado gana en tasa y
paga en confiabilidad cuando el canal pierde paquetes. En PDCP, RLC AM retransmite y ese costo
desaparece.

### 4.7 En Grafana

Dos dashboards en la carpeta `lab-free5gc`, los dos con un selector de clase de tráfico:

- **lab5g · Compresión — por algoritmo**: se elige el algoritmo arriba y se ven su original,
  su comprimido, su recuperado y su información mutua, más la velocidad y la confiabilidad.
- **lab5g · Compresión — comparación de algoritmos**: todos lado a lado (tasa, H, I(O;R) con y sin
  pérdida, velocidad y una tabla resumen). Abajo, el bandido en vivo en la red 5G.

Los algoritmos salen de la etiqueta `algoritmo` de las métricas, así que uno nuevo aparece solo.
Los menús muestran un texto legible y numerado ("09 · Bandido UCB (IA): elige por paquete"), no el
id. En el tablero por algoritmo, un panel dice qué hace el algoritmo elegido, y en el de
comparación una tabla explica cada uno. Esos textos vienen de las métricas
`lab5g_cmp_eval_algoritmo_info` y `lab5g_cmp_eval_clase_info`, que publica `informacion.py` con el
`titulo` y la `descripcion` de cada clase `Codec`. Un códec nuevo trae los suyos.

Para llenarlos, con el perfil `compresion` y el pushgateway arriba:

```bash
docker exec -w /cmp cmp-dn python3 -I evaluar.py informacion --sin-archivos --publicar http://10.100.205.14:9091
```

Tarda unos 2,5 minutos en el LXC. Los valores son los de la última corrida, no series en el
tiempo. Los dashboards se generan con
[`dashboards_compresion.py`](../observability/grafana/dashboards_compresion.py).

> ⚠️ Grafana (`grafana:latest`, la 13.2) ocupa ~440 MB, y en este host de 3,8 GB eso basta para
> llevar el disco al 98 % por swap. Levántalo solo para mirar, y bájalo después.

### 4.8 Con pérdida acotada: `predictivo_iot` y la curva tasa–distorsión

Todo lo anterior es sin pérdida. Para la telemetría IoT (mMTC) hay un códec más,
[`PredictivoIoT`](../compresion/compresores.py) (id 7), que cambia precisión por bytes a propósito:

- **Cómo funciona.** Lee el JSON del sensor. Cada campo numérico se predice con el último valor
  **reconstruido** del mismo flujo (DPCM de lazo cerrado: el emisor predice con lo mismo que verá
  el receptor, así el error no se acumula). Solo se manda el error de predicción cuantizado con un
  paso por campo, en varint. Los enteros (`ts`, `rssi`) y el texto viajan exactos.
- **Pasos por defecto** (`PASOS_IOT`): temp 0,05 °C, hum 0,5 %, bat 0,005 V. El error queda
  siempre ≤ paso/2.
- **Marco completo cada 16 paquetes**, como `deflate_flujo`: el JSON exacto con DEFLATE y
  diccionario, más los pasos. Si se pierde un delta, el receptor se resincroniza ahí.
- **El receptor recibe un paquete UDP válido.** Como la carga cambia (21,53 → 21,5), el emisor
  rehace la carga del paquete *antes* de comprimir la cabecera, con los largos y las sumas IP y UDP
  recalculadas (`Compresor.preparar`). Si no, ROHC llevaría la suma del original y el sistema
  operativo del receptor descartaría el paquete.
- **No es un brazo del bandido.** Está en `CODECS_CON_PERDIDA`, aparte de `CODECS`: perder
  precisión es una decisión de la aplicación, no algo que la IA deba aprender a premiar por bytes.
  Se activa en el túnel con `PERDIDA_IOT` (vacío = todo sin pérdida; `1` = pasos por defecto;
  `0.5` = la mitad; `temp=0.1,hum=1` = por campo). El resto del tráfico sigue con el bandido.

Resultados (`python evaluar.py informacion`, sección 4 de
[`resultados/informacion.md`](../compresion/resultados/informacion.md)), 600 paquetes iot_json de
~122 B:

| Método | Bytes por paquete | Ahorro | Error máx. temp / hum / bat | I(X;Y)/H(X) medio, canal perfecto | Dentro de tolerancia con 1 % de pérdida | Dañados con 1 % de pérdida |
|---|---:|---:|---|---:|---:|---:|
| zstd con diccionario (sin pérdida) | 55,1 | 54,8 % | 0 / 0 / 0 | 100 % | 100 % | 0 % |
| DEFLATE por flujo (sin pérdida) | 25,4 | 79,1 % | 0 / 0 / 0 | 100 % | 90,7 % | 8,3 % |
| predictivo ×0 (pasos 1e-6) | 20,6 | 83,1 % | 0 / 0 / 0 | 100 % | 94,1 % | 0,7 % |
| predictivo ×1 (por defecto) | 17,4 | **85,7 %** | 0,02 / 0,2 / 0,001 | 75,9 % | 96,6 % | 0,7 % |
| predictivo ×10 | 17,4 | 85,7 % | 0,25 / 1,9 / 0,001 | 62,0 % | 98,1 % | 0,7 % |

Lo que dice:

- **Es el primer algoritmo con I(X;Y) < H(X) con canal perfecto.** La información que falta es la
  que se decidió no mandar: las décimas de grado que están por debajo del paso. Aquí I(X;Y) se mide
  sobre los **valores** de cada campo, no byte a byte: con pérdida el JSON cambia de largo y alinear
  bytes por posición ya no compara lo mismo. Con ~600 lecturas el estimador tiene sesgo, así que
  sirve para comparar puntos entre sí.
- **Aun con pasos de 1e-6 (×0, sin pérdida en la práctica) ya ahorra más que DEFLATE por flujo**
  (83,1 % frente a 79,1 %): conocer la estructura del mensaje vale más que un compresor genérico.
- **Más allá de ×0,1 el ahorro no sigue subiendo.** Cada error cuantizado ya cabe en 1 byte de
  varint; lo que queda son el id, el mapa de textos, los enteros y los marcos completos. Para bajar
  de ahí habría que codificar a nivel de bit (Golomb o aritmético), no cuantizar más grueso.
- **Con pérdida es más robusto que DEFLATE por flujo** (0,7 % dañados frente a 8,3 %): un delta
  perdido no rompe la descompresión, solo deja la predicción corrida hasta el siguiente marco
  completo. Por eso "dentro de tolerancia" baja a 94–98 %, con errores de pocas décimas, no
  basura. Los dañados son los paquetes donde el texto (`st`: "ok" → "alarma") cambió en el delta
  perdido.
- **bat casi no cambia** (baja 0,0001 V por lectura y se redondea a 3 decimales), así que su error
  y su I/H casi no se mueven con la escala.

En Grafana es la fila **"Tasa–distorsión (IoT, compresión con pérdida)"** del tablero de
comparación (métricas `lab5g_cmp_eval_td_*`, etiquetas `metodo`, `canal` y `campo`).

### 4.9 Mandar un archivo real por la red: la página de envío

Todo lo anterior usa tráfico de prueba. Con el perfil `compresion` arriba hay además una página,
**http://192.168.1.50:8088** (LAN o tailnet), para subir un archivo cualquiera (audio, video,
imagen o texto, hasta 20 MB) y mandarlo por la red 5G a través del compresor, ida y vuelta:

```
navegador ─HTTP─► envio (netns de cmp-dn) ─TCP─► cmp0 ─[comprimir]─► UPF ══ 5G ══► UE ─► envio-eco
navegador ◄─────── resultados ◄── envio ◄── cmp0 ◄─[descomprimir]══ 5G ◄─[comprimir]── envio-eco
```

- **`envio`** ([`envio.py pagina`](../compresion/envio.py)) vive en el netns de `cmp-dn` y manda el
  archivo por TCP a 10.99.0.1, el `cmp0` del UE: la bajada la comprime `cmp-dn`.
- **`envio-eco`** (`envio.py eco`) vive en el netns del UE, junto a `cmp-ue`, y lo devuelve por la
  subida, que comprime `cmp-ue`.
- La página compara lo que volvió con el original y muestra: SHA-256 de los dos, tiempo de ida y
  vuelta, H(O), H(R), I(O;R), y por cada sentido los bytes IP que entraron al compresor, los que
  viajaron por el túnel (marco + 28 B de IP/UDP del túnel por paquete), el ahorro y qué códec
  eligió el bandido.
- Esos contadores salen de `shim.py`, que ahora los sirve en JSON en `127.0.0.1:7701` de su netns.
  Así son exactos antes y después del envío; el pushgateway solo se actualiza cada 10 s.

Qué esperar: un TXT o un WAV vuelven idénticos con ahorro; un MP3, JPG o MP4 casi no ahorran
(ya vienen comprimidos) y el bandido aprende a mandarlos con «ninguno». En todos, si el SHA-256
coincide, I(O;R) = H(O): el compresor no perdió nada. La radio simulada de UERANSIM va a pocos
Mbps, así que un archivo de varios MB tarda segundos o minutos.

Límites: no tiene login, así que el puerto 8088 se publica en la LAN y el tailnet pero **no** va
por `tailscale funnel`. Un envío a la vez. El archivo vive solo en memoria y se descarta al
terminar. Los contadores son de todo el túnel: si a la vez corre el generador de tráfico, se
mezcla con él.

## 5. Implementación en el lab

```
cmp-ue (netns del UE)                                                     cmp-dn (dn_net, 10.100.204.30)
 app ─► cmp0 10.99.0.1 ─[ROHC + bandido]─► UDP:7700 ─► uesimtun0 ══ gNB ══ N3 ══ UPF ══ N6 ─► [descomprimir] ─► cmp0 10.99.0.2 ─► sumideros
```

- [`compresion/compresores.py`](../compresion/compresores.py): códecs (una estrategia por algoritmo, ver 5.1), ROHC-lite, formato del marco.
- [`compresion/bandido.py`](../compresion/bandido.py): bandido UCB y `EmisorAdaptativo`, el emisor que usa la red.
- [`compresion/shim.py`](../compresion/shim.py): el túnel TUN↔UDP, en los dos lados. Publica `lab5g_cmp_*` al pushgateway.
- [`compresion/trafico.py`](../compresion/trafico.py), [`generar.py`](../compresion/generar.py), [`evaluar.py`](../compresion/evaluar.py), [`medir.sh`](../compresion/medir.sh), [`test_compresores.py`](../compresion/test_compresores.py).
- [`compresion/modelo.json`](../compresion/modelo.json): tabla de Huffman, diccionario de DEFLATE de 4 KB y diccionario de zstd de 4 KB (entrenado por zstd). Los dos extremos cargan el mismo archivo.

### 5.1 Agregar un algoritmo de compresión (patrón Strategy)

Cada códec de carga es una **estrategia**: una clase que hereda de `Codec` en
[`compresores.py`](../compresion/compresores.py) y se anota en el registro con `@registrar`.
`Compresor` y `Descompresor` no conocen ningún algoritmo: buscan la estrategia por el id que
viaja en el byte 0 y le delegan el trabajo. ROHC-lite (cabeceras) y el bandido quedan fuera del
patrón: el bandido solo ve ids, y la cabecera se comprime antes, igual para todos los códecs.

```python
@registrar
class Brotli(Codec):
    id, nombre = 7, "brotli"               # id libre de 0-15; un id usado no se reasigna
    titulo = "Brotli"                      # lo que muestra el menú de Grafana
    descripcion = "..."                    # lo que muestra el panel "Qué hace este algoritmo"
    def comprimir(self, carga, clave):     # -> (bytes, confirmar o None)
        return ..., None
    def descomprimir(self, datos):
        return ...
```

Con eso el algoritmo ya se puede usar en `Compresor`/`Descompresor` y aparece solo en todas las
mediciones: `evaluar.py comparar` (ahorro por clase, flujo mezclado, latencia y pérdidas),
`balance` e `informacion`, y en Grafana. Para que el bandido lo pueda elegir en la red hay que
agregarlo a `BRAZOS_RED` en [`bandido.py`](../compresion/bandido.py).

`ZstdDiccionario` (id 6) se agregó así, sin tocar `Compresor`, `Descompresor` ni las
mediciones. Como zstd solo está en la biblioteca estándar desde Python 3.14, se registra solo
si hay zstd disponible: con un Python más viejo, el resto funciona igual. En un PC se puede
usar el paquete `zstandard`.

Reglas del contrato, para que emisor y receptor no se desincronicen:

- Cada extremo crea **su propia instancia** de cada estrategia, así que una estrategia puede
  guardar estado (como `DeflateFlujo`, por flujo).
- `comprimir()` **no cambia el estado**. Si lo necesita, devuelve una función `confirmar`, que
  el `Compresor` llama solo si el paquete sale con ese códec. Si la salida no achica, el paquete
  se manda con `ninguno` y el receptor nunca lo ve por ese códec.
- Lo que deban compartir los dos extremos (tablas, diccionarios) sale del `Modelo`
  (`modelo.json`).
- Solo biblioteca estándar: los contenedores son `python:3.14-alpine` sin `pip install`. Los dos
  extremos tienen que tener la misma versión, porque los códecs registrados tienen que ser los
  mismos.

Costo del patrón: buscar la estrategia cuesta 0,04 µs, y comprimir un paquete cuesta 35–200 µs.
El cambio no movió ni un byte: con el mismo tráfico, `evaluar.py comparar` da exactamente los
mismos tamaños que la versión con `if` encadenados, en todas las tablas.
`test_compresores.py` verifica que un códec nuevo se enchufa sin tocar nada más y que no se
puede repetir un id.

### 5.2 Despliegue y enrutamiento en el UE

Detalle de enrutamiento que la prueba en WSL no podía mostrar: en el `ue`, UERANSIM solo
manda por `uesimtun0` lo que sale **con la IP de la sesión PDU**
(`ip rule from 10.60.0.1 lookup rt_uesimtun0`); la ruta por defecto del contenedor es `eth0`.
Por eso `shim.py` amarra su socket a la IP de `uesimtun0` en vez de a `0.0.0.0`. Sin eso, el
túnel saldría por la red de Docker y se saltaría el 5G.

Perfil opcional `compresion` en `docker-compose.yaml`: dos contenedores `python:3.14-alpine` de
unos 15 MB de RAM cada uno. No arranca con el despliegue normal y el CI no lo toca.

```bash
export COMPOSE_PROJECT_NAME=lab5g COMPOSE_PROFILES=ran,compresion
docker compose up -d
bash compresion/medir.sh
```

Para usar el bandido por latencia (§4.5.1), se define la velocidad del enlace antes de levantar
el perfil. Por ejemplo, con `uesimtun0` limitado a 1 Mbps:

```bash
VELOCIDAD_ENLACE_BPS=1000000 docker compose up -d cmp-dn cmp-ue
```

`docker logs cmp-ue` muestra al arrancar qué códecs hay y qué premio usa el bandido.

**Prueba funcional (2026-11-04, sin núcleo 5G).** Se hizo en WSL con dos network namespaces
unidos por un veth llamado `uesimtun0`, con el mismo `shim.py` y `generar.py` y tráfico TCP y
UDP real del kernel:

| | Bytes por `uesimtun0` (subida) |
|---|---|
| Sin compresor | 874 969 |
| Con compresor | 735 629 (**−15,9 %**) |
| Dentro del túnel (lo que ahorraría PDCP, sin los 28 B/paq del túnel) | 867 967 → 676 906 (**−22,0 %**) |

Hubo 0 errores de descompresión. La bajada son solo ACK de TCP: el ROHC-lite no tiene perfil
TCP, así que salen +1,9 %.
**Medición en la red 5G real (2026-11-05, LXC 110).** Con el núcleo, la RAN y el perfil
`compresion` arriba, sin `obs`. El tráfico atraviesa la sesión PDU completa: `uesimtun0` →
gNB → GTP-U → UPF → `dn_net`. Las dos corridas salen con la IP de la sesión (`10.60.0.1`).

| | Bytes por `uesimtun0` (subida) | Bajada |
|---|---|---|
| Sin compresor | 867 985 | 21 492 |
| Con compresor | 719 553 (**−17,1 %**) | 33 573 |
| Dentro del túnel (lo que ahorraría PDCP) | 868 163 → 680 422 (**−21,6 %**) | 21 684 → 22 057 (+1,7 %) |

Hubo 0 errores de descompresión en los dos lados. El ahorro coincide con la evaluación offline
(22,4 %) y con la prueba en WSL. La bajada crece en `uesimtun0` porque cada ACK de TCP paga los
28 B del túnel UDP; dentro del túnel es +1,7 %, igual que en WSL. Los 11 tests de entonces también
pasaron con el Python 3.12 del contenedor. Esta medición es de la configuración anterior (antes
de zstd); hoy los 16 tests pasan con el Python 3.14 del contenedor y su `compression.zstd`.

Latencia de ida y vuelta (`ping` desde el UE, 400 paquetes a 50 por segundo):

| | Mediana | p90 | p99 | Máximo |
|---|---|---|---|---|
| Directo por `uesimtun0` | 0,81 ms | 0,93 ms | 1,09 ms | 3,4 ms |
| Por el túnel compresor | 2,1 ms | 2,5–12 ms | 22–120 ms | 28–146 ms |

Ese es el costo real del lado de la latencia, y tiene dos partes distintas:

- **+1,3 ms en la mediana: el túnel en espacio de usuario.** Cada paquete sale del kernel hacia
  Python por `cmp0` y vuelve a entrar por un socket UDP, en los dos extremos y en las dos
  direcciones. Los códecs cuestan del orden de 0,1 ms por ida y vuelta (§4.3); el resto es el
  túnel. En PDCP ese salto no existe, porque la compresión está dentro de la pila de radio.
  Por eso el modelo de §4.5, que solo cuenta la CPU de los códecs, subestima el costo de **este**
  prototipo, pero no el de un compresor en PDCP.
- **La cola larga (p99 de 22 a 120 ms): swap del host.** Los procesos del compresor tienen 2–6 MB
  de su memoria en el swap del disco de 5400 rpm. En cada ráfaga suman 20–30 fallos de página
  mayores, y cada uno es una lectura de disco. En la primera prueba, con el host todavía cargado,
  la mediana llegó a 470 ms y el máximo a 3,8 s. Es un problema de este servidor, no del
  algoritmo: con los contenedores del compresor sin swap, debería desaparecer.

En este lab, con enlaces de varios Gbps, el resultado es el esperado según §4.5: se ahorran
bytes, pero la latencia sube. Para ver la ganancia en latencia hay que limitar la velocidad de
`uesimtun0` con `tc`.

## 6. Límites declarados

1. **El tráfico es sintético**, aunque tiene forma real: IPv4 completo con checksums válidos, RTP, TLS, y texto real de estos docs para HTTP. El ahorro del flujo mezclado depende de la mezcla, que aquí es de 600 paquetes por clase. Lo que no depende de la mezcla son las cifras por clase de §4.1.
2. **El ROHC-lite es una versión reducida** de RFC 5795. No tiene perfil TCP, ni canal de vuelta (modo O/R), ni CRC de cabecera. Si se pierde un IR, el flujo queda sin contexto hasta el siguiente IR (cada 64 paquetes).
3. **La latencia es de Python.** Sirve para comparar los códecs entre sí, no como cifra absoluta de un equipo real.
4. **En el lab el compresor no está en PDCP** (§1), así que lo medido en `uesimtun0` es menor que lo que ahorraría PDCP.
5. **La parte de IA vale por la CPU, la robustez y la latencia, no por bytes extra.** Un DEFLATE por flujo fijo ahorra un poco más. Lo que aporta el bandido es saltarse lo que no se puede comprimir, usar el códec sin estado donde casi no se pierde ahorro (texto) y, premiado por latencia, dejar de comprimir cuando el enlace es rápido.
6. **El bandido por latencia necesita que le digan la velocidad del enlace** (`VELOCIDAD_ENLACE_BPS`), y su premio no ve la cola (§4.5.1).

## 7. Siguientes alternativas, si se quiere ir más allá

- **Codificar el error de predicción a nivel de bit** (Golomb-Rice o aritmético) en `predictivo_iot`, para que la tasa siga bajando al cuantizar más grueso (§4.8). Y un delta **sin** pérdida para URLLC (posición y secuencia), donde en control no se tolera error.
- **Darle al bandido por latencia la ocupación del enlace**, no solo la velocidad, para que vea la cola.
- **Formatos binarios en la aplicación** (CBOR o Protobuf en vez de JSON para IoT): ahorran antes de llegar a la red, sin estado que se pueda desincronizar.
- **ROHC completo** (biblioteca `rohc` en C) con perfil TCP, y medir en PDCP con una pila que lo implemente (OpenAirInterface o srsRAN) en vez de UERANSIM.
