# 10 — Codificación de canal: códigos de bloque lineales

Esta entrega añade protección contra errores de bit al túnel de compresión
([docs/08](08-compresion-paquetes.md)). Usa solo lo visto en clase de
Teoría de la Información: códigos de bloque lineales, matrices $G$ y $H$, y
síndrome. La teoría sigue la notación del apunte de clase del 9 de noviembre de 2026.

---

## Índice

- [1. Objetivo y alcance de esta entrega](#1-objetivo-y-alcance-de-esta-entrega)
- [2. Teoría de clase](#2-teoría-de-clase)
- [3. Dónde encaja en el proyecto](#3-dónde-encaja-en-el-proyecto)
- [4. Inyección manual del error](#4-inyección-manual-del-error)
- [5. Visualización «Laboratorio de síndrome»](#5-visualización-laboratorio-de-síndrome)
- [6. Grafana (diseño, tablero aún no construido)](#6-grafana-diseño-tablero-aún-no-construido)
- [7. Relación con la compresión](#7-relación-con-la-compresión)
- [8. Estado del arte](#8-estado-del-arte)
- [9. Hoja de ruta: siguiente entrega](#9-hoja-de-ruta-siguiente-entrega)
- [10. Cómo probar](#10-cómo-probar)

---

## 1. Objetivo y alcance de esta entrega

**Objetivo.** Que un paquete que llega con un bit cambiado por el canal pueda
detectarse y, si el error es de un bit y el código lo permite, corregirse en
`cmp-dn` antes de entregarlo.

**Alcance: lo que sí entra en esta entrega**

- Códigos de bloque lineales vistos en clase, en dos instancias:
  - **Repetición (5,1)**: cada bit se envía cinco veces.
  - **Hamming (7,4)**: cuatro bits de mensaje y tres de paridad.
- Decodificación por **síndrome**, tal como aparece en el apunte.
- **Inyección de error solo manual**: la página de envío pide voltear de 1 a 8
  bits del siguiente marco que reciba `cmp-dn`. No hay canal aleatorio.
- Integración en el shim (`shim.py`), servidor de línea y endpoints HTTP, página
  «Laboratorio de síndrome» y métricas `lab5g_cmp_fec_*`.

**Siguiente entrega (no implementado aquí):** SECDED, CRC, entrelazado,
Reed-Solomon, paridad entre paquetes, modelos de canal aleatorios (BSC, ráfagas
Gilbert-Elliott, borrado) y RaptorQ. Ver [§9](#9-hoja-de-ruta-siguiente-entrega).

**Estado de la implementación**

El código está escrito y tiene pruebas unitarias (82 pruebas en los cinco archivos
de [§10](#10-cómo-probar), ejecutadas en un PC con Windows). **No se ha probado de
extremo a extremo en el laboratorio real**: en Windows no hay TUN, así que
`shim.py` no se ha ejecutado con tráfico de verdad. Lo que falta probar está en
[§10](#10-cómo-probar).

| Pieza | Estado |
|---|---|
| `compresion/fec.py` (códigos de repetición y Hamming, `CodigoLineal`, `codificar`/`decodificar` sobre bytes) | Implementado y probado con pruebas unitarias; los ejemplos de clase son pruebas exactas |
| `compresion/canal.py` (`inyectar` y `elegir_posiciones`) | Implementado y probado con pruebas unitarias |
| `compresion/registro_fec.py` (`RegistroFec`, `CapaFec`, `mtu_con_fec`, `aplicar_canal`) | Implementado y probado con pruebas unitarias (sin TUN ni sockets) |
| Integración en `compresion/shim.py` (variable `FEC`, servidor de línea, métricas) | Implementada; **no probada en el laboratorio** |
| Endpoints HTTP y página «Laboratorio de síndrome» (`compresion/envio.py`) | Implementados; las pruebas unitarias cubren la validación y el resumen (`test_envio_fec.py`); **la página no se ha usado contra un shim real** |
| Métricas `lab5g_cmp_fec_*` | Implementadas en el shim; **no verificadas en el pushgateway** |
| Tablero de Grafana | **Diseño, aún no construido** ([§6](#6-grafana-diseño-tablero-aún-no-construido)) |

---

## 2. Teoría de clase

### 2.1 Código $(n,k)$

- Mensaje $\mathbf m$ de **$k$ bits** → palabra código $\mathbf c$ de **$n$ bits**.
- Se añaden $n-k$ bits de paridad $\mathbf b$. La tasa del código es $R=k/n$.

$$\mathbf m=[m_0,\dots,m_{k-1}]\qquad \mathbf b=[b_0,\dots,b_{n-k-1}]\qquad \mathbf c=[c_0,\dots,c_{n-1}]$$

Toda la aritmética es **módulo 2** (suma = XOR). El código es **sistemático**: el
mensaje aparece intacto dentro de $\mathbf c$.

### 2.2 Paridad y generadora

$$\boxed{\mathbf b=\mathbf m\,P}$$

- $P$ tiene $k$ filas y $n-k$ columnas, con elementos 0 o 1.
- La generadora es $G=[P:I_k]$, de $k$ filas y $n$ columnas, y

$$\boxed{\mathbf c=\mathbf m\,G}$$

Porque $\mathbf m\,[P:I_k]=[\mathbf mP:\mathbf m]$.

### 2.3 Matriz de verificación $H$

$H$ tiene $(n-k)$ filas y $n$ columnas:

$$H=[\,I_{n-k}:P^{T}\,]$$

Propiedad clave: $G$ y $H$ son ortogonales.

$$H\,G^{T}=[\,I_{n-k}:P^{T}\,]\begin{bmatrix}P^{T}\\ I_k\end{bmatrix}=P^{T}+P^{T}=0\pmod 2$$

### 2.4 Síndrome

Una palabra válida cumple $\mathbf c\,H^{T}=\mathbf m\,G\,H^{T}=0$.

El receptor no ve $\mathbf c$ sino $\mathbf r=\mathbf c+\mathbf e$, donde $\mathbf e$ es el
patrón de error. El **síndrome** es

$$\boxed{\mathbf s=\mathbf r\,H^{T}}\qquad\Rightarrow\qquad \mathbf s=(\mathbf c+\mathbf e)H^{T}=\mathbf e\,H^{T}$$

- Si $\mathbf s=0$, no hay error detectable.
- El síndrome **solo depende del error**, no del mensaje.
- Si el error es de un bit en la posición $i$, $\mathbf s$ es la columna $i$ de $H$
  (la fila $i$ de $H^T$). Eso permite localizar el bit y corregirlo.

### 2.5 Distancia mínima

En clase se usa $d_{min}=3$ para $(7,4)$: el código **detecta hasta 2 errores y
corrige 1**. La razón es que las 7 columnas de $H$ son distintas y no nulas: cada
error de un bit da un síndrome propio.

### 2.6 Ejemplo 1: repetición (5,1)

Con $k=1$ y $P=[1\;1\;1\;1]$, la matriz de verificación es

$$H=\begin{bmatrix}1&0&0&0&1\\0&1&0&0&1\\0&0&1&0&1\\0&0&0&1&1\end{bmatrix}\qquad H^{T}=\begin{bmatrix}1&0&0&0\\0&1&0&0\\0&0&1&0\\0&0&0&1\\1&1&1&1\end{bmatrix}$$

Las palabras válidas son $00000$ y $11111$. Síndromes de error de un bit:

| Error en posición | Síndrome |
|---|---|
| 1 | $1000$ |
| 2 | $0100$ |
| 3 | $0010$ |
| 4 | $0001$ |

Se recibe $\mathbf r=[1\,0\,1\,1\,1]$:

$$\mathbf r\,H^{T}=0100=s_2\;\Rightarrow\;\text{error en la posición 2}\;\Rightarrow\;\hat{\mathbf c}=[1\,1\,1\,1\,1]$$

### 2.7 Ejemplo 2: Hamming (7,4)

$$P=\begin{bmatrix}1&1&0\\0&1&1\\1&1&1\\1&0&1\end{bmatrix}\qquad G=[P:I_4]\qquad H=[I_3:P^{T}]=\begin{bmatrix}1&0&0&1&0&1&1\\0&1&0&1&1&1&0\\0&0&1&0&1&1&1\end{bmatrix}$$

**Codificación.** Mensaje $\mathbf m=[1\,0\,1\,1]$. Se suman las filas de $P$ de
los bits de mensaje que valen 1 (filas 1, 3 y 4):

$$\mathbf b=\mathbf mP=[1,1,0]\oplus[1,1,1]\oplus[1,0,1]=[1,0,0]\quad\Rightarrow\quad \mathbf c=[\mathbf b:\mathbf m]=[1\,0\,0\,1\,0\,1\,1]$$

**Síndrome.** El canal cambia el bit 5, así que $\mathbf r=[1\,0\,0\,1\,1\,1\,1]$:

$$\mathbf s=\mathbf r\,H^{T}=011$$

Ese vector coincide con la **columna 5 de $H$**, así que se corrige el bit 5 y se
recupera $\mathbf c$.

### 2.8 Lo que el apunte no resuelve

- El «111» encerrado bajo $P$ en la foto de clase no se usa aquí. Queda por
  confirmar con el profesor.
- La matriz de ejemplo de $2\times5$ de la página 4 no se lee bien en las fotos
  y no se usa.

---

## 3. Dónde encaja en el proyecto

La protección va **dentro de la sesión PDU**, entre el marco comprimido y el
socket del shim. No modifica el `Compresor` ni el bandido UCB. Se activa con la
variable de entorno `FEC` (`hamming_7_4` o `repeticion_5_1`; vacía = sin FEC), que
`docker-compose.yaml` pasa igual a `cmp-ue` y `cmp-dn`.

```
 cmp-ue                                                       cmp-dn
 app ─▶ Compresor (ROHC + códec elegido por el bandido)
          │  marco comprimido (su byte 0 lleva el id del códec)
          ▼
       FEC: codifica [seq 2 B][largo 2 B][marco comprimido] ──▶ palabras código
          │
          ▼
       marco = [1 B id del CÓDIGO FEC][palabras]  ──▶ sendto (shim) ─▶ uesimtun0 ─▶ gNB ─▶ GTP-U ─▶ UPF ─▶ ...
                                                                                                │
 ... ─▶ recvfrom ─▶ [canal: voltea los bits armados] ─▶ FEC: decodifica por síndrome ─▶ Descompresor ─▶ TUN
```

**Formato del marco en el cable**

| Parte | Tamaño | Protegida por FEC |
|---|---|---|
| Id del **código FEC** (1 = repetición (5,1), 2 = Hamming (7,4)) | 1 B (byte 0) | **No.** Viaja sin protección |
| Palabras código del contenido | variable | Sí |

El **contenido codificado** (lo que entra a la FEC) es:

| Campo | Tamaño |
|---|---|
| Número de secuencia (propio de la FEC, módulo 65536) | 2 B |
| Largo de los datos | 2 B |
| Marco comprimido (su byte 0: id del códec del bandido en los 4 bits bajos; el bit 7 indica cabecera ROHC-lite) | variable |

El contenido se rellena con ceros hasta un múltiplo de $k$ bits. El **id del
códec del bandido va protegido**: está dentro de las palabras código, no en el
byte 0 del cable.

**Qué pasa si se voltea el byte 0.** Es el id del código FEC y no está
protegido. Si se altera, `fec.decodificar` lanza `ValueError` (id desconocido o
que no corresponde al código configurado), el marco no se entrega a la TUN, el
paquete queda con estado **`invalido`** y suma al contador de errores del shim.
La inyección manual nunca toca el byte 0 (solo posiciones desde el bit 8), así
que este caso se comprueba con pruebas unitarias y no desde la página.

**Módulos**

- `compresion/fec.py`: códigos de bloque lineales (`CodigoLineal`; instancias
  `REPETICION_5_1` y `HAMMING_7_4`), con `codificar`, `decodificar` por síndrome y
  `buscar` (por objeto, id o nombre). Es el punto de enchufe de las siguientes
  entregas.
- `compresion/canal.py`: `inyectar` (voltea los bits indicados) y
  `elegir_posiciones` (elige posiciones al azar, con semilla opcional).
- `compresion/registro_fec.py`: `RegistroFec` (anillo con los últimos 200
  paquetes, id incremental desde 1), `CapaFec` (secuencia, canal armado, registro,
  contadores y comandos), `mtu_con_fec` y `aplicar_canal`. Es puro: no abre TUN ni
  sockets, y por eso se puede probar sin laboratorio.
- `compresion/shim.py`: crea la `CapaFec` si `FEC` está definida, abre `cmp0` con
  el MTU que corresponde y sirve la línea de comandos.
- `compresion/envio.py`: endpoints HTTP y página de la [§5](#5-visualización-laboratorio-de-síndrome).
- Pruebas: `test_fec.py`, `test_registro_fec.py` y `test_envio_fec.py` (más
  `test_compresores.py` y `test_reproductores.py`, que no son de FEC).

### 3.1 MTU de cmp0 con FEC

Con FEC el marco crece $n/k$, así que el MTU de `cmp0` baja para que el marco
quepa en `uesimtun0` (MTU 1464, menos 28 B de IP y UDP del túnel). La variable
`CMP_MTU` **se ignora** cuando hay FEC. La fórmula (`mtu_con_fec`):

$$\text{bloques}=\left\lfloor\frac{8\,(1464-28-1)}{n}\right\rfloor\qquad
L_{max}=\left\lfloor\frac{\text{bloques}\cdot k-32}{8}\right\rfloor\qquad
\text{MTU}=L_{max}-36$$

Resultado: **780** con Hamming (7,4) y **247** con repetición (5,1). Sin FEC sigue
valiendo `CMP_MTU` (1400 por defecto).

### 3.2 Costo de CPU

La FEC corre **en el bucle principal** del shim, el mismo que lee la TUN y el
socket. Medido en un PC (no en el LXC 110): unos 2 a 4 ms por paquete de 780 B con
Hamming (7,4) y hasta unos 12 ms con repetición (5,1). **No está medido en el
LXC 110**, que es más lento y sufre con la carga de E/S, así que el costo real
allí puede ser mayor.

### 3.3 Configuración y avisos

- `ue` y `dn` deben tener el **mismo** `FEC`. Si difieren, los marcos de un lado no
  se decodifican en el otro (estado `invalido`).
- Un valor de `FEC` que no existe hace **caer el shim al arrancar** (`fec.buscar`
  lanza `ValueError`).
- Los keepalive no pasan por la FEC.

---

## 4. Inyección manual del error

La página de envío ([docs/08 §4.9](08-compresion-paquetes.md)) permite armar un
error antes de mandar un archivo.

**Dónde se aplica.** El error se inyecta en la **recepción del shim de `cmp-dn`**,
antes de decodificar. Equivale a un canal ruidoso entre codificar y decodificar, y
deja todo el registro en un solo proceso. Consecuencia: **solo se ve el tráfico que
recibe `cmp-dn`**, por ejemplo la vuelta del eco. El tráfico que `cmp-dn` envía y
recibe el UE no pasa por este canal.

**Cómo funciona**

1. La página (o un cliente HTTP) arma el error: voltear $N$ bits al azar, con $N$
   entre 1 y 8, o voltear posiciones exactas (enteros $\ge 8$ dentro del marco).
2. El shim lo guarda como «armada» y la aplica **al siguiente marco FEC que
   reciba**; después se desarma sola.
3. Se calcula $\mathbf r=\mathbf c+\mathbf e$, donde $\mathbf e$ tiene un 1 en cada
   bit volteado, y recién entonces se decodifica por síndrome.
4. El paquete queda en el registro con los bits volteados, los síndromes, las
   posiciones corregidas y el estado.

**Decisión de diseño.** La inyección solo cambia bits de las palabras código
(desde el bit 8), nunca el byte 0 (id del código FEC). Así el experimento mide la
protección de la FEC y no un fallo del identificador, que está fuera de la
protección.

**Servidor de línea** en `127.0.0.1:7702` (variable `PUERTO_FEC`), solo local al
netns del shim. El cliente manda **una línea** (hasta 4 KB, 2 s) y lee el JSON
hasta el cierre. Verbos:

| Verbo | Efecto |
|---|---|
| `PAQUETES <desde>` | Paquetes del registro con id **mayor** que `<desde>` (exclusivo), más el código y la armada actual |
| `INYECTAR <json>` | Arma el error: `{"bits": N}` o `{"posiciones": [...]}`, `semilla` opcional |
| `CODIGO` | Nombre, $n$, $k$ y matrices $P$ y $H$ del código activo |
| `CONTADORES` | Contadores del shim |

**Endpoints HTTP** de `envio.py pagina` (en el puerto de la página):

| Endpoint | Efecto |
|---|---|
| `GET /fec/codigo` | Equivale a `CODIGO` |
| `GET /fec/paquetes?desde=<id>` | Equivale a `PAQUETES <id>` |
| `POST /fec/inyectar` | Equivale a `INYECTAR`; cuerpo JSON de hasta 1 KB, con `bits` entre 1 y 8, o hasta 8 `posiciones` $\ge 8$. **`Content-Type: application/json` es obligatorio** |
| `POST /enviar` con cabecera `X-Inyectar: N` | Arma $N$ bits (1 a 8) justo antes de mandar el archivo y devuelve en el resultado los paquetes FEC observados y su resumen |

Si el shim no responde (sin FEC o `cmp-dn` caído), los endpoints `/fec/*` devuelven 503.

**Seguridad.** La página **no tiene login**: quien esté en la LAN puede armar
errores mientras la FEC esté activa. Debe exponerse solo en la LAN o la tailnet,
**nunca por Funnel**.

El error se aplica por orden manual, no por canal aleatorio. El canal aleatorio es
parte de la siguiente entrega.

---

## 5. Visualización «Laboratorio de síndrome»

Es la segunda pestaña de la página de envío. Consulta `GET /fec/paquetes` cada
1,5 s. Todo texto que llega del shim se inserta como texto, no como HTML.

**Código activo.** Dibuja las matrices $G=[P:I_k]$ y $H=[I:P^{T}]$, con la parte
de paridad sombreada. Si el shim no tiene FEC, lo indica y la pestaña de envío
sigue funcionando.

**Inyección.** Botones para armar 1 bit, 2 bits o un bit exacto (entero $\ge 8$), y
una línea que dice qué hay armado.

**Nivel 1: línea y tabla de paquetes.** Una casilla por paquete y una tabla (últimos
100, con filtro por estado) con: id, lado, secuencia, códec elegido por el bandido
UCB, estado, síndromes distintos de cero, corregidas, no corregibles y bits
volteados. Estados:

- **limpio**: sin síndrome distinto de cero;
- **corregido**: se localizó y corrigió un error de un bit en cada palabra afectada;
- **no corregible**: síndrome que no coincide con ninguna columna de $H$
  (por ejemplo, repetición (5,1) con 2 o más errores en una palabra);
- **inválido**: el marco no se pudo decodificar (id de código alterado, largo
  inconsistente). No se entrega a la TUN.

**Nivel 2: detalle de un paquete.** Al abrirlo se lista paso a paso:

1. Códec elegido (nombre e id), lado, secuencia y código.
2. Bytes comprimidos, bytes con paridad (sobrecoste) y número de palabras.
3. Bits volteados en el canal, con su posición en el marco.
4. Cada síndrome observado ($\mathbf s\neq 0$), cuántas veces apareció, qué
   columna de $H$ coincide y cuántas palabras se corrigieron.
5. Lista de correcciones (palabra y posición corregida). La numeración de las
   posiciones es **desde 1**, como en clase.
6. Resultado e íntegro (ver el límite de abajo).

**Lo que NO está implementado.** El detalle **no dibuja las palabras una por una**
con la paridad resaltada, ni la palabra recibida con los bits volteados marcados,
ni la palabra corregida. Solo se dibujan las matrices $G$ y $H$; el resto es texto
con los síndromes y las posiciones.

**Límites que hay que saber al leer los resultados**

- Un paquete **`no_corregible` se entrega igual a la TUN**: el shim descomprime lo
  que decodificó, aunque sea basura.
- **`integro` solo significa «descomprimió sin excepción»**. El receptor no tiene
  el paquete original, así que no se compara con él. Una corrección errónea
  puede dar `integro = sí` con datos distintos; lo detectaría el checksum de IP,
  TCP o UDP de la aplicación. En `/enviar`, `sha_identico` sí compara el archivo
  de ida con el de vuelta.
- Con **2 errores en una misma palabra Hamming (7,4)** el decodificador corrige mal
  sin avisar (ver [§7](#7-relación-con-la-compresión)).

---

## 6. Grafana (diseño, tablero aún no construido)

Regla del proyecto: pocos paneles y cada uno con su explicación. Cada panel se
describe con **Significado / Cómo funciona / Eje X / Eje Y / A qué responde**.
Ningún panel se construye sin aprobación previa. **El tablero es solo diseño; no
existe todavía.** Las métricas sí existen.

**Métricas ya implementadas** en `shim.py`, empujadas al pushgateway cada 10 s, con
las etiquetas `lado` y `codigo` (nombre del código FEC) en todas. **No se han
visto aún en el pushgateway real.** Solo aparecen si el shim tiene FEC activa.

| Métrica | Etiquetas extra | Qué cuenta |
|---|---|---|
| `lab5g_cmp_fec_paquetes_total` | `estado`, `codec` | Paquetes recibidos por estado (limpio, corregido, no_corregible, invalido) y códec |
| `lab5g_cmp_fec_sindrome_total` | `sindrome` | Palabras por valor de síndrome distinto de cero |
| `lab5g_cmp_fec_bits_inyectados_total` | ninguna | Bits volteados por la inyección |
| `lab5g_cmp_fec_palabras_total` | ninguna | Palabras código decodificadas |
| `lab5g_cmp_fec_bytes_total` | `tipo` (`comprimido`, `fec`) | Bytes emitidos antes y después de añadir la paridad |

Los paneles son previstos; el panel 1 necesita contadores por etapa (recibidos, entregados) que las métricas actuales no tienen del todo, y habría que añadirlos.

**Panel 1. Embudo del flujo**

- **Significado:** cuántos paquetes llegan a cada etapa, desde el envío hasta la
  entrega.
- **Cómo funciona:** barras con el total de paquetes en cada etapa (codificados,
  recibidos, decodificados, entregados).
- **Eje X:** etapa.
- **Eje Y:** número de paquetes.
- **A qué responde:** dónde se pierde un paquete, si en la red o en la decodificación.

**Panel 2. Paquetes por estado en el tiempo**

- **Significado:** la evolución de limpios, corregidos y no corregibles.
- **Cómo funciona:** serie temporal apilada.
- **Eje X:** tiempo.
- **Eje Y:** paquetes por ventana.
- **A qué responde:** si la tasa de corrección sube o baja al inyectar más errores.

**Panel 3. Síndromes por valor**

- **Significado:** qué síndromes aparecen y con qué frecuencia.
- **Cómo funciona:** histograma de los valores de $\mathbf s$.
- **Eje X:** valor del síndrome (los 8 posibles en Hamming (7,4)).
- **Eje Y:** número de paquetes.
- **A qué responde:** si los errores caen donde el código los corrige o si hay
  síndromes que apuntan a columnas incorrectas.

**Panel 4. Códec del bandido × estado**

- **Significado:** qué códec elige el bandido y cómo le va a cada uno con la FEC.
- **Cómo funciona:** mapa de calor, códec en el eje Y y estado en el X.
- **Eje X:** estado del paquete.
- **Eje Y:** códec.
- **A qué responde:** si el bandido sigue eligiendo códecs frágiles con errores
  presentes.

---

## 7. Relación con la compresión

- **Comprimir quita redundancia; la FEC la añade.** Un paquete comprimido tiene
  casi toda la información en pocos bits, así que un bit cambiado puede dañar más.
- **Estado en la compresión.** El códec DEFLATE por flujo guarda estado entre
  paquetes ([docs/08 §4.4](08-compresion-paquetes.md)): un paquete perdido o mal
  decodificado desincroniza a los siguientes. La FEC corrige errores de bit, pero no
  arregla un paquete que se pierde entero.
- **Costo de la FEC.** Hamming (7,4) envía $n/k = 7/4 = 1{,}75$ veces los bytes,
  es decir, un 75 % más. La repetición (5,1) envía 5 veces los bytes.
- **Cuenta honesta.** La compresión medida ahorra alrededor de un 21 % del
  tráfico ([docs/08 §4.2](08-compresion-paquetes.md)). Si se aplica Hamming (7,4)
  encima, el resultado neto es $0{,}79 \times 1{,}75 \approx 1{,}38$ veces los bytes
  originales. Es decir, un 38 % más en el aire. Esta cifra es un cálculo
  aritmético, no una medida.
- **Comparación que sí vale.** La comparación justa es **bytes totales en el
  aire** frente a **errores residuales** (paquetes mal entregados), no solo ahorro
  de compresión.
- **Límite de Hamming (7,4).** Con dos errores, el síndrome nunca vale 0, así que
  el error se nota. Pero en Hamming (7,4) cada síndrome no nulo coincide con la
  columna de algún bit, y la suma de dos columnas es siempre una tercera columna.
  El decodificador **corrige mal sin avisar**. Esta es la razón de la siguiente
  entrega (SECDED, §9).

---

## 8. Estado del arte

Esta sección es investigación de referencia. No se implementa en esta entrega.
Las fuentes son las indicadas; las afirmaciones sin fuente son de conocimiento
general y no contienen cifras.

### 8.1 Códigos de bloque clásicos

| Técnica | Dónde se usa | Cómo funciona | Relación con la compresión del proyecto |
|---|---|---|---|
| Repetición | Canales muy ruidosos y enlaces de control simples | Cada bit se envía varias veces y se vota por mayoría | Es el código más caro. Aquí es el caso de referencia (5,1) |
| Hamming (7,4) | Memoria ECC de corrección de un bit | Paridad con síndrome; corrige 1 error, detecta 2 | Es la entrega actual. Su costo (+75 %) supera el ahorro de la compresión |
| SECDED (Hamming extendido) | Memoria ECC de servidor | Añade un bit de paridad global: detecta 2 errores sin corregir mal | Es el primer paso de la siguiente entrega |
| CRC | Casi todos los protocolos: Ethernet, 5G (en bloques de transporte) | Polinomio sobre el mensaje; detecta ráfagas de error, no corrige | Es barato y no añade bytes grandes; es lo que conviene junto a la compresión con estado |

### 8.2 Códigos para ráfagas, borrado y paquetes

| Técnica | Dónde se usa | Cómo funciona | Relación con la compresión del proyecto |
|---|---|---|---|
| Reed-Solomon | Discos ópticos, códigos QR, transmisión satelital | Códigos sobre símbolos de varios bits; corrige ráfagas y pérdidas de símbolos conocidos | Corrige ráfagas, que Hamming no. Su costo depende de cuánta redundancia se elija |
| Paridad entre paquetes (XOR) | Multimedia en tiempo real y RAID | Un paquete de paridad es la XOR de un grupo; recupera uno perdido | Opera sobre el flujo y no sobre cada paquete. Compatible con la compresión, pero añade latencia del grupo |
| RaptorQ (RFC 6330) | Distribución por broadcast y multicast | Código fuente sin tasa fija: se generan paquetes de reparación según se necesiten | Pensado para pérdidas de paquetes, no de bits. Encaja con el tráfico de borrado de la siguiente entrega |

### 8.3 Códigos del estándar 5G y decodificadores modernos

| Técnica | Dónde se usa | Cómo funciona | Relación con la compresión del proyecto |
|---|---|---|---|
| LDPC | Canal de datos compartido de 5G NR | Matriz de paridad dispersa con decodificación iterativa por creencia | El túnel viaja sobre la radio 5G, que ya usa LDPC. Esta capa protege de extremo a extremo |
| Polar con CRC (SCL) | Canal de control de 5G NR | Polarización de canales; decodificación de lista (SCL) apoyada en CRC | Igual que LDPC: la radio ya lo aplica por debajo |
| Decodificadores neuronales unificados | Investigación | Un transformador aprende a decodificar distintos códigos con una sola arquitectura | Investigación. No se usa en el proyecto |

**Fuentes**

- [Demystifying 5G Polar and LDPC Codes](https://arxiv.org/abs/2502.11053). Según esta fuente, 3GPP mantendría LDPC y Polar para 6G (oct 2025). **Sin verificar en las especificaciones de 3GPP.**
- [Unified Error Correction Code Transformer](https://arxiv.org/abs/2410.03364). Decodificador unificado basado en transformador.
- [arXiv 2603.23123](https://arxiv.org/pdf/2603.23123). Referencia adicional de la lista de fuentes.
- [Survey de aprendizaje profundo para códigos de corrección](https://people.montefiore.uliege.be/lwh/Info/DL4CC-Survey.pdf). Revisión de decodificadores basados en aprendizaje profundo.

**Nota sobre la radio.** El túnel de esta red viaja sobre una radio 5G simulada
por UERANSIM, que no aplica LDPC ni Polar. Por eso la protección de esta capa es
extremo a extremo y de fin didáctico. En una radio real, la radio ya corrige
errores por debajo.

---

## 9. Hoja de ruta: siguiente entrega

Todo lo siguiente queda fuera de esta entrega:

- **SECDED (8,4):** detecta dos errores sin corregirlos mal. Corrige un error y
  da aviso con dos.
- **CRC:** detección de error de bloque para el marco completo.
- **Entrelazado:** reparte los bits de un paquete entre varios paquetes para que
  una ráfaga quede repartida.
- **Reed-Solomon:** corrección de ráfagas y de símbolos perdidos.
- **XOR entre paquetes:** recuperación de paquetes perdidos del flujo.
- **Modelos de canal aleatorios:** BSC (error independiente por bit), ráfagas
  Gilbert-Elliott y borrado de paquetes.
- **RaptorQ:** código de fuente para pérdidas de paquetes.

**Cómo se enchufan.** Cada código nuevo implementa la interfaz `CodigoCanal` de
`compresion/fec.py`. El shim no cambia: solo elige qué código usar. Cada modelo
de canal nuevo es un generador de errores que `canal.py` aplica en el mismo
punto que la inyección manual.

---

## 10. Cómo probar

Desde la raíz del repositorio, con Python 3. Son los cinco archivos que corre el
paso «Pruebas de compresion» del workflow
[`deploy.yml`](../.github/workflows/deploy.yml):

```bash
python3 -m unittest compresion/test_compresores.py compresion/test_reproductores.py compresion/test_fec.py compresion/test_envio_fec.py compresion/test_registro_fec.py
```

| Archivo | Qué cubre | Pruebas |
|---|---|---|
| `compresion/test_fec.py` | Códigos de repetición y Hamming; ejemplos exactos de clase; marco y síndromes | 18 |
| `compresion/test_registro_fec.py` | `CapaFec`, `RegistroFec`, canal armado, `mtu_con_fec`, métricas y comandos | 15 |
| `compresion/test_envio_fec.py` | Validación de `/fec/inyectar`, `X-Inyectar` y resumen de paquetes | 11 |
| `compresion/test_compresores.py` | Compresión (no es de FEC) | 32 |
| `compresion/test_reproductores.py` | Reproductores de tráfico (no es de FEC) | 6 |

**Resultado ejecutado en un PC con Windows:** unas 82 pruebas OK en total. Son
pruebas unitarias; ninguna toca la TUN ni el laboratorio.

**Lo que falta probar en el laboratorio (no probado):**

- Levantar `cmp-ue` y `cmp-dn` con el mismo `FEC` y comprobar que el MTU de `cmp0`
  queda en 780 (Hamming (7,4)) o 247 (repetición (5,1)).
- Prueba de extremo a extremo con envío de un archivo desde la página: que el
  archivo vuelva idéntico (`sha_identico`) sin inyección.
- Inyección de 1 bit: comprobar que aparecen paquetes `corregido` y que el archivo
  vuelve idéntico.
- Inyección de 2 bits con Hamming (7,4): ver la corrección errónea descrita en la
  [§7](#7-relación-con-la-compresión).
- Que las métricas `lab5g_cmp_fec_*` lleguen al pushgateway con las etiquetas
  `lado` y `codigo`.
- El costo de CPU de la FEC en el LXC 110 y su efecto en el rendimiento del túnel.

---

**Anterior:** [09 — Arranque seguro y recuperación](09-arranque-seguro.md) ·
**Volver al** [README](../README.md)
