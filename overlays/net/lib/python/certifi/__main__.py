"""``python -m certifi`` prints the resolved OS CA bundle path (like real certifi)."""
from certifi import where

if __name__ == "__main__":
    cert_path = where()
    if cert_path:
        print(cert_path)
    else:
        print("No system CA certificate bundle found")
