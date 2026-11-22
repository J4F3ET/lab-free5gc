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
- [4. Inyección manual del error (diseño)](#4-inyección-manual-del-error-diseño)
- [5. Visualización «Laboratorio de síndrome» (diseño)](#5-visualización-laboratorio-de-síndrome-diseño)
- [6. Grafana (diseño, pendiente de aprobación)](#6-grafana-diseño-pendiente-de-aprobación)
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
- **Inyección de error solo manual**: la página de envío pide voltear bits del
  siguiente paquete. No hay canal aleatorio.

**Siguiente entrega (no implementado aquí):** SECDED, CRC, entrelazado,
Reed-Solomon, paridad entre paquetes, modelos de canal aleatorios (BSC, ráfagas
Gilbert-Elliott, borrado) y RaptorQ. Ver [§9](#9-hoja-de-ruta-siguiente-entrega).

**Estado de la implementación**

| Pieza | Estado |
|---|---|
| `compresion/fec.py` (códigos de repetición y Hamming, interfaz `CodigoCanal`) | Implementado y probado (`python -m unittest compresion/test_fec.py`: 18 pruebas OK) |
| `compresion/canal.py` (`inyectar` y `elegir_posiciones`: voltea bits indicados) | Implementado y probado |
| `compresion/test_fec.py` | Implementado; los ejemplos de clase son pruebas exactas. Se ejecuta en `deploy.yml` |
| Integración en `compresion/shim.py` | Diseñado, pendiente |
| Endpoint local de inyección y página de envío | Diseñado, pendiente |
| Paneles de Grafana y métricas `lab5g_cmp_fec_*` | Diseñado, pendiente de aprobación |

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
socket del shim. No modifica el `Compresor` ni el bandido UCB.

```
 cmp-ue                                                       cmp-dn
 app ─▶ Compresor (ROHC + códec elegido por el bandido)
          │  marco comprimido
          ▼
       FEC: codifica el contenido ──▶ palabras código
          │
          ▼
       marco = [1 B id códec][palabras]  ──▶ sendto (shim) ─▶ uesimtun0 ─▶ gNB ─▶ GTP-U ─▶ UPF ─▶ ...
                                                                                                │
 ... ─▶ recvfrom ─▶ FEC: decodifica por síndrome ─▶ Descompresor ─▶ entrega
```

**Formato del marco**

| Parte | Tamaño | Protegida por FEC |
|---|---|---|
| Id del códec | 1 B | **No.** Viaja sin protección |
| Palabras código del contenido | variable | Sí |

El **contenido codificado** antes de la FEC es:

| Campo | Tamaño |
|---|---|
| Número de secuencia | 2 B |
| Largo de los datos | 2 B |
| Datos comprimidos | variable |

**Consecuencia.** Si el bit del id se voltea, el receptor usa el descompresor
equivocado y el paquete sale mal aunque la FEC haya corregido todo. Es un límite
aceptado en esta entrega; ver [§7](#7-relación-con-la-compresión).

**Módulos**

- `compresion/fec.py`: códigos de bloque lineales bajo una interfaz común
  `CodigoCanal` (codificar y decodificar por síndrome). Es el punto de enchufe de
  las siguientes entregas.
- `compresion/canal.py`: encadena FEC y marco, aplica la inyección de error y
  registra qué bits se voltearon.

**Estado** de esta integración: diseñada, pendiente. Los módulos y sus pruebas
ya existen; la integración en `shim.py` aún no.

---

## 4. Inyección manual del error (diseño)

La página de envío ([docs/08 §4.9](08-compresion-paquetes.md)) permite pedir un
error antes de que el paquete salga. Diseño:

1. La página llama a un endpoint local del shim con una de estas órdenes: voltear
   $N$ bits elegidos al azar dentro de las palabras, o voltear el bit $k$.
2. El shim aplica la orden **al siguiente paquete**, después de codificarlo y
   antes de `sendto`.
3. Se calcula $\mathbf r=\mathbf c+\mathbf e$, donde $\mathbf e$ tiene un 1 en
   cada bit volteado.
4. Se registra qué bits se volteron, en qué paquete y con qué resultado tras
   decodificar.

**Decisión de diseño.** La inyección solo cambia bits de las palabras, no el id
del códec. Así el experimento mide la protección de la FEC y no un fallo del
identificador, que está fuera de la protección. Ese caso se puede estudiar
aparte.

El error se aplica por orden, no por canal aleatorio. El canal aleatorio es
parte de la siguiente entrega.

---

## 5. Visualización «Laboratorio de síndrome» (diseño)

Dos niveles, para no saturar la vista:

**Nivel 1, paquetes.** Una fila por paquete con:

- número de secuencia;
- códec elegido por el bandido UCB;
- estado: **limpio** (sin error), **corregido** (error de un bit localizado y
  corregido) o **no corregible** (síndrome que no se resuelve o error de más de
  un bit).

Los bits no aparecen en este nivel.

**Nivel 2, detalle de un paquete.** Al abrir un paquete se muestra el proceso
paso a paso:

1. Códec elegido.
2. Marco: id, luego palabras código.
3. Palabras con la paridad resaltada.
4. Bits volteados por la inyección, marcados en la palabra recibida.
5. Síndrome $\mathbf s=\mathbf rH^{T}$ calculado.
6. Columna de $H$ que coincide con $\mathbf s$, si la hay.
7. Bit corregido y palabra resultante.

Los bits solo aparecen en el detalle.

---

## 6. Grafana (diseño, pendiente de aprobación)

Regla del proyecto: pocos paneles y cada uno con su explicación. Cada panel se
describe con **Significado / Cómo funciona / Eje X / Eje Y / A qué responde**.
Ningún panel se construye sin aprobación previa.

Métricas previstas, prefijo `lab5g_cmp_fec_*` (nombres por definir al
implementar):

- contador de paquetes por estado (limpio, corregido, no corregible);
- contador de síndromes por valor;
- contador de bits volteados por inyección.

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

```bash
python -m unittest compresion/test_fec.py
```

Esta prueba se ejecuta también en el paso «Pruebas de compresion» del workflow
[`deploy.yml`](../.github/workflows/deploy.yml).

**Resultados.** Aún no hay resultados de pruebas. Esta sección se rellenará
cuando se ejecuten.

---

**Anterior:** [09 — Arranque seguro y recuperación](09-arranque-seguro.md) ·
**Volver al** [README](../README.md)
