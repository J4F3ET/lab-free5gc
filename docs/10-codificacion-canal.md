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
- [6. Grafana (tablero `lab5g-cmp-fec`)](#6-grafana-tablero-lab5g-cmp-fec)
- [7. Relación con la compresión](#7-relación-con-la-compresión)
- [8. Estado del arte](#8-estado-del-arte)
- [9. Hoja de ruta: siguiente entrega](#9-hoja-de-ruta-siguiente-entrega)
- [10. Resultados del banco offline](#10-resultados-del-banco-offline)
- [11. Cómo probar](#11-cómo-probar)
- [12. Banda en el aire: códigos más baratos y el bandido](#12-banda-en-el-aire-códigos-más-baratos-y-el-bandido-24-11-2026)

---

## 1. Objetivo y alcance de esta entrega

**Objetivo.** Que un paquete que llega con un bit cambiado por el canal pueda
detectarse y, si el error es de un bit y el código lo permite, corregirse en
`cmp-dn` antes de entregarlo.

**Alcance: lo que sí entra en esta entrega**

- Códigos de bloque lineales vistos en clase, en tres instancias:
  - **Repetición (5,1)**: cada bit se envía cinco veces.
  - **Hamming (7,4)**: cuatro bits de mensaje y tres de paridad.
  - **SECDED (8,4)** (Hamming extendido de Hsiao): corrige 1 error y detecta 2 sin
    corregirlos mal ([§2.9](#29-secded-84)).
- Decodificación por **síndrome**, tal como aparece en el apunte.
- **Inyección de error**: manual (la página de envío pide voltear de 1 a 8 bits del
  siguiente marco que reciba `cmp-dn`) y **canales aleatorios** BSC y de ráfagas
  (Gilbert-Elliott), activados con la variable `CANAL` ([§4](#canales-aleatorios)).
- **Banco offline** que mide el BER residual de cada código frente al BER de
  entrada ([§10](#10-resultados-del-banco-offline)).
- Integración en el shim (`shim.py`), servidor de línea y endpoints HTTP, página
  «Laboratorio de síndrome» y métricas `lab5g_cmp_fec_*`.

**Siguiente entrega (no implementado aquí):** CRC, entrelazado, Reed-Solomon,
paridad entre paquetes (XOR), RaptorQ y el canal de borrado de paquetes. Ver
[§9](#9-hoja-de-ruta-siguiente-entrega).

**Estado de la implementación**

El código tiene 187 pruebas unitarias (en los archivos de [§11](#11-cómo-probar),
ejecutadas en un PC con Windows con 187 OK) y **se probó de extremo a extremo en el
laboratorio real el 22-11-2026** con `FEC=hamming_7_4` en `cmp-ue` y `cmp-dn`:
inyección de 1 y 2 bits, síndromes observados, archivo de vuelta con SHA idéntico,
métricas en el pushgateway y consultas del tablero contestadas por Prometheus.
**SECDED (8,4) y el canal aleatorio BSC también se probaron en el laboratorio el
23-11-2026** (`FEC=secded_8_4`, `CANAL=bsc:1e-3`); el canal de ráfagas solo está probado
con pruebas unitarias y en el banco offline. Los resultados y las reservas están en
[§11](#11-cómo-probar).

| Pieza | Estado |
|---|---|
| `compresion/fec.py` (códigos de repetición, Hamming y SECDED (8,4), `CodigoLineal`, `codificar`/`decodificar` sobre bytes) | Implementado y probado con pruebas unitarias; los ejemplos de clase son pruebas exactas; SECDED con pruebas de corrección de 1 bit y de detección de 2 |
| `compresion/canal.py` (`inyectar`, `elegir_posiciones`, canales `bsc` y `rafaga`, `parse_canal`) | Implementado y probado con pruebas unitarias; `bsc` probado en el laboratorio (23-11); `rafaga` solo con pruebas unitarias y banco offline |
| `compresion/registro_fec.py` (`RegistroFec`, `CapaFec`, `mtu_con_fec`, `aplicar_canal`) | Implementado y probado con pruebas unitarias (sin TUN ni sockets) |
| Integración en `compresion/shim.py` (variable `FEC`, servidor de línea, métricas) | Implementada y **probada en el laboratorio** con Hamming (22-11), con SECDED y con `CANAL=bsc:1e-3` (23-11) |
| Endpoints HTTP y página «Laboratorio de síndrome» (`compresion/envio.py`) | Implementados; las pruebas unitarias cubren la validación y el resumen (`test_envio_fec.py`); probados en el laboratorio con `/enviar` y `/fec/inyectar` |
| Métricas `lab5g_cmp_fec_*` | Verificadas en el pushgateway y en Prometheus (22-11) |
| Tablero de Grafana | **Construido**, con 7 elementos (un texto y 6 paneles, incluidos «BER de entrada del canal» y «Sobrecoste de la protección») (`lab5g-cmp-fec`; sus consultas se probaron contra Prometheus y el tablero se vio renderizado en Grafana el 24-11, con datos coherentes con el tráfico) ([§6](#6-grafana-tablero-lab5g-cmp-fec)) |
| Banco offline (`compresion/banco_fec.py`, `python compresion/evaluar.py fec`) y `compresion/resultados/fec.md` | Implementado y probado con pruebas unitarias; las cifras de [§10](#10-resultados-del-banco-offline) salen de ese archivo |

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

### 2.9 SECDED (8,4)

SECDED (8,4) es el código de Hsiao: la misma forma $H=[I_4:P^{T}]$, pero con las cuatro
filas de $P$ de peso 3. Así cada columna de $P^{T}$ tiene peso 3 y las 8 columnas de
$H$ son distintas y de peso impar (cuatro de peso 1 y cuatro de peso 3). Su distancia
mínima es $d_{min}=4$: corrige 1 error y detecta 2.

$$P=\begin{bmatrix}1&1&1&0\\1&1&0&1\\1&0&1&1\\0&1&1&1\end{bmatrix}\qquad
H=\begin{bmatrix}1&0&0&0&1&1&1&0\\0&1&0&0&1&1&0&1\\0&0&1&0&1&0&1&1\\0&0&0&1&0&1&1&1\end{bmatrix}$$

Regla del decodificador, según el peso del síndrome $\mathbf s\neq 0$:

| Síndrome | Significa | Acción |
|---|---|---|
| $\mathbf s=0$ | Sin error | Entregar |
| $\mathbf s\neq 0$ de **peso impar** | Un error: coincide con una columna de $H$ | Corregir ese bit |
| $\mathbf s\neq 0$ de **peso par** | Dos errores: nunca coincide con una columna | **Detectar, no corregir** |

¿Por qué los dobles dan siempre peso par? La suma de dos columnas de peso impar tiene
peso par. Como las columnas son distintas, esa suma nunca es cero, y por tanto nunca
coincide con una columna, que son todas de peso impar.

**Contraste con Hamming (7,4) con dos errores.** Con la palabra
$\mathbf c=[1\,0\,0\,1\,0\,1\,1]$ del ejemplo de [§2.7](#27-ejemplo-2-hamming-74) y errores
en los bits 1 y 2, $\mathbf r=[0\,1\,0\,1\,0\,1\,1]$ y $\mathbf s=\mathbf r\,H^{T}=110$,
que es la columna 4 de $H$. Hamming corrige el bit 4, que estaba bien, y queda con tres
errores sin aviso. Con SECDED, el mensaje $\mathbf m=[1\,0\,1\,1]$ se codifica en
$\mathbf c=[0\,0\,1\,0\,1\,0\,1\,1]$. Con errores en los bits 1 y 5,
$\mathbf r=[1\,0\,1\,0\,0\,0\,1\,1]$ y $\mathbf s=0110$: peso 2 y sin columna igual, así
que se **detecta** el doble error y no se toca ningún bit.

- **Costo:** $n/k=2$, el doble de bits que el mensaje. Con $n=8$ el MTU de `cmp0` baja
  a **677** (`mtu_con_fec`, [§3.1](#31-mtu-de-cmp0-con-fec)).
- **Activación:** `FEC=secded_8_4` (id 3 en el marco, [§3](#3-dónde-encaja-en-el-proyecto)).
  Las pruebas cubren que todo error de un bit se corrige para cualquier mensaje y que los
  dobles dan síndrome de peso par, sin corrección.

---

## 3. Dónde encaja en el proyecto

La protección va **dentro de la sesión PDU**, entre el marco comprimido y el
socket del shim. No modifica el `Compresor` ni el bandido UCB. Se activa con la
variable de entorno `FEC` (`secded_8_4`, `hamming_7_4` o `repeticion_5_1`; vacía = sin FEC), que
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
 ... ─▶ recvfrom ─▶ [canal: voltea la armada manual y, si hay, el canal aleatorio] ─▶ FEC: decodifica por síndrome ─▶ Descompresor ─▶ TUN
```

**Formato del marco en el cable**

| Parte | Tamaño | Protegida por FEC |
|---|---|---|
| Id del **código FEC** (1 = repetición (5,1), 2 = Hamming (7,4), 3 = SECDED (8,4)) | 1 B (byte 0) | **No.** Viaja sin protección |
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
  `REPETICION_5_1`, `HAMMING_7_4` y `SECDED_8_4`), con `codificar`, `decodificar` por síndrome y
  `buscar` (por objeto, id o nombre). Es el punto de enchufe de las siguientes
  entregas.
- `compresion/canal.py`: `inyectar` (voltea los bits indicados), `elegir_posiciones`
  (elige posiciones al azar, con semilla opcional), los canales aleatorios `bsc` y
  `rafaga` (Gilbert-Elliott) y `parse_canal`, que lee la variable `CANAL`
  ([§4](#canales-aleatorios)).
- `compresion/registro_fec.py`: `RegistroFec` (anillo con los últimos 200
  paquetes, id incremental desde 1), `CapaFec` (secuencia, canal armado, registro,
  contadores y comandos), `mtu_con_fec` y `aplicar_canal`. Es puro: no abre TUN ni
  sockets, y por eso se puede probar sin laboratorio.
- `compresion/shim.py`: crea la `CapaFec` si `FEC` está definida, abre `cmp0` con
  el MTU que corresponde y sirve la línea de comandos.
- `compresion/envio.py`: endpoints HTTP y página de la [§5](#5-visualización-laboratorio-de-síndrome).
- Pruebas: `test_fec.py`, `test_registro_fec.py`, `test_envio_fec.py`,
  `test_tablero_fec.py`, `test_canal.py`, `test_canal_capa.py` y `test_banco_fec.py`
  (más `test_compresores.py` y `test_reproductores.py`, que no son de FEC; la lista
  completa está en [§11](#11-cómo-probar)).

### 3.1 MTU de cmp0 con FEC

Con FEC el marco crece $n/k$, así que el MTU de `cmp0` baja para que el marco
quepa en `uesimtun0` (MTU 1464, menos 28 B de IP y UDP del túnel). La variable
`CMP_MTU` **se ignora** cuando hay FEC. La fórmula (`mtu_con_fec`):

$$\text{bloques}=\left\lfloor\frac{8\,(1464-28-1)}{n}\right\rfloor\qquad
L_{max}=\left\lfloor\frac{\text{bloques}\cdot k-32}{8}\right\rfloor\qquad
\text{MTU}=L_{max}-36$$

Resultado: **780** con Hamming (7,4), **677** con SECDED (8,4) y **247** con repetición (5,1). Sin FEC sigue
valiendo `CMP_MTU` (1400 por defecto).

### 3.2 Costo de CPU

La FEC corre **en el bucle principal** del shim, el mismo que lee la TUN y el
socket. Medido en un PC (no en el LXC 110): unos 2 a 4 ms por paquete de 780 B con
Hamming (7,4) y hasta unos 12 ms con repetición (5,1). **No está medido en el
LXC 110**, que es más lento y sufre con la carga de E/S, así que el costo real
allí puede ser mayor.

### 3.3 Configuración y avisos

- `ue` y `dn` deben tener el **mismo** `FEC`, `CANAL` y `CANAL_SEMILLA`. Si `FEC`
  difiere, los marcos de un lado no se decodifican en el otro (estado `invalido`). Si
  `CANAL` o la semilla difieren, cada lado recibe errores distintos y el experimento no
  es reproducible.
- `FEC` admite `secded_8_4`, `hamming_7_4` o `repeticion_5_1`. `CANAL` admite `bsc:P`
  (por ejemplo `bsc:1e-3`) o `rafaga:P_MALO,P_BUENO_A_MALO,P_MALO_A_BUENO` (por ejemplo
  `rafaga:0.3,0.01,0.2`). Un valor de `CANAL` mal escrito hace terminar el shim al
  arrancar, igual que `FEC`. Con `FEC` apagada, `CANAL` se ignora y el shim lo avisa.
  El canal solo tiene efecto con `FEC` activa.
- `CANAL_SEMILLA` (entero, por defecto 1) es la semilla del canal. El marco $n$ que
  recibe un lado usa la semilla `CANAL_SEMILLA` + $n$.
- Para cambiar `FEC` o `CANAL` hay que fijar las variables con `lab5g.sh env-set` y
  reiniciar `cmp-ue` y `cmp-dn`, como indica la página de envío.
- Un valor de `FEC` que no existe hace **terminar el shim al arrancar** con un mensaje claro
  (`validar_codigo`, antes de esperar a `uesimtun0`).
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
| `CONTADORES` | Contadores acumulados desde el arranque del shim (no dependen del anillo de 200 paquetes). `POST /enviar` pide estos contadores antes y después del envío y el resumen es la diferencia; el bloque `fec` trae `resumen_fuente` (`contadores` o `anillo`, si el shim es viejo) y `truncado` (la lista de paquetes no cubre todos los nuevos) |

**Endpoints HTTP** de `envio.py pagina` (en el puerto de la página):

| Endpoint | Efecto |
|---|---|
| `GET /fec/codigo` | Equivale a `CODIGO` |
| `GET /fec/paquetes?desde=<id>` | Equivale a `PAQUETES <id>` |
| `POST /fec/inyectar` | Equivale a `INYECTAR`; cuerpo JSON de hasta 1 KB, con `bits` entre 1 y 8, o hasta 8 `posiciones` $\ge 8$. **`Content-Type: application/json` es obligatorio** |
| `POST /enviar` con cabecera `X-Inyectar: N` | Arma $N$ bits (1 a 8) justo antes de mandar el archivo (la orden la consume el **siguiente marco que recibe `cmp-dn`**, normalmente de `cmp-gen`, así que no garantiza que el error caiga en el archivo; para verlo con certeza, detener `cmp-gen` con `lab5g.sh stop cmp-gen` y volver a arrancarlo con `lab5g.sh start cmp-gen` después) y devuelve en el resultado los paquetes FEC observados y su resumen |

**Nota práctica.** Para ver el error dentro del archivo de `/enviar` hay que parar el generador (`lab5g.sh stop cmp-gen`, y `lab5g.sh start cmp-gen` después), porque la orden armada la gasta el siguiente marco que recibe `cmp-dn`; en las pruebas la gastaron marcos de 50, 54 y 1375 B del generador. Por eso un `sha_identico` con `X-Inyectar` no demuestra por sí solo que el error tocó los datos del archivo.

Si el shim no responde (sin FEC o `cmp-dn` caído), los endpoints `/fec/*` devuelven 503.

**Seguridad.** La página **no tiene login**: quien esté en la LAN puede armar
errores mientras la FEC esté activa. Debe exponerse solo en la LAN o la tailnet,
**nunca por Funnel**.

### Canales aleatorios

Además de la orden manual, la variable `CANAL` activa un canal que voltea bits al azar
en el mismo punto (recepción de `cmp-dn`, antes de decodificar), solo con `FEC` activa:

- **`bsc:P`** (canal binario simétrico): cada bit de las palabras código se voltea con
  probabilidad $P$, de forma independiente. Son errores sueltos, como en el modelo de
  clase.
- **`rafaga:P_MALO,P_BUENO_A_MALO,P_MALO_A_BUENO`** (Gilbert-Elliott): dos estados. En el
  estado bueno no hay errores; en el malo, cada bit se voltea con $P_{MALO}$. En cada bit
  se decide el error con la probabilidad del estado actual y después se cambia de estado
  con $P_{BUENO\to MALO}$ o $P_{MALO\to BUENO}$. Los errores salen agrupados, que es lo
  que Hamming y SECDED no aguantan. El canal empieza en el estado bueno.

**Combinación con la manual.** El canal se aplica después de la armada manual, sobre el
mismo marco, por XOR. Un bit que voltean las dos fuentes queda sin cambio, y el registro
solo cuenta los bits que cambiaron de valor neto. Ese es el número que suma a
`lab5g_cmp_fec_bits_inyectados_total`. El byte 0 (id del código FEC) no se toca.

**Determinismo.** La semilla del marco $n$ es `CANAL_SEMILLA` + $n$, donde $n$ es el
número de marcos que el lado ha recibido. Con la misma semilla y el mismo orden de
marcos salen los mismos errores, así que un experimento se puede repetir.

---

## 5. Visualización «Laboratorio de síndrome»

Es la segunda pestaña de la página de envío. Consulta `GET /fec/paquetes` cada
1,5 s. Todo texto que llega del shim se inserta como texto, no como HTML.

**Código activo.** Dibuja las matrices $G=[P:I_k]$ y $H=[I:P^{T}]$, con la parte
de paridad sombreada. Si el shim no tiene FEC, lo indica y la pestaña de envío
sigue funcionando.

**Inyección.** Botones para armar 1 bit, 2 bits o un bit exacto (entero $\ge 8$), y
una línea que dice qué hay armado. La pestaña también muestra el canal aleatorio activo
(`CANAL`), si lo hay.

**Nivel 1: línea y tabla de paquetes.** Una casilla por paquete y una tabla (últimos
100, con filtro por estado) con: id, lado, secuencia, códec elegido por el bandido
UCB, estado, síndromes distintos de cero, corregidas, no corregibles y bits
volteados. Estados:

- **limpio**: sin síndrome distinto de cero;
- **corregido**: se localizó y corrigió un error de un bit en cada palabra afectada;
- **corregido_mal** («corrigió mal»; estado añadido tras la verificación del 22-11):
  el síndrome indicó una corrección (`corregido`) pero el paquete no se pudo
  reconstruir (`integro = false`: la descompresión falla o la TUN lo rechaza). Es lo
  que pasa típicamente con 2 errores en una palabra Hamming (7,4). No se confunde con
  `corregido` (íntegro), `no_corregible` ni `invalido`. El contador `corregidas` de
  palabras no cambia; solo cambia el estado del paquete;
- **no corregible**: síndrome que no coincide con ninguna columna de $H$
  (por ejemplo, repetición (5,1) con 2 o más errores en una palabra; ojo: $d_{min}=5$ permitiría corregir 2 con una tabla de síndromes completa, pero este decodificador solo corrige un bit por palabra). Con SECDED (8,4), los dobles errores de una palabra
  caen aquí y la página los muestra como «doble detectado»;
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
  sin avisar en el síndrome (ver [§7](#7-relación-con-la-compresión)). Si el paquete
  no se reconstruye, hoy queda como `corregido_mal`; si se reconstruye (por ejemplo,
  el error cae en bytes que no afectan al contenido), sigue contando como `corregido`.

---

## 6. Grafana (tablero `lab5g-cmp-fec`)

Regla del proyecto: pocos paneles y cada uno con su explicación. Cada panel se describe
con **Significado / Cómo funciona / Eje X / Eje Y / A qué responde**, y ese mismo texto
aparece en su **ⓘ** en Grafana.

**El tablero está construido:** `lab5g-cmp-fec`, «Codificación de canal (síndrome)»,
generado por `observability/grafana/dashboards_compresion.py` en
`observability/grafana/provisioning/dashboards/json/lab5g-cmp-fec.json` (se provisiona
solo). Tiene menús `lado` y `codigo`, y siete elementos en cuatro filas: un texto de
ayuda y seis paneles. **No se ha visto aún en un Grafana real.**

| Fila | Elemento | Tipo |
|---|---|---|
| 1 | Cómo leer este tablero | Texto |
| 2 | Qué pasó con los paquetes | Barras horizontales |
| 2 | Estados en el tiempo | Barras apiladas |
| 3 | Síndromes observados | Barras |
| 3 | Códec del bandido por estado | Mapa de calor |
| 4 | BER de entrada del canal | Serie (escala logarítmica) |
| 4 | Sobrecoste de la protección | Serie |

**Métricas ya implementadas** en `shim.py`, empujadas al pushgateway cada 10 s, con las
etiquetas `lado` y `codigo` (nombre del código FEC) en todas. **Ya se ven en el
pushgateway real (22-11).** Solo aparecen si el shim tiene FEC activa.

| Métrica | Etiquetas extra | Qué cuenta |
|---|---|---|
| `lab5g_cmp_fec_paquetes_total` | `estado`, `codec` | Paquetes recibidos por estado (limpio, corregido, corregido_mal, no_corregible, invalido) y códec. La etiqueta `codec` pertenece a un conjunto cerrado precargado a 0: los nombres registrados, `n/a` (marco `invalido`) y `desconocido` (id sin nombre registrado, p. ej. un bit volteado en el byte 0; un `corregido_mal` con id de códec corrupto se etiqueta `codec="desconocido"`, aunque el registro de la página conserva el `codec_id` numérico real) |
| `lab5g_cmp_fec_sindrome_total` | `sindrome` | Palabras por valor de síndrome distinto de cero |
| `lab5g_cmp_fec_bits_inyectados_total` | ninguna | Bits volteados por la inyección manual y por el canal aleatorio |
| `lab5g_cmp_fec_palabras_total` | ninguna | Palabras código decodificadas |
| `lab5g_cmp_fec_bytes_total` | `tipo` (`comprimido`, `fec`) | Bytes emitidos antes y después de añadir la paridad |

En estado `invalido` no se registra el síndrome del paquete (el decodificador falla antes
de producirlo), así que no suma a `lab5g_cmp_fec_sindrome_total`.

El embudo por etapa del diseño inicial no se construyó, porque no hay contadores por
etapa. «Qué pasó con los paquetes» muestra los paquetes por estado en su lugar.

**Cómo leer este tablero (texto).** Explica la idea del síndrome, los estados y el
significado de «doble detectado» con SECDED. No tiene datos.

**Qué pasó con los paquetes**

- **Significado:** cuántos paquetes recibidos cayeron en cada estado en el rango de
  tiempo elegido.
- **Cómo funciona:** `increase()` del contador `lab5g_cmp_fec_paquetes_total` sumado por
  estado, con un color fijo por estado.
- **Eje X:** estado del paquete.
- **Eje Y:** número de paquetes en el rango de tiempo.
- **A qué responde:** cómo le va al código con el canal actual. Si «corregido» crece al
  inyectar errores, la FEC trabaja; «corregido_mal» es lo que corrigió mal, y
  «no_corregible» lo que se le escapa.

**Estados en el tiempo**

- **Significado:** cuántos paquetes por minuto llegan en cada estado, a lo largo del
  tiempo.
- **Cómo funciona:** `rate()` del contador por estado, multiplicado por 60, en barras
  apiladas. La altura total es el tráfico recibido.
- **Eje X:** hora.
- **Eje Y:** paquetes por minuto, apilados por estado.
- **A qué responde:** cuándo se inyectaron errores y cómo respondió el código.

**Síndromes observados**

- **Significado:** cuántas palabras código dieron cada valor de síndrome distinto de cero.
- **Cómo funciona:** `increase()` de `lab5g_cmp_fec_sindrome_total` por valor. Cada valor
  (en binario) es una columna de $H$, y $\mathbf s=0$ no se cuenta.
- **Eje X:** valor del síndrome $\mathbf s$, en binario.
- **Eje Y:** número de palabras en el rango de tiempo.
- **A qué responde:** si los errores caen siempre en el mismo bit (un síndrome domina) o
  se reparten.

**Códec del bandido por estado**

- **Significado:** qué códec eligió el bandido y en qué estado terminaron sus paquetes.
- **Cómo funciona:** mapa de calor. Cada celda es el `increase()` de paquetes por códec
  (fila) y estado (columna); más color, más paquetes.
- **Eje X:** estado del paquete.
- **Eje Y:** códec que eligió el bandido.
- **A qué responde:** si el bandido sigue eligiendo códecs frágiles cuando hay errores.

**BER de entrada del canal**

- **Significado:** fracción de los bits del aire que el canal volteó, incluidas las
  inyecciones manuales.
- **Cómo funciona:** bits invertidos (`lab5g_cmp_fec_bits_inyectados_total`) entre 8
  veces los bytes con paridad (`lab5g_cmp_fec_bytes_total` con `tipo="fec"`).
- **Eje X:** hora.
- **Eje Y:** fracción de bits, en escala logarítmica.
- **A qué responde:** qué nivel de ruido ve el código. Con `CANAL=bsc:P` la gráfica debe
  quedarse cerca de $P$.

**Sobrecoste de la protección**

- **Significado:** bytes que salen al aire por cada byte comprimido.
- **Cómo funciona:** cociente de los `increase()` de bytes con `tipo="fec"` entre los de
  `tipo="comprimido"`. Hamming (7,4) ≈ 1,75; SECDED (8,4) ≈ 2; repetición (5,1) = 5, más
  la cabecera del marco.
- **Eje X:** hora.
- **Eje Y:** veces (1 = sin redundancia).
- **A qué responde:** si la redundancia se come lo que ahorró la compresión.

---

## 7. Relación con la compresión

- **Comprimir quita redundancia; la FEC la añade.** Un paquete comprimido tiene
  casi toda la información en pocos bits, así que un bit cambiado puede dañar más.
- **Estado en la compresión.** El códec DEFLATE por flujo guarda estado entre
  paquetes ([docs/08 §4.4](08-compresion-paquetes.md)): un paquete perdido o mal
  decodificado desincroniza a los siguientes. La FEC corrige errores de bit, pero no
  arregla un paquete que se pierde entero.
- **Costo de la FEC.** Hamming (7,4) envía $n/k = 7/4 = 1{,}75$ veces los bytes,
  es decir, un 75 % más. SECDED (8,4) envía 2 veces los bytes (un 100 % más). La repetición (5,1) envía 5 veces los bytes.
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
  El decodificador **corrige mal sin avisar** en el contador de síndromes: el estado queda `corregido` si el paquete aun así se reconstruye, y `corregido_mal` si sale no íntegro (la descompresión falla o la TUN lo rechaza; entonces suma además a `lab5g_cmp_errores`). Nota: el estado `corregido_mal` se añadió tras la verificación del 22-11; antes el tablero contaba esos paquetes como corrección exitosa. SECDED (8,4) ([§2.9](#29-secded-84)) lo
  resuelve: detecta esos dobles en vez de corregirlos mal, a costo de 2 veces los bytes.

---

## 8. Estado del arte

Esta sección es investigación de referencia. Solo están implementadas en esta entrega las filas marcadas como tales.
Las fuentes son las indicadas; las afirmaciones sin fuente son de conocimiento
general y no contienen cifras.

### 8.1 Códigos de bloque clásicos

| Técnica | Dónde se usa | Cómo funciona | Relación con la compresión del proyecto |
|---|---|---|---|
| Repetición | Canales muy ruidosos y enlaces de control simples | Cada bit se envía varias veces y se vota por mayoría | Es el código más caro. Aquí es el caso de referencia (5,1) |
| Hamming (7,4) | Memoria ECC de corrección de un bit | Paridad con síndrome; corrige 1 error, detecta 2 | Implementado en esta entrega. Su costo (+75 %) supera el ahorro de la compresión |
| SECDED (Hamming extendido) | Memoria ECC de servidor | Añade un bit de paridad global: detecta 2 errores sin corregir mal | Implementado en esta entrega ([§2.9](#29-secded-84)) |
| CRC | Casi todos los protocolos: Ethernet, 5G (en bloques de transporte) | Polinomio sobre el mensaje; detecta ráfagas de error, no corrige | Es barato y no añade bytes grandes; es lo que conviene junto a la compresión con estado |

### 8.2 Códigos para ráfagas, borrado y paquetes

| Técnica | Dónde se usa | Cómo funciona | Relación con la compresión del proyecto |
|---|---|---|---|
| Reed-Solomon | Discos ópticos, códigos QR, transmisión satelital | Códigos sobre símbolos de varios bits; corrige ráfagas y pérdidas de símbolos conocidos | Corrige ráfagas, que Hamming no. Su costo depende de cuánta redundancia se elija |
| Paridad entre paquetes (XOR) | Multimedia en tiempo real y RAID | Un paquete de paridad es la XOR de un grupo; recupera uno perdido | Opera sobre el flujo y no sobre cada paquete. Compatible con la compresión, pero añade latencia del grupo |
| RaptorQ (RFC 6330) | Distribución por broadcast y multicast | Código fuente sin tasa fija: se generan paquetes de reparación según se necesiten | Pensado para pérdidas de paquetes, no de bits. Encaja con el borrado de paquetes, pendiente ([§9](#9-hoja-de-ruta-siguiente-entrega)) |

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

- **CRC:** detección de error de bloque para el marco completo.
- **Entrelazado:** reparte los bits de un paquete entre varios paquetes para que
  una ráfaga quede repartida.
- **Reed-Solomon:** corrección de ráfagas y de símbolos perdidos.
- **XOR entre paquetes:** recuperación de paquetes perdidos del flujo.
- **Borrado de paquetes:** un canal que pierde paquetes enteros. Los canales BSC y de
  ráfagas ya están en [§4](#canales-aleatorios).
- **RaptorQ:** código de fuente para pérdidas de paquetes.

**Por qué los códigos entre paquetes van al final.** XOR entre paquetes, Reed-Solomon
entre paquetes y RaptorQ reconstruyen un paquete perdido a partir de otros. Ese paquete
recuperado llega tarde, y `deflate_flujo` necesita los paquetes en orden
([§7](#7-relación-con-la-compresión)). Hamming, SECDED y CRC trabajan dentro de un
paquete y no tienen ese problema.

**Cómo se enchufan.** Cada código nuevo implementa la interfaz `CodigoCanal` de
`compresion/fec.py`. El shim no cambia: solo elige qué código usar. Cada modelo
de canal nuevo es un generador de errores que `canal.py` aplica en el mismo
punto que la inyección manual.

---

## 10. Resultados del banco offline

Fuente: [`compresion/resultados/fec.md`](../compresion/resultados/fec.md), generado por
`python compresion/evaluar.py fec` (`compresion/banco_fec.py`). Son 300 cargas aleatorias
de 200 B por código y punto, con semilla 1. El canal es BSC independiente por bit
(barrido de $p$) y Gilbert-Elliott (ráfagas). El byte del id de código nunca se voltea.
Todas las cifras de esta sección salen de ese archivo.

**BER de entrada contra BER residual (canal BSC)**

| $p$ | BER de entrada | Sin código | Repetición (5,1) | Hamming (7,4) | SECDED (8,4) |
|---|---|---|---|---|---|
| $10^{-3}$ | 9.60e-04 | 9.60e-04 | 2.08e-06 | 0 | 4.17e-06 |
| $3\cdot10^{-3}$ | 3.08e-03 | 3.08e-03 | 3.37e-03 | 3.43e-03 | 6.88e-05 |
| $10^{-2}$ | 1.01e-02 | 1.01e-02 | 1.04e-02 | 1.09e-02 | 1.07e-02 |
| $3\cdot10^{-2}$ | 2.99e-02 | 2.99e-02 | 6.65e-02 | 8.35e-02 | 8.87e-02 |
| $10^{-1}$ | 1.00e-01 | 1.00e-01 | 5.11e-01 | 4.87e-01 | 5.75e-01 |

Lectura:

- **Con $p$ pequeño, los códigos bajan el BER.** A $p=10^{-3}$, Hamming (7,4) lo deja en 0
  y SECDED (8,4) en 4.17e-06, a cambio de enviar 1,75 y 2 veces los bits.
- **El cruce.** A $p=3\cdot10^{-3}$, Hamming (7,4) y repetición (5,1) ya empeoran el BER
  residual frente al de entrada (3.43e-03 y 3.37e-03 frente a 3.08e-03). SECDED (8,4)
  todavía mejora ahí (6.88e-05), pero a $p=10^{-2}$ los tres quedan por encima del BER de
  entrada, SECDED incluido (1.07e-02 frente a 1.01e-02).
- **Límite de Hamming con dos errores.** A $p=3\cdot10^{-2}$, Hamming (7,4) deja 277 de
  300 paquetes corregidos mal y sin aviso. SECDED (8,4) marca 275 como no corregibles y no
  deja ninguno corregido mal (0). Repetición (5,1) también detecta sin corregir mal: 281
  no corregibles y 0 corregidos mal.
- **Efecto de la ráfaga.** Con ráfagas (Gilbert-Elliott con 0.3, 0.01, 0.2), el BER de
  entrada es 1.39e-02 sin código y entre 1.41e-02 y 1.43e-02 con los códigos. Ningún
  código baja el BER residual, que queda en: repetición (5,1) da 9.67e-02, Hamming (7,4) 8.43e-02 y SECDED (8,4) 5.59e-02. Hamming
  falla en 2.52e-02 de sus palabras, frente a 2.07e-03 con errores independientes al BER
  más cercano ($p=10^{-2}$). SECDED tiene más palabras fallidas observadas (2.90e-02),
  pero ningún paquete corregido mal sin aviso, frente a 278 de Hamming. Los errores
  agrupados caen varios en la misma palabra, que es justo lo que el código no aguanta.
- **Advertencia de método.** Un paquete inválido cuenta todos sus bits de datos como
  erróneos, y un paquete no corregible se entrega con errores. Por eso, a $p$ alto, el BER
  residual de un código puede superar al de entrada aunque el decodificador haya avisado.
  Es un límite de la métrica, no una mejora del código.

---

## 11. Cómo probar

Desde la raíz del repositorio, con Python 3. Son los trece archivos que corre el paso
«Pruebas de compresion» del workflow [`deploy.yml`](../.github/workflows/deploy.yml), en
el mismo orden:

```bash
python3 -m unittest compresion/test_compresores.py compresion/test_reproductores.py compresion/test_fec.py compresion/test_envio_fec.py compresion/test_registro_fec.py compresion/test_tablero_fec.py compresion/test_canal.py compresion/test_canal_capa.py compresion/test_banco_fec.py compresion/test_sumidero.py compresion/test_bandido_aire.py compresion/test_adaptativo.py compresion/test_adaptativo_capa.py
```

| Archivo | Qué cubre | Pruebas |
|---|---|---|
| `compresion/test_compresores.py` | Compresión (no es de FEC) | 32 |
| `compresion/test_reproductores.py` | Reproductores de tráfico (no es de FEC) | 6 |
| `compresion/test_fec.py` | Códigos de repetición, Hamming (7,4) y (15,11) y SECDED (8,4) y (16,11), con tablas compactas; ejemplos exactos de clase; corrección de 1 bit y detección de 2 en SECDED; marco y síndromes | 38 |
| `compresion/test_envio_fec.py` | Validación de `/fec/inyectar`, `X-Inyectar`, resumen de paquetes, pestaña de síndrome y modo automático (`FEC=auto`) | 28 |
| `compresion/test_registro_fec.py` | `CapaFec`, `RegistroFec`, canal armado, `mtu_con_fec`, métricas, comandos y bucle UDP en loopback | 28 |
| `compresion/test_tablero_fec.py` | Tablero `lab5g-cmp-fec`: cantidad de paneles, paneles de BER y sobrecoste, consultas PromQL, UID únicos | 9 |
| `compresion/test_canal.py` | Canales BSC y ráfaga: determinismo por semilla, frecuencias, `parse_canal` | 18 |
| `compresion/test_canal_capa.py` | Canal dentro de la capa FEC: semilla por marco, combinación XOR con la armada manual | 7 |
| `compresion/test_banco_fec.py` | Banco offline con 6 códigos: determinismo, BER, categorías, SECDED dirigido, bytes en el cable, tabla Markdown | 13 |
| `compresion/test_sumidero.py` | Sumidero TCP de `cmp-dn`: cierra conexiones colgadas y sigue escuchando sin hilos | 3 |
| `compresion/test_bandido_aire.py` | Recompensa del bandido en bytes del aire | 9 |
| `compresion/test_adaptativo.py` | Estimador del BER, política con histéresis, informe de 12 B (magia, CRC8) | 24 |
| `compresion/test_adaptativo_capa.py` | Capa FEC en modo auto: arranque en secded (8,4), histéresis ida y vuelta, informes malos, MTU de la escalera, marca 0xFF, contadores y texto de la pestaña | 16 |
| **Total** | | **231** |

**Resultado ejecutado en un PC con Windows (24-11-2026):** 231 pruebas OK. Son pruebas
unitarias; ninguna toca la TUN ni el laboratorio.

**Probado en el laboratorio (22-11-2026, `FEC=hamming_7_4`; verificación independiente):**

- `cmp-ue` y `cmp-dn` con el mismo `FEC` y `PUERTO_FEC`; MTU de `cmp0` = 780; las matrices
  P y H de `/fec/codigo` coinciden con el apunte del 9-nov.
- Inyección de 1 bit: el síndrome es la columna de $H$ de la posición (bit en la palabra
  12, posición 5 → $s=011$, como el ejemplo de clase), estado `corregido`, archivo de 20 KB
  de vuelta con SHA idéntico, `lab5g_cmp_errores` sin crecer.
- Inyección de 2 bits en la misma palabra: se ve la limitación. Ejemplo: posiciones 1 y 2
  dan $s=110$, la columna 4, y se «corrige» el bit equivocado; el paquete puede salir no
  íntegro. Si el error cae en el campo de largo, el marco queda `invalido`.
- Resumen exacto de `/enviar` por `CONTADORES` (`resumen_fuente: contadores`, `truncado: false`).
- Métricas `lab5g_cmp_fec_*` en el pushgateway (series precargadas a 0) y consultas
  `increase()` de los paneles contestadas por Prometheus. Grafana estaba apagado: el tablero
  no se ha visto renderizado.
- Tráfico normal: `cmp-gen` sigue enviando, sin errores nuevos sin inyección.
- Casos de la verificación del 22-11: 1 bit en la posición 82 → $s=011$, `corregido`; 2 bits
  en 71 y 72 (nibble del id de códec) → `corregido_mal`; 2 bits en 15 y 16 (campo de
  secuencia) → `corregido` íntegro pero con secuencia falsa (nadie lo detecta: el receptor
  no compara con el original); 2 bits en 43 y 44 (campo de largo) → `invalido`.

**Probado en el laboratorio (23-11-2026, `FEC=secded_8_4`):** MTU de `cmp0` = 677, como da la
fórmula de §3.1. Con `cmp-gen` detenido y un archivo de 6000 B por `/enviar`:

| Caso (posiciones de bit del marco) | Resultado |
|---|---|
| Sin errores | 26 paquetes `limpio` |
| 1 bit (107, palabra 12) | `corregido`, síndrome `0001` |
| 2 bits en la misma palabra (104 y 108) | `no_corregible` (**doble detectado**), síndrome `0110` (peso par); no se corrige ningún bit |
| 2 bits en palabras distintas (105 y 170) | 2 palabras `corregido`, síndromes `0100` y `0010` |
| Archivo completo sin error inyectado | `identico: true`, mismo SHA a la ida y a la vuelta |

El síndrome `0110` coincide con el cálculo a mano de [§2.9](#29-secded-84): es la suma de
dos columnas de $H$ de peso impar, y por eso nunca coincide con una columna.

**Probado en el laboratorio (23-11-2026, canal aleatorio `CANAL=bsc:1e-3`, `FEC=secded_8_4`):**
tres envíos de un archivo de 20 KB con el generador activo; los tres volvieron con SHA
idéntico. Resumen de `/enviar` por envío:

| Envío | Paquetes | Con error del canal | Síndromes ≠ 0 | Corregidas | No corregibles | Corregidos mal |
|---|---|---|---|---|---|---|
| 1 | 84 | 69 | 234 | 233 | 1 | 0 |
| 2 | 71 | 56 | 286 | 286 | 0 | 0 |
| 3 | 337 | 262 | 1396 | 1384 | 12 | 15 |

Con $p=10^{-3}$ casi todas las palabras con error se corrigen; las que tienen dos errores
se detectan, y con tres o más errores en una palabra SECDED puede corregir mal (los
«corregidos mal» del tercer envío), como predice $d_{min}=4$.

**Consultas de los tableros:** las 96 consultas de los cuatro tableros contestan con
datos en el Prometheus real, incluidas las 10 del tablero de síndrome. El tablero **se vio renderizado**
en Grafana el 24-11 (capturas del usuario): «BER de entrada» ≈ 0,1 % (coincide con `bsc:1e-3`),
«Sobrecoste» ≈ 2,03 (SECDED ×2 más cabecera; antes ≈ 1,75 con Hamming) y «Síndromes observados» con
los 8 síndromes de las columnas de $H$ casi iguales (~194 000) y, aparte, los de peso par `1001` y
`1111` (~800), que son los dobles detectados. Limitación: los síndromes salen sin ceros a la
izquierda (`1` en vez de `0001`).

**Grafana ligero (PR #14):** con los 17 plugins no usados desactivados, un
`docker start grafana` simple tarda unos 10 s en servir (health 200 local y por el Funnel).

**Reservas observadas (pendientes):**

- La orden de inyección la gasta el siguiente marco que recibe `cmp-dn`, normalmente de
  `cmp-gen`.
- Una ráfaga de 64 marcos `invalido` justo tras activar `FEC` en `cmp-dn`: durante el
  cambio los dos lados llevaban valores distintos. Cambiar `FEC` siempre en ambos lados.
- ~~Un paquete `corregido` con `integro=false` contaba como corrección en el tablero.~~
  Resuelto tras la verificación del 22-11 con el estado `corregido_mal` (desplegado y
  verificado en el lab real el 22-11). Sigue pendiente la limitación de fondo: con 2 errores
  Hamming (7,4) no puede corregir (SECDED, [§2.9](#29-secded-84)), y un `corregido` íntegro
  puede seguir llevando datos distintos al original, porque el receptor no compara.
- La memoria de `cmp-ue` subió de ~23 a ~47 MiB de 64 y parecía estabilizarse; vigilar con
  `docker stats` y subir el tope si llega cerca.
- Costo de CPU en el LXC 110: sin cuello de botella visible (carga ~1-2), pero no se midió
  con tráfico alto.

---

**Anterior:** [09 — Arranque seguro y recuperación](09-arranque-seguro.md) ·
**Volver al** [README](../README.md)

---

## 12. Banda en el aire: códigos más baratos y el bandido (24-11-2026)

Objetivo acordado: **priorizar el ancho de banda en el aire sobre la velocidad**, contando los
bits de paridad. Todo lo de esta sección se midió offline (`banco_fec.py`, `evaluar.py aire`);
**nada de esto se desplegó en el lab**: los códigos nuevos solo se activan con `FEC=hamming_15_11`
o `FEC=secded_16_11`.

### 12.1 Dos códigos más

| Código | id | $n,k$ | $d_{min}$ | Con 2 errores en una palabra | MTU de `cmp0` |
|---|---|---|---|---|---|
| Hamming (15,11) | 4 | 15, 11 | 3 | corrige mal, sin avisar | 1011 |
| SECDED (16,11) | 5 | 16, 11 | 4 | **detecta**, no corrige | 945 |

Son solo matrices $P$ nuevas del mismo motor ($P$ de 11×4 con los 11 vectores de peso $\ge2$, y de
11×5 con los 10 de peso 3 y el de peso 5). Para que cupieran en `cmp-ue` (64 MB) hubo que rehacer las
tablas de decodificación como arreglos de enteros: de ~10 y ~20 MB a ~0,2 y ~0,4 MB, con el mismo
comportamiento (1200 marcos con errores comparados con la versión anterior: 0 diferencias).

### 12.2 Banda medida por código

Factor cable/carga (incluye id, secuencia, largo y los 28 B de IP+UDP), de `resultados/fec.md` §4:

| Carga que entra a la FEC | Rep. (5,1) | Hamming (7,4) | Hamming (15,11) | SECDED (8,4) | SECDED (16,11) |
|---|---|---|---|---|---|
| 200 B | 5,25 | 1,93 | **1,55** | 2,19 | 1,64 |
| 780 B | 5,06 | 1,80 | **1,41** | 2,05 | 1,50 |
| 1400 B | 5,04 | 1,78 | **1,39** | 2,03 | 1,48 |

Con la compresión incluida (flujo mezclado de prueba, `resultados/aire.md`), los bytes en el aire
frente a mandar todo crudo y sin FEC quedan en: solo compresión ≈ 0,79; con Hamming (15,11) ≈ 1,06;
con Hamming (7,4) ≈ 1,35; con SECDED (8,4) ≈ 1,53. Es decir, la paridad cuesta mucho más que lo que
la compresión ahorra, y **elegir el código mueve la banda 10 veces más que ajustar el bandido**.

### 12.3 El precio en errores (canal BSC)

BER residual de `resultados/fec.md` §1 (BER de entrada = $p$ aprox.):

| $p$ | Hamming (7,4) | Hamming (15,11) | SECDED (8,4) | SECDED (16,11) |
|---|---|---|---|---|
| $10^{-3}$ | 0 | 1,5e-05 | 4,2e-06 | 6,3e-06 |
| $3\cdot10^{-3}$ | 3,4e-03 | 3,6e-03 | **6,9e-05** | 3,5e-03 |
| $10^{-2}$ | 1,1e-02 | 2,2e-02 | 1,1e-02 | 1,8e-02 |

Lectura: con ruido bajo ($\le10^{-3}$) cualquiera sirve y Hamming (15,11) es el más barato. Con
$p=3\cdot10^{-3}$ solo SECDED (8,4) sigue mejorando el BER; los demás ya dejan más errores que los
de entrada. Con ráfagas (errores agrupados) ningún código corto rinde bien. Conclusión práctica:
**el código ideal depende del ruido**: barato cuando el canal está limpio y fuerte cuando está sucio.
Esa elección adaptativa (estimar el BER por los síndromes y cambiar de código) es la palanca real
de banda y queda como siguiente entrega, porque el emisor necesita saber lo que mide el receptor.

### 12.4 El bandido: recompensa en bytes del aire

`bandido.recompensa_aire()` cuenta la paridad ($f=n/k$) y el overhead fijo de cada paquete
($33+f\,(4+L)$ bytes) y escala el costo de CPU con $1/f$. Se barrió $\gamma$, $c$ y el costo de CPU
para $f\in\{1; 1{,}364; 1{,}75; 2\}$ (72 configuraciones de entrenamiento y 8 de prueba,
`resultados/aire.md`). Resultado: **la mejora en bytes del aire es 0,00 puntos en todos los $f$**. Lo
único que cambia es la CPU: por ejemplo con $f=1$, $\gamma=0{,}999$, $c=0{,}005$ y costo 0,01 hace
1231 intentos de comprimir en vez de 1526 (y 35 respaldos en vez de 329) con los mismos bytes.
La razón es que el bandido ya ahorra 20,58 % frente a 20,84 % del oráculo (arrepentimiento de 0,26
puntos), y que 4 de las 6 clases de tráfico de prueba no comprimen con ningún códec.

**Observación sobre robustez.** Ninguna configuración cumple hoy el límite de 2 % de paquetes mal
reconstruidos con 1 % de pérdida: todas dan ~3,8 % (entrenamiento) y 3,6 % (prueba), mientras
`ajuste.md` (8-11) decía 1,91 %. Las ráfagas del bandido se añadieron el 21-11 y mantienen
`deflate_flujo` 8 paquetes seguidos, que es una explicación probable (suben el ahorro de 19,16 % a
20,58 % a cambio de más paquetes dañados por cada pérdida); no se comprobó repitiendo `ajustar` sin
ráfagas, así que es una hipótesis.

---

## 13. Código adaptativo (FEC=auto) (24-11-2026)

Lo de esta sección cierra la entrega que §12.3 dejó pendiente: el emisor elige el código
según el BER que mide el receptor. Solo hay pruebas unitarias; **no se midió en el laboratorio
en esta entrega**.

### 13.1 Motivación

El mejor código depende del ruido (§12.3): Hamming (15,11) es el más barato y basta mientras
$p \le 10^{-3}$; cuando el BER sube hacia $3\cdot10^{-3}$ solo SECDED (8,4) sigue mejorando el BER.
Con un código fijo hay que pagar siempre la paridad del peor caso o aceptar el BER del peor canal.
`FEC=auto` recorre una escalera de menos a más protección:

`hamming_15_11` → `secded_16_11` → `secded_8_4`. Arranca en `secded_8_4` (el más protector: arranque conservador).

### 13.2 Estimador del BER a partir de síndromes

El receptor no conoce los bits enviados, pero el decodificador sí ve los síndromes. Por lote de
palabras decodificadas:

$$\text{bits\_mal} \approx \#\{s \ne 0\} + \#\{\text{no corregibles}\}$$

(cada no corregible implica al menos 2 bits), y

$$\text{BER}_{\text{lote}} = \frac{\text{bits\_mal}}{\text{palabras}\cdot n},\qquad
\text{BER} \leftarrow (1-\alpha)\,\text{BER} + \alpha\,\text{BER}_{\text{lote}},\quad \alpha = 0{,}2.$$

Es una **estimación que se queda corta**: con Hamming, dos errores en una palabra dan un síndrome
distinto de cero y se cuentan como uno; y errores de peso 3 o más pueden dar síndrome cero sin
que nadie lo vea. Por eso el BER estimado se lee como orden de magnitud.

### 13.3 Tabla de umbrales

| BER estimado | Código activo |
|---|---|
| $\le 10^{-3}$ | `hamming_15_11` |
| $\le 2\cdot10^{-3}$ | `secded_16_11` |
| resto | `secded_8_4` |

Los umbrales son inclusivos (`ber <= umbral`). Está en `TABLA_AUTO` (`compresion/registro_fec.py`).

### 13.4 Histéresis

- **Subir protección:** inmediato, en cuanto un informe supera el umbral del escalón actual.
- **Bajar protección:** solo tras 3 informes seguidos con BER < 0,5 × el umbral del escalón
  inmediatamente inferior. En la práctica: bajar de `secded_8_4` a `secded_16_11` exige BER < $10^{-3}$;
  bajar de `secded_16_11` a `hamming_15_11` exige BER < $5\cdot10^{-4}$.

Un informe que no cumple la condición de bajada reinicia el contador. Así el código no oscila
cuando el BER anda justo en un umbral.

### 13.5 Informe del receptor al emisor

El receptor manda un informe cuando hay muestras nuevas, como mucho cada 2 s
(`INTERVALO_INFORME`). El informe viaja **sin FEC**, en un marco de 13 B:

| Campo | Bytes | Contenido |
|---|---|---|
| marca | 1 | `0xFF` (`MARCA_INFORME`). No pasa por el decodificador; el KEEPALIVE también es `0xFF` pero de 1 B |
| magia | 2 | `F0 52` |
| BER × 10⁹ | 4 | uint32 |
| muestras | 3 | 24 bits (se satura en 2²⁴−1) |
| secuencia | 2 | uint16 |
| CRC8 | 1 | polinomio 0x07, valor inicial 0, sobre los 11 B anteriores |

Un informe con magia, largo o CRC malos se cuenta como «malo» y **no cambia el código**. Un
informe perdido solo retrasa la decisión.

### 13.6 MTU

`cmp0` en auto usa el **mínimo** de la escalera: `mtu_auto()` = **677 B**. Así cabe cualquier código
al que el sistema pueda cambiar sin renegociar el MTU del túnel. Es menos que con un código fijo
(p. ej. 1011 B con Hamming (15,11), §12.1).

### 13.7 Cómo activarlo

```bash
printf 'auto' | lab5g.sh env-set FEC
```

y reiniciar `cmp-ue` y `cmp-dn`, como indica la pestaña de síndrome (§5). Para volver a un código
fijo, `FEC` toma cualquier nombre de código o `auto`.

### 13.8 Qué se ve

- **Pestaña «Laboratorio de síndrome»** (§5): con `auto` aparece el aviso «Modo automático: código
  activo X, BER estimado Y (escalera: …)» y la nota sobre el informe cada ~2 s con histéresis. La
  matriz y el código se refrescan cada ~12 s porque el código activo cambia. Con `auto` apagado la
  pantalla es la de siempre.
- **Métricas** (`lab5g-cmp-fec`):
  - `lab5g_cmp_fec_ber_estimado`: el BER que usa la política.
  - `lab5g_cmp_fec_codigo_activo{codigo=…}`: 1 en el código vigente.
  - `lab5g_cmp_fec_informes_total`: enviados, recibidos y malos.
  - `lab5g_cmp_fec_cambios_codigo_total`: cambios de código del receptor.

  Los paquetes ya en vuelo se etiquetan con el código real de su marco, no con el vigente.

### 13.9 Limitaciones

- **Informe sin FEC:** un informe corrupto se descarta por CRC y no se corrige; el código se
  mantiene hasta el siguiente informe bueno.
- **Cada sentido decide por separado:** el código de la bajada (cmp-dn → UE) y el de la subida
  (UE → cmp-dn) son independientes. Cada receptor informa al emisor del otro sentido.
- **Lazo de realimentación con retardo de ~2–6 s** (intervalo de informe, tránsito por el túnel,
  histéresis). Ráfagas de ruido más cortas que eso no se siguen; la protección llega tarde.
- **Estimador:** subestima el BER en los casos de §13.2. Su valor es un orden de magnitud para
  elegir código, no una medida del BER real.
- **Sin medición en el laboratorio** en esta entrega: la prueba de que el lazo funciona en el enlace 5G
  real queda pendiente.

## 14. Reenvío por NACK (`REENVIO=1`)

SECDED detecta los dobles errores pero no los corrige. Con `REENVIO=1` el receptor, ante una palabra no corregible, **descarta el paquete** (no lo entrega) y manda al emisor un **NACK** con la secuencia. El emisor guarda los últimos 128 marcos limpios y reenvía el pedido.

- NACK: byte `0xFE`, n (1..8), n secuencias de 16 bits, CRC8. Sin protección FEC; se manda de inmediato.
- Límites: máximo 2 reenvíos por secuencia y 8 reenvíos por segundo (evita tormentas con canal malo).
- Solo actúa donde hay detección: `secded_8_4` y `secded_16_11`. Hamming y repetición corrigen mal los dobles errores en silencio (estado `corregido_mal`); ahí recupera TCP.
- Métricas: `lab5g_cmp_fec_reenvio_total{lado,tipo}` con nack_enviados, nack_recibidos, reenviados, sin_copia, descartados_limite, nack_malos.
- Medido en el laboratorio (24-nov-2026, `secded_8_4`): con BSC 3e-3 el archivo llegó idéntico con 9 NACK y 10 reenvíos; con BSC 1e-2 casi todos los paquetes (≈2800 palabras) traen algún doble error, se descartan casi todos y la transferencia no termina: el reenvío ayuda con ruido moderado, no extremo.
- Limitación: con compresores con estado (`deflate_flujo`) un reenvío llega fuera de orden y su descompresión puede fallar.
- En el laboratorio de síndrome, la página marca en azul los paquetes que llegaron por reenvío y filtra por ellos («reenviado»); el contador «reenviados» de la tarjeta FEC cuenta esos mismos paquetes.

## 15. Elegir el algoritmo en caliente

Hasta aquí, cambiar de código FEC exigía fijar `FEC` con `lab5g.sh env-set` y reiniciar cmp-ue y cmp-dn (unos 4 min en el LXC). Con `FEC` en {`auto`, `hamming_15_11`, `secded_16_11`, `secded_8_4`, `hamming_7_4`} el despliegue es **dinámico**: el algoritmo se elige desde la pestaña «Laboratorio de síndrome» sin reiniciar nada.

### 15.1 Cómo se aplica

- Selector «Algoritmo FEC» (Automático, Hamming (15,11), SECDED (16,11), SECDED (8,4), Hamming (7,4)) y botón Aplicar. Llama a `POST /fec/elegir` con `{"codigo": "..."}`, que reenvía `ELEGIR <nombre>` al shim de cmp-dn.
- **Sentido DN→UE**: cambia al instante, porque lo emite el propio cmp-dn.
- **Sentido UE→DN**: cmp-dn manda al UE una trama de control de 3 B (`0xFD`, índice del algoritmo, CRC8) por el túnel. El UE la aplica a su emisión. Tarda unos segundos. La trama se repite cada 10 s mientras la elección siga vigente, así que un UE que reinicia la recibe sola.
- Un marco nunca necesita saber qué código eligió el otro lado: el byte 0 de cada marco dice su código, y el receptor decodifica con ese.

### 15.2 Reglas

- `repeticion_5_1` **no** es dinámico: conserva su MTU de 247 B y, si se fija, la página muestra «hay que reiniciar». Responde 409 a `/fec/elegir`.
- Con dinámico el MTU de cmp0 es el de `auto` (**677 B**, el mínimo de la escalera), también si el código fijo es `hamming_7_4` (en fijo eran 780 B). Es el precio de poder cambiar a `secded_8_4` sin recalcular el MTU.
- El receptor informa de su BER (cada ~2 s, 12 B) aunque el código sea fijo. Solo cambia de código el extremo que está en `auto`.
- Elegir `auto` reinicia la política y empieza en `secded_8_4`. Elegir un código fijo saca de `auto`.
- Un nombre que no esté en la lista da 400 con las opciones válidas. Una trama de control con CRC malo no cambia nada y cuenta como «mala».

### 15.3 Métricas y verbos

- `CODIGO` añade `dinamico`, `opciones` (null si no es dinámico) y `eleccion` (último nombre elegido, o null). `ELEGIR <nombre>` responde `{"ok": true, "codigo_activo", "auto", "eleccion"}` o `{"ok": false, "error", "dinamico"}`.
- Con dinámico, `lab5g_cmp_fec_codigo_activo{lado,codigo}` cubre los cuatro códigos (1 el vigente), las series de síndrome y paquetes de los cuatro nacen a 0, y aparece `lab5g_cmp_fec_elegir_total{lado,tipo}` con `tipo` en `elegida_local` (ELEGIR en este lado), `recibida` (trama aplicada) y `mala` (CRC o índice malos).

### 15.4 Limitaciones

- La trama de control no tiene FEC: va en 3 B con CRC8. Si se corrompe, se descarta y la siguiente repetición (10 s después) la reemplaza.
- Cambiar de código no cambia el canal ni la semilla: el CANAL sigue fijado por entorno y necesita reinicio, como antes.
- La prueba automática cubre la capa (`registro_fec.py`) y la API (`envio.py`) con `test_elegir.py`. El bucle de `shim.py` solo usa Linux (`fcntl`), así que se verifica en el laboratorio, no en las pruebas unitarias.
