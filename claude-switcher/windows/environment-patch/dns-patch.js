// claude-environment-patch v0.1.0
// Installed into Clash Verge Rev's global extension script.
const CLAUDE_PATCH_PROXY_GROUP = "__CLAUDE_PATCH_PROXY_GROUP__";

function main(config) {
  const group = CLAUDE_PATCH_PROXY_GROUP;
  if (/[&=%]/.test(group)) {
    throw new Error("代理组名称包含 DNS 参数保留字符，需要服务人员人工处理。");
  }
  if (group === "__CLAUDE_PATCH_PROXY_GROUP__" ||
      !(config["proxy-groups"] || []).some(item => item.name === group)) {
    throw new Error("请选择配置中实际存在的代理组，然后重新生成补丁。");
  }
  const overseas = [
    "https://1.1.1.1/dns-query#" + group,
    "https://8.8.8.8/dns-query#" + group,
  ];
  const domestic = ["https://doh.pub/dns-query", "https://dns.alidns.com/dns-query"];
  const foreignDomains = [
    "+.claude.ai", "+.anthropic.com", "+.google.com", "+.googleapis.com",
    "+.gstatic.com", "+.googleusercontent.com", "+.youtube.com",
    "+.openai.com", "+.chatgpt.com", "+.oaiusercontent.com", "+.oaistatic.com",
    "+.github.com", "+.githubassets.com", "+.githubusercontent.com",
    "+.cloudflare.dev", "+.argotunnel.com", "+.cloudflaretunnel.com",
    "+.bash.ws", "+.ipcheck.ing", "geosite:geolocation-!cn",
  ];
  const uploadDomains = ["+.vodupload.com", "+.jinritemai.com", "+.zijieapi.com"];
  const existing = config.dns || {};
  const previousPolicy = existing["nameserver-policy"] || {};
  const supported = new Set(foreignDomains.concat(uploadDomains,
    ["geosite:cn", "geosite:cn,private", "geosite:private"]));
  const custom = Object.keys(previousPolicy).filter(key => !supported.has(key));
  if (custom.length) {
    throw new Error("检测到自定义 DNS 策略，需要服务人员人工合并，补丁未应用。");
  }
  // Work on a copy: a failed precondition must not partly mutate the client's config.
  const result = JSON.parse(JSON.stringify(config));
  const policy = {};
  for (const domain of foreignDomains) policy[domain] = overseas.slice();
  for (const domain of uploadDomains) policy[domain] = domestic.slice();
  const privateResolvers = previousPolicy["geosite:private"] || previousPolicy["geosite:cn,private"];
  if (privateResolvers) {
    policy["geosite:private"] = privateResolvers;
  }
  policy["geosite:cn"] = domestic.slice();
  const dns = Object.assign({}, result.dns || {}, {
    enable: true,
    nameserver: overseas.slice(),
    fallback: overseas.slice(),
    "nameserver-policy": policy,
    "direct-nameserver": overseas.slice(),
    "direct-nameserver-follow-policy": true,
  });
  // Node hostname resolution must not depend on the very proxy being established.
  if (!dns["proxy-server-nameserver"] || !dns["proxy-server-nameserver"].length) {
    dns["proxy-server-nameserver"] = domestic.slice();
  }
  result.dns = dns;
  // Keep uploads direct before overseas routes; retain all other customer rules.
  // geosite DNS selectors are not domain names and need no new routing geodata.
  const managedRules = uploadDomains.map(domain => "DOMAIN-SUFFIX," + domain.slice(2) + ",DIRECT")
    .concat(foreignDomains.filter(domain => domain.startsWith("+."))
      .map(domain => "DOMAIN-SUFFIX," + domain.slice(2) + "," + group));
  const managedSet = new Set(managedRules);
  result.rules = managedRules.concat((result.rules || []).filter(rule => !managedSet.has(rule)));
  return result;
}
