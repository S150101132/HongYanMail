"""Synthetic certificates only; never loads personal certificates."""
from datetime import datetime, timedelta, timezone
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID


def identity(address='test@example.invalid', password=b'test-password', expired=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic test only')])
    builder = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now - timedelta(days=10))
               .not_valid_after(now + timedelta(days=-1 if expired else 30))
               .add_extension(x509.SubjectAlternativeName([x509.RFC822Name(address)]), False)
               .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
               .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), False)
               .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                           key_encipherment=True, data_encipherment=False,
                                           key_agreement=False, key_cert_sign=False, crl_sign=False,
                                           encipher_only=False, decipher_only=False), True))
    cert = builder.sign(key, hashes.SHA256())
    pfx = pkcs12.serialize_key_and_certificates(b'synthetic', key, cert, None,
                                               serialization.BestAvailableEncryption(password))
    return key, cert, pfx
