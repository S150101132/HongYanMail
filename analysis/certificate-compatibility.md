# 证书兼容性报告（Task 0）

日期：2026-10-15（第 6 轮更新，含真实证书与真实加密邮件实测）
状态：**解密阅读能力已打通并通过真实邮件验证**；与原客户端的双向互通声明仍待完成。  
本报告不含私钥、证书密码、邮件正文；所有结论来自本机真实材料或明确的合成样本。

## 1. 结论摘要

| 项目 | 结论 | 证据 |
| --- | --- | --- |
| 证书解锁 | **实测通过**（本机真实 PFX） | `analysis/certificate-inspection-result.json` |
| 证书身份 | 1 个邮箱身份；证书与私钥公钥匹配校验通过 | `CryptoService.unlock_bytes` 的 `CERT_KEY_MISMATCH` 校验 |
| 证书用途 | 允许加密（keyEncipherment + emailProtection，且在有效期内） | `can_encrypt=true` |
| 有效期 | 2024-01-20 至 2027-01-09（UTC） | 同上 |
| 指纹（SHA-256） | `6f31454ef9879020ffd8b76d5f29f1699446ecb56d72cff9fa2ab30d38b5a519` | 同上 |
| 信任策略 | 自有密钥，仅标记 `own_key`；**未做证书链与吊销校验** | `trust` 字段 |
| 真实邮件外层格式 | `application/pkcs7-mime; smime-type=enveloped-data` | 真实邮件头 |
| 真实邮件内层结构 | 外层容器 `signedData`（`1.2.840.113549.1.7.3`），内层内容 `data` + `EnvelopedData` | `analysis/envelope-algorithm.json` 的 DER 遍历结果 |
| 真实邮件内容加密算法 | **3DES-CBC（`1.2.840.113549.3.7`）** | `analysis/envelope-algorithm.json` 的 DER 遍历结果 |
| 原始客户端 → 新实现解密 | **已打通**：新实现的 CMS 路径解密真实邮件成功，外层 3DES → 内层 signedData → 14,187 字节可读 `multipart/related` 邮件 | `tests/test_desktop_real_message.py`（4 项，均通过） |
| 签名状态 | 供应商邮件**带签名**；摘要算法 SHA-256，签名算法 RSA；**只报告不验证** | 同上 `signed_data_summary` |
| 该能力是否可达 | 是，且已实现（进程内，私钥不离开进程） | `coremail_desktop/crypto/cms.py` |
| 新实现 → 原客户端解密 | **未验证** | 无 |
| 自加密往返 | 通过（合成样本），**不能替代互通证据** | `tests/test_crypto_probe.py` |
| 加密载荷网络提交契约 | **未验证**，产品显式阻止加密发送 | `submit_encrypted` 抛 `ENCRYPTED_SUBMISSION_UNVERIFIED` |

## 2. 真实邮件实测：格式与算法

本机 `邮箱发送测试.eml` 由原客户端发出：

```text
X-Mailer: Coremail cmclient(4.2.1.1421 win64.exe)
x-cm-smime: encrypted,signed,cmsm
x-cm-smime-version: cmsm openssl OpenSSL 3.3.1 4 Jun 2024(gm:GmSSL 3.1.1) v1
Content-Type: application/pkcs7-mime; smime-type=enveloped-data; name="smime.p7m"
```

对 DER 结构逐层遍历后得到：

| 层级 | OID | 含义 |
| --- | --- | --- |
| 外层容器 | `1.2.840.113549.1.7.3` | `signedData` |
| 内层内容类型 | `1.2.840.113549.1.7.1` | `data` |
| 收件人密钥传输 | `1.2.840.113549.1.1.1` | `rsaEncryption`（RSA 密钥传输） |
| **内容加密** | **`1.2.840.113549.3.7`** | **`des-ede3-cbc`（3DES-CBC）** |

结论：外层是标准 S/MIME，但**内容加密用的是 3DES-CBC，而不是 AES-CBC**。`x-cm-smime-version` 里的 `gm:GmSSL` 说明该客户端基于国密版 OpenSSL 构建，但这封真实邮件的内容加密确实走的是 3DES（不是 SM4）。

## 3. 实现记录与过程中发现的缺陷

### 3.1 已实现的解密路径（`coremail_desktop/crypto/cms.py`）

因为 `pyca/cryptography` 的高层 `pkcs7_decrypt_der` 只接受 AES-CBC，产品改为在同进程内直接驱动同一套原语：`asn1.py` 只做 ASN.1 解析，所有密码学原语（RSA PKCS#1 v1.5、3DES、AES、PKCS#7 去填充）都来自成熟库，**没有自行实现任何算法**。

解析器必须容忍真实世界差异，这些都是实测踩到的：

| 差异 | 处理 |
| --- | --- |
| ContentInfo 的 content 被包在 EXPLICIT `[0]` 中，内部才是 SEQUENCE | `_unwrap_explicit` 自动剥一层 |
| 密文放在原始上下文标签 `[0]`（`0x80`）而非 OCTET STRING | `parse_encrypted_content_info` 同时接受两种形式 |
| `recipientInfo` 的 `rid` 本身是 SEQUENCE，按位置取字段会误当算法 | 按类型选取字段（`_key_transport_fields`） |
| 两个收件人中只有一个属于本机私钥 | 先用密钥长度过滤，再用 PKCS#7 填充校验确认 |
| 解密正文本身又是 MIME 包装的 S/MIME 容器 | `unwrap_mime_entity` + `peel_message` 逐层剥离，最多 4 层 |

### 3.2 修复的缺陷

1. **未处理异常**：`UnsupportedAlgorithm` 穿透成未处理异常，界面只能显示泛化内部错误。现在有专用错误码。
2. **OID 标签错误**：早期表格把 `1.2.840.113549.1.7.3` 标成 `signedData`，实际按 RFC 5652 是 **`envelopedData`**。已用 OpenSSL 生成的 `-des3` 信封交叉验证并改正。
3. **填充处理**：高层去填充助手会拒绝合法的短填充（实测 4 字节填充被误判为非法）。改为按 RFC 5652 显式校验并剥离长度字节——这是对已由真实密码产出的字节做长度检查，不是自行实现密码学。
4. **错误码语义**：算法不受支持与私钥不匹配必须区分，否则用户会误以为证书不对。

### 3.3 端到端实测结果

`tests/test_desktop_real_message.py` 在本机真实邮件上验证（缺少真实邮件或证书时自动跳过，不写入任何正文）：

| 检查 | 结果 |
| --- | --- |
| 外层为 3DES EnvelopedData，IV 8 字节，密文为 8 的整数倍 | 通过 |
| 逐层剥离后得到可读邮件（> 1000 字节，`multipart/*` 或 `text/*`） | 通过 |
| 签名层被识别且**只报告不验证**（`present_not_verified`） | 通过 |
| 供应商签名摘要算法为 SHA-256（`2.16.840.1.101.3.4.2.1`），1 个签名者 | 通过 |
| 另一张证书的私钥无法读取该邮件 | 通过 |

## 4. 发布门槛现状

按设计稿 12.4 与 14.1，证书加密邮件的**解密阅读是 V0.1 发布条件**。现状：

| 事项 | 结论 |
| --- | --- |
| 真实邮件是否需要 3DES | 是，已由 DER 证据确认 |
| 新实现能否解 3DES | **能**，已实现并通过真实邮件验证 |
| 加密阅读是否可作为 V0.1 能力声明 | **尚不能**，见下 |

**仍然阻止发布声明的是互通与提交，而不是解密能力本身**：

1. **新实现加密 → 原客户端解密未验证**，因此“与原客户端双向互通”不能声明完成。
2. **加密载荷提交契约未验证**，加密发送继续被显式阻止。

也就是说：解密阅读的**能力**已经打通并有真实证据；**互通声明**仍需原客户端配合。

## 5. 实现方式说明（原方案对比）

已采用**路径 A**：进程内直接驱动密码学原语。私钥不离开进程、不写临时明文、不调用外部程序。

未采用**路径 B**（调用外部 OpenSSL）。它虽然同样能解密真实邮件（已实测：OpenSSL 3.5.7，退出码 0），但需要把私钥交给子进程或临时文件，与设计稿 12.5 冲突，因此仅作为“能力可达”的旁证保留在 `analysis/openssl-3des-feasibility.json`。

## 6. 仍然未验证

1. **新实现加密 → 原客户端解密**：需要原客户端环境或明确收件人配合，尚未执行。
2. **加密载荷提交契约**：普通 `compose/continue/deliver` 不能假定原样保留信封；加密发送保持阻止。
3. **企业 `csm` 签名语义**：内层 `signedData` 的签名策略与信任链未解析。
4. **证书链与吊销**：本机为自有密钥场景，未建立企业 CA 信任；产品按“用户显式信任”处理。
5. **收件人公钥来源**：首版依赖用户显式选择文件，未接入组织目录。

加密阅读能力已落地并通过真实邮件验证，但**互通声明仍未完成**：新实现加密→原客户端解密、加密提交契约、企业 csm 签名语义与证书链校验都仍是未验证项。在完成双向互通前，产品仍只能称为预览版。
