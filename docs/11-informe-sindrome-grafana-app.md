# 11 — Informe: detección y corrección de errores por síndrome (app y Grafana)

Resumen para el informe final. Detalle técnico y teoría completa en
[docs/10](10-codificacion-canal.md). Fecha de verificación en el lab: 22-11-2026.

## 1. Qué se hizo

Se tomó lo visto en clase (códigos de bloque lineales, apunte del 9-11) y se
implementó dentro del proyecto de compresión de paquetes sobre free5GC:

- Un **código lineal genérico** definido solo por la matriz de paridad $P$:
  $\mathbf b=\mathbf m P$, $G=[P:I_k]$, $\mathbf c=\mathbf m G$, $H=[I_{n-k}:P^T]$,
  con la comprobación $HG^T=0$ al construirlo.
- Dos instancias vistas en clase: **repetición (5,1)** y **Hamming (7,4)**
  (distancia mínima 3: detecta 2 errores y corrige 1), y una tercera añadida después:
  **SECDED (8,4)** (distancia mínima 4: corrige 1 y detecta 2 sin corregir mal; ver §6).
- Una **capa FEC** entre el marco comprimido y el socket UDP del túnel
  (`compresion/shim.py`). Está apagada por defecto (`FEC` vacío = bytes idénticos
  a antes).
- **Inyección manual** de errores desde la página de envío, y un receptor que
  calcula el síndrome $\mathbf s=\mathbf r H^T=\mathbf e H^T$, lo compara con las
  columnas de $H$ y corrige el bit señalado.
- **Visualización** en la app (pestaña «Laboratorio de síndrome») y en Grafana
  (tablero «Codificación de canal (síndrome)»).

Quedan solo documentados como siguiente paso: CRC, entrelazado, Reed-Solomon,
XOR entre paquetes, RaptorQ y LDPC/Polar (ver §6 para lo que sí entró después).

## 2. En la app: pestaña «Laboratorio de síndrome»

Página de envío de archivos (`compresion/envio.py`), segunda pestaña.

| Elemento | Qué muestra | Para qué sirve en el informe |
|---|---|---|
| Código activo | Matrices $G=[P:I_k]$ y $H=[I:P^T]$ dibujadas, con la parte de paridad sombreada | Conecta el código con el apunte |
| Botones de inyección | Voltear 1 bit, 2 bits o un bit exacto en el siguiente paquete | Provocar el error a voluntad |
| Línea de paquetes | Una casilla por paquete, coloreada por estado | Ver de un vistazo qué pasó |
| Tabla de paquetes | Últimos 100, con filtro por estado: id, lado, secuencia, códec del bandido, estado, síndromes, palabras corregidas, bits volteados | Identificar cada paquete |
| Detalle de un paquete | Códec elegido, bytes comprimidos y con paridad, bits volteados, cada síndrome y la columna de $H$ que coincide, posición corregida (numerada desde 1, como en clase), resultado | Seguir un paquete paso a paso |
| Resumen del envío | Paquetes observados y su estado al mandar un archivo | Relacionar un archivo con los errores |

**Estados de un paquete**

| Estado | Significado |
|---|---|
| `limpio` | $\mathbf s=0$: no se detecta error |
| `corregido` | $\mathbf s\neq 0$ coincide con una columna de $H$; se corrigió ese bit y el paquete quedó íntegro |
| `corregido_mal` | El síndrome corrigió un bit pero el paquete no se pudo reconstruir (típico con 2 errores en una palabra Hamming) |
| `no_corregible` | $\mathbf s$ no coincide con ninguna columna de $H$ |
| `invalido` | El marco no se pudo leer (por ejemplo, id de código alterado) |

**Cómo se ve el síndrome en la práctica**

- 1 bit volteado → $\mathbf s$ = columna $i$ de $H$ → se corrige el bit $i$ → `corregido`.
- 2 bits en la misma palabra Hamming (7,4) → el síndrome apunta a un tercer bit y
  «corrige» el equivocado → normalmente `corregido_mal`. Es la consecuencia de
  $d_{min}=3$: detecta 2 errores, pero con 2 no sabe cuál corregir.

**Dónde se inyecta.** En la recepción de `cmp-dn`, antes de decodificar. Solo se
ve el tráfico que recibe `cmp-dn`. La orden la gasta el siguiente marco que llegue
(normalmente del generador `cmp-gen`), así que para llevar el error al archivo
hay que parar `cmp-gen` antes.

**Efecto sobre el túnel.** Con FEC el marco crece $n/k$: el MTU de `cmp0` baja a
780 con Hamming (7,4) y a 247 con repetición (5,1). El sobrecoste de Hamming es
×1,75 en bytes.

## 3. En Grafana: tablero «Codificación de canal (síndrome)»

Archivo: `observability/grafana/provisioning/dashboards/json/lab5g-cmp-fec.json`
(generado por `dashboards_compresion.py`). Variables `lado` y `codigo`. Cada panel
lleva su ficha Significado / Cómo funciona / Eje X / Eje Y / A qué responde.

| Panel | Tipo | Qué muestra | Qué responde |
|---|---|---|---|
| Cómo leer este tablero | Texto | La idea de clase en pocas líneas: $\mathbf r=\mathbf c+\mathbf e$, síndrome y estados | Orientar a quien lo abre |
| Qué pasó con los paquetes | Barras | Paquetes por estado en el rango elegido | ¿Qué fracción fue limpia, corregida o perdida? |
| Estados en el tiempo | Serie apilada | Paquetes por minuto por estado | ¿Cuándo se inyectaron errores y cómo respondió el código? |
| Síndromes observados | Barras | Palabras por valor de síndrome distinto de cero | ¿El síndrome señala las columnas esperadas de $H$? |
| Códec del bandido por estado | Tabla (mapa de calor) | Paquetes por códec (fila) y estado (columna) | ¿Los códecs con estado, como `deflate_flujo`, sufren más con errores? |

**Métricas nuevas** (publicadas por el shim al pushgateway cada 10 s; todas con
etiquetas `lado` y `codigo`):

| Métrica | Etiquetas extra |
|---|---|
| `lab5g_cmp_fec_paquetes_total` | `estado`, `codec` |
| `lab5g_cmp_fec_sindrome_total` | `sindrome` |
| `lab5g_cmp_fec_bits_inyectados_total` | — |
| `lab5g_cmp_fec_palabras_total` | — |
| `lab5g_cmp_fec_bytes_total` | `tipo` (`comprimido`, `fec`) |

Las series se precargan en 0, para que `increase()` y `rate()` cuenten también el
primer evento.

**Otros cambios en Grafana**

- Se quitó el panel «CPU por contenedor del lab» del tablero overview, porque
  cAdvisor ya no existe y no tenía fuente de datos.
- Grafana arranca bajo demanda (`lab5g.sh grafana up` / `down`) y se desactivaron
  17 plugins que el lab no usa (PR #14), para que arranque más ligero en un host
  de 3.8 GB.

## 4. Verificación

- **Pruebas automáticas:** 108 a 109 pruebas unitarias (`test_fec.py`,
  `test_registro_fec.py`, `test_envio_fec.py`, `test_tablero_fec.py`, más las de
  compresores y reproductores). Incluyen los ejemplos exactos de clase
  (Hamming (7,4), $\mathbf m=1011\to\mathbf c=1001011$, error en el bit 5 →
  $\mathbf s=011$).
- **Lab real (22-11):** un archivo enviado con FEC activa llegó idéntico; las
  métricas `lab5g_cmp_fec_*` llegaron al pushgateway; las 94 consultas de los
  tableros devuelven datos en Prometheus. Un agente validador (Opus) revisó el
  trabajo y lo dio por «funciona con reservas».

## 5. Límites que conviene declarar

- El tablero de FEC **no se ha visto aún dibujado en un Grafana real**: las
  consultas están probadas contra Prometheus, pero no la pantalla. (Grafana estaba
  sin servir por saturación del host cuando se escribió este informe.)
- La pestaña **no dibuja las palabras una por una** con los bits volteados en
  rojo; muestra las matrices, y los síndromes y las posiciones como texto.
- «Íntegro» solo significa que el paquete descomprimió sin excepción. Una
  corrección equivocada puede pasar por íntegra; `sha_identico` en `/enviar`
  compara el archivo de ida con el de vuelta.
- Un paquete `no_corregible` se entrega igual a la TUN.
- El costo de CPU de la FEC está medido en un PC, no en el LXC 110 (más lento).
- La página de envío no tiene login: no debe exponerse por Funnel.
- Con SECDED, tres o más errores en una palabra todavía pueden corregirse mal; el
  canal de ráfagas solo se probó con pruebas unitarias y en el banco offline, no en el lab.

## 6. Ampliación del 23-11: SECDED, canales aleatorios y curva BER

**SECDED (8,4).** Es un Hamming con un bit de paridad extra (código de Hsiao): las 8
columnas de $H$ son distintas y de peso impar, así que $d_{min}=4$. Regla: $\mathbf s=0$,
sin error; $\mathbf s$ de peso impar, un error y se corrige; $\mathbf s$ de peso par, **dos
errores y no se corrige** («doble detectado»). Cuesta el doble de bytes (MTU de `cmp0` 677).
Entró en el motor genérico solo con una matriz $P$ nueva.

**Canales aleatorios.** Variable `CANAL`: `bsc:P` (cada bit se voltea con probabilidad
$P$) o `rafaga:P_MALO,P_BUENO_A_MALO,P_MALO_A_BUENO` (Gilbert-Elliott, errores agrupados),
con semilla `CANAL_SEMILLA`. Se aplican al recibir, antes de decodificar, y se suman a la
inyección manual.

**Curva BER (banco offline, `compresion/resultados/fec.md`).** BER residual contra BER de
entrada, 300 paquetes de 200 B por punto:

| BER de entrada | Sin código | Repetición (5,1) | Hamming (7,4) | SECDED (8,4) |
|---|---|---|---|---|
| 9,6e-04 | 9,6e-04 | 2,1e-06 | 0 | 4,2e-06 |
| 3,1e-03 | 3,1e-03 | 3,4e-03 | 3,4e-03 | 6,9e-05 |
| 1,0e-02 | 1,0e-02 | 1,0e-02 | 1,1e-02 | 1,1e-02 |
| 3,0e-02 | 3,0e-02 | 6,7e-02 | 8,4e-02 | 8,9e-02 |

Lectura: con ruido bajo ($\le 10^{-3}$) los códigos casi eliminan los errores. Por encima
de unos $3\cdot10^{-3}$ los códigos cortos dejan de ayudar, porque aparecen palabras con
dos o más errores. Hamming las corrige mal sin avisar (a $p=3\cdot10^{-2}$, 277 de 300
paquetes); SECDED las detecta. La probabilidad teórica de palabra fallida coincide con la
observada. Con errores en ráfaga todos los códigos pierden eficacia. Advertencia de método:
un paquete inválido cuenta todos sus bits de datos como erróneos y uno no corregible se
entrega con errores, por eso a $p$ alto el BER residual supera al de entrada.

**Verificado en el lab real (23-11), `FEC=secded_8_4`:**

| Caso | Resultado |
|---|---|
| 1 bit | `corregido`, síndrome `0001` |
| 2 bits en la misma palabra | `no_corregible` («doble detectado»), síndrome `0110` (peso par); no corrige ningún bit |
| 2 bits en palabras distintas | 2 palabras `corregido` |
| Canal `bsc:1e-3`, 3 archivos de 20 KB | los 3 con SHA idéntico; 234 a 1396 síndromes por envío, casi todos corregidos |

**En la app y en Grafana:** la pestaña muestra el canal activo y «doble detectado» con
SECDED. El tablero suma dos paneles: «BER de entrada del canal» y «Sobrecoste de la
protección» (7 elementos en total). Las 96 consultas de los cuatro tableros devuelven
datos en Prometheus; el tablero de síndrome sigue sin verse renderizado en pantalla.

**Por qué no se hizo XOR, Reed-Solomon ni RaptorQ entre paquetes:** reconstruyen un
paquete perdido a partir de otros, y ese paquete llega tarde; los códecs con estado (como
`deflate_flujo`) necesitan los paquetes en orden y se desincronizarían.

## 7. Siguiente entrega

CRC-32 del marco (saber con certeza si el paquete llegó íntegro), entrelazado
(ráfagas), borrado de paquetes, Reed-Solomon y XOR entre paquetes (pérdida de paquetes
enteros), RaptorQ, y estado del arte de 5G NR (LDPC para datos, Polar para control). Detalle y fuentes en [docs/10 §8-9](10-codificacion-canal.md).
