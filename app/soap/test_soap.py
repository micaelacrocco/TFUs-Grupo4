import urllib.request
import urllib.error

def run_soap_test():
    # URL corregida incluyendo el prefijo /api/v1/wallets definido en el APIRouter
    url = "http://wallet_service:8000/api/v1/wallets/soap/wallet"
    
    soap_xml = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:wal="http://investnow.com/wallet">
   <soapenv:Header/>
   <soapenv:Body>
      <wal:GetWalletRequest>
         <wal:user_id>1</wal:user_id>
      </wal:GetWalletRequest>
   </soapenv:Body>
</soapenv:Envelope>"""

    req = urllib.request.Request(
        url, 
        data=soap_xml.encode("utf-8"), 
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://investnow.com/wallet/GetWallet"
        },
        method="POST"
    )
    
    print(f"Enviando request SOAP a: {url}")
    try:
        with urllib.request.urlopen(req) as response:
            print(f"STATUS HTTP: {response.status}")
            body = response.read().decode("utf-8")
            print("RESPUESTA EXITOSA:")
            print(body)
    except urllib.error.HTTPError as e:
        print(f"ERROR HTTP {e.code}:")
        print(e.read().decode())
    except Exception as e:
        print(f"OTRO ERROR: {e}")

if __name__ == "__main__":
    run_soap_test()