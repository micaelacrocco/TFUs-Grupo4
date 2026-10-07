"""Retry (UT4): reintenta llamadas salientes a otros servicios ante fallas
transitorias (conexión rechazada, timeout, 502/503/504), con backoff
exponencial y un número acotado de intentos.

Constantes hardcodeadas a propósito: no forman parte de `config.yaml` para
no extender el `AppConfig` tipado y el sanity checking de UT2 por un
parámetro que no aporta a la demostración de este patrón puntual.
"""

import time

import httpx

MAX_ATTEMPTS = 3
BASE_DELAY_SECONDS = 1  # backoff exponencial entre intentos: 1s, 2s
RETRYABLE_STATUS_CODES = {502, 503, 504}


def call_with_retry(
    method: str,
    url: str,
    *,
    retry_on_connect_only: bool = False,
    **kwargs,
) -> httpx.Response:
    """Ejecuta una request HTTP con reintentos ante fallas transitorias.

    - `retry_on_connect_only=False` (default): reintenta tanto si la
      conexión nunca se estableció (`httpx.ConnectError`) como si se
      estableció pero no hubo respuesta a tiempo (`httpx.TimeoutException`)
      o el servidor devolvió 502/503/504. Pensado para operaciones
      idempotentes (ej. un GET de solo lectura): repetirlas no cambia el
      resultado final.
    - `retry_on_connect_only=True`: solo reintenta ante `httpx.ConnectError`
      (la request nunca llegó a enviarse). Ante un timeout de lectura NO
      reintenta, porque el servidor pudo haber procesado el pedido antes de
      que la respuesta se perdiera en el camino de vuelta; reintentar
      ciegamente una operación no-idempotente (ej. un débito de saldo)
      arriesgaría aplicarla dos veces. Ver ASR-05 (UT1): prevención de
      operaciones duplicadas.

    Devuelve la última `httpx.Response` obtenida (haya sido exitosa o no:
    el llamador decide cómo reportar un status no-2xx). Si todos los
    intentos fallan por una excepción de conexión/timeout, re-lanza la
    última excepción (`httpx.RequestError`), que el llamador ya captura.
    """
    last_exc: httpx.RequestError | None = None
    response: httpx.Response | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = httpx.request(method, url, **kwargs)
            if response.status_code not in RETRYABLE_STATUS_CODES:
                return response
            last_exc = None
        except httpx.ConnectError as exc:
            last_exc = exc
            response = None
        except httpx.RequestError as exc:
            if retry_on_connect_only:
                # No es un error de conexión (ej. ReadTimeout): el pedido
                # pudo haber llegado al servidor. No reintentamos a ciegas.
                raise
            last_exc = exc
            response = None

        if attempt < MAX_ATTEMPTS:
            delay = BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            print(
                f"[retry] intento {attempt}/{MAX_ATTEMPTS} a {method} {url} "
                f"falló, reintentando en {delay:.0f}s"
            )
            time.sleep(delay)

    if last_exc is not None:
        raise last_exc
    return response
