# 12 — Plan de la siguiente entrega (miércoles 25-11-2026)

**Estado (23-11-2026)**

| Hecho | Falta |
|---|---|
| SECDED (8,4), canales BSC y de ráfagas, banco offline con la curva BER, página «Laboratorio de síndrome» y tablero `lab5g-cmp-fec` (7 paneles). 157 pruebas OK en local. | Verificación en el laboratorio (SECDED y canales aleatorios no se han probado allí) y despliegue en `cmp-ue` y `cmp-dn`. |

Alcance acordado: **Hamming y SECDED, canales aleatorios (BSC y ráfagas) y curva BER**.
Todo protege **dentro de un paquete**, así que no depende de otros paquetes ni
interfiere con la compresión con estado. Parte de lo que ya existe: código lineal
genérico, capa FEC, inyección manual, pestaña «Laboratorio de síndrome» y tablero
de Grafana.

**Aviso.** No tengo el enunciado de esta entrega. El alcance sale de lo acordado en
la conversación y de la hoja de ruta de `docs/10 §9`. Las cifras de MTU salen de la
fórmula de `docs/10 §3.1`; hay que confirmarlas con las pruebas.

## 1. Qué se implementa

| # | Pieza | Para qué sirve | Esfuerzo |
|---|---|---|---|
| 1 | **SECDED (8,4)** | Con 2 errores avisa en vez de corregir mal | Bajo |
| 2 | **Canales aleatorios: BSC y ráfagas** | Los errores dejan de depender de un botón | Medio |
| 3 | **Curva BER (banco offline)** | La figura central del informe | Medio |
| 4 | **App y Grafana** | Controles del canal y paneles nuevos | Medio |

**Se documentan, no se implementan:** XOR entre paquetes, Reed-Solomon entre
paquetes, RaptorQ, CRC-32, entrelazado y los Hamming más grandes ((15,11) y
(31,26)).

**Por qué no los códigos entre paquetes.** XOR, RaptorQ y Reed-Solomon entre
paquetes reconstruyen un paquete perdido a partir de otros. Ese paquete llega
tarde, y los códecs con estado (como `deflate_flujo`) necesitan los paquetes en
orden, así que se desincronizan. Hamming y SECDED trabajan dentro de un paquete y
no tienen ese problema.

## 2. Diseño de cada pieza

### 2.1 SECDED (8,4) con el motor actual

El motor genérico de `compresion/fec.py` ya construye $G$ y $H$ a partir de $P$.
SECDED entra **sin código nuevo de matrices**: es un código de Hsiao, con
$H=[I_4:P^T]$ y las cuatro columnas de $P^T$ de **peso 3** (1110, 1101, 1011, 0111).
Así las 8 columnas de $H$ son distintas y de peso impar. Distancia mínima 4.

Regla del decodificador, sobre el síndrome $\mathbf s$:

| Síndrome | Significa | Acción |
|---|---|---|
| $\mathbf s=0$ | Sin error | Entregar |
| $\mathbf s\neq 0$ de **peso impar** | Un error: coincide con una columna de $H$ | Corregir ese bit |
| $\mathbf s\neq 0$ de **peso par** | Dos errores: nunca coincide con una columna | **Detectar, no corregir** |

**Cambio en el código:** un indicador `detecta_doble` en `CodigoLineal` y la
instancia `SECDED_8_4` con id 3 en `CODIGOS`. Para el estado se reutiliza
`no_corregible` con una etiqueta que lo distinga.

- **Costo:** $n/k=2$, el doble de bytes. Con $n=8$ el MTU de `cmp0` baja a unos 677
  (por la fórmula; hay que confirmarlo).
- **Pruebas exhaustivas:** los 8 errores de un bit se corrigen; las 28 parejas de
  errores dan síndrome par y no se corrigen; $HG^T=0$.
- **Qué se ve:** con 2 bits en la misma palabra el paquete ya no dice «corregido»:
  dice «doble detectado». Es el contraste directo con Hamming (7,4). Se activa con
  `FEC=secded_8_4`, sin tocar el shim.

### 2.2 Canales aleatorios

`compresion/canal.py` hoy solo hace `inyectar(datos, posiciones)`. Se añaden dos
generadores de errores que actúan en el **mismo punto** que la inyección manual
(recepción de `cmp-dn`, antes de decodificar), con semilla para repetir resultados:

- **BSC($p$):** cada bit del marco se voltea con probabilidad $p$, de forma
  independiente.
- **Ráfagas (Gilbert-Elliott):** dos estados, bueno y malo, con probabilidades de
  error distintas. Produce errores agrupados, que es lo que Hamming y SECDED **no**
  aguantan; sirve para mostrar su límite.

Se eligen con la variable `CANAL` (por ejemplo `bsc:1e-3` o `rafaga:...`), vacía por
defecto. La inyección manual sigue funcionando. Como antes, el byte del id del
código FEC no se toca. Los bits que voltea el canal suman a
`lab5g_cmp_fec_bits_inyectados_total`.

### 2.3 Curva BER (banco offline)

`compresion/evaluar.py fec`: para cada código y cada valor de $p$ de un barrido,
pasa un conjunto de marcos por el canal y el decodificador, y mide:

- BER de entrada (bits volteados entre bits enviados);
- BER residual (bits erróneos tras decodificar);
- paquetes limpios, corregidos, no corregibles y mal corregidos.

Se compara con la fórmula teórica. Para Hamming (7,4), la probabilidad de que una
palabra falle (dos o más errores) es $1-(1-p)^7-7p(1-p)^6$. Salida en
`compresion/resultados/fec.md`.

**Figura central del informe:** BER de salida contra BER de entrada, una curva por
código: sin código, repetición (5,1), Hamming (7,4) y SECDED (8,4). Se verá que:

- con $p$ pequeño, los códigos reducen el BER residual;
- con $p$ alto, Hamming corrige mal y su BER residual sube, mientras que SECDED
  detecta los dobles;
- con ráfagas, ambos pierden eficacia.

### 2.4 App y Grafana

**Pestaña «Laboratorio de síndrome»:**
- Selector de código (repetición, Hamming, SECDED) con sus matrices.
- Controles del canal: manual, BSC o ráfagas, con $p$ y semilla.
- Estado «doble detectado» en la tabla y en el detalle del paquete.

**Tablero `lab5g-cmp-fec`** (se amplía, sin tableros nuevos):
- Paquetes por estado, con «doble detectado» incluido.
- BER de entrada y residual por código.
- Sobrecoste: bytes con paridad frente a bytes comprimidos.

## 3. Orden de trabajo y PRs

| Día | Trabajo | PR |
|---|---|---|
| Lunes 23 | SECDED (§2.1) con pruebas exhaustivas | PR A |
| Lunes 23 a martes 24 | Canales BSC y ráfagas (§2.2) y banco con curva BER (§2.3) | PR B |
| Martes 24 | App y Grafana (§2.4), documentación e informe | PR C |
| Miércoles 25 | Verificación en el lab y entrega | — |

Cada PR lleva sus pruebas en `deploy.yml`. Los códigos nuevos entran por la
interfaz `CodigoCanal`, sin cambiar el shim más de lo necesario.

## 4. Criterios para dar cada pieza por hecha

- Las pruebas unitarias pasan en local y en el workflow.
- SECDED: pruebas exhaustivas de errores de 1 y 2 bits.
- El canal con semilla es repetible: la misma semilla da los mismos errores.
- La curva BER sale por código y coincide con la teoría para $p$ pequeños.
- En el lab, con `FEC=secded_8_4` en `cmp-ue` y `cmp-dn`: 1 bit da «corregido» y el
  archivo vuelve con SHA idéntico; 2 bits en la misma palabra dan «doble detectado».
- Los paneles nuevos devuelven datos en Prometheus y se ven en el Grafana real.
- Se declaran los límites: lo no medido en el LXC 110 y lo no visto en pantalla.

## 5. Riesgos

- **Reiniciar `cmp-*` es caro** (hasta 3 h con el host cargado). Se despliega en
  caliente y se prueba antes en local y con el banco offline.
- **MTU y CPU.** SECDED duplica los bytes y el canal suma trabajo al bucle del
  shim. Hay que medirlo antes de dar por buena la tasa.
- **Sin CRC.** Un paquete mal corregido puede no detectarse; queda como límite
  declarado, igual que en Hamming.
- **El host sigue frágil.** La saturación de disco rompió el plano de usuario más
  de una vez. Conviene tener los probes apagados durante las pruebas.
- **Sin enunciado.** Falta confirmar con el curso qué se evalúa el miércoles.
