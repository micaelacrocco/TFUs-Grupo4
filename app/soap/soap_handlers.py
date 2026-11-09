import xml.etree.ElementTree as ET

def parse_get_wallet_request(body_bytes: bytes) -> int:
    """Extrae el user_id del sobre SOAP de entrada."""
    root = ET.fromstring(body_bytes)
    for elem in root.iter():
        if elem.tag.endswith("user_id"):
            if elem.text:
                return int(elem.text)
    raise ValueError("Missing or invalid user_id in SOAP request")

def build_get_wallet_response(user_id: int, balance: float) -> str:
    """Construye el sobre SOAP de respuesta exitosa."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:wal="http://investnow.com/wallet">
       <soapenv:Header/>
       <soapenv:Body>
          <wal:GetWalletResponse>
             <wal:user_id>{user_id}</wal:user_id>
             <wal:balance>{balance:.2f}</wal:balance>
             <wal:status>success</wal:status>
          </wal:GetWalletResponse>
       </soapenv:Body>
    </soapenv:Envelope>"""

def build_soap_fault(fault_string: str) -> str:
    """Construye un SOAP Fault estándar para errores."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
       <soapenv:Body>
          <soapenv:Fault>
             <faultcode>soapenv:Client</faultcode>
             <faultstring>{fault_string}</faultstring>
          </soapenv:Fault>
       </soapenv:Body>
    </soapenv:Envelope>"""