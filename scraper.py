"""
SantaLucia table scraper — pure Python stdlib, zero external dependencies.
Extracts rows where T.Trabajo == 'Encargo' and Fecha visita is empty.

Login flow:
  1. GET /pyp/Profesionales  -> extract CSRF tokens from hidden inputs
  2. POST /pyp/CNCEntrada    -> MotorAJAX JSON response with ControladorURLVuelta
  3. GET /pyp/<urlVuelta>    -> full HTML page with the table

Pagination:
  POST /pyp/CNCEntrada with page-change fields -> another MotorAJAX or full HTML
"""

import json
import re
import ssl
import threading
import urllib.request
import urllib.parse
import http.cookiejar
from html.parser import HTMLParser

BASE_URL        = "https://wwwssl.santalucia.es:3415"
LOGIN_URL       = BASE_URL + "/pyp/Profesionales"
POST_URL        = BASE_URL + "/pyp/CNCEntrada"
FILTER_TTRABAJO = "Encargo"


# --------------------------------------------------------------------------- #
# HTML parser — extracts hidden inputs, table headers, table rows, pagination
# --------------------------------------------------------------------------- #

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden: dict[str, str] = {}

        self._in_header_table = False
        self._in_data_table   = False
        self._in_th           = False
        self._in_td           = False
        self._skip            = False
        self._skip_tag        = ""
        self._buf             = ""
        self._cur_row: list[str] = []

        self.headers: list[str] = []
        self.rows: list[list[str]] = []

        self._in_total_rows   = False
        self.total_rows_text  = ""
        self._in_page_select  = False
        self.page_options: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)

        if tag in ("script", "style"):
            self._skip = True
            self._skip_tag = tag
            return
        if self._skip:
            return

        # Hidden inputs (skip names that contain % — they would double-encode)
        if tag == "input" and a.get("type", "").lower() == "hidden":
            name = a.get("name", "")
            val  = a.get("value", "")
            if name and "%" not in name:
                self.hidden[name] = val
            return

        # Table detection by id suffix
        tid = a.get("id", "")
        if tag == "table":
            if "_cabecera" in tid:
                self._in_header_table = True
            elif "_datos" in tid:
                self._in_data_table = True

        if self._in_header_table and tag == "th":
            self._in_th = True
            self._buf = ""

        if self._in_data_table:
            if tag == "tr":
                self._cur_row = []
            elif tag == "td":
                self._in_td = True
                self._buf = ""

        # Total rows
        if a.get("id") == "caTotalFilas":
            self._in_total_rows = True

        # Page select
        if tag == "select" and "nuPaginaSeleccionada" in a.get("name", ""):
            self._in_page_select = True
        if self._in_page_select and tag == "option":
            self._buf = ""
            self._in_td = True

    def handle_endtag(self, tag):
        if self._skip and tag == self._skip_tag:
            self._skip = False
            return
        if self._skip:
            return

        if tag == "table":
            self._in_header_table = False
            self._in_data_table   = False

        if self._in_header_table and tag == "th":
            self._in_th = False
            self.headers.append(self._buf.strip())
            self._buf = ""

        if self._in_data_table:
            if tag == "td":
                self._in_td = False
                self._cur_row.append(self._buf.strip())
                self._buf = ""
            elif tag == "tr" and self._cur_row:
                self.rows.append(self._cur_row)
                self._cur_row = []

        if self._in_total_rows and tag == "p":
            self._in_total_rows = False

        if self._in_page_select and tag == "option":
            self._in_td = False
            self.page_options.append(self._buf.strip())
            self._buf = ""
        if tag == "select":
            self._in_page_select = False

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_th or self._in_td:
            self._buf += data
        if self._in_total_rows:
            self.total_rows_text += data


# --------------------------------------------------------------------------- #
# MotorAJAX response parser
# --------------------------------------------------------------------------- #

def _parse_motor_ajax(text: str) -> dict:
    """Extract the JSON payload from a <mensaje-motor-ajax>…</mensaje-motor-ajax> wrapper."""
    m = re.search(r"<mensaje-motor-ajax>(.*?)</mensaje-motor-ajax>", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except Exception:
        return {}


def _get_redirect_url(motor_data: dict) -> str | None:
    """Return the urlVuelta from a ControladorURLVuelta task, if present."""
    for task in motor_data.get("tareas", []):
        if task.get("tipoElemento") == "ControladorURLVuelta":
            return task.get("datosElemento", {}).get("datos", {}).get("urlVuelta")
    return None


def _get_token_update(motor_data: dict) -> str | None:
    """Return the updated CNC_codigoToken from a ControladorGenerico task."""
    for task in motor_data.get("tareas", []):
        if task.get("tipoElemento") == "ControladorGenerico":
            attrs = task.get("datosElemento", {}).get("atributos", [])
            for attr in attrs:
                if attr.get("nombre") == "value":
                    return attr.get("valor")
    return None


def _get_zone_html(motor_data: dict) -> str:
    """Collect any zone HTML fragments returned in a pagination AJAX response."""
    parts = []
    for task in motor_data.get("tareas", []):
        datos = task.get("datosElemento", {}).get("datos", {})
        html_fragment = datos.get("deContenidoZona") or datos.get("html") or ""
        if html_fragment:
            parts.append(html_fragment)
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# HTTP session helpers
# --------------------------------------------------------------------------- #

def _make_opener() -> urllib.request.OpenerDirector:
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl_ctx),
        urllib.request.HTTPCookieProcessor(jar),
    )
    opener.addheaders = [
        ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"),
        ("Accept", "text/html,application/xhtml+xml,*/*"),
        ("Accept-Language", "es-ES,es;q=0.9"),
        ("Accept-Encoding", "identity"),
    ]
    return opener


def _get(opener, url: str) -> str:
    with opener.open(url, timeout=30) as r:
        raw = r.read()
        ct = r.headers.get("Content-Type", "")
        charset = "latin-1"
        for p in ct.split(";"):
            p = p.strip()
            if p.lower().startswith("charset="):
                charset = p.split("=", 1)[1].strip()
        return raw.decode(charset, errors="replace")


def _post(opener, url: str, fields: dict, referer: str = "") -> str:
    # Use urllib.parse.urlencode so that literal '%' in keys/values is encoded to %25.
    # This matches how a real browser submits form fields whose HTML names contain '%'.
    body = urllib.parse.urlencode(fields)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, data=body.encode("ascii"), headers=headers)
    with opener.open(req, timeout=30) as r:
        raw = r.read()
        ct = r.headers.get("Content-Type", "")
        charset = "latin-1"
        for p in ct.split(";"):
            p = p.strip()
            if p.lower().startswith("charset="):
                charset = p.split("=", 1)[1].strip()
        return raw.decode(charset, errors="replace")


def _col_index(headers: list[str], *candidates: str) -> int:
    for cand in candidates:
        for i, h in enumerate(headers):
            if cand.lower() in h.lower():
                return i
    return -1


# --------------------------------------------------------------------------- #
# Main scrape function
# --------------------------------------------------------------------------- #

def scrape(username: str, password: str,
           on_progress,
           on_row,
           stop_event: threading.Event) -> int:

    opener = _make_opener()

    # ------------------------------------------------------------------ #
    # 1. GET login page — extract CSRF tokens
    # ------------------------------------------------------------------ #
    on_progress("Cargando pagina de login...")
    try:
        login_html = _get(opener, LOGIN_URL)
    except Exception as e:
        on_progress(f"ERROR al conectar: {e}")
        return 0

    pp0 = PageParser()
    pp0.feed(login_html)
    hidden = pp0.hidden

    if "CNC_codigoToken" not in hidden or "CNC_identificacionPN" not in hidden:
        on_progress(f"ERROR: No se encontraron tokens. Campos: {list(hidden)}")
        return 0

    on_progress("Tokens extraidos. Iniciando sesion...")

    # ------------------------------------------------------------------ #
    # 2. POST login
    # Note: field names that contain '%' in the HTML (e.g. 'deContrase%F1a')
    #       must be passed as literal strings so urlencode encodes % -> %25,
    #       matching real browser form submission behaviour.
    # ------------------------------------------------------------------ #
    login_fields = {
        **hidden,
        "nuDocumentoIdentificacion": username,
        "deContrase%F1a": password,        # literal name from HTML attribute
        "CNC_codigoEvento": "entrar[0]",
        "inTipoPeticionAJAX": "true",
        "coEvento_entrar_0": "Entrar",
    }

    try:
        login_resp = _post(opener, POST_URL, login_fields, referer=LOGIN_URL)
    except Exception as e:
        on_progress(f"ERROR en login POST: {e}")
        return 0

    motor = _parse_motor_ajax(login_resp)
    if not motor:
        # Might be a plain HTML error page
        if any(w in login_resp.lower() for w in ["contraseña incorrecta", "error de acceso"]):
            on_progress("ERROR: Login fallido.")
            return 0

    # Check for validation errors in MotorAJAX response
    for task in motor.get("tareas", []):
        if task.get("tipoElemento") == "ControladorMensajesValidacion":
            msgs = task.get("datosElemento", {}).get("datos", {}).get("liMensajesErrores", [])
            if msgs:
                on_progress(f"ERROR de login: {msgs}")
                return 0

    redirect = _get_redirect_url(motor)
    if not redirect:
        on_progress("ERROR: No se recibio URL de redireccion tras el login.")
        return 0

    on_progress(f"Login correcto. Cargando pagina principal...")

    # ------------------------------------------------------------------ #
    # 3. GET the authenticated page
    # ------------------------------------------------------------------ #
    page_url = BASE_URL + "/pyp/" + redirect
    try:
        page_html = _get(opener, page_url)
    except Exception as e:
        on_progress(f"ERROR al cargar pagina principal: {e}")
        return 0

    # ------------------------------------------------------------------ #
    # 4. Parse table
    # ------------------------------------------------------------------ #
    def parse(html: str) -> PageParser:
        pp = PageParser()
        pp.feed(html)
        return pp

    pp1 = parse(page_html)

    if not pp1.headers:
        on_progress("ERROR: No se encontraron columnas en la tabla.")
        return 0

    on_progress(f"Columnas: {pp1.headers}")

    idx_t = _col_index(pp1.headers, "t. trabajo", "ttrabajo", "trabajo")
    idx_f = _col_index(pp1.headers, "fecha visita", "fecha_visita", "fechavisita", "visita")

    if idx_t < 0:
        on_progress("ERROR: No se encontro la columna 'T. Trabajo'.")
        return 0
    if idx_f < 0:
        on_progress("ERROR: No se encontro la columna 'Fecha visita'.")
        return 0

    on_progress(f"'T. Trabajo' col {idx_t} | 'Fecha visita' col {idx_f}")

    out_indices = [i for i, h in enumerate(pp1.headers) if h.strip()]

    total_pages = len(pp1.page_options) if pp1.page_options else 1
    try:
        total_rows = int(pp1.total_rows_text.strip())
        on_progress(f"Total filas: {total_rows} | Total paginas: {total_pages}")
    except Exception:
        on_progress(f"Total paginas: {total_pages}")

    # Tokens for pagination come from the current page's hidden fields
    current_hidden = pp1.hidden

    def process_rows(pp: PageParser, headers) -> int:
        found = 0
        for row_cells in pp.rows:
            if len(row_cells) <= max(idx_t, idx_f):
                continue
            if row_cells[idx_t] == FILTER_TTRABAJO and row_cells[idx_f] == "":
                found += 1
                row_dict = {headers[i]: row_cells[i] if i < len(row_cells) else ""
                            for i in out_indices}
                on_row(row_dict)
        return found

    # ------------------------------------------------------------------ #
    # 5. Process pages
    # ------------------------------------------------------------------ #
    total_found = 0
    on_progress("Procesando pagina 1...")
    total_found += process_rows(pp1, pp1.headers)

    for page_num in range(2, total_pages + 1):
        if stop_event.is_set():
            on_progress("Proceso cancelado.")
            break

        on_progress(f"Procesando pagina {page_num} / {total_pages}...")

        page_fields = {
            **current_hidden,
            "paginaNueva": "false",
            "inTipoPeticionAJAX": "true",
            "controlAccionesPiePaginacionBE.nuPaginaSeleccionada": str(page_num),
            "coEvento_recuperarPagina_0": "Pagina",
            "CNC_codigoEvento": "recuperarPagina[0]",
        }

        try:
            resp_html = _post(opener, POST_URL, page_fields, referer=page_url)
        except Exception as e:
            on_progress(f"Error al obtener pagina {page_num}: {e}")
            break

        # The response may be:
        # a) MotorAJAX JSON with zone HTML fragments
        # b) A full page redirect (ControladorURLVuelta)
        # c) Full HTML directly
        if "<mensaje-motor-ajax>" in resp_html:
            motor_p = _parse_motor_ajax(resp_html)
            redir = _get_redirect_url(motor_p)
            if redir:
                # Follow the redirect
                page_url = BASE_URL + "/pyp/" + redir
                resp_html = _get(opener, page_url)
                ppN = parse(resp_html)
            else:
                # Zone update — look for table HTML in zone data
                zone_html = _get_zone_html(motor_p)
                ppN = parse(zone_html if zone_html else resp_html)
                # Update token from AJAX response
                new_token = _get_token_update(motor_p)
                if new_token and current_hidden:
                    current_hidden = {**current_hidden, "CNC_codigoToken": new_token}
        else:
            ppN = parse(resp_html)

        headers_n = ppN.headers if ppN.headers else pp1.headers
        if ppN.hidden:
            current_hidden = ppN.hidden
        total_found += process_rows(ppN, headers_n)

    on_progress("Proceso completado.")
    return total_found
