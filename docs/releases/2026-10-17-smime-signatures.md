# 2026-10-17 · S/MIME digital signatures

## What changed

- **Sending:** the composer has a 签名 toggle next to 加密发送. It follows encryption by default and can be turned on or off by itself.
  - Signed and encrypted mail is signed first, then encrypted: an opaque SignedData inside the EnvelopedData, as the official client does.
  - Signed-only mail is `multipart/signed` with a detached `application/pkcs7-signature` (`micalg=sha-256`). Its text parts are base64, so relays cannot alter the signed bytes.
  - Both kinds of mail go through the pre-encoded submission path.
- **Algorithm:** SHA-256 with RSA PKCS#1 v1.5 (pyca `PKCS7SignatureBuilder`). The signature includes the signer certificate and the pfx chain.
- **Locked certificate:** if the certificate is locked and signing is on, `send` returns `NeedsSigningKey`. The UI then offers to unlock the certificate, or the user can turn 签名 off. Mail is never sent unsigned silently.
- **Send is blocked when:**
  - the certificate's KeyUsage does not allow signing (`CERT_SIGN_USAGE`);
  - the certificate's email does not match the From address (`SIGN_CERT_ADDRESS_MISMATCH`).
- **Reading:** `crypto/signature.py` verifies `multipart/signed` mail and opaque SignedData mail, including the SignedData inside decrypted mail. It checks:
  - the messageDigest against the content;
  - the signature over the signed attributes;
  - that the signer certificate's email matches From;
  - the certificate's validity at the signing time.

  The reader shows one of three results:
  - 签名有效，证书由 <issuer> 签发 (plus a match with the company directory when the signer certificate is identical to `cert:listPublicKey`);
  - 签名无效 (content altered);
  - 无法验证 (with the reason: no certificate, an unsupported algorithm such as SM2/SM3, or a malformed structure).

  There is no CA store, so the issuer is reported but not treated as trusted.

## Real-account verification (2026-10-17, `tools/verify/verify_signed_real.py`)

- **Personal pfx:**
  - RSA 2048 key; the certificate is signed with sha256WithRSA by INSPUR-CA, and the pfx carries a chain of 1.
  - KeyUsage is digitalSignature + keyEncipherment. EKU includes emailProtection.
  - The certificate's email matches the account.
  - It is not an SM2 certificate.
- **Real received colleague mail (read-only):** the signature is valid (SHA-256), the signer matches From, and the certificate was within its validity dates.
- **Send-to-self, signed and encrypted:** Accepted. The inbox copy and the sent copy both decrypt. Both signatures are valid, the signer matches From, and the signer certificate equals the directory certificate.
- **Send-to-self, signed only:** Accepted. The inbox copy and the sent copy are both valid, and `smime.p7s` is not shown as an attachment.
