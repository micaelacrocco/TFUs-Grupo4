import sys
import requests

GATEWAY_URL = "http://localhost:8000/api/v1/wallets"
AUTH_URL = "http://localhost:8000/api/v1/auth/login" # O la ruta de login de tu API de auth
DIRECT_URL = "http://wallet_service:8000/api/v1/wallets" # Directo en la red Docker

SOAP_REQUEST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:wal="http://investnow.com/wallet">
   <soapenv:Header/>
   <soapenv:Body>
      <wal:GetWalletRequest>
         <wal:user_id>1</wal:user_id>
      </wal:GetWalletRequest>
   </soapenv:Body>
</soapenv:Envelope>
"""

def get_auth_token():
    """Obtiene un token JWT válido desde el Gateway (ajusta user/password según tus fixtures o tests)."""
    try:
        response = requests.post(AUTH_URL, json={"username": "inversor1", "password": "password123"})
        if response.status_code == 200:
            return response.json().get("access_token")
        else:
            print(f"Aviso: No se pudo autenticar automáticamente ({response.status_code}): {response.text}")
            print("Intentando continuar sin token o usando token de prueba...")
    except Exception as e:
        print(f"No se pudo conectar al endpoint de auth: {e}")
    return None

def test_wsdl(base_url, name, headers=None):
    print(f"\n--- 1. Obteniendo WSDL ({name}) ---")
    wsdl_url = f"{base_url}/soap/wallet/wsdl"
    try:
        response = requests.get(wsdl_url, headers=headers)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print("¡WSDL obtenido con éxito!")
            print(response.text[:300] + "...\n[Contenido truncado]")
        else:
            print(f"Error al obtener WSDL: {response.text}")
    except Exception as e:
        print(f"No se pudo conectar a {wsdl_url}: {e}")

def test_soap_call(url, name, headers):
    print(f"\n--- 2. Ejecutando llamada SOAP ({name}) a {url} ---")
    try:
        response = requests.post(url, data=SOAP_REQUEST_XML, headers=headers, timeout=5)
        print(f"Status Code: {response.status_code}")
        print("Respuesta XML:")
        print(response.text)
    except Exception as e:
        print(f"Error en la llamada SOAP: {e}")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "gateway"
    
    if mode == "direct":
        # Llamada directa dentro de la red Docker (no pasa por el Gateway, no requiere JWT de perimetro)
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://investnow.com/wallet/GetWallet"
        }
        test_wsdl(DIRECT_URL, "Directa (Wallet Service)", headers)
        test_soap_call(f"{DIRECT_URL}/soap/wallet", "Directa (Wallet Service)", headers)
    else:
        # Llamada a través del Gateway (Pasa por el Gatekeeper, requiere JWT)
        print("Autenticándose en el Gateway...")
        token = get_auth_token()
        
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://investnow.com/wallet/GetWallet"
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
            
        test_wsdl(GATEWAY_URL, "Vía Gateway (API)", headers)
        test_soap_call(f"{GATEWAY_URL}/soap/wallet", "Vía Gateway (API)", headers)