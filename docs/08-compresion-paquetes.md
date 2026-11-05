# 08 — Compresión de paquetes en la red 5G (con IA)

El Lab 3 de Teoría de la Información ([`UD.Lab3.TeoriaInformacion`](https://github.com/J4F3ET/UD.Lab3.TeoriaInformacion))
construyó un códec Huffman adaptable a k bits por símbolo y un bandido UCB que elige k.
Aquí se lleva esa idea a los **paquetes** de esta red 5G: dónde debería ir, si es viable tal
cual, qué hubo que cambiar y por qué.

**Resumen:**

1. El códec del Lab 3 tal cual **no es viable por paquete**: un paquete IoT de 92 B se vuelve un `.tic` de 300 B.
2. Lo que sí sirve es **comprimir cabeceras (ROHC) más DEFLATE con diccionario y estado por flujo**, que es lo que 3GPP ya hace en PDCP (ROHC y UDC).
3. La IA (el mismo bandido UCB) sigue siendo útil, pero ahora elige **qué códec usar en cada paquete, incluido "ninguno"**, no k.
4. Resultado sobre tráfico de prueba que el modelo no vio: **22,4 %** menos bytes. Con eso, el bandido solo le pasa al compresor la mitad de los bytes; un códec fijo se los pasa todos.

Todo está en [`compresion/`](../compresion). Solo usa la biblioteca estándar de Python, así
que corre en `python:3.12-alpine` sin instalar nada.

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
| Ajustar y evaluar sobre el mismo flujo (límite 5 del Lab 3) | **Se corrige**: se entrena y ajusta con la semilla 1 y los docs 01, 03, 05 y 07; se evalúa con la semilla 2 y los docs 02, 04 y 06. | Resultado no optimista. |
| NumPy, gráficas, bitstrip, `.tic`, `--perdida` | **Se quitan.** | Nada de eso hace falta, o puede ir, en el camino de cada paquete. |
| — | **Nuevo: ROHC-lite** (cabeceras IP/UDP/RTP, RFC 5795 simplificado, modo U). | En voz y URLLC las cabeceras son más de la mitad del paquete. |
| — | **Nuevo: DEFLATE con diccionario entrenado** (como UDC de LTE) y **DEFLATE por flujo** (con memoria entre paquetes). | Son los que ganan en todo lo compresible. |

## 4. Comparación (tráfico de prueba, 3600 paquetes, todo verificado byte a byte)

Reproducible con `python compresion/evaluar.py entrenar && python compresion/evaluar.py ajustar && python compresion/evaluar.py comparar`.
Tablas completas en [`compresion/resultados/resultados.md`](../compresion/resultados/resultados.md) y
el ajuste en [`ajuste.md`](../compresion/resultados/ajuste.md).

### 4.1 Por clase de tráfico (% de bytes ahorrados)

| Clase (slice) | B/paq | Solo ROHC | +Huffman estático | +DEFLATE | +DEFLATE dicc. | **+DEFLATE flujo** | +TIC por paquete |
|---|---|---|---|---|---|---|---|
| iot_json (mMTC) | 122 | 17,6 | 29,7 | 26,3 | 60,8 | **81,8** | 17,6 |
| urllc_ctrl (URLLC) | 50 | **43,0** | 43,0 | 43,0 | 43,0 | 43,0 | 43,0 |
| voip_rtp (eMBB) | 73 | **45,5** | 45,5 | 45,5 | 45,5 | 45,5 | 45,5 |
| video_rtp (eMBB) | 1240 | 2,5 | 2,5 | 2,5 | 2,5 | 2,5 | 2,5 |
| web_tls (eMBB) | 1440 | −0,07 | −0,07 | −0,07 | −0,07 | −0,07 | −0,07 |
| http_texto (eMBB) | 1434 | −0,07 | 31,9 | 40,3 | 43,9 | **60,6** | 20,5 |

Lectura:

- En **URLLC y voz** todo el ahorro viene de las **cabeceras**. ROHC baja IP+UDP+RTP de 40 B a 5 B. La carga de voz ya viene comprimida por el códec AMR.
- En **IoT y texto** gana la **memoria entre paquetes**: los mensajes de un sensor se parecen mucho entre sí, aunque cada uno por separado casi no tenga redundancia. Por eso Huffman, que mira un paquete a la vez, se queda en 30 %.
- **TLS y video no se pueden comprimir.** Lo único que se pierde es el byte de marco (−0,07 %), porque todo códec cae al respaldo "sin comprimir".

### 4.2 Flujo mezclado (las 6 clases intercaladas, como una celda)

| Política | Ahorro | Bytes que pasan por un compresor, por byte enviado |
|---|---|---|
| Sin compresión | 0 % | 0 |
| Solo ROHC | 2,4 % | 0 |
| ROHC + DEFLATE dicc. (fijo) | 18,1 % | 1 |
| ROHC + DEFLATE flujo (fijo, sin reinicio) | 24,2 % | 1 |
| ROHC + DEFLATE flujo (fijo, reinicio cada 16) | 22,5 % | 1 |
| ROHC + bandido, 6 brazos, γ = 0,99 (los parámetros del Lab 3) | 22,1 % | 0,80 |
| **ROHC + bandido, configuración de red** (3 brazos, γ = 0,999, reinicio cada 16) | **22,4 %** | **0,51** |

El bandido logra casi el mismo ahorro que el mejor códec fijo, pero **solo comprime la mitad
de los bytes**: aprendió a no gastar CPU en TLS, video, voz ni URLLC. Ahí elige "ninguno" en
unos 450 de cada 600 paquetes; el resto es exploración.

La configuración de red salió de una rejilla sobre el conjunto de **entrenamiento**
([`ajuste.md`](../compresion/resultados/ajuste.md)): de γ ∈ {0,99; 0,995; 0,999}, c ∈ {0,02; 0,05}
y 6 o 3 brazos, quedó la de mayor ahorro con menos CPU. Quitar Huffman estático, DEFLATE sin
diccionario y TIC (nunca fueron los mejores en ninguna clase) bajó la CPU unas 4 veces sin
perder ahorro.

### 4.3 Latencia por paquete (Python puro, µs, comprimir / descomprimir)

| Códec | iot_json | urllc_ctrl | http_texto |
|---|---|---|---|
| ninguno (solo ROHC) | 10 / 10 | 9 / 10 | 4 / 1 |
| DEFLATE dicc. | 49 / 15 | 44 / 10 | 107 / 19 |
| DEFLATE flujo | 35 / 13 | 62 / 11 | 201 / 21 |
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
| ROHC + DEFLATE flujo, sin reinicio | **26,5 %** |
| ROHC + DEFLATE flujo, reinicio cada 16 paquetes | 3,1 % |
| ROHC + bandido, configuración de red | 3,5 % |

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

| Enlace | Carga | Sin compresión | Solo ROHC | DEFLATE dicc. | DEFLATE flujo | Bandido |
|---|---|---|---|---|---|---|
| 100 kbps | 80 % | 267 | 244 | 137 | **122** | 122 |
| 1 Mbps | 80 % | 26,7 | 24,5 | 13,8 | 12,3 | **12,3** |
| 10 Mbps | 80 % | 2,67 | 2,46 | 1,49 | 1,36 | **1,32** |
| 100 Mbps | 80 % | 0,267 | **0,266** | ∞ | ∞ | ∞ |
| 1 Gbps | 50 % | **0,011** | 0,113 | ∞ | ∞ | ∞ |

∞ significa que el compresor en Python no alcanza a procesar todos los paquetes que llegan.

Lo que dice:

- **Enlace lento o cargado (≤ 10 Mbps): comprimir baja la latencia a la mitad o menos.** Los
  bytes que se ahorran pesan más que la CPU que se gasta, y la cola se encoge mucho porque la
  espera crece como 1/(1 − ocupación).
- **Enlace rápido (≥ 100 Mbps con Python): comprimir solo suma latencia, o el compresor no da
  abasto.** Solo ROHC, que es muy barato, todavía empata o gana un poco con carga alta.
- **Con la CPU 10× más rápida** (el caso de C o del hardware de la estación base), la frontera
  sube un orden de magnitud: el bandido gana hasta ~100 Mbps.
- **Tráfico cifrado (TLS): ningún algoritmo gana a ninguna velocidad.** Por eso el bandido
  aprende a no comprimirlo.
- **Velocidad de equilibrio por clase** (carga 50 %, Python): VoIP y URLLC ganan con ROHC por
  debajo de ~13–15 Mbps; IoT con DEFLATE flujo, por debajo de ~11 Mbps; texto, por debajo de
  ~50 Mbps.
- **El ganador de menor latencia no siempre es el más robusto.** DEFLATE flujo y el bandido
  reconstruyen mal ~3 % de los paquetes con 1 % de pérdida. Si se exige ≤ 1 %, el ganador en
  enlaces lentos pasa a ser DEFLATE con diccionario, que no guarda estado.

Lo que el modelo deja fuera: la propagación, que es igual para todos, y el planificador de radio
de 5G. Este último premia todavía más a los paquetes chicos, así que el modelo es conservador a
favor de no comprimir. El lab no puede mostrar la ganancia tal cual, porque sus enlaces son
puentes de Docker de varios Gbps. Para verla habría que limitar `uesimtun0` con `tc`.

## 5. Implementación en el lab

```
cmp-ue (netns del UE)                                                     cmp-dn (dn_net, 10.100.204.30)
 app ─► cmp0 10.99.0.1 ─[ROHC + bandido]─► UDP:7700 ─► uesimtun0 ══ gNB ══ N3 ══ UPF ══ N6 ─► [descomprimir] ─► cmp0 10.99.0.2 ─► sumideros
```

- [`compresion/compresores.py`](../compresion/compresores.py): códecs (una estrategia por algoritmo, ver 5.1), ROHC-lite, formato del marco.
- [`compresion/bandido.py`](../compresion/bandido.py): bandido UCB y `EmisorAdaptativo`, el emisor que usa la red.
- [`compresion/shim.py`](../compresion/shim.py): el túnel TUN↔UDP, en los dos lados. Publica `lab5g_cmp_*` al pushgateway.
- [`compresion/trafico.py`](../compresion/trafico.py), [`generar.py`](../compresion/generar.py), [`evaluar.py`](../compresion/evaluar.py), [`medir.sh`](../compresion/medir.sh), [`test_compresores.py`](../compresion/test_compresores.py).
- [`compresion/modelo.json`](../compresion/modelo.json): tabla de Huffman y diccionario de 4 KB. Los dos extremos cargan el mismo archivo.

### 5.1 Agregar un algoritmo de compresión (patrón Strategy)

Cada códec de carga es una **estrategia**: una clase que hereda de `Codec` en
[`compresores.py`](../compresion/compresores.py) y se anota en el registro con `@registrar`.
`Compresor` y `Descompresor` no conocen ningún algoritmo: buscan la estrategia por el id que
viaja en el byte 0 y le delegan el trabajo. ROHC-lite (cabeceras) y el bandido quedan fuera del
patrón: el bandido solo ve ids, y la cabecera se comprime antes, igual para todos los códecs.

```python
@registrar
class Zstd(Codec):
    id, nombre = 6, "zstd"                 # id libre de 0-15; un id usado no se reasigna
    def comprimir(self, carga, clave):     # -> (bytes, confirmar o None)
        return ..., None
    def descomprimir(self, datos):
        return ...
```

Con eso el algoritmo ya se puede usar en `Compresor`/`Descompresor` y aparece solo en todas las
mediciones de `evaluar.py comparar`: ahorro por clase, flujo mezclado, latencia y pérdidas. Para
que el bandido lo pueda elegir en la red hay que agregarlo a `BRAZOS_RED` en
[`bandido.py`](../compresion/bandido.py).

Reglas del contrato, para que emisor y receptor no se desincronicen:

- Cada extremo crea **su propia instancia** de cada estrategia, así que una estrategia puede
  guardar estado (como `DeflateFlujo`, por flujo).
- `comprimir()` **no cambia el estado**. Si lo necesita, devuelve una función `confirmar`, que
  el `Compresor` llama solo si el paquete sale con ese códec. Si la salida no achica, el paquete
  se manda con `ninguno` y el receptor nunca lo ve por ese códec.
- Lo que deban compartir los dos extremos (tablas, diccionarios) sale del `Modelo`
  (`modelo.json`).
- Solo biblioteca estándar: los contenedores son `python:3.12-alpine` sin `pip install`.

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

Perfil opcional `compresion` en `docker-compose.yaml`: dos contenedores `python:3.12-alpine` de
unos 15 MB de RAM cada uno. No arranca con el despliegue normal y el CI no lo toca.

```bash
export COMPOSE_PROJECT_NAME=lab5g COMPOSE_PROFILES=ran,compresion
docker compose up -d
bash compresion/medir.sh
```

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
**Pendiente:** correr `medir.sh` sobre la red 5G real del LXC 110. El 2026-11-05 el stack y
`cmp-ue`/`cmp-dn` quedaron arriba y el ping por `uesimtun0` hasta `10.100.204.30` respondía,
pero la medición no se completó. Antes de medir, `generar.py` también tiene que salir con la
IP de la sesión cuando manda tráfico directo (sin compresor); si no, la comparación no pasa
por el 5G.

## 6. Límites declarados

1. **El tráfico es sintético**, aunque tiene forma real: IPv4 completo con checksums válidos, RTP, TLS, y texto real de estos docs para HTTP. El ahorro del flujo mezclado depende de la mezcla, que aquí es de 600 paquetes por clase. Lo que no depende de la mezcla son las cifras por clase de §4.1.
2. **El ROHC-lite es una versión reducida** de RFC 5795. No tiene perfil TCP, ni canal de vuelta (modo O/R), ni CRC de cabecera. Si se pierde un IR, el flujo queda sin contexto hasta el siguiente IR (cada 64 paquetes).
3. **La latencia es de Python.** Sirve para comparar los códecs entre sí, no como cifra absoluta de un equipo real.
4. **En el lab el compresor no está en PDCP** (§1), así que lo medido en `uesimtun0` es menor que lo que ahorraría PDCP.
5. **La parte de IA vale por la CPU que ahorra, no por bytes extra.** Un DEFLATE por flujo fijo ahorra lo mismo o un poco más; lo que aporta el bandido es saltarse el tráfico que no se puede comprimir.

## 7. Siguientes alternativas, si se quiere ir más allá

- **zstd con diccionario entrenado** (`zstd --train`) en vez de DEFLATE: suele comprimir mejor los mensajes chicos y es más rápido. Python 3.14 lo trae en la biblioteca estándar (`compression.zstd`).
- **Formatos binarios en la aplicación** (CBOR o Protobuf en vez de JSON para IoT): ahorran antes de llegar a la red, sin estado que se pueda desincronizar.
- **ROHC completo** (biblioteca `rohc` en C) con perfil TCP, y medir en PDCP con una pila que lo implemente (OpenAirInterface o srsRAN) en vez de UERANSIM.
