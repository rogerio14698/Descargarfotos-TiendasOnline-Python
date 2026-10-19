# Biblioteca personal de productos e imágenes

Requiere Python 3.10 o posterior. No necesita instalar paquetes.

## Uso

1. Añade las direcciones que quieras procesar a [urls.md](urls.md), una por línea.
   También admite enlaces Markdown, líneas vacías y comentarios que empiezan con `#`.
2. Desde esta carpeta, ejecuta en PowerShell:

```powershell
py descargar.py
```

El archivo de URLs y el destino se buscan junto al script, aunque lo ejecutes
desde otra carpeta. En equipos que tengan el comando `python`, también puedes
usar `python descargar.py`.

Solo descarga contenido público. Usa imágenes propias o con autorización/licencia
adecuada: poder descargarlas no da permiso para publicarlas en otra tienda.
Respeta las condiciones de la fuente. No se eluden inicios de sesión, CAPTCHA,
bloqueos ni restricciones de acceso.

## Organización

```text
carpetaDestino\
  tienda-identificador\
    coleccion-identificador\
      coleccion.json
      catalogo.csv
      producto_identificador\
        producto.json
        datos.txt
        img_identificador_version.jpg
        img_identificador_version.txt
```

- **producto.json**: datos normalizados, descripción original, variantes/ofertas,
  precios, disponibilidad, procedencia, fecha y copia de los datos originales.
  Su lista `imagenes` asocia cada foto con su archivo local, URL, texto alternativo,
  dimensiones y variantes vinculadas cuando la fuente proporciona esa relación.
- **TXT**: ficha del producto y ficha de cada imagen.
- **catalogo.csv**: una fila por imagen activa y rutas relativas al directorio de
  la colección. Separador `;`, UTF-8 con BOM para Excel. Los textos que puedan
  interpretarse como fórmulas se protegen; el JSON conserva el texto original.
- **coleccion.json**: selección de origen, lista de fichas, fecha y errores.
- **errores.txt**: aparece si hay errores, tanto en la selección como en el
  destino general. El proceso termina con código `1` si algo falla y continúa
  con las demás URLs.

Los identificadores evitan colisiones entre nombres iguales y caracteres
problemáticos en Windows. No dependen del orden ni del nombre del producto.
Cada producto individual o tienda completa se agrupa en `productos`. Una misma
prenda en dos colecciones se guarda dentro de ambas, tal como se ha solicitado.
Las selecciones con distintos filtros tienen carpetas independientes.

## Fuentes compatibles y límites

### Shopify

Admite URLs de colección, producto individual, producto dentro de colección y
raíz de tienda, incluidos prefijos de idioma habituales. Usa los endpoints
públicos de productos y pagina hasta terminar.

Sin filtro incluye disponibles y agotados. Respeta `filter.v.availability=1`
(disponibles), `=0` (agotados) o ambos valores. Otros parámetros, filtros,
ordenaciones y seguimientos **se rechazan explícitamente**: no se ignorarán
porque eso podría descargar una selección diferente. Quita esos parámetros
solo si quieres descargar la selección completa.

Los endpoints públicos de Shopify no indican necesariamente la moneda.
En ese caso se guarda `null` y se muestra un aviso: no se presupone BRL ni se
deduce una moneda a partir del dominio. Los precios son los valores públicos
del endpoint y pueden diferir de precios regionales o de una sesión autenticada.
Se conservan todos los campos originales, incluidas composición y medidas
si forman parte de la descripción; no se inventan campos ausentes.

### Otras webs

Hay soporte genérico para JSON-LD de Schema.org:

- `Product`, también dentro de `@graph`.
- `ItemList` con enlaces a las fichas de producto.
- Paginación publicada con `rel="next"`.
- Ofertas, moneda y disponibilidad cuando estén declaradas.

**No equivale a compatibilidad universal con cualquier URL.** Las webs que
solo generan datos con JavaScript, usan otro formato, referencias complejas
de Schema.org o no publican datos de producto requieren un adaptador específico.
Se informa del error en lugar de bajar logos, banners o imágenes sin ficha.
No se rastrean indiscriminadamente todos los enlaces de una web.

Se rechazan catálogos enlazados a otros dominios, ofertas con varias monedas
incompatibles y filtros genéricos no implementados. Hay un límite explícito
de 200 páginas por URL; alcanzarlo es un error de descarga incompleta,
no un resultado satisfactorio.

## Actualización y reanudación

Puedes volver a ejecutar el mismo comando:

- Actualiza fichas, variantes, precios y disponibilidad.
- No vuelve a descargar una imagen con la misma identidad y URL si su archivo
  local existe y no está vacío.
- Reintenta imágenes pendientes o fallidas.
- Si cambia la URL de una imagen, guarda la nueva versión y conserva la anterior.
  En el JSON, las imágenes retiradas se identifican con `activa: false`.
- Los productos retirados del catálogo permanecen en disco, pero no aparecen
  en el índice ni CSV de la selección actual.
- Las descargas usan un archivo temporal y sustitución atómica, para no confundir
  una descarga interrumpida con una imagen completa.

No se puede detectar que un servidor haya cambiado el contenido manteniendo
exactamente la misma URL. Para forzar la descarga de esa imagen, elimina
únicamente su archivo local y vuelve a ejecutar. No se valida la integridad
de imágenes locales modificadas manualmente.

## Opciones

```powershell
py descargar.py --sin-descarga
py descargar.py --url "https://tienda.example/collections/verano"
py descargar.py --urls "otras-urls.md" --salida "D:\MiBiblioteca"
py descargar.py --todos
py descargar.py --url "https://tienda.example" --coleccion verano
py descargar.py --url "https://tienda.example/collections/verano" --json "products.json" --sin-descarga
```

`--sin-descarga` genera los datos y marca las imágenes nuevas como pendientes,
no como descargadas. `--todos` ignora únicamente el filtro de disponibilidad
en Shopify; no habilita otros filtros. `--json` permite usar un listado local
de Shopify para pruebas.

## Verificación local

```powershell
py -m unittest -v test_descargar
```

Las pruebas usan un servidor HTTP local y respuestas simuladas. No descargan
imágenes de tiendas reales ni modifican tu biblioteca.
