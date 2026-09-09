# lab-free5gc — una red móvil 5G de verdad, dentro de una sola máquina

Este repositorio levanta una **red 5G completa y funcional** usando solo software:
un núcleo de red ([free5GC](https://free5gc.org/)) y una antena y un teléfono
simulados ([UERANSIM](https://github.com/aligungr/UERANSIM)). No hace falta ni
una antena, ni una SIM, ni un teléfono, ni licencia de espectro.

Al terminar tendrás un "teléfono" que se registra en tu red, obtiene una IP y
navega a través de ella — igual que tu móvil real con su operador, pero todo
ocurriendo dentro de un servidor tuyo y con cada paso visible.

> **¿Para quién es esta guía?**
> Para alguien que sabe usar una terminal de Linux pero **no sabe de redes
> móviles ni de redes en general**. No se asume nada: cada término se explica la
> primera vez que aparece. Si algo no se entiende, es un fallo de esta guía —
> abre un issue.

---

## Índice

1. [Qué hace esto, con una analogía](#1-qué-hace-esto-con-una-analogía)
2. [El vocabulario mínimo (10 palabras)](#2-el-vocabulario-mínimo-10-palabras)
3. [Qué necesitas antes de empezar](#3-qué-necesitas-antes-de-empezar)
4. [Instalación paso a paso](#4-instalación-paso-a-paso)
5. [Cómo se reparten los servicios](#5-cómo-se-reparten-los-servicios)
6. [Qué hace cada servicio del docker-compose](#6-qué-hace-cada-servicio-del-docker-compose)
7. [Las cinco redes internas y por qué son cinco](#7-las-cinco-redes-internas-y-por-qué-son-cinco)
8. [Perfiles: arrancar poco o arrancar todo](#8-perfiles-arrancar-poco-o-arrancar-todo)
9. [Comprobar que funciona](#9-comprobar-que-funciona)
10. [Cuando algo falla](#10-cuando-algo-falla)
11. [Mapa del repositorio](#11-mapa-del-repositorio)
12. [Documentación detallada](#12-documentación-detallada)

---

## 1. Qué hace esto, con una analogía

Piensa en un **aeropuerto**.

Hay dos mundos que no se mezclan. Por un lado la **torre de control**: nadie
viaja en ella, pero decide quién puede aterrizar, quién es cada avión y qué
pista le toca. Por otro lado las **pistas y las cintas de equipaje**: ahí no se
decide nada, solo se mueven cosas, y se mueven muchas y muy rápido.

Una red móvil está partida exactamente igual:

| En el aeropuerto | En una red 5G | En este repositorio |
|---|---|---|
| Torre de control | **Plano de control** — autentica, autoriza, decide | `nrf` `amf` `smf` `ausf` `udm` `udr` `pcf` `nssf` |
| Pistas y cintas | **Plano de usuario** — transporta los datos | `upf` |
| El avión | El teléfono (**UE**) | `ue` |
| La torre de radio | La antena (**gNB**) | `gnb` |
| El registro de pasajeros | La base de datos de abonados | `mongodb` |

Esta separación es la idea más importante de todo el proyecto. Cuando algo
falla, lo primero es saber **en cuál de los dos mundos** falló:

- Si el teléfono **no se registra**, el problema está en la torre de control.
- Si se registra, obtiene IP, pero **no navega**, el problema está en la pista.

Son dos investigaciones completamente distintas, con comandos distintos. La
mayoría del tiempo que se pierde en laboratorios como este se pierde depurando
el mundo equivocado.

### Lo que ocurre cuando el "teléfono" se enciende

Ocho pasos, siempre en este orden. Guárdalos: son también el orden en que hay
que depurar.

```
1. Cada pieza del núcleo se presenta en el directorio (NRF)
      "hola, soy el AMF, estoy en esta dirección"
2. El SMF y el UPF se dan la mano            <-- se rompe aquí muy a menudo
      "cuando yo decida abrir un tubo de datos, tú lo transportas"
3. La antena llama al AMF por SCTP
4. El AMF responde y acepta la antena         <-- "NG Setup successful"
5. El teléfono encuentra la antena (radio simulada)
6. El teléfono se autentica con su clave secreta  <-- "Registration successful"
7. Se abre el tubo de datos (sesión PDU)      <-- se rompe aquí muy a menudo
8. Aparece la interfaz uesimtun0 con una IP   <-- el laboratorio funciona
```

El paso 8 es la meta. Cuando `uesimtun0` existe y responde a un `ping`, tienes
una red móvil operativa.

---

## 2. El vocabulario mínimo (10 palabras)

No hace falta más para seguir esta guía. Cada término tiene su explicación larga
en [docs/01-conceptos-5g.md](docs/01-conceptos-5g.md).

| Palabra | Qué es, en una frase |
|---|---|
| **UE** | *User Equipment*. El teléfono. Aquí es un programa, no un aparato. |
| **gNB** | La antena 5G (la "g" es de *generation*, "NB" de *NodeB*). |
| **Núcleo / core** | El cerebro del operador: todo lo que no es antena ni teléfono. |
| **NF** | *Network Function*. Una pieza del núcleo. Hay 8 aquí, cada una con un trabajo. |
| **PLMN** | El identificador de tu operador: país (**MCC**) + operador (**MNC**). Aquí `732/101`. |
| **IMSI** | El número de 15 dígitos de la SIM. Aquí `732101000000001`. |
| **Plano de control** | La torre: decide. Habla HTTP y SCTP. |
| **Plano de usuario** | La pista: transporta. Habla GTP-U. |
| **Túnel GTP-U** | El "tubo" por donde viajan los datos del teléfono. Añade 36 bytes a cada paquete. |
| **Interfaz N2, N3, N4…** | Nombres estándar de los cables entre piezas. N2 = antena↔AMF, N3 = antena↔UPF, N4 = SMF↔UPF. |

### Los tres números que deben coincidir en todas partes

Si solo recuerdas una cosa de este documento, que sea esta. La causa número uno
de fallos en free5GC es cambiar uno de estos números **en un sitio y no en los
otros**:

```
PLMN      732 / 101         (país / operador)
TAC       000001            (zona geográfica)
S-NSSAI   sst=1 sd=010203   (el "carril" de red que usa el teléfono)
```

Aparecen repartidos entre once ficheros **y también dentro de MongoDB**. Por eso
el repositorio incluye un validador que los compara todos:

```bash
python3 ci/check-plmn.py
```

Ejecútalo siempre que toques un YAML. Detalles en
[docs/06-plmn-e-identidades.md](docs/06-plmn-e-identidades.md).

---

## 3. Qué necesitas antes de empezar

### Hardware

| Recurso | Mínimo real | Recomendado | Por qué |
|---|---|---|---|
| CPU | 4 núcleos | 6+ | El UPF encapsula tráfico dentro del kernel |
| RAM | 4 GB para el núcleo solo | 8 GB | Con observabilidad son 22 contenedores |
| Disco | 20 GB | 64 GB | MongoDB, Prometheus y las imágenes de Docker |

> ⚠️ **Esto no es un consejo teórico.** En un host de 4 núcleos y 3.8 GB,
> arrancar los 22 contenedores de golpe llevó la carga del sistema a **105** y
> el servidor dejó de responder incluso a SSH. Por eso el arranque por defecto
> son solo 10 contenedores. Lee la
> [sección 8](#8-perfiles-arrancar-poco-o-arrancar-todo) antes de activar la
> observabilidad.

### Software

- **Proxmox VE** (o cualquier Linux que pueda compilar módulos de kernel).
- Un **contenedor LXC privilegiado**, o una VM. No sirve un LXC normal: hace
  falta crear interfaces de red, y eso un contenedor sin privilegios no puede.
- **Docker** y **docker compose** dentro de ese contenedor.
- El módulo de kernel **`gtp5g`**, compilado en el host. Es la pieza que hace
  el túnel de datos, y sin ella el UPF no arranca.

### El detalle que sorprende a todo el mundo

Un contenedor LXC **comparte el kernel de la máquina anfitriona**. No tiene uno
propio, como sí tendría una máquina virtual. Consecuencia práctica:

> `gtp5g` y `sctp` se instalan **en el host Proxmox**, no dentro del contenedor,
> aunque quien los usa sea un programa que vive dentro del contenedor.

Si intentas `modprobe gtp5g` dentro del LXC no funcionará, y el mensaje de error
no explicará por qué.

---

## 4. Instalación paso a paso

Seis pasos. **Cada uno termina con una comprobación: no pases al siguiente si
falla**, porque el error reaparecerá tres pasos después disfrazado de otra cosa.

La versión ampliada de cada paso, con las razones detrás de cada opción, está en
[docs/02-instalacion.md](docs/02-instalacion.md).

### Paso 1 — Módulos de kernel, en el HOST Proxmox

```bash
apt update && apt install -y git build-essential dkms pve-headers-$(uname -r)
```

```bash
cd /usr/src && git clone https://github.com/free5gc/gtp5g.git gtp5g-0.9.0 && cd gtp5g-0.9.0 && git checkout v0.9.0
```

```bash
dkms add -m gtp5g -v 0.9.0 && dkms build -m gtp5g -v 0.9.0 && dkms install -m gtp5g -v 0.9.0
```

Se usa **DKMS** en lugar de `make install` por un motivo concreto: DKMS
recompila el módulo automáticamente cuando Proxmox actualiza el kernel. Sin él,
el laboratorio se rompe tras cada `apt upgrade` y el síntoma es "el UPF ya no
arranca y ayer funcionaba".

Que los módulos se carguen en cada arranque:

```bash
printf 'gtp5g\nsctp\n' > /etc/modules-load.d/free5gc.conf && modprobe gtp5g && modprobe sctp
```

**`sctp` no es opcional.** El cable entre la antena y el AMF (la interfaz N2) no
usa TCP, usa SCTP, que es un protocolo distinto. Si el módulo falta, el AMF falla
al abrir su puerto con un `protocol not supported` que no dice nada.

✅ **Comprobación** — deben aparecer las dos líneas:

```bash
lsmod | grep -E '^(gtp5g|sctp)'
```

### Paso 2 — El contenedor LXC

Con el contenedor **detenido**, edita `/etc/pve/lxc/110.conf` (cambia el número
por el tuyo):

```ini
arch: amd64
cores: 4
memory: 8192
swap: 2048
features: nesting=1,keyctl=1
hostname: lab-free5gc
unprivileged: 0
net0: name=eth0,bridge=vmbr0,firewall=0,gw=192.168.1.1,ip=192.168.1.50/24,type=veth

lxc.apparmor.profile: unconfined
lxc.cap.drop:
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net dev/net none bind,create=dir
lxc.mount.auto: proc:rw sys:rw cgroup:rw
```

Las cuatro líneas que más se olvidan, y qué pasa si faltan:

| Línea | Si falta |
|---|---|
| `unprivileged: 0` | El UPF no puede crear su interfaz de red. No hay forma de sortearlo. |
| `lxc.apparmor.profile: unconfined` | `permission denied` en operaciones que los permisos sí permiten. El error más confuso de todos. |
| `firewall=0` | El firewall de Proxmox descarta SCTP y GTP-U en silencio. |
| `keyctl=1` | Docker arranca, pero falla de forma intermitente al montar volúmenes. |

✅ **Comprobación:**

```bash
pct start 110 && pct exec 110 -- ls -l /dev/net/tun && pct exec 110 -- grep gtp5g /proc/modules
```

`/dev/net/tun` debe existir, y `gtp5g` debe aparecer — ese `/proc/modules` es en
realidad el del host, que es justo lo que se quiere comprobar.

### Paso 3 — Docker dentro del contenedor

```bash
curl -fsSL https://get.docker.com | sh && systemctl enable --now docker
```

```bash
cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "50m", "max-file": "3" },
  "default-address-pools": [ { "base": "10.100.0.0/16", "size": 24 } ]
}
EOF
```

```bash
systemctl restart docker
```

`default-address-pools` importa más de lo que parece: por defecto Docker se
inventa redes en rangos como `192.168.x.x`, que es **exactamente donde vive tu
red doméstica**. Cuando eso pasa, el contenedor pierde acceso a la LAN sin
motivo aparente. Esta línea lo confina a `10.100.x.x`.

✅ **Comprobación:**

```bash
docker run --rm hello-world
```

### Paso 4 — Permitir el tráfico desde tu red local

Docker instala una regla que **descarta todo el tráfico que entra desde fuera**
hacia sus redes internas. Es una protección razonable, pero aquí bloquea el
acceso a Grafana y a la consola web desde tu portátil. El repositorio trae un
script que abre solo lo necesario:

```bash
install -m 755 deploy/lab5g-netrules.sh /usr/local/sbin/lab5g-netrules.sh
```

```bash
install -m 644 deploy/lab5g-netrules.service /etc/systemd/system/ && systemctl enable --now lab5g-netrules.service
```

Se instala como servicio de systemd porque **las reglas de iptables se pierden
al reiniciar**. Sin el servicio, funciona hoy y no funciona mañana.

✅ **Comprobación** — desde tu portátil:

```bash
ping 192.168.1.50
```

### Paso 5 — Clonar y configurar

```bash
git clone https://github.com/J4F3ET/lab-free5gc.git && cd lab-free5gc && cp .env.example .env
```

Edita `.env` y cambia como mínimo `GRAFANA_ADMIN_PASSWORD`.

✅ **Comprobación** — las tres deben pasar:

```bash
bash ci/preflight.sh && docker compose config -q && python3 ci/check-plmn.py
```

`ci/preflight.sh` comprueba de una pasada las 13 condiciones de los pasos 1 a 4
y dice **cuál** falta. Un `WARN` de RAM es normal en hardware modesto; un `BAD`
no. Merece la pena ejecutarlo aunque creas que todo está bien.

### Paso 6 — Arrancar

Por etapas, a propósito. El motivo está en la
[sección 8](#8-perfiles-arrancar-poco-o-arrancar-todo).

```bash
docker compose up -d --wait --wait-timeout 600 mongodb
```

```bash
docker compose up -d && bash ci/wait-ready.sh
```

```bash
bash ci/provision-subscriber.sh
```

```bash
docker compose --profile ran up -d
```

✅ **Comprobación final:**

```bash
COMPOSE_PROFILES=ran bash ci/smoke-ue.sh
```

Si termina en verde, tienes una red 5G funcionando.

> **¿Por qué hace falta el tercer comando?** El abonado vive en MongoDB, **no en
> el repositorio**. Si borras el volumen de Mongo (`docker compose down -v`), la
> SIM desaparece y el teléfono deja de registrarse aunque todos los ficheros
> estén perfectos. Es un fallo desconcertante la primera vez.
> `provision-subscriber.sh` la recrea.

---

## 5. Cómo se reparten los servicios

Hay **tres niveles**, y confundirlos es la fuente principal de errores. Cada
cosa vive en un nivel y solo en uno.

```
┌───────────────────────────────────────────────────────────────┐
│  NIVEL 1 — HOST PROXMOX (la máquina física)                   │
│                                                               │
│    módulo gtp5g   módulo sctp   ip_forward                    │
│    ↑ El kernel es UNO y lo comparten todos los de abajo       │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  NIVEL 2 — CONTENEDOR LXC 110      192.168.1.50         │  │
│  │                                                         │  │
│  │    dockerd    reglas iptables    el runner de CI        │  │
│  │                                                         │  │
│  │  ┌───────────────────────────────────────────────────┐  │  │
│  │  │  NIVEL 3 — CONTENEDORES DOCKER                    │  │  │
│  │  │                                                   │  │  │
│  │  │   los 22 servicios del docker-compose.yaml        │  │  │
│  │  │   repartidos en 5 redes internas                  │  │  │
│  │  └───────────────────────────────────────────────────┘  │  │
│  └─────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────┘
```

| Si el problema es… | Está en el nivel | Se arregla con |
|---|---|---|
| El UPF no puede crear `upfgtp` | 1 (kernel) | `modprobe gtp5g` en el **host** |
| El AMF no abre su puerto SCTP | 1 (kernel) | `modprobe sctp` en el **host** |
| No se ve Grafana desde el portátil | 2 (LXC) | `lab5g-netrules.service` |
| Un contenedor reinicia en bucle | 3 (Docker) | `docker compose logs <servicio>` |

### Lo que se publica hacia tu red local

Solo cuatro puertos salen del contenedor. Todo lo demás es interno y
deliberadamente inalcanzable desde fuera:

| Dirección | Qué es | Perfil |
|---|---|---|
| `http://192.168.1.50:3000` | **Grafana** — gráficas del estado de la red | `obs` |
| `http://192.168.1.50:9090` | **Prometheus** — las métricas en crudo | `obs` |
| `http://192.168.1.50:8888` | **Dozzle** — los logs de todos los contenedores en el navegador | `obs` |
| `http://192.168.1.50:5000` | **WebUI** — consola de abonados de free5GC | `tools` |

---

## 6. Qué hace cada servicio del docker-compose

Los 22 servicios de [`docker-compose.yaml`](docker-compose.yaml), en lenguaje
llano. La columna **⚠️** marca los tres servicios donde se concentran casi
todos los fallos.

### Datos

| Servicio | Qué hace, de verdad | ⚠️ |
|---|---|---|
| `mongodb` | Guarda los abonados: quién es cada SIM, su clave secreta y qué tiene contratado. Es el único servicio con estado persistente: si borras su volumen, pierdes las SIM. Tarda **~90 s** en arrancar en hardware modesto, y por eso su chequeo de salud tiene `start_period: 180s`. | |

### Plano de control — "la torre"

| Servicio | Qué hace, de verdad | ⚠️ |
|---|---|---|
| `nrf` | La **agenda telefónica** del núcleo. Las demás piezas se apuntan aquí al arrancar y luego se buscan unas a otras preguntándole. Arranca primero por necesidad: sin el NRF, ninguna pieza encuentra a nadie. | |
| `amf` | El **recepcionista**. Es lo único con lo que habla el teléfono directamente. Recibe el "quiero conectarme", coordina la autenticación y decide si entras. Habla SCTP con la antena. | ⚠️ |
| `ausf` | El **verificador de identidad**. Ejecuta el desafío criptográfico (5G-AKA): manda un reto, y solo la SIM con la clave correcta sabe responderlo. | |
| `udm` | El **intérprete** de los datos del abonado. Calcula las respuestas criptográficas y descifra el identificador oculto que envía el teléfono (SUCI). | |
| `udr` | El **archivero**. La única pieza que habla con MongoDB; las demás le preguntan a él. | |
| `pcf` | Las **reglas del contrato**: cuánta velocidad, qué prioridad, qué está permitido. | |
| `nssf` | El **asignador de carriles**. El 5G puede tener varias redes lógicas sobre el mismo hardware (*network slicing*); esta pieza dice qué carril te toca. Aquí solo hay uno: `sst=1, sd=010203`. | |
| `smf` | El **jefe de operaciones del tubo de datos**. No transporta nada: decide que hay que abrir un tubo, elige la IP del teléfono y **le da las órdenes al UPF** por la interfaz N4. | ⚠️ |

### Plano de usuario — "la pista"

| Servicio | Qué hace, de verdad | ⚠️ |
|---|---|---|
| `upf` | Donde pasan **los datos reales**. Recibe los paquetes del teléfono envueltos en GTP-U, les quita el envoltorio y los saca hacia la red de destino. Es el único servicio `privileged`, porque crea una interfaz de kernel (`upfgtp`) usando `gtp5g`. Está en **tres redes a la vez**: recibe órdenes por una, datos del túnel por otra, y saca el tráfico por una tercera. | ⚠️ |

### Radio simulada — perfil `ran`

| Servicio | Qué hace, de verdad |
|---|---|
| `gnb` | La **antena**. Puentea dos mundos: habla el idioma del teléfono por un lado y, por el otro, con el AMF (control) y el UPF (datos). Está en tres redes por eso mismo. |
| `ue` | El **teléfono**. Se enciende, busca la antena, se autentica con su clave y, si todo va bien, crea la interfaz `uesimtun0` con una IP del rango `10.60.0.0/16`. Esa interfaz **es** la prueba de que la red funciona. |
| `dn-iperf` | El **destino**. Un servidor al que el teléfono manda tráfico para demostrar que el túnel transporta datos de verdad. Hace de "internet" del laboratorio. |

### Herramientas — perfil `tools`

| Servicio | Qué hace, de verdad |
|---|---|
| `webui` | Consola web oficial de free5GC para dar de alta abonados a mano, en `:5000`. **No hace falta** para el flujo normal: `ci/provision-subscriber.sh` hace lo mismo por línea de comandos y sin arrancar un contenedor extra. |

### Observabilidad — perfil `obs`

| Servicio | Qué hace, de verdad |
|---|---|
| `prometheus` | Pregunta cada pocos segundos "¿cómo estás?" a todos los demás y guarda las respuestas con su hora. Guarda **números**, no texto. 15 días de historia. |
| `grafana` | Dibuja esos números. Trae un panel ya montado (`lab5g-overview`) que no hay que configurar: aparece solo al arrancar. Usuario `admin`, contraseña la de tu `.env`. |
| `cadvisor` | Mide CPU, memoria y red **de cada contenedor por separado**. Responde a "¿quién se está comiendo la máquina?". |
| `node-exporter` | Mide el contenedor LXC **completo**: CPU, RAM, disco, swap. Responde a "¿es la máquina la que va mal, y no un servicio?". |
| `pushgateway` | Un buzón. Las pruebas que se ejecutan de vez en cuando —no de forma continua— dejan aquí su resultado para que Prometheus lo recoja después. |
| `dozzle` | Los **logs** de todos los contenedores en una web, en vivo. Prometheus guarda números; los mensajes de texto se ven aquí. La alternativa estándar (Loki) pesa ~250 MB de RAM frente a los ~20 MB de dozzle, y en este host eso decide. |

### Sondas — perfil `probes`

Estos dos servicios usan un truco que conviene entender, porque es lo que hace
creíbles las mediciones:

```yaml
network_mode: "service:ue"
```

Significa **"vive dentro del teléfono"**: comparte su pila de red completa,
interfaces incluidas. Así, cuando la sonda hace un `ping`, ese ping **sale por
`uesimtun0`** y atraviesa el túnel GTP-U de verdad. Si midiera desde fuera,
estaría midiendo la red de Docker y no la red 5G, y el número no valdría nada.

| Servicio | Qué hace, de verdad |
|---|---|
| `blackbox` | Mide **latencia y disponibilidad**: ¿responde el destino a través del túnel, y en cuánto tiempo? |
| `iperf-probe` | Mide **velocidad**. Cada 5 minutos hace una transferencia de 10 s contra `dn-iperf` y deja el resultado en el `pushgateway`. |

> ⚠️ `probes` **necesita** `ran` activo. Si el `ue` no existe, estos dos
> contenedores no tienen dónde vivir y fallan al arrancar.

---

## 7. Las cinco redes internas y por qué son cinco

Podrían ser una sola. Están separadas por una razón muy práctica: **para poder
capturar tráfico limpio**. Con todo mezclado, un `tcpdump` devuelve una sopa
ilegible donde lo que buscas se esconde entre miles de mensajes de otras piezas.

| Red | Rango | Quién vive ahí | Para qué sirve la separación |
|---|---|---|---|
| `sbi_net` | `10.100.200.0/24` | Todo el plano de control, mongo, webui | Es la "oficina": las piezas del núcleo se hablan entre sí |
| `n3_net` | `10.100.201.0/24` | `upf` `gnb` | Aísla el túnel de datos: `tcpdump -i any udp port 2152` sale limpio |
| `radio_net` | `10.100.203.0/24` | `gnb` `ue` | Distingue "el teléfono no encuentra la antena" de "la antena no encuentra el núcleo" |
| `dn_net` | `10.100.204.0/24` | `upf` `dn-iperf` | El NAT se aplica a una interfaz concreta y estable, no a "la que Docker haya llamado eth0 hoy" |
| `obs_net` | `10.100.205.0/24` | Toda la observabilidad | Prometheus pregunta cada 15 s; sin aislarlo, contamina todas las capturas |
| — | `10.60.0.0/16` | Las IP que reciben los teléfonos | No es una red de Docker: la reparte el UPF |

Cada servicio tiene además una **IP fija**, no una que cambie en cada arranque.
Es imprescindible: varias configuraciones se refieren a otra pieza por su IP, y
con direcciones dinámicas el laboratorio se rompería en cada reinicio.

La tabla completa de direcciones y puertos está en
[docs/03-servicios.md](docs/03-servicios.md).

---

## 8. Perfiles: arrancar poco o arrancar todo

### La lección que hay detrás

Arrancar los 22 contenedores a la vez en un host de 4 núcleos y 3.8 GB **no
funciona, y no es un problema de paciencia**. Lo que ocurre es una espiral:

```
demasiados servicios arrancando a la vez
   → el disco se satura y ninguno termina de arrancar
      → los chequeos de salud expiran
         → Docker reinicia los servicios "caídos"
            → cada reinicio consume el disco que los demás necesitaban
               → carga del sistema: 105 en 4 núcleos. El host deja de responder.
```

Con 26 veces más carga que núcleos disponibles, ampliar los tiempos de espera no
arregla nada: hay que arrancar **menos cosas**. De ahí los perfiles.

### Los perfiles

| Comando | Contenedores | Qué obtienes |
|---|---|---|
| `docker compose up -d` | **10** | El núcleo 5G. Lo mínimo utilizable. |
| `docker compose --profile ran up -d` | **13** | + antena, teléfono y destino. **Una red 5G completa y verificable.** |
| `… --profile obs up -d` | **19** | + Grafana, Prometheus y los logs en web |
| `… --profile obs --profile probes up -d` | **21** | + las mediciones desde dentro del teléfono |
| `… --profile tools up -d` | **22** | + la consola web de abonados |

**Recomendación:** quédate en 13 mientras montas el laboratorio. Añade `obs`
solo cuando `smoke-ue.sh` pase en verde, y solo si te sobran 2 GB de RAM.

### El detalle que confunde a todo el mundo

Un servicio de un perfil que no está activo es **invisible** para
`docker compose`, incluso si el contenedor está corriendo. Esto responde
`no such service`:

```bash
docker compose logs ue
```

Con el perfil declarado, en cambio, funciona:

```bash
COMPOSE_PROFILES=ran docker compose logs ue
```

Lo más cómodo es exportarlo una vez al abrir la terminal:

```bash
export COMPOSE_PROFILES=ran
```

---

## 9. Comprobar que funciona

### La prueba de un solo comando

```bash
COMPOSE_PROFILES=ran bash ci/smoke-ue.sh
```

Recorre las seis condiciones que tienen que cumplirse, en orden, esperando a
cada una con su propio margen de tiempo:

1. La antena habló con el AMF (`NG Setup successful`)
2. El teléfono se registró (`Registration is successful`)
3. La interfaz `uesimtun0` existe y tiene IP
4. El UPF creó su interfaz `upfgtp`
5. Un `ping` **por el túnel** llega al destino
6. Hay velocidad medible

Si algo falla, dice **cuál** de los seis y en qué estado quedó. Ahí empieza la
[sección 10](#10-cuando-algo-falla).

### A mano, mirando el resultado

```bash
export COMPOSE_PROFILES=ran
```

¿Existe la interfaz del teléfono, y con qué IP? Una salida
`uesimtun0 UNKNOWN 10.60.0.1/32` es el éxito:

```bash
docker compose exec ue ip -brief addr show uesimtun0
```

¿Pasan datos por el túnel? El `-I` es la clave: fuerza la salida por el túnel en
lugar de por la red normal de Docker.

```bash
docker compose exec ue ping -c3 -I uesimtun0 10.100.204.10
```

¿Se registraron las 8 piezas del núcleo en la agenda?

```bash
docker compose exec nrf curl -s http://nrf:8000/nnrf-nfm/v1/nf-instances | grep -o '"nfType":"[A-Z]*"' | sort | uniq -c
```

### Con Grafana

Con el perfil `obs` activo, entra en `http://192.168.1.50:3000` y abre el panel
**lab5g-overview**. Está montado para leerse de arriba abajo:

| Fila | Qué te dice |
|---|---|
| Arriba | Un cuadro verde por pieza del núcleo. Rojo = esa pieza no responde. |
| Medio | `ue_tunnel_up`: ¿existe el túnel? Latencia y velocidad medidas **desde el teléfono**. |
| Abajo | CPU y memoria por contenedor, e **iowait** del LXC. |

Ese último panel es el más valioso del conjunto. En este tipo de host el cuello
de botella real casi nunca es la CPU: es el **disco**. Cuando el `iowait` sube,
los chequeos de salud empiezan a expirar y los servicios parecen "caídos" sin
tener nada roto. Si ves timeouts inexplicables, mira ahí primero.

---

## 10. Cuando algo falla

### Primero: ¿en qué mundo estás?

```
¿Existe uesimtun0?    (docker compose exec ue ip addr show uesimtun0)
│
├─ NO  → el problema está en LA TORRE (plano de control)
│         Mira, en este orden:  logs de ue → gnb → amf → smf
│
└─ SÍ  → ¿ping -I uesimtun0 funciona?
          ├─ NO  → el problema está en LA PISTA (plano de usuario)
          │         Mira: logs del upf, y captura en n3_net
          └─ SÍ  → funciona. Si falla solo con transferencias grandes → es el MTU
```

El árbol de decisión completo, con los comandos de cada rama, está en
[docs/05-diagnostico.md](docs/05-diagnostico.md).

### Los cinco fallos que verás de verdad

| Síntoma | Causa casi segura | Comprobación |
|---|---|---|
| El UPF reinicia en bucle | `gtp5g` no está cargado **en el host** | `lsmod \| grep gtp5g` en el host |
| El teléfono no se registra | Los tres números no coinciden, o falta el abonado | `python3 ci/check-plmn.py`, luego `bash ci/provision-subscriber.sh` |
| Se registra pero no hay `uesimtun0` | El SMF y el UPF no se dieron la mano (N4) | `docker compose logs smf \| grep -i pfcp` |
| El túnel sube pero no pasa nada | La antena manda los datos a la dirección equivocada | Comparar `gtpIp` de `gnb.yaml` con la IP N3 del UPF |
| `ping` va, transferencias grandes se cuelgan | MTU: GTP-U añade 36 bytes | `UE_MTU` debe ser `DOCKER_MTU - 36` |

### Recoger todo de golpe

```bash
bash ci/collect-logs.sh
```

Deja un paquete en `/tmp/lab5g-diag/` con los logs de cada contenedor, el estado
de red del LXC, las reglas de iptables, la configuración efectiva y una captura
corta de los tres planos. Es lo que hay que adjuntar al pedir ayuda.

### Empezar de cero

```bash
docker compose --profile ran --profile obs --profile probes --profile tools down
```

```bash
docker compose up -d --wait --wait-timeout 600 mongodb && docker compose up -d && bash ci/wait-ready.sh && docker compose --profile ran up -d
```

Fíjate en que no lleva `-v`. **`down -v` borra el volumen de MongoDB** y con él
el abonado: si lo usas, ejecuta después `bash ci/provision-subscriber.sh`.

---

## 11. Mapa del repositorio

```
docker-compose.yaml      Los 22 servicios y las 5 redes. El fichero central.
.env.example             Versiones, PLMN, direcciones, MTU. Cópialo a .env.

config/                  Configuración del núcleo free5GC — un YAML por pieza
  nrfcfg.yaml            La agenda: dónde escucha, cómo llega a MongoDB
  amfcfg.yaml            PLMN, TAC, zonas servidas.   <-- toca los 3 números
  smfcfg.yaml            Pool de IP de los teléfonos, DNN, nodeID de N4
  upfcfg.yaml            Interfaz GTP-U y el mismo nodeID que el SMF
  uerouting.yaml         Rutas por abonado (se refiere a él por su IMSI)
  udrcfg / udmcfg /
  ausfcfg / pcfcfg /
  nssfcfg / webuicfg     El resto de piezas
  upf-nat.sh             Da salida al tráfico del teléfono. Resuelve su
                         interfaz POR IP, no por nombre: Docker no garantiza
                         que dn_net sea siempre eth1.

ueransim/
  gnb.yaml               La antena: sus tres IP y a qué AMF llama
  ue.yaml                El teléfono: IMSI, clave y qué carril pide
  ue-entrypoint.sh       Arranca el teléfono y ajusta el MTU del túnel

ci/                      Todo esto se puede ejecutar a mano, no solo en CI
  preflight.sh           13 comprobaciones del entorno antes de desplegar
  check-plmn.py          Compara los tres números en los 11 sitios
  wait-ready.sh          Espera al núcleo, con presupuesto POR comprobación
  provision-subscriber.sh  Crea la SIM en MongoDB
  smoke-ue.sh            La prueba end-to-end de 6 pasos
  collect-logs.sh        Paquete de diagnóstico

deploy/                  Lo que va instalado en el LXC, fuera de Docker
  lab5g-netrules.sh      Abre el paso desde la LAN a las redes internas
  lab5g-netrules.service Para que sobreviva a los reinicios
  sudoers-lab5g-deploy   Permisos acotados del usuario del runner
  bootstrap-runner.sh    Instala lo anterior de forma reproducible

observability/
  prometheus/            A quién preguntar y cada cuánto
  blackbox/              Cómo medir latencia desde dentro del teléfono
  probe/iperf-probe.sh   Cómo medir velocidad
  grafana/provisioning/  Panel y fuente de datos, montados automáticamente

.github/workflows/
  deploy.yml             Despliegue automático. Ver docs/07-cicd.md
```

---

## 12. Documentación detallada

Este README cubre el camino normal. Cuando necesites el "por qué":

| Documento | Cuándo leerlo |
|---|---|
| [01 — Conceptos 5G](docs/01-conceptos-5g.md) | Quieres entender qué hace cada pieza y por qué existe |
| [02 — Instalación detallada](docs/02-instalacion.md) | Algo de los pasos 1 a 4 no salió, o quieres la razón de cada opción |
| [03 — Referencia de servicios](docs/03-servicios.md) | Necesitas una IP, un puerto o una dependencia exacta |
| [04 — Operación diaria](docs/04-operacion.md) | Ya funciona y quieres usarlo, medirlo o modificarlo |
| [05 — Diagnóstico](docs/05-diagnostico.md) | Algo se rompió y necesitas el árbol completo síntoma → comando |
| [06 — PLMN e identidades](docs/06-plmn-e-identidades.md) | Vas a cambiar el operador, el IMSI o el slice |
| [07 — CI/CD](docs/07-cicd.md) | Quieres que se despliegue solo al hacer push |

---

## Licencia y créditos

Laboratorio construido sobre [free5GC](https://free5gc.org/) y
[UERANSIM](https://github.com/aligungr/UERANSIM), de sus respectivos autores.
Este repositorio aporta la integración, el direccionamiento, la observabilidad y
el pipeline de despliegue.

El PLMN `732/101` corresponde a Colombia y **está asignado a un operador real**.
Aquí se usa como *simulación* en un entorno sin radiofrecuencia: no hay
transmisión, no hay interferencia, y nada de esto es alcanzable desde una red
móvil real.
