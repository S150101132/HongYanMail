# 证书兼容性报告（Task 0）

日期：2026-10-15（第 6 轮更新，含真实证书与真实加密邮件实测）
状态：**局部验证通过，并且发现一个必须先解决的发布级阻塞**。  
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
| **真实邮件内容加密算法** | **3DES-CBC（`1.2.840.113549.3.7`）** | 同上；这是本次最关键的发现 |
| 原始客户端 → 新实现解密 | **失败（能力缺口）**：`pyca/cryptography` 的 `pkcs7_decrypt_der` 只支持 AES-CBC，遇到 3DES 抛 `UnsupportedAlgorithm` | 真实解密运行记录 |
| 该能力是否可达 | **可达**：系统内 OpenSSL 3.5.7 成功解密真实邮件（退出码 0，24,668 字节有效 RFC 5322） | `analysis/openssl-3des-feasibility.json` |
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

## 3. 本次发现的产品缺陷（已修复）

真实解密暴露出一个未分类异常：

```text
UnsupportedAlgorithm: Only AES (with key sizes 128 or 256) with CBC mode is
currently supported for content decryption.
```

`decrypt_smime` 原先只捕获 `ValueError / TypeError / NotImplementedError`，因此 `UnsupportedAlgorithm` 会穿透成未处理异常，界面只能给出泛化的内部错误，也没有说明为什么打不开。

修复：捕获 `UnsupportedAlgorithm` 并映射为专用错误码 `ENCRYPTED_ALGORITHM_UNSUPPORTED`，加入 worker 的加密错误白名单，并给出明确中文提示（说明是算法不受支持，而不是密码错或邮件损坏）。回归测试用 OpenSSL 生成真实 3DES 信封，验证得到该专用错误码且不抛未处理异常（`tests/test_desktop_crypto_algorithms.py`）。

行为定级：**能力缺口不等于解密失败**。私钥正确、归属正确，只是后端不支持该算法，因此不能报成“解密失败”让用户误以为证书不对。

## 4. 发布级阻塞：加密阅读不可用

按设计稿 12.4 与 14.1，证书加密邮件的**解密阅读是 V0.1 发布条件**。现状：

| 事项 | 结论 |
| --- | --- |
| 真实邮件是否需要 3DES | **是**，已由 DER 证据确认 |
| 当前后端能否解 3DES | **不能**，`pkcs7_decrypt_der` 仅接受 AES-CBC |
| 能力是否可达 | **可达**，见第 5 节两条路径 |
| 因此当前状态 | **V0.1 不能声称已支持加密阅读；加密发送继续阻止** |

产品当前行为是正确的保守行为：识别出是加密邮件、尝试解密、失败后如实报告算法不受支持，**不显示空正文、不当作明文、不静默降级**。

## 5. 两条可行修复路径

### 路径 A（推荐）：在进程内自行实现 CMS 3DES 解密

`cryptography` 仍然提供 3DES **原语**（`hazmat.decrepit.ciphers.algorithms.TripleDES`，本机实测可用），只是高层 `pkcs7` 封装不开放 3DES。因此可以：

1. 解析 `EnvelopedData` 的 ASN.1，取出 `recipientInfo` 中被 RSA 加密的内容密钥；
2. 用私钥做 RSA 解封装；
3. 用 `TripleDES-CBC` 原语解密内容；
4. 处理 `signedData` 内层，并分别报告签名校验与信任状态。

优点：私钥不离开进程、不写临时明文、不依赖外部程序，仍在“成熟密码库”范围内（只用原语，不自己实现算法）。这是符合设计稿安全约束的做法。

### 路径 B：外部密码工具（当前仅为可行性验证）

本机 `C:\Program Files\Git\usr\bin\openssl.exe`（OpenSSL 3.5.7）用 `cms -decrypt` **成功解密**了这封真实邮件。但集成它意味着把私钥导出到临时文件或通过管道交给子进程，与设计稿 12.5“私钥不导出到普通临时文件”冲突，因此只作为“能力可达”的证据，不作为推荐实现。

## 6. 仍然未验证

1. **新实现加密 → 原客户端解密**：需要原客户端环境或明确收件人配合，尚未执行。
2. **加密载荷提交契约**：普通 `compose/continue/deliver` 不能假定原样保留信封；加密发送保持阻止。
3. **企业 `csm` 签名语义**：内层 `signedData` 的签名策略与信任链未解析。
4. **证书链与吊销**：本机为自有密钥场景，未建立企业 CA 信任；产品按“用户显式信任”处理。
5. **收件人公钥来源**：首版依赖用户显式选择文件，未接入组织目录。

在第 5 节路径 A 或等价方案落地并通过真实样本验证前，产品只能称为预览版，**不得声称已完成证书加密邮件的阅读**。
