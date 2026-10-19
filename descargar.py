#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Biblioteca local de productos e imágenes. Uso: py descargar.py

Lee urls.md junto al script y guarda en carpetaDestino. Admite Shopify y páginas
con datos estructurados Product/ItemList de Schema.org. No elude bloqueos.
Usa --help para ver opciones. Solo requiere Python 3.10+ y su biblioteca estándar.
"""
import argparse
import csv
import hashlib
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit, urlunsplit

RAIZ = Path(__file__).resolve().parent
UA = "BibliotecaPersonal/1.0 (Python urllib)"
MAX_PAGINAS = 200
COLUMNAS = [
    "producto_id", "producto", "marca", "tipo", "coleccion", "precio_min",
    "precio_max", "moneda", "disponible", "imagen_id", "alt", "ancho", "alto",
    "variantes_imagen", "archivo_imagen", "estado", "url_producto", "url_imagen",
    "archivo_datos",
]


def validar_url(url):
    partes = urlsplit(url)
    if (partes.scheme not in ("http", "https") or not partes.hostname
            or partes.username or partes.password):
        raise ValueError(f"URL no válida (se requiere http/https sin credenciales): {url}")
    return urlunsplit((partes.scheme, partes.netloc, partes.path or "/", partes.query, ""))


def leer_urls(archivo):
    urls = []
    for linea in archivo.read_text(encoding="utf-8-sig").splitlines():
        if linea.lstrip().startswith("#"):
            continue
        for url in re.findall(r"https?://[^\s<>\]\)\"`]+", linea):
            url = validar_url(url)
            if url not in urls:
                urls.append(url)
    if not urls:
        raise ValueError(f"No hay URLs http/https en {archivo}")
    return urls


def leer(url, binario=False, reintentos=3):
    validar_url(url)
    for intento in range(reintentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as respuesta:
                datos = respuesta.read()
                if binario:
                    tipo = respuesta.headers.get_content_type()
                    if not (tipo.startswith("image/") or tipo == "application/octet-stream"):
                        raise ValueError(f"La URL no devolvió una imagen ({tipo}): {url}")
                    if not datos:
                        raise ValueError(f"Imagen vacía: {url}")
                    return datos
                return datos.decode("utf-8-sig")
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or intento == reintentos - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            if intento == reintentos - 1:
                raise
        time.sleep(1.5 * (intento + 1))
    raise RuntimeError(f"No se pudo leer {url}")


def guardar_json(archivo, datos):
    guardar_bytes(archivo, (json.dumps(datos, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def guardar_bytes(archivo, datos):
    temporal = archivo.with_name(archivo.name + ".part")
    try:
        temporal.write_bytes(datos)
        temporal.replace(archivo)
    finally:
        temporal.unlink(missing_ok=True)


def clave(valor):
    return hashlib.sha256(str(valor).encode("utf-8")).hexdigest()[:16]


def nombre(valor, identidad=None):
    limpio = re.sub(r"[^a-z0-9_-]+", "-", str(valor).lower()).strip("-_")[:45] or "sin-nombre"
    return f"{limpio}-{clave(valor if identidad is None else identidad)[:8]}"


class Pagina(HTMLParser):
    def __init__(self):
        super().__init__()
        self.bloques = []
        self.siguiente = None
        self._script = None

    def handle_starttag(self, tag, attrs):
        atributos = dict(attrs)
        if tag == "script" and atributos.get("type", "").split(";")[0].lower() == "application/ld+json":
            self._script = []
        if tag in ("link", "a") and "next" in atributos.get("rel", "").lower().split():
            self.siguiente = atributos.get("href")

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            self.bloques.append("".join(self._script))
            self._script = None


class TextoHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.partes = []

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p", "li", "div"):
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in ("p", "li", "div"):
            self.partes.append("\n")

    def handle_data(self, data):
        self.partes.append(data)


def texto_html(html):
    parser = TextoHTML()
    parser.feed(html or "")
    return "\n".join(linea.strip() for linea in "".join(parser.partes).splitlines() if linea.strip())


def recorrer_json(dato):
    if isinstance(dato, dict):
        yield dato
        for valor in dato.values():
            yield from recorrer_json(valor)
    elif isinstance(dato, list):
        for valor in dato:
            yield from recorrer_json(valor)


def es_tipo(dato, tipo):
    tipos = dato.get("@type", [])
    if isinstance(tipos, str):
        tipos = [tipos]
    return any(str(t).rstrip("/").rsplit("/", 1)[-1] == tipo for t in tipos)


def filtro_disponibilidad(url, todos=False):
    parametros = parse_qs(urlsplit(url).query, keep_blank_values=True)
    desconocidos = set(parametros) - {"filter.v.availability"}
    if desconocidos:
        raise ValueError("Parámetros sin soporte: " + ", ".join(sorted(desconocidos))
                         + ". Elimina esos parámetros o añade un adaptador; no se ignorarán.")
    valores = parametros.get("filter.v.availability")
    if valores is not None and (not valores or any(v not in ("0", "1") for v in valores)):
        raise ValueError("filter.v.availability debe valer 0 o 1")
    return None if todos or valores is None or set(valores) == {"0", "1"} else valores[0] == "1"


def producto_shopify(producto, base):
    variantes = producto.get("variants", [])
    imagenes = [{
        "id": str(imagen.get("id") or clave(imagen["src"])),
        "url": validar_url(urljoin(base, imagen["src"])),
        "alt": imagen.get("alt"), "ancho": imagen.get("width"),
        "alto": imagen.get("height"), "posicion": imagen.get("position"),
        "variantes": imagen.get("variant_ids") or [
            v["id"] for v in variantes if imagen.get("id") is not None and v.get("id") is not None and
            (v.get("image_id") == imagen.get("id") or
             isinstance(v.get("featured_image"), dict) and
             v["featured_image"].get("id") == imagen.get("id"))
        ],
    } for imagen in producto.get("images", [])]
    precios = [str(v["price"]) for v in variantes if v.get("price") is not None]
    return {
        "id": str(producto.get("id") or producto["handle"]),
        "nombre": producto.get("title", ""), "marca": producto.get("vendor"),
        "tipo": producto.get("product_type"), "etiquetas": producto.get("tags", []),
        "descripcion": texto_html(producto.get("body_html")),
        "descripcion_html": producto.get("body_html"),
        "url": f"{base}/products/{producto['handle']}",
        "moneda": None, "precios": precios,
        "disponible": (True if any(v.get("available") is True for v in variantes) else
                       False if variantes and all(v.get("available") is False for v in variantes) else None),
        "variantes": variantes, "imagenes": imagenes,
        "adaptador": "shopify", "original": producto,
    }


def productos_shopify(url):
    partes = urlsplit(url)
    ruta = partes.path.rstrip("/")
    match = re.fullmatch(r"(.*?)/collections/([^/]+)(?:/products/([^/]+))?", ruta)
    producto_match = re.fullmatch(r"(.*?)/products/([^/]+)", ruta)
    if match:
        prefijo, coleccion, producto = match.groups()
    elif producto_match:
        prefijo, producto = producto_match.groups()
        coleccion = None
    elif ruta in ("", "/") or re.fullmatch(r"/[a-z]{2}(?:-[a-z]{2})?", ruta):
        prefijo, coleccion, producto = ruta, None, None
    else:
        return None
    base = f"{partes.scheme}://{partes.netloc}{prefijo}"
    endpoint = (f"{base}/products/{producto}.json" if producto else
                f"{base}/collections/{coleccion}/products.json" if coleccion else
                f"{base}/products.json")
    resultado, vistos = [], set()
    for pagina in range(1, MAX_PAGINAS + 1):
        direccion = endpoint if producto else endpoint + "?" + urlencode({"limit": 250, "page": pagina})
        try:
            datos = json.loads(leer(direccion))
        except urllib.error.HTTPError as error:
            if pagina == 1 and error.code == 404:
                return None
            raise
        except json.JSONDecodeError:
            if pagina == 1:
                return None
            raise
        campo = "product" if producto else "products"
        if not isinstance(datos, dict) or campo not in datos:
            raise ValueError(f"Respuesta inesperada en {direccion}: falta {campo}")
        lote = [datos[campo]] if producto else datos[campo]
        if not isinstance(lote, list) or any(not isinstance(p, dict) or "id" not in p for p in lote):
            raise ValueError(f"Lista de productos inválida en {direccion}")
        nuevos = [p for p in lote if p["id"] not in vistos]
        if lote and not nuevos:
            raise ValueError(f"La paginación se repite en {direccion}; descarga incompleta")
        resultado.extend(producto_shopify(p, base) for p in nuevos)
        vistos.update(p["id"] for p in nuevos)
        if producto or not lote:
            break
        time.sleep(0.2)
    else:
        raise ValueError(f"Se alcanzó el límite de {MAX_PAGINAS} páginas; descarga incompleta")
    return coleccion or "productos", resultado


def producto_schema(dato, url):
    origen = validar_url(urljoin(url, dato.get("url") or url))
    imagenes = dato.get("image", [])
    if not isinstance(imagenes, list):
        imagenes = [imagenes]
    fotos = []
    for imagen in imagenes:
        src = imagen.get("contentUrl") or imagen.get("url") if isinstance(imagen, dict) else imagen
        if not isinstance(src, str) or not src:
            raise ValueError(f"Imagen estructurada sin URL en {origen}")
        src = validar_url(urljoin(url, src))
        fotos.append({
            "id": clave(src), "url": src,
            "alt": imagen.get("caption") if isinstance(imagen, dict) else None,
            "ancho": imagen.get("width") if isinstance(imagen, dict) else None,
            "alto": imagen.get("height") if isinstance(imagen, dict) else None,
            "posicion": len(fotos) + 1, "variantes": [],
        })
    ofertas = dato.get("offers") or []
    if isinstance(ofertas, dict):
        ofertas = [ofertas]
    ofertas_completas = [nodo for oferta in ofertas for nodo in recorrer_json(oferta)
                         if es_tipo(nodo, "Offer") or es_tipo(nodo, "AggregateOffer")]
    precios, monedas, estados = [], set(), []
    for oferta in ofertas_completas:
        for campo in ("price", "lowPrice", "highPrice"):
            if oferta.get(campo) is not None:
                precios.append(str(oferta[campo]))
        if oferta.get("priceCurrency"):
            monedas.add(oferta["priceCurrency"])
        estado = str(oferta.get("availability", "")).rsplit("/", 1)[-1]
        if estado in ("InStock", "LimitedAvailability", "OnlineOnly", "PreOrder", "PreSale"):
            estados.append(True)
        elif estado in ("OutOfStock", "SoldOut", "Discontinued"):
            estados.append(False)
    if len(monedas) > 1:
        raise ValueError(f"Ofertas en varias monedas en {origen}; se necesita un adaptador")
    marca = dato.get("brand") or dato.get("manufacturer")
    if isinstance(marca, dict):
        marca = marca.get("name")
    return {
        "id": origen,
        "nombre": dato.get("name", ""), "marca": marca, "tipo": dato.get("category"),
        "etiquetas": dato.get("keywords", []),
        "descripcion": texto_html(dato.get("description")),
        "descripcion_html": dato.get("description"), "url": origen,
        "moneda": next(iter(monedas), None), "precios": precios,
        "disponible": True if any(estados) else False if estados and len(estados) == len(ofertas_completas) else None,
        "variantes": ofertas, "imagenes": fotos,
        "adaptador": "schema.org", "original": dato,
    }


def productos_schema(url):
    pendientes, visitadas, productos = [url], set(), {}
    while pendientes:
        actual = pendientes.pop(0)
        if actual in visitadas:
            continue
        if len(visitadas) >= MAX_PAGINAS:
            raise ValueError(f"Se alcanzó el límite de {MAX_PAGINAS} páginas; descarga incompleta")
        visitadas.add(actual)
        pagina = Pagina()
        pagina.feed(leer(actual))
        encontrados, enlaces = [], []
        for bloque in pagina.bloques:
            for nodo in recorrer_json(json.loads(bloque)):
                if es_tipo(nodo, "Product"):
                    encontrados.append(nodo)
                if es_tipo(nodo, "ItemList"):
                    elementos = nodo.get("itemListElement", [])
                    if not isinstance(elementos, list):
                        elementos = [elementos]
                    for elemento in elementos:
                        item = elemento.get("item", elemento) if isinstance(elemento, dict) else elemento
                        enlace = item.get("url") or item.get("@id") if isinstance(item, dict) else item
                        if isinstance(enlace, str):
                            enlaces.append(urljoin(actual, enlace))
        for dato in encontrados:
            producto = producto_schema(dato, actual)
            productos[producto["url"]] = producto
        if not encontrados and not enlaces:
            raise ValueError(f"Sin datos públicos Product/ItemList en {actual}. Hace falta un adaptador "
                             "para esta web; no se extraerán imágenes sin datos de producto.")
        for enlace in enlaces + ([urljoin(actual, pagina.siguiente)] if pagina.siguiente else []):
            enlace = validar_url(enlace)
            if urlsplit(enlace).netloc != urlsplit(url).netloc:
                raise ValueError(f"Enlace de catálogo a otro dominio: {enlace}")
            if enlace not in visitadas and enlace not in pendientes:
                pendientes.append(enlace)
    return unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]) or "productos", list(productos.values())


def obtener_productos(url, todos=False, archivo_json=None):
    filtro = filtro_disponibilidad(url, todos)
    if archivo_json:
        datos = json.loads(archivo_json.read_text(encoding="utf-8-sig"))
        partes = urlsplit(url)
        base = f"{partes.scheme}://{partes.netloc}"
        coleccion = partes.path.rstrip("/").rsplit("/", 1)[-1] or "productos"
        productos = [producto_shopify(p, base) for p in datos["products"]]
    else:
        resultado = productos_shopify(url)
        if resultado is None:
            if urlsplit(url).query:
                raise ValueError("Los filtros solo están implementados para Shopify, no para esta web")
            resultado = productos_schema(url)
        coleccion, productos = resultado
    if filtro is not None:
        if any(p["disponible"] is None for p in productos):
            raise ValueError("No se puede aplicar el filtro: hay productos sin disponibilidad verificable")
        productos = [p for p in productos if p["disponible"] == filtro]
    return coleccion, productos


def extension_imagen(url):
    extension = Path(unquote(urlsplit(url).path)).suffix.lower()
    return extension if extension in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif",
                                      ".svg", ".bmp", ".tif", ".tiff", ".ico") else ".img"


def resumen_producto(producto):
    try:
        precios = [Decimal(p) for p in producto["precios"]]
    except InvalidOperation as error:
        raise ValueError(f"Precio no válido en {producto['url']}") from error
    if any(not precio.is_finite() for precio in precios):
        raise ValueError(f"Precio no finito en {producto['url']}")
    return {
        "producto_id": producto["id"], "producto": producto["nombre"],
        "marca": producto["marca"], "tipo": producto["tipo"],
        "precio_min": str(min(precios)) if precios else None,
        "precio_max": str(max(precios)) if precios else None,
        "moneda": producto["moneda"], "disponible": producto["disponible"],
        "url_producto": producto["url"],
    }


def texto_ficha(producto, imagen=None):
    datos = resumen_producto(producto)
    partes = [f"{campo}: {valor if valor is not None else 'no indicado'}" for campo, valor in datos.items()]
    partes.extend(["", "Descripción:", producto["descripcion"], "", "Variantes:",
                   json.dumps(producto["variantes"], ensure_ascii=False, indent=2)])
    if imagen:
        partes.extend(["", "Imagen:", json.dumps(imagen, ensure_ascii=False, indent=2)])
    partes.extend(["", "Datos originales y asociación de imágenes: producto.json",
                   "Los derechos de uso no se transfieren al descargar."])
    return "\n".join(partes) + "\n"


def exportar_producto(producto, carpeta, sin_descarga, errores, cache):
    carpeta.mkdir(parents=True, exist_ok=True)
    anterior = carpeta / "producto.json"
    historicas = []
    if anterior.exists():
        historicas = json.loads(anterior.read_text(encoding="utf-8")).get("imagenes", [])
    imagenes = []
    for fuente in producto["imagenes"]:
        imagen = dict(fuente)
        # La URL forma parte de la identidad: un cambio de versión conserva la foto anterior.
        identificador = f"img_{clave(imagen['id'])}_{clave(imagen['url'])}"
        archivo = carpeta / (identificador + extension_imagen(imagen["url"]))
        imagen.update(archivo=archivo.name, activa=True, estado="pendiente", error=None)
        try:
            if archivo.exists() and archivo.stat().st_size:
                imagen["estado"] = "descargada"
            elif not sin_descarga:
                datos = cache.get(imagen["url"])
                if datos is None:
                    datos = leer(imagen["url"], binario=True)
                guardar_bytes(archivo, datos)
                # Solo se cachea la última imagen: no cargar toda la biblioteca en RAM.
                cache.clear()
                cache[imagen["url"]] = datos
                imagen["estado"] = "descargada"
        except (OSError, ValueError, http.client.HTTPException) as error:
            imagen["estado"], imagen["error"] = "error", str(error)
            errores.append(f"{producto['url']} | {imagen['url']} | {error}")
        imagenes.append(imagen)
        (carpeta / (identificador + ".txt")).write_text(texto_ficha(producto, imagen), encoding="utf-8")
    actuales = {imagen["archivo"] for imagen in imagenes}
    for imagen in historicas:
        if imagen["archivo"] not in actuales:
            imagenes.append(dict(imagen, activa=False))
    producto = dict(producto, imagenes=imagenes, actualizado_en=datetime.now(timezone.utc).isoformat())
    guardar_json(anterior, producto)
    (carpeta / "datos.txt").write_text(texto_ficha(producto), encoding="utf-8")
    if not any(imagen["activa"] for imagen in producto["imagenes"]):
        errores.append(f"{producto['url']} | Producto sin imágenes públicas")
    return producto


def guardar_csv(archivo, filas):
    temporal = archivo.with_name(archivo.name + ".part")
    try:
        with temporal.open("w", newline="", encoding="utf-8-sig") as salida:
            writer = csv.DictWriter(salida, fieldnames=COLUMNAS, delimiter=";")
            writer.writeheader()
            for fila in filas:
                # Evita que textos externos se interpreten como fórmulas en Excel.
                writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@"))
                                 else v for k, v in fila.items()})
        temporal.replace(archivo)
    finally:
        temporal.unlink(missing_ok=True)


def procesar_url(url, destino, sin_descarga=False, todos=False, archivo_json=None):
    coleccion, productos = obtener_productos(url, todos, archivo_json)
    tienda = destino / nombre(urlsplit(url).netloc)
    # Los filtros distintos son selecciones distintas, sin pisarse al ejecutar el lote.
    carpeta = tienda / nombre(coleccion, url)
    carpeta.mkdir(parents=True, exist_ok=True)
    errores, filas, cache, rutas = [], [], {}, []
    for producto in productos:
        ruta = carpeta / ("producto_" + clave(producto["id"]))
        try:
            resumen_producto(producto)
            exportado = exportar_producto(producto, ruta, sin_descarga, errores, cache)
            rutas.append(str((ruta / "producto.json").relative_to(carpeta)))
            for imagen in exportado["imagenes"]:
                if not imagen["activa"]:
                    continue
                filas.append(dict(
                    resumen_producto(exportado), coleccion=coleccion,
                    imagen_id=imagen["id"], alt=imagen["alt"], ancho=imagen["ancho"],
                    alto=imagen["alto"], variantes_imagen=json.dumps(imagen["variantes"]),
                    archivo_imagen=str((ruta / imagen["archivo"]).relative_to(carpeta)),
                    estado=imagen["estado"], url_imagen=imagen["url"],
                    archivo_datos=str((ruta / "producto.json").relative_to(carpeta)),
                ))
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException) as error:
            errores.append(f"{producto.get('url', url)} | {error}")
    guardar_csv(carpeta / "catalogo.csv", filas)
    guardar_json(carpeta / "coleccion.json", {
        "url_origen": url, "coleccion": coleccion,
        "ignorar_filtro_disponibilidad": todos,
        "filtro_disponibilidad": filtro_disponibilidad(url, todos),
        "actualizado_en": datetime.now(timezone.utc).isoformat(),
        "productos": rutas, "total_productos": len(productos),
        "total_imagenes": len(filas), "sin_descarga": sin_descarga,
        "errores": errores,
        "aviso": "Los archivos antiguos se conservan. Los precios y la disponibilidad son una instantánea. "
                 "Una moneda nula significa que la fuente no la indica; no se presupone BRL.",
    })
    faltan_monedas = sum(p["moneda"] is None for p in productos)
    if faltan_monedas:
        print(f"  AVISO: {faltan_monedas} productos sin moneda verificable; se guarda null.")
    if errores:
        (carpeta / "errores.txt").write_text("\n".join(errores) + "\n", encoding="utf-8")
    else:
        (carpeta / "errores.txt").unlink(missing_ok=True)
    print(f"  {len(productos)} productos, {len(filas)} imágenes registradas, "
          f"{len(errores)} errores. Destino: {carpeta}")
    return errores


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--urls", type=Path, default=RAIZ / "urls.md", help="archivo de URLs")
    parser.add_argument("--url", help="procesar solo esta URL en vez de urls.md")
    parser.add_argument("--coleccion", help="handle Shopify, requiere --url")
    parser.add_argument("--salida", type=Path, default=RAIZ / "carpetaDestino", help="carpeta de destino")
    parser.add_argument("--todos", action="store_true", help="ignorar solo el filtro de disponibilidad")
    parser.add_argument("--json", type=Path, help="products.json local; requiere --url")
    parser.add_argument("--sin-descarga", action="store_true", help="crear datos sin descargar imágenes")
    args = parser.parse_args(argv)
    if (args.coleccion or args.json) and not args.url:
        parser.error("--coleccion y --json requieren --url")
    try:
        urls = [validar_url(args.url)] if args.url else leer_urls(args.urls)
        if args.coleccion:
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", args.coleccion):
                parser.error("--coleccion debe ser un handle, no una URL")
            base = urlsplit(urls[0])
            if base.query or not re.fullmatch(r"/?(?:[a-z]{2}(?:-[a-z]{2})?/?)?", base.path):
                parser.error("--coleccion requiere --url con la raíz de la tienda o su prefijo de idioma")
            urls = [urls[0].rstrip("/") + "/collections/" + args.coleccion]
        args.salida.mkdir(parents=True, exist_ok=True)
        errores = []
        for url in urls:
            print(f"Procesando {url}")
            try:
                errores.extend(procesar_url(url, args.salida, args.sin_descarga, args.todos, args.json))
            except (OSError, ValueError, KeyError, TypeError) as error:
                mensaje = f"{url} | {error}"
                errores.append(mensaje)
                print(f"  ERROR: {mensaje}", file=sys.stderr)
        registro = args.salida / "errores.txt"
        if errores:
            registro.write_text("\n".join(errores) + "\n", encoding="utf-8")
            print(f"Terminado con {len(errores)} errores. Revisa {registro}", file=sys.stderr)
            return 1
        registro.unlink(missing_ok=True)
        print("Datos guardados sin descargar imágenes." if args.sin_descarga else "Descarga completada.")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
