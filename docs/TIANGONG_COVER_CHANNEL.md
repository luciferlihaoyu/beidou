# 天宫封面通道开通指南

> 北斗 AI 工厂「封面」功能支持两条路径：
> 1. **无通道**（默认）：北斗生成封面绘图 prompt，复制给碧霄/婉儿在天宫发任务
> 2. **全自动通道**：北斗直接调天宫 `beidou-external-router` 创建 `cover-generate` 任务

开通全自动通道需要一次天宫侧运维操作（签发 service key），步骤如下。

## 1. 天宫侧：签发北斗服务密钥

天宫代码库已有完整的签发实现（`api/lib/beidou-service-keys.ts` 的
`issueServiceKey`），但未暴露为 API，需在天宫项目目录跑一次脚本：

```bash
cd /path/to/tiangong
cat > /tmp/issue-beidou-key.ts << 'EOF'
import { issueServiceKey } from "./api/lib/beidou-service-keys";

const result = await issueServiceKey({
  label: "beidou-cover-channel",
  scopes: ["research-task:create", "research-task:read", "research-task:cancel"],
  workspaceSlug: "<你的工作区 slug>",   // 北斗书稿所属工作区
  projectSlug: "<项目 slug>",          // 可选
});
console.log("KEY_ID:", result.keyId);
console.log("TOKEN:", result.token);  // 只显示这一次！立即复制保存
EOF
npx tsx /tmp/issue-beidou-key.ts
```

> ⚠️ TOKEN 只在签发时显示一次（HMAC verifier 存储，无法找回），请立即复制。

## 2. 北斗侧：配置环境变量

在北斗部署环境（Zeabur 服务变量 / .env）加三条：

```bash
TIANGONG_BASE_URL=https://<天宫平台域名>
TIANGONG_SERVICE_KEY=<上一步的 TOKEN>
TIANGONG_SERVICE_KEY_ID=<上一步的 KEY_ID>
```

重启北斗服务后，「封面」按钮生成 prompt 时会自动提交天宫任务
（弹窗显示 ✅ 任务已提交 + external_ref）。

## 3. 取图

天宫 agent 完成封面生成后，产物走天宫 artifact 通道；
北斗端后续可加轮询 `research-task:read` 自动回填封面图（当前为手动取回）。

## 安全说明

- service key 按 scope 最小授权（只允许建/读/取消研究任务，无审批/删除权）
- token 传输走 HTTPS + `Authorization: Bearer` + `X-TG-Service-Key-ID` 双头
- 天宫侧每次认证都有审计日志（key_id + 来源系统）
- 轮换：天宫侧 `rotateServiceKey` 可无缝换 key（旧 key 24h 重叠期）
