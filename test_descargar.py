import copy
import csv
import io
import json
import tempfile
import threading
import unittest
import urllib.error
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import descargar as app


def producto(numero=1, disponible=True):
    return {
        "id": numero, "handle": f"prenda-{numero}", "title": "Prenda real",
        "body_html": "<p>Algodón</p><p>Medidas originales</p>",
        "vendor": "Marca", "product_type": "Ropa", "tags": ["verano"],
        "variants": [{"id": numero * 10, "title": "M", "available": disponible,
                      "price": "12.30", "compare_at_price": "15.00",
                      "sku": f"SKU-{numero}", "featured_image": {"id": numero * 100}}],
        "images": [{"id": numero * 100, "position": 1, "src": f"/foto-{numero}.png",
                    "width": 100, "height": 200, "alt": "Vista frontal"}],
    }


class BibliotecaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.peticiones = Counter()
        cls.catalogo = [producto(), producto(2, False)]
        cls.fallar_imagen = False
        cls.contenido_imagen = b"\x89PNG\r\n\x1a\nimagen-de-prueba"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.peticiones[self.path] += 1
                if self.path.startswith("/collections/ropa/products.json?"):
                    datos = {"products": [] if "page=2" in self.path else cls.catalogo}
                    contenido = json.dumps(datos).encode("utf-8")
                    tipo, estado = "application/json", 200
                elif self.path.startswith("/foto-"):
                    contenido = cls.contenido_imagen
                    tipo, estado = "image/png", 404 if cls.fallar_imagen else 200
                elif self.path in ("/sin-datos", "/no-imagen"):
                    contenido, tipo, estado = b"<html>Sin datos estructurados</html>", "text/html", 200
                else:
                    contenido, tipo, estado = b"no encontrado", "text/html", 404
                self.send_response(estado)
                self.send_header("Content-Type", tipo)
                self.end_headers()
                self.wfile.write(contenido)

            def log_message(self, formato, *args):
                pass

        cls.servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.hilo = threading.Thread(target=cls.servidor.serve_forever, daemon=True)
        cls.hilo.start()
        cls.base = f"http://127.0.0.1:{cls.servidor.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.servidor.server_close()
        cls.hilo.join()

    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporal.cleanup)
        self.raiz = Path(self.temporal.name)
        self.destino = self.raiz / "salida"
        self.urls = self.raiz / "urls.md"
        self.urls.write_text(self.base + "/collections/ropa", encoding="utf-8")
        type(self).catalogo = [producto(), producto(2, False)]
        type(self).fallar_imagen = False
        self.peticiones.clear()

    def ejecutar(self, *args):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), patch.object(app.time, "sleep"):
            return app.main(["--urls", str(self.urls), "--salida", str(self.destino), *args])

    def fichas(self):
        return sorted(self.destino.rglob("producto.json"))

    def test_descarga_real_local_y_asociacion(self):
        self.assertEqual(self.ejecutar(), 0)
        self.assertEqual(len(self.fichas()), 2)
        for archivo in self.fichas():
            datos = json.loads(archivo.read_text(encoding="utf-8"))
            imagen = datos["imagenes"][0]
            self.assertEqual((archivo.parent / imagen["archivo"]).read_bytes(), self.contenido_imagen)
            self.assertEqual(imagen["variantes"], [int(datos["id"]) * 10])
            self.assertEqual(datos["original"]["title"], datos["nombre"])
            self.assertIsNone(datos["moneda"])
            self.assertEqual(imagen["estado"], "descargada")
            self.assertTrue((archivo.parent / "datos.txt").exists())
            self.assertTrue((archivo.parent / Path(imagen["archivo"]).with_suffix(".txt")).exists())
        with next(self.destino.rglob("catalogo.csv")).open(encoding="utf-8-sig", newline="") as archivo:
            filas = list(csv.DictReader(archivo, delimiter=";"))
        self.assertEqual(len(filas), 2)
        self.assertEqual(filas[0]["precio_min"], "12.30")
        self.assertNotEqual(filas[0]["archivo_imagen"], filas[1]["archivo_imagen"])
        self.assertFalse(list(self.destino.rglob("*.part")))

    def test_reordenar_no_cambia_identidad_ni_redescarga(self):
        self.assertEqual(self.ejecutar(), 0)
        antes = {p: json.loads(p.read_text(encoding="utf-8"))["imagenes"][0]["archivo"] for p in self.fichas()}
        type(self).catalogo.reverse()
        type(self).catalogo[0]["title"] = "Nombre actualizado"
        self.assertEqual(self.ejecutar(), 0)
        despues = {p: json.loads(p.read_text(encoding="utf-8"))["imagenes"][0]["archivo"] for p in self.fichas()}
        self.assertEqual(antes, despues)
        self.assertEqual(self.peticiones["/foto-1.png"], 1)
        self.assertEqual(self.peticiones["/foto-2.png"], 1)
        self.assertTrue(any(json.loads(p.read_text(encoding="utf-8"))["nombre"] == "Nombre actualizado"
                            for p in self.fichas()))

    def test_version_nueva_conserva_anterior(self):
        self.assertEqual(self.ejecutar(), 0)
        type(self).catalogo[0]["images"][0]["src"] += "?v=2"
        self.assertEqual(self.ejecutar(), 0)
        datos = next(json.loads(p.read_text(encoding="utf-8")) for p in self.fichas()
                     if json.loads(p.read_text(encoding="utf-8"))["id"] == "1")
        self.assertEqual(len(datos["imagenes"]), 2)
        self.assertEqual(sum(im["activa"] for im in datos["imagenes"]), 1)
        self.assertEqual(len(list(self.destino.rglob("*.png"))), 3)

    def test_filtros_disponibilidad_y_todos(self):
        self.urls.write_text(self.base + "/collections/ropa?filter.v.availability=1", encoding="utf-8")
        self.assertEqual(self.ejecutar(), 0)
        self.assertEqual(len(self.fichas()), 1)
        self.assertEqual(self.ejecutar("--todos"), 0)
        self.assertEqual(len(self.fichas()), 2)
        self.assertFalse(app.filtro_disponibilidad("https://ejemplo.test/?filter.v.availability=0"))
        self.assertIsNone(app.filtro_disponibilidad("https://ejemplo.test/"))

    def test_sin_descarga_y_reanudacion(self):
        self.assertEqual(self.ejecutar("--sin-descarga"), 0)
        self.assertFalse(list(self.destino.rglob("*.png")))
        self.assertEqual(json.loads(self.fichas()[0].read_text(encoding="utf-8"))["imagenes"][0]["estado"], "pendiente")
        self.assertEqual(self.ejecutar(), 0)
        self.assertEqual(len(list(self.destino.rglob("*.png"))), 2)

    def test_error_imagen_se_registra_y_se_recupera(self):
        type(self).fallar_imagen = True
        self.assertEqual(self.ejecutar(), 1)
        self.assertTrue((self.destino / "errores.txt").exists())
        self.assertEqual(json.loads(self.fichas()[0].read_text(encoding="utf-8"))["imagenes"][0]["estado"], "error")
        type(self).fallar_imagen = False
        self.assertEqual(self.ejecutar(), 0)
        self.assertFalse(list(self.destino.rglob("errores.txt")))

    def test_continua_tras_url_no_soportada(self):
        self.urls.write_text(self.base + "/sin-datos\n" + self.base + "/collections/ropa", encoding="utf-8")
        self.assertEqual(self.ejecutar(), 1)
        self.assertEqual(len(self.fichas()), 2)
        self.assertIn("Sin datos públicos", (self.destino / "errores.txt").read_text(encoding="utf-8"))

    def test_filtro_no_soportado_no_descarga(self):
        self.urls.write_text(self.base + "/collections/ropa?filter.p.vendor=Marca", encoding="utf-8")
        self.assertEqual(self.ejecutar(), 1)
        self.assertFalse(self.peticiones)
        with self.assertRaises(ValueError):
            app.filtro_disponibilidad("https://ejemplo.test/?filter.v.availability=incorrecto")

    def test_markdown_duplicados_y_comentarios(self):
        self.urls.write_text("# Comentario\n[colección](https://ejemplo.test/collections/ropa)\n"
                             "https://ejemplo.test/collections/ropa\n\nhttps://otra.test/products/prenda\n",
                             encoding="utf-8-sig")
        self.assertEqual(app.leer_urls(self.urls), [
            "https://ejemplo.test/collections/ropa", "https://otra.test/products/prenda"])

    def test_urls_invalidas_y_archivo_vacio(self):
        for url in ("file:///C:/secreto", "https://usuario:clave@ejemplo.test"):
            with self.assertRaises(ValueError):
                app.validar_url(url)
        self.urls.write_text("", encoding="utf-8")
        self.assertEqual(self.ejecutar(), 1)

    def test_producto_individual_shopify(self):
        with patch.object(app, "leer", return_value=json.dumps({"product": producto()})) as leer:
            coleccion, productos = app.obtener_productos("https://tienda.test/products/prenda-1")
        self.assertEqual(coleccion, "productos")
        self.assertEqual(productos[0]["id"], "1")
        leer.assert_called_once_with("https://tienda.test/products/prenda-1.json")

    def test_tienda_entera_y_prefijo_idioma(self):
        with patch.object(app, "leer", side_effect=[json.dumps({"products": [producto()]}),
                                                   '{"products": []}']) as leer:
            _, productos = app.obtener_productos("https://tienda.test/es")
        self.assertEqual(len(productos), 1)
        self.assertIn("/es/products.json", leer.call_args_list[0].args[0])

    def test_paginacion_repetida_no_se_declara_completa(self):
        with patch.object(app, "leer", return_value=json.dumps({"products": [producto()]})), \
                patch.object(app.time, "sleep"):
            with self.assertRaisesRegex(ValueError, "paginación se repite"):
                app.obtener_productos("https://tienda.test")

    def test_precio_invalido_no_aborta_otros_productos(self):
        type(self).catalogo[0]["variants"][0]["price"] = "no es un precio"
        self.assertEqual(self.ejecutar(), 1)
        self.assertEqual(len(self.fichas()), 1)
        self.assertIn("Precio no válido", (self.destino / "errores.txt").read_text(encoding="utf-8"))

    def test_producto_sin_imagen_registra_error(self):
        type(self).catalogo[0]["images"] = []
        self.assertEqual(self.ejecutar(), 1)
        self.assertEqual(len(self.fichas()), 2)
        self.assertIn("sin imágenes", (self.destino / "errores.txt").read_text(encoding="utf-8"))

    def test_json_local_no_accede_a_red_sin_descarga(self):
        archivo = self.raiz / "products.json"
        archivo.write_text(json.dumps({"products": [producto()]}), encoding="utf-8")
        with patch.object(app, "leer", side_effect=AssertionError("No acceder a red")):
            self.assertEqual(self.ejecutar("--url", "https://tienda.test/collections/ropa",
                                          "--json", str(archivo), "--sin-descarga"), 0)

    def test_exportacion_csv_no_ejecuta_formulas(self):
        archivo = self.raiz / "catalogo.csv"
        app.guardar_csv(archivo, [{"producto": "=HYPERLINK(\"malicioso\")"}])
        with archivo.open(encoding="utf-8-sig", newline="") as entrada:
            self.assertTrue(next(csv.DictReader(entrada, delimiter=";"))["producto"].startswith("'="))

    def test_no_guarda_html_como_imagen(self):
        with self.assertRaisesRegex(ValueError, "no devolvió una imagen"):
            app.leer(self.base + "/no-imagen", binario=True)

    def test_descarga_atomica_conserva_archivo_previo_si_falla(self):
        archivo = self.raiz / "foto.png"
        archivo.write_bytes(b"anterior")
        with patch.object(Path, "replace", side_effect=OSError("fallo de disco")):
            with self.assertRaises(OSError):
                app.guardar_bytes(archivo, b"nuevo")
        self.assertEqual(archivo.read_bytes(), b"anterior")
        self.assertFalse(archivo.with_name("foto.png.part").exists())

    def test_reintenta_http_transitorio_pero_no_404(self):
        url = "https://tienda.test/products.json"
        transitorio = urllib.error.HTTPError(url, 503, "ocupado", None, None)
        permanente = urllib.error.HTTPError(url, 404, "no existe", None, None)
        with patch.object(app.urllib.request, "urlopen", side_effect=[transitorio, permanente]) as abrir, \
                patch.object(app.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError) as resultado:
                app.leer(url)
        self.assertEqual(resultado.exception.code, 404)
        self.assertEqual(abrir.call_count, 2)

    def test_imagen_sin_id_no_se_asocia_a_variantes_sin_imagen(self):
        dato = producto()
        dato["images"][0].pop("id")
        dato["variants"][0].pop("featured_image")
        normalizado = app.producto_shopify(dato, "https://tienda.test")
        self.assertEqual(normalizado["imagenes"][0]["variantes"], [])

    def test_disponibilidad_ausente_no_se_inventa_como_agotado(self):
        type(self).catalogo[0]["variants"][0].pop("available")
        normalizado = app.producto_shopify(type(self).catalogo[0], self.base)
        self.assertIsNone(normalizado["disponible"])
        self.urls.write_text(self.base + "/collections/ropa?filter.v.availability=1", encoding="utf-8")
        self.assertEqual(self.ejecutar(), 1)
        self.assertFalse(self.fichas())
        self.assertIn("sin disponibilidad verificable",
                      (self.destino / "errores.txt").read_text(encoding="utf-8"))

    def test_colecciones_mismo_nombre_distinta_ruta_no_colisionan(self):
        self.assertNotEqual(app.nombre("ropa", "https://tienda.test/es/collections/ropa"),
                            app.nombre("ropa", "https://tienda.test/pt/collections/ropa"))


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.producto = {
            "@type": "Product", "name": "Bolso", "sku": "B1",
            "url": "/articulo/bolso", "brand": {"name": "Marca"},
            "image": [{"url": "/foto.jpg", "caption": "Detalle"}],
            "description": "<p>Descripción original</p>",
            "offers": {"@type": "Offer", "price": "25.50", "priceCurrency": "EUR",
                       "availability": "https://schema.org/InStock"},
        }

    def html(self, datos, siguiente=""):
        return '<script type="application/ld+json">' + json.dumps(datos) + "</script>" + siguiente

    def test_producto_schema_preserva_datos(self):
        with patch.object(app, "leer", return_value=self.html({"@graph": [self.producto]})):
            _, productos = app.obtener_productos("https://tienda.test/articulo/bolso")
        producto = productos[0]
        self.assertEqual(producto["moneda"], "EUR")
        self.assertEqual(producto["precios"], ["25.50"])
        self.assertTrue(producto["disponible"])
        self.assertEqual(producto["imagenes"][0]["url"], "https://tienda.test/foto.jpg")
        self.assertEqual(producto["original"], self.producto)

    def test_coleccion_schema_y_paginacion(self):
        lista = {"@type": "ItemList", "itemListElement": [
            {"@type": "ListItem", "item": {"url": "/articulo/bolso"}}]}
        segundo = copy.deepcopy(self.producto)
        segundo.update(sku="B2", url="/articulo/otro")
        paginas = {
            "https://tienda.test/catalogo": self.html(lista, '<link rel="next" href="/catalogo/pagina-2">'),
            "https://tienda.test/articulo/bolso": self.html(self.producto),
            "https://tienda.test/catalogo/pagina-2": self.html(segundo),
        }
        with patch.object(app, "leer", side_effect=lambda url: paginas[url]):
            _, productos = app.obtener_productos("https://tienda.test/catalogo")
        self.assertEqual(len(productos), 2)

    def test_sin_estructura_no_inventa_datos(self):
        with patch.object(app, "leer", return_value="<img src='logo.jpg'>"):
            with self.assertRaisesRegex(ValueError, "adaptador"):
                app.obtener_productos("https://tienda.test/catalogo")

    def test_fallback_404_shopify_a_schema(self):
        error = urllib.error.HTTPError("https://tienda.test/products.json", 404, "no existe", None, None)
        with patch.object(app, "leer", side_effect=[error, self.html(self.producto)]):
            _, productos = app.obtener_productos("https://tienda.test/")
        self.assertEqual(productos[0]["adaptador"], "schema.org")

    def test_bloqueo_no_se_oculta_con_otro_adaptador(self):
        error = urllib.error.HTTPError("https://tienda.test/products.json", 403, "prohibido", None, None)
        with patch.object(app, "leer", side_effect=error) as leer:
            with self.assertRaises(urllib.error.HTTPError):
                app.obtener_productos("https://tienda.test/")
        self.assertEqual(leer.call_count, 1)

    def test_disponibilidad_desconocida_y_monedas_multiples(self):
        self.producto["offers"].pop("availability")
        self.assertIsNone(app.producto_schema(self.producto, "https://tienda.test")["disponible"])
        self.producto["offers"] = [self.producto["offers"],
                                   {"@type": "Offer", "price": "20", "priceCurrency": "USD"}]
        with self.assertRaisesRegex(ValueError, "varias monedas"):
            app.producto_schema(self.producto, "https://tienda.test")

    def test_catalogo_no_sigue_otro_dominio(self):
        lista = {"@type": "ItemList", "itemListElement": ["https://otra.test/bolso"]}
        with patch.object(app, "leer", return_value=self.html(lista)):
            with self.assertRaisesRegex(ValueError, "otro dominio"):
                app.obtener_productos("https://tienda.test/catalogo")

    def test_sku_repetido_no_colisiona_entre_productos(self):
        primero = app.producto_schema(self.producto, "https://tienda.test")
        self.producto["url"] = "/articulo/otro"
        segundo = app.producto_schema(self.producto, "https://tienda.test")
        self.assertNotEqual(primero["id"], segundo["id"])


if __name__ == "__main__":
    unittest.main()
