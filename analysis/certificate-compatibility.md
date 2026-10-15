# 证书兼容性报告（Task 0）

日期：2026-10-15  
状态：**部分验证**。原客户端加密邮件可在本机解锁后由新实现解密（见下）；新实现加密后由原客户端解密尚未验证。  
本报告不含私钥、证书密码、邮件正文；所有结论来自本机真实材料或明确的合成样本。

## 1. 结论摘要

| 项目 | 结论 | 证据 |
| --- | --- | --- |
| 证书格式 | PKCS#12（.pfx），RSA 私钥 + 单张终端实体证书 | `analysis/certificate-inspection-result.json` |
| 证书身份 | 1 个邮箱身份，主体、邮箱与私钥公钥匹配校验通过 | `CryptoService.unlock_bytes` 的 `CERT_KEY_MISMATCH` 校验与真实解锁 |
| 证书用途 | 允许加密（key encipherment + emailProtection，且在有效期内） | `certificate-inspection-result.json` 的 `can_encrypt=true` |
| 有效期 | 2024-01-20 至 2027-01-09（UTC） | 同上 |
| 指纹（SHA-256） | `6f31454ef9879020ffd8b76d5f29f1699446ecb56d72cff9fa2ab30d38b5a519` | 同上 |
| 信任策略 | 本机自签/自有密钥，仅标记 `own_key`；**未做证书链与吊销校验** | `certificate-real-decryption-result.json` 的 `trust` 字段 |
| 目标加密格式 | 标准 S/MIME `application/pkcs7-mime; smime-type=enveloped-data` | 真实邮件头 `Content-Type`（见第 3 节） |
| 原客户端 → 新实现解密 | 已在本机真实邮件上执行，结果见 `analysis/task0-real-decryption-result.json` | 该报告只记录结构、身份与摘要 |
| 新实现 → 原客户端解密 | **未验证**。需要原客户端可运行环境或收件人配合 | 无 |
| 自加密往返（新实现 → 新实现） | 通过（合成样本）；**不能替代互通证据** | `tests/test_crypto_probe.py::test_real_smime_roundtrip_preserves_chinese_and_crlf` |
| 加密载荷的网络提交契约 | **未验证**，因此产品将加密发送显式阻止 | `CoremailAdapter.submit_encrypted` 抛 `ENCRYPTED_SUBMISSION_UNVERIFIED` |

## 2. 密码后端与算法

- 后端：`pyca/cryptography` 50.0.2（成熟实现，未自行实现任何算法）。
- 允许的算法范围：RSA 密钥传输 + S/MIME EnvelopedData。
- 明确拒绝并如实报告，而不是猜测处理：
  - `multipart/encrypted` → `unsupported-crypto`；
  - `smime-type=signed-data` 等非 enveloped-data → `UNSUPPORTED_ENCRYPTION_FORMAT`；
  - 解密后仍为 `application/pkcs7-mime` 的嵌套加密 → `NESTED_CRYPTO_UNSUPPORTED`；
  - 非 RSA 私钥 → `CERT_ALGORITHM_UNSUPPORTED`。
- 大小限制：信封 25 MiB，解密后明文 64 MiB，证书文件 4 MiB。
- 签名状态与信任状态分开报告：解密成功不等于签名可信（`signature_status` / `trust_status` 独立字段）。

## 3. 真实样本结构（脱敏）

本机 `邮箱发送测试.eml` 由原客户端发出，头部显示企业封装标记：

```text
X-Mailer: Coremail cmclient(4.2.1.1421 win64.exe)
x-cm-smime: encrypted,signed,cmsm
x-cm-smime-version: cmsm openssl OpenSSL 3.3.1 4 Jun 2024(gm:GmSSL 3.1.1) v1
Content-Type: application/pkcs7-mime; smime-type=enveloped-data; name="smime.p7m"
Content-Transfer-Encoding: base64
```

结论：外层是标准 S/MIME enveloped-data，因此本机可解；企业扩展标记 `cmsm` 说明原客户端在此之上还有自有的签名/封装语义，**该语义未在本项目中解析或复现**。这一条是后续加密发送互通的主要未知项。

## 4. 安全处理

- 证书密码只在本地遮蔽输入；本报告与所有落盘文件不含密码、私钥或正文。
- 解密后的 MIME 只在内存中交给既有解析器；不写临时明文文件、不做正文缓存。
- 解密结果复用 Task 5 的原始大小、解码总量、MIME 深度限制与 HTML 隔离（无脚本、无网络、无 QWebChannel、CID 仅限本文档资源）。
- 锁定或退出时释放密钥引用并清空已解密视图；不声称 Python 内存可保证清零。
- 收件人证书只接受用户显式选择并显式标记信任（`explicit_pin`）；未信任证书阻止加密发送，永不静默降级为明文。

## 5. 未验证项与阻塞

1. **新实现加密 → 原客户端解密**：需要原客户端环境或明确收件人配合，尚未执行。
2. **加密载荷提交契约**：普通 `compose/continue/deliver` 路径不能假定会原样保留信封，尚未取得真实验证；因此加密发送保持阻止。
3. **企业 `cmsm` 签名/封装语义**：未解析，未复现，未验证。
4. **证书链与吊销校验**：本机为自有密钥场景，未建立企业 CA 信任链；产品按“用户显式信任”处理，并在界面标注为手工信任。
5. **收件人公钥来源**：首版依赖用户显式选择文件；未接入组织通讯录或企业目录。

在 (1)(2) 通过前，产品只能称为预览版，不得声称已完成证书加密收发互通。
