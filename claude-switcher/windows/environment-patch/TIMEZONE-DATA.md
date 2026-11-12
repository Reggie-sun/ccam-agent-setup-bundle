# Timezone Data

IANA → Windows 映射来自 Unicode CLDR 的 windowsZones.xml，离线随包交付。

- 固定源码： https://github.com/unicode-org/cldr/blob/f69257926c43f1cb22cf3fcd93e709a2ca994bb7/common/supplemental/windowsZones.xml
- 源文件 SHA256： `5f902f94106a396cb7774f0b0e1ca4dfecb686494045350c0b4935d378e3913b`
- 许可证：同目录 `UNICODE-LICENSE.txt`，保留上游授权文本。
- 额外纳入明确等于 UTC 的标准别名；不从 IP 城市或当前小时差推算时区。
- 客户 Windows 必须实际支持映射后的 ID，否则拒绝应用。VPS 的未知别名也会明确拒绝，不猜测。

## IANA Aliases

同时使用同一固定版本的 [CLDR BCP47 timezone.xml](https://github.com/unicode-org/cldr/blob/f69257926c43f1cb22cf3fcd93e709a2ca994bb7/common/bcp47/timezone.xml) 扩展官方 IANA 别名组，包含如 `Asia/Kolkata` / `Asia/Calcutta` 的对应关系。仅在别名组已有唯一 Windows ID 时扩展；不按名称相似度或小时差猜测。

- 别名源文件 SHA256： `1f63924aba623465ad487bc3452bdc269f01fa787477610f8f526e9ad25efcae`
